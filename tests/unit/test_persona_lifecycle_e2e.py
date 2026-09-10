import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.persona.control import atomic_json
from src.persona.manager import PersonaManager
from src.persona.model import CapabilitySnapshot, TemplateCatalog


class PersonaLifecycleE2ETests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.catalog = TemplateCatalog.default()
        self.snapshot = CapabilitySnapshot.from_mapping({
            "environment": {"backend": "software", "firefox_version": "140.0"},
            "capabilities": {},
        })

        self.manager = PersonaManager(root=self.root, backend="software")
        self.manager.current_snapshot = mock.Mock(return_value=self.snapshot)
        self.manager.catalog = mock.Mock(return_value=self.catalog)

        # Create two personas: pA and pB
        self.pA = self.manager.create(template_id="linux-firefox-software-glx-v1", experimental=True)
        self.pB = self.manager.create(template_id="linux-firefox-privacy-storage-glx-v1", experimental=True)

    async def asyncTearDown(self):
        self.temp_dir.cleanup()

    async def test_end_to_end_multivariable_lifecycle(self):
        paths_a = self.manager.paths(self.pA.persona_id)
        paths_b = self.manager.paths(self.pB.persona_id)

        # 1. State setup: simulate running instances with distinct displays and workers
        atomic_json(paths_a["state"], {
            "status": "ready", "instance_id": "inst-a-1", "token": "tok-a",
            "display": ":211", "worker": {"pid": 3001, "start_ticks": "1", "pgid": 3001},
            "resources": [{"pid": 3002, "start_ticks": "1", "pgid": 3001}],
            "mode": "normal",
        })
        atomic_json(paths_b["state"], {
            "status": "ready", "instance_id": "inst-b-1", "token": "tok-b",
            "display": ":212", "worker": {"pid": 4001, "start_ticks": "2", "pgid": 4001},
            "resources": [{"pid": 4002, "start_ticks": "2", "pgid": 4001}],
            "mode": "normal",
        })

        alive_pids = {3001, 3002, 4001, 4002}
        async def fake_terminate(record, timeout=20):
            if record and "pid" in record:
                alive_pids.discard(record["pid"])

        def fake_is_owned(record):
            if not record or not isinstance(record, dict):
                return False
            return record.get("pid") in alive_pids

        # 3. Targeted command dispatch to Instance A
        async def fake_request(socket_path, token, action, params=None, timeout=60):
            if token == "tok-a":
                if action == "tab_new":
                    return {"success": True, "data": {"context": "ctx-a-tab2", "persona_id": self.pA.persona_id}}
                if action == "status":
                    return {"success": True, "data": {"state": "ready"}}
            if token == "tok-b":
                if action == "tab_new":
                    return {"success": True, "data": {"context": "ctx-b-tab2", "persona_id": self.pB.persona_id}}
            return {"success": True}

        with mock.patch("src.persona.manager.is_owned", side_effect=fake_is_owned), \
             mock.patch("src.persona.manager.terminate_owned", side_effect=fake_terminate), \
             mock.patch("src.persona.manager.request", side_effect=fake_request):

            # 2. Check independent status
            status_a = self.manager.status(self.pA.persona_id)
            status_b = self.manager.status(self.pB.persona_id)
            self.assertEqual(status_a["state"], "ready")
            self.assertEqual(status_b["state"], "ready")
            self.assertEqual(status_a["display"], ":211")
            self.assertEqual(status_b["display"], ":212")

            tab_res = await self.manager.command(self.pA.persona_id, "tab_new", {"url": "https://example.com"})
            self.assertEqual(tab_res["context"], "ctx-a-tab2")
            self.assertEqual(tab_res["persona_id"], self.pA.persona_id)

            # 4. Stop Instance A while Instance B keeps running
            stop_res = await self.manager.stop(self.pA.persona_id)
            self.assertEqual(stop_res["state"], "stopped")

            # Instance A pids terminated, Instance B pids alive
            self.assertNotIn(3001, alive_pids)
            self.assertNotIn(3002, alive_pids)
            self.assertIn(4001, alive_pids)
            self.assertIn(4002, alive_pids)

            # Instance B status still ready
            status_b_after = self.manager.status(self.pB.persona_id)
            self.assertEqual(status_b_after["state"], "ready")

            # 5. Stop Instance B as well
            stop_b_res = await self.manager.stop(self.pB.persona_id)
            self.assertEqual(stop_b_res["state"], "stopped")
            self.assertNotIn(4001, alive_pids)
            self.assertNotIn(4002, alive_pids)


if __name__ == "__main__":
    unittest.main()
