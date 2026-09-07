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
_CSS_GENERIC_ALIASES = frozenset({"sans-serif", "serif", "monospace"})


def _font_rendering_valid(value: Any, *, worker: bool = False) -> bool:
    rendered = _mapping(value)
    digest = rendered.get("hash")
    metrics = _mapping(rendered.get("metrics"))
    common = (
        all(rendered.get(key) is True for key in ("supported", "nonEmpty", "stable", "exportMatches"))
        and isinstance(digest, str) and len(digest) == 8
        and all(char in "0123456789abcdef" for char in digest)
        and rendered.get("repeatHash") == digest
        and type(rendered.get("inkPixels")) is int and rendered["inkPixels"] > 0
        and all(type(metrics.get(key)) in (int, float) and math.isfinite(metrics[key])
                for key in ("width", "ascent", "descent", "left", "right"))
        and metrics["width"] > 0
    )
    data_url = _mapping(rendered.get("dataUrlDecoded"))
    blob = _mapping(rendered.get("blobDecoded"))
    if worker:
        return common and rendered.get("exportApi") == "convertToBlob" \
            and rendered.get("dataUrlLength") is None \
            and data_url.get("supported") is False \
            and data_url.get("notApplicable") is True \
            and blob.get("supported") is True \
            and blob.get("hash") == digest
    return common and data_url.get("supported") is True \
        and data_url.get("hash") == digest \
        and blob.get("supported") is True \
        and blob.get("hash") == digest


def _font_renderings_match(first: Any, second: Any, *, worker: bool = False) -> bool:
    first, second = _mapping(first), _mapping(second)
    return (_font_rendering_valid(first, worker=worker) and _font_rendering_valid(second, worker=worker)
            and first["metrics"] == second["metrics"] and first["hash"] == second["hash"])


def _font_observation_valid(
    observed: Mapping[str, Any],
    expected_config: Mapping[str, Any],
    *,
    worker: bool,
) -> tuple[bool, dict[str, Any]]:
    """Validate one window/worker font observation and return evidence data."""
    expected_families = expected_config.get("families", ())
    aliases = _mapping(expected_config.get("aliases"))
    expected_aliases = {
        str(alias): target for alias, target in aliases.items()
        if str(alias).lower() in _CSS_GENERIC_ALIASES
    }
    expected_samples = _mapping(expected_config.get("samples"))
    expected_blocked = expected_config.get("blocked_families", ())
    positive = _mapping(observed.get("positive"))
    observed_families = _mapping(observed.get("families"))
    observed_aliases = _mapping(observed.get("aliases"))
    aliases_checked = observed.get("aliases_checked")
    negative = _mapping(observed.get("negative"))
    valid = (
        expected_config.get("policy") == "whitelist"
        and isinstance(expected_families, Sequence)
        and not isinstance(expected_families, (str, bytes))
        and bool(expected_families)
        and observed.get("workerFonts") == ("measured" if worker else "not_verified")
    )
    values: dict[str, Any] = {
        "families": {}, "aliases": {}, "blocked_families": {},
        "aliases_checked": aliases_checked,
        "missing_family": observed.get("missingFamily"),
        "worker_fonts": observed.get("workerFonts", "unknown"),
        "policy": observed.get("policy"),
    }
    for family_value in expected_families if isinstance(expected_families, Sequence) else ():
        family = str(family_value)
        item = _mapping(observed_families.get(family))
        positive_item = _mapping(positive.get(family, item.get("positive")))
        sample = str(expected_samples.get(family, ""))
        values["families"][family] = item
        valid = valid and bool(item) and positive_item.get("ok") is True
        valid = valid and family in expected_samples and item.get("sample") == sample
        valid = valid and item.get("metricsMatch") is True and item.get("pixelMatch") is True
        valid = valid and _font_renderings_match(item.get("direct"), item.get("local"), worker=worker)
    if isinstance(expected_blocked, Sequence) and not isinstance(expected_blocked, (str, bytes)):
        for family_value in expected_blocked:
            family = str(family_value)
            item = _mapping(negative.get(family))
            values["blocked_families"][family] = item
            valid = valid and item.get("failed") is True
    missing_family = observed.get("missingFamily")
    missing_item = _mapping(negative.get(str(missing_family))) if missing_family else {}
    values["missing"] = missing_item
    valid = valid and bool(missing_item) and missing_item.get("failed") is True
    valid = valid and (
        isinstance(aliases_checked, Sequence)
        and not isinstance(aliases_checked, (str, bytes))
        and sorted(str(alias) for alias in aliases_checked) == sorted(expected_aliases)
    )
    for alias_value, target_value in expected_aliases.items():
        alias, target = str(alias_value), str(target_value)
        item = _mapping(observed_aliases.get(alias))
        values["aliases"][alias] = item
        valid = valid and bool(item) and item.get("target") == target
        valid = valid and item.get("metricsMatch") is True and item.get("pixelMatch") is True
        valid = valid and _font_renderings_match(
            item.get("generic"), item.get("targetLocal"), worker=worker
        )
    return valid, values


