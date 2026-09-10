"""Unit tests for desktop input devices, window interaction and cross-context consistency."""

from copy import deepcopy
import unittest

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


class PersonaInteractionModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = TemplateCatalog.default()
        self.template = self.catalog.get("linux-firefox-interaction-glx-v1")

    def test_interaction_template_variants_and_expansion(self) -> None:
        self.assertEqual(self.template.status, "candidate")
        self.assertEqual(self.template.version, "1.0.0")
        self.assertEqual(len(self.template.variants), 4)
        self.assertIn("input_window", self.template.required_capabilities)
        self.assertIn("media_codecs_window", self.template.required_capabilities)
        self.assertIn("audio_realtime", self.template.required_capabilities)
        self.assertEqual(len(self.template.required_capabilities), 14)

        for index in range(4):
            config = self.template.expand(index, "154.0.1")
            self.assertEqual(config["input"], {
                "pointer": "fine",
                "hover": True,
                "max_touch_points": 0,
            })
            self.assertEqual(config["interaction"], {
                "visibility_state": "visible",
                "has_focus": True,
            })

    def test_unqualified_interaction_template_cannot_generate_strict_persona(self) -> None:
        snapshot = CapabilitySnapshot(environment={"firefox_version": "154.0.1"}, capabilities={})
        with self.assertRaises(NoEligiblePersonaError):
            PersonaGenerator(TemplateCatalog([self.template]), snapshot,
                             runtime_browser_version="154.0.1").create(seed=1)

    def test_interaction_template_rejects_invalid_input_config(self) -> None:
        cases = [
            ("pointer", "touch"),
            ("pointer", "invalid"),
            ("pointer", 123),
            ("hover", "true"),
            ("hover", 1),
            ("max_touch_points", -1),
            ("max_touch_points", "0"),
            ("max_touch_points", 1.5),
        ]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                data = self.template.to_dict()
                data["base_config"]["input"][key] = value
                with self.assertRaises(TemplateError):
                    PersonaTemplate.from_mapping(data)

    def test_interaction_template_requires_all_inherited_capabilities(self) -> None:
        for key in ("input", "media", "audio", "fonts", "geolocation", "appearance"):
            with self.subTest(key=key):
                data = self.template.to_dict()
                if key in data["base_config"]:
                    del data["base_config"][key]
                for variant in data["variants"]:
                    if key in variant:
                        del variant[key]
                with self.assertRaises(TemplateError):
                    PersonaTemplate.from_mapping(data)


class PersonaInteractionQualificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = CapabilitySnapshot(
            environment={"firefox_version": "154.0.1", "backend": "software"},
            capabilities={},
        )
        # Construct an exact lightweight testing template requesting input_window
        template_dict = {
            "schema_version": 1,
            "id": "test-interaction-template",
            "version": "1.0.0",
            "status": "candidate",
            "strict_eligible": True,
            "metadata": {"description": "Test interaction template"},
            "base_config": {
                "browser": {
                    "family": "firefox", "platform": "Linux x86_64",
                    "oscpu": "Linux x86_64", "app_version": "5.0 (X11)",
                    "firefox_version": "154.0.1",
                    "user_agent": "Mozilla/5.0 (X11; Linux x86_64; rv:154.0) Gecko/20100101 Firefox/154.0",
                },
                "input": {
                    "pointer": "fine",
                    "hover": True,
                    "max_touch_points": 0,
                },
            },
            "required_capabilities": [
                "browser_identity",
                "cpu_window_worker",
                "display_window",
                "locale_window_worker_http",
                "input_window",
            ],
            "variants": [
                {
                    "variant_id": "test-v0",
                    "cpu": {"hardware_concurrency": 2},
                    "display": {
                        "window_policy": "maximized", "viewport_policy": "derived",
                        "screen_width": 1280, "screen_height": 800,
                        "avail_width": 1280, "avail_height": 800,
                        "device_pixel_ratio": 1, "color_depth": 24, "pixel_depth": 24,
                    },
                    "locale": {
                        "locale": "en-US", "languages": ["en-US", "en"],
                        "accept_language": "en-US,en;q=0.9", "timezone": "America/New_York",
                    },
                }
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
        browser = self.config["browser"]
        display = self.config["display"]
        cpu = self.config["cpu"]["hardware_concurrency"]
        locale = self.config["locale"]

        realm = {
            "languages": list(locale["languages"]),
            "language": locale["locale"],
            "timezone": locale["timezone"],
            "hardwareConcurrency": cpu,
            "maxTouchPoints": 0,
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
            "input": {
                "pointer": "fine", "anyPointer": "fine",
                "hover": True, "anyHover": True,
                "maxTouchPoints": 0,
            },
            "interaction": {
                "visibilityState": "visible", "hidden": False,
                "hasFocus": True, "scrollX": 0, "scrollY": 0,
            },
            "workers": {
                kind: deepcopy(realm)
                for kind in ("dedicated", "shared", "service")
            },
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

    def test_input_and_interaction_qualification_passes(self) -> None:
        probe = self._valid_probe()
        report = qualify_probe(self.persona, self.snapshot, probe, catalog=self.catalog)
        self.assertTrue(report.passed)
        self.assertTrue(report.full_combination)
        statuses = {item.capability: item.status for item in report.evidence}
        self.assertEqual(statuses.get("input_window"), "supported")
        self.assertEqual(statuses.get("display_window"), "supported")
        self.assertEqual(statuses.get("locale_window_worker_http"), "supported")

    def test_pointer_coarse_fails_input_qualification(self) -> None:
        probe = self._valid_probe()
        probe["observations"]["page"]["input"]["pointer"] = "coarse"
        report = qualify_probe(self.persona, self.snapshot, probe, catalog=self.catalog)
        self.assertFalse(report.passed)
        input_ev = next(e for e in report.evidence if e.capability == "input_window")
        self.assertEqual(input_ev.status, "partial")
        self.assertIn("input devices", str(report.reasons))

    def test_hover_false_fails_input_qualification(self) -> None:
        probe = self._valid_probe()
        probe["observations"]["page"]["input"]["hover"] = False
        report = qualify_probe(self.persona, self.snapshot, probe, catalog=self.catalog)
        self.assertFalse(report.passed)
        input_ev = next(e for e in report.evidence if e.capability == "input_window")
        self.assertEqual(input_ev.status, "partial")

    def test_touch_points_fails_input_qualification(self) -> None:
        probe = self._valid_probe()
        probe["observations"]["page"]["input"]["maxTouchPoints"] = 2
        report = qualify_probe(self.persona, self.snapshot, probe, catalog=self.catalog)
        self.assertFalse(report.passed)
        input_ev = next(e for e in report.evidence if e.capability == "input_window")
        self.assertEqual(input_ev.status, "partial")

    def test_worker_touch_points_mismatch_fails_input_qualification(self) -> None:
        probe = self._valid_probe()
        probe["observations"]["page"]["workers"]["dedicated"]["maxTouchPoints"] = 1
        report = qualify_probe(self.persona, self.snapshot, probe, catalog=self.catalog)
        self.assertFalse(report.passed)
        input_ev = next(e for e in report.evidence if e.capability == "input_window")
        self.assertEqual(input_ev.status, "partial")

    def test_hidden_visibility_fails_input_qualification(self) -> None:
        probe = self._valid_probe()
        probe["observations"]["page"]["interaction"]["visibilityState"] = "hidden"
        report = qualify_probe(self.persona, self.snapshot, probe, catalog=self.catalog)
        self.assertFalse(report.passed)
        input_ev = next(e for e in report.evidence if e.capability == "input_window")
        self.assertEqual(input_ev.status, "partial")

    def test_negative_screen_coords_fails_display_qualification(self) -> None:
        probe = self._valid_probe()
        probe["observations"]["page"]["display"]["screenX"] = -1
        report = qualify_probe(self.persona, self.snapshot, probe, catalog=self.catalog)
        self.assertFalse(report.passed)
        display_ev = next(e for e in report.evidence if e.capability == "display_window")
        self.assertEqual(display_ev.status, "partial")

    def test_portrait_orientation_fails_display_qualification(self) -> None:
        probe = self._valid_probe()
        probe["observations"]["page"]["display"]["orientation"] = {"type": "portrait-primary", "angle": 0}
        report = qualify_probe(self.persona, self.snapshot, probe, catalog=self.catalog)
        self.assertFalse(report.passed)
        display_ev = next(e for e in report.evidence if e.capability == "display_window")
        self.assertEqual(display_ev.status, "partial")

    def test_language_mismatch_fails_locale_qualification(self) -> None:
        probe = self._valid_probe()
        probe["observations"]["page"]["window"]["language"] = "fr-FR"
        report = qualify_probe(self.persona, self.snapshot, probe, catalog=self.catalog)
        self.assertFalse(report.passed)
        locale_ev = next(e for e in report.evidence if e.capability == "locale_window_worker_http")
        self.assertEqual(locale_ev.status, "partial")

    def test_worker_language_mismatch_fails_locale_qualification(self) -> None:
        probe = self._valid_probe()
        probe["observations"]["page"]["workers"]["shared"]["language"] = "de-DE"
        report = qualify_probe(self.persona, self.snapshot, probe, catalog=self.catalog)
        self.assertFalse(report.passed)
        locale_ev = next(e for e in report.evidence if e.capability == "locale_window_worker_http")
        self.assertEqual(locale_ev.status, "partial")


if __name__ == "__main__":
    unittest.main()
