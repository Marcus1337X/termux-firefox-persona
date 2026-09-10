import tempfile
import unittest
import zipfile
from pathlib import Path

from src.persona.patch import patch_firefox_webdriver


class PatchTests(unittest.TestCase):
    def test_patch_omni_ja_modifies_remote_agent(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            fake_omni = Path(tmp_dir) / "omni.ja"
            entry_name = "chrome/remote/content/components/RemoteAgent.sys.mjs"
            original_code = (
                "updateWebdriverActiveFlag(value) {\n"
                "    Services.ppmm.sharedData.set(SHARED_DATA_ACTIVE_KEY, value);\n"
                "    Services.ppmm.sharedData.flush();\n"
                "}"
            )
            with zipfile.ZipFile(fake_omni, "w") as zf:
                zf.writestr(entry_name, original_code.encode("utf-8"))

            res = patch_firefox_webdriver(str(fake_omni))
            self.assertTrue(res)

            # Verify content
            with zipfile.ZipFile(fake_omni, "r") as zf:
                patched_code = zf.read(entry_name).decode("utf-8")
            self.assertIn("Services.ppmm.sharedData.set(SHARED_DATA_ACTIVE_KEY, false);", patched_code)
            self.assertNotIn("Services.ppmm.sharedData.set(SHARED_DATA_ACTIVE_KEY, value);", patched_code)

            # Second run is idempotent
            res2 = patch_firefox_webdriver(str(fake_omni))
            self.assertTrue(res2)


if __name__ == "__main__":
    unittest.main()
