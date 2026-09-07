"""Font qualification invalidates on system fonts and configuration changes."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.persona.environment import font_environment


class FontEnvironmentTests(unittest.TestCase):
    def test_system_bundled_and_user_font_rules_are_tracked(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {}, clear=True):
            root = Path(root)
            prefix, home, system = root / "prefix", root / "home", root / "system"
            for directory in (prefix / "lib/firefox/fonts", prefix / "etc/fonts", home, system):
                directory.mkdir(parents=True)
            (prefix / "lib/firefox/fonts/emoji.ttf").write_bytes(b"bundled")
            (system / "cjk.ttc").write_bytes(b"system")
            config = prefix / "etc/fonts/fonts.conf"
            config.write_text("<fontconfig/>")
            def snapshot():
                return font_environment(prefix, prefix / "bin/firefox", home=home,
                                        system_roots=(system,))
            original = snapshot()
            self.assertEqual(len(original["files"]), 2)
            self.assertEqual(original, snapshot())
            config.write_text("<fontconfig><dir>/new/fonts</dir></fontconfig>")
            self.assertNotEqual(original, snapshot())
            changed_config = snapshot()
            (system / "cjk.ttc").write_bytes(b"new-system-font")
            self.assertNotEqual(changed_config, snapshot())


if __name__ == "__main__":
    unittest.main()
