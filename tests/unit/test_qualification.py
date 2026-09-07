"""Pure qualification tests using synthetic probe observations."""

from __future__ import annotations

import copy
import unittest

from src.persona import (
    CapabilitySnapshot,
    Persona,
    TemplateCatalog,
    qualify_probe,
)


class QualificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = TemplateCatalog.default()
        self.snapshot = CapabilitySnapshot(
            environment={"firefox_version": "154.0.1", "backend": "software"},
            capabilities={},
        )
        self.template = self.catalog.get("linux-firefox-native-phase0")
        self.config = self.template.expand(0, "154.0.1")
        self.persona = Persona.build(
            seed=7, template=self.template, final_config=self.config,
            snapshot=self.snapshot, experimental=True,
        )

    def _report(self) -> dict:
        browser = self.config["browser"]
        display = self.config["display"]
        cpu = self.config["cpu"]["hardware_concurrency"]
        locale = self.config["locale"]
        realm = {
            "languages": list(locale["languages"]),
            "timezone": locale["timezone"],
            "hardwareConcurrency": cpu,
        }
        page = {
            "window": {
                "userAgent": browser["user_agent"], "platform": browser["platform"],
                "oscpu": browser["oscpu"], "appVersion": browser["app_version"],
                "hardwareConcurrency": cpu, "languages": list(locale["languages"]),
                "timezone": locale["timezone"],
            },
            "workers": {
                "dedicated": copy.deepcopy(realm), "shared": copy.deepcopy(realm),
                "service": copy.deepcopy(realm),
            },
            "display": {
                "screen": {"width": display["screen_width"], "height": display["screen_height"],
                           "availWidth": display["avail_width"], "availHeight": display["avail_height"],
                           "colorDepth": display["color_depth"], "pixelDepth": display["pixel_depth"]},
                "viewport": {"width": 1200, "height": 700, "outerWidth": 1280, "outerHeight": 800},
                "devicePixelRatio": display["device_pixel_ratio"], "screenX": 0, "screenY": 0,
                "orientation": {"type": "landscape-primary", "angle": 0},
            },
            "webgl": {"supported": True},
        }
        headers = {"User-Agent": browser["user_agent"], "Accept-Language": locale["accept_language"]}
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

    def test_native_combination_passes_and_keeps_derived_viewport(self) -> None:
        report = qualify_probe(self.persona, self.snapshot, self._report(), catalog=self.catalog)
        self.assertTrue(report.passed)
        self.assertTrue(report.full_combination)
        self.assertEqual(report.environment_fingerprint, self.snapshot.fingerprint)
        self.assertEqual({item.status for item in report.evidence}, {"supported"})
        display = next(item for item in report.evidence if item.capability == "display_window")
        self.assertEqual(display.requested["viewport_policy"], "derived")

    def test_missing_worker_is_partial_and_cannot_pass(self) -> None:
        probe = self._report()
        del probe["observations"]["page"]["workers"]["shared"]
        report = qualify_probe(self.persona, self.snapshot, probe, catalog=self.catalog)
        self.assertFalse(report.passed)
        cpu = next(item for item in report.evidence if item.capability == "cpu_window_worker")
        self.assertEqual(cpu.status, "partial")
        self.assertIn("shared", cpu.contexts)

    def test_missing_worker_http_context_is_partial(self) -> None:
        probe = self._report()
        probe["observations"]["http"]["worker"] = [
            item for item in probe["observations"]["http"]["worker"]
            if item["worker_type"] != "service"
        ]
        report = qualify_probe(self.persona, self.snapshot, probe, catalog=self.catalog)
        self.assertFalse(report.passed)
        locale = next(item for item in report.evidence if item.capability == "locale_window_worker_http")
        self.assertEqual(locale.status, "partial")
        self.assertIsNone(locale.observed["worker_headers"]["service"]["user_agent"])

    def test_q_formatting_is_semantic(self) -> None:
        probe = self._report()
        locale = self.config["locale"]
        probe["observations"]["http"]["document"]["Accept-Language"] = "en-US;q=1.0, zh;q=0.90" if locale["locale"] != "zh-CN" else "zh-CN;q=1.0,zh;q=0.90,en-US;q=0.80,en;q=0.70"
        probe["observations"]["http"]["worker"][0]["headers"]["Accept-Language"] = probe["observations"]["http"]["document"]["Accept-Language"]
        # Use the matching language list and quality values from the template;
        # only textual q formatting differs here.
        if locale["locale"] == "zh-CN":
            probe["observations"]["http"]["document"]["Accept-Language"] = "zh-CN;q=1.0,zh;q=0.90,en-US;q=0.80,en;q=0.70"
            probe["observations"]["http"]["worker"][0]["headers"]["Accept-Language"] = probe["observations"]["http"]["document"]["Accept-Language"]
        report = qualify_probe(self.persona, self.snapshot, probe, catalog=self.catalog)
        self.assertTrue(report.passed)

    def test_igpu_candidate_is_explicitly_unsupported(self) -> None:
        template = self.catalog.get("linux-firefox-igpu-candidate-phase0")
        config = template.expand(0, "154.0.1")
        persona = Persona.build(seed=8, template=template, final_config=config,
                                snapshot=self.snapshot, experimental=True)
        report = qualify_probe(persona, self.snapshot, self._report(), catalog=self.catalog)
        self.assertFalse(report.passed)
        graphics = next(item for item in report.evidence if item.capability == "graphics_full_combination")
        self.assertEqual(graphics.status, "unsupported")


if __name__ == "__main__":
    unittest.main()
