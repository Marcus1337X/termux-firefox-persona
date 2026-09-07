"""Local, side-effect-free browser capability probe.

The probe deliberately uses a loopback origin.  It needs no external network
access and records the request headers made by the first document and worker
resource, while the page collects the values exposed to each JavaScript realm.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping
from urllib.parse import urlsplit


def _worker_values(kind: str) -> str:
    """Return a worker script for the requested worker kind."""

    source = r'''
function tbpWorkerValues(kind) {
  let timezone = null;
  try { timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || null; }
  catch (_) {}
  return {
    kind: kind,
    userAgent: navigator.userAgent || null,
    platform: navigator.platform || null,
    hardwareConcurrency: navigator.hardwareConcurrency || null,
    languages: Array.from(navigator.languages || []),
    timezone: timezone
  };
}
'''
    if kind == "dedicated":
        return source + "postMessage(tbpWorkerValues('dedicated'));"
    if kind == "shared":
        return source + "onconnect = function(e) { const p=e.ports[0]; p.start(); p.postMessage(tbpWorkerValues('shared')); };"
    return source + r'''
self.addEventListener('install', function(e) { self.skipWaiting(); });
self.addEventListener('activate', function(e) {
  e.waitUntil(self.clients.claim());
});
self.addEventListener('message', function(e) {
  if (e.source) e.source.postMessage(tbpWorkerValues('service')); 
});
'''


PROBE_HTML = r'''<!doctype html>
<meta charset="utf-8">
<title>Termux Browser Pilot capability probe</title>
<script>
(function() {
  function timezone() {
    try { return Intl.DateTimeFormat().resolvedOptions().timeZone || null; }
    catch (_) { return null; }
  }
  function windowValues() {
    return {
      userAgent: navigator.userAgent || null,
      platform: navigator.platform || null,
      oscpu: navigator.oscpu || null,
      appVersion: navigator.appVersion || null,
      hardwareConcurrency: navigator.hardwareConcurrency || null,
      languages: Array.from(navigator.languages || []),
      language: navigator.language || null,
      timezone: timezone()
    };
  }
  function displayValues() {
    let orientation = null;
    try {
      orientation = screen.orientation ? {
        type: screen.orientation.type || null,
        angle: screen.orientation.angle
      } : null;
    } catch (_) {}
    return {
      screen: {
        width: screen.width, height: screen.height,
        availWidth: screen.availWidth, availHeight: screen.availHeight,
        colorDepth: screen.colorDepth, pixelDepth: screen.pixelDepth
      },
      viewport: {width: innerWidth, height: innerHeight,
                 outerWidth: outerWidth, outerHeight: outerHeight},
      devicePixelRatio: devicePixelRatio,
      screenX: screenX, screenY: screenY,
      orientation: orientation
    };
  }
  function appearanceValues() {
    function matches(query) {
      try { return matchMedia(query).matches; } catch (_) { return null; }
    }
    return {
      colorScheme: matches('(prefers-color-scheme: dark)') === true ? 'dark' :
                   (matches('(prefers-color-scheme: light)') === true ? 'light' : null),
      reducedMotion: matches('(prefers-reduced-motion: reduce)'),
      forcedColors: matches('(forced-colors: active)')
    };
  }
  function inputValues() {
    function matches(query) {
      try { return matchMedia(query).matches; } catch (_) { return null; }
    }
    return {
      pointer: matches('(pointer: fine)') ? 'fine' :
               (matches('(pointer: coarse)') ? 'coarse' : 'none'),
      hover: matches('(hover: hover)'),
      maxTouchPoints: navigator.maxTouchPoints || 0
    };
  }
  function webglValues() {
    const c = document.createElement('canvas');
    let contextCreationError = null;
    c.addEventListener('webglcontextcreationerror', function(e) {
      contextCreationError = e.statusMessage || String(e);
    });
    const gl = c.getContext('webgl') || c.getContext('experimental-webgl');
    if (!gl) return {supported: false, contextCreationError: contextCreationError};
    const debug = gl.getExtension('WEBGL_debug_renderer_info');
    let maxViewport = null;
    try { maxViewport = Array.from(gl.getParameter(gl.MAX_VIEWPORT_DIMS)); }
    catch (_) {}
    let extensions = [];
    try { extensions = gl.getSupportedExtensions() || []; } catch (_) {}
    return {
      supported: true,
      contextCreationError: contextCreationError,
      vendor: gl.getParameter(gl.VENDOR), renderer: gl.getParameter(gl.RENDERER),
      unmaskedVendor: debug ? gl.getParameter(debug.UNMASKED_VENDOR_WEBGL) : null,
      unmaskedRenderer: debug ? gl.getParameter(debug.UNMASKED_RENDERER_WEBGL) : null,
      version: gl.getParameter(gl.VERSION),
      shadingLanguageVersion: gl.getParameter(gl.SHADING_LANGUAGE_VERSION),
      maxTextureSize: gl.getParameter(gl.MAX_TEXTURE_SIZE),
      maxCubeMapTextureSize: gl.getParameter(gl.MAX_CUBE_MAP_TEXTURE_SIZE),
      maxViewportDims: maxViewport, extensionCount: extensions.length,
      extensions: extensions
    };
  }
  function canvasValues() {
    const c = document.createElement('canvas');
    c.width = 240; c.height = 60;
    const x = c.getContext('2d');
    if (!x) return {supported: false};
    x.textBaseline = 'alphabetic'; x.font = '16px sans-serif';
    x.fillStyle = '#17324d'; x.fillRect(0, 0, 240, 60);
    x.fillStyle = '#d8a21b'; x.fillText('Termux Firefox persona', 3, 32);
    const m = x.measureText('Termux Firefox persona');
    return {supported: true, dataUrlLength: c.toDataURL().length,
            textMetrics: {width: m.width, actualBoundingBoxAscent: m.actualBoundingBoxAscent,
                          actualBoundingBoxDescent: m.actualBoundingBoxDescent}};
  }
  function audioValues() {
    const AC = window.AudioContext || window.webkitAudioContext;
    const OAC = window.OfflineAudioContext || window.webkitOfflineAudioContext;
    const out = {supported: !!AC, offlineSupported: !!OAC};
    if (AC) {
      try {
        const ac = new AC();
        out.sampleRate = ac.sampleRate; out.state = ac.state;
        out.maxChannelCount = ac.destination.maxChannelCount;
        out.baseLatency = ac.baseLatency;
        if (ac.close) ac.close();
      } catch (e) { out.error = String(e); }
    }
    if (OAC) {
      try { out.offlineSampleRate = new OAC(1, 16, 44100).sampleRate; }
      catch (e) { out.offlineError = String(e); }
    }
    return out;
  }
  const probe = window.__tbpProbe = {
    window: windowValues(), display: displayValues(), appearance: appearanceValues(),
    input: inputValues(), webgl: webglValues(), canvas: canvasValues(),
    audio: audioValues(), workers: {}
  };
  let dedicated = null;
  let shared = null;
  let serviceRegistration = null;
  function worker(kind, value) { probe.workers[kind] = value; }
  try {
    dedicated = new Worker('/__tbp_dedicated_worker.js');
    dedicated.onmessage = function(e) { worker('dedicated', e.data); };
    dedicated.onerror = function(e) { worker('dedicated', {error: String(e.message || e)}); };
  } catch (e) { worker('dedicated', {error: String(e)}); }
  try {
    shared = new SharedWorker('/__tbp_shared_worker.js');
    shared.port.onmessage = function(e) { worker('shared', e.data); };
    shared.port.start();
  } catch (e) { worker('shared', {error: String(e)}); }
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.addEventListener('message', function(e) {
      if (e.data && e.data.kind === 'service') worker('service', e.data);
    });
    navigator.serviceWorker.register('/__tbp_service_worker.js').then(function(reg) {
      serviceRegistration = reg;
      return navigator.serviceWorker.ready;
    }).then(function(reg) {
      if (reg.active) reg.active.postMessage({probe: true});
    }).catch(function(e) { worker('service', {error: String(e)}); });
  } else {
    worker('service', {unsupported: true});
  }
  window.__tbpProbeCleanup = function() {
    try { if (dedicated) dedicated.terminate(); } catch (_) {}
    try { if (shared && shared.port) shared.port.close(); } catch (_) {}
    try {
      if (serviceRegistration) {
        serviceRegistration.unregister();
      } else if (navigator.serviceWorker) {
        navigator.serviceWorker.getRegistration('/probe.html').then(function(reg) {
          if (reg) reg.unregister();
        });
      }
    } catch (_) {}
    return true;
  };
})();
</script>'''


class _ProbeState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.document_headers: dict[str, str] | None = None
        self.worker_headers: list[dict[str, Any]] = []
        self.requests: list[dict[str, Any]] = []

    def record(self, path: str, headers: Mapping[str, str]) -> None:
        clean = {str(k): str(v) for k, v in headers.items()}
        path_name = urlsplit(path).path
        if path_name in {"/", "/probe.html"}:
            kind = "document"
        elif "worker" in path_name:
            kind = "worker"
        else:
            kind = "resource"
        worker_type = None
        if kind == "worker":
            if "dedicated" in path_name:
                worker_type = "dedicated"
            elif "shared" in path_name:
                worker_type = "shared"
            elif "service" in path_name:
                worker_type = "service"
        item = {"path": path_name, "kind": kind, "worker_type": worker_type,
                "headers": clean,
                "time": time.time()}
        with self.lock:
            self.requests.append(item)
            if kind == "document" and self.document_headers is None:
                self.document_headers = clean
            elif kind == "worker":
                self.worker_headers.append(item)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "document": dict(self.document_headers or {}),
                "worker": [dict(item) for item in self.worker_headers],
                "requests": [dict(item) for item in self.requests],
            }


class _ProbeHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def _handler_for(state: _ProbeState):
    class ProbeHandler(BaseHTTPRequestHandler):
        server_version = "TBPProbe/1"
        sys_version = ""

        def log_message(self, *_args: Any) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802 (stdlib handler API)
            state.record(self.path, self.headers)
            path = urlsplit(self.path).path
            if path in {"/", "/probe.html"}:
                body, content_type = PROBE_HTML.encode(), "text/html; charset=utf-8"
            elif path == "/__tbp_dedicated_worker.js":
                body, content_type = _worker_values("dedicated").encode(), "text/javascript"
            elif path == "/__tbp_shared_worker.js":
                body, content_type = _worker_values("shared").encode(), "text/javascript"
            elif path == "/__tbp_service_worker.js":
                body, content_type = _worker_values("service").encode(), "text/javascript"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return ProbeHandler


class LoopbackProbeServer:
    """Threaded loopback HTTP server used by :class:`ProbeRunner`."""

    def __init__(self) -> None:
        self.state = _ProbeState()
        self._server: _ProbeHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        if self._server is None:
            raise RuntimeError("probe server is not started")
        return int(self._server.server_address[1])

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/probe.html"

    def start(self) -> "LoopbackProbeServer":
        if self._server is not None:
            return self
        self._server = _ProbeHTTPServer(("127.0.0.1", 0), _handler_for(self.state))
        bound_host = str(self._server.server_address[0])
        if bound_host not in {"127.0.0.1", "::1", "localhost"}:
            self._server.server_close()
            self._server = None
            raise RuntimeError(f"probe server unexpectedly bound to non-loopback host {bound_host!r}")
        self._thread = threading.Thread(target=self._server.serve_forever,
                                         name="tbp-probe-http", daemon=True)
        self._thread.start()
        return self

    def snapshot(self) -> dict[str, Any]:
        return self.state.snapshot()

    def close(self) -> None:
        server, self._server = self._server, None
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._thread = None

    def __enter__(self) -> "LoopbackProbeServer":
        return self.start()

    def __exit__(self, *_exc: Any) -> None:
        self.close()


class ProbeRunner:
    """Run a browser capability probe through a BiDi-like client."""

    WORKER_KINDS = ("dedicated", "shared", "service")

    def __init__(self, client: Any, *, timeout: float = 5.0, poll_interval: float = 0.1):
        self.client = client
        self.timeout = float(timeout)
        self.poll_interval = float(poll_interval)

    async def run(self, context: str, *, timeout: float | None = None) -> dict[str, Any]:
        wait_timeout = self.timeout if timeout is None else float(timeout)
        server = LoopbackProbeServer().start()
        try:
            await self.client.navigate(context, server.url, wait="complete", timeout=wait_timeout)
            page = await self._read_page(context, wait_timeout)
            http = server.snapshot()
            observations = {"http": http, "page": page,
                            "workers": page.get("workers", {}) if isinstance(page, Mapping) else {}}
            checks = self._checks(observations)
            return {
                "context": context, "url": server.url,
                "observations": observations, "raw": observations,
                "checks": checks,
            }
        finally:
            # Worker and ServiceWorker registrations outlive the document.
            # Ask the page to clean them up before tearing down its origin.
            try:
                await self.client.evaluate(
                    context,
                    "window.__tbpProbeCleanup ? window.__tbpProbeCleanup() : true",
                    timeout=min(wait_timeout, 1.0),
                )
            except Exception:
                pass
            server.close()

    async def _read_page(self, context: str, timeout: float) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + timeout
        expression = "window.__tbpProbe || null"
        last: Any = None
        while True:
            try:
                last = await self.client.evaluate(context, expression, timeout=timeout)
            except Exception as exc:
                last = {"error": str(exc)}
            if isinstance(last, Mapping) and last.get("window"):
                workers = last.get("workers") or {}
                if all(kind in workers for kind in self.WORKER_KINDS):
                    return dict(last)
            if asyncio.get_running_loop().time() >= deadline:
                return dict(last) if isinstance(last, Mapping) else {"error": "probe timeout"}
            await asyncio.sleep(self.poll_interval)

    @staticmethod
    def _checks(observations: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        http = observations.get("http") or {}
        page = observations.get("page") or {}
        workers = observations.get("workers") or {}
        checks: dict[str, dict[str, Any]] = {}

        def add(name: str, status: str, detail: Any = None) -> None:
            item: dict[str, Any] = {"status": status}
            if detail is not None:
                item["detail"] = detail
            checks[name] = item

        add("document_headers", "pass" if http.get("document") else "fail")
        add("worker_headers", "pass" if http.get("worker") else "partial")
        window = page.get("window") or {}
        window_complete = (
            isinstance(window, Mapping)
            and all(window.get(key) not in (None, "") for key in (
                "userAgent", "platform", "oscpu", "appVersion",
                "hardwareConcurrency", "timezone",
            ))
            and isinstance(window.get("languages"), (list, tuple))
        )
        add("window", "pass" if window_complete else "partial")
        for kind in ProbeRunner.WORKER_KINDS:
            value = workers.get(kind)
            complete_worker = (
                isinstance(value, Mapping)
                and not value.get("error")
                and not value.get("unsupported")
                and all(value.get(key) not in (None, "") for key in (
                    "userAgent", "platform", "hardwareConcurrency", "timezone"
                ))
                and isinstance(value.get("languages"), (list, tuple))
            )
            if complete_worker:
                add(f"{kind}_worker", "pass")
            elif value and value.get("unsupported"):
                add(f"{kind}_worker", "partial", "unsupported by this browser")
            else:
                add(f"{kind}_worker", "partial", "timed out or unavailable")
        display = page.get("display") or {}
        display_complete = (
            isinstance(display, Mapping)
            and isinstance(display.get("screen"), Mapping)
            and isinstance(display.get("viewport"), Mapping)
            and display.get("devicePixelRatio") is not None
        )
        add("display", "pass" if display_complete else "partial")
        webgl = page.get("webgl") or {}
        add("webgl", "pass" if webgl.get("supported") and not webgl.get("contextCreationError") else "partial")
        add("canvas", "pass" if (page.get("canvas") or {}).get("supported") else "partial")
        add("audio", "pass" if (page.get("audio") or {}).get("supported") else "partial")
        return checks


__all__ = ["LoopbackProbeServer", "ProbeRunner", "PROBE_HTML"]
