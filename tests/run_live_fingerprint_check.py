import asyncio
import json
import os
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.persona.manager import PersonaManager


ARTIFACTS_DIR = Path("/data/data/com.termux/files/home/.gemini/antigravity-cli/brain/6d4e1011-dcd7-42c7-8e56-aeaee28d7986")


async def run_check():
    manager = PersonaManager()
    # Create an experimental persona using software glx
    persona = manager.create(template_id="linux-firefox-software-glx-v1", experimental=True)
    pid = persona.persona_id
    print(f"[1/5] Created test Persona {pid}", flush=True)

    try:
        print("[2/5] Starting Persona instance (Xvfb + Firefox)...", flush=True)
        await manager.start(pid, timeout=90)
        status = manager.status(pid)
        print(f"[+] Persona running on DISPLAY {status.get('display')}, PID {status.get('pid')}", flush=True)

        # 1. Test IPHEY
        print("[3/5] Navigating to https://iphey.com/ ...", flush=True)
        await manager.command(pid, "goto", {"url": "https://iphey.com/"})
        print("[*] Waiting 12 seconds for IPHEY analysis to complete...", flush=True)
        await asyncio.sleep(12)

        iphey_js = """(() => {
            const body = document.body ? document.body.innerText : '';
            const trustworthy = body.includes('Trustworthy') || body.includes('You are trustworthy');
            const suspicious = body.includes('Suspicious');
            const sections = {};
            document.querySelectorAll('.check-item, .card, section, div[class*="status"]').forEach(el => {
                const text = el.innerText.trim();
                if (text && text.length < 150) {
                    sections[text.split('\\n')[0]] = text.replace(/\\n+/g, ' | ');
                }
            });
            return {
                title: document.title,
                trustworthy,
                suspicious,
                snippet: body.slice(0, 1000)
            };
        })()"""
        iphey_eval = await manager.command(pid, "eval", {"expression": iphey_js})
        print(f"[+] IPHEY Result: {json.dumps(iphey_eval.get('result'), ensure_ascii=False, indent=2)}", flush=True)

        # Capture screenshot for IPHEY
        try:
            iphey_shot = ARTIFACTS_DIR / "iphey_result.png"
            shot_res = await manager.command(pid, "screenshot", {"path": str(iphey_shot)})
            print(f"[+] IPHEY Screenshot saved to {iphey_shot}", flush=True)
        except Exception as e:
            print(f"[-] Screenshot warning: {e}", flush=True)

        # 2. Test CreepJS
        print("[4/5] Navigating to https://abrahamjuliot.github.io/creepjs/ ...", flush=True)
        await manager.command(pid, "goto", {"url": "https://abrahamjuliot.github.io/creepjs/"})
        print("[*] Waiting 15 seconds for CreepJS fingerprinter and lies analysis...", flush=True)
        await asyncio.sleep(15)

        creep_js = """(() => {
            const scoreEl = document.querySelector('.grade, .trust-score, #fingerprint-data');
            const liesEl = document.querySelectorAll('.lies, .untrustworthy, [data-lies]');
            const body = document.body ? document.body.innerText : '';
            return {
                title: document.title,
                scoreText: scoreEl ? scoreEl.innerText : null,
                bodySnippet: body.slice(0, 1500),
            };
        })()"""
        creep_eval = await manager.command(pid, "eval", {"expression": creep_js})
        print(f"[+] CreepJS Result: {json.dumps(creep_eval.get('result'), ensure_ascii=False, indent=2)}", flush=True)

        # Capture screenshot for CreepJS (both viewport and full-page stitched)
        try:
            creep_shot = ARTIFACTS_DIR / "creepjs_result.png"
            await manager.command(pid, "screenshot", {"path": str(creep_shot)})
            print(f"[+] CreepJS Viewport Screenshot saved to {creep_shot}", flush=True)

            # Capture full-page stitched screenshot
            metrics = await manager.command(pid, "eval", {"expression": """(() => {
                return {
                    scrollHeight: document.documentElement.scrollHeight,
                    innerHeight: window.innerHeight
                };
            })()"""})
            m_res = metrics.get("result", {})
            scroll_h = m_res.get("scrollHeight", 4000)
            inner_h = m_res.get("innerHeight", 659)

            slices = []
            curr_y = 0
            idx = 0
            while curr_y < scroll_h:
                await manager.command(pid, "eval", {"expression": f"window.scrollTo(0, {curr_y})"})
                await asyncio.sleep(0.3)
                s_path = ARTIFACTS_DIR / f"creepjs_slice_{idx}.png"
                await manager.command(pid, "screenshot", {"path": str(s_path)})
                slices.append(s_path)
                idx += 1
                if curr_y + inner_h >= scroll_h:
                    break
                curr_y += inner_h

            full_shot = ARTIFACTS_DIR / "creepjs_full_page.png"
            import subprocess
            subprocess.run(["convert"] + [str(p) for p in slices] + ["-append", str(full_shot)], check=True)
            print(f"[+] CreepJS Full-page Screenshot saved to {full_shot}", flush=True)
        except Exception as e:
            print(f"[-] Screenshot warning: {e}", flush=True)

    finally:
        print("[5/5] Stopping Persona instance...", flush=True)
        stop_res = await manager.stop(pid)
        print(f"[+] Persona stopped cleanly: state={stop_res.get('state')}", flush=True)


if __name__ == "__main__":
    asyncio.run(run_check())
