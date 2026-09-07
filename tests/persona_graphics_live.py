"""Opt-in single-instance GL diagnostics; never grants template qualification.

Run: python -m tests.persona_graphics_live
Results/profiles remain under ~/.tbp/graphics-diagnostics, outside the repo.
"""
import asyncio
from contextlib import ExitStack
from pathlib import Path
import secrets

from src.persona.bidi import BiDiClient
from src.persona.control import atomic_json, check_process_budget, process_usage
from src.persona.manager import PersonaManager
from src.persona.probe import ProbeRunner
from src.persona.runtime import PersonaRuntime, firefox_settings
from src.pilot import Pilot


async def main():
    manager = PersonaManager()
    snapshot = manager.current_snapshot()
    config = manager.catalog().get("linux-firefox-native-phase0").expand(
        0, snapshot.environment["firefox_version"])
    base_prefs, env = firefox_settings(config)
    directory = Path.home() / ".tbp" / "graphics-diagnostics" / secrets.token_hex(8)
    directory.mkdir(mode=0o700, parents=True)
    results = {"environment": dict(snapshot.environment), "cases": [], "baseline": process_usage()}
    cases = [("baseline", {}), ("glx", {"gfx.x11-egl.force-disabled": True}),
             ("egl", {"gfx.x11-egl.force-enabled": True})]
    try:
        for name, prefs in cases:
            check_process_budget(additional=10)
            case_dir = directory / name
            case_dir.mkdir(mode=0o700)
            entry = {"name": name, "prefs": prefs}
            results["cases"].append(entry)
            client = None
            with ExitStack() as leases:
                display = await PersonaRuntime._display(leases)
                pilot = Pilot(browser="firefox", display=display,
                              window_size="1280,800", user_data_dir=str(case_dir / "profile"),
                              lock_path=str(case_dir / "browser.lock"),
                              firefox_launch_env=env, firefox_prefs={**base_prefs, **prefs},
                              remote_debugging_port=0, firefox_backend="software", profile_kind="persona")
                try:
                    await pilot.start()
                    client = await BiDiClient(ws_url=pilot._session.bidi_url).connect()
                    context = (await client.get_tree())["contexts"][0]["context"]
                    check_process_budget(additional=5)
                    probe = await ProbeRunner(client).run(context)
                    atomic_json(case_dir / "probe.json", probe)
                    entry["webgl"] = probe["observations"]["page"].get("webgl")
                    entry["firefox_log"] = list(pilot._session.firefox_log)[-100:]
                    webgl = entry["webgl"] or {}
                    print(f"{name}: supported={webgl.get('supported')}, "
                          f"renderer={webgl.get('unmaskedRenderer')}, "
                          f"error={webgl.get('contextCreationError')}", flush=True)
                except Exception as exc:
                    entry["error"] = f"{type(exc).__name__}: {exc}"
                    print(f"{name}: {entry['error']}", flush=True)
                finally:
                    if client:
                        await client.close()
                    await pilot.stop()
    finally:
        results["final_usage"] = process_usage()
        atomic_json(directory / "results.json", results)
        print(f"Diagnostic results: {directory / 'results.json'}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
