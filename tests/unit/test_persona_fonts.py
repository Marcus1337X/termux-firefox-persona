"""Fontconfig ctypes and private Persona font tree tests."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import src.persona.fonts as fonts
from src.persona.fonts import (
    FontDatabase,
    FontError,
    FontNotFoundError,
    build_persona_fontconfig,
)


class PersonaFontsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.database = FontDatabase()
            cls.dejavu = cls.database.match("DejaVu Sans")
        except Exception as exc:  # pragma: no cover - only for non-Termux hosts
            raise unittest.SkipTest(f"Fontconfig/DejaVu unavailable: {exc}")

    def test_ctypes_inventory_returns_real_source_file(self):
        self.assertEqual(self.dejavu.family, "DejaVu Sans")
        self.assertTrue(self.dejavu.file.is_file())
        self.assertTrue(self.dejavu.style)

    def test_unknown_family_does_not_accept_fontconfig_fallback(self):
        with self.assertRaises(FontNotFoundError):
            self.database.match("TBP family that cannot exist 8e7f")

    def test_collection_exposes_only_selected_family(self):
        try:
            self.database.match("Noto Sans CJK SC")
        except FontNotFoundError:
            self.skipTest("CJK collection unavailable")
        with tempfile.TemporaryDirectory() as directory:
            bundle = build_persona_fontconfig(directory, ["Noto Sans CJK SC"], aliases={},
                                               database=self.database)
            private = FontDatabase(config_file=bundle.config_file)
            try:
                self.assertEqual(set(private.families()), {"Noto Sans CJK SC"})
            finally:
                private.close()

    def test_private_tree_is_content_addressed_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            config = {
                "policy": "whitelist",
                "families": ["DejaVu Sans"],
                "aliases": {"sans-serif": "DejaVu Sans", "x&y": "DejaVu Sans"},
                "samples": {"DejaVu Sans": "Aa"},
                "blocked_families": [],
            }
            first = build_persona_fontconfig(directory, config, database=self.database)
            second = build_persona_fontconfig(directory, config, database=self.database)
            self.assertEqual(first.config_file, second.config_file)
            self.assertEqual(first.fonts_dir, second.fonts_dir)
            self.assertEqual(list(first.fonts_dir.iterdir()), list(second.fonts_dir.iterdir()))
            self.assertEqual(first.prefs, {})
            self.assertEqual(first.env["FONTCONFIG_FILE"], str(first.config_file))
            self.assertIn(b"x&amp;y", first.config_file.read_bytes())
            private_files = list(first.fonts_dir.iterdir())
            self.assertEqual(len(private_files), 1)
            self.assertTrue(private_files[0].is_symlink())
            self.assertEqual(private_files[0].resolve(), self.dejavu.file.resolve())

    def test_copy_mode_has_no_symlink_and_blocked_family_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = build_persona_fontconfig(
                directory,
                ["DejaVu Sans"],
                aliases={"sans-serif": "DejaVu Sans"},
                database=self.database,
                copy_fonts=True,
            )
            copied = next(bundle.fonts_dir.iterdir())
            self.assertFalse(copied.is_symlink())
            self.assertTrue(copied.is_file())
            with self.assertRaises(FontError):
                build_persona_fontconfig(
                    directory,
                    {
                        "families": ["DejaVu Sans"],
                        "blocked_families": ["DejaVu Sans"],
                    },
                    database=self.database,
                )

    def test_foreign_private_directory_and_symlink_cache_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "fontconfig").mkdir()
            with self.assertRaises(FontError):
                build_persona_fontconfig(
                    root, ["DejaVu Sans"], database=self.database
                )

        with tempfile.TemporaryDirectory() as directory:
            bundle = build_persona_fontconfig(
                directory, ["DejaVu Sans"], database=self.database
            )
            outside = Path(directory) / "outside-cache"
            outside.mkdir()
            bundle.cache_dir.rmdir()
            bundle.cache_dir.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(FontError):
                build_persona_fontconfig(
                    directory, ["DejaVu Sans"], database=self.database
                )

    def test_chmod_failure_is_not_silenced(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(fonts.os, "chmod", side_effect=OSError("denied")):
                with self.assertRaises(OSError):
                    build_persona_fontconfig(
                        directory, ["DejaVu Sans"], database=self.database
                    )


if __name__ == "__main__":
    unittest.main()
