"""Persistent Persona creation and one isolated control process per identity."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time

from .control import atomic_json, check_process_budget, file_lock, is_owned, read_json, request, socket_path, terminate_owned
from .environment import snapshot as host_snapshot
from .model import CapabilitySnapshot, Persona, PersonaGenerator, TemplateCatalog, QualificationError
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
                "state": state.get("status", "stopped") if alive else ("interrupted" if interrupted else "stopped"),
                "instance_id": state.get("instance_id"),
                "display": state.get("display") if alive else None,
                "pid": state.get("worker", {}).get("pid") if alive else None,
                "last_error": state.get("error") or ("Worker exited without cleanup" if interrupted else None), "alive": alive}

    def list(self) -> list[dict]:
        return [self.status(persona.persona_id) for persona in self.store.list()]

    async def start(self, persona_id: str, *, timeout: float = 75) -> dict:
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
        paths = self.paths(persona_id)
        # Nonblocking: concurrent callers receive a clear busy error instead of
        # blocking this event loop while another process boots Firefox.
        with file_lock(Path(tempfile.gettempdir()) / "tbp-persona-admission.lock", blocking=False), file_lock(paths["lock"], blocking=False):
            old = read_json(paths["state"]) or {}
            if is_owned(old.get("worker")):
                if old.get("status") == "ready":
                    response = await request(paths["socket"], old["token"], "status", timeout=5)
                    if not response.get("success"):
                        raise RuntimeError(response.get("error", "Worker is not ready"))
                    return self.status(persona_id)
                raise RuntimeError("Persona worker is already starting or stopping")
            # Only recover resources whose saved PID and start ticks still match.
            for record in reversed(old.get("resources", [])):
                await terminate_owned(record)
            usage = check_process_budget(additional=10)
            try:
                paths["socket"].unlink()
            except FileNotFoundError:
                pass
            token = secrets.token_hex(32)
            instance_id = secrets.token_hex(16)
            atomic_json(paths["state"], {"status": "starting", "instance_id": instance_id,
                                        "token": token, "backend": self.backend,
                                        "startup_usage": usage})
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
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                state = read_json(paths["state"]) or {}
                if state.get("instance_id") != instance_id:
                    raise RuntimeError("Persona startup ownership changed unexpectedly")
                if state.get("status") == "ready" and paths["socket"].exists():
                    response = await request(paths["socket"], token, "status", timeout=5)
                    if response.get("success"):
                        return self.status(persona_id)
                if state.get("status") == "failed" or child.poll() is not None:
                    raise RuntimeError(state.get("error") or f"Worker exited; see {paths['log']}")
                await asyncio.sleep(0.2)
            # Gracefully cancel our own child; its SIGTERM handler cleans Firefox.
            child.terminate()
            try:
                await asyncio.to_thread(child.wait, 15)
            except subprocess.TimeoutExpired:
                state = read_json(paths["state"]) or {}
                await terminate_owned(state.get("worker", {}))
            raise TimeoutError(f"Persona startup timed out; see {paths['log']}")

    async def command(self, persona_id: str, action: str, params: dict | None = None,
                      *, timeout: float = 60) -> dict:
        paths = self.paths(persona_id)
        state = read_json(paths["state"]) or {}
        if not is_owned(state.get("worker")) or state.get("status") != "ready":
            raise RuntimeError("Persona is not running; start it first")
        result = await request(paths["socket"], state["token"], action, params, timeout=timeout)
        if not result.get("success"):
            raise RuntimeError(result.get("error", "Persona command failed"))
        return result.get("data")

    async def stop(self, persona_id: str, *, timeout: float = 20) -> dict:
        paths = self.paths(persona_id)
        with file_lock(paths["lock"], blocking=False):
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
            for record in reversed(state.get("resources", [])):
                await terminate_owned(record)
            final = read_json(paths["state"]) or state
            if final:
                final.update(status="stopped", resources=[])
                atomic_json(paths["state"], final)
            return self.status(persona_id)

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
