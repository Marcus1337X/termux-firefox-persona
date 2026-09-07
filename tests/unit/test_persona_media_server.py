"""HTTP delivery checks for the bundled codec qualification fixtures."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from src.persona import probe


class MediaServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        (self.directory / "sample.mp4").write_bytes(b"0123456789")
        (self.directory / "unlisted.mp4").write_bytes(b"private")
        self.write_manifest([{"filename": "sample.mp4", "mime": "video/mp4"}])
        self.asset_patch = patch.object(probe, "_MEDIA_DIRECTORY", self.directory)
        self.asset_patch.start()
        self.addCleanup(self.asset_patch.stop)

    def write_manifest(self, fixtures):
        (self.directory / "manifest.json").write_text(json.dumps({
            "schema_version": 1, "fixtures": fixtures,
        }))

    def request(self, server, path="sample.mp4", byte_range=None):
        headers = {"Range": byte_range} if byte_range is not None else {}
        return urlopen(Request(
            f"http://127.0.0.1:{server.port}/__tbp_media/{path}", headers=headers,
        ), timeout=2)

    def test_whitelisted_fixture_mime_body_and_resource_record(self):
        with probe.LoopbackProbeServer() as server:
            with self.request(server) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers["Content-Type"], "video/mp4")
                self.assertEqual(response.headers["Content-Length"], "10")
                self.assertEqual(response.headers["Accept-Ranges"], "bytes")
                self.assertEqual(response.read(), b"0123456789")
            snapshot = server.snapshot()
        self.assertEqual(snapshot["document"], {})
        self.assertEqual(snapshot["worker"], [])
        self.assertEqual(snapshot["requests"][0]["kind"], "resource")

    def test_single_ranges_support_seek_open_ends_and_suffixes(self):
        cases = [
            ("bytes=2-5", b"2345", "bytes 2-5/10"),
            ("bytes=7-", b"789", "bytes 7-9/10"),
            ("bytes=-3", b"789", "bytes 7-9/10"),
            ("bytes=9-99", b"9", "bytes 9-9/10"),
            ("bytes=-99", b"0123456789", "bytes 0-9/10"),
            ("bytes=0-0", b"0", "bytes 0-0/10"),
        ]
        with probe.LoopbackProbeServer() as server:
            for header, body, content_range in cases:
                with self.subTest(header=header), self.request(server, byte_range=header) as response:
                    self.assertEqual(response.status, 206)
                    self.assertEqual(response.headers["Content-Range"], content_range)
                    self.assertEqual(int(response.headers["Content-Length"]), len(body))
                    self.assertEqual(response.headers["Content-Type"], "video/mp4")
                    self.assertEqual(response.read(), body)

    def test_unsatisfiable_malformed_and_multiple_ranges_return_416(self):
        with probe.LoopbackProbeServer() as server:
            for header in ("bytes=10-", "bytes=5-2", "bytes=-0", "bytes=-", "bytes=0-1,3-4", "items=0-1"):
                with self.subTest(header=header), self.assertRaises(HTTPError) as caught:
                    self.request(server, byte_range=header)
                with caught.exception as response:
                    self.assertEqual(response.code, 416)
                    self.assertEqual(response.headers["Content-Range"], "bytes */10")
                    self.assertEqual(response.read(), b"")

    def test_only_exact_manifest_paths_are_readable(self):
        with probe.LoopbackProbeServer() as server:
            for path in ("unlisted.mp4", "manifest.json", "../sample.mp4", "%2e%2e/sample.mp4",
                         "%73ample.mp4", "nested/sample.mp4", "sample.mp4/extra"):
                with self.subTest(path=path), self.assertRaises(HTTPError) as caught:
                    self.request(server, path)
                with caught.exception as response:
                    self.assertEqual(response.code, 404)

    def test_manifest_cannot_expose_traversal_symlinks_or_header_injection(self):
        outside = self.directory / "outside"
        outside.mkdir()
        (outside / "secret.mp4").write_bytes(b"secret")
        (self.directory / "linked.mp4").symlink_to(outside / "secret.mp4")
        self.write_manifest([
            {"filename": "outside/secret.mp4", "mime": "video/mp4"},
            {"filename": "linked.mp4", "mime": "video/mp4"},
            {"filename": "sample.mp4", "mime": "video/mp4\r\nX-Injected: yes"},
        ])
        with probe.LoopbackProbeServer() as server:
            for path in ("outside/secret.mp4", "linked.mp4", "sample.mp4"):
                with self.subTest(path=path), self.assertRaises(HTTPError) as caught:
                    self.request(server, path)
                with caught.exception as response:
                    self.assertEqual(response.code, 404)

    def test_missing_or_malformed_manifest_preserves_document_and_workers(self):
        manifest = self.directory / "manifest.json"
        for content in (None, "invalid JSON", '{"schema_version":1,"fixtures":null}'):
            if content is None:
                manifest.unlink()
            else:
                manifest.write_text(content)
            with self.subTest(content=content), probe.LoopbackProbeServer() as server:
                with urlopen(server.url, timeout=2) as response:
                    self.assertEqual(response.status, 200)
                with urlopen(f"http://127.0.0.1:{server.port}/__tbp_dedicated_worker.js", timeout=2) as response:
                    self.assertEqual(response.status, 200)
                with self.assertRaises(HTTPError) as caught:
                    self.request(server)
                with caught.exception as response:
                    self.assertEqual(response.code, 404)


if __name__ == "__main__":
    unittest.main()
