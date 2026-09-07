"""Packaged media must match the finite probe manifest exactly."""

import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from src.persona.media import ASSET_ROOT, media_manifest


class MediaManifestTests(unittest.TestCase):
    def test_all_generated_fixtures_have_their_declared_content(self):
        self.assertEqual([f["id"] for f in media_manifest()["fixtures"]],
                         ["h264", "vp8", "vp9", "av1", "aac", "opus"])

    def test_changed_fixture_cannot_be_used_with_saved_manifest(self):
        with tempfile.TemporaryDirectory() as root:
            assets = Path(root) / "media"
            shutil.copytree(ASSET_ROOT, assets)
            (assets / "h264.mp4").write_bytes(b"invalid video")
            with patch("src.persona.media.ASSET_ROOT", assets), self.assertRaises(ValueError):
                media_manifest()

    def test_missing_or_non_mapping_manifest_is_rejected(self):
        manifest = media_manifest()
        with tempfile.TemporaryDirectory() as root:
            assets = Path(root)
            for value in ([], {**manifest, "fixtures": manifest["fixtures"][:-1]}):
                (assets / "manifest.json").write_text(json.dumps(value))
                with patch("src.persona.media.ASSET_ROOT", assets), self.assertRaises(ValueError):
                    media_manifest()

    def test_codec_metadata_cannot_claim_a_different_decoder(self):
        with tempfile.TemporaryDirectory() as root:
            assets = Path(root) / "media"
            shutil.copytree(ASSET_ROOT, assets)
            manifest = media_manifest()
            manifest["fixtures"][0]["content_type"] = 'video/mp4; codecs="av01.0.00M.08"'
            (assets / "manifest.json").write_text(json.dumps(manifest))
            with patch("src.persona.media.ASSET_ROOT", assets), self.assertRaises(ValueError):
                media_manifest()

    def test_frame_rate_must_be_the_fixed_integer_value(self):
        with tempfile.TemporaryDirectory() as root:
            assets = Path(root) / "media"
            shutil.copytree(ASSET_ROOT, assets)
            manifest = media_manifest()
            manifest["fixtures"][0]["framerate"] = 8.0
            (assets / "manifest.json").write_text(json.dumps(manifest))
            with patch("src.persona.media.ASSET_ROOT", assets), self.assertRaises(ValueError):
                media_manifest()
