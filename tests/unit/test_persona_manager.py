"""Persistent manager admission tests; subprocess creation is always mocked."""
from dataclasses import replace
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from src.persona import CapabilityEvidence, CapabilitySnapshot, NoEligiblePersonaError, QualificationError
from src.persona.manager import PersonaManager


class ManagerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        # Admission locks must not contend with real browser launches when
        # these isolated unit tests run beside local device acceptance.
        temporary = mock.patch("src.persona.manager.tempfile",
                               SimpleNamespace(gettempdir=lambda: self.directory.name))
        temporary.start()
        self.addCleanup(temporary.stop)
        self.manager = PersonaManager(self.directory.name)
        self.snapshot = CapabilitySnapshot(environment={
            "firefox_version": "154.0.1", "backend": "software", "host_arch": "aarch64"
        }, capabilities={})
        patcher = mock.patch.object(self.manager, "current_snapshot", return_value=self.snapshot)
        patcher.start()
        self.addCleanup(patcher.stop)
        spawn = mock.patch("src.persona.manager.subprocess.Popen")
        self.spawn = spawn.start()
        self.addCleanup(spawn.stop)

    def create(self):
        return self.manager.create(seed=9, experimental=True)

    def test_identical_seed_keeps_profiles_and_control_paths_independent(self):
        first, second = self.create(), self.create()
        self.assertEqual(first.final_config, second.final_config)
        self.assertNotEqual(first.persona_id, second.persona_id)
        first_paths = self.manager.paths(first.persona_id)
        second_paths = self.manager.paths(second.persona_id)
        for name in ("profile", "socket", "state", "worker_lock"):
            self.assertNotEqual(first_paths[name], second_paths[name])
        self.assertEqual(len(self.manager.store.list()), 2)
        self.spawn.assert_not_called()

    def test_strict_creation_without_qualification_persists_nothing(self):
        with self.assertRaises(NoEligiblePersonaError):
            self.manager.create(seed=9)
        self.assertEqual(self.manager.store.list(), [])
        self.spawn.assert_not_called()

    async def test_changed_environment_refuses_start_before_spawn(self):
        persona = self.create()
        changed = CapabilitySnapshot(environment={
            **self.snapshot.environment, "firefox_version": "155.0"
        }, capabilities={})
        with mock.patch.object(self.manager, "current_snapshot", return_value=changed):
            with self.assertRaisesRegex(QualificationError, "environment changed"):
                await self.manager.start(persona.persona_id)
        self.spawn.assert_not_called()

    async def test_failed_requalification_revokes_saved_strict_persona(self):
        experimental = self.create()
        template = self.manager.catalog().get(experimental.template_id)
        # Structural fixture; no claim of browser qualification in this test.
        evidence = [CapabilityEvidence(
            name, "supported", self.snapshot.fingerprint,
            observed={"measured": True}, requested={"config": experimental.final_config},
            contexts=("window",), proof=("unit-test fixture",),
        ) for name in template.required_capabilities]
        report = self.manager.catalog().qualify(
            template.template_id, experimental.final_config, self.snapshot, evidence)
        self.assertTrue(report.passed)
        self.manager.store.save_qualification(report)
        strict = self.manager.create(seed=9)
        self.assertFalse(strict.experimental)
        # Reaching admission proves valid saved/current reports are accepted.
        with mock.patch("src.persona.manager.check_process_budget",
                        side_effect=RuntimeError("admission reached")):
            with self.assertRaisesRegex(RuntimeError, "admission reached"):
                await self.manager.start(strict.persona_id)
        evidence[0] = replace(evidence[0], status="partial")
        failed = self.manager.catalog().qualify(
            template.template_id, experimental.final_config, self.snapshot, evidence)
        self.assertFalse(failed.passed)
        self.manager.store.save_qualification(failed)
        self.assertTrue(self.manager.store.load(strict.persona_id).qualification.passed)
        with self.assertRaisesRegex(QualificationError, "revoked"):
            await self.manager.start(strict.persona_id)
        self.spawn.assert_not_called()

    async def test_resource_budget_denial_does_not_spawn_worker(self):
        persona = self.create()
        with mock.patch("src.persona.manager.check_process_budget",
                        side_effect=RuntimeError("Android process budget exceeded")):
            with self.assertRaisesRegex(RuntimeError, "process budget"):
                await self.manager.start(persona.persona_id)
        self.spawn.assert_not_called()
        self.assertFalse(self.manager.paths(persona.persona_id)["state"].exists())

    async def test_stopping_never_started_persona_is_idempotent(self):
        persona = self.create()
        with mock.patch("src.persona.manager.request", new=mock.AsyncMock()) as request, \
                mock.patch("src.persona.manager.terminate_owned", new=mock.AsyncMock()) as terminate:
            first = await self.manager.stop(persona.persona_id)
            second = await self.manager.stop(persona.persona_id)
        self.assertEqual(first, second)
        self.assertEqual(first["state"], "stopped")
        self.assertFalse(first["alive"])
        request.assert_not_awaited()
        terminate.assert_not_awaited()
        self.spawn.assert_not_called()

    async def test_stale_worker_credentials_cannot_send_commands(self):
        persona = self.create()
        from src.persona.control import atomic_json
        atomic_json(self.manager.paths(persona.persona_id)["state"], {
            "status": "ready", "token": "secret", "worker": {
                "pid": 99999999, "start_ticks": "1", "pgid": 99999999}})
        with mock.patch("src.persona.manager.request", new=mock.AsyncMock()) as request:
            with self.assertRaisesRegex(RuntimeError, "not running"):
                await self.manager.command(persona.persona_id, "status")
        request.assert_not_awaited()
        self.assertNotIn("token", self.manager.status(persona.persona_id))
