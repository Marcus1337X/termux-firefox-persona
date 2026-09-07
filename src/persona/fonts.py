"""Private Persona font inventories and fontconfig environments.

The Android Termux image used by the project does not install the
``fc-list`` command.  Fontconfig itself is present, so this module talks to
its C API through :mod:`ctypes`.  A Persona receives a small fontconfig
configuration that names only the font files selected for that Persona.

The module intentionally treats Fontconfig's fallback result as a diagnostic,
not as proof that a requested family exists.  ``FcFontMatch`` may return a
substitute for an unknown family; callers must get ``FontNotFoundError`` in
that case rather than silently accepting the substitute.
"""

from __future__ import annotations

import ctypes
import ctypes.util
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
from typing import Iterable, Mapping
from xml.etree import ElementTree


FONTCONFIG_LIBRARY_CANDIDATES = (
    "/data/data/com.termux/files/usr/lib/libfontconfig.so",
    "libfontconfig.so.1",
    "libfontconfig.so",
)

# These are deliberately a small, finite policy choice.  They are aliases to
# real family names and do not ask Fontconfig to synthesize a fallback.
DEFAULT_FONT_ALIASES = {
    "sans": "DejaVu Sans",
    "sans-serif": "DejaVu Sans",
    "system-ui": "DejaVu Sans",
    "serif": "DejaVu Serif",
    "monospace": "DejaVu Sans Mono",
    "mono": "DejaVu Sans Mono",
    "emoji": "Noto Color Emoji",
    "cjk": "Noto Sans CJK SC",
    "cjk-sans": "Noto Sans CJK SC",
}

_FC_RESULT_MATCH = 0
_FC_MATCH_PATTERN = 0
_FC_SET_SYSTEM = 0


class FontError(RuntimeError):
    """Base class for font inventory and private configuration errors."""


class FontconfigUnavailableError(FontError):
    """The Fontconfig shared library could not be loaded or initialized."""


class FontNotFoundError(FontError):
    """A requested family was not an actual Fontconfig family."""


@dataclass(frozen=True)
class FontRecord:
    """One Fontconfig family/style/file record."""

    family: str
    style: str
    file: Path

    @property
    def path(self) -> Path:
        """Compatibility spelling for callers that use ``path``."""
        return self.file


@dataclass(frozen=True)
class FontConfigBundle:
    """Private fontconfig files and the environment passed to Firefox."""

    config_file: Path
    fonts_dir: Path
    cache_dir: Path
    records: tuple[FontRecord, ...]
    aliases: Mapping[str, str]

    @property
    def env(self) -> dict[str, str]:
        return {
            "FONTCONFIG_FILE": str(self.config_file),
            "FONTCONFIG_PATH": str(self.config_file.parent),
        }

    @property
    def environment(self) -> dict[str, str]:
        return self.env

    @property
    def prefs(self) -> dict[str, str]:
        """Return no Firefox font whitelist prefs.

        ``font.system.whitelist`` also blocks CSS ``local()`` resolution in
        Firefox.  The process-scoped ``FONTCONFIG_FILE`` is the isolation
        mechanism; adding that pref would break local font probes.
        """
        return {}

    @property
    def firefox_prefs(self) -> dict[str, str]:
        return self.prefs

    @property
    def fontconfig_file(self) -> Path:
        return self.config_file


class _FcFontSet(ctypes.Structure):
    _fields_ = [
        ("nfont", ctypes.c_int),
        ("sfont", ctypes.c_int),
        ("fonts", ctypes.POINTER(ctypes.c_void_p)),
    ]


def _load_fontconfig(library_path: str | os.PathLike[str] | None = None):
    candidates = []
    if library_path:
        candidates.append(os.fspath(library_path))
    discovered = ctypes.util.find_library("fontconfig")
    if discovered:
        candidates.append(discovered)
    candidates.extend(FONTCONFIG_LIBRARY_CANDIDATES)
    errors = []
    for candidate in dict.fromkeys(candidates):
        try:
            return ctypes.CDLL(candidate)
        except OSError as exc:
            errors.append(f"{candidate}: {exc}")
    detail = "; ".join(errors)
    raise FontconfigUnavailableError(
        "could not load libfontconfig.so" + (f": {detail}" if detail else "")
    )


