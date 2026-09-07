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
from typing import Mapping


_AUDIO_ENVIRONMENT_VARS = (
    # PulseAudio client selection and latency.
    "PULSE_SERVER", "PULSE_SINK", "PULSE_SOURCE", "PULSE_LATENCY_MSEC",
    "PULSE_CLIENTCONFIG", "PULSE_RUNTIME_PATH", "PULSE_BINARY",
    # PipeWire endpoint/configuration selection.
    "PIPEWIRE_REMOTE", "PIPEWIRE_CONFIG_FILE", "PIPEWIRE_CONFIG_DIR",
    "PIPEWIRE_LATENCY", "PIPEWIRE_DEBUG", "SPA_PLUGIN_DIR",
    # ALSA and common compatibility backends.
    "ALSA_CONFIG_PATH", "ALSA_CONFIG_DIR", "ALSA_CARD", "ALSA_PCM_CARD",
    "ALSA_PCM_DEVICE", "JACK_DEFAULT_SERVER", "JACK_SERVER_NAME",
    "AUDIODRIVER", "SDL_AUDIODRIVER",
)

_LOADER_ENVIRONMENT_VARS = ("LD_LIBRARY_PATH", "LD_PRELOAD")


def _file_hashes(paths: list[Path]) -> list[list[str]]:
    """Return deterministic hashes while tolerating files removed mid-scan."""

    result: list[list[str]] = []
    for path in sorted(set(paths)):
        try:
            if path.is_file():
                result.append([str(path), hashlib.sha256(path.read_bytes()).hexdigest()])
        except OSError:
            # A package/config update can replace a file during collection. A
            # later snapshot will capture the new contents and invalidate the
            # old fingerprint; collection itself must remain non-fatal.
            continue
    return result


def _ordered_regular_file_hashes(paths: list[Path]) -> list[list[str]]:
    """Hash existing ordinary files in caller-supplied order.

    Loader search order is part of the observed runtime state. ``is_file``
    follows a symlink only when its target is an ordinary file; directories,
    devices and FIFOs are therefore excluded without traversing any tree.
    """

    result: list[list[str]] = []
    seen: set[Path] = set()
    for path in paths:
        try:
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            result.append([str(path), hashlib.sha256(path.read_bytes()).hexdigest()])
        except OSError:
            continue
    return result


def loader_environment(
    prefix: Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Record dynamic-loader inputs that can affect native media libraries.

    This is a partial observation of loader state, not a complete ELF
    dependency resolution. It records both raw variable strings and hashes of
    existing ordinary files referenced by ``LD_PRELOAD``. For
    ``libc++_shared.so`` it records candidates in ``LD_LIBRARY_PATH`` order,
    followed by the prefix library.
    """

    prefix = prefix or Path(os.environ.get("PREFIX", "/usr"))
    source_env = os.environ if environ is None else environ
    library_path = str(source_env.get("LD_LIBRARY_PATH", ""))
    preload = str(source_env.get("LD_PRELOAD", ""))

    cxx_candidates: list[Path] = []
    for directory in library_path.split(os.pathsep):
        if directory:
            cxx_candidates.append(Path(directory) / "libc++_shared.so")
    cxx_candidates.append(prefix / "lib/libc++_shared.so")

    preload_candidates = [Path(value) for value in re.split(r"[\s:]+", preload) if value]
    return {
        "environment": {
            name: str(source_env.get(name, ""))
            for name in _LOADER_ENVIRONMENT_VARS
        },
        "libcxx_shared": _ordered_regular_file_hashes(cxx_candidates),
        "preload_files": _ordered_regular_file_hashes(preload_candidates),
    }


def _pactl_info(environ: Mapping[str, str] | None = None) -> dict[str, object]:
    """Read a small, non-secret subset of ``pactl info``.

    This only invokes the read-only ``info`` command. It deliberately ignores
    Cookie, User Name, Host Name, Server String and stderr contents.
    """

    pactl = shutil.which("pactl")
    if not pactl:
        return {"status": "unavailable", "available": False, "error": "pactl_not_found"}
    command_env = dict(os.environ if environ is None else environ)
    command_env["LC_ALL"] = "C"
    try:
        result = subprocess.run(
            [pactl, "info"], capture_output=True, text=True, timeout=3,
            env=command_env,
        )
    except subprocess.TimeoutExpired:
        return {"status": "error", "available": False, "error": "timeout"}
    except OSError:
        return {"status": "unavailable", "available": False, "error": "pactl_unavailable"}
    if result.returncode != 0:
        return {"status": "error", "available": False, "error": "pactl_exit_nonzero"}

    fields = {
        "Server Name": "server_name",
        "Server Version": "server_version",
        "Default Sample Specification": "default_sample_specification",
        "Default Sink": "default_sink",
        "Default Source": "default_source",
    }
    parsed: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition(":")
        target = fields.get(key.strip()) if separator else None
        if target and value.strip():
            parsed[target] = value.strip()
    if not parsed:
        return {"status": "error", "available": False, "error": "pactl_malformed_output"}
    return {"status": "available", "available": True, **parsed}


def audio_environment(
    prefix: Path | None = None,
    *,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Collect non-secret host audio facts used to bind qualification.

    PulseAudio/PipeWire are shared host services, so this records the client
    selection variables and config-file contents without storing credentials or
    raw ``pactl`` output. Missing services are explicit and non-fatal.
    """

    prefix = prefix or Path(os.environ.get("PREFIX", "/usr"))
    home = home or Path.home()
    source_env = os.environ if environ is None else environ
    variables = {
        name: str(source_env[name])
        for name in _AUDIO_ENVIRONMENT_VARS
        if name in source_env
    }
    config_paths: list[Path] = []
    for directory in (
        prefix / "etc/pulse",
        home / ".config/pulse",
        home / ".pulse",
    ):
        try:
            config_paths.extend(path for path in directory.glob("*.conf") if path.is_file())
        except OSError:
            continue
    return {
        "environment": variables,
        "configuration": _file_hashes(config_paths),
        "pactl": _pactl_info(source_env),
    }


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
                "xorg-server", "openbox", "pulseaudio", "pipewire", "libcubeb",
                "libasound", "alsa", "ffmpeg", "libvpx", "libaom", "dav1d",
                "opus", "x264", "x265", "fdk-aac", "libc++", "libplacebo")
    packages = sorted(line for line in packages.splitlines()
                      if any(word in line.lower() for word in relevant))
    prefix = Path(os.environ.get("PREFIX", "/usr"))
    fonts = font_environment(prefix, Path(firefox))
    facts = {
        "schema_version": 4,
        "runtime_policy_version": 2,
        "firefox_version": match.group(1),
        "platform": platform.system(),
        "architecture": platform.machine(),
        "backend": backend,
        "packages": packages,
        "fonts": fonts,
        "audio_environment": audio_environment(prefix=prefix),
        "loader_environment": loader_environment(prefix=prefix),
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
