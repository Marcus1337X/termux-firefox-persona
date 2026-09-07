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


class RequalificationTests(unittest.IsolatedAsyncioTestCase):
    """Mock process transport, but use real qualification and persistence."""

    def setUp(self):
        import asyncio
        from src.persona import Persona, qualify_probe
        from src.persona.control import atomic_json
        self.atomic_json = atomic_json
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        patcher = mock.patch("src.persona.manager.tempfile",
                             SimpleNamespace(gettempdir=lambda: self.directory.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.manager = PersonaManager(self.directory.name)
        self.old_snapshot = CapabilitySnapshot(environment={
            "firefox_version": "154.0.1", "backend": "software", "font_revision": "old"
        }, capabilities={})
        self.new_snapshot = CapabilitySnapshot(environment={
            **self.old_snapshot.environment, "font_revision": "new"
        }, capabilities={})
        patcher = mock.patch.object(self.manager, "current_snapshot", return_value=self.new_snapshot)
        self.snapshot_mock = patcher.start()
        self.addCleanup(patcher.stop)
        self.template = self.manager.catalog().get("linux-firefox-native-phase0")
        config = self.template.expand(0, "154.0.1")
        draft = Persona.build(seed=42, template=self.template, final_config=config,
                              snapshot=self.old_snapshot, experimental=True)
        self.observations = self.probe_data(config)
        report = qualify_probe(draft, self.old_snapshot, self.observations)
        self.assertTrue(report.passed)
        self.original = replace(draft, experimental=False, qualification=report)
        self.manager.store.save(self.original)
        self.manager.store.save_qualification(report)
        self.paths = self.manager.paths(self.original.persona_id)
        self.paths["profile"].mkdir()
        self.profile_file = self.paths["profile"] / "keep-private-data"
        self.profile_file.write_bytes(b"profile must remain in place")
        self.probe_started = asyncio.Event()
        self.release_probe = asyncio.Event()
        self.pause_probe = False
        self.old_bytes = (self.manager.store.personas_dir / (self.original.persona_id + ".json")).read_bytes()

        async def launch(persona_id, paths, **kwargs):
            self.assert_original()
            self.assertEqual(persona_id, self.original.persona_id)
            self.assertEqual(paths["profile"], self.paths["profile"])
            self.atomic_json(paths["state"], {
                "instance_id": kwargs["instance_id"], "mode": kwargs["mode"],
                "status": "ready", "token": "unit-only", "resources": [],
            })
            candidate = self.manager.load_worker_persona(persona_id, kwargs["instance_id"])
            self.assertTrue(candidate.experimental)
            self.assertIsNone(candidate.qualification)
            self.assertEqual(candidate.final_config, self.original.final_config)
            self.assertEqual(candidate.capability_snapshot_fingerprint, self.new_snapshot.fingerprint)

        async def stop(paths, **kwargs):
            from src.persona.control import read_json
            self.assert_original()
            state = read_json(paths["state"]) or {}
            if state:
                self.atomic_json(paths["state"], {**state, "status": "stopped"})

        async def request(path, token, action, *args, **kwargs):
            self.assert_original()
            if action == "shutdown":
                self.release_probe.set()
                return {"success": True, "data": {"stopping": True}}
            self.assertEqual(action, "probe")
            self.probe_started.set()
            if self.pause_probe:
                await self.release_probe.wait()
            return {"success": True, "data": self.observations}

        for target, name, side_effect in ((self.manager, "_launch_worker", launch),
                                         (self.manager, "_stop_worker", stop)):
            patcher = mock.patch.object(target, name, new=mock.AsyncMock(side_effect=side_effect))
            setattr(self, name + "_mock", patcher.start())
            self.addCleanup(patcher.stop)
        patcher = mock.patch("src.persona.manager.request", new=mock.AsyncMock(side_effect=request))
        self.request_mock = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch("src.persona.manager.subprocess.Popen")
        self.spawn = patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def probe_data(config):
        import copy
        browser, locale, display = (config[name] for name in ("browser", "locale", "display"))
        realm = {"languages": locale["languages"], "timezone": locale["timezone"],
                 "hardwareConcurrency": config["cpu"]["hardware_concurrency"]}
        headers = {"User-Agent": browser["user_agent"], "Accept-Language": locale["accept_language"]}
        return {"observations": {"page": {
            "window": {**realm, "userAgent": browser["user_agent"], "platform": browser["platform"],
                       "oscpu": browser["oscpu"], "appVersion": browser["app_version"]},
            "workers": {name: copy.deepcopy(realm) for name in ("dedicated", "shared", "service")},
            "display": {
                "screen": {"width": display["screen_width"], "height": display["screen_height"],
                           "availWidth": display["avail_width"], "availHeight": display["avail_height"],
                           "colorDepth": display["color_depth"], "pixelDepth": display["pixel_depth"]},
                "viewport": {"width": 1200, "height": 700, "outerWidth": 1280, "outerHeight": 800},
                "devicePixelRatio": display["device_pixel_ratio"], "screenX": 0, "screenY": 0,
                "orientation": {"type": "landscape-primary", "angle": 0},
            }}, "http": {"document": headers, "worker": [
                {"worker_type": name, "headers": headers.copy()}
                for name in ("dedicated", "shared", "service")]}}}

    def assert_original(self):
        self.assertEqual(self.manager.store.load(self.original.persona_id).to_dict(), self.original.to_dict())
        self.assertEqual(self.profile_file.read_bytes(), b"profile must remain in place")

    def assert_no_candidate(self):
        self.assertEqual(list(self.paths["directory"].glob("requalify-*.json")), [])
        self.assertEqual(list(self.paths["directory"].glob("cancel-requalify-*.json")), [])
        self.spawn.assert_not_called()

    async def test_success_rebinds_only_snapshot_and_qualification_after_shutdown(self):
        report = await self.manager.requalify(self.original.persona_id)
        self.assertTrue(report.passed)
        updated = self.manager.store.load(self.original.persona_id)
        expected = replace(self.original, capability_snapshot_fingerprint=self.new_snapshot.fingerprint,
                           qualification=report)
        self.assertEqual(updated.to_dict(), expected.to_dict())
        self.assertEqual(self.profile_file.read_bytes(), b"profile must remain in place")
        self._stop_worker_mock.assert_awaited_once()
        self.assert_no_candidate()
        # Passing the real start admission proves the saved strict identity
        # and promoted new report are mutually valid, without starting Firefox.
        with mock.patch.object(self.manager, "_launch_worker", new=mock.AsyncMock()) as launch:
            await self.manager.start(self.original.persona_id)
            launch.assert_awaited_once()

    async def test_failed_probe_preserves_original_report_and_metadata_bytes(self):
        self.observations["observations"]["page"]["workers"].pop("shared")
        with self.assertRaisesRegex(QualificationError, "requalification failed"):
            await self.manager.requalify(self.original.persona_id)
        self.assert_original()
        saved_path = self.manager.store.personas_dir / (self.original.persona_id + ".json")
        self.assertEqual(saved_path.read_bytes(), self.old_bytes)
        self.assertEqual(len(self.manager.store.list_qualifications()), 1)
        with self.assertRaisesRegex(QualificationError, "environment changed"):
            await self.manager.start(self.original.persona_id)
        self.assert_no_candidate()

    async def test_stop_during_final_environment_check_prevents_commit(self):
        from src.persona.control import read_json
        def snapshot():
            state = read_json(self.paths["state"]) or {}
            if state.get("status") == "stopped" and state.get("instance_id"):
                self.atomic_json(self.manager._validation_path(
                    self.paths, state["instance_id"], cancel=True), {"cancelled": True})
            return self.new_snapshot
        self.snapshot_mock.side_effect = snapshot
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            await self.manager.requalify(self.original.persona_id)
        self.assert_original()
        self.assert_no_candidate()

    async def test_task_cancellation_cleans_worker_and_leaves_old_identity(self):
        import asyncio
        self.pause_probe = True
        task = asyncio.create_task(self.manager.requalify(self.original.persona_id))
        await self.probe_started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assert_original()
        self._stop_worker_mock.assert_awaited_once()
        self.assert_no_candidate()

    async def test_concurrent_requalification_is_rejected_without_overwriting_candidate(self):
        import asyncio
        self.pause_probe = True
        task = asyncio.create_task(self.manager.requalify(self.original.persona_id))
        await self.probe_started.wait()
        candidates = list(self.paths["directory"].glob("requalify-*.json"))
        with self.assertRaises(BlockingIOError):
            await self.manager.requalify(self.original.persona_id)
        self.assertEqual(list(self.paths["directory"].glob("requalify-*.json")), candidates)
        self.assert_original()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assert_no_candidate()

    async def test_stop_cancels_inflight_validation_before_commit(self):
        import asyncio
        self.pause_probe = True
        task = asyncio.create_task(self.manager.requalify(self.original.persona_id))
        await self.probe_started.wait()
        # A worker identity is only needed to request immediate shutdown. The
        # fake process transport releases the pending probe on that request.
        with mock.patch("src.persona.manager.is_owned", return_value=True):
            stopping = asyncio.create_task(self.manager.stop(self.original.persona_id))
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                await task
            await stopping
        self.assert_original()
        self.assert_no_candidate()

    async def test_running_persona_cannot_be_requalified(self):
        self.atomic_json(self.paths["state"], {"status": "ready", "worker": {"pid": 111}})
        with mock.patch("src.persona.manager.is_owned", side_effect=lambda record: bool(record)):
            with self.assertRaisesRegex(RuntimeError, "Stop the running"):
                await self.manager.requalify(self.original.persona_id)
        self._launch_worker_mock.assert_not_awaited()
        self.assert_original()
        self.assert_no_candidate()

    async def test_firefox_version_change_requires_new_persona(self):
        self.snapshot_mock.return_value = CapabilitySnapshot(environment={
            **self.new_snapshot.environment, "firefox_version": "155.0"
        }, capabilities={})
        with self.assertRaisesRegex(QualificationError, "create a new Persona"):
            await self.manager.requalify(self.original.persona_id)
        self._launch_worker_mock.assert_not_awaited()
        self.assert_original()
        self.assert_no_candidate()

    async def test_environment_drift_during_probe_cannot_commit(self):
        async def drift(*args, **kwargs):
            self.snapshot_mock.return_value = CapabilitySnapshot(environment={
                **self.new_snapshot.environment, "font_revision": "changed-again"
            }, capabilities={})
            return {"success": True, "data": self.observations}
        self.request_mock.side_effect = drift
        with self.assertRaisesRegex(QualificationError, "Environment changed during"):
            await self.manager.requalify(self.original.persona_id)
        self.assert_original()
        self.assert_no_candidate()

    async def test_validation_mode_rejects_public_browser_commands(self):
        self.atomic_json(self.paths["state"], {"mode": "requalify", "status": "ready"})
        with self.assertRaisesRegex(RuntimeError, "being requalified"):
            await self.manager.command(self.original.persona_id, "goto", {"url": "https://example.com"})
        self.request_mock.assert_not_awaited()

    async def test_stale_candidate_is_not_normal_start_authorization(self):
        # Models a manager crash after staging, before any successful commit.
        instance_id = "a" * 32
        candidate = replace(self.original, experimental=True, qualification=None,
                            capability_snapshot_fingerprint=self.new_snapshot.fingerprint)
        self.atomic_json(self.manager._validation_path(self.paths, instance_id), candidate.to_dict())
        self.atomic_json(self.paths["state"], {"mode": "requalify", "instance_id": instance_id,
                                              "status": "starting"})
        with self.assertRaisesRegex(QualificationError, "environment changed"):
            await self.manager.start(self.original.persona_id)
        with self.assertRaisesRegex(RuntimeError, "Stale worker"):
            self.manager.load_worker_persona(self.original.persona_id, "b" * 32)
        self.assert_original()
        self._launch_worker_mock.assert_not_awaited()


    async def test_failed_atomic_metadata_commit_preserves_old_identity(self):
        with mock.patch.object(self.manager.store, "save", side_effect=OSError("disk write failed")):
            with self.assertRaisesRegex(OSError, "disk write failed"):
                await self.manager.requalify(self.original.persona_id)
        self.assert_original()
        self.assert_no_candidate()
        with self.assertRaisesRegex(QualificationError, "environment changed"):
            await self.manager.start(self.original.persona_id)

    async def test_worker_boot_failure_leaves_original_intact(self):
        self._launch_worker_mock.side_effect = RuntimeError("Firefox startup failed")
        with self.assertRaisesRegex(RuntimeError, "Firefox startup failed"):
            await self.manager.requalify(self.original.persona_id)
        self.assert_original()
        self.assert_no_candidate()

    async def test_validation_socket_rejects_arbitrary_browser_actions(self):
        from src.persona.runtime import PersonaRuntime
        runtime = PersonaRuntime.__new__(PersonaRuntime)
        runtime.state = {"mode": "requalify"}
        for action in ("eval", "goto", "profile_load", "tab_new", "permission_set"):
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, "Validation workers"):
                await runtime.dispatch(action, {})