def _bind_fontconfig(lib):
    """Declare only the stable Fontconfig ABI used by this module."""
    lib.FcInit.restype = ctypes.c_int
    lib.FcGetVersion.restype = ctypes.c_int
    lib.FcConfigCreate.restype = ctypes.c_void_p
    lib.FcConfigDestroy.argtypes = [ctypes.c_void_p]
    lib.FcConfigParseAndLoad.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    lib.FcConfigParseAndLoad.restype = ctypes.c_int
    lib.FcConfigBuildFonts.argtypes = [ctypes.c_void_p]
    lib.FcConfigBuildFonts.restype = ctypes.c_int
    lib.FcConfigGetCurrent.restype = ctypes.c_void_p
    lib.FcConfigGetFonts.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.FcConfigGetFonts.restype = ctypes.POINTER(_FcFontSet)

    lib.FcPatternGetString.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_char_p),
    ]
    lib.FcPatternGetString.restype = ctypes.c_int

    lib.FcPatternCreate.restype = ctypes.c_void_p
    lib.FcPatternDestroy.argtypes = [ctypes.c_void_p]
    lib.FcPatternAddString.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
    ]
    lib.FcPatternAddString.restype = ctypes.c_int
    lib.FcConfigSubstitute.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int]
    lib.FcConfigSubstitute.restype = ctypes.c_int
    lib.FcDefaultSubstitute.argtypes = [ctypes.c_void_p]
    lib.FcFontMatch.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_int),
    ]
    lib.FcFontMatch.restype = ctypes.c_void_p
    return lib


def _decode(value: bytes | None) -> str:
    return os.fsdecode(value or b"")


def _fold(value: str) -> str:
    return " ".join(value.casefold().split())


