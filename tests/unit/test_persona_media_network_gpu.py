import unittest
from unittest import mock
from src.persona.model import CapabilitySnapshot, TemplateCatalog, PersonaTemplate, Persona
from src.persona.qualification import qualify_probe
from src.persona.runtime import firefox_settings


class MediaAndWebRtcQualificationTests(unittest.TestCase):
    def setUp(self):
        self.catalog = TemplateCatalog.default()
        self.snapshot = CapabilitySnapshot.from_mapping({
            "environment": {"backend": "software", "firefox_version": "140.0"},
            "capabilities": {"webgl": {"vendor": "Mesa", "renderer": "llvmpipe"}},
        })

    def _base_probe_report(self):
        return {
            "page": {
                "window": {
                    "userAgent": "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0",
                    "platform": "Linux x86_64", "oscpu": "Linux x86_64", "appVersion": "5.0 (X11)",
                    "hardwareConcurrency": 4, "timezone": "UTC", "languages": ["en-US", "en"],
                    "innerWidth": 1280, "innerHeight": 800,
                },
                "display": {
                    "screen": {"width": 1280, "height": 800, "availWidth": 1280, "availHeight": 800, "colorDepth": 24, "pixelDepth": 24},
                    "viewport": {"innerWidth": 1280, "innerHeight": 800},
                    "devicePixelRatio": 1.0,
                },
                "mediaDevices": {
                    "supported": True, "videoInputs": 0, "audioInputs": 0, "audioOutputs": 0,
                    "labelsExposed": False, "devices": [],
                },
                "webrtc": {"supported": True, "dataChannel": True, "error": None},
                "webgl": {"supported": True, "vendor": "Mesa", "renderer": "llvmpipe", "behavior": {"passed": True}},
                "canvas": {"supported": True},
                "audio": {"supported": True},
                "privacy": {"doNotTrack": "unspecified", "globalPrivacyControl": False},
                "storage": {"localStorage": True, "sessionStorage": True, "indexedDB": True, "caches": True},
                "network": {"onLine": True},
            },
            "http": {
                "document": {"Accept-Language": "en-US,en;q=0.9", "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0"},
                "worker": {"Accept-Language": "en-US,en;q=0.9"},
            },
            "workers": {
                "dedicated": {"userAgent": "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0", "platform": "Linux x86_64", "hardwareConcurrency": 4, "timezone": "UTC", "languages": ["en-US", "en"], "onLine": True, "probeComplete": True},
                "shared": {"userAgent": "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0", "platform": "Linux x86_64", "hardwareConcurrency": 4, "timezone": "UTC", "languages": ["en-US", "en"], "onLine": True, "probeComplete": True},
                "service": {"userAgent": "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0", "platform": "Linux x86_64", "hardwareConcurrency": 4, "timezone": "UTC", "languages": ["en-US", "en"], "onLine": True, "probeComplete": True},
            },
        }

    def test_media_devices_zero_hardware_with_unsupported_claim_passes(self):
        template = PersonaTemplate(
            template_id="custom-media-test-v1",
            version="1.0.0",
            status="candidate",
            strict_eligible=False,
            metadata={"phase": 2},
            base_config={
                "browser": {"family": "firefox", "platform": "Linux x86_64", "oscpu": "Linux x86_64", "app_version": "5.0 (X11)", "firefox_version": "140.0", "user_agent": "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0"},
                "media": {
                    "fixture_set": "native-codecs-v1",
                    "codecs": ["h264", "vp8", "vp9", "av1", "aac", "opus"],
                    "scope": "native-decode-playback",
                    "physical_input": "unsupported",
                    "physical_output": "not_verified",
                    "webrtc": "not_verified",
                },
            },
            required_capabilities=["media_devices_and_webrtc"],
            variants=[{
                "variant_id": "var1",
                "cpu": {"hardware_concurrency": 4},
                "display": {"window_policy": "maximized", "viewport_policy": "derived", "screen_width": 1280, "screen_height": 800, "avail_width": 1280, "avail_height": 800, "device_pixel_ratio": 1.0, "color_depth": 24, "pixel_depth": 24, "orientation": "landscape"},
                "locale": {"locale": "en-US", "languages": ["en-US", "en"], "accept_language": "en-US,en;q=0.9", "timezone": "UTC"},
            }],
        )
        catalog = TemplateCatalog([template])
        config = template.expand(0, "140.0")
        persona = Persona.build(seed=1, template=template, final_config=config, snapshot=self.snapshot, experimental=True)
        report = qualify_probe(persona, self.snapshot, self._base_probe_report(), catalog=catalog)
        
        media_evidence = next(ev for ev in report.evidence if ev.capability == "media_devices_and_webrtc")
        self.assertEqual(media_evidence.status, "supported")

    def test_webrtc_prefs_disabled_sets_firefox_pref(self):
        config = {
            "locale": {"timezone": "UTC", "languages": ["en-US"], "locale": "en-US"},
            "cpu": {"hardware_concurrency": 4},
            "display": {"device_pixel_ratio": 1.0},
            "media": {
                "fixture_set": "native-codecs-v1",
                "codecs": ["h264", "vp8", "vp9", "av1", "aac", "opus"],
                "scope": "native-decode-playback",
                "physical_input": "unsupported",
                "physical_output": "not_verified",
                "webrtc": "disabled",
            },
        }
        prefs, _ = firefox_settings(config)
        self.assertFalse(prefs["media.peerconnection.enabled"])
        self.assertFalse(prefs["media.navigator.streams.fake"])


class NetworkTrafficShapingTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = CapabilitySnapshot.from_mapping({
            "environment": {"backend": "software", "firefox_version": "140.0"},
            "capabilities": {},
        })

    def _make_shaping_persona(self, mode="kernel"):
        template = PersonaTemplate(
            template_id="custom-shaping-test-v1",
            version="1.0.0",
            status="candidate",
            strict_eligible=False,
            metadata={"phase": 3},
            base_config={
                "browser": {"family": "firefox", "platform": "Linux x86_64", "oscpu": "Linux x86_64", "app_version": "5.0 (X11)", "firefox_version": "140.0", "user_agent": "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0"},
                "network": {
                    "online": True,
                    "shaping": {"mode": mode, "latency_ms": 100, "packet_loss_rate": 0.05},
                },
            },
            required_capabilities=["network_traffic_shaping"],
            variants=[{
                "variant_id": "var1",
                "cpu": {"hardware_concurrency": 4},
                "display": {"window_policy": "maximized", "viewport_policy": "derived", "screen_width": 1280, "screen_height": 800, "avail_width": 1280, "avail_height": 800, "device_pixel_ratio": 1.0, "color_depth": 24, "pixel_depth": 24, "orientation": "landscape"},
                "locale": {"locale": "en-US", "languages": ["en-US", "en"], "accept_language": "en-US,en;q=0.9", "timezone": "UTC"},
            }],
        )
        catalog = TemplateCatalog([template])
        config = template.expand(0, "140.0")
        persona = Persona.build(seed=2, template=template, final_config=config, snapshot=self.snapshot, experimental=True)
        return persona, catalog

    def test_kernel_shaping_in_non_root_termux_is_explicitly_unsupported(self):
        persona, catalog = self._make_shaping_persona(mode="kernel")
        probe_report = {"page": {}, "http": {}, "workers": {}}

        # In non-root Termux, os.getuid() != 0
        with mock.patch("os.getuid", return_value=10234), \
             mock.patch("shutil.which", return_value=None):
            report = qualify_probe(persona, self.snapshot, probe_report, catalog=catalog)
            shaping_ev = next(ev for ev in report.evidence if ev.capability == "network_traffic_shaping")
            self.assertEqual(shaping_ev.status, "unsupported")
            self.assertTrue(any("CAP_NET_ADMIN" in p or "root" in p for p in shaping_ev.proof))

    def test_disabled_or_unsupported_shaping_claim_passes_qualification(self):
        persona, catalog = self._make_shaping_persona(mode="unsupported")
        probe_report = {"page": {}, "http": {}, "workers": {}}

        report = qualify_probe(persona, self.snapshot, probe_report, catalog=catalog)
        shaping_ev = next(ev for ev in report.evidence if ev.capability == "network_traffic_shaping")
        self.assertEqual(shaping_ev.status, "supported")


class GpuDriverStackCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.catalog = TemplateCatalog.default()
        self.snapshot = CapabilitySnapshot.from_mapping({
            "environment": {"backend": "software", "firefox_version": "140.0"},
            "capabilities": {},
        })

    def test_igpu_candidate_on_software_backend_is_unsupported(self):
        template = self.catalog.get("linux-firefox-igpu-candidate-phase0")
        config = template.expand(0, "140.0")
        persona = Persona.build(seed=3, template=template, final_config=config, snapshot=self.snapshot, experimental=True)
        report_data = {
            "page": {
                "window": {"userAgent": "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0", "platform": "Linux x86_64", "oscpu": "Linux x86_64", "appVersion": "5.0 (X11)", "hardwareConcurrency": 2, "timezone": "America/New_York", "languages": ["en-US", "en"], "innerWidth": 1280, "innerHeight": 800},
                "display": {"screen": {"width": 1280, "height": 800, "availWidth": 1280, "availHeight": 800, "colorDepth": 24, "pixelDepth": 24}, "viewport": {"innerWidth": 1280, "innerHeight": 800}, "devicePixelRatio": 1.0},
                "webgl": {"supported": True, "renderer": "llvmpipe (LLVM 19.1.1, 256 bits)", "behavior": {"passed": True}},
            },
            "http": {"document": {"Accept-Language": "en-US,en;q=0.9"}, "worker": {"Accept-Language": "en-US,en;q=0.9"}},
            "workers": {"dedicated": {"hardwareConcurrency": 2, "timezone": "America/New_York", "languages": ["en-US", "en"], "probeComplete": True}, "shared": {"hardwareConcurrency": 2, "timezone": "America/New_York", "languages": ["en-US", "en"], "probeComplete": True}, "service": {"hardwareConcurrency": 2, "timezone": "America/New_York", "languages": ["en-US", "en"], "probeComplete": True}},
        }
        report = qualify_probe(persona, self.snapshot, report_data, catalog=self.catalog)
        gfx_ev = next(ev for ev in report.evidence if ev.capability == "graphics_full_combination")
        self.assertEqual(gfx_ev.status, "unsupported")
        self.assertTrue(any("software rasterizer" in str(r) for r in report.reasons))


if __name__ == "__main__":
    unittest.main()
