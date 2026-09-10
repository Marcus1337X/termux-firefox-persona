"""Unit tests for Persona privacy settings, storage APIs and network state qualification."""

from copy import deepcopy
import unittest
from unittest import mock

from src.persona.model import (
    CapabilitySnapshot,
    NoEligiblePersonaError,
    Persona,
    PersonaGenerator,
    PersonaTemplate,
    TemplateCatalog,
    TemplateError,
)
from src.persona.qualification import qualify_probe
from src.persona.runtime import firefox_settings


class PersonaPrivacyStorageModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = TemplateCatalog.default()
        self.template = self.catalog.get("linux-firefox-privacy-storage-glx-v1")

    def test_privacy_storage_template_variants_and_expansion(self) -> None:
        self.assertEqual(self.template.status, "candidate")
        self.assertEqual(self.template.version, "1.0.0")
        self.assertEqual(len(self.template.variants), 4)
        self.assertIn("privacy_window_http", self.template.required_capabilities)
        self.assertIn("storage_window", self.template.required_capabilities)
        self.assertIn("network_window_worker", self.template.required_capabilities)
        self.assertIn("input_window", self.template.required_capabilities)
        self.assertEqual(len(self.template.required_capabilities), 17)

        for index in range(4):
            config = self.template.expand(index, "154.0.1")
            self.assertEqual(config["privacy"], {
                "do_not_track": True,
                "global_privacy_control": True,
            })
            self.assertEqual(config["storage"], {
                "local_storage": True,
                "session_storage": True,
                "indexed_db": True,
                "caches": True,
            })
            self.assertEqual(config["network"], {
                "online": True,
            })

    def test_unqualified_privacy_storage_template_cannot_generate_strict_persona(self) -> None:
        snapshot = CapabilitySnapshot(environment={"firefox_version": "154.0.1"}, capabilities={})
        with self.assertRaises(NoEligiblePersonaError):
            PersonaGenerator(TemplateCatalog([self.template]), snapshot,
                             runtime_browser_version="154.0.1").create(seed=1)

    def test_privacy_template_rejects_invalid_privacy_config(self) -> None:
        cases = [
            ("do_not_track", "invalid"),
            ("do_not_track", 2),
            ("global_privacy_control", "true"),
            ("global_privacy_control", 1),
            ("global_privacy_control", None),
        ]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                data = self.template.to_dict()
                data["base_config"]["privacy"][key] = value
                with self.assertRaises(TemplateError):
                    PersonaTemplate.from_mapping(data)

    def test_storage_template_rejects_invalid_storage_config(self) -> None:
        cases = [
            ("local_storage", "true"),
            ("session_storage", 1),
            ("indexed_db", None),
            ("caches", 0),
        ]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                data = self.template.to_dict()
                data["base_config"]["storage"][key] = value
                with self.assertRaises(TemplateError):
                    PersonaTemplate.from_mapping(data)

    def test_network_template_rejects_invalid_network_config(self) -> None:
        for val in ("true", 1, None, []):
            with self.subTest(val=val):
                data = self.template.to_dict()
                data["base_config"]["network"]["online"] = val
                with self.assertRaises(TemplateError):
                    PersonaTemplate.from_mapping(data)

    def test_privacy_template_accepts_and_rejects_tracking_and_cookies(self) -> None:
        valid_data = self.template.to_dict()
        valid_data["base_config"]["privacy"]["tracking_protection"] = True
        valid_data["base_config"]["privacy"]["cookie_policy"] = "block_cross_site_tracking"
        tmpl = PersonaTemplate.from_mapping(valid_data)
        self.assertTrue(tmpl.base_config["privacy"]["tracking_protection"])

        for invalid_policy in ("invalid", 99, []):
            with self.subTest(invalid_policy=invalid_policy):
                data = self.template.to_dict()
                data["base_config"]["privacy"]["cookie_policy"] = invalid_policy
                with self.assertRaises(TemplateError):
                    PersonaTemplate.from_mapping(data)

    def test_network_template_accepts_and_rejects_proxy_config(self) -> None:
        valid_data = self.template.to_dict()
        valid_data["base_config"]["network"]["proxy"] = {
            "type": "manual", "host": "127.0.0.1", "port": 8080,
        }
        tmpl = PersonaTemplate.from_mapping(valid_data)
        self.assertEqual(tmpl.base_config["network"]["proxy"]["host"], "127.0.0.1")

        cases = [
            {"type": "invalid"},
            {"type": "manual", "host": "", "port": 8080},
            {"type": "manual", "host": "127.0.0.1", "port": -1},
            {"type": "manual", "host": "127.0.0.1", "port": 70000},
        ]
        for proxy_case in cases:
            with self.subTest(proxy_case=proxy_case):
                data = self.template.to_dict()
                data["base_config"]["network"]["proxy"] = proxy_case
                with self.assertRaises(TemplateError):
                    PersonaTemplate.from_mapping(data)


