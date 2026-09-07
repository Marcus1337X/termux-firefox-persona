"""Strict qualification of one Persona against a browser probe report.

Qualification is intentionally more demanding than merely having a value in
``window.navigator``.  A combination is eligible only when the requested
configuration agrees with every realm and with the first HTTP requests made
by the page.  The resulting evidence is bound to the supplied capability
snapshot and can therefore be promoted by :class:`TemplateCatalog` safely.
"""

from __future__ import annotations

import copy
import math
from dataclasses import replace
from typing import Any, Iterable, Mapping, Sequence

from .model import (
    CapabilityEvidence,
    CapabilitySnapshot,
    QualificationError,
    QualificationReport,
    Persona,
    TemplateCatalog,
)


_REALMS = ("window", "dedicated", "shared", "service")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _copy(value: Any) -> Any:
    return copy.deepcopy(value)


def _probe_parts(report: Mapping[str, Any]) -> tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]:
    """Accept both ProbeRunner's result and its raw observations."""

    root = _mapping(report)
    observations = _mapping(root.get("observations"))
    if not observations:
        observations = _mapping(root.get("raw")) or root
    page = _mapping(observations.get("page"))
    if not page:
        page = _mapping(root.get("page")) or observations
    http = _mapping(observations.get("http"))
    if not http:
        http = _mapping(root.get("http"))
    workers = _mapping(page.get("workers"))
    if not workers:
        workers = _mapping(observations.get("workers"))
    if workers and "workers" not in page:
        page = dict(page)
        page["workers"] = workers
    return page, http, workers


def _realm(workers: Mapping[str, Any], kind: str) -> Mapping[str, Any]:
    aliases = {
        "dedicated": ("dedicated", "dedicated_worker", "dedicatedWorker"),
        "shared": ("shared", "shared_worker", "sharedWorker"),
        "service": ("service", "service_worker", "serviceWorker"),
    }
    for key in aliases.get(kind, (kind,)):
        value = workers.get(key)
        if isinstance(value, Mapping):
            return value
    return {}


def _window(page: Mapping[str, Any]) -> Mapping[str, Any]:
    value = page.get("window")
    return value if isinstance(value, Mapping) else {}


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _same_languages(expected: Any, observed: Any) -> bool:
    if isinstance(expected, str) or not isinstance(expected, Sequence):
        return False
    if isinstance(observed, str) or not isinstance(observed, Sequence):
        return False
    return [str(item).strip().lower() for item in expected] == [
        str(item).strip().lower() for item in observed
    ]


def _parse_accept_language(value: Any) -> list[tuple[str, float]] | None:
    """Parse Accept-Language with q-value semantics, ignoring formatting."""

    if not isinstance(value, str) or not value.strip():
        return None
    result: list[tuple[str, float]] = []
    for item in value.split(","):
        parts = [part.strip() for part in item.split(";")]
        language = parts[0].lower()
        if not language or any(ch.isspace() for ch in language):
            return None
        quality = 1.0
        for parameter in parts[1:]:
            if not parameter:
                continue
            name, separator, raw = parameter.partition("=")
            if not separator or name.strip().lower() != "q":
                continue
            try:
                quality = float(raw.strip())
            except ValueError:
                return None
            if not 0.0 <= quality <= 1.0:
                return None
        result.append((language, round(quality, 6)))
    return result or None


def _same_accept_language(expected: Any, observed: Any) -> bool:
    lhs, rhs = _parse_accept_language(expected), _parse_accept_language(observed)
    return lhs is not None and lhs == rhs


def _header(headers: Any, name: str) -> str | None:
    if not isinstance(headers, Mapping):
        return None
    wanted = name.lower()
    for key, value in headers.items():
        if str(key).lower() == wanted:
            return str(value)
    return None


def _document_headers(http: Mapping[str, Any]) -> Mapping[str, Any]:
    value = http.get("document", http.get("document_headers", {}))
    return value if isinstance(value, Mapping) else {}


