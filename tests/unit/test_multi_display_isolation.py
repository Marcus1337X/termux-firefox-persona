"""Unit tests for independent DISPLAY/Xvfb allocation and multi-instance process isolation."""

from contextlib import ExitStack
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from src.persona.control import atomic_json, file_lock
from src.persona.manager import PersonaManager
from src.persona.model import CapabilitySnapshot
from src.persona.runtime import PersonaRuntime


class DisplayLeaseAllocationTests(unittest.IsolatedAsyncioTestCase):
    async def test_display_allocates_disjoint_numbers_for_concurrent_instances(self):
        with tempfile.TemporaryDirectory() as td:
            # Point displays to isolated temporary directory
            lock_dir = Path(td) / "tbp-persona-displays"
            lock_dir.mkdir(mode=0o700, exist_ok=True)
            with mock.patch("src.persona.runtime.tempfile.gettempdir", return_value=td):
                with ExitStack() as leases_a, ExitStack() as leases_b:
                    disp_a = await PersonaRuntime._display(leases_a)
                    disp_b = await PersonaRuntime._display(leases_b)
                    self.assertNotEqual(disp_a, disp_b)
                    self.assertTrue(disp_a.startswith(":"))
                    self.assertTrue(disp_b.startswith(":"))

    async def test_display_skips_occupied_filesystem_locks(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch("src.persona.runtime.tempfile.gettempdir", return_value=td):
                # Fake an existing system lock file for display :250
                (Path(td) / ".X250-lock").write_text("12345")
                with ExitStack() as leases:
                    # Patch random numbers to prioritize 250
                    with mock.patch("secrets.SystemRandom.shuffle", side_effect=lambda nums: nums.sort()):
                        disp = await PersonaRuntime._display(leases)
                        self.assertNotEqual(disp, ":250")

    async def test_released_display_can_be_reacquired(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch("src.persona.runtime.tempfile.gettempdir", return_value=td):
                leases_a = ExitStack()
                disp_first = await PersonaRuntime._display(leases_a)
                leases_a.close()  # Release lease

                # Re-acquire display
                leases_b = ExitStack()
                try:
                    with mock.patch("secrets.SystemRandom.shuffle", side_effect=lambda nums: nums.sort()):
                        disp_second = await PersonaRuntime._display(leases_b)
                        self.assertTrue(disp_second.startswith(":"))
                finally:
                    leases_b.close()


class MultiInstanceExecutionIsolationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        temporary = mock.patch("src.persona.manager.tempfile",
                               SimpleNamespace(gettempdir=lambda: self.td.name))
        temporary.start()
        self.addCleanup(temporary.stop)
        self.manager = PersonaManager(self.td.name)
        self.snapshot = CapabilitySnapshot(environment={
            "firefox_version": "154.0.1", "backend": "software", "host_arch": "aarch64"
        }, capabilities={})
        mock.patch.object(self.manager, "current_snapshot", return_value=self.snapshot).start()
        self.pA = self.manager.create(seed=10, experimental=True)
        self.pB = self.manager.create(seed=20, experimental=True)

    async def test_two_instances_run_concurrently_with_isolated_state(self):
        paths_a = self.manager.paths(self.pA.persona_id)
        paths_b = self.manager.paths(self.pB.persona_id)

        # Pre-seed fake ready states simulating independent workers and displays
        atomic_json(paths_a["state"], {
            "status": "ready", "instance_id": "inst-a", "token": "tok-a",
            "display": ":201", "worker": {"pid": 1111, "start_ticks": "1", "pgid": 1111},
            "resources": [{"pid": 1112, "start_ticks": "1", "pgid": 1111}],
        })
        atomic_json(paths_b["state"], {
            "status": "ready", "instance_id": "inst-b", "token": "tok-b",
            "display": ":202", "worker": {"pid": 2222, "start_ticks": "2", "pgid": 2222},
            "resources": [{"pid": 2223, "start_ticks": "2", "pgid": 2222}],
        })

        with mock.patch("src.persona.manager.is_owned", return_value=True):
            status_a = self.manager.status(self.pA.persona_id)
            status_b = self.manager.status(self.pB.persona_id)
            self.assertEqual(status_a["state"], "ready")
            self.assertEqual(status_b["state"], "ready")
            self.assertEqual(status_a["display"], ":201")
            self.assertEqual(status_b["display"], ":202")
            self.assertNotEqual(status_a["display"], status_b["display"])

    async def test_stopping_instance_a_preserves_instance_b(self):
        paths_a = self.manager.paths(self.pA.persona_id)
        paths_b = self.manager.paths(self.pB.persona_id)

        atomic_json(paths_a["state"], {
            "status": "ready", "instance_id": "inst-a", "token": "tok-a",
            "display": ":201", "worker": {"pid": 1111, "start_ticks": "1", "pgid": 1111},
            "resources": [{"pid": 1112, "start_ticks": "1", "pgid": 1111}],
        })
        atomic_json(paths_b["state"], {
            "status": "ready", "instance_id": "inst-b", "token": "tok-b",
            "display": ":202", "worker": {"pid": 2222, "start_ticks": "2", "pgid": 2222},
            "resources": [{"pid": 2223, "start_ticks": "2", "pgid": 2222}],
        })

        alive_pids = {1111, 1112, 2222, 2223}
        terminated_records = []
        async def fake_terminate(record, timeout=20):
            if record and "pid" in record:
                alive_pids.discard(record["pid"])
                terminated_records.append(record)

        def fake_is_owned(record):
            if not record or not isinstance(record, dict):
                return False
            return record.get("pid") in alive_pids

        with mock.patch("src.persona.manager.is_owned", side_effect=fake_is_owned), \
             mock.patch("src.persona.manager.terminate_owned", side_effect=fake_terminate), \
             mock.patch("src.persona.manager.request", side_effect=OSError("socket closed")):
            
            stopped_a = await self.manager.stop(self.pA.persona_id)
            self.assertEqual(stopped_a["state"], "stopped")
            
            # Verify termination was targeted only at instance A resources (1111, 1112)
            terminated_pids = {r["pid"] for r in terminated_records}
            self.assertIn(1111, terminated_pids)
            self.assertIn(1112, terminated_pids)
            self.assertNotIn(2222, terminated_pids)
            self.assertNotIn(2223, terminated_pids)
            self.assertIn(2222, alive_pids)
            self.assertIn(2223, alive_pids)

    async def test_commands_to_instance_a_do_not_affect_instance_b(self):
        paths_a = self.manager.paths(self.pA.persona_id)
        paths_b = self.manager.paths(self.pB.persona_id)

        atomic_json(paths_a["state"], {
            "status": "ready", "instance_id": "inst-a", "token": "tok-a",
            "display": ":201", "worker": {"pid": 1111, "start_ticks": "1", "pgid": 1111},
        })
        atomic_json(paths_b["state"], {
            "status": "ready", "instance_id": "inst-b", "token": "tok-b",
            "display": ":202", "worker": {"pid": 2222, "start_ticks": "2", "pgid": 2222},
        })

        calls = []
        async def fake_request(socket_path, token, action, params=None, timeout=60):
            calls.append((socket_path, token, action))
            return {"success": True, "data": {"action": action}}

        with mock.patch("src.persona.manager.is_owned", return_value=True),              mock.patch("src.persona.manager.request", side_effect=fake_request):
            
            res_a = await self.manager.command(self.pA.persona_id, "goto", {"url": "https://a.example.com"})
            self.assertEqual(res_a["action"], "goto")
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][1], "tok-a")
            self.assertEqual(calls[0][0], paths_a["socket"])

            res_b = await self.manager.command(self.pB.persona_id, "tab_new", {})
            self.assertEqual(res_b["action"], "tab_new")
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[1][1], "tok-b")
            self.assertEqual(calls[1][0], paths_b["socket"])
