import asyncio
import json
import os
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.persona.manager import PersonaManager

ARTIFACTS_DIR = Path("/data/data/com.termux/files/home/.gemini/antigravity-cli/brain/6d4e1011-dcd7-42c7-8e56-aeaee28d7986")

async def run():
    manager = PersonaManager()
    persona = manager.create(template_id="linux-firefox-software-glx-v1", experimental=True)
    pid = persona.persona_id
    print(f"[*] Created Persona {pid}", flush=True)

    try:
        await manager.start(pid, timeout=90)
        status = manager.status(pid)
        print(f"[+] Started on DISPLAY {status.get('display')}", flush=True)

        # 1. 测试 Sannysoft Bot Detection (非常纯粹的硬性反爬测试)
        print("[*] Navigating to https://bot.sannysoft.com/ ...", flush=True)
        await manager.command(pid, "goto", {"url": "https://bot.sannysoft.com/"})
        await asyncio.sleep(8)

        sanny_js = """(() => {
            const results = {};
            document.querySelectorAll('table tr').forEach(tr => {
                const tds = tr.querySelectorAll('td, th');
                if (tds.length >= 2) {
                    const key = tds[0].innerText.trim();
                    const val = tds[1].innerText.trim();
                    const isPassed = !tr.classList.contains('failed') && !val.toLowerCase().includes('fail');
                    results[key] = { value: val, passed: isPassed };
                }
            });
            return {
                title: document.title,
                results
            };
        })()"""
        sanny_res = await manager.command(pid, "eval", {"expression": sanny_js})
        print(f"[+] Sannysoft Evaluation: {json.dumps(sanny_res, ensure_ascii=False, indent=2)}", flush=True)

        sanny_shot = ARTIFACTS_DIR / "sannysoft_result.png"
        await manager.command(pid, "screenshot", {"path": str(sanny_shot)})
        print(f"[+] Sannysoft screenshot saved: {sanny_shot}", flush=True)

        # 2. 测试 BrowserScan (权威综合评分)
        print("[*] Navigating to https://www.browserscan.net/ ...", flush=True)
        try:
            await manager.command(pid, "goto", {"url": "https://www.browserscan.net/"})
            print("[*] Waiting 15s for BrowserScan detection...", flush=True)
            await asyncio.sleep(15)

            bscan_js = """(() => {
                const body = document.body ? document.body.innerText : '';
                return {
                    title: document.title,
                    snippet: body.slice(0, 1500)
                };
            })()"""
            bscan_res = await manager.command(pid, "eval", {"expression": bscan_js})
            print(f"[+] BrowserScan Evaluation: {json.dumps(bscan_res, ensure_ascii=False, indent=2)}", flush=True)

            bscan_shot = ARTIFACTS_DIR / "browserscan_result.png"
            await manager.command(pid, "screenshot", {"path": str(bscan_shot)})
            print(f"[+] BrowserScan screenshot saved: {bscan_shot}", flush=True)
        except Exception as e:
            print(f"[-] BrowserScan error: {e}", flush=True)

    finally:
        await manager.stop(pid)
        print("[+] Persona stopped cleanly.", flush=True)

if __name__ == "__main__":
    asyncio.run(run())
