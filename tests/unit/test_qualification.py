"""Pure qualification tests using synthetic probe observations."""

from __future__ import annotations

import copy
import unittest
from dataclasses import replace

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

    def _appearance_geo_persona(self):
        base_config = copy.deepcopy(self.template.base_config)
        base_config["appearance"] = {
            "color_scheme": "light", "reduced_motion": False,
            "contrast": "no-preference", "forced_colors": False,
        }
        base_config["geolocation"] = {
            "latitude": 31.2304, "longitude": 121.4737,
            "accuracy": 50, "permission": "prompt",
        }
        template = replace(
            self.template,
            base_config=base_config,
            required_capabilities=self.template.required_capabilities + (
                "appearance_window", "geolocation_window",
            ),
        )
        config = template.expand(0, "154.0.1")
        persona = Persona.build(
            seed=11, template=template, final_config=config,
            snapshot=self.snapshot, experimental=True,
        )
        return template, config, persona

    def test_appearance_and_geolocation_require_window_evidence(self) -> None:
        template, config, persona = self._appearance_geo_persona()
        probe = self._report()
        probe["observations"]["page"]["appearance"] = {
            "colorScheme": "light", "reducedMotion": False,
            "contrast": "no-preference", "forcedColors": False,
        }
        probe["observations"]["page"]["geolocation"] = {
            "origin": "http://127.0.0.1:12345", "original_state": "prompt",
            "granted_state": "granted",
            "granted_position": {"ok": True, "latitude": 31.2304,
                                  "longitude": 121.4737, "accuracy": 50},
            "denied_state": "denied",
            "denied_position": {"ok": False, "errorCode": 1},
            "restored_state": "prompt",
            "worker_contexts": {"dedicated": "notapplicable", "shared": "notapplicable", "service": "notapplicable"},
        }
        report = qualify_probe(
            persona, self.snapshot, probe,
            catalog=TemplateCatalog([template]),
        )
        self.assertTrue(report.passed)
        self.assertEqual(
            next(item for item in report.evidence if item.capability == "geolocation_window").contexts[1:],
            ("dedicated:notapplicable", "shared:notapplicable", "service:notapplicable"),
        )

    def test_geolocation_restore_or_appearance_mismatch_is_partial(self) -> None:
        template, config, persona = self._appearance_geo_persona()
        probe = self._report()
        probe["observations"]["page"]["appearance"] = {
            "colorScheme": "dark", "reducedMotion": False,
            "contrast": "no-preference", "forcedColors": False,
        }
        probe["observations"]["page"]["geolocation"] = {
            "original_state": "prompt", "granted_state": "granted",
            "granted_position": {"ok": True, "latitude": 31.2304,
                                  "longitude": 121.4737, "accuracy": 50},
            "denied_state": "denied", "denied_position": {"ok": False, "errorCode": 1},
            "restored_state": "granted", "worker_contexts": {
                "dedicated": "notapplicable", "shared": "notapplicable", "service": "notapplicable",
            },
        }
        report = qualify_probe(persona, self.snapshot, probe, catalog=TemplateCatalog([template]))
        self.assertFalse(report.passed)
        statuses = {item.capability: item.status for item in report.evidence}
        self.assertEqual(statuses["appearance_window"], "partial")
        self.assertEqual(statuses["geolocation_window"], "partial")

    def test_webgl_window_requires_both_context_behavior_reports(self) -> None:
        base_config = copy.deepcopy(self.template.base_config)
        base_config["graphics"].update({
            "context_backend": "glx", "execution_backend": "software",
            "vendor": "Mesa", "renderer": "llvmpipe, or similar",
            "webgl1": True, "webgl2": True,
        })
        template = replace(
            self.template, base_config=base_config,
            required_capabilities=self.template.required_capabilities + ("webgl_window",),
        )
        config = template.expand(0, "154.0.1")
        persona = Persona.build(seed=12, template=template, final_config=config,
                                snapshot=self.snapshot, experimental=True)
        probe = self._report()
        behavior = {
            "passed": True, "compile": True, "link": True,
            "errors": [],
            "triangle": {"compile": True, "link": True, "nonEmpty": True,
                          "readback": True, "exactRed": True,
                          "rgba": [255, 0, 0, 255]},
            "framebuffer": {"rgba8": True, "complete": True, "readback": True,
                            "exactGreen": True, "rgba": [0, 255, 0, 255]},
        }
        probe["observations"]["page"]["webgl"] = {
            "supported": True, "unmaskedVendor": "Mesa",
            "unmaskedRenderer": "llvmpipe (LLVM 18.1.8, 256 bits)",
            "webgl1": {"supported": True, "unmaskedVendor": "Mesa",
                       "unmaskedRenderer": "llvmpipe (LLVM 18.1.8, 256 bits)",
                       "behavior": behavior},
            "webgl2": {"supported": True, "unmaskedVendor": "Mesa",
                       "unmaskedRenderer": "llvmpipe (LLVM 18.1.8, 256 bits)",
                       "behavior": behavior},
            "behavior": {"webgl1": behavior, "webgl2": behavior},
        }
        report = qualify_probe(persona, self.snapshot, probe, catalog=TemplateCatalog([template]))
        self.assertTrue(report.passed)

        broken = copy.deepcopy(probe)
        broken["observations"]["page"]["webgl"]["webgl2"]["behavior"]["triangle"]["rgba"] = [255, 255, 255, 255]
        broken["observations"]["page"]["webgl"]["behavior"]["webgl2"] = broken["observations"]["page"]["webgl"]["webgl2"]["behavior"]
        failed = qualify_probe(persona, self.snapshot, broken, catalog=TemplateCatalog([template]))
        self.assertFalse(failed.passed)
        webgl = next(item for item in failed.evidence if item.capability == "webgl_window")
        self.assertEqual(webgl.status, "partial")

    def test_fonts_window_requires_positive_negative_and_canvas_evidence(self) -> None:
        base_config = copy.deepcopy(self.template.base_config)
        base_config["fonts"] = {
            "families": ["Probe Sans"],
            "aliases": {"sans-serif": "Probe Sans", "serif": "Probe Sans",
                         "monospace": "Probe Sans", "emoji": "Probe Sans", "cjk": "Probe Sans"},
            "samples": {"Probe Sans": "Aa 上海 😀"},
            "blocked_families": ["Blocked Font"],
            "policy": "whitelist",
        }
        template = replace(
            self.template, base_config=base_config,
            required_capabilities=self.template.required_capabilities + ("fonts_window",),
        )
        config = template.expand(0, "154.0.1")
        persona = Persona.build(seed=13, template=template, final_config=config,
                                snapshot=self.snapshot, experimental=True)
        probe = self._report()
        rendered = {
            "supported": True, "nonEmpty": True, "stable": True, "exportMatches": True,
            "metrics": {"width": 42, "ascent": 24, "descent": 7, "left": -1, "right": 41},
            "inkPixels": 123, "hash": "abcd1234", "repeatHash": "abcd1234",
            "dataUrlDecoded": {"supported": True, "hash": "abcd1234"},
            "blobDecoded": {"supported": True, "hash": "abcd1234"},
        }
        probe["observations"]["page"]["fonts"] = {
            "policy": "whitelist", "workerFonts": "not_verified",
            "aliases_checked": ["sans-serif", "serif", "monospace"],
            "missingFamily": "__TBP_MISSING_FONT__",
            "positive": {"Probe Sans": {"ok": True, "status": "loaded"}},
            "negative": {
                "__TBP_MISSING_FONT__": {"failed": True},
                "Blocked Font": {"failed": True},
            },
            "families": {"Probe Sans": {
                "sample": "Aa 上海 😀", "positive": {"ok": True},
                "direct": copy.deepcopy(rendered), "local": copy.deepcopy(rendered),
                "metricsMatch": True, "pixelMatch": True,
            }},
            "aliases": {
                alias: {
                    "target": "Probe Sans", "generic": copy.deepcopy(rendered),
                    "targetLocal": copy.deepcopy(rendered),
                    "metricsMatch": True, "pixelMatch": True,
                }
                for alias in ("sans-serif", "serif", "monospace")
            },
        }
        report = qualify_probe(persona, self.snapshot, probe, catalog=TemplateCatalog([template]))
        self.assertTrue(report.passed)

        broken = copy.deepcopy(probe)
        broken["observations"]["page"]["fonts"]["negative"]["Blocked Font"]["failed"] = False
        failed = qualify_probe(persona, self.snapshot, broken, catalog=TemplateCatalog([template]))
        self.assertFalse(failed.passed)
        fonts = next(item for item in failed.evidence if item.capability == "fonts_window")
        self.assertEqual(fonts.status, "partial")
        tampered = copy.deepcopy(probe)
        tampered["observations"]["page"]["fonts"]["families"]["Probe Sans"]["direct"]["blobDecoded"]["hash"] = "ffffffff"
        self.assertFalse(qualify_probe(persona, self.snapshot, tampered, catalog=TemplateCatalog([template])).passed)

    @staticmethod
    def _font_rendering(worker=False, digest="abcd1234"):
        return {
            "supported": True, "nonEmpty": True, "stable": True, "exportMatches": True,
            "metrics": {"width": 42, "ascent": 24, "descent": 7, "left": -1, "right": 41},
            "inkPixels": 123, "hash": digest, "repeatHash": digest,
            "dataUrlDecoded": ({"supported": False, "notApplicable": True}
                                if worker else {"supported": True, "hash": digest}),
            "dataUrlLength": None if worker else 1234,
            "exportApi": "convertToBlob" if worker else "toBlob",
            "blobDecoded": {"supported": True, "hash": digest},
        }

    @staticmethod
    def _font_config_for_workers():
        return {
            "families": ["Probe Sans"],
            "aliases": {"sans-serif": "Probe Sans", "serif": "Probe Sans",
                         "monospace": "Probe Sans"},
            "samples": {"Probe Sans": "Aa 上海 😀"},
            "blocked_families": ["Blocked Font"], "policy": "whitelist",
        }

    def _font_observation_for_workers(self, worker=False, digest="abcd1234"):
        rendered = self._font_rendering(worker, digest)
        item = {"sample": "Aa 上海 😀", "positive": {"ok": True},
                "direct": copy.deepcopy(rendered), "local": copy.deepcopy(rendered),
                "metricsMatch": True, "pixelMatch": True}
        alias_item = {"target": "Probe Sans", "generic": copy.deepcopy(rendered),
                      "targetLocal": copy.deepcopy(rendered),
                      "metricsMatch": True, "pixelMatch": True}
        return {
            "policy": "whitelist", "workerFonts": "measured" if worker else "not_verified",
            "aliases_checked": ["sans-serif", "serif", "monospace"],
            "missingFamily": "__TBP_MISSING_FONT__",
            "positive": {"Probe Sans": {"ok": True}},
            "negative": {"__TBP_MISSING_FONT__": {"failed": True},
                         "Blocked Font": {"failed": True}},
            "families": {"Probe Sans": item},
            "aliases": {alias: copy.deepcopy(alias_item)
                        for alias in ("sans-serif", "serif", "monospace")},
        }

    def test_worker_fonts_require_measured_export_and_window_consistency(self):
        base_config = copy.deepcopy(self.template.base_config)
        base_config["fonts"] = self._font_config_for_workers()
        template = replace(self.template, base_config=base_config,
                           required_capabilities=self.template.required_capabilities +
                           ("fonts_window", "fonts_workers"))
        config = template.expand(0, "154.0.1")
        persona = Persona.build(seed=14, template=template, final_config=config,
                                snapshot=self.snapshot, experimental=True)
        probe = self._report()
        probe["observations"]["page"]["fonts"] = self._font_observation_for_workers()
        for realm in ("dedicated", "shared", "service"):
            probe["observations"]["page"]["workers"][realm]["fonts"] = self._font_observation_for_workers(True)
        report = qualify_probe(persona, self.snapshot, probe,
                               catalog=TemplateCatalog([template]))
        self.assertTrue(report.passed)
        self.assertEqual(next(item for item in report.evidence
                              if item.capability == "fonts_workers").status, "supported")

        broken = copy.deepcopy(probe)
        del broken["observations"]["page"]["workers"]["shared"]["fonts"]
        failed = qualify_probe(persona, self.snapshot, broken,
                               catalog=TemplateCatalog([template]))
        self.assertFalse(failed.passed)
        self.assertEqual(next(item for item in failed.evidence
                              if item.capability == "fonts_workers").status, "partial")

        tampered = copy.deepcopy(probe)
        tampered["observations"]["page"]["workers"]["dedicated"]["fonts"]["families"]["Probe Sans"]["direct"]["hash"] = "ffffffff"
        self.assertFalse(qualify_probe(persona, self.snapshot, tampered,
                                       catalog=TemplateCatalog([template])).passed)
        for key, value in (("blobDecoded", {}), ("exportApi", "toBlob"),
                           ("dataUrlDecoded", {"supported": False})):
            with self.subTest(export_field=key):
                broken = copy.deepcopy(probe)
                broken["observations"]["page"]["workers"]["service"]["fonts"]["families"]["Probe Sans"]["direct"][key] = value
                self.assertFalse(qualify_probe(persona, self.snapshot, broken,
                                               catalog=TemplateCatalog([template])).passed)
        different = copy.deepcopy(probe)
        different["observations"]["page"]["workers"]["dedicated"]["fonts"] = self._font_observation_for_workers(True, "ffff1111")
        self.assertFalse(qualify_probe(persona, self.snapshot, different,
                                       catalog=TemplateCatalog([template])).passed)

    @staticmethod
    def _webgl_observation_for_workers():
        behavior = {
            "passed": True, "compile": True, "link": True, "errors": [],
            "triangle": {"compile": True, "link": True, "nonEmpty": True,
                          "readback": True, "exactRed": True,
                          "rgba": [255, 0, 0, 255]},
            "framebuffer": {"rgba8": True, "complete": True, "readback": True,
                            "exactGreen": True, "rgba": [0, 255, 0, 255]},
        }
        context = {"supported": True, "unmaskedVendor": "Mesa",
                   "unmaskedRenderer": "llvmpipe (LLVM 18.1.8, 256 bits)",
                   "behavior": copy.deepcopy(behavior)}
        return {"supported": True, "unmaskedVendor": "Mesa",
                "unmaskedRenderer": "llvmpipe (LLVM 18.1.8, 256 bits)",
                "webgl1": copy.deepcopy(context), "webgl2": copy.deepcopy(context),
                "behavior": {"webgl1": copy.deepcopy(behavior),
                              "webgl2": copy.deepcopy(behavior)}}

    def test_worker_webgl_requires_all_contexts_and_exact_pixels(self):
        base_config = copy.deepcopy(self.template.base_config)
        base_config["graphics"] = {
            "identity_class": "native-linux-firefox", "hardware_class": "software",
            "execution_backend": "software", "context_backend": "glx",
            "vendor": "Mesa", "renderer": "llvmpipe, or similar",
            "webgl1": True, "webgl2": True,
        }
        template = replace(self.template, base_config=base_config,
                           required_capabilities=self.template.required_capabilities +
                           ("webgl_window", "webgl_workers"))
        config = template.expand(0, "154.0.1")
        persona = Persona.build(seed=15, template=template, final_config=config,
                                snapshot=self.snapshot, experimental=True)
        probe = self._report()
        probe["observations"]["page"]["webgl"] = self._webgl_observation_for_workers()
        for realm in ("dedicated", "shared", "service"):
            probe["observations"]["page"]["workers"][realm]["webgl"] = self._webgl_observation_for_workers()
        self.assertTrue(qualify_probe(persona, self.snapshot, probe,
                                      catalog=TemplateCatalog([template])).passed)
        broken = copy.deepcopy(probe)
        broken["observations"]["page"]["workers"]["service"]["webgl"]["webgl2"]["behavior"]["framebuffer"]["rgba"] = [255, 0, 0, 255]
        broken["observations"]["page"]["workers"]["service"]["webgl"]["behavior"]["webgl2"]["framebuffer"]["rgba"] = [255, 0, 0, 255]
        failed = qualify_probe(persona, self.snapshot, broken,
                               catalog=TemplateCatalog([template]))
        self.assertFalse(failed.passed)
        self.assertEqual(next(item for item in failed.evidence
                              if item.capability == "webgl_workers").status, "partial")


if __name__ == "__main__":
    unittest.main()