class FontDatabase:
    """Read actual Fontconfig records without invoking external commands."""

    def __init__(
        self,
        library_path: str | os.PathLike[str] | None = None,
        config: ctypes.c_void_p | None = None,
        config_file: str | os.PathLike[str] | None = None,
    ):
        self._lib = _bind_fontconfig(_load_fontconfig(library_path))
        if not self._lib.FcInit():
            raise FontconfigUnavailableError("FcInit failed")
        self._config_file = config_file
        self._owns_config = config is None
        if config is None:
            # Do not use FcConfigGetCurrent(): it is process-global and can
            # retain an old font cache after a font is installed or removed.
            # Each database receives a fresh config and rebuilds its font set.
            self._config = self._lib.FcConfigCreate()
            if not self._config:
                raise FontconfigUnavailableError("FcConfigCreate returned NULL")
            filename = None if config_file is None else os.fsencode(config_file)
            if not self._lib.FcConfigParseAndLoad(self._config, filename, 1):
                self._lib.FcConfigDestroy(self._config)
                self._config = None
                raise FontconfigUnavailableError("FcConfigParseAndLoad failed")
            if not self._lib.FcConfigBuildFonts(self._config):
                self._lib.FcConfigDestroy(self._config)
                self._config = None
                raise FontconfigUnavailableError("FcConfigBuildFonts failed")
        else:
            self._config = config
        if not self._config:
            raise FontconfigUnavailableError("FcConfigGetCurrent returned NULL")

    def refresh(self) -> "FontDatabase":
        """Rebuild this database's independent Fontconfig config in place."""
        if not self._owns_config:
            raise FontconfigUnavailableError(
                "cannot refresh a FontDatabase using an external FcConfig"
            )
        old_config = self._config
        new_config = self._lib.FcConfigCreate()
        if not new_config:
            raise FontconfigUnavailableError("FcConfigCreate returned NULL")
        try:
            filename = (
                None
                if self._config_file is None
                else os.fsencode(self._config_file)
            )
            if not self._lib.FcConfigParseAndLoad(new_config, filename, 1):
                raise FontconfigUnavailableError("FcConfigParseAndLoad failed")
            if not self._lib.FcConfigBuildFonts(new_config):
                raise FontconfigUnavailableError("FcConfigBuildFonts failed")
            self._config = new_config
            self._lib.FcConfigDestroy(old_config)
            return self
        except Exception:
            self._lib.FcConfigDestroy(new_config)
            raise

    def close(self) -> None:
        if getattr(self, "_owns_config", False) and getattr(self, "_config", None):
            self._lib.FcConfigDestroy(self._config)
            self._config = None

    def __del__(self):  # pragma: no cover - interpreter shutdown ordering
        try:
            self.close()
        except Exception:
            pass

    @property
    def version(self) -> int:
        return int(self._lib.FcGetVersion())

    def _pattern_value(self, pattern, object_name: bytes, index: int = 0) -> str:
        value = ctypes.c_char_p()
        result = self._lib.FcPatternGetString(
            pattern, object_name, index, ctypes.byref(value)
        )
        return _decode(value.value) if result == _FC_RESULT_MATCH else ""

    def list_fonts(self) -> tuple[FontRecord, ...]:
        font_set = self._lib.FcConfigGetFonts(self._config, _FC_SET_SYSTEM)
        if not font_set:
            return ()
        records = []
        seen = set()
        for index in range(max(0, int(font_set.contents.nfont))):
            pattern = font_set.contents.fonts[index]
            family = self._pattern_value(pattern, b"family")
            style = self._pattern_value(pattern, b"style")
            filename = self._pattern_value(pattern, b"file")
            if not family or not filename:
                continue
            path = Path(filename)
            key = (_fold(family), _fold(style), str(path))
            if key in seen:
                continue
            seen.add(key)
            records.append(FontRecord(family, style, path))
        return tuple(records)

    def families(self) -> tuple[str, ...]:
        """Return unique actual family names, preserving Fontconfig order."""
        result = []
        seen = set()
        for record in self.list_fonts():
            key = _fold(record.family)
            if key not in seen:
                seen.add(key)
                result.append(record.family)
        return tuple(result)

    def match(
        self,
        family: str,
        style: str | None = None,
        allowed_families: Iterable[str] | None = None,
    ) -> FontRecord:
        """Resolve a family with Fontconfig and reject fallback families."""
        if not isinstance(family, str) or not family.strip():
            raise FontNotFoundError("font family must be a non-empty string")
        pattern = self._lib.FcPatternCreate()
        if not pattern:
            raise FontconfigUnavailableError("FcPatternCreate returned NULL")
        try:
            if not self._lib.FcPatternAddString(
                pattern, b"family", family.encode("utf-8")
            ):
                raise FontconfigUnavailableError("FcPatternAddString(family) failed")
            if style is not None and not self._lib.FcPatternAddString(
                pattern, b"style", style.encode("utf-8")
            ):
                raise FontconfigUnavailableError("FcPatternAddString(style) failed")
            if not self._lib.FcConfigSubstitute(self._config, pattern, _FC_MATCH_PATTERN):
                raise FontconfigUnavailableError("FcConfigSubstitute failed")
            self._lib.FcDefaultSubstitute(pattern)
            result = ctypes.c_int()
            matched = self._lib.FcFontMatch(self._config, pattern, ctypes.byref(result))
            if not matched or result.value != _FC_RESULT_MATCH:
                raise FontNotFoundError(f"font family {family!r} was not matched")
            try:
                actual_family = self._pattern_value(matched, b"family")
                actual_style = self._pattern_value(matched, b"style")
                filename = self._pattern_value(matched, b"file")
                allowed = None if allowed_families is None else {
                    _fold(item) for item in allowed_families
                }
                if (_fold(actual_family) != _fold(family)
                        or (allowed is not None and _fold(actual_family) not in allowed)
                        or not filename):
                    raise FontNotFoundError(
                        f"font family {family!r} resolved to fallback {actual_family!r}"
                    )
                return FontRecord(actual_family, actual_style, Path(filename))
            finally:
                self._lib.FcPatternDestroy(matched)
        finally:
            self._lib.FcPatternDestroy(pattern)

    def resolve_allowed(
        self,
        families: Iterable[str] | Mapping[str, str],
        aliases: Mapping[str, str] | None = None,
    ) -> tuple[FontRecord, ...]:
        """Resolve only requested families and fail on every missing family."""
        if isinstance(families, Mapping):
            requested = list(families.items())
        else:
            requested = [(name, None) for name in families]
        alias_map = {_fold(key): value for key, value in (aliases or {}).items()}
        result = []
        seen = set()
        for name, style in requested:
            if not isinstance(name, str) or not name.strip():
                raise FontNotFoundError("font family whitelist contains an empty name")
            target = alias_map.get(_fold(name), name)
            record = self.match(target, style)
            source = str(record.file.resolve(strict=True))
            key = (_fold(record.family), source)
            if key not in seen:
                seen.add(key)
                result.append(FontRecord(record.family, record.style, Path(source)))
        return tuple(result)


