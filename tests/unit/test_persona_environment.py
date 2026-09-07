"""Font qualification invalidates on system fonts and configuration changes."""
import os
from pathlib import Path
import tempfile
import unittest
from subprocess import CompletedProcess
from unittest.mock import patch

from src.persona.environment import audio_environment, font_environment


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


class AudioEnvironmentTests(unittest.TestCase):
    def test_available_pactl_is_parsed_without_sensitive_fields(self):
        output = """Server Name: pulseaudio
Server Version: 17.0
Default Sample Specification: s16le 2ch 44100Hz
Default Sink: OpenSL_ES_sink
Default Source: OpenSL_ES_sink.monitor
Cookie: do-not-record
User Name: private-user
Host Name: private-host
Server String: /private/socket
"""
        with patch("src.persona.environment.shutil.which", return_value="/usr/bin/pactl") as which, \
             patch("src.persona.environment.subprocess.run", return_value=CompletedProcess(
                 ["pactl", "info"], 0, output, "Cookie: secret\n")) as run:
            result = audio_environment(environ={
                "PULSE_SERVER": "unix:/tmp/pulse/native",
                "PULSE_SINK": "OpenSL_ES_sink",
                "PULSE_COOKIE": "secret-cookie",
            })
        self.assertEqual(result["pactl"]["status"], "available")
        self.assertEqual(result["pactl"]["server_name"], "pulseaudio")
        self.assertNotIn("cookie", str(result).lower())
        self.assertNotIn("user_name", result["pactl"])
        self.assertNotIn("host_name", result["pactl"])
        self.assertNotIn("server_string", result["pactl"])
        self.assertNotIn("PULSE_COOKIE", result["environment"])
        which.assert_called_once_with("pactl")
        self.assertEqual(run.call_args.args[0], ["/usr/bin/pactl", "info"])
        self.assertEqual(run.call_args.kwargs["timeout"], 3)
        self.assertEqual(run.call_args.kwargs["env"]["LC_ALL"], "C")

    def test_missing_pactl_is_explicitly_unavailable(self):
        with patch("src.persona.environment.shutil.which", return_value=None):
            result = audio_environment(environ={"PULSE_SERVER": "unix:/tmp/pulse/native"})
        self.assertEqual(result["pactl"], {
            "status": "unavailable", "available": False, "error": "pactl_not_found",
        })

    def test_pactl_failure_is_classified_without_stderr(self):
        with patch("src.persona.environment.shutil.which", return_value="/usr/bin/pactl"), \
             patch("src.persona.environment.subprocess.run", return_value=CompletedProcess(
                 ["pactl", "info"], 1, "", "Cookie: secret; private details")):
            result = audio_environment()
        self.assertEqual(result["pactl"], {
            "status": "error", "available": False, "error": "pactl_exit_nonzero",
        })
        self.assertNotIn("secret", str(result))

    def test_pulse_configuration_hash_changes_with_file_contents(self):
        with tempfile.TemporaryDirectory() as root, \
             patch("src.persona.environment.shutil.which", return_value=None):
            root = Path(root)
            prefix, home = root / "prefix", root / "home"
            (prefix / "etc/pulse").mkdir(parents=True)
            (home / ".config/pulse").mkdir(parents=True)
            config = prefix / "etc/pulse/client.conf"
            config.write_text("autospawn = yes\n")
            first = audio_environment(prefix, home=home, environ={})
            config.write_text("autospawn = no\n")
            second = audio_environment(prefix, home=home, environ={})
        self.assertEqual(len(first["configuration"]), 1)
        self.assertNotEqual(first["configuration"], second["configuration"])


if __name__ == "__main__":
    unittest.main()