def _worker_headers_by_kind(http: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """Return first headers for each worker script kind."""

    value = http.get("worker", http.get("worker_headers", []))
    result: dict[str, Mapping[str, Any]] = {}
    if isinstance(value, Mapping):
        nested = value.get("headers")
        if not isinstance(nested, Mapping):
            for candidate in ("dedicated", "shared", "service"):
                candidate_value = value.get(candidate)
                if isinstance(candidate_value, Mapping):
                    candidate_headers = candidate_value.get("headers", candidate_value)
                    if isinstance(candidate_headers, Mapping):
                        result[candidate] = candidate_headers
            if result:
                return result
        headers = nested if isinstance(nested, Mapping) else value
        kind = str(value.get("worker_type", value.get("type", value.get("kind", "worker"))))
        result[kind] = headers
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            if isinstance(item, Mapping):
                nested = item.get("headers")
                headers = nested if isinstance(nested, Mapping) else item
                path = str(item.get("path", "")).lower()
                kind = str(item.get("worker_type", item.get("type", item.get("kind", "")))).lower()
                if kind not in {"dedicated", "shared", "service"}:
                    for candidate in ("dedicated", "shared", "service"):
                        if candidate in path:
                            kind = candidate
                            break
                if kind and kind not in result:
                    result[kind] = headers
    return result


def _status(ok: bool, missing: bool = False) -> str:
    # A mismatch and a missing observation are both deliberately non-passing.
    # ``partial`` communicates that the probe did run but cannot qualify it.
    return "supported" if ok and not missing else "partial"


def _evidence(
    capability: str,
    status: str,
    snapshot: CapabilitySnapshot,
    requested: Any,
    observed: Any,
    contexts: Iterable[str],
    proof: Iterable[str],
) -> CapabilityEvidence:
    return CapabilityEvidence(
        capability=capability,
        status=status,
        environment_fingerprint=snapshot.fingerprint,
        requested=_copy(requested),
        observed=_copy(observed),
        contexts=tuple(contexts),
        proof=tuple(proof),
    )


def _persona_data(persona: Persona | Mapping[str, Any]) -> tuple[str, Mapping[str, Any]]:
    if isinstance(persona, Mapping):
        template_id = persona.get("template_id")
        config = persona.get("final_config", persona.get("config"))
    else:
        template_id = getattr(persona, "template_id", None)
        config = getattr(persona, "final_config", getattr(persona, "config", None))
    if not isinstance(template_id, str) or not template_id:
        raise QualificationError("persona.template_id is required for probe qualification")
    if not isinstance(config, Mapping) or not config:
        raise QualificationError("persona.final_config is required for probe qualification")
    return template_id, config


def qualify_probe(
    persona: Persona | Mapping[str, Any],
    snapshot: CapabilitySnapshot | Mapping[str, Any],
    probe_report: Mapping[str, Any],
    *,
    catalog: TemplateCatalog | None = None,
) -> QualificationReport:
    """Create strict evidence for ``persona`` from one ProbeRunner report.

    Every required template capability receives one evidence record.  Missing
    realms, malformed values, policy violations and mismatches are ``partial``
    and therefore cannot pass ``QualificationReport.build``.  The graphics
    candidate is explicitly ``unsupported`` until a full WebGL/GPU/media
    qualification implementation exists; a renderer string alone is never
    treated as that evidence.
    """

    if not isinstance(probe_report, Mapping):
        raise QualificationError("probe_report must be an object")
    template_id, final_config = _persona_data(persona)
    snapshot_obj = CapabilitySnapshot.from_mapping(snapshot)
    catalog_obj = catalog or TemplateCatalog.default()
    template = catalog_obj.get(template_id)
    page, http, workers = _probe_parts(probe_report)
    win = _window(page)
    browser = _mapping(final_config.get("browser"))
    cpu = _mapping(final_config.get("cpu"))
    display = _mapping(final_config.get("display"))
    locale = _mapping(final_config.get("locale"))
    evidence: list[CapabilityEvidence] = []
    diagnostic_reasons: list[str] = []

    # Browser identity is compared exactly.  These are the fields that are
    # visible to scripts and that the HTTP layer can independently confirm.
    browser_requested = {
        "user_agent": browser.get("user_agent"),
        "platform": browser.get("platform"),
        "oscpu": browser.get("oscpu"),
        "app_version": browser.get("app_version"),
    }
    browser_observed = {
        "user_agent": win.get("userAgent"),
        "platform": win.get("platform"),
        "oscpu": win.get("oscpu"),
        "app_version": win.get("appVersion"),
    }
    browser_ok = all(
        browser_requested[key] is not None
        and browser_observed[key] is not None
        and browser_requested[key] == browser_observed[key]
        for key in browser_requested
    )
    if "browser_identity" in template.required_capabilities:
        evidence.append(_evidence(
            "browser_identity", _status(browser_ok), snapshot_obj,
            browser_requested, browser_observed, ("window",),
            tuple(f"observations.page.window.{key}" for key in browser_observed),
        ))
        if not browser_ok:
            diagnostic_reasons.append("browser identity does not exactly match")

    # CPU must be stable in the window and all three worker realms.
    requested_cpu = {"hardware_concurrency": cpu.get("hardware_concurrency")}
    observed_cpu: dict[str, Any] = {"window": win.get("hardwareConcurrency")}
    cpu_ok = requested_cpu["hardware_concurrency"] is not None
    for realm in _REALMS[1:]:
        observed_cpu[realm] = _realm(workers, realm).get("hardwareConcurrency")
        cpu_ok = cpu_ok and observed_cpu[realm] == requested_cpu["hardware_concurrency"]
    cpu_ok = cpu_ok and observed_cpu["window"] == requested_cpu["hardware_concurrency"]
    if "cpu_window_worker" in template.required_capabilities:
        evidence.append(_evidence(
            "cpu_window_worker", _status(cpu_ok), snapshot_obj,
            requested_cpu, observed_cpu, _REALMS,
            tuple(f"observations.page.{realm}.hardwareConcurrency" for realm in _REALMS),
        ))
        if not cpu_ok:
            diagnostic_reasons.append("hardwareConcurrency is missing or differs by realm")

    # Display fields are fixed, while viewport dimensions are derived from
    # the actual window.  In particular, never compare viewport to screen.
    screen = _mapping(display_observed := _mapping(page.get("display")).get("screen"))
    viewport = _mapping(_mapping(page.get("display")).get("viewport"))
    display_observed_values = {
        "screen_width": screen.get("width"),
        "screen_height": screen.get("height"),
        "avail_width": screen.get("availWidth"),
        "avail_height": screen.get("availHeight"),
        "color_depth": screen.get("colorDepth"),
        "pixel_depth": screen.get("pixelDepth"),
        "device_pixel_ratio": _mapping(page.get("display")).get("devicePixelRatio"),
        "window_width": viewport.get("outerWidth"),
        "window_height": viewport.get("outerHeight"),
        "inner_width": viewport.get("width"),
        "inner_height": viewport.get("height"),
        "screen_x": _mapping(page.get("display")).get("screenX"),
        "screen_y": _mapping(page.get("display")).get("screenY"),
        "orientation": _mapping(_mapping(page.get("display")).get("orientation")),
    }
    fixed_display_keys = (
        "screen_width", "screen_height", "avail_width", "avail_height",
        "color_depth", "pixel_depth", "device_pixel_ratio",
    )
    display_ok = all(
        key in display and display.get(key) is not None
        and display_observed_values.get(key) is not None
        and (_as_number(display.get(key)) == _as_number(display_observed_values.get(key)))
        for key in fixed_display_keys
    )
    iw, ih = _as_number(display_observed_values["inner_width"]), _as_number(display_observed_values["inner_height"])
    ow, oh = _as_number(display_observed_values["window_width"]), _as_number(display_observed_values["window_height"])
    sw, sh = _as_number(display_observed_values["screen_width"]), _as_number(display_observed_values["screen_height"])
    bounds_ok = all(value is not None and value > 0 for value in (iw, ih, ow, oh, sw, sh)) and all(
        (inner <= outer <= screen_size)
        for inner, outer, screen_size in ((iw, ow, sw), (ih, oh, sh))
    )
    policy_ok = display.get("window_policy", "maximized") == "maximized" and display.get("viewport_policy", "derived") == "derived"
    viewport_derived = (
        iw is not None and ih is not None
        and _as_number(viewport.get("width")) == iw
        and _as_number(viewport.get("height")) == ih
    )
    display_ok = display_ok and bounds_ok and policy_ok and viewport_derived
    requested_display = {key: display.get(key) for key in fixed_display_keys}
    requested_display.update({"window_policy": display.get("window_policy", "maximized"),
                              "viewport_policy": display.get("viewport_policy", "derived")})
    if "display_window" in template.required_capabilities:
        evidence.append(_evidence(
            "display_window", _status(display_ok), snapshot_obj,
            requested_display, display_observed_values,
            ("window",),
            tuple(f"observations.page.display.{key}" for key in display_observed_values),
        ))
        if not display_ok:
            diagnostic_reasons.append("display or derived window bounds are invalid")

    # Locale is checked in four JavaScript realms and independently in the
    # first document and worker HTTP requests.  Header language quality values
    # are parsed, so harmless q formatting differences don't fail a match.
    requested_locale = {
        "languages": locale.get("languages"),
        "timezone": locale.get("timezone"),
        "accept_language": locale.get("accept_language"),
    }
    observed_locale: dict[str, Any] = {}
    locale_ok = isinstance(locale.get("languages"), Sequence) and not isinstance(locale.get("languages"), (str, bytes))
    for realm in _REALMS:
        realm_data = win if realm == "window" else _realm(workers, realm)
        observed_locale[realm] = {
            "languages": realm_data.get("languages"),
            "timezone": realm_data.get("timezone"),
        }
        locale_ok = locale_ok and _same_languages(locale.get("languages"), realm_data.get("languages"))
        locale_ok = locale_ok and locale.get("timezone") == realm_data.get("timezone")
    doc_headers, worker_headers = _document_headers(http), _worker_headers_by_kind(http)
    observed_locale["document_headers"] = {
        "user_agent": _header(doc_headers, "User-Agent"),
        "accept_language": _header(doc_headers, "Accept-Language"),
    }
    observed_locale["worker_headers"] = {}
    locale_ok = locale_ok and bool(doc_headers)
    locale_ok = locale_ok and _header(doc_headers, "User-Agent") == browser.get("user_agent")
    locale_ok = locale_ok and _same_accept_language(
        locale.get("accept_language"), _header(doc_headers, "Accept-Language")
    )
    for kind in ("dedicated", "shared", "service"):
        headers = worker_headers.get(kind, {})
        observed_locale["worker_headers"][kind] = {
            "user_agent": _header(headers, "User-Agent"),
            "accept_language": _header(headers, "Accept-Language"),
        }
        locale_ok = locale_ok and bool(headers)
        locale_ok = locale_ok and _header(headers, "User-Agent") == browser.get("user_agent")
        locale_ok = locale_ok and _same_accept_language(
            locale.get("accept_language"), _header(headers, "Accept-Language")
        )
    if "locale_window_worker_http" in template.required_capabilities:
        evidence.append(_evidence(
            "locale_window_worker_http", _status(locale_ok), snapshot_obj,
            requested_locale, observed_locale,
            (*_REALMS, "document", "worker"),
            tuple(f"observations.page.{realm}.languages/timezone" for realm in _REALMS)
            + ("observations.http.document.headers",)
            + tuple(f"observations.http.worker.{kind}.headers" for kind in ("dedicated", "shared", "service")),
        ))
        if not locale_ok:
            diagnostic_reasons.append("locale, timezone or first HTTP headers do not match")

    if "graphics_full_combination" in template.required_capabilities:
        graphics = _mapping(final_config.get("graphics"))
        webgl = _mapping(page.get("webgl"))
        evidence.append(_evidence(
            "graphics_full_combination", "unsupported", snapshot_obj,
            graphics,
            {"implemented": False, "webgl_observed": webgl},
            ("window",),
            ("graphics_full_combination qualification is not implemented",),
        ))
        diagnostic_reasons.append("full graphics combination qualification is not implemented")

    missing_required = [
        name for name in template.required_capabilities
        if not any(item.capability == name for item in evidence)
    ]
    for name in missing_required:
        evidence.append(_evidence(
            name, "partial", snapshot_obj, None, None, ("unknown",),
            (f"required capability {name} was not evaluated",),
        ))
        diagnostic_reasons.append(f"required capability was not evaluated: {name}")

    full_combination = bool(template.required_capabilities) and all(
        item.status == "supported" for item in evidence
        if item.capability in template.required_capabilities
    ) and not missing_required
    report = QualificationReport.build(
        template,
        final_config,
        snapshot_obj,
        evidence,
        full_combination=full_combination,
    )
    if diagnostic_reasons:
        report = replace(report, reasons=tuple(dict.fromkeys((*report.reasons, *diagnostic_reasons))))
    return report


__all__ = ["qualify_probe"]
