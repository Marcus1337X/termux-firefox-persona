"""Opt-in Termux GUI acceptance: python -m tests.persona_live.

Requires locally qualified templates. Creates two private test Personas;
results and screenshots stay in the private Persona root, outside the repo.
"""
import asyncio
import argparse
import json

from src.persona.control import atomic_json, check_process_budget, process_usage, read_json
from src.persona.manager import PersonaManager
from src.persona.model import PersonaGenerator, TemplateCatalog


HTML = '''<!doctype html><meta charset="utf-8"><title>Persona acceptance</title>
<input id="entry"><button id="button" onclick="this.dataset.clicked='yes';this.dataset.trusted=String(event.isTrusted)">Click</button>
<button id="popup" onclick="this.dataset.trusted=String(event.isTrusted);window.open(location.href,'persona-popup')">Popup</button>
<p style="font:32px serif">Hamburgefontsiv AVMW 0123456789 汉字中文测试天地玄黄 😀🌍🚀</p>'''.encode("utf-8")
IDENTITY = """({languages:navigator.languages,cpu:navigator.hardwareConcurrency,
tz:Intl.DateTimeFormat().resolvedOptions().timeZone,screen:[screen.width,screen.height],
appearance:{dark:matchMedia('(prefers-color-scheme: dark)').matches,
reducedMotion:matchMedia('(prefers-reduced-motion: reduce)').matches,
ordinaryContrast:matchMedia('(prefers-contrast: no-preference)').matches,
forcedColors:matchMedia('(forced-colors: active)').matches}})"""


FONT_TEMPLATE = "linux-firefox-fonts-glx-v1"
WORKER_TEMPLATE = "linux-firefox-workers-glx-v1"
AUDIO_TEMPLATE = "linux-firefox-audio-glx-v1"
MEDIA_TEMPLATE = "linux-firefox-media-glx-v1"
FONT_TEMPLATES = {FONT_TEMPLATE, WORKER_TEMPLATE, AUDIO_TEMPLATE, MEDIA_TEMPLATE}


def create_pair(manager, template):
    """Keep ordinary seeds; font acceptance requires two qualified font sets."""
    if template not in FONT_TEMPLATES:
        return manager.create(seed=1, template_id=template), manager.create(seed=2, template_id=template)
    snapshot = manager.current_snapshot()
    catalog = manager.catalog()
    selected = TemplateCatalog([catalog.get(template)])
    for report in catalog.reports():
        if report.template_id == template:
            selected.promote(report)
    generator = PersonaGenerator(selected, snapshot,
                                 runtime_browser_version=snapshot.environment["firefox_version"])
    chosen = []
    seen = set()
    # Search metadata only: do not create surplus saved Personas or processes.
    for seed in range(1, 65):
        candidate = generator.create(seed=seed)
        signature = tuple(sorted(candidate.final_config["fonts"]["families"]))
        if signature not in seen:
            seen.add(signature)
            chosen.append(seed)
        if len(chosen) == 2:
            break
    if len(chosen) != 2:
        raise AssertionError("Font acceptance requires two qualified, different font whitelists")
    return tuple(manager.create(seed=seed, template_id=template) for seed in chosen)