def _webgl_context_identity(context: Mapping[str, Any]) -> tuple[Any, Any]:
    return (
        context.get("unmaskedVendor") or context.get("vendor"),
        context.get("unmaskedRenderer") or context.get("renderer"),
    )


def _webgl_rgba(value: Any, expected: list[int]) -> bool:
    return (
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes))
        and list(value) == expected
    )


def _webgl_behavior_valid(value: Mapping[str, Any]) -> bool:
    triangle = _mapping(value.get("triangle"))
    framebuffer = _mapping(value.get("framebuffer"))
    errors = value.get("errors")
    return (
        value.get("compile") is True
        and value.get("link") is True
        and value.get("passed") is True
        and isinstance(errors, Sequence)
        and not isinstance(errors, (str, bytes))
        and len(errors) == 0
        and triangle.get("compile") is True
        and triangle.get("link") is True
        and triangle.get("nonEmpty") is True
        and triangle.get("readback") is True
        and triangle.get("exactRed") is True
        and _webgl_rgba(triangle.get("rgba"), [255, 0, 0, 255])
        and framebuffer.get("rgba8") is True
        and framebuffer.get("complete") is True
        and framebuffer.get("readback") is True
        and framebuffer.get("exactGreen") is True
        and _webgl_rgba(framebuffer.get("rgba"), [0, 255, 0, 255])
    )


