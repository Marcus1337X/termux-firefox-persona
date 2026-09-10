import asyncio
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.persona.manager import PersonaManager

ARTIFACTS_DIR = Path("/data/data/com.termux/files/home/.gemini/antigravity-cli/brain/6d4e1011-dcd7-42c7-8e56-aeaee28d7986")

async def test_cloudflare():
    manager = PersonaManager()
    persona = manager.create(template_id="linux-firefox-software-glx-v1", experimental=True)
    pid = persona.persona_id
    print(f"[*] Created Persona {pid}", flush=True)

    try:
        await manager.start(pid, timeout=90)
        status = manager.status(pid)
        print(f"[+] Started on DISPLAY {status.get('display')}", flush=True)

        # 访问 Cloudflare Turnstile 官方标准测试站点
        # 1. peet.ws Non-interactive 挑战 (非交互式静默挑战，全靠指纹与环境评估，自动通过)
        url = "https://peet.ws/turnstile-test/non-interactive.html"
        print(f"[*] Navigating to {url} ...", flush=True)
        await manager.command(pid, "goto", {"url": url})

        print("[*] Waiting 12 seconds for Cloudflare Turnstile non-interactive evaluation...", flush=True)
        await asyncio.sleep(12)

        # 获取页面 DOM 结果
        eval_js = """(() => {
            const bodyText = document.body ? document.body.innerText : '';
            const inputs = Array.from(document.querySelectorAll('input[name*="turnstile"], [name="cf-turnstile-response"]')).map(i => ({
                name: i.name,
                valueLength: i.value ? i.value.length : 0,
                hasToken: Boolean(i.value),
                tokenPrefix: i.value ? i.value.slice(0, 20) : ''
            }));
            const iframes = Array.from(document.querySelectorAll('iframe')).map(f => ({
                src: f.src,
                title: f.title
            }));
            return {
                title: document.title,
                turnstileInputs: inputs,
                iframeCount: iframes.length,
                snippet: bodyText.slice(0, 1000)
            };
        })()"""
        cf_res = await manager.command(pid, "eval", {"expression": eval_js})
        print(f"[+] Turnstile State: {json.dumps(cf_res, ensure_ascii=False, indent=2)}", flush=True)

        shot_path = ARTIFACTS_DIR / "cf_turnstile_non_interactive.png"
        await manager.command(pid, "screenshot", {"path": str(shot_path)})
        print(f"[+] Screenshot saved to {shot_path}", flush=True)

        # 2. 尝试测试另外一个 Cloudflare 保护的真实站点: https://nowsecure.nl
        url2 = "https://nowsecure.nl"
        print(f"[*] Navigating to {url2} (Cloudflare Under Attack / Bot Protection) ...", flush=True)
        await manager.command(pid, "goto", {"url": url2})
        print("[*] Waiting 12 seconds for NowSecure Cloudflare challenge...", flush=True)
        await asyncio.sleep(12)

        nowsecure_js = """(() => {
            return {
                title: document.title,
                h1: document.querySelector('h1') ? document.querySelector('h1').innerText : '',
                snippet: document.body ? document.body.innerText.slice(0, 500) : ''
            };
        })()"""
        nowsecure_res = await manager.command(pid, "eval", {"expression": nowsecure_js})
        print(f"[+] NowSecure State: {json.dumps(nowsecure_res, ensure_ascii=False, indent=2)}", flush=True)

        shot_path2 = ARTIFACTS_DIR / "cf_nowsecure_result.png"
        await manager.command(pid, "screenshot", {"path": str(shot_path2)})
        print(f"[+] Screenshot saved to {shot_path2}", flush=True)

    finally:
        await manager.stop(pid)
        print("[+] Persona stopped.", flush=True)

if __name__ == "__main__":
    asyncio.run(test_cloudflare())