def enumerate_fonts(
    library_path: str | os.PathLike[str] | None = None,
) -> tuple[FontRecord, ...]:
    """Enumerate installed font records through Fontconfig's C API."""
    return FontDatabase(library_path).list_fonts()


def match_font(
    family: str,
    style: str | None = None,
    library_path: str | os.PathLike[str] | None = None,
    allowed_families: Iterable[str] | None = None,
) -> FontRecord:
    """Match a requested family while rejecting Fontconfig fallback."""
    return FontDatabase(library_path).match(family, style, allowed_families)


def _atomic_write(path: Path, content: bytes, mode: int = 0o600) -> None:
    _ensure_private_dir(path.parent)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temp_path = Path(temp_name)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def _lexists(path: Path) -> bool:
    return os.path.lexists(os.fspath(path))


def _ensure_private_dir(path: Path) -> Path:
    """Create/check a directory without ever following a directory symlink."""
    if _lexists(path):
        if path.is_symlink():
            raise FontError(f"private directory must not be a symlink: {path}")
        if not path.is_dir():
            raise FontError(f"private path is not a directory: {path}")
    else:
        path.mkdir(mode=0o700)
    # chmod failures are security failures; do not silently continue.
    os.chmod(path, 0o700)
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise FontError(f"private directory permissions are too broad: {path}")
    return path


def _reject_symlinks_below(root: Path) -> None:
    """Reject symlink entries in an owned tree before any reads or writes."""
    if root.is_symlink():
        raise FontError(f"private path must not be a symlink: {root}")
    if not root.is_dir():
        raise FontError(f"private path is not a directory: {root}")
    for current, dirs, files in os.walk(root, followlinks=False):
        for name in (*dirs, *files):
            entry = Path(current) / name
            if entry.is_symlink():
                raise FontError(f"private tree contains a symlink: {entry}")


def _safe_root(path: str | os.PathLike[str]) -> Path:
    root = Path(path).expanduser()
    if _lexists(root) and root.is_symlink():
        raise FontError(f"private root must not be a symlink: {root}")
    if not _lexists(root):
        root.mkdir(parents=True, mode=0o700)
    return _ensure_private_dir(root).resolve()


def _ensure_owned_dir(path: Path, marker_name: str, marker_content: bytes) -> Path:
    """Ensure a private directory is ours before entering or modifying it."""
    marker = path / marker_name
    if _lexists(path):
        if path.is_symlink() or not path.is_dir():
            raise FontError(f"private path is not an owned directory: {path}")
        if marker.is_symlink() or not marker.is_file():
            raise FontError(f"private directory has no ownership marker: {path}")
        try:
            existing = marker.read_bytes()
        except OSError as exc:
            raise FontError(f"cannot read private ownership marker: {marker}") from exc
        if existing != marker_content:
            raise FontError(f"private directory ownership marker does not match: {path}")
    else:
        path.mkdir(mode=0o700)
        _atomic_write(marker, marker_content)
    return _ensure_private_dir(path)


