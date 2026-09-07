import copy
from dataclasses import replace
import unittest

from src.persona import (
    CapabilityEvidence,
    CapabilitySnapshot,
    NoEligiblePersonaError,
    Persona,
    PersonaGenerator,
    QualificationError,
    TemplateCatalog,
    PersonaError,
    PersonaTemplate,
    QualificationReport,
    TemplateError,
    SchemaVersionError,
    IncompatibleVersionError,
)


class PersonaModelTests(unittest.TestCase):
    def setUp(self):
        self.catalog = TemplateCatalog.default()
        self.snapshot = CapabilitySnapshot(
            environment={
                "firefox_version": "154.0.1",
                "platform": "Linux x86_64",
                "backend": "software/native",
                "host_arch": "aarch64",
            },
            capabilities={},
        )

    def test_experimental_generation_is_reproducible_but_ids_are_unique(self):
        generator = PersonaGenerator(
            self.catalog,
            self.snapshot,
            runtime_browser_version="154.0.1",
        )
        first = generator.create(seed=20260907, experimental=True)
        second = generator.create(seed=20260907, experimental=True)
        self.assertEqual(first.final_config, second.final_config)
        self.assertEqual(first.seed, second.seed)
        self.assertNotEqual(first.persona_id, second.persona_id)
        self.assertEqual(
            first.final_config["browser"]["user_agent"],
            "Mozilla/5.0 (X11; Linux x86_64; rv:154.0) Gecko/20100101 Firefox/154.0",
        )

    def test_strict_creation_requires_exact_promoted_report(self):
        generator = PersonaGenerator(
            self.catalog,
            self.snapshot,
            runtime_browser_version="154.0.1",
        )
        with self.assertRaises(NoEligiblePersonaError):
            generator.create(seed=9)

        experimental = generator.create(seed=9, experimental=True)
        template = self.catalog.get(experimental.template_id)
        evidence = [
            CapabilityEvidence(
                capability=name,
                status="supported",
                environment_fingerprint=self.snapshot.fingerprint,
                observed={"passed": True},
                requested={"combination": experimental.final_config},
                contexts=("window", "worker", "http"),
                proof=("unit-test",),
            )
            for name in template.required_capabilities
        ]
        report = self.catalog.qualify(
            template.template_id,
            experimental.final_config,
            self.snapshot,
            evidence,
        )
        self.assertTrue(report.passed)
        self.catalog.promote(report)
        strict = PersonaGenerator(
            self.catalog,
            self.snapshot,
            runtime_browser_version="154.0.1",
        ).create(seed=9)
        self.assertFalse(strict.experimental)
        self.assertEqual(strict.final_config, experimental.final_config)

    def test_partial_evidence_never_passes(self):
        experimental = PersonaGenerator(
            self.catalog,
            self.snapshot,
            runtime_browser_version="154.0.1",
        ).create(seed=2, experimental=True)
        template = self.catalog.get(experimental.template_id)
        evidence = [
            CapabilityEvidence(
                capability=template.required_capabilities[0],
                status="supported",
                environment_fingerprint=self.snapshot.fingerprint,
            )
        ]
        report = self.catalog.qualify(
            template.template_id,
            experimental.final_config,
            self.snapshot,
            evidence,
        )
        self.assertFalse(report.passed)
        self.assertTrue(any("missing evidence" in reason for reason in report.reasons))
        with self.assertRaises(QualificationError):
            self.catalog.promote(report)

    def test_wrong_environment_evidence_is_not_usable(self):
        experimental = PersonaGenerator(
            self.catalog,
            self.snapshot,
            runtime_browser_version="154.0.1",
        ).create(seed=3, experimental=True)
        template = self.catalog.get(experimental.template_id)
        wrong = CapabilitySnapshot(environment={"firefox_version": "other"}, capabilities={})
        evidence = [
            CapabilityEvidence(name, "supported", wrong.fingerprint)
            for name in template.required_capabilities
        ]
        report = self.catalog.qualify(template.template_id, experimental.config, self.snapshot, evidence)
        self.assertFalse(report.passed)
        self.assertTrue(any("environment mismatch" in reason for reason in report.reasons))

    def test_persona_round_trip_keeps_id_and_final_config(self):
        persona = PersonaGenerator(self.catalog).create(seed=4, experimental=True)
        restored = Persona.from_mapping(persona.to_dict())
        self.assertEqual(restored.persona_id, persona.persona_id)
        self.assertEqual(restored.final_config, persona.final_config)


class QualificationAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.catalog = TemplateCatalog.default()
        self.template = self.catalog.get("linux-firefox-native-phase0")
        self.snapshot = CapabilitySnapshot(environment={"firefox_version": "154.0.1"}, capabilities={})
        self.config = self.template.expand(0, "154.0.1")
        # Structural fixtures only: passing these is not a device qualification.
        self.evidence = tuple(CapabilityEvidence(
            name, "supported", self.snapshot.fingerprint,
            observed={"measured": True}, requested={"expected": self.config},
            contexts=("window",), proof=("unit-test fixture",),
        ) for name in self.template.required_capabilities)

    def report(self, evidence=None, config=None):
        return QualificationReport.build(self.template, config or self.config, self.snapshot,
                                         self.evidence if evidence is None else evidence)

    def test_supported_record_cannot_hide_partial_duplicate(self):
        report = self.report((*self.evidence, replace(self.evidence[0], status="partial")))
        self.assertFalse(report.passed)
        forged = replace(report, passed=True)
        self.assertFalse(forged.usable_for(self.template, self.config, self.snapshot))
        with self.assertRaises(QualificationError):
            self.catalog.promote(forged)

    def test_contradictory_supported_measurements_are_rejected(self):
        conflicting = replace(self.evidence[0], observed={"measured": False})
        report = self.report((*self.evidence, conflicting))
        self.assertFalse(report.passed)
        with self.assertRaises(QualificationError):
            self.catalog.promote(replace(report, passed=True))

    def test_claimed_pass_cannot_replace_missing_evidence(self):
        report = replace(self.report(()), passed=True)
        self.assertFalse(report.usable_for(self.template, self.config, self.snapshot))
        with self.assertRaises(QualificationError):
            self.catalog.promote(report.to_dict())

    def test_incomplete_evidence_never_enters_pool(self):
        for field, value in (("proof", ()), ("contexts", ()), ("proof", ("",)),
                             ("observed", {}), ("requested", None),
                             ("environment_fingerprint", "different")):
            with self.subTest(field=field, value=value):
                evidence = (replace(self.evidence[0], **{field: value}), *self.evidence[1:])
                report = self.report(evidence)
                self.assertFalse(report.passed)
                with self.assertRaises(QualificationError):
                    self.catalog.promote(replace(report, passed=True))

    def test_different_requested_and_observed_structures_are_valid(self):
        report = self.report()
        self.assertTrue(report.passed)
        self.catalog.promote(report)
        self.assertTrue(report.usable_for(self.template, self.config, self.snapshot))

    def test_full_config_must_equal_declared_template_variant(self):
        for section, key, value in (("cpu", "hardware_concurrency", 6),
                                    ("browser", "user_agent", "different browser")):
            with self.subTest(key=key):
                config = copy.deepcopy(self.config)
                config[section][key] = value
                report = self.report(config=config)
                self.assertFalse(report.passed)
                forged = replace(report, passed=True)
                self.assertFalse(forged.usable_for(self.template, config, self.snapshot))
                with self.assertRaises(QualificationError):
                    self.catalog.promote(forged)

    def test_disabled_template_rejects_old_valid_report(self):
        report = self.report()
        disabled = replace(self.template, status="disabled")
        catalog = TemplateCatalog([disabled])
        with self.assertRaises(QualificationError):
            catalog.promote(report)
        self.assertFalse(report.usable_for(disabled, self.config, self.snapshot))
        with self.assertRaises(NoEligiblePersonaError):
            PersonaGenerator(catalog, self.snapshot).create(seed=1, experimental=True)

    def test_restored_persona_cannot_borrow_other_combination_qualification(self):
        persona = Persona.build(seed=1, template=self.template, final_config=self.config,
                                snapshot=self.snapshot, experimental=False, qualification=self.report())
        data = persona.to_dict()
        data["final_config"] = self.template.expand(1, "154.0.1")
        with self.assertRaisesRegex(QualificationError, "combination"):
            Persona.from_mapping(data)
        data = persona.to_dict()
        data["capability_snapshot_fingerprint"] = "other-environment"
        with self.assertRaises(QualificationError):
            Persona.from_mapping(data)

    def test_incompatible_persona_and_report_versions_are_rejected(self):
        persona = Persona.build(seed=1, template=self.template, final_config=self.config,
                                snapshot=self.snapshot, experimental=False, qualification=self.report())
        for cls, mapping in ((Persona, persona.to_dict()), (QualificationReport, self.report().to_dict())):
            for key, value, error in (("schema_version", 99, SchemaVersionError),
                                      ("generator_version", "2.0.0", IncompatibleVersionError)):
                with self.subTest(cls=cls, key=key):
                    with self.assertRaises(error):
                        cls.from_mapping({**mapping, key: value})

    def test_invalid_config_is_rejected_in_templates_and_persisted_personas(self):
        persona = Persona.build(seed=1, template=self.template, final_config=self.config,
                                snapshot=self.snapshot, experimental=True)
        cases = [("cpu", "hardware_concurrency", True),
                 ("cpu", "hardware_concurrency", 65),
                 ("display", "screen_width", True),
                 ("display", "screen_width", 7681),
                 ("display", "screen_height", 4321),
                 ("display", "device_pixel_ratio", True),
                 ("display", "device_pixel_ratio", float("nan")),
                 ("display", "device_pixel_ratio", float("inf")),
                 ("display", "window_width", True),
                 ("locale", "timezone", "Invalid/Nowhere"),
                 ("locale", "languages", ["en-US", "zh-CN"]),
                 ("locale", "accept_language", "en-US,zh-CN;q=0.9")]
        for section, key, value in cases:
            with self.subTest(key=key, value=value):
                data = persona.to_dict()
                data["final_config"][section][key] = value
                with self.assertRaises(TemplateError):
                    Persona.from_mapping(data)
                template_data = self.template.to_dict()
                template_data["variants"][0].setdefault(section, {})[key] = value
                with self.assertRaises(TemplateError):
                    PersonaTemplate.from_mapping(template_data)

    def test_bool_seed_and_non_boolean_experimental_are_rejected_on_load(self):
        persona = Persona.build(seed=1, template=self.template, final_config=self.config,
                                snapshot=self.snapshot, experimental=True)
        for key, value in (("seed", True), ("experimental", "false")):
            with self.subTest(key=key), self.assertRaises(PersonaError):
                Persona.from_mapping({**persona.to_dict(), key: value})


