import asyncio
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.persona.manager import PersonaManager

ARTIFACTS_DIR = Path("/data/data/com.termux/files/home/.gemini/antigravity-cli/brain/6d4e1011-dcd7-42c7-8e56-aeaee28d7986")

async def test_incolumitas():
    manager = PersonaManager()
    persona = manager.create(template_id="linux-firefox-software-glx-v1", experimental=True)
    pid = persona.persona_id
    print(f"[*] Created Persona {pid}", flush=True)

    try:
        await manager.start(pid, timeout=90)
        status = manager.status(pid)
        print(f"[+] Started on DISPLAY {status.get('display')}", flush=True)

        url = "https://bot.incolumitas.com/"
        print(f"[*] Navigating to {url} ...", flush=True)
        await manager.command(pid, "goto", {"url": url})

        print("[*] Waiting 12 seconds for Incolumitas Bot analysis...", flush=True)
        await asyncio.sleep(12)

        eval_js = """(() => {
            const table = document.querySelector('table');
            const results = {};
            if (table) {
                table.querySelectorAll('tr').forEach(tr => {
                    const tds = tr.querySelectorAll('td, th');
                    if (tds.length >= 2) {
                        results[tds[0].innerText.trim()] = tds[1].innerText.trim();
                    }
                });
            }
            return {
                title: document.title,
                results: results,
                snippet: document.body ? document.body.innerText.slice(0, 1000) : ''
            };
        })()"""
        res = await manager.command(pid, "eval", {"expression": eval_js})
        print(f"[+] Incolumitas State: {json.dumps(res, ensure_ascii=False, indent=2)}", flush=True)

        shot_path = ARTIFACTS_DIR / "incolumitas_result.png"
        await manager.command(pid, "screenshot", {"path": str(shot_path)})
        print(f"[+] Screenshot saved to {shot_path}", flush=True)

    finally:
        await manager.stop(pid)
        print("[+] Done.", flush=True)

if __name__ == "__main__":
    asyncio.run(test_incolumitas())