def font_window_expression(personas):
    """A lightweight same-sample Window check while two instances are running.

    Full probe creates Worker realms and is only run with one live Persona.
    This check needs no new tabs, worker processes or temporary font CSS.
    """
    media_fixtures = []
    if all("media" in persona.final_config for persona in personas):
        from src.persona.media import media_manifest
        media_fixtures = [{key: fixture[key] for key in ("id", "kind", "content_type")}
                          for fixture in media_manifest()["fixtures"]]
    names = {"__TBP_ACCEPTANCE_MISSING_FONT__"}
    local_names = {}
    for persona in personas:
        fonts = persona.final_config["fonts"]
        names.update(fonts["families"])
        names.update(fonts["blocked_families"])
        local_names.update(fonts.get("local_names", {}))
    return """(async () => {
      const names = __FAMILY_NAMES__;
      const localNames = __LOCAL_NAMES__;
      const local = {};
      for (let i = 0; i < names.length; i++) {
        try {
          const face = new FontFace('__tbpAcceptance' + i, 'local(' + JSON.stringify(localNames[names[i]] || names[i]) + ')');
          await face.load(); local[names[i]] = face.status === 'loaded';
        } catch (_) { local[names[i]] = false; }
      }
      await document.fonts.ready;
      const sample = 'Hamburgefontsiv AVMW 0123456789 汉字中文测试天地玄黄';
      const canvas = document.createElement('canvas'); canvas.width = 1024; canvas.height = 128;
      const ctx = canvas.getContext('2d');
      ctx.fillStyle = '#102030'; ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = '#f0d050'; ctx.font = '32px serif';
      const m = ctx.measureText(sample); ctx.fillText(sample, 8, 72);
      const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
      let hash = 2166136261, inkPixels = 0;
      for (let i = 0; i < pixels.length; i++) { hash ^= pixels[i]; hash = Math.imul(hash, 16777619); }
      for (let i = 0; i < pixels.length; i += 4) {
        if (pixels[i] !== 16 || pixels[i+1] !== 32 || pixels[i+2] !== 48 || pixels[i+3] !== 255) inkPixels++;
      }
      const audio = {};
      if (__CHECK_AUDIO__) {
        const context = new AudioContext();
        try { audio.sampleRate = context.sampleRate; }
        finally { await context.close(); }
        audio.closedState = context.state;
      }
      const mediaFixtures = __MEDIA_FIXTURES__;
      const media = {};
      for (const fixture of mediaFixtures) {
        const element = document.createElement(fixture.kind);
        media[fixture.id] = element.canPlayType(fixture.content_type);
      }
      return {...(mediaFixtures.length ? {media} : {}), ...(__CHECK_AUDIO__ ? {audio} : {}), local, serif: {sample, hash: (hash >>> 0).toString(16).padStart(8, '0'), inkPixels,
        metrics: {width:m.width, ascent:m.actualBoundingBoxAscent, descent:m.actualBoundingBoxDescent,
                  left:m.actualBoundingBoxLeft, right:m.actualBoundingBoxRight}}};
    })()""".replace("__FAMILY_NAMES__", json.dumps(sorted(names), ensure_ascii=False)).replace(
        "__LOCAL_NAMES__", json.dumps(local_names, ensure_ascii=False)).replace(
        "__CHECK_AUDIO__", json.dumps(all("audio" in persona.final_config for persona in personas))).replace(
        "__MEDIA_FIXTURES__", json.dumps(media_fixtures))


def check_font_window(persona, observed):
    allowed = set(persona.final_config["fonts"]["families"])
    assert observed["local"] == {name: name in allowed for name in observed["local"]}, \
        "Window local font visibility does not match its Persona whitelist"
    assert allowed.issubset(observed["local"])
    assert observed["serif"]["inkPixels"] > 0, "Font comparison canvas contains no text"
    assert observed["serif"]["metrics"]["width"] > 0
    if "audio" in persona.final_config:
        assert observed["audio"]["sampleRate"] == persona.final_config["audio"]["sample_rate"]
        assert observed["audio"]["closedState"] == "closed"
    if "media" in persona.final_config:
        assert set(observed["media"]) == set(persona.final_config["media"]["codecs"])
        assert all(value in ("maybe", "probably") for value in observed["media"].values())


def stable_font_evidence(fonts):
    """Keep actual pixels and TextMetrics, excluding diagnostic error wording."""
    def rendering(value):
        return {key: value[key] for key in ("hash", "metrics", "inkPixels", "repeatHash", "exportMatches")}
    return {
        "positive": {name: value["ok"] for name, value in fonts["positive"].items()},
        "negative": {name: value["failed"] for name, value in fonts["negative"].items()},
        "families": {name: {"sample": value["sample"], "direct": rendering(value["direct"]),
                             "local": rendering(value["local"])}
                     for name, value in fonts["families"].items()},
        "aliases": {name: {"target": value["target"], "generic": rendering(value["generic"]),
                            "targetLocal": rendering(value["targetLocal"])}
                    for name, value in fonts["aliases"].items()},
    }


def stable_worker_graphics(worker):
    """Compare actual Worker fonts and GL readback, not process diagnostics."""
    graphics = {}
    for api in ("webgl1", "webgl2"):
        value = worker["webgl"][api]
        behavior = value["behavior"]
        assert value["supported"] is True and behavior["passed"] is True
        graphics[api] = {
            key: value[key]
            for key in ("supported", "vendor", "renderer", "unmaskedVendor", "unmaskedRenderer")
        }
        graphics[api]["behavior"] = {
            "compile": behavior["compile"], "link": behavior["link"], "passed": behavior["passed"],
            "triangle": {key: behavior["triangle"][key] for key in (
                "compile", "link", "redPixels", "nonEmpty", "exactRed", "rgba", "readback")},
            "framebuffer": {key: behavior["framebuffer"][key] for key in (
                "rgba8", "complete", "readback", "exactGreen", "rgba")},
        }
    return {"fonts": stable_font_evidence(worker["fonts"]), **graphics}


