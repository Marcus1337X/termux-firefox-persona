"""Unit tests for the direct Firefox BiDi transport and loopback probe."""

from __future__ import annotations

import asyncio
import json
import threading
import unittest
from unittest.mock import patch
from urllib.request import urlopen

from src.persona import bidi
from src.persona.bidi import (
    BiDiClient,
    BiDiCommandError,
    BiDiTimeoutError,
    BiDiScriptError,
)
from src.persona.probe import PROBE_HTML, LoopbackProbeServer, ProbeRunner


class _FakeWebSocket:
    def __init__(self) -> None:
        self.messages: asyncio.Queue[str | None] = asyncio.Queue()
        self.closed = False
        self.sent: list[dict] = []
        self._drop = set()

    async def send(self, raw: str) -> None:
        payload = json.loads(raw)
        self.sent.append(payload)
        if payload["method"] in self._drop:
            return
        if payload["method"] == "session.new":
            result = {"sessionId": "session-1", "capabilities": {}}
        elif payload["method"] == "script.evaluate":
            if "throw" in payload["params"]["expression"]:
                result = {"type": "exception", "exceptionDetails": {"text": "boom"}}
            else:
                result = {"type": "success", "result": {"type": "string", "value": '{"ok":true}'}}
        else:
            result = {"ok": True}
        await self.messages.put(json.dumps({"type": "success", "id": payload["id"], "result": result}))

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        message = await self.messages.get()
        if message is None:
            raise StopAsyncIteration
        return message

    async def close(self) -> None:
        self.closed = True
        await self.messages.put(None)


class BiDiClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_session_evaluate_and_event_dispatch(self) -> None:
        fake = _FakeWebSocket()
        seen: list[dict] = []

        async def connect(_url: str, **_kwargs):
            return fake

        client = BiDiClient(1234)
        client.on("log.entryAdded", seen.append)
        with patch.object(bidi, "ws_connect", connect):
            await client.connect()
            await fake.messages.put(json.dumps({
                "type": "event", "method": "log.entryAdded", "params": {"text": "ok"}
            }))
            self.assertEqual(await client.evaluate("ctx", "({ok: true})"), {"ok": True})
            await asyncio.sleep(0)
            self.assertEqual(seen, [{"text": "ok"}])
            self.assertEqual(client.session_id, "session-1")
            await client.close()
        self.assertTrue(fake.closed)

    async def test_timeout_removes_pending_request(self) -> None:
        fake = _FakeWebSocket()
        fake._drop.add("browsingContext.getTree")

        async def connect(_url: str, **_kwargs):
            return fake

        with patch.object(bidi, "ws_connect", connect):
            client = await BiDiClient(1234, timeout=0.01).connect()
            with self.assertRaises(BiDiTimeoutError):
                await client.get_tree(timeout=0.01)
            self.assertEqual(client._pending, {})
            await client.close()

    async def test_command_error_is_typed(self) -> None:
        fake = _FakeWebSocket()

        async def connect(_url: str, **_kwargs):
            return fake

        original_send = fake.send

        async def rejecting_send(raw: str):
            payload = json.loads(raw)
            if payload["method"] == "browsingContext.navigate":
                await fake.messages.put(json.dumps({
                    "type": "error", "id": payload["id"],
                    "error": "invalid argument", "message": "bad url"
                }))
            else:
                await original_send(raw)

        fake.send = rejecting_send  # type: ignore[method-assign]
        with patch.object(bidi, "ws_connect", connect):
            client = await BiDiClient(1234).connect()
            with self.assertRaises(BiDiCommandError):
                await client.navigate("ctx", "bad")
            await client.close()

    async def test_javascript_exception_is_not_returned_as_a_value(self) -> None:
        fake = _FakeWebSocket()

        async def connect(_url: str, **_kwargs):
            return fake

        with patch.object(bidi, "ws_connect", connect):
            client = await BiDiClient(1234).connect()
            with self.assertRaises(BiDiScriptError):
                await client.evaluate("ctx", "(() => { throw Error('boom') })()")
            await client.close()


