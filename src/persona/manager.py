"""Persistent Persona creation and one isolated control process per identity."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import secrets
import re
import subprocess
import sys
import tempfile
import time

from .control import atomic_json, check_process_budget, file_lock, is_owned, process_identity, read_json, request, socket_path, terminate_owned
from .environment import snapshot as host_snapshot
from .model import CapabilitySnapshot, Persona, PersonaGenerator, TemplateCatalog, QualificationError, combination_fingerprint
from .store import PersonaStore


DEFAULT_ROOT = Path.home() / ".tbp" / "personas"


class PersonaManager:
    def __init__(self, root: str | Path | None = None, *, backend: str = "software"):
        self.store = PersonaStore(Path(root or DEFAULT_ROOT).expanduser().absolute())
        self.root = self.store.root
        # chmod is ignored by Android shared storage; profiles must not live there.
        if self.root.stat().st_mode & 0o077:
            raise RuntimeError("Persona root must be a private directory, not Android shared storage")
        self.backend = backend

    def current_snapshot(self) -> CapabilitySnapshot:
        return CapabilitySnapshot(environment=host_snapshot(self.backend), capabilities={})

    def catalog(self) -> TemplateCatalog:
        catalog = TemplateCatalog.default()
        for report in self.store.list_qualifications():
            try:
                catalog.promote(report)
            except QualificationError:
                # Failed and stale reports remain useful diagnostics, never eligibility.
                continue
        return catalog

    def create(self, *, seed: int | None = None, experimental: bool = False,
               template_id: str | None = None):
        snapshot = self.current_snapshot()
        catalog = self.catalog()
        if template_id is not None:
            selected = catalog.get(template_id)
            selected_catalog = TemplateCatalog([selected])
            for report in catalog.reports():
                if report.template_id == template_id:
                    selected_catalog.promote(report)
            catalog = selected_catalog
        persona = PersonaGenerator(
            catalog, snapshot,
            runtime_browser_version=snapshot.environment["firefox_version"],
        ).create(seed=seed, experimental=experimental)
        self.store.save_snapshot(snapshot)
        self.store.save(persona)
        return persona

    def paths(self, persona_id: str) -> dict[str, Path]:
        self.store.load(persona_id)  # Validate the persistent identity before path use.
        directory = self.root / "instances" / persona_id
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        return {"directory": directory, "profile": directory / "profile",
                "state": directory / "state.json", "lock": directory / "start.lock",
                "worker_lock": directory / "worker.lock", "log": directory / "worker.log",
                "socket": socket_path(self.root, persona_id)}

    def status(self, persona_id: str) -> dict:
        persona = self.store.load(persona_id)
        state = read_json(self.paths(persona_id)["state"]) or {}
        alive = is_owned(state.get("worker"))
        interrupted = not alive and state.get("status") in {"starting", "ready", "stopping"}
        # Never return the control token or private process credentials.
        return {"persona_id": persona_id, "template_id": persona.template_id,
                "experimental": persona.experimental,
                "state": ("requalifying" if state.get("mode") == "requalify" else state.get("status", "stopped"))
                if alive else ("interrupted" if interrupted else "stopped"),
                "instance_id": state.get("instance_id"),
                "display": state.get("display") if alive else None,
                "pid": state.get("worker", {}).get("pid") if alive else None,
                "last_error": state.get("error") or ("Worker exited without cleanup" if interrupted else None), "alive": alive}

    def list(self) -> list[dict]:
        return [self.status(persona.persona_id) for persona in self.store.list()]

    def _admission_lock(self):
        return file_lock(Path(tempfile.gettempdir()) / "tbp-persona-admission.lock", blocking=False)

    @staticmethod
    def _validation_path(paths: dict, instance_id: str, *, cancel: bool = False) -> Path:
        if not isinstance(instance_id, str) or not re.fullmatch(r"[0-9a-f]{32}", instance_id):
            raise RuntimeError("Invalid validation instance id")
        prefix = "cancel-requalify" if cancel else "requalify"
        return paths["directory"] / f"{prefix}-{instance_id}.json"

    def load_worker_persona(self, persona_id: str, instance_id: str) -> Persona:
        """Only a matching validation worker can read its temporary candidate."""
        original = self.store.load(persona_id)
        paths = self.paths(persona_id)
        state = read_json(paths["state"]) or {}
        if state.get("instance_id") != instance_id:
            raise RuntimeError("Stale worker startup request")
        if state.get("mode") != "requalify":
            return original
        self._check_validation_cancelled(paths, instance_id)
        candidate_path = self._validation_path(paths, instance_id)
        if candidate_path.is_symlink():
            raise RuntimeError("Refusing symlink validation candidate")
        data = read_json(candidate_path)
        if data is None:
            raise RuntimeError("Validation candidate is missing")
        candidate = Persona.from_mapping(data)
        snapshot = self.current_snapshot()
        expected = replace(original, capability_snapshot_fingerprint=snapshot.fingerprint,
                           qualification=None, experimental=True)
        if candidate.to_dict() != expected.to_dict():
            raise QualificationError("Validation candidate no longer matches saved identity or environment")
        self._requalification_template(original, snapshot)
        return candidate

    def _requalification_template(self, persona: Persona, snapshot: CapabilitySnapshot):
        template = self.catalog().get(persona.template_id)
        expected = template.expand(persona.final_config["variant_index"],
                                   snapshot.environment["firefox_version"])
        if (template.status == "disabled" or template.version != persona.template_version
                or combination_fingerprint(template.template_id, template.version, expected)
                != combination_fingerprint(persona.template_id, persona.template_version, persona.final_config)):
            raise QualificationError(
                "Saved identity does not match the current Firefox version or enabled template; "
                "create a new Persona instead of changing its saved fields")
        configured_backend = persona.final_config.get("graphics", {}).get("execution_backend")
        if configured_backend in {"software", "native"} and configured_backend != self.backend:
            raise QualificationError("Saved Persona requires its configured graphics backend")
        return template

    def _check_validation_cancelled(self, paths: dict, instance_id: str):
        if self._validation_path(paths, instance_id, cancel=True).exists():
            raise RuntimeError("Persona requalification was cancelled by stop")

    async def start(self, persona_id: str, *, timeout: float = 75) -> dict:
        paths = self.paths(persona_id)
        with self._admission_lock(), file_lock(paths["lock"], blocking=False):
            # Read inside the lifecycle lock so a completed requalification
            # cannot leave this caller checking stale in-memory metadata.
            persona = self.store.load(persona_id)
            current = self.current_snapshot()
            if persona.capability_snapshot_fingerprint != current.fingerprint:
                raise QualificationError("Firefox/backend environment changed; requalification is required")
            if not persona.experimental:
                catalog = self.catalog()
                template = catalog.get(persona.template_id)
                report = persona.qualification
                if report is None or not report.usable_for(template, persona.final_config, current):
                    raise QualificationError("Saved Persona lacks current full-combination qualification")
                current_report = catalog.report_for(template, persona.final_config, current)
                if current_report is None or not current_report.usable_for(template, persona.final_config, current):
                    raise QualificationError("Persona combination qualification has been revoked or is unavailable")
            old = read_json(paths["state"]) or {}
            if is_owned(old.get("worker")):
                if old.get("status") == "ready" and old.get("mode") != "requalify":
                    response = await request(paths["socket"], old["token"], "status", timeout=5)
                    if not response.get("success"):
                        raise RuntimeError(response.get("error", "Worker is not ready"))
                    return self.status(persona_id)
                raise RuntimeError("Persona worker is already starting, stopping or requalifying")
            for record in reversed(old.get("resources", [])):
                await terminate_owned(record)
            await self._launch_worker(persona_id, paths, timeout=timeout)
            return self.status(persona_id)

    async def _launch_worker(self, persona_id: str, paths: dict, *, timeout: float = 75,
                             instance_id: str | None = None, mode: str = "normal"):
        """Launch under the caller's admission and lifecycle locks."""
        usage = check_process_budget(additional=10)
        paths["socket"].unlink(missing_ok=True)
        token = secrets.token_hex(32)
        instance_id = instance_id or secrets.token_hex(16)
        atomic_json(paths["state"], {"status": "starting", "instance_id": instance_id,
                                    "token": token, "backend": self.backend, "mode": mode,
                                    "startup_usage": usage})
        if mode == "requalify":
            self._check_validation_cancelled(paths, instance_id)
        log_fd = os.open(paths["log"], os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
        project = Path(__file__).resolve().parents[2]
        try:
            child = subprocess.Popen(
                [sys.executable, "-m", "src.persona.runtime", "--root", str(self.root),
                 "--persona-id", persona_id, "--backend", self.backend,
                 "--instance-id", instance_id],
                cwd=project, env={**os.environ, "TBP_RUNTIME_DIR": str(paths["directory"])},
                stdin=subprocess.DEVNULL, stdout=log_fd, stderr=log_fd,
                start_new_session=True,
            )
        finally:
            os.close(log_fd)
        identity = process_identity(child.pid)
        try:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if mode == "requalify":
                    self._check_validation_cancelled(paths, instance_id)
                state = read_json(paths["state"]) or {}
                if state.get("instance_id") != instance_id:
                    raise RuntimeError("Persona startup ownership changed unexpectedly")
                if state.get("status") == "ready" and paths["socket"].exists():
                    response = await request(paths["socket"], token, "status", timeout=5)
                    if response.get("success"):
                        return
                if state.get("status") == "failed" or child.poll() is not None:
                    raise RuntimeError(state.get("error") or f"Worker exited; see {paths['log']}")
                await asyncio.sleep(0.2)
            raise TimeoutError(f"Persona startup timed out; see {paths['log']}")
        except BaseException:
            # Also covers cancellation before the worker writes its PID.
            if child.poll() is None:
                child.terminate()
                try:
                    await asyncio.to_thread(child.wait, 15)
                except subprocess.TimeoutExpired:
                    await terminate_owned(identity or {}, timeout=1)
            await self._stop_worker(paths)
            raise

    async def command(self, persona_id: str, action: str, params: dict | None = None,
                      *, timeout: float = 60) -> dict:
        paths = self.paths(persona_id)
        state = read_json(paths["state"]) or {}
        if state.get("mode") == "requalify":
            raise RuntimeError("Persona is being requalified; browser commands are unavailable")
        if not is_owned(state.get("worker")) or state.get("status") != "ready":
            raise RuntimeError("Persona is not running; start it first")
        result = await request(paths["socket"], state["token"], action, params, timeout=timeout)
        if not result.get("success"):
            raise RuntimeError(result.get("error", "Persona command failed"))
        return result.get("data")

    async def stop(self, persona_id: str, *, timeout: float = 20) -> dict:
        paths = self.paths(persona_id)
        state = read_json(paths["state"]) or {}
        if state.get("mode") == "requalify":
            instance_id = state["instance_id"]
            atomic_json(self._validation_path(paths, instance_id, cancel=True), {"cancelled": True})
            if is_owned(state.get("worker")):
                try:
                    await request(paths["socket"], state["token"], "shutdown", timeout=5)
                except (OSError, TimeoutError):
                    # Startup may not have opened its socket yet. The launch
                    # loop sees the marker and stops its own child.
                    pass
            deadline = time.monotonic() + timeout
            while True:
                lock = file_lock(paths["lock"], blocking=False)
                try:
                    lock.__enter__()
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Requalification cancellation requested; cleanup is still running")
                    await asyncio.sleep(0.1)
            try:
                latest = read_json(paths["state"]) or {}
                if latest.get("instance_id") == instance_id:
                    await self._stop_worker(paths, timeout=timeout)
                return self.status(persona_id)
            finally:
                self._validation_path(paths, instance_id, cancel=True).unlink(missing_ok=True)
                lock.__exit__(None, None, None)
        with file_lock(paths["lock"], blocking=False):
            await self._stop_worker(paths, timeout=timeout)
            return self.status(persona_id)

    async def _stop_worker(self, paths: dict, *, timeout: float = 20):
        """Stop owned resources while the caller holds the lifecycle lock."""
        state = read_json(paths["state"]) or {}
        worker = state.get("worker")
        if is_owned(worker):
            try:
                await request(paths["socket"], state["token"], "shutdown", timeout=5)
            except (OSError, TimeoutError):
                await terminate_owned(worker, timeout=timeout)
            deadline = time.monotonic() + timeout
            while is_owned(worker) and time.monotonic() < deadline:
                await asyncio.sleep(0.1)
            if is_owned(worker):
                await terminate_owned(worker)
        resources = state.get("resources", [])
        for record in reversed(resources):
            await terminate_owned(record)
        deadline = time.monotonic() + min(timeout, 5)
        while any(is_owned(record) for record in [worker, *resources]):
            if time.monotonic() >= deadline:
                raise RuntimeError("Persona resources are still alive after cleanup")
            await asyncio.sleep(0.1)
        final = read_json(paths["state"]) or state
        if final:
            final.update(status="stopped", resources=[])
            atomic_json(paths["state"], final)

    async def requalify(self, persona_id: str, *, timeout: float = 75):
        """Validate saved fields on the current host, then atomically rebind them.

        The profile is reused in place. Only the private worker sees a staged
        experimental candidate; saved Persona metadata remains unchanged until
        successful validation and complete process shutdown.
        """
        from .qualification import qualify_probe
        paths = self.paths(persona_id)
        with self._admission_lock(), file_lock(paths["lock"], blocking=False):
            original = self.store.load(persona_id)
            old_state = read_json(paths["state"]) or {}
            if any(is_owned(record) for record in [old_state.get("worker"), *old_state.get("resources", [])]):
                raise RuntimeError("Stop the running Persona before requalification")
            snapshot = self.current_snapshot()
            template = self._requalification_template(original, snapshot)
            candidate = replace(original, capability_snapshot_fingerprint=snapshot.fingerprint,
                                qualification=None, experimental=True)
            instance_id = secrets.token_hex(16)
            candidate_path = self._validation_path(paths, instance_id)
            cancel_path = self._validation_path(paths, instance_id, cancel=True)
            atomic_json(candidate_path, candidate.to_dict())
            try:
                try:
                    await self._launch_worker(persona_id, paths, timeout=timeout,
                                              instance_id=instance_id, mode="requalify")
                    self._check_validation_cancelled(paths, instance_id)
                    state = read_json(paths["state"]) or {}
                    if state.get("instance_id") != instance_id or state.get("mode") != "requalify":
                        raise RuntimeError("Validation worker ownership changed")
                    result = await request(paths["socket"], state["token"], "probe", timeout=90)
                    self._check_validation_cancelled(paths, instance_id)
                    if not result.get("success"):
                        raise QualificationError(result.get("error", "Validation probe failed"))
                    observations = result.get("data")
                    atomic_json(paths["directory"] / "last-requalification-probe.json", observations)
                    qualification = qualify_probe(candidate, snapshot, observations)
                    if not qualification.usable_for(template, original.final_config, snapshot):
                        raise QualificationError("Saved Persona requalification failed: " + "; ".join(qualification.reasons))
                finally:
                    # Metadata stays old on failure/cancellation; a process crash
                    # likewise leaves only an unusable candidate outside the store.
                    cleanup = asyncio.create_task(self._stop_worker(paths))
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError:
                        await cleanup
                        raise
                    finally:
                        cancelled = cancel_path.exists()
                        candidate_path.unlink(missing_ok=True)
                # No await occurs after the commit, so task cancellation cannot
                # report failure after an identity was successfully rebound.
                if cancelled:
                    raise RuntimeError("Persona requalification was cancelled by stop")
                if self.current_snapshot().fingerprint != snapshot.fingerprint:
                    raise QualificationError("Environment changed during requalification")
                self._check_validation_cancelled(paths, instance_id)
                self.store.commit_requalification(original, snapshot, qualification)
                return qualification
            finally:
                cancel_path.unlink(missing_ok=True)

    async def qualify(self, persona_id: str):
        from .qualification import qualify_probe
        persona = self.store.load(persona_id)
        snapshot = self.current_snapshot()
        if persona.capability_snapshot_fingerprint != snapshot.fingerprint:
            raise QualificationError("Environment changed since creation; cannot qualify stale settings")
        report = await self.command(persona_id, "probe", timeout=90)
        atomic_json(self.paths(persona_id)["directory"] / "last-probe.json", report)
        qualification = qualify_probe(persona, snapshot, report)
        self.store.save_qualification(qualification)
        return qualification

    async def bootstrap(self, template_id: str = "linux-firefox-native-phase0", *,
                        limit: int | None = None, progress=None) -> dict:
        """Qualify finite preset variants sequentially on this device."""
        snapshot = self.current_snapshot()
        template = self.catalog().get(template_id)
        if template.status == "disabled":
            raise QualificationError("Template is disabled")
        if limit is not None and limit < 1:
            raise ValueError("limit must be positive")
        self.store.save_snapshot(snapshot)
        results = []
        for index in range(min(limit or len(template.variants), len(template.variants))):
            if progress:
                progress(f"Qualifying {template_id} variant {index + 1}/{len(template.variants)}")
            persona = Persona.build(
                seed=index, template=template,
                final_config=template.expand(index, snapshot.environment["firefox_version"]),
                snapshot=snapshot, experimental=True,
                persona_id="persona_" + secrets.token_hex(16),
            )
            self.store.save(persona)
            try:
                await self.start(persona.persona_id)
                report = await self.qualify(persona.persona_id)
                results.append({"persona_id": persona.persona_id, "variant_index": index,
                                "passed": report.passed, "reasons": list(report.reasons)})
            except Exception as exc:
                results.append({"persona_id": persona.persona_id, "variant_index": index,
                                "passed": False, "reasons": [str(exc)]})
            finally:
                await self.stop(persona.persona_id)
            if progress:
                progress(json.dumps(results[-1], ensure_ascii=False))
        return {"template_id": template_id, "results": results,
                "passed": bool(results) and all(item["passed"] for item in results)}