def _webgl_observation_valid(
    observed: Mapping[str, Any],
    graphics: Mapping[str, Any],
    *,
    reference_identity: tuple[Any, Any] | None = None,
) -> tuple[bool, dict[str, Any], tuple[Any, Any] | None]:
    """Validate one realm's WebGL1/WebGL2 identity and pixel behavior."""
    webgl1 = _mapping(observed.get("webgl1"))
    webgl2 = _mapping(observed.get("webgl2"))
    behavior = _mapping(observed.get("behavior"))
    behavior1 = _mapping(behavior.get("webgl1", webgl1.get("behavior")))
    behavior2 = _mapping(behavior.get("webgl2", webgl2.get("behavior")))
    identity = _webgl_context_identity(observed)
    identity1 = _webgl_context_identity(webgl1)
    identity2 = _webgl_context_identity(webgl2)
    observed_vendor, observed_renderer = identity

    def graphics_match(expected: Any, actual: Any) -> bool:
        if not isinstance(expected, str) or not isinstance(actual, str):
            return False
        lhs, rhs = expected.strip().lower(), actual.strip().lower()
        if lhs == "llvmpipe, or similar":
            return "llvmpipe" in rhs
        if lhs == "mesa":
            return rhs == "mesa" or rhs.startswith("mesa ")
        return lhs == rhs

    ok = (
        observed.get("supported") is True
        and graphics_match(graphics.get("vendor"), observed_vendor)
        and graphics_match(graphics.get("renderer"), observed_renderer)
        and identity is not None
        and identity1 == identity
        and identity2 == identity
        and (reference_identity is None or identity == reference_identity)
        and bool(graphics.get("webgl1"))
        and bool(graphics.get("webgl2"))
        and webgl1.get("supported") is True
        and webgl2.get("supported") is True
        and _webgl_behavior_valid(behavior1)
        and _webgl_behavior_valid(behavior2)
    )
    values = {
        "supported": observed.get("supported"),
        "vendor": observed_vendor,
        "renderer": observed_renderer,
        "webgl1": {"supported": webgl1.get("supported"),
                    "vendor": identity1[0], "renderer": identity1[1],
                    "behavior": behavior1},
        "webgl2": {"supported": webgl2.get("supported"),
                    "vendor": identity2[0], "renderer": identity2[1],
                    "behavior": behavior2},
    }
    return ok, values, identity if all(item is not None for item in identity) else None


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

    if "appearance_window" in template.required_capabilities:
        appearance = _mapping(final_config.get("appearance"))
        observed_appearance = _mapping(page.get("appearance"))
        requested_appearance = {
            "color_scheme": appearance.get("color_scheme"),
            "reduced_motion": appearance.get("reduced_motion"),
            "contrast": appearance.get("contrast"),
            "forced_colors": appearance.get("forced_colors"),
        }
        observed_appearance_values = {
            "color_scheme": observed_appearance.get("colorScheme", observed_appearance.get("color_scheme")),
            "reduced_motion": observed_appearance.get("reducedMotion", observed_appearance.get("reduced_motion")),
            "contrast": observed_appearance.get("contrast"),
            "forced_colors": observed_appearance.get("forcedColors", observed_appearance.get("forced_colors")),
        }
        appearance_ok = all(
            requested_appearance[key] is not None
            and observed_appearance_values[key] is not None
            and requested_appearance[key] == observed_appearance_values[key]
            for key in requested_appearance
        )
        evidence.append(_evidence(
            "appearance_window", _status(appearance_ok), snapshot_obj,
            requested_appearance,
            {**observed_appearance_values, "workers": "notapplicable"},
            ("window", "dedicated:notapplicable", "shared:notapplicable", "service:notapplicable"),
            tuple(f"observations.page.appearance.{key}" for key in observed_appearance_values),
        ))
        if not appearance_ok:
            diagnostic_reasons.append("appearance values are missing or do not match")

    if "geolocation_window" in template.required_capabilities:
        requested_geo = _mapping(final_config.get("geolocation", final_config.get("geo")))
        observed_geo = _mapping(page.get("geolocation"))
        expected_position = {
            "latitude": requested_geo.get("latitude"),
            "longitude": requested_geo.get("longitude"),
            "accuracy": requested_geo.get("accuracy"),
        }
        granted_position = _mapping(observed_geo.get("granted_position"))
        denied_position = _mapping(observed_geo.get("denied_position"))

        def close_number(left: Any, right: Any, tolerance: float) -> bool:
            lhs, rhs = _as_number(left), _as_number(right)
            return lhs is not None and rhs is not None and abs(lhs - rhs) <= tolerance

        geo_ok = (
            bool(requested_geo)
            and requested_geo.get("permission") == "prompt"
            and observed_geo.get("original_state") == "prompt"
            and observed_geo.get("granted_state") == "granted"
            and granted_position.get("ok") is True
            and close_number(granted_position.get("latitude"), expected_position.get("latitude"), 1e-5)
            and close_number(granted_position.get("longitude"), expected_position.get("longitude"), 1e-5)
            and close_number(granted_position.get("accuracy"), expected_position.get("accuracy"), 1e-5)
            and observed_geo.get("denied_state") == "denied"
            and denied_position.get("ok") is False
            and denied_position.get("errorCode") == 1
            and observed_geo.get("restored_state") == observed_geo.get("original_state")
            and not observed_geo.get("error")
            and not observed_geo.get("restore_error")
        )
        observed_geo_values = _copy(dict(observed_geo))
        worker_contexts = observed_geo_values.setdefault("worker_contexts", {})
        if not isinstance(worker_contexts, Mapping):
            worker_contexts = {}
            observed_geo_values["worker_contexts"] = worker_contexts
        for realm in _REALMS[1:]:
            worker_contexts.setdefault(realm, "notapplicable")
        evidence.append(_evidence(
            "geolocation_window", _status(geo_ok), snapshot_obj,
            {**expected_position, "permission": requested_geo.get("permission")},
            observed_geo_values,
            ("window", "dedicated:notapplicable", "shared:notapplicable", "service:notapplicable"),
            (
                "observations.page.geolocation.original_state",
                "observations.page.geolocation.granted_position",
                "observations.page.geolocation.denied_position",
                "observations.page.geolocation.restored_state",
                "observations.page.geolocation.worker_contexts=notapplicable",
            ),
        ))
        if not geo_ok:
            diagnostic_reasons.append("geolocation grant/deny/restore or position does not match")

    if "fonts_window" in template.required_capabilities:
        fonts_config = _mapping(final_config.get("fonts"))
        fonts_observed = _mapping(page.get("fonts"))
        fonts_ok, observed_font_values = _font_observation_valid(
            fonts_observed, fonts_config, worker=False
        )
        evidence.append(_evidence(
            "fonts_window", _status(fonts_ok), snapshot_obj,
            {"families": fonts_config.get("families"), "aliases": fonts_config.get("aliases"),
             "samples": fonts_config.get("samples"), "blocked_families": fonts_config.get("blocked_families"),
             "policy": fonts_config.get("policy")},
            observed_font_values, ("window",),
            (
                "observations.page.fonts.positive",
                "observations.page.fonts.families.metrics/pixels/exports",
                "observations.page.fonts.aliases.metrics/pixels/exports",
                "observations.page.fonts.negative.blocked_families",
                "observations.page.fonts.negative.missingFamily",
                "observations.page.fonts.workerFonts=not_verified",
            ),
        ))
        if not fonts_ok:
            diagnostic_reasons.append("font whitelist, aliases, negative loads or canvas evidence do not match")

    if "fonts_workers" in template.required_capabilities:
        fonts_config = _mapping(final_config.get("fonts"))
        window_fonts = _mapping(page.get("fonts"))
        window_families = _mapping(window_fonts.get("families"))
        window_aliases = _mapping(window_fonts.get("aliases"))
        worker_values: dict[str, Any] = {}
        workers_fonts_ok = True

        def same_rendering(window_value: Any, worker_value: Any) -> bool:
            window_rendering = _mapping(window_value)
            worker_rendering = _mapping(worker_value)
            return (
                _font_rendering_valid(window_rendering, worker=False)
                and _font_rendering_valid(worker_rendering, worker=True)
                and window_rendering.get("metrics") == worker_rendering.get("metrics")
                and window_rendering.get("hash") == worker_rendering.get("hash")
            )

        for realm in _REALMS[1:]:
            realm_data = _realm(workers, realm)
            observed = _mapping(realm_data.get("fonts"))
            realm_ok, values = _font_observation_valid(
                observed, fonts_config, worker=True
            )
            for family_value in fonts_config.get("families", ()):
                family = str(family_value)
                worker_item = _mapping(_mapping(observed.get("families")).get(family))
                window_item = _mapping(window_families.get(family))
                realm_ok = realm_ok and same_rendering(
                    window_item.get("direct"), worker_item.get("direct")
                ) and same_rendering(
                    window_item.get("local"), worker_item.get("local")
                )
            for alias in ("sans-serif", "serif", "monospace"):
                worker_item = _mapping(_mapping(observed.get("aliases")).get(alias))
                window_item = _mapping(window_aliases.get(alias))
                if worker_item or window_item:
                    realm_ok = realm_ok and same_rendering(
                        window_item.get("generic"), worker_item.get("generic")
                    ) and same_rendering(
                        window_item.get("targetLocal"), worker_item.get("targetLocal")
                    )
            values = dict(values)
            values["window_consistency"] = realm_ok
            worker_values[realm] = values
            workers_fonts_ok = workers_fonts_ok and realm_ok
        observed_workers_fonts = {"workers": worker_values, "window_consistency": workers_fonts_ok}
        evidence.append(_evidence(
            "fonts_workers", _status(workers_fonts_ok), snapshot_obj,
            {"families": fonts_config.get("families"), "aliases": fonts_config.get("aliases"),
             "samples": fonts_config.get("samples"), "blocked_families": fonts_config.get("blocked_families"),
             "policy": fonts_config.get("policy"), "contexts": list(_REALMS[1:])},
            observed_workers_fonts, _REALMS,
            (
                "observations.page.workers.{dedicated,shared,service}.fonts.positive",
                "observations.page.workers.*.fonts.families.direct/local.metrics/hash/repeatHash",
                "observations.page.workers.*.fonts.aliases.generic/targetLocal",
                "observations.page.workers.*.fonts.negative",
                "observations.page.workers.*.fonts.workerFonts=measured",
                "observations.page.workers.*.fonts.window_consistency",
            ),
        ))
        if not workers_fonts_ok:
            diagnostic_reasons.append("worker fonts are missing, invalid, or differ from the window")

    window_webgl_identity: tuple[Any, Any] | None = None
    if "webgl_window" in template.required_capabilities:
        graphics = _mapping(final_config.get("graphics"))
        webgl = _mapping(page.get("webgl"))
        graphics_ok, observed_graphics, window_webgl_identity = _webgl_observation_valid(
            webgl, graphics
        )
        observed_graphics = {
            **observed_graphics,
            "worker_webgl": (
                "separate_evidence"
                if "webgl_workers" in template.required_capabilities
                else "not_verified"
            ),
            "raw": webgl,
        }
        evidence.append(_evidence(
            "webgl_window", _status(graphics_ok), snapshot_obj,
            graphics, observed_graphics, ("window",),
            (
                "observations.page.webgl.unmaskedVendor",
                "observations.page.webgl.unmaskedRenderer",
                "observations.page.webgl.webgl1.behavior",
                "observations.page.webgl.webgl2.behavior",
                "observations.page.webgl.raw",
                "observations.page.webgl.worker_webgl",
            ),
        ))
        if not graphics_ok:
            diagnostic_reasons.append("WebGL1/WebGL2 context, shader, draw or framebuffer behavior does not match")

    if "webgl_workers" in template.required_capabilities:
        graphics = _mapping(final_config.get("graphics"))
        window_webgl = _mapping(page.get("webgl"))
        if window_webgl_identity is None:
            _, _, window_webgl_identity = _webgl_observation_valid(
                window_webgl, graphics
            )
        worker_webgl_values: dict[str, Any] = {}
        workers_webgl_ok = True
        for realm in _REALMS[1:]:
            observed = _mapping(_realm(workers, realm).get("webgl"))
            realm_ok, values, identity = _webgl_observation_valid(
                observed, graphics, reference_identity=window_webgl_identity
            )
            values = dict(values)
            values["identity_matches_window"] = (
                identity is not None and identity == window_webgl_identity
            )
            worker_webgl_values[realm] = values
            workers_webgl_ok = workers_webgl_ok and realm_ok
        observed_workers_webgl = {
            "window_identity": window_webgl_identity,
            "workers": worker_webgl_values,
        }
        evidence.append(_evidence(
            "webgl_workers", _status(workers_webgl_ok), snapshot_obj,
            {"vendor": graphics.get("vendor"), "renderer": graphics.get("renderer"),
             "webgl1": graphics.get("webgl1"), "webgl2": graphics.get("webgl2"),
             "contexts": list(_REALMS[1:])},
            observed_workers_webgl, _REALMS,
            (
                "observations.page.workers.{dedicated,shared,service}.webgl.unmaskedVendor",
                "observations.page.workers.*.webgl.unmaskedRenderer",
                "observations.page.workers.*.webgl.webgl1.behavior",
                "observations.page.workers.*.webgl.webgl2.behavior",
                "observations.page.workers.*.webgl.identity_matches_window",
            ),
        ))
        if not workers_webgl_ok:
            diagnostic_reasons.append("worker WebGL contexts, identities or shader/framebuffer behavior do not match")

    if {"audio_offline", "audio_realtime"} & set(template.required_capabilities):
        from .audio_qualification import validate_offline, validate_realtime
        audio_config = _mapping(final_config.get("audio"))
        audio_observed = _mapping(page.get("audio_behavior"))
        for capability, key, validator in (
                ("audio_offline", "offline", validate_offline),
                ("audio_realtime", "realtime", validate_realtime)):
            if capability not in template.required_capabilities:
                continue
            observed = _mapping(audio_observed.get(key))
            valid = validator(audio_config, observed)
            if key == "realtime" and audio_observed.get("cleanup_error"):
                valid = False
            evidence.append(_evidence(
                capability, _status(valid), snapshot_obj, audio_config,
                {"data": observed, "workers": "notapplicable",
                 "physical_input": "not_verified", "physical_output": "not_verified"},
                ("window",), (f"observations.page.audio_behavior.{key}",)))
            if not valid:
                diagnostic_reasons.append(f"{capability} behavior is missing or does not match")

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
