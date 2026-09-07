"""Ownership and authentication checks without starting browser processes."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from src.persona import control
from src.persona.runtime import PersonaRuntime


class OwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_reused_pid_is_never_signalled(self):
        saved = {"pid": 4321, "start_ticks": "100", "pgid": 4321}
        reused = {**saved, "start_ticks": "200"}
        with mock.patch.object(control, "process_identity", return_value=reused), \
                mock.patch.object(control.os, "kill") as kill, \
                mock.patch.object(control.os, "killpg") as killpg:
            self.assertFalse(await control.terminate_owned(saved, timeout=0))
        kill.assert_not_called()
        killpg.assert_not_called()

    async def test_shared_process_group_only_signals_owned_pid(self):
        saved = {"pid": 4321, "start_ticks": "100", "pgid": 999}
        with mock.patch.object(control, "is_owned", side_effect=[True, True, False, False]), \
                mock.patch.object(control.os, "kill") as kill, \
                mock.patch.object(control.os, "killpg") as killpg:
            self.assertTrue(await control.terminate_owned(saved))
        kill.assert_called_once_with(4321, control.signal.SIGTERM)
        killpg.assert_not_called()

    async def test_pid_reuse_before_signal_is_rechecked(self):
        saved = {"pid": 4321, "start_ticks": "100", "pgid": 4321}
        with mock.patch.object(control, "is_owned", side_effect=[True, False, False, False]), \
                mock.patch.object(control.os, "kill") as kill, \
                mock.patch.object(control.os, "killpg") as killpg:
            await control.terminate_owned(saved)
        kill.assert_not_called()
        killpg.assert_not_called()


class PrivateControlTests(unittest.TestCase):
    def test_socket_namespace_private_and_identity_specific(self):
        with tempfile.TemporaryDirectory(prefix="") as td, \
                mock.patch.object(control.tempfile, "gettempdir", return_value=td):
            first = control.socket_path(Path(td) / "a", "one")
            self.assertEqual(first, control.socket_path(Path(td) / "a", "one"))
            self.assertNotEqual(first, control.socket_path(Path(td) / "a", "two"))
            self.assertNotEqual(first, control.socket_path(Path(td) / "b", "one"))
            self.assertEqual(first.parent.stat().st_mode & 0o777, 0o700)

    def test_symlink_namespace_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="") as td, \
                mock.patch.object(control.tempfile, "gettempdir", return_value=td):
            target = Path(td) / "other"
            target.mkdir()
            (Path(td) / "tbp-persona-sockets").symlink_to(target)
            with self.assertRaisesRegex(RuntimeError, "privately owned"):
                control.socket_path(Path(td), "one")

    def test_android_budget_refuses_overload(self):
        with mock.patch.object(control.sys, "executable", "/data/com.termux/bin/python"), \
                mock.patch.object(control, "process_usage", return_value={"uid_processes": 25}), \
                mock.patch.dict(os.environ, {"TBP_PERSONA_PROCESS_BUDGET": "30"}):
            with self.assertRaisesRegex(RuntimeError, "process budget"):
                control.check_process_budget(10)
            self.assertEqual(control.check_process_budget(5)["admission_budget"], 30)


class AuthenticationTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_tokens_cannot_dispatch_commands(self):
        runtime = PersonaRuntime.__new__(PersonaRuntime)
        runtime.state = {"token": "private-secret"}
        runtime.command_lock = asyncio.Lock()
        runtime.dispatch = mock.AsyncMock()
        for token in (None, 123, "wrong"):
            with self.subTest(token=token):
                reader = mock.Mock(readline=mock.AsyncMock(return_value=json.dumps(
                    {"token": token, "action": "shutdown"}).encode() + b"\n"))
                writer = mock.Mock(drain=mock.AsyncMock(), wait_closed=mock.AsyncMock())
                await runtime.handle_client(reader, writer)
                response = json.loads(writer.write.call_args.args[0])
                self.assertFalse(response["success"])
                self.assertIn("Invalid control token", response["error"])
                writer.close.assert_called_once()
        runtime.dispatch.assert_not_awaited()

    async def test_authenticated_request_dispatches_and_replies(self):
        runtime = PersonaRuntime.__new__(PersonaRuntime)
        runtime.state = {"token": "private-secret"}
        runtime.command_lock = asyncio.Lock()
        runtime.dispatch = mock.AsyncMock(return_value={"alive": True})
        reader = mock.Mock(readline=mock.AsyncMock(return_value=json.dumps(
            {"id": 7, "token": "private-secret", "action": "status", "params": {}}).encode()))
        writer = mock.Mock(drain=mock.AsyncMock(), wait_closed=mock.AsyncMock())
        await runtime.handle_client(reader, writer)
        runtime.dispatch.assert_awaited_once_with("status", {})
        self.assertEqual(json.loads(writer.write.call_args.args[0]),
                         {"id": 7, "success": True, "data": {"alive": True}})