class PersonaRuntimePrivacySettingsTests(unittest.TestCase):
    def test_firefox_settings_configures_dnt_and_gpc(self) -> None:
        config = {
            "browser": {"family": "firefox", "firefox_version": "154.0.1"},
            "cpu": {"hardware_concurrency": 4},
            "display": {"device_pixel_ratio": 1},
            "locale": {"locale": "zh-CN", "languages": ["zh-CN", "en-US"], "timezone": "Asia/Shanghai"},
            "privacy": {
                "do_not_track": True,
                "global_privacy_control": True,
            },
        }
        prefs, env = firefox_settings(config)
        self.assertTrue(prefs["privacy.donottrackheader.enabled"])
        self.assertEqual(prefs["privacy.donottrackheader.value"], 1)
        self.assertTrue(prefs["privacy.globalprivacycontrol.enabled"])
        self.assertTrue(prefs["privacy.globalprivacycontrol.functionality.enabled"])

    def test_firefox_settings_disables_dnt_and_gpc_when_requested(self) -> None:
        config = {
            "browser": {"family": "firefox", "firefox_version": "154.0.1"},
            "cpu": {"hardware_concurrency": 4},
            "display": {"device_pixel_ratio": 1},
            "locale": {"locale": "zh-CN", "languages": ["zh-CN", "en-US"], "timezone": "Asia/Shanghai"},
            "privacy": {
                "do_not_track": False,
                "global_privacy_control": False,
            },
        }
        prefs, env = firefox_settings(config)
        self.assertFalse(prefs["privacy.donottrackheader.enabled"])
        self.assertFalse(prefs["privacy.globalprivacycontrol.enabled"])
        self.assertFalse(prefs["privacy.globalprivacycontrol.functionality.enabled"])

    def test_firefox_settings_configures_tracking_protection_and_cookie_policy(self) -> None:
        config = {
            "browser": {"family": "firefox", "firefox_version": "154.0.1"},
            "cpu": {"hardware_concurrency": 4},
            "display": {"device_pixel_ratio": 1},
            "locale": {"locale": "zh-CN", "languages": ["zh-CN", "en-US"], "timezone": "Asia/Shanghai"},
            "privacy": {
                "tracking_protection": True,
                "cookie_policy": "block_cross_site_tracking",
            },
        }
        prefs, env = firefox_settings(config)
        self.assertTrue(prefs["privacy.trackingprotection.enabled"])
        self.assertTrue(prefs["privacy.trackingprotection.socialtracking.enabled"])
        self.assertEqual(prefs["network.cookie.cookieBehavior"], 4)

    def test_firefox_settings_configures_proxy_isolation(self) -> None:
        direct_config = {
            "browser": {"family": "firefox", "firefox_version": "154.0.1"},
            "cpu": {"hardware_concurrency": 4},
            "display": {"device_pixel_ratio": 1},
            "locale": {"locale": "zh-CN", "languages": ["zh-CN", "en-US"], "timezone": "Asia/Shanghai"},
            "network": {"proxy": {"type": "direct"}},
        }
        prefs, _ = firefox_settings(direct_config)
        self.assertEqual(prefs["network.proxy.type"], 0)

        manual_config = {
            "browser": {"family": "firefox", "firefox_version": "154.0.1"},
            "cpu": {"hardware_concurrency": 4},
            "display": {"device_pixel_ratio": 1},
            "locale": {"locale": "zh-CN", "languages": ["zh-CN", "en-US"], "timezone": "Asia/Shanghai"},
            "network": {
                "proxy": {
                    "type": "manual", "host": "192.168.1.100", "port": 8888, "socks": True,
                },
            },
        }
        prefs, _ = firefox_settings(manual_config)
        self.assertEqual(prefs["network.proxy.type"], 1)
        self.assertEqual(prefs["network.proxy.http"], "192.168.1.100")
        self.assertEqual(prefs["network.proxy.http_port"], 8888)
        self.assertEqual(prefs["network.proxy.ssl"], "192.168.1.100")
        self.assertEqual(prefs["network.proxy.ssl_port"], 8888)
        self.assertEqual(prefs["network.proxy.socks"], "192.168.1.100")
        self.assertEqual(prefs["network.proxy.socks_port"], 8888)
        self.assertEqual(prefs["network.proxy.socks_version"], 5)
        self.assertTrue(prefs["network.proxy.socks_remote_dns"])


class PersonaPrivacyStorageQualificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = CapabilitySnapshot(
            environment={"firefox_version": "154.0.1", "backend": "software"},
            capabilities={},
        )
        template_dict = {
            "schema_version": 1,
            "id": "test-privacy-storage-template",
            "version": "1.0.0",
            "status": "candidate",
            "strict_eligible": True,
            "metadata": {"description": "Test privacy, storage and network template"},
            "base_config": {
                "browser": {
                    "family": "firefox", "platform": "Linux x86_64",
                    "oscpu": "Linux x86_64", "app_version": "5.0 (X11)",
                    "firefox_version": "154.0.1",
                    "user_agent": "Mozilla/5.0 (X11; Linux x86_64; rv:154.0) Gecko/20100101 Firefox/154.0",
                },
                "privacy": {"do_not_track": True, "global_privacy_control": True},
                "storage": {"local_storage": True, "session_storage": True, "indexed_db": True, "caches": True},
                "network": {"online": True},
            },
            "required_capabilities": [
                "privacy_window_http",
                "storage_window",
                "network_window_worker",
            ],
            "variants": [
                {
                    "variant_id": "v1",
                    "cpu": {"hardware_concurrency": 4},
                    "display": {
                        "screen_width": 1280, "screen_height": 800,
                        "avail_width": 1280, "avail_height": 800,
                        "device_pixel_ratio": 1, "color_depth": 24, "pixel_depth": 24,
                    },
                    "locale": {
                        "locale": "zh-CN",
                        "languages": ["zh-CN", "zh", "en-US", "en"],
                        "accept_language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
                        "timezone": "Asia/Shanghai",
                    },
                },
            ],
        }
        self.template = PersonaTemplate.from_mapping(template_dict)
        self.catalog = TemplateCatalog([self.template])
        self.config = self.template.expand(0, "154.0.1")
        self.persona = Persona.build(
            seed=101, template=self.template, final_config=self.config,
            snapshot=self.snapshot, experimental=True,
        )

    def _valid_probe(self) -> dict:
        browser = self.persona.config["browser"]
        locale = self.persona.config["locale"]
        cpu = self.persona.config["cpu"]["hardware_concurrency"]
        display = self.persona.config["display"]

        realm = {
            "kind": "worker",
            "userAgent": browser["user_agent"],
            "platform": browser["platform"],
            "hardwareConcurrency": cpu,
            "languages": list(locale["languages"]),
            "language": locale["locale"],
            "timezone": locale["timezone"],
            "globalPrivacyControl": True,
            "onLine": True,
        }

        page = {
            "window": {
                "userAgent": browser["user_agent"], "platform": browser["platform"],
                "oscpu": browser["oscpu"], "appVersion": browser["app_version"],
                "hardwareConcurrency": cpu, "languages": list(locale["languages"]),
                "language": locale["locale"], "timezone": locale["timezone"],
            },
            "display": {
                "screen": {"width": display["screen_width"], "height": display["screen_height"],
                           "availWidth": display["avail_width"], "availHeight": display["avail_height"],
                           "colorDepth": display["color_depth"], "pixelDepth": display["pixel_depth"]},
                "viewport": {"width": 1200, "height": 700, "outerWidth": 1280, "outerHeight": 800},
                "devicePixelRatio": display["device_pixel_ratio"], "screenX": 0, "screenY": 0,
                "orientation": {"type": "landscape-primary", "angle": 0},
            },
            "privacy": {
                "doNotTrack": "1",
                "globalPrivacyControl": True,
            },
            "storage": {
                "localStorage": True,
                "sessionStorage": True,
                "indexedDB": True,
                "caches": True,
            },
            "network": {
                "onLine": True,
            },
            "workers": {
                kind: deepcopy(realm)
                for kind in ("dedicated", "shared", "service")
            },
        }

        headers = {
            "User-Agent": browser["user_agent"],
            "Accept-Language": locale["accept_language"],
            "DNT": "1",
            "Sec-GPC": "1",
        }
        worker_headers = [
            {"worker_type": kind, "headers": headers.copy()}
            for kind in ("dedicated", "shared", "service")
        ]
        return {
            "observations": {
                "page": page,
                "http": {"document": headers, "worker": worker_headers},
            }
        }

    def test_privacy_storage_qualification_passes(self) -> None:
        probe = self._valid_probe()
        report = qualify_probe(self.persona, self.snapshot, probe, catalog=self.catalog)
        self.assertTrue(report.passed)
        self.assertTrue(report.full_combination)
        statuses = {item.capability: item.status for item in report.evidence}
        self.assertEqual(statuses.get("privacy_window_http"), "supported")
        self.assertEqual(statuses.get("storage_window"), "supported")
        self.assertEqual(statuses.get("network_window_worker"), "supported")

    def test_privacy_dnt_mismatch_fails_qualification(self) -> None:
        probe = self._valid_probe()
        del probe["observations"]["http"]["document"]["DNT"]
        report = qualify_probe(self.persona, self.snapshot, probe, catalog=self.catalog)
        self.assertFalse(report.passed)
        ev = next(e for e in report.evidence if e.capability == "privacy_window_http")
        self.assertEqual(ev.status, "partial")
        self.assertIn("privacy settings", str(report.reasons))

    def test_privacy_gpc_mismatch_fails_qualification(self) -> None:
        probe = self._valid_probe()
        probe["observations"]["page"]["privacy"]["globalPrivacyControl"] = False
        report = qualify_probe(self.persona, self.snapshot, probe, catalog=self.catalog)
        self.assertFalse(report.passed)
        ev = next(e for e in report.evidence if e.capability == "privacy_window_http")
        self.assertEqual(ev.status, "partial")

    def test_worker_gpc_inconsistent_fails_qualification(self) -> None:
        probe = self._valid_probe()
        probe["observations"]["page"]["workers"]["dedicated"]["globalPrivacyControl"] = False
        report = qualify_probe(self.persona, self.snapshot, probe, catalog=self.catalog)
        self.assertFalse(report.passed)
        ev = next(e for e in report.evidence if e.capability == "privacy_window_http")
        self.assertEqual(ev.status, "partial")

    def test_storage_unavailable_fails_qualification(self) -> None:
        for storage_key in ("localStorage", "sessionStorage", "indexedDB", "caches"):
            with self.subTest(storage_key=storage_key):
                probe = self._valid_probe()
                probe["observations"]["page"]["storage"][storage_key] = False
                report = qualify_probe(self.persona, self.snapshot, probe, catalog=self.catalog)
                self.assertFalse(report.passed)
                ev = next(e for e in report.evidence if e.capability == "storage_window")
                self.assertEqual(ev.status, "partial")
                self.assertIn("browser storage APIs", str(report.reasons))

    def test_network_offline_fails_qualification(self) -> None:
        probe = self._valid_probe()
        probe["observations"]["page"]["network"]["onLine"] = False
        report = qualify_probe(self.persona, self.snapshot, probe, catalog=self.catalog)
        self.assertFalse(report.passed)
        ev = next(e for e in report.evidence if e.capability == "network_window_worker")
        self.assertEqual(ev.status, "partial")
        self.assertIn("network status", str(report.reasons))

    def test_worker_network_offline_fails_qualification(self) -> None:
        probe = self._valid_probe()
        probe["observations"]["page"]["workers"]["shared"]["onLine"] = False
        report = qualify_probe(self.persona, self.snapshot, probe, catalog=self.catalog)
        self.assertFalse(report.passed)
        ev = next(e for e in report.evidence if e.capability == "network_window_worker")
        self.assertEqual(ev.status, "partial")


