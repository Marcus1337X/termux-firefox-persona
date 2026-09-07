"""Foreground worker. Each process owns exactly one Persona and its windows."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import ExitStack
import hmac
import json
import logging
import os
from pathlib import Path
import secrets
import signal
import tempfile
import time

from .bidi import BiDiClient
from .control import atomic_json, check_process_budget, file_lock, process_identity, process_usage, read_json
from .manager import PersonaManager
from .probe import ProbeRunner

logger = logging.getLogger(__name__)


def firefox_settings(config: dict) -> tuple[dict, dict]:
    locale = config["locale"]
    appearance = config.get("appearance", {})
    env = {"TZ": locale["timezone"]}
    prefs = {
        "dom.maxHardwareConcurrency": config["cpu"]["hardware_concurrency"],
        # Keep Fission/site isolation, but avoid spare processes and four
        # renderers per site on Android's tightly limited process budget.
        "dom.ipc.processPrelaunch.enabled": False,
        "dom.ipc.processCount": 1,
        "dom.ipc.processCount.webIsolated": 1,
        "dom.ipc.keepProcessesAlive.privilegedabout": 0,
        "intl.accept_languages": ", ".join(locale["languages"]),
        "intl.locale.requested": locale["locale"],
        "privacy.resistFingerprinting": False,
        "privacy.fingerprintingProtection": False,
        "layout.css.devPixelsPerPx": str(config["display"]["device_pixel_ratio"]),
        "browser.startup.page": 0,
        "browser.startup.homepage": "about:blank",
        "browser.sessionstore.resume_from_crash": False,
        "browser.tabs.warnOnClose": False,
        "browser.tabs.warnOnCloseOtherTabs": False,
        "browser.warnOnQuitShortcut": False,
        "security.enterprise_roots.enabled": True,
        "ui.systemUsesDarkTheme": int(appearance.get("color_scheme") == "dark"),
        "ui.prefersReducedMotion": int(appearance.get("reduced_motion", False)),
        "browser.theme.content-theme": 0 if appearance.get("color_scheme") == "dark" else 1,
    }
    if "contrast" in appearance:
        # The region/appearance preset explicitly requests ordinary document
        # colors. Firefox derives both contrast and forced-colors from this
        # native preference policy; qualification checks its actual queries.
        prefs["browser.display.document_color_use"] = 1
        prefs["ui.useAccessibilityTheme"] = 0
    if config.get("graphics", {}).get("context_backend") == "glx":
        # The explicit software-GLX preset avoids this device's failing EGL
        # display path. Existing presets retain their original GL policy.
        prefs["gfx.x11-egl.force-disabled"] = True
    if config.get("fonts"):
        fonts = config["fonts"]
        # Firefox's whitelist disables every CSS local() source. The private
        # Fontconfig inventory enforces the selection without disabling them.
        prefs["font.system.whitelist"] = ""
        prefs["gfx.bundled-fonts.activate"] = 0
        for group in ("x-western", "x-unicode", "zh-CN", "zh-TW"):
            for generic in ("sans-serif", "serif", "monospace"):
                family = fonts["aliases"][generic]
                prefs[f"font.name.{generic}.{group}"] = family
                fallback = fonts["aliases"].get("cjk")
                prefs[f"font.name-list.{generic}.{group}"] = ",".join(dict.fromkeys(
                    name for name in (family, fallback) if name))
        if fonts["aliases"].get("emoji"):
            prefs["font.name-list.emoji"] = fonts["aliases"]["emoji"]
    if config.get("audio"):
        prefs["media.cubeb.force_sample_rate"] = config["audio"]["sample_rate"]
    return prefs, env


async def apply_browser_overrides(bidi, config: dict) -> dict:
    """Install identity before any target navigation; leave site consent intact."""
    applied = {}
    geolocation = config.get("geolocation")
    if geolocation is not None:
        coordinates = {name: geolocation[name] for name in ("latitude", "longitude", "accuracy")}
        await bidi.send("emulation.setGeolocationOverride", {
            "coordinates": coordinates, "userContexts": ["default"],
        })
        applied["geolocation"] = {"coordinates": coordinates, "scope": "user-context:default",
                                  "permission_policy": "native-site-consent"}
    return applied


class PersonaRuntime:
    def __init__(self, manager: PersonaManager, persona_id: str, instance_id: str):
        self.manager = manager
        self.persona = manager.load_worker_persona(persona_id, instance_id)
        self.paths = manager.paths(persona_id)
        self.state = read_json(self.paths["state"]) or {}
        if self.state.get("instance_id") != instance_id:
            raise RuntimeError("Stale worker startup request")
        self.instance_id = instance_id
        self.pilot = None
        self.bidi = None
        self.legacy = None
        self.context = None
        self.root_wid = None
        self.stop_event = asyncio.Event()
        self.command_lock = asyncio.Lock()
        self.server = None
        self.watchdog = None
        self.started_at = time.time()

    def persist(self, status: str, **extra):
        resources = []
        if self.pilot:
            for process in (self.pilot._browser._xvfb_proc, self.pilot._browser._wm_proc,
                            getattr(self.pilot._session, "_firefox_proc", None)):
                if process and (identity := process_identity(process.pid)):
                    resources.append(identity)
        self.state.update(status=status, worker=process_identity(os.getpid()), resources=resources,
                          usage=process_usage(),
                          **extra)
        atomic_json(self.paths["state"], self.state)

    @staticmethod
    async def _display(leases: ExitStack) -> str:
        lock_dir = Path(tempfile.gettempdir()) / "tbp-persona-displays"
        lock_dir.mkdir(mode=0o700, exist_ok=True)
        numbers = list(range(200, 800))
        secrets.SystemRandom().shuffle(numbers)
        for number in numbers:
            lease = file_lock(lock_dir / f"{number}.lock", blocking=False)
            try:
                lease.__enter__()
            except BlockingIOError:
                continue
            candidates = {Path("/tmp"), Path(tempfile.gettempdir())}
            if any((base / f".X{number}-lock").exists() or
                   (base / ".X11-unix" / f"X{number}").exists() for base in candidates):
                lease.__exit__(None, None, None)
                continue
            leases.callback(lease.__exit__, None, None, None)
            return f":{number}"
        raise RuntimeError("No unused Xvfb display available")

    async def run(self):
        from ..pilot import Pilot
        # Legacy actions use relative export paths; keep their defaults private
        # and distinct for each Persona, just like its profile and state.
        os.chdir(self.paths["directory"])
        with ExitStack() as leases:
            leases.enter_context(file_lock(self.paths["worker_lock"], blocking=False))
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, self.stop_event.set)
            try:
                display = await self._display(leases)
                self.persist("starting", display=display)
                self.paths["profile"].mkdir(mode=0o700, exist_ok=True)
                config = dict(self.persona.final_config)
                if config.get("graphics", {}).get("context_backend") == "glx" and self.manager.backend != "software":
                    raise ValueError("The software GLX preset requires the software execution backend")
                prefs, env = firefox_settings(config)
                if config.get("fonts"):
                    from .fonts import build_persona_fontconfig
                    bundle = build_persona_fontconfig(self.paths["directory"] / "font-environment", config["fonts"])
                    env.update(bundle.env)
                    prefs.update(bundle.prefs)
                self.pilot = Pilot(
                    browser="firefox", display=display,
                    window_size=f"{config['display']['screen_width']},{config['display']['screen_height']}",
                    user_data_dir=str(self.paths["profile"]),
                    lock_path=str(self.paths["directory"] / "browser.lock"),
                    firefox_launch_env=env, firefox_prefs=prefs,
                    remote_debugging_port=0, firefox_backend=self.manager.backend,
                    profile_kind="persona",
                )
                boot = asyncio.create_task(self.pilot.start())
                # Record owned resources during startup as well, so a crash
                # before Firefox announces readiness can be recovered safely.
                while not boot.done():
                    self.persist("starting")
                    if self.stop_event.is_set():
                        boot.cancel()
                        try:
                            await boot
                        except asyncio.CancelledError:
                            pass
                        return
                    await asyncio.sleep(0.1)
                await boot
                self.persist("starting")
                self.bidi = await BiDiClient(ws_url=self.pilot._session.bidi_url).connect()
                overrides = await apply_browser_overrides(self.bidi, config)
                self.persist("starting", browser_overrides=overrides)
                tree = await self.bidi.get_tree()
                if not tree.get("contexts"):
                    raise RuntimeError("Firefox has no browser context")
                self.context = tree["contexts"][0]["context"]
                self.root_wid = self.pilot._session._main_wid
                self.pilot._session.bind_javascript_evaluator(self.evaluate)
                for state in ("MAXIMIZED_VERT", "MAXIMIZED_HORZ"):
                    await self.pilot._session._xdt(
                        ["windowstate", "--add", state, self.root_wid])
                await asyncio.sleep(0.4)
                # One legacy daemon object per worker process preserves all
                # upstream module state without sharing it between Personas.
                from ..daemon import Daemon
                self.legacy = Daemon(browser="firefox")
                self.legacy.pilot = self.pilot
                self.legacy._main_wid = self.root_wid
                self.legacy._start_time = self.started_at
                self.legacy._cmd_lock = asyncio.Lock()
                self.legacy.shutdown = self.shutdown
                self.server = await asyncio.start_unix_server(
                    self.handle_client, path=str(self.paths["socket"]), limit=4 * 1024 * 1024)
                os.chmod(self.paths["socket"], 0o600)
                self.persist("ready", bidi_port=self.pilot._session.remote_debugging_port,
                             root_window=self.root_wid, context=self.context)
                self.watchdog = asyncio.create_task(self.monitor())
                await self.stop_event.wait()
            except Exception as exc:
                logger.exception("Persona worker failed")
                self.persist("failed", error=str(exc))
                raise
            finally:
                if self.watchdog:
                    self.watchdog.cancel()
                    try:
                        await self.watchdog
                    except asyncio.CancelledError:
                        pass
                if self.server:
                    self.server.close()
                    await self.server.wait_closed()
                failed = self.state.get("status") == "failed"
                self.persist("failed" if failed else "stopping")
                if self.bidi:
                    await self.bidi.close()
                if self.pilot:
                    await self.pilot.stop()
                try:
                    self.paths["socket"].unlink()
                except FileNotFoundError:
                    pass
                self.persist("failed" if failed else "stopped")

    async def shutdown(self):
        self.stop_event.set()

    async def monitor(self):
        while not self.stop_event.is_set():
            await asyncio.sleep(2)
            process = self.pilot._session._firefox_proc
            if process is None or process.returncode is not None:
                self.stop_event.set()
                return
            try:
                pid = await self.pilot._session._xdt(["getwindowpid", self.root_wid], timeout=3)
                if not pid.strip():
                    self.stop_event.set()
                    return
            except Exception:
                self.stop_event.set()
                return

    async def select_context(self, context: str | None = None):
        tree = await self.bidi.get_tree()
        contexts = tree.get("contexts", [])
        ids = [item["context"] for item in contexts]
        if context is not None:
            if context not in ids:
                raise ValueError("Context does not belong to this Persona")
            await self.bidi.send("browsingContext.activate", {"context": context})
            self.context = context
        else:
            visible = []
            for item in contexts:
                try:
                    state = await self.bidi.evaluate(item["context"],
                        "({visible:document.visibilityState==='visible',focused:document.hasFocus()})",
                        timeout=3)
                except Exception:
                    continue
                if isinstance(state, dict) and state.get("visible"):
                    visible.append(item["context"])
                    if state.get("focused"):
                        self.context = item["context"]
                        break
            else:
                if self.context not in visible and visible:
                    self.context = visible[0]
                elif self.context not in ids and ids:
                    self.context = ids[0]
        if self.context not in ids:
            raise RuntimeError("No live browser context")
        # Native input must use the X11 window which is actually active in
        # this instance's private display, and whose PID belongs to Firefox.
        session = self.pilot._session
        try:
            wid = (await session._xdt(["getactivewindow"])).strip()
            pid = (await session._xdt(["getwindowpid", wid])).strip()
            if int(pid) == session._firefox_proc.pid:
                session._main_wid = wid
        except (RuntimeError, ValueError):
            pass
        return self.context

    async def evaluate(self, expression: str, timeout: float = 30):
        context = await self.select_context()
        return await self.bidi.evaluate(context, expression, timeout=timeout)

    async def _native_click(self, params: dict) -> dict:
        """Send real pointer actions so pages receive trusted user input."""
        import math
        button = params.get("button", "left")
        count = params.get("count", 1)
        if not isinstance(button, str) or button not in {"left", "middle", "right"}:
            raise ValueError("button must be left, middle or right")
        if type(count) is not int or not 1 <= count <= 3:
            raise ValueError("count must be an integer from 1 to 3")
        target = params.get("target")
        context = self.context
        if target is not None:
            if not isinstance(target, str) or not target.strip():
                raise ValueError("target must be a non-empty CSS selector")
            if "x" in params or "y" in params:
                raise ValueError("Provide either target or x/y coordinates")
            position = await self.bidi.evaluate(context, """(() => {
                const element = document.querySelector(%s);
                if (!element) return {error: 'CSS target was not found'};
                element.scrollIntoView({block:'center', inline:'center', behavior:'instant'});
                const rect = element.getBoundingClientRect();
                if (rect.width <= 0 || rect.height <= 0) return {error:'CSS target has no rendered box'};
                return {x:rect.left + rect.width/2, y:rect.top + rect.height/2};
            })()""" % json.dumps(target), timeout=10)
            if not isinstance(position, dict) or position.get("error"):
                raise ValueError(position.get("error", "Cannot locate CSS target")
                                 if isinstance(position, dict) else "Cannot locate CSS target")
            x, y = position.get("x"), position.get("y")
        else:
            x, y = params.get("x"), params.get("y")
        for name, value in (("x", x), ("y", y)):
            if type(value) not in (int, float) or not 0 <= value <= 2147483647 or not math.isfinite(value):
                raise ValueError(f"{name} must be a finite, non-negative viewport coordinate")
        # BiDi pointer coordinates are integer CSS pixels, not device pixels.
        x, y = math.floor(x), math.floor(y)
        button_number = {"left": 0, "middle": 1, "right": 2}[button]
        actions = [{"type": "pointerMove", "origin": "viewport", "x": x, "y": y, "duration": 0}]
        for _ in range(count):
            actions.extend([{"type": "pointerDown", "button": button_number},
                            {"type": "pointerUp", "button": button_number}])
        primary_error = None
        try:
            await self.bidi.send("input.performActions", {
                "context": context, "actions": [{"type": "pointer", "id": "persona-native-mouse",
                    "parameters": {"pointerType": "mouse"}, "actions": actions}],
            })
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            try:
                await self.bidi.send("input.releaseActions", {"context": context})
            except Exception:
                if primary_error is None:
                    raise
                logger.warning("Could not release pointer actions after failed click", exc_info=True)
        return {"method": "bidi", "x": x, "y": y, "button": button, "count": count, "context": context}

    async def dispatch(self, action: str, params: dict):
        if self.state.get("mode") == "requalify" and action not in {"status", "probe", "shutdown"}:
            raise ValueError("Validation workers only accept status, probe and shutdown")
        if action == "status":
            return {"persona_id": self.persona.persona_id, "instance_id": self.instance_id,
                    "display": self.state["display"], "context": self.context,
                    "experimental": self.persona.experimental, "uptime": time.time() - self.started_at}
        if action == "diagnostics":
            return {"usage": process_usage(), "backend": self.manager.backend,
                    "graphics_policy": self.persona.final_config.get("graphics", {}).get("context_backend", "default"),
                    "firefox_log": list(self.pilot._session.firefox_log)[-100:]}
        if action == "shutdown":
            # Let the current response reach the socket before cleanup.
            asyncio.get_running_loop().call_later(0.1, self.stop_event.set)
            return {"stopping": True}
        await self.select_context(params.get("context"))
        if action == "click_native":
            return await self._native_click(params)
        if action == "goto":
            url = params.get("url", "")
            if not isinstance(url, str) or not url.startswith(("http://", "https://", "about:blank")):
                raise ValueError("URL must be http://, https:// or about:blank")
            return await self.bidi.navigate(self.context, url)
        if action == "eval":
            return {"result": await self.bidi.evaluate(self.context, params.get("expression", params.get("js", "null")))}
        if action == "reload":
            return await self.bidi.send("browsingContext.reload", {"context": self.context, "wait": "complete"})
        if action in {"tab_new", "window_new"}:
            created = await self.bidi.create("tab" if action == "tab_new" else "window",
                                             reference_context=self.context)
            await self.select_context(created["context"])
            if params.get("url"):
                await self.bidi.navigate(self.context, params["url"])
            return {**created, "persona_id": self.persona.persona_id}
        if action == "tab_list":
            return await self.bidi.get_tree()
        if action == "tab_switch":
            return {"context": await self.select_context(params["context"])}
        if action == "tab_close":
            closed = self.context
            await self.bidi.send("browsingContext.close", {"context": closed})
            return {"closed": closed}
        if action == "probe":
            check_process_budget(additional=5)
            # Diagnostic tabs are closed afterwards; the user's page survives.
            original = self.context
            created = await self.bidi.create("tab", reference_context=original)
            probe_context = created["context"]
            try:
                await self.select_context(probe_context)
                return await ProbeRunner(self.bidi, timeout=15).run(
                    probe_context, geolocation="geolocation" in self.persona.final_config,
                    font_config=self.persona.final_config.get("fonts"),
                    worker_graphics=bool(self.persona.final_config.get("worker_graphics")),
                    audio_config=self.persona.final_config.get("audio"),
                    media_config=self.persona.final_config.get("media"),
                    trusted_click=lambda target: self._native_click({"target": target}))
            finally:
                await self.bidi.send("browsingContext.close", {"context": probe_context})
                await self.select_context(original)
        if action in {"useragent_set", "useragent_clear", "geo_set", "geo_clear", "headers_set"}:
            raise ValueError("Persona identity is immutable; create a new Persona to change it")
        response = await self.legacy._dispatch({"id": 1, "action": action, "params": params})
        if not response.get("success"):
            raise RuntimeError(response.get("error", "Legacy command failed"))
        return response.get("data")

    async def handle_client(self, reader, writer):
        response = {"success": False}
        try:
            raw = await asyncio.wait_for(reader.readline(), 10)
            message = json.loads(raw)
            if not isinstance(message, dict) or not isinstance(message.get("token"), str) or not hmac.compare_digest(message["token"], self.state["token"]):
                raise ValueError("Invalid control token")
            params = message.get("params", {})
            if not isinstance(params, dict):
                raise ValueError("params must be an object")
            async with self.command_lock:
                data = await self.dispatch(message.get("action", ""), params)
            response = {"id": message.get("id"), "success": True, "data": data}
        except Exception as exc:
            response = {"success": False, "error": str(exc)}
        try:
            writer.write(json.dumps(response, ensure_ascii=False).encode() + b"\n")
            await writer.drain()
        except (OSError, ConnectionError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--persona-id", required=True)
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--backend", choices=["software", "native"], default="software")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    manager = PersonaManager(args.root, backend=args.backend)
    asyncio.run(PersonaRuntime(manager, args.persona_id, args.instance_id).run())


if __name__ == "__main__":
    main()