class RegionAppearanceTemplateTests(unittest.TestCase):
    def setUp(self):
        self.template = TemplateCatalog.default().get("linux-firefox-region-appearance-v1")

    def test_four_candidate_combinations_require_new_capabilities(self):
        self.assertEqual(self.template.version, "1.0.0")
        self.assertEqual(self.template.status, "candidate")
        self.assertEqual(len(self.template.variants), 4)
        self.assertIn("geolocation_window", self.template.required_capabilities)
        self.assertIn("appearance_window", self.template.required_capabilities)
        seen = set()
        for index in range(4):
            config = self.template.expand(index, "154.0.1")
            seen.add((config["locale"]["locale"], config["appearance"]["color_scheme"]))
            self.assertEqual(config["geolocation"]["permission"], "prompt")
            self.assertEqual(config["geolocation"]["accuracy"], 50)
            self.assertEqual(config["appearance"]["reduced_motion"],
                             config["appearance"]["color_scheme"] == "dark")
        self.assertEqual(seen, {(locale, scheme) for locale in ("zh-CN", "en-US")
                               for scheme in ("light", "dark")})
        snapshot = CapabilitySnapshot(environment={"firefox_version": "154.0.1"}, capabilities={})
        generator = PersonaGenerator(TemplateCatalog([self.template]), snapshot,
                                     runtime_browser_version="154.0.1")
        with self.assertRaises(NoEligiblePersonaError):
            generator.create(seed=1)
        first = generator.create(seed=1, experimental=True)
        self.assertEqual(Persona.from_mapping(first.to_dict()).final_config, first.final_config)

    def test_invalid_geolocation_or_appearance_is_rejected(self):
        cases = [("geolocation", "latitude", True), ("geolocation", "longitude", float("nan")),
                 ("geolocation", "accuracy", float("inf")), ("geolocation", "latitude", 91),
                 ("geolocation", "longitude", -181), ("geolocation", "accuracy", -1),
                 ("geolocation", "permission", "allow"),
                 ("appearance", "color_scheme", "automatic"),
                 ("appearance", "contrast", "high"), ("appearance", "forced_colors", 0),
                 ("appearance", "reduced_motion", "false")]
        for section, key, value in cases:
            with self.subTest(section=section, key=key, value=value):
                data = self.template.to_dict()
                data["variants"][0][section][key] = value
                with self.assertRaises(TemplateError):
                    PersonaTemplate.from_mapping(data)

    def test_cross_region_coordinates_and_timezone_are_rejected(self):
        for replacement in ({"geolocation": {"latitude": 40.7128, "longitude": -74.0060,
                                              "accuracy": 50, "permission": "prompt"}},
                            {"timezone": "America/New_York"}):
            with self.subTest(replacement=replacement):
                data = self.template.to_dict()
                if "geolocation" in replacement:
                    data["variants"][0]["geolocation"] = replacement["geolocation"]
                else:
                    data["variants"][0]["locale"]["timezone"] = replacement["timezone"]
                with self.assertRaisesRegex(TemplateError, "locale region"):
                    PersonaTemplate.from_mapping(data)

    def test_missing_geolocation_cannot_be_loaded_as_regional_persona(self):
        persona = PersonaGenerator(TemplateCatalog([self.template]),
                                   runtime_browser_version="154.0.1").create(seed=1, experimental=True)
        data = persona.to_dict()
        del data["final_config"]["geolocation"]
        with self.assertRaises(TemplateError):
            Persona.from_mapping(data)