def stable_audio_evidence(audio):
    """Compare rendered samples and natural states, excluding realtime timing/phase."""
    offline = audio["offline"]["actual"]
    realtime = audio["realtime"]
    return {
        "offline": {
            run: {key: offline[run][key] for key in (
                "context", "renderBuffer", "channels", "stateBeforeRender",
                "stateAfterRender", "stateAfterClose")}
            for run in ("first", "second")
        },
        "realtime": {
            **{key: realtime[key] for key in (
                "sampleRate", "initialState", "runningState", "suspendedState",
                "resumedState", "closedState", "maxChannelCount")},
            "analyser": {key: realtime["analyser"][key] for key in (
                "fftSize", "frequencyBinCount", "peakBin")},
        },
    }


def stable_media_evidence(media):
    """Keep decoded content and stable playback structure, excluding realtime phase/timing."""
    codecs = {}
    for name, observed in media["codecs"].items():
        value = {key: observed[key] for key in ("kind", "contentType", "sha256", "canPlayType", "ended")}
        value["decodingInfo"] = {"supported": observed["decodingInfo"]["supported"]}
        if observed["kind"] == "video":
            value.update({key: observed[key] for key in ("width", "height", "red", "green")})
        else:
            value["decoded"] = {key: observed["decoded"][key]
                                for key in ("sampleRate", "channels", "length", "signal")}
            value["playback"] = {key: observed["playback"][key]
                                 for key in ("sampleRate", "fftSize", "peakBin")}
        codecs[name] = value
    return {"codecs": codecs, **{key: media[key] for key in (
        "fixtureSet", "sampleRate", "runningState", "closedState", "trusted", "done")}}