class ProbeTests(unittest.TestCase):
    def test_page_probe_has_graphics_diagnostic_and_cleanup(self) -> None:
        self.assertIn("webglcontextcreationerror", PROBE_HTML)
        self.assertIn("__tbpProbeCleanup", PROBE_HTML)
        self.assertIn("contrast", PROBE_HTML)

    def test_loopback_server_records_document_and_worker_headers(self) -> None:
        with LoopbackProbeServer() as server:
            with urlopen(server.url, timeout=2) as response:
                self.assertEqual(response.status, 200)
            with urlopen(f"http://127.0.0.1:{server.port}/__tbp_dedicated_worker.js", timeout=2) as response:
                self.assertEqual(response.status, 200)
            snapshot = server.snapshot()
        self.assertTrue(snapshot["document"])
        self.assertEqual(snapshot["worker"][0]["kind"], "worker")
        self.assertIn("User-Agent", snapshot["document"])

    def test_runner_returns_raw_observations_and_checks(self) -> None:
        class FakeClient:
            async def navigate(self, *_args, **_kwargs):
                return {"url": "ok"}

            async def evaluate(self, *_args, **_kwargs):
                return {
                    "window": {"userAgent": "ua", "platform": "Linux x86_64",
                                "oscpu": "Linux x86_64", "appVersion": "5.0 (X11)",
                                "hardwareConcurrency": 2, "timezone": "UTC",
                                "languages": ["en-US"]},
                    "display": {"devicePixelRatio": 1},
                    "webgl": {"supported": True},
                    "canvas": {"supported": True},
                    "audio": {"supported": True},
                    "workers": {
                        "dedicated": {"kind": "dedicated", "userAgent": "ua", "platform": "Linux x86_64", "hardwareConcurrency": 2, "timezone": "UTC", "languages": ["en-US"]},
                        "shared": {"kind": "shared", "userAgent": "ua", "platform": "Linux x86_64", "hardwareConcurrency": 2, "timezone": "UTC", "languages": ["en-US"]},
                        "service": {"kind": "service", "userAgent": "ua", "platform": "Linux x86_64", "hardwareConcurrency": 2, "timezone": "UTC", "languages": ["en-US"]},
                    },
                }

        result = asyncio.run(ProbeRunner(FakeClient(), timeout=0.2).run("ctx"))
        self.assertIn("raw", result)
        self.assertIn("checks", result)
        self.assertEqual(result["checks"]["window"]["status"], "pass")

    def test_optional_geolocation_grants_denies_and_restores_origin(self) -> None:
        class GeoClient:
            def __init__(self):
                self.states = iter(("prompt", "granted", "denied", "prompt"))
                self.positions = iter((
                    {"ok": True, "latitude": 31.2304, "longitude": 121.4737, "accuracy": 50},
                    {"ok": False, "errorCode": 1},
                ))
                self.commands = []

            async def evaluate(self, _context, expression, **_kwargs):
                if "permissions.query" in expression:
                    return next(self.states)
                if "getCurrentPosition" in expression:
                    return next(self.positions)
                return True

            async def send(self, method, params, **_kwargs):
                self.commands.append((method, params))
                return {}

        client = GeoClient()
        result = asyncio.run(ProbeRunner(client)._run_geolocation(
            "ctx", "http://127.0.0.1:34567", 0.2
        ))
        self.assertEqual(result["original_state"], "prompt")
        self.assertEqual(result["denied_position"]["errorCode"], 1)
        self.assertEqual(result["restored_state"], "prompt")
        self.assertEqual(len(client.commands), 3)
        self.assertEqual(client.commands[0][1]["origin"], "http://127.0.0.1:34567")
        self.assertEqual(client.commands[-1][1]["state"], "prompt")
        self.assertEqual(result["worker_contexts"]["service"], "notapplicable")


if __name__ == "__main__":
    unittest.main()
