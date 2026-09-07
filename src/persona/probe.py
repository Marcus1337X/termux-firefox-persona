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
      contrast: matches('(prefers-contrast: more)') === true ? 'more' :
                (matches('(prefers-contrast: less)') === true ? 'less' :
                 (matches('(prefers-contrast: custom)') === true ? 'custom' : 'no-preference')),
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
    function precision(gl, shaderType, precisionType) {
      try {
        const value = gl.getShaderPrecisionFormat(shaderType, precisionType);
        return value ? {rangeMin: value.rangeMin, rangeMax: value.rangeMax,
                        precision: value.precision} : null;
      } catch (_) { return null; }
    }
    function inspect(kind) {
      const canvas = document.createElement('canvas');
      canvas.width = 2; canvas.height = 2;
      let contextCreationError = null;
      canvas.addEventListener('webglcontextcreationerror', function(e) {
        contextCreationError = e.statusMessage || String(e);
      });
      const gl = canvas.getContext(kind, {preserveDrawingBuffer: true});
      if (!gl) {
        return {supported: false, contextCreationError: contextCreationError,
                behavior: {passed: false, reason: 'context-unavailable'}};
      }
      const is2 = kind === 'webgl2';
      const debug = gl.getExtension('WEBGL_debug_renderer_info');
      let maxViewport = null;
      try { maxViewport = Array.from(gl.getParameter(gl.MAX_VIEWPORT_DIMS)); }
      catch (_) {}
      let extensions = [];
      try { extensions = gl.getSupportedExtensions() || []; } catch (_) {}
      const limits = {};
      for (const name of ['MAX_TEXTURE_SIZE', 'MAX_CUBE_MAP_TEXTURE_SIZE',
                          'MAX_VERTEX_ATTRIBS', 'MAX_COMBINED_TEXTURE_IMAGE_UNITS',
                          'MAX_FRAGMENT_UNIFORM_VECTORS', 'MAX_VERTEX_UNIFORM_VECTORS']) {
        try { limits[name] = gl.getParameter(gl[name]); } catch (_) { limits[name] = null; }
      }
      if (is2) {
        for (const name of ['MAX_UNIFORM_BUFFER_BINDINGS', 'MAX_3D_TEXTURE_SIZE',
                            'MAX_ARRAY_TEXTURE_LAYERS']) {
          try { limits[name] = gl.getParameter(gl[name]); } catch (_) { limits[name] = null; }
        }
      }
      const precisionValues = {
        vertexHighFloat: precision(gl, gl.VERTEX_SHADER, gl.HIGH_FLOAT),
        fragmentHighFloat: precision(gl, gl.FRAGMENT_SHADER, gl.HIGH_FLOAT),
        vertexMediumFloat: precision(gl, gl.VERTEX_SHADER, gl.MEDIUM_FLOAT),
        fragmentMediumFloat: precision(gl, gl.FRAGMENT_SHADER, gl.MEDIUM_FLOAT)
      };
      const errors = [];
      function note(label, value) { if (!value) errors.push(label); return value; }
      const vertexSource = is2 ? '#version 300 es\nin vec2 p;\nvoid main(){gl_Position=vec4(p,0.0,1.0);}'
        : 'attribute vec2 p;\nvoid main(){gl_Position=vec4(p,0.0,1.0);}';
      const fragmentSource = is2 ? '#version 300 es\nprecision highp float;\nout vec4 color;\nvoid main(){color=vec4(1.0,0.0,0.0,1.0);}'
        : 'precision mediump float;\nvoid main(){gl_FragColor=vec4(1.0,0.0,0.0,1.0);}';
      function shader(type, source, label) {
        const value = gl.createShader(type);
        if (!value) { errors.push(label + '-create'); return null; }
        gl.shaderSource(value, source); gl.compileShader(value);
        if (!gl.getShaderParameter(value, gl.COMPILE_STATUS)) {
          errors.push(label + '-compile');
          gl.deleteShader(value); return null;
        }
        return value;
      }
      const vertex = shader(gl.VERTEX_SHADER, vertexSource, 'vertex');
      const fragment = shader(gl.FRAGMENT_SHADER, fragmentSource, 'fragment');
      const program = gl.createProgram();
      let linked = false;
      if (program && vertex && fragment) {
        gl.attachShader(program, vertex); gl.attachShader(program, fragment);
        gl.linkProgram(program); linked = !!gl.getProgramParameter(program, gl.LINK_STATUS);
        if (!linked) errors.push('link');
      } else { errors.push('program-create'); }
      let triangle = {compile: !!(vertex && fragment), link: linked,
                      redPixels: 0, nonEmpty: false, readback: false};
      if (program && linked) {
        gl.useProgram(program);
        const buffer = gl.createBuffer();
        const location = gl.getAttribLocation(program, 'p');
        if (buffer && location >= 0) {
          gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
          // Cover the complete 2x2 canvas so every sampled pixel has the
          // shader's exact red output.
          gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
          gl.enableVertexAttribArray(location); gl.vertexAttribPointer(location, 2, gl.FLOAT, false, 0, 0);
          gl.viewport(0, 0, 2, 2); gl.clearColor(0, 0, 0, 1); gl.clear(gl.COLOR_BUFFER_BIT);
          gl.drawArrays(gl.TRIANGLES, 0, 3);
          const pixels = new Uint8Array(16); gl.readPixels(0, 0, 2, 2, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
          let redPixels = 0; let anyPixel = false; let exactRed = true;
          for (let i = 0; i < pixels.length; i += 4) {
            redPixels += pixels[i] > 0 ? 1 : 0;
            anyPixel = anyPixel || pixels[i] > 0 || pixels[i + 1] > 0 || pixels[i + 2] > 0;
            exactRed = exactRed && pixels[i] === 255 && pixels[i + 1] === 0 &&
                       pixels[i + 2] === 0 && pixels[i + 3] === 255;
          }
          triangle = {compile: true, link: true, redPixels: redPixels,
                      nonEmpty: redPixels > 0, exactRed: exactRed,
                      rgba: Array.from(pixels.slice(0, 4)),
                      readback: exactRed && anyPixel && gl.getError() === gl.NO_ERROR};
          gl.deleteBuffer(buffer);
        } else { errors.push('triangle-buffer-or-attribute'); }
      }
      const texture = gl.createTexture(); const framebuffer = gl.createFramebuffer();
      let framebufferCheck = {complete: false, readback: false, rgba8: false,
                              exactGreen: false, rgba: null};
      if (texture && framebuffer) {
        gl.bindTexture(gl.TEXTURE_2D, texture);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
        const internal = is2 && gl.RGBA8 !== undefined ? gl.RGBA8 : gl.RGBA;
        try {
          gl.texImage2D(gl.TEXTURE_2D, 0, internal, 2, 2, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
          framebufferCheck.rgba8 = true;
        } catch (_) { errors.push('rgba8-texture'); }
        gl.bindFramebuffer(gl.FRAMEBUFFER, framebuffer);
        gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, texture, 0);
        framebufferCheck.complete = gl.checkFramebufferStatus(gl.FRAMEBUFFER) === gl.FRAMEBUFFER_COMPLETE;
        if (!framebufferCheck.complete) errors.push('framebuffer-complete');
        if (framebufferCheck.complete) {
          gl.clearColor(0, 1, 0, 1); gl.clear(gl.COLOR_BUFFER_BIT);
          const readback = new Uint8Array(4); gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, readback);
          framebufferCheck.rgba = Array.from(readback);
          framebufferCheck.exactGreen = readback[0] === 0 && readback[1] === 255 &&
                                        readback[2] === 0 && readback[3] === 255;
          framebufferCheck.readback = framebufferCheck.exactGreen && gl.getError() === gl.NO_ERROR;
          if (!framebufferCheck.readback) errors.push('framebuffer-readback');
        }
        gl.bindFramebuffer(gl.FRAMEBUFFER, null); gl.deleteFramebuffer(framebuffer); gl.deleteTexture(texture);
      } else { errors.push('framebuffer-create'); }
      if (vertex) gl.deleteShader(vertex); if (fragment) gl.deleteShader(fragment); if (program) gl.deleteProgram(program);
      const behavior = {compile: triangle.compile, link: triangle.link,
                        triangle: triangle, framebuffer: framebufferCheck,
                        passed: triangle.compile && triangle.link && triangle.nonEmpty && triangle.exactRed && triangle.readback &&
                                framebufferCheck.rgba8 && framebufferCheck.complete && framebufferCheck.exactGreen &&
                                framebufferCheck.readback && errors.length === 0,
                        errors: errors};
      // Copy identity values before releasing the context.  Some Firefox
      // builds return null from getParameter after loseContext().
      const identity = {
        vendor: gl.getParameter(gl.VENDOR), renderer: gl.getParameter(gl.RENDERER),
        unmaskedVendor: debug ? gl.getParameter(debug.UNMASKED_VENDOR_WEBGL) : null,
        unmaskedRenderer: debug ? gl.getParameter(debug.UNMASKED_RENDERER_WEBGL) : null,
        version: gl.getParameter(gl.VERSION), shadingLanguageVersion: gl.getParameter(gl.SHADING_LANGUAGE_VERSION),
        maxTextureSize: limits.MAX_TEXTURE_SIZE, maxCubeMapTextureSize: limits.MAX_CUBE_MAP_TEXTURE_SIZE,
        maxViewportDims: maxViewport, extensionCount: extensions.length, extensions: extensions,
        limits: limits, precision: precisionValues
      };
      const lose = gl.getExtension('WEBGL_lose_context');
      if (lose) { try { lose.loseContext(); } catch (_) {} }
      return {supported: true, contextCreationError: contextCreationError,
              ...identity, behavior: behavior};
    }
    const webgl1 = inspect('webgl');
    const webgl2 = inspect('webgl2');
    return {
      supported: webgl1.supported,
      contextCreationError: webgl1.contextCreationError,
      vendor: webgl1.vendor, renderer: webgl1.renderer,
      unmaskedVendor: webgl1.unmaskedVendor, unmaskedRenderer: webgl1.unmaskedRenderer,
      version: webgl1.version, shadingLanguageVersion: webgl1.shadingLanguageVersion,
      maxTextureSize: webgl1.maxTextureSize, maxCubeMapTextureSize: webgl1.maxCubeMapTextureSize,
      maxViewportDims: webgl1.maxViewportDims, extensionCount: webgl1.extensionCount,
      extensions: webgl1.extensions, limits: webgl1.limits, precision: webgl1.precision,
      webgl1: webgl1, webgl2: webgl2,
      behavior: {webgl1: webgl1.behavior, webgl2: webgl2.behavior}
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

    async def run(
        self,
        context: str,
        *,
        timeout: float | None = None,
        geolocation: bool = False,
    ) -> dict[str, Any]:
        wait_timeout = self.timeout if timeout is None else float(timeout)
        server = LoopbackProbeServer().start()
        origin = f"http://127.0.0.1:{server.port}"
        try:
            await self.client.navigate(
                context,
                server.url,
                wait="complete",
                timeout=wait_timeout,
            )
            page = await self._read_page(context, wait_timeout)
            if geolocation:
                page = dict(page)
                page["geolocation"] = await self._run_geolocation(
                    context, origin, wait_timeout
                )
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

    async def _permission_state(self, context: str, timeout: float) -> Any:
        return await self.client.evaluate(
            context,
            "navigator.permissions && navigator.permissions.query "
            "? navigator.permissions.query({name: 'geolocation'}).then(p => p.state) "
            ": 'unsupported'",
            timeout=timeout,
        )

    async def _set_geolocation_permission(
        self,
        origin: str,
        state: str,
        timeout: float,
    ) -> Any:
        """Set one origin's permission in the default BiDi user context."""

        return await self.client.send(
            "permissions.setPermission",
            {
                "descriptor": {"name": "geolocation"},
                "state": state,
                "origin": origin,
                "userContext": "default",
            },
            timeout=timeout,
        )

    async def _position(self, context: str, timeout: float) -> Any:
        # A timeout prevents a denied or unsupported implementation from
        # holding the entire qualification run open.
        expression = """new Promise(resolve => {
          if (!navigator.geolocation) { resolve({supported:false}); return; }
          let done = false;
          const finish = value => { if (!done) { done = true; resolve(value); } };
          const timer = setTimeout(() => finish({ok:false, errorCode:3, error:'timeout'}), 2500);
          navigator.geolocation.getCurrentPosition(
            p => { clearTimeout(timer); finish({ok:true, latitude:p.coords.latitude,
              longitude:p.coords.longitude, accuracy:p.coords.accuracy}); },
            e => { clearTimeout(timer); finish({ok:false, errorCode:e.code, error:e.message || ''}); },
            {maximumAge: 0, timeout: 2000}
          );
        })"""
        return await self.client.evaluate(context, expression, timeout=timeout)

    async def _run_geolocation(
        self,
        context: str,
        origin: str,
        timeout: float,
    ) -> dict[str, Any]:
        """Probe one loopback origin and restore its prior permission state."""

        result: dict[str, Any] = {
            "origin": origin,
            "user_context": "default",
            "worker_contexts": {
                "dedicated": "notapplicable",
                "shared": "notapplicable",
                "service": "notapplicable",
            },
            "permission": "geolocation",
        }
        original: Any = None
        try:
            original = await self._permission_state(context, timeout)
            result["original_state"] = original
            result["original_query"] = {"name": "geolocation", "state": original}
        except Exception as exc:
            result["original_state"] = None
            result["error"] = f"permission query failed: {exc}"

        try:
            if original not in {"prompt", "granted", "denied"}:
                raise RuntimeError("original geolocation permission state is unavailable")
            await self._set_geolocation_permission(origin, "granted", timeout)
            result["granted_state"] = await self._permission_state(context, timeout)
            result["granted_position"] = await self._position(context, timeout)
            await self._set_geolocation_permission(origin, "denied", timeout)
            result["denied_state"] = await self._permission_state(context, timeout)
            result["denied_position"] = await self._position(context, timeout)
        except Exception as exc:
            result["error"] = str(exc)
        finally:
            if original in {"prompt", "granted", "denied"}:
                try:
                    await self._set_geolocation_permission(origin, original, timeout)
                    result["restored_state"] = await self._permission_state(context, timeout)
                    result["restored_query"] = {
                        "name": "geolocation", "state": result["restored_state"]
                    }
                except Exception as exc:
                    result["restore_error"] = str(exc)
            else:
                result["restore_error"] = "original permission state was not known"
        return result

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
