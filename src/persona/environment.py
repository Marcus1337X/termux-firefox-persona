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
    # Font content/installation changes can alter Canvas and text metrics even
    # when the Firefox package version is unchanged.
    prefix = Path(os.environ.get("PREFIX", "/usr"))
    font_dirs = [prefix / "share/fonts", Path.home() / ".fonts",
                 Path.home() / ".local/share/fonts"]
    fonts = []
    for directory in font_dirs:
        if directory.is_dir():
            for path in sorted(directory.rglob("*")):
                if path.is_file() and path.suffix.lower() in {".ttf", ".otf", ".ttc"}:
                    stat = path.stat()
                    fonts.append([str(path.relative_to(directory)), stat.st_size,
                                  stat.st_mtime_ns])
    facts = {
        "schema_version": 1,
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
