"""Private process ownership and local control transport for Persona workers."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time


def process_usage() -> dict:
    """Count visible processes of this app UID, not Android's global limit."""
    count = 0
    rss_kib = 0
    for path in Path("/proc").glob("[0-9]*"):
        try:
            if path.stat().st_uid != os.getuid():
                continue
            stat = (path / "stat").read_text().rsplit(")", 1)[1].split()
            if stat[0] == "Z":
                continue
            count += 1
            for line in (path / "status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    rss_kib += int(line.split()[1])
                    break
        except (OSError, ValueError, IndexError):
            continue
    return {"uid_processes": count, "summed_rss_kib": rss_kib}


def check_process_budget(additional: int) -> dict:
    usage = process_usage()
    if "com.termux" in sys.executable:
        budget = int(os.environ.get("TBP_PERSONA_PROCESS_BUDGET", "30"))
        if budget < 1:
            raise ValueError("TBP_PERSONA_PROCESS_BUDGET must be positive")
        usage["admission_budget"] = budget
        if usage["uid_processes"] + additional > budget:
            raise RuntimeError(
                f"Android process budget: {usage['uid_processes']} visible app processes, "
                f"operation reserves {additional}, budget {budget}. "
                "Stop an unused Persona before continuing. This is a local admission "
                "estimate; other apps and later page processes are outside this count."
            )
    return usage


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def read_json(path: Path) -> dict | None:
    try:
        with path.open() as stream:
            data = json.load(stream)
        return data if isinstance(data, dict) else None
    except (FileNotFoundError, json.JSONDecodeError):
        return None


@contextmanager
def file_lock(path: Path, *, blocking: bool = True):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        yield
    finally:
        os.close(fd)


def process_identity(pid: int) -> dict | None:
    """Linux start ticks prevent stale metadata from targeting a reused PID."""
    try:
        stat = Path(f"/proc/{int(pid)}/stat").read_text()
        values = stat.rsplit(")", 1)[1].split()
        if values[0] == "Z":
            return None
        return {"pid": int(pid), "start_ticks": values[19], "pgid": int(values[2])}
    except (OSError, ValueError, IndexError):
        return None


def is_owned(record: dict | None) -> bool:
    if not record or not isinstance(record.get("pid"), int):
        return False
    current = process_identity(record["pid"])
    return current is not None and current == record


async def terminate_owned(record: dict, timeout: float = 5) -> bool:
    if not is_owned(record):
        return False
    # Only signal a whole group if this process owns that group.  Otherwise
    # signal just the saved PID; never kill the caller's shell/process group.
    def send(sig):
        if not is_owned(record):
            return
        try:
            if record.get("pgid") == record["pid"]:
                os.killpg(record["pid"], sig)
            else:
                os.kill(record["pid"], sig)
        except ProcessLookupError:
            pass
    send(signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while is_owned(record) and time.monotonic() < deadline:
        await asyncio.sleep(0.1)
    if is_owned(record):
        send(signal.SIGKILL)
    return True


def socket_path(root: Path, persona_id: str) -> Path:
    # Android AF_UNIX uses a short sun_path.  Profiles may have long paths,
    # therefore use a private temporary socket namespace with a content hash.
    directory = Path(tempfile.gettempdir()) / "tbp-persona-sockets"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink() or directory.stat().st_uid != os.getuid():
        raise RuntimeError("Persona socket directory is not privately owned")
    os.chmod(directory, 0o700)
    key = hashlib.sha256((str(root.resolve()) + ":" + persona_id).encode()).hexdigest()[:32]
    path = directory / (key + ".sock")
    if len(os.fsencode(path)) >= 104:
        raise RuntimeError("Temporary directory path is too long for a control socket")
    return path


async def request(path: Path, token: str, action: str, params: dict | None = None,
                  timeout: float = 60) -> dict:
    async def exchange():
        reader, writer = await asyncio.open_unix_connection(str(path), limit=4 * 1024 * 1024)
        try:
            data = {"id": 1, "token": token, "action": action, "params": params or {}}
            writer.write(json.dumps(data).encode() + b"\n")
            await writer.drain()
            line = await reader.readline()
            if not line:
                raise ConnectionError("Persona worker closed the connection")
            result = json.loads(line)
            if not isinstance(result, dict):
                raise ConnectionError("Invalid worker response")
            return result
        finally:
            writer.close()
            await writer.wait_closed()
    return await asyncio.wait_for(exchange(), timeout)
