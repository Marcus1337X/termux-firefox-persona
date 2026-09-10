import asyncio
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.persona.manager import PersonaManager

async def check_fonts():
    manager = PersonaManager()
    persona = manager.create(template_id="linux-firefox-software-glx-v1", experimental=True)
    pid = persona.persona_id
    print(f"[*] Created Persona {pid}", flush=True)

    try:
        await manager.start(pid, timeout=90)
        status = manager.status(pid)
        print(f"[+] Started on DISPLAY {status.get('display')}", flush=True)

        await manager.command(pid, "goto", {"url": "about:blank"})
        await asyncio.sleep(1)

        font_js = """(() => {
            const baseFonts = ['monospace', 'sans-serif', 'serif'];
            const testString = 'mmmmmmmmmmlli1234567890!@#$%^&*()_+';
            const testSize = '72px';
            const h = document.body;
            const s = document.createElement('span');
            s.style.fontSize = testSize;
            s.innerHTML = testString;
            const defaultWidth = {};
            const defaultHeight = {};
            for (const base of baseFonts) {
                s.style.fontFamily = base;
                h.appendChild(s);
                defaultWidth[base] = s.offsetWidth;
                defaultHeight[base] = s.offsetHeight;
                h.removeChild(s);
            }
            const check = (font) => {
                for (const base of baseFonts) {
                    s.style.fontFamily = `"${font}", ${base}`;
                    h.appendChild(s);
                    const matched = (s.offsetWidth !== defaultWidth[base] || s.offsetHeight !== defaultHeight[base]);
                    h.removeChild(s);
                    if (matched) return true;
                }
                return false;
            };
            const candidates = [
                'Liberation Sans', 'Liberation Serif', 'Liberation Mono',
                'DejaVu Sans', 'DejaVu Serif', 'DejaVu Sans Mono',
                'Ubuntu', 'Ubuntu Mono', 'Cantarell',
                'FreeSans', 'FreeSerif', 'FreeMono',
                'Carlito', 'Caladea',
                'WenQuanYi Micro Hei',
                'Roboto', 'Source Sans Pro', 'Noto Serif',
                'Droid Sans Mono', 'Cutive Mono',
                'Arial', 'Times New Roman', 'Courier New', 'Calibri'
            ];
            const detected = {};
            for (const c of candidates) {
                detected[c] = check(c);
            }
            return detected;
        })()"""
        res = await manager.command(pid, "eval", {"expression": font_js})
        print(f"[+] Detected fonts in Firefox:\n{json.dumps(res.get('result', {}), indent=2)}", flush=True)

    finally:
        await manager.stop(pid)
        print("[+] Done.", flush=True)

if __name__ == "__main__":
    asyncio.run(check_fonts())