async def main(template=None):
    manager = PersonaManager()
    a, b = create_pair(manager, template)
    font_mode = template in FONT_TEMPLATES
    worker_mode = template in {WORKER_TEMPLATE, AUDIO_TEMPLATE, MEDIA_TEMPLATE}
    audio_mode = template in {AUDIO_TEMPLATE, MEDIA_TEMPLATE}
    media_mode = template == MEDIA_TEMPLATE
    font_expression = font_window_expression((a, b)) if font_mode else None
    async def serve(reader, writer):
        try:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nConnection: close\r\nContent-Length: " + str(len(HTML)).encode() + b"\r\n\r\n" + HTML)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    url = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/"
    ids = [a.persona_id, b.persona_id]
    results = {"personas": ids, "checks": [], "baseline": process_usage()}
    if font_mode:
        results["fonts"] = {"seeds": [a.seed, b.seed],
                            "requested": {persona.persona_id: persona.final_config["fonts"] for persona in (a, b)}}
    async def command(pid, action, **params):
        return await manager.command(pid, action, params)
    async def evaluate(pid, expression):
        return (await command(pid, "eval", expression=expression))["result"]
    async def capture_font_probe(persona, label):
        assert (await manager.qualify(persona.persona_id)).passed
        report = read_json(manager.paths(persona.persona_id)["directory"] / "last-probe.json")
        fonts = report["observations"]["page"]["fonts"]
        results["fonts"][label] = fonts
        window_evidence = stable_font_evidence(fonts)
        if not worker_mode:
            return window_evidence
        workers = report["observations"]["page"]["workers"]
        # Full qualification has already checked every required context. Keep
        # the observed pixel/metric and identity values for restart comparison.
        worker_evidence = {
            context: stable_worker_graphics(workers[context])
            for context in persona.final_config["worker_graphics"]["contexts"]
        }
        results.setdefault("workers", {})[label] = worker_evidence
        evidence = {"window_fonts": window_evidence, "workers": worker_evidence}
        if audio_mode:
            audio = report["observations"]["page"]["audio_behavior"]
            results.setdefault("audio", {})[label] = audio
            evidence["audio"] = stable_audio_evidence(audio)
        if media_mode:
            media = report["observations"]["page"]["media_behavior"]
            results.setdefault("media", {})[label] = media
            evidence["media"] = stable_media_evidence(media)
        return evidence

    def passed(name):
        results["checks"].append(name)
        print(name, flush=True)
    async def new_context_after(pid, action, **params):
        old = {item["context"] for item in (await command(pid, "tab_list"))["contexts"]}
        await command(pid, action, **params)
        for _ in range(20):
            tree = await command(pid, "tab_list")
            created = [item["context"] for item in tree["contexts"] if item["context"] not in old]
            if created:
                await command(pid, "tab_switch", context=created[0])
                return created[0]
            await asyncio.sleep(0.25)
        raise AssertionError(f"{action} did not create a browser context")
    try:
        await manager.start(ids[0])
        await command(ids[0], "goto", url=url)
        identity = await evaluate(ids[0], IDENTITY)
        if font_mode:
            font_a = await evaluate(ids[0], font_expression)
            check_font_window(a, font_a)
            results["fonts"]["a_window_before"] = font_a
            font_a_probe = await capture_font_probe(a, "a_probe_before")
            passed("A font positive/negative, Canvas exports and TextMetrics")
            if worker_mode:
                passed("A Dedicated/Shared/Service Worker fonts and WebGL")
            if media_mode:
                passed("A six-codec native decode/playback and MediaCapabilities")
            # Firefox retains content processes after the full multi-realm
            # probe. Restart this same profile before the dual-instance test
            # so its probe-only processes do not consume the second slot.
            await manager.stop(ids[0])
            await manager.start(ids[0])
            await command(ids[0], "goto", url=url)
            assert await evaluate(ids[0], IDENTITY) == identity
            assert await evaluate(ids[0], font_expression) == font_a
        await evaluate(ids[0], "(()=>{localStorage.setItem('owner','A');document.cookie='owner=A;max-age=3600';return true})()")
        # Native X11 input and legacy screenshot routing on a controlled page.
        await command(ids[0], "type", target="#entry", text="persona-A")
        await command(ids[0], "click_native", target="#button")
        assert await evaluate(ids[0], "document.querySelector('#entry').value") == "persona-A"
        assert await evaluate(ids[0], "document.querySelector('#button').dataset.clicked") == "yes"
        assert await evaluate(ids[0], "document.querySelector('#button').dataset.trusted === 'true'") is True
        await command(ids[0], "screenshot")
        assert (manager.paths(ids[0])["directory"] / "screenshot.png").is_file()
        passed("native input and private screenshot")
        first = (await command(ids[0], "tab_list"))["contexts"][0]["context"]
        await command(ids[0], "tab_new", url=url)
        assert await evaluate(ids[0], IDENTITY) == identity
        assert await evaluate(ids[0], "localStorage.getItem('owner')") == "A"
        if font_mode:
            assert await evaluate(ids[0], font_expression) == font_a
        await command(ids[0], "tab_close")
        await command(ids[0], "tab_switch", context=first)
        await command(ids[0], "reload")
        assert await evaluate(ids[0], IDENTITY) == identity
        if font_mode:
            assert await evaluate(ids[0], font_expression) == font_a
        passed("tab inheritance and reload")
        # Probe Worker shutdown is asynchronous in Firefox. Wait briefly for
        # its processes to settle before the unchanged admission check.
        for attempt in range(30):
            try:
                check_process_budget(additional=10)
                break
            except RuntimeError:
                if attempt == 29:
                    raise
                await asyncio.sleep(0.5)
        await manager.start(ids[1])
        await command(ids[1], "goto", url=url)
        if font_mode:
            font_b = await evaluate(ids[1], font_expression)
            check_font_window(b, font_b)
            assert await evaluate(ids[0], font_expression) == font_a
            assert font_a["local"] != font_b["local"], "Font whitelists are not independently visible"
            assert font_a["serif"]["sample"] == font_b["serif"]["sample"]
            assert font_a["serif"]["hash"] != font_b["serif"]["hash"], "Different serif fonts rendered identical pixels"
            assert font_a["serif"]["metrics"] != font_b["serif"]["metrics"], "Different serif fonts have identical TextMetrics"
            if audio_mode:
                assert font_a["audio"]["sampleRate"] != font_b["audio"]["sampleRate"], \
                    "Default AudioContext sample rates are not independently visible"
                results.setdefault("audio", {})["dual_default_sample_rates"] = {
                    "a": font_a["audio"], "b": font_b["audio"]}
                passed("dual native AudioContext default sample rates differ")
            if media_mode:
                assert font_a["media"] == font_b["media"]
                results.setdefault("media", {})["dual_can_play_type"] = {
                    "a": font_a["media"], "b": font_b["media"]}
                passed("dual six-codec canPlayType remains available without decoding")
            results["fonts"]["b_window_dual"] = font_b
            passed("dual font whitelists, same-sample Canvas and TextMetrics differ")
        assert manager.status(ids[0])["display"] != manager.status(ids[1])["display"]
        assert await evaluate(ids[1], "localStorage.getItem('owner')") is None
        assert await evaluate(ids[1], "document.cookie") == ""
        await evaluate(ids[1], "(()=>{localStorage.setItem('owner','B');return true})()")
        await command(ids[1], "type", target="#entry", text="persona-B")
        assert await evaluate(ids[1], "document.querySelector('#entry').value") == "persona-B"
        assert await evaluate(ids[0], "document.querySelector('#entry').value") != "persona-B"
        assert await evaluate(ids[0], "localStorage.getItem('owner')") == "A"
        await command(ids[1], "screenshot")
        results["dual_usage"] = process_usage()
        passed("dual displays, navigation, input and storage isolation")
        old_instance = manager.status(ids[0])["instance_id"]
        await manager.stop(ids[0])
        assert manager.status(ids[1])["alive"]
        assert await evaluate(ids[1], "localStorage.getItem('owner')") == "B"
        if font_mode:
            assert await evaluate(ids[1], font_expression) == font_b
            await capture_font_probe(b, "b_probe_after_a_stop")
            passed("B font probe remains valid after A stops")
            if worker_mode:
                passed("B Worker fonts and WebGL remain valid after A stops")
            if media_mode:
                passed("B six-codec native decode/playback remains valid after A stops")
        await manager.stop(ids[1])
        passed("stopping A leaves B intact")
        await manager.start(ids[0])
        await command(ids[0], "goto", url=url)
        assert manager.status(ids[0])["instance_id"] != old_instance
        assert await evaluate(ids[0], IDENTITY) == identity
        assert await evaluate(ids[0], "localStorage.getItem('owner')") == "A"
        assert "owner=A" in await evaluate(ids[0], "document.cookie")
        passed("restart preserves Persona and persistent storage")
        if font_mode:
            restored_fonts = await evaluate(ids[0], font_expression)
            assert restored_fonts == font_a
            results["fonts"]["a_window_restarted"] = restored_fonts
            assert await capture_font_probe(a, "a_probe_restarted") == font_a_probe
            passed("restart preserves font visibility, Canvas pixels/exports and TextMetrics")
            if worker_mode:
                passed("restart preserves three Worker font and WebGL evidence")
            if media_mode:
                passed("restart preserves six-codec decoded pixels/audio and stable playback evidence")
        if "geolocation" in a.final_config:
            if not font_mode:
                assert (await manager.qualify(ids[0])).passed
            assert await evaluate(ids[0], "navigator.permissions.query({name:'geolocation'}).then(p=>p.state)") == "prompt"
            passed("restart preserves geolocation and probe leaves site permission untouched")
        root_context = (await command(ids[0], "tab_list"))["contexts"][0]["context"]
        await new_context_after(ids[0], "click_native", target="#popup")
        assert await evaluate(ids[0], IDENTITY) == identity
        if font_mode:
            assert await evaluate(ids[0], font_expression) == font_a
        assert await evaluate(ids[0], "localStorage.getItem('owner')") == "A"
        await command(ids[0], "tab_close")
        await command(ids[0], "tab_switch", context=root_context)
        assert await evaluate(ids[0], "document.querySelector('#popup').dataset.trusted === 'true'") is True
        await new_context_after(ids[0], "press", key="ctrl+n")
        await command(ids[0], "goto", url=url)
        assert await evaluate(ids[0], IDENTITY) == identity
        if font_mode:
            assert await evaluate(ids[0], font_expression) == font_a
        await command(ids[0], "tab_close")
        await command(ids[0], "tab_switch", context=root_context)
        passed("trusted-click script popup and native Ctrl+N inherit Persona")
        root_context = (await command(ids[0], "tab_list"))["contexts"][0]["context"]
        await command(ids[0], "window_new", url=url)
        assert await evaluate(ids[0], IDENTITY) == identity
        if font_mode:
            assert await evaluate(ids[0], font_expression) == font_a
        await command(ids[0], "tab_close", context=root_context)
        for _ in range(30):
            await asyncio.sleep(0.5)
            if not manager.status(ids[0])["alive"]:
                break
        assert not manager.status(ids[0])["alive"], "main window close must stop its derived windows"
        passed("derived window inheritance and main-window close")
        results["passed"] = True
    except BaseException as exc:
        results.update(passed=False, error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        for pid in ids:
            await manager.stop(pid)
        server.close()
        await server.wait_closed()
        results["final_usage"] = process_usage()
        atomic_json(manager.root / "live-acceptance.json", results)
        print(json.dumps(results, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", help="Use a specific locally qualified preset family")
    asyncio.run(main(parser.parse_args().template))