def _xml_config(
    font_dir: Path,
    cache_dir: Path,
    aliases: Mapping[str, str],
    allowed_families: Iterable[str],
) -> bytes:
    root = ElementTree.Element("fontconfig")
    ElementTree.SubElement(root, "dir").text = str(font_dir)
    ElementTree.SubElement(root, "cachedir").text = str(cache_dir)
    selectfont = ElementTree.SubElement(root, "selectfont")
    # An accept list alone does not exclude other faces in a TTC collection.
    rejectfont = ElementTree.SubElement(selectfont, "rejectfont")
    ElementTree.SubElement(rejectfont, "pattern")
    acceptfont = ElementTree.SubElement(selectfont, "acceptfont")
    for family in sorted(set(allowed_families), key=_fold):
        pattern = ElementTree.SubElement(acceptfont, "pattern")
        patelt = ElementTree.SubElement(pattern, "patelt", {"name": "family"})
        ElementTree.SubElement(patelt, "string").text = family
    for alias, target in sorted(aliases.items(), key=lambda item: _fold(item[0])):
        alias_node = ElementTree.SubElement(root, "alias")
        ElementTree.SubElement(alias_node, "family").text = alias
        prefer = ElementTree.SubElement(alias_node, "prefer")
        ElementTree.SubElement(prefer, "family").text = target
    payload = ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)
    return payload + b"\n"


