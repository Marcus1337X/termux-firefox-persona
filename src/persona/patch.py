"""Utility to patch Firefox omni.ja, permanently disabling the internal RemoteAgent:Active broadcast flag.

When Firefox starts with remote debugging or BiDi, RemoteAgent broadcasts
RemoteAgent:Active=true across all content processes and Web Workers, forcing
navigator.webdriver=true. This patch forces the broadcast to false, making
navigator.webdriver evaluate to false naturally across all contexts and iframes
without any JS monkey-patching or prototype tampering.
"""

import logging
import os
import shutil
import tempfile
import zipfile

logger = logging.getLogger(__name__)

DEFAULT_OMNI_PATHS = [
    "/data/data/com.termux/files/usr/lib/firefox/omni.ja",
    "/usr/lib/firefox/omni.ja",
]


def patch_firefox_webdriver(omni_path: str | None = None) -> bool:
    if not omni_path:
        for path in DEFAULT_OMNI_PATHS:
            if os.path.exists(path):
                omni_path = path
                break

    if not omni_path or not os.path.exists(omni_path):
        logger.debug("Firefox omni.ja not found at default locations")
        return False

    target_entry = "chrome/remote/content/components/RemoteAgent.sys.mjs"
    target_str = "Services.ppmm.sharedData.set(SHARED_DATA_ACTIVE_KEY, value);"
    replace_str = "Services.ppmm.sharedData.set(SHARED_DATA_ACTIVE_KEY, false);"

    try:
        with zipfile.ZipFile(omni_path, "r") as zin:
            if target_entry not in zin.namelist():
                return False
            data = zin.read(target_entry).decode("utf-8")
            if replace_str in data:
                logger.info("Firefox omni.ja is already patched")
                return True
            if target_str not in data:
                logger.warning("Target string not found in RemoteAgent.sys.mjs")
                return False

        # Backup original
        bak_path = omni_path + ".bak"
        if not os.path.exists(bak_path):
            shutil.copyfile(omni_path, bak_path)

        tmp_dir = tempfile.mkdtemp()
        tmp_omni = os.path.join(tmp_dir, "omni.ja")

        with zipfile.ZipFile(omni_path, "r") as zin, zipfile.ZipFile(
            tmp_omni, "w", compression=zipfile.ZIP_DEFLATED
        ) as zout:
            for item in zin.infolist():
                content = zin.read(item.filename)
                if item.filename == target_entry:
                    text = content.decode("utf-8")
                    text = text.replace(target_str, replace_str)
                    content = text.encode("utf-8")
                zout.writestr(item, content)

        shutil.move(tmp_omni, omni_path)
        shutil.rmtree(tmp_dir, ignore_errors=True)

        # Clear startup cache
        cache_dir = os.path.expanduser("~/.cache/mozilla")
        if os.path.exists(cache_dir):
            shutil.rmtree(cache_dir, ignore_errors=True)

        logger.info("Successfully patched Firefox omni.ja")
        return True
    except Exception as exc:
        logger.error("Failed to patch Firefox omni.ja: %s", exc)
        return False


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    success = patch_firefox_webdriver()
    sys.exit(0 if success else 1)
