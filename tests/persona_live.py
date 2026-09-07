"""Opt-in Termux GUI acceptance: python -m tests.persona_live.

Requires locally qualified templates. Creates two private test Personas;
results and screenshots stay in the private Persona root, outside the repo.
"""
import asyncio
import argparse
import json

from src.persona.control import atomic_json, process_usage
from src.persona.manager import PersonaManager


HTML = b'''<!doctype html><meta charset="utf-8"><title>Persona acceptance</title>
<input id="entry"><button id="button" onclick="this.dataset.clicked='yes'">Click</button>
<button id="popup" onclick="window.open(location.href,'persona-popup')">Popup</button>'''
IDENTITY = """({languages:navigator.languages,cpu:navigator.hardwareConcurrency,
tz:Intl.DateTimeFormat().resolvedOptions().timeZone,screen:[screen.width,screen.height],
appearance:{dark:matchMedia('(prefers-color-scheme: dark)').matches,
reducedMotion:matchMedia('(prefers-reduced-motion: reduce)').matches,
ordinaryContrast:matchMedia('(prefers-contrast: no-preference)').matches,
forcedColors:matchMedia('(forced-colors: active)').matches}})"""


async def main(template=None):
    manager = PersonaManager()
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
    a, b = manager.create(seed=1, template_id=template), manager.create(seed=2, template_id=template)
    ids = [a.persona_id, b.persona_id]
    results = {"personas": ids, "checks": [], "baseline": process_usage()}
    async def command(pid, action, **params):
        return await manager.command(pid, action, params)
    async def evaluate(pid, expression):
        return (await command(pid, "eval", expression=expression))["result"]
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
        await evaluate(ids[0], "(()=>{localStorage.setItem('owner','A');document.cookie='owner=A;max-age=3600';return true})()")
        # Native X11 input and legacy screenshot routing on a controlled page.
        await command(ids[0], "type", target="#entry", text="persona-A")
        await command(ids[0], "click", target="#button")
        assert await evaluate(ids[0], "document.querySelector('#entry').value") == "persona-A"
        assert await evaluate(ids[0], "document.querySelector('#button').dataset.clicked") == "yes"
        await command(ids[0], "screenshot")
        assert (manager.paths(ids[0])["directory"] / "screenshot.png").is_file()
        passed("native input and private screenshot")
        first = (await command(ids[0], "tab_list"))["contexts"][0]["context"]
        await command(ids[0], "tab_new", url=url)
        assert await evaluate(ids[0], IDENTITY) == identity
        assert await evaluate(ids[0], "localStorage.getItem('owner')") == "A"
        await command(ids[0], "tab_close")
        await command(ids[0], "tab_switch", context=first)
        await command(ids[0], "reload")
        assert await evaluate(ids[0], IDENTITY) == identity
        passed("tab inheritance and reload")
        await manager.start(ids[1])
        await command(ids[1], "goto", url=url)
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
        await manager.stop(ids[1])
        passed("stopping A leaves B intact")
        await manager.start(ids[0])
        await command(ids[0], "goto", url=url)
        assert manager.status(ids[0])["instance_id"] != old_instance
        assert await evaluate(ids[0], IDENTITY) == identity
        assert await evaluate(ids[0], "localStorage.getItem('owner')") == "A"
        assert "owner=A" in await evaluate(ids[0], "document.cookie")
        passed("restart preserves Persona and persistent storage")
        if "geolocation" in a.final_config:
            assert (await manager.qualify(ids[0])).passed
            assert await evaluate(ids[0], "navigator.permissions.query({name:'geolocation'}).then(p=>p.state)") == "prompt"
            passed("restart preserves geolocation and probe leaves site permission untouched")
        root_context = (await command(ids[0], "tab_list"))["contexts"][0]["context"]
        await new_context_after(ids[0], "click", target="#popup")
        assert await evaluate(ids[0], IDENTITY) == identity
        assert await evaluate(ids[0], "localStorage.getItem('owner')") == "A"
        await command(ids[0], "tab_close")
        await command(ids[0], "tab_switch", context=root_context)
        await new_context_after(ids[0], "press", key="ctrl+n")
        await command(ids[0], "goto", url=url)
        assert await evaluate(ids[0], IDENTITY) == identity
        await command(ids[0], "tab_close")
        await command(ids[0], "tab_switch", context=root_context)
        passed("trusted-click script popup and native Ctrl+N inherit Persona")
        root_context = (await command(ids[0], "tab_list"))["contexts"][0]["context"]
        await command(ids[0], "window_new", url=url)
        assert await evaluate(ids[0], IDENTITY) == identity
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
