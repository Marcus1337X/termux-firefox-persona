"""Versioned host facts used to invalidate local Persona qualifications."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess


def _output(args: list[str], timeout: int = 10) -> str:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return result.stdout.strip() if result.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def font_environment(prefix: Path, firefox: Path, *, home: Path | None = None,
                     system_roots: tuple[Path, ...] | None = None) -> dict:
    """Track font files and fontconfig rules, including Android system fonts."""
    home = home or Path.home()
    system_roots = system_roots if system_roots is not None else (
        Path("/system/fonts"), Path("/product/fonts"), Path("/vendor/fonts"))
    directories = [prefix / "share/fonts", home / ".fonts", home / ".local/share/fonts",
                   prefix / "lib/firefox/fonts", firefox.resolve().parent / "fonts", *system_roots]
    files = []
    for directory in sorted(set(directories)):
        if directory.is_dir():
            for path in sorted(directory.rglob("*")):
                if path.is_file() and path.suffix.lower() in {".ttf", ".otf", ".ttc"}:
                    stat = path.stat()
                    files.append([str(directory), str(path.relative_to(directory)),
                                  stat.st_size, stat.st_mtime_ns])
    config_roots = [prefix / "etc/fonts", home / ".config/fontconfig"]
    if os.environ.get("FONTCONFIG_PATH"):
        config_roots.extend(Path(value) for value in os.environ["FONTCONFIG_PATH"].split(os.pathsep) if value)
    configs = {home / ".fonts.conf"}
    for root in config_roots:
        if root.is_dir():
            configs.update(root.rglob("*.conf"))
    if os.environ.get("FONTCONFIG_FILE"):
        configs.add(Path(os.environ["FONTCONFIG_FILE"]).expanduser())
    return {"files": files,
            "configuration": [[str(path), hashlib.sha256(path.read_bytes()).hexdigest()]
                              for path in sorted(configs) if path.is_file()]}


def snapshot(backend: str = "software") -> dict:
    """Return reproducible, non-secret facts; this is not browser qualification."""
    if backend not in {"software", "native"}:
        raise ValueError("backend must be software or native")
    firefox = shutil.which("firefox")
    if not firefox:
        raise RuntimeError("Firefox is not installed")
    version_output = _output([firefox, "--version"])
    match = re.search(r"Firefox\s+([\d.]+[\w.-]*)", version_output)
    if not match:
        raise RuntimeError("Cannot determine Firefox version")
    packages = _output(["dpkg-query", "-W", "-f=${Package}=${Version}\n"])
    relevant = ("firefox", "mesa", "virgl", "font", "freetype", "harfbuzz",
                "xorg-server", "openbox", "pulseaudio", "pipewire", "ffmpeg")
    packages = sorted(line for line in packages.splitlines()
                      if any(word in line.lower() for word in relevant))
    prefix = Path(os.environ.get("PREFIX", "/usr"))
    fonts = font_environment(prefix, Path(firefox))
    facts = {
        "schema_version": 2,
        "runtime_policy_version": 2,
        "firefox_version": match.group(1),
        "platform": platform.system(),
        "architecture": platform.machine(),
        "backend": backend,
        "packages": packages,
        "fonts": fonts,
        "graphics_environment": {name: os.environ.get(name) for name in (
            "GALLIUM_DRIVER", "MESA_LOADER_DRIVER_OVERRIDE", "LIBGL_DRIVERS_PATH",
            "LIBGL_ALWAYS_SOFTWARE", "MESA_GL_VERSION_OVERRIDE",
            "MESA_GLES_VERSION_OVERRIDE", "MESA_GLSL_VERSION_OVERRIDE",
            "MESA_EXTENSION_OVERRIDE", "FONTCONFIG_FILE", "FONTCONFIG_PATH")},
    }
    fingerprint = hashlib.sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest()
    return {"id": fingerprint, **facts}


def doctor() -> dict:
    binaries = {name: shutil.which(name) for name in (
        "firefox", "Xvfb", "openbox", "xdotool", "xclip", "import")}
    try:
        import websockets
        websocket_version = websockets.__version__
    except ImportError:
        websocket_version = None
    return {
        "binaries": binaries,
        "websockets": websocket_version,
        "ready": all(binaries.values()) and websocket_version is not None,
        "note": "Host prerequisites only; templates require local browser qualification.",
    }