def build_persona_fontconfig(
    root: str | os.PathLike[str],
    whitelist: Iterable[str] | Mapping[str, str] | Mapping[str, object],
    *,
    aliases: Mapping[str, str] | None = None,
    database: FontDatabase | None = None,
    copy_fonts: bool = False,
) -> FontConfigBundle:
    """Build an idempotent, private Fontconfig tree for one Persona.

    ``root`` is owned by the Persona.  The generated tree is content-addressed
    so repeated starts reuse the same files and a changed whitelist receives a
    new private directory.  The default uses absolute symlinks to verified
    source files; ``copy_fonts=True`` copies them into the private tree.
    """
    db = database or FontDatabase()
    blocked = set()
    policy = "whitelist"
    # Accept the Persona template's structured font policy directly while
    # retaining the compact iterable/mapping API for lower-level callers.
    if isinstance(whitelist, Mapping) and "families" in whitelist:
        policy = whitelist.get("policy", "whitelist")
        if policy != "whitelist":
            raise FontError(f"unsupported font policy: {policy!r}")
        requested = whitelist["families"]
        if not isinstance(requested, (list, tuple, set, frozenset, Mapping)):
            raise FontError("fonts.families must be a sequence or family/style map")
        blocked = {
            _fold(name) for name in whitelist.get("blocked_families", ())
            if isinstance(name, str)
        }
        structured_aliases = whitelist.get("aliases")
        if aliases is None and structured_aliases is not None:
            if not isinstance(structured_aliases, Mapping):
                raise FontError("fonts.aliases must be an object")
            aliases = {str(key): str(value) for key, value in structured_aliases.items()}
    else:
        requested = dict(whitelist) if isinstance(whitelist, Mapping) else list(whitelist)
    if isinstance(requested, Mapping):
        requested_names = requested.keys()
    else:
        requested_names = requested
    requested_names = list(requested_names)
    if any(_fold(name) in blocked for name in requested_names):
        raise FontError("font whitelist contains a blocked family")
    alias_source = DEFAULT_FONT_ALIASES if aliases is None else dict(aliases)
    records = db.resolve_allowed(requested, alias_source)
    selected = {_fold(record.family): record.family for record in records}
    configured_aliases = {}
    for alias, target in alias_source.items():
        if _fold(target) in selected:
            configured_aliases[str(alias)] = selected[_fold(target)]
        elif aliases is not None:
            raise FontNotFoundError(
                f"alias {alias!r} targets unselected family {target!r}"
            )

    identity = {
        "records": [
            {"family": r.family, "style": r.style, "file": str(r.file)}
            for r in records
        ],
        "aliases": sorted(configured_aliases.items()),
        "policy": policy,
        "copy": bool(copy_fonts),
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:24]
    persona_root = _safe_root(root)
    owner_marker = b'{"owner":"termux-browser-pilot-fontconfig","version":1}\n'
    fontconfig_root = _ensure_owned_dir(
        persona_root / "fontconfig", ".tbp-owner.json", owner_marker
    )
    tree_root = fontconfig_root / digest
    fonts_dir = tree_root / "fonts"
    cache_dir = tree_root / "cache"
    config_file = tree_root / "fonts.conf"

    expected = []
    for index, record in enumerate(records):
        source = record.file.resolve(strict=True)
        if not source.is_file():
            raise FontNotFoundError(f"font source is not a regular file: {source}")
        suffix = source.suffix.lower() or ".font"
        filename = (
            f"{index:04d}-{hashlib.sha256(str(source).encode()).hexdigest()[:12]}"
            f"{suffix}"
        )
        expected.append((filename, source))

    marker = tree_root / ".tbp-manifest.json"
    marker_payload = {
        "version": 1,
        "digest": digest,
        "copy": bool(copy_fonts),
        "files": [{"name": name, "source": str(source)} for name, source in expected],
    }
    marker_bytes = (json.dumps(marker_payload, sort_keys=True) + "\n").encode("utf-8")
    expected_names = {name for name, _ in expected}
    allowed_families = tuple(
        sorted({record.family for record in records}, key=_fold)
    )
    config_bytes = _xml_config(
        fonts_dir, cache_dir, configured_aliases, allowed_families
    )

    if _lexists(tree_root):
        if tree_root.is_symlink() or not tree_root.is_dir():
            raise FontError(f"fontconfig bundle path is not an owned directory: {tree_root}")
        if marker.is_symlink() or not marker.is_file():
            raise FontError(f"fontconfig bundle has no ownership manifest: {tree_root}")
        try:
            existing_manifest = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise FontError(f"invalid fontconfig ownership manifest: {marker}") from exc
        if existing_manifest != marker_payload:
            raise FontError(f"fontconfig bundle manifest does not match: {tree_root}")
        _ensure_private_dir(fonts_dir)
        _ensure_private_dir(cache_dir)
        _reject_symlinks_below(cache_dir)
        if config_file.is_symlink() or not config_file.is_file():
            raise FontError(f"fontconfig bundle config is missing or unsafe: {config_file}")
        if config_file.read_bytes() != config_bytes:
            raise FontError(f"fontconfig bundle config was modified: {config_file}")
        actual_names = {entry.name for entry in fonts_dir.iterdir()}
        if actual_names != expected_names:
            raise FontError(f"fontconfig bundle files do not match its manifest: {fonts_dir}")
        complete = True
        for name, source in expected:
            target = fonts_dir / name
            if copy_fonts:
                if target.is_symlink() or not target.is_file():
                    complete = False
                    break
            elif not target.is_symlink() or target.resolve(strict=True) != source:
                complete = False
                break
        if complete:
            return FontConfigBundle(
                config_file=config_file,
                fonts_dir=fonts_dir,
                cache_dir=cache_dir,
                records=records,
                aliases=dict(configured_aliases),
            )
        # The manifest proves that this incomplete tree belongs to this bundle.
        # Rebuild it rather than writing into an unowned directory.
        shutil.rmtree(tree_root)

    tree_root.mkdir(mode=0o700)
    _ensure_private_dir(tree_root)
    _atomic_write(marker, marker_bytes)
    _ensure_private_dir(fonts_dir)
    _ensure_private_dir(cache_dir)
    for name, source in expected:
        target = fonts_dir / name
        if copy_fonts:
            shutil.copy2(source, target)
            os.chmod(target, 0o600)
            if stat.S_IMODE(target.stat().st_mode) & 0o077:
                raise FontError(f"private font permissions are too broad: {target}")
        else:
            os.symlink(str(source), target)
        if copy_fonts:
            valid = target.is_file() and not target.is_symlink()
        else:
            valid = target.is_symlink() and target.resolve(strict=True) == source
        if not valid:
            raise FontError(f"failed to create private font file: {target}")
    _atomic_write(config_file, config_bytes)

    return FontConfigBundle(
        config_file=config_file,
        fonts_dir=fonts_dir,
        cache_dir=cache_dir,
        records=records,
        aliases=dict(configured_aliases),
    )


# Short aliases keep the public surface discoverable for callers that use the
# nouns from the task description.
create_persona_fontconfig = build_persona_fontconfig
fontconfig_for_persona = build_persona_fontconfig


__all__ = [
    "DEFAULT_FONT_ALIASES",
    "FontConfigBundle",
    "FontDatabase",
    "FontError",
    "FontNotFoundError",
    "FontRecord",
    "FontconfigUnavailableError",
    "build_persona_fontconfig",
    "create_persona_fontconfig",
    "enumerate_fonts",
    "fontconfig_for_persona",
    "match_font",
]