class SoftwareGlxTemplateTests(unittest.TestCase):
    def setUp(self):
        self.template = TemplateCatalog.default().get("linux-firefox-software-glx-v1")

    def test_software_glx_family_remains_unqualified_and_narrowly_scoped(self):
        self.assertEqual(self.template.version, "1.0.0")
        self.assertEqual(self.template.status, "candidate")
        self.assertEqual(len(self.template.variants), 4)
        self.assertIn("webgl_window", self.template.required_capabilities)
        self.assertEqual(len(self.template.required_capabilities), 7)
        for index in range(4):
            config = self.template.expand(index, "154.0.1")
            self.assertEqual(config["graphics"]["hardware_class"], "software")
            self.assertEqual(config["graphics"]["context_backend"], "glx")
            self.assertEqual(config["graphics"]["renderer"], "llvmpipe, or similar")
        snapshot = CapabilitySnapshot(environment={"firefox_version": "154.0.1"}, capabilities={})
        generator = PersonaGenerator(TemplateCatalog([self.template]), snapshot,
                                     runtime_browser_version="154.0.1")
        with self.assertRaises(NoEligiblePersonaError):
            generator.create(seed=1)

    def test_graphics_cannot_claim_another_backend_or_hardware_identity(self):
        for key, value in (("execution_backend", "native"), ("context_backend", "egl"),
                           ("vendor", "Intel"), ("renderer", "Intel UHD Graphics"),
                           ("hardware_class", "integrated-gpu"), ("webgl1", 1),
                           ("webgl2", False)):
            with self.subTest(key=key):
                data = self.template.to_dict()
                data["base_config"]["graphics"][key] = value
                with self.assertRaisesRegex(TemplateError, "software GLX"):
                    PersonaTemplate.from_mapping(data)

    def test_software_glx_retains_region_constraints(self):
        data = self.template.to_dict()
        data["variants"][0]["geolocation"]["latitude"] = 40.7128
        with self.assertRaisesRegex(TemplateError, "locale region"):
            PersonaTemplate.from_mapping(data)


if __name__ == "__main__":
    unittest.main()