class PersonaPermissionProbingTests(unittest.IsolatedAsyncioTestCase):
    async def test_permission_query_evaluates_named_descriptors(self):
        from src.persona.probe import ProbeRunner
        runner = ProbeRunner.__new__(ProbeRunner)
        runner.client = mock.Mock(evaluate=mock.AsyncMock())
        runner.client.evaluate.side_effect = ["prompt", "granted"]

        geo = await runner._permission_state("ctx-1", timeout=5, name="geolocation")
        self.assertEqual(geo, "prompt")
        self.assertIn("name: 'geolocation'", runner.client.evaluate.await_args.args[1])

        notif = await runner._permission_state("ctx-1", timeout=5, name="notifications")
        self.assertEqual(notif, "granted")
        self.assertIn("name: 'notifications'", runner.client.evaluate.await_args.args[1])

    async def test_query_permissions_queries_bundle(self):
        from src.persona.probe import ProbeRunner
        runner = ProbeRunner.__new__(ProbeRunner)
        runner._permission_state = mock.AsyncMock(side_effect=lambda ctx, to, name: f"{name}-state")

        bundle = await runner._query_permissions("ctx-1", timeout=5)
        self.assertEqual(bundle, {
            "geolocation": "geolocation-state",
            "notifications": "notifications-state",
        })
