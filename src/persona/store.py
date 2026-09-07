"""Private, atomic persistence for Persona state and qualification reports."""

from __future__ import annotations

import json
from dataclasses import replace
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any, Iterable

from .model import (
    CapabilitySnapshot,
    IncompatibleVersionError,
    Persona,
    PersonaError,
    QualificationReport,
    SchemaVersionError,
)


_PERSONA_ID_RE = re.compile(r"^persona_[0-9a-f]{32}$")
_REPORT_ID_RE = re.compile(r"^[0-9a-f]{64}$")


class PersonaStoreError(PersonaError):
    """Raised for storage paths and atomic persistence failures."""


class PersonaNotFoundError(PersonaStoreError):
    """Raised when a requested Persona does not exist."""


class PersonaStore:
    """Persist Personas below a mode-0700 private directory.

    The store does not persist browser profiles or cookies.  It only stores
    manager metadata and qualification reports.  Every file is written to a
    same-directory 0600 temporary file, fsynced, and atomically replaced.
    """

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)
        self.personas_dir = self.root / "personas"
        self.qualifications_dir = self.root / "qualifications"
        self._prepare_private_dir(self.root)
        self._prepare_private_dir(self.personas_dir)
        self._prepare_private_dir(self.qualifications_dir)

    @staticmethod
    def _prepare_private_dir(path: Path) -> None:
        if path.exists() and path.is_symlink():
            raise PersonaStoreError(f"refusing symlink storage directory: {path}")
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            path.chmod(0o700)
        except OSError as exc:
            raise PersonaStoreError(f"cannot make storage directory private: {path}") from exc
        if stat.S_IMODE(path.stat().st_mode) != 0o700:
            raise PersonaStoreError(f"storage directory is not private (expected mode 0700): {path}")

    @staticmethod
    def _write_atomic(path: Path, value: dict[str, Any]) -> None:
        parent = path.parent
        PersonaStore._prepare_private_dir(parent)
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(parent))
        temporary_path = Path(temporary)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
            if stat.S_IMODE(path.stat().st_mode) != 0o600:
                raise PersonaStoreError(f"store file is not private (expected mode 0600): {path}")
            # Persist the directory entry as well.  Some filesystems do not
            # support directory fsync; in that case the atomic replacement is
            # still safe, and the exception is intentionally ignored.
            try:
                directory_fd = os.open(parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
        except Exception:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
            raise

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        try:
            with path.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
        except FileNotFoundError as exc:
            raise PersonaNotFoundError(f"stored Persona does not exist: {path.stem}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise PersonaStoreError(f"cannot read Persona store file: {path}") from exc
        if not isinstance(value, dict):
            raise PersonaStoreError(f"store file is not a JSON object: {path}")
        return value

    @staticmethod
    def _persona_path(persona_id: str, directory: Path) -> Path:
        if not _PERSONA_ID_RE.fullmatch(persona_id):
            raise PersonaStoreError("invalid Persona id")
        return directory / f"{persona_id}.json"

    @staticmethod
    def _report_path(report: QualificationReport, directory: Path) -> Path:
        # Include the environment digest and exact combination digest so two
        # probes can never overwrite one another's qualification evidence.
        name = f"{report.template_id}--{report.environment_fingerprint}--{report.combination_fingerprint}"
        # Template ids are catalog-owned but still sanitize path separators.
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
        if not _REPORT_ID_RE.fullmatch(report.environment_fingerprint) or not _REPORT_ID_RE.fullmatch(report.combination_fingerprint):
            raise PersonaStoreError("qualification fingerprints must be sha256 hex strings")
        return directory / f"{safe}.json"

    def save(self, persona: Persona) -> Path:
        if not isinstance(persona, Persona):
            raise PersonaStoreError("save expects a Persona")
        path = self._persona_path(persona.persona_id, self.personas_dir)
        self._write_atomic(path, persona.to_dict())
        return path

    def save_persona(self, persona: Persona) -> Path:
        return self.save(persona)

    def load(self, persona_id: str) -> Persona:
        path = self._persona_path(persona_id, self.personas_dir)
        data = self._read(path)
        # from_mapping enforces schema and generator major compatibility.  Do
        # not catch those exceptions: a stale file must fail loudly instead of
        # silently regenerating a different Persona.
        return Persona.from_mapping(data)

    def load_persona(self, persona_id: str) -> Persona:
        return self.load(persona_id)

    def list(self) -> list[Persona]:
        result: list[Persona] = []
        for path in sorted(self.personas_dir.glob("persona_*.json")):
            try:
                result.append(Persona.from_mapping(self._read(path)))
            except (SchemaVersionError, IncompatibleVersionError, PersonaError):
                # Listing should not hide incompatible state.  The first
                # incompatible entry is surfaced to the caller.
                raise
        return result

    def list_personas(self) -> list[Persona]:
        return self.list()

    def delete(self, persona_id: str) -> None:
        path = self._persona_path(persona_id, self.personas_dir)
        try:
            path.unlink()
        except FileNotFoundError as exc:
            raise PersonaNotFoundError(f"stored Persona does not exist: {persona_id}") from exc

    def save_qualification(self, report: QualificationReport) -> Path:
        if not isinstance(report, QualificationReport):
            raise PersonaStoreError("save_qualification expects a QualificationReport")
        path = self._report_path(report, self.qualifications_dir)
        self._write_atomic(path, report.to_dict())
        return path

    def commit_requalification(self, original: Persona, snapshot: CapabilitySnapshot,
                               qualification: QualificationReport) -> Persona:
        """Commit under the manager's lifecycle lock, with identity preserved.

        Supporting files are durable before the single atomic Persona replace.
        A crash beforehand leaves the original metadata unchanged.
        """
        if self.load(original.persona_id).to_dict() != original.to_dict():
            raise PersonaStoreError("Saved Persona changed during requalification")
        if (qualification.passed is not True or qualification.full_combination is not True
                or qualification.environment_fingerprint != snapshot.fingerprint):
            raise PersonaStoreError("Requalification must pass for the new snapshot")
        updated = replace(original, capability_snapshot_fingerprint=snapshot.fingerprint,
                          qualification=qualification)
        # Reuse persisted schema/config/qualification binding validation rather
        # than silently rewriting any saved identity field.
        updated = Persona.from_mapping(updated.to_dict())
        self.save_snapshot(snapshot)
        self.save_qualification(qualification)
        self.save(updated)
        return updated

    def load_qualification(
        self,
        template_id: str,
        environment_fingerprint: str,
        combination_digest: str,
    ) -> QualificationReport:
        if not _REPORT_ID_RE.fullmatch(environment_fingerprint) or not _REPORT_ID_RE.fullmatch(combination_digest):
            raise PersonaStoreError("qualification fingerprints must be sha256 hex strings")
        safe = re.sub(
            r"[^A-Za-z0-9_.-]",
            "_",
            f"{template_id}--{environment_fingerprint}--{combination_digest}",
        )
        data = self._read(self.qualifications_dir / f"{safe}.json")
        report = QualificationReport.from_mapping(data)
        if (
            report.template_id != template_id
            or report.environment_fingerprint != environment_fingerprint
            or report.combination_fingerprint != combination_digest
        ):
            raise PersonaStoreError("qualification path does not match report contents")
        return report

    def list_qualifications(self) -> list[QualificationReport]:
        reports: list[QualificationReport] = []
        for path in sorted(self.qualifications_dir.glob("*.json")):
            reports.append(QualificationReport.from_mapping(self._read(path)))
        return reports

    def save_snapshot(self, snapshot: CapabilitySnapshot) -> Path:
        """Store a probe snapshot for diagnostics and report reconstruction."""
        if not isinstance(snapshot, CapabilitySnapshot):
            raise PersonaStoreError("save_snapshot expects a CapabilitySnapshot")
        path = self.root / "snapshots" / f"{snapshot.fingerprint}.json"
        self._prepare_private_dir(path.parent)
        self._write_atomic(path, snapshot.to_dict())
        return path

    def load_snapshot(self, fingerprint: str) -> CapabilitySnapshot:
        if not _REPORT_ID_RE.fullmatch(fingerprint):
            raise PersonaStoreError("snapshot fingerprint must be sha256 hex")
        path = self.root / "snapshots" / f"{fingerprint}.json"
        return CapabilitySnapshot.from_mapping(self._read(path))


__all__ = [
    "PersonaNotFoundError",
    "PersonaStore",
    "PersonaStoreError",
]
