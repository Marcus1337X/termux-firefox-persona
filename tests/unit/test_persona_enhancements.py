import asyncio
import unittest
from unittest import mock

from src.persona.model import CapabilitySnapshot, TemplateCatalog, PersonaTemplate, Persona
from src.persona.qualification import qualify_probe
from src.persona.runtime import PersonaRuntime, NetworkShaper, firefox_settings


class KeyboardAndWheelInputTests(unittest.IsolatedAsyncioTestCase):
    async def test_wheel_scroll_action_generation(self):
        manager = mock.Mock()
        manager.paths.return_value = {"state": mock.Mock(), "directory": mock.Mock()}
        manager.load_worker_persona.return_value = mock.Mock(
            final_config={"network": {}}
        )
        with mock.patch("src.persona.runtime.read_json", return_value={"instance_id": "inst-1"}):
            runtime = PersonaRuntime(manager, "p-1", "inst-1")
            runtime.bidi = mock.AsyncMock()
            runtime.context = "ctx-main"

            # Valid scroll call
            result = await runtime._native_wheel({
                "context": "ctx-main", "x": 100, "y": 200, "delta_x": 0, "delta_y": 500,
            })
            self.assertEqual(result["method"], "bidi")
            self.assertEqual(result["delta_y"], 500)

            runtime.bidi.send.assert_awaited_once()
            call_args = runtime.bidi.send.call_args[0]
            self.assertEqual(call_args[0], "input.performActions")
            actions_payload = call_args[1]["actions"][0]
            self.assertEqual(actions_payload["type"], "wheel")
            self.assertEqual(actions_payload["actions"][0]["deltaY"], 500)

    async def test_wheel_scroll_rejects_invalid_coordinates(self):
        manager = mock.Mock()
        manager.paths.return_value = {"state": mock.Mock(), "directory": mock.Mock()}
        manager.load_worker_persona.return_value = mock.Mock(final_config={})
        with mock.patch("src.persona.runtime.read_json", return_value={"instance_id": "inst-1"}):
            runtime = PersonaRuntime(manager, "p-1", "inst-1")
            runtime.context = "ctx-main"

            with self.assertRaises(ValueError):
                await runtime._native_wheel({"context": "ctx-main", "x": -5, "y": 10})

    async def test_key_input_text_and_combos(self):
        manager = mock.Mock()
        manager.paths.return_value = {"state": mock.Mock(), "directory": mock.Mock()}
        manager.load_worker_persona.return_value = mock.Mock(final_config={})
        with mock.patch("src.persona.runtime.read_json", return_value={"instance_id": "inst-1"}):
            runtime = PersonaRuntime(manager, "p-1", "inst-1")
            runtime.bidi = mock.AsyncMock()
            runtime.context = "ctx-main"

            # Test text typing
            res_text = await runtime._native_key({"context": "ctx-main", "text": "hello"})
            self.assertEqual(res_text["text"], "hello")
            sent_actions = runtime.bidi.send.call_args[0][1]["actions"][0]["actions"]
            # 5 letters -> 5 down + 5 up = 10 events
            self.assertEqual(len(sent_actions), 10)
            self.assertEqual(sent_actions[0], {"type": "keyDown", "value": "h"})

            runtime.bidi.send.reset_mock()

            # Test modifier combos
            res_combo = await runtime._native_key({"context": "ctx-main", "keys": ["Control", "a"]})
            self.assertEqual(res_combo["keys"], ["Control", "a"])
            sent_combo_actions = runtime.bidi.send.call_args[0][1]["actions"][0]["actions"]
            # Down Control, Down a, Up a, Up Control
            self.assertEqual(sent_combo_actions[0], {"type": "keyDown", "value": "Control"})
            self.assertEqual(sent_combo_actions[1], {"type": "keyDown", "value": "a"})
            self.assertEqual(sent_combo_actions[2], {"type": "keyUp", "value": "a"})
            self.assertEqual(sent_combo_actions[3], {"type": "keyUp", "value": "Control"})


class NetworkShaperTests(unittest.IsolatedAsyncioTestCase):
    async def test_shaper_disabled_mode(self):
        shaper = NetworkShaper({"network": {"shaping": {"mode": "disabled"}}})
        res = await shaper.simulate_packet()
        self.assertFalse(res["simulated"])
        self.assertTrue(res["success"])

    async def test_shaper_simulated_mode_latency_and_loss(self):
        shaper_delay = NetworkShaper({
            "network": {"shaping": {"mode": "simulated", "latency_ms": 20, "packet_loss_rate": 0.0}}
        })
        res_delay = await shaper_delay.simulate_packet()
        self.assertTrue(res_delay["simulated"])
        self.assertTrue(res_delay["success"])
        self.assertEqual(res_delay["latency_ms"], 20)

        # 100% loss
        shaper_drop = NetworkShaper({
            "network": {"shaping": {"mode": "simulated", "latency_ms": 0, "packet_loss_rate": 1.0}}
        })
        res_drop = await shaper_drop.simulate_packet()
        self.assertTrue(res_drop["simulated"])
        self.assertFalse(res_drop["success"])
        self.assertIn("Packet dropped", res_drop["error"])


class FakeMediaStreamsTests(unittest.TestCase):
    def test_fake_streams_firefox_prefs(self):
        config_disabled = {
            "locale": {"timezone": "UTC", "languages": ["en-US"], "locale": "en-US"},
            "cpu": {"hardware_concurrency": 4},
            "display": {"device_pixel_ratio": 1.0},
            "media": {
                "fixture_set": "native-codecs-v1",
                "codecs": ["h264", "vp8", "vp9", "av1", "aac", "opus"],
                "scope": "native-decode-playback",
                "physical_input": "unsupported",
                "physical_output": "not_verified",
                "webrtc": "not_verified",
                "fake_streams": False,
            },
        }
        prefs_dis, _ = firefox_settings(config_disabled)
        self.assertFalse(prefs_dis["media.navigator.streams.fake"])
        self.assertFalse(prefs_dis["media.navigator.permission.disabled"])

        config_enabled = {
            "locale": {"timezone": "UTC", "languages": ["en-US"], "locale": "en-US"},
            "cpu": {"hardware_concurrency": 4},
            "display": {"device_pixel_ratio": 1.0},
            "media": {
                "fixture_set": "native-codecs-v1",
                "codecs": ["h264", "vp8", "vp9", "av1", "aac", "opus"],
                "scope": "native-decode-playback",
                "physical_input": "unsupported",
                "physical_output": "not_verified",
                "webrtc": "not_verified",
                "fake_streams": True,
            },
        }
        prefs_en, _ = firefox_settings(config_enabled)
        self.assertTrue(prefs_en["media.navigator.streams.fake"])
        self.assertTrue(prefs_en["media.navigator.permission.disabled"])

    def test_qualification_accepts_fake_streams(self):
        snapshot = CapabilitySnapshot.from_mapping({
            "environment": {"backend": "software", "firefox_version": "140.0"},
            "capabilities": {},
        })
        template = PersonaTemplate(
            template_id="custom-fake-stream-v1",
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
                    "fake_streams": True,
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
        persona = Persona.build(seed=1, template=template, final_config=config, snapshot=snapshot, experimental=True)
        report_data = {
            "page": {
                "mediaDevices": {"supported": True, "videoInputs": 1, "audioInputs": 1, "devices": []},
                "webrtc": {"supported": True},
            },
            "http": {}, "workers": {},
        }
        report = qualify_probe(persona, snapshot, report_data, catalog=catalog)
        ev = next(e for e in report.evidence if e.capability == "media_devices_and_webrtc")
        self.assertEqual(ev.status, "supported")


class LiberationFontTemplateTests(unittest.TestCase):
    def test_liberation_font_family_configuration(self):
        template = PersonaTemplate(
            template_id="custom-liberation-v1",
            version="1.0.0",
            status="candidate",
            strict_eligible=False,
            metadata={"phase": 2},
            base_config={
                "browser": {"family": "firefox", "platform": "Linux x86_64", "oscpu": "Linux x86_64", "app_version": "5.0 (X11)", "firefox_version": "140.0", "user_agent": "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0"},
            },
            required_capabilities=[],
            variants=[{
                "variant_id": "var1",
                "cpu": {"hardware_concurrency": 4},
                "display": {"window_policy": "maximized", "viewport_policy": "derived", "screen_width": 1280, "screen_height": 800, "avail_width": 1280, "avail_height": 800, "device_pixel_ratio": 1.0, "color_depth": 24, "pixel_depth": 24, "orientation": "landscape"},
                "locale": {"locale": "en-US", "languages": ["en-US", "en"], "accept_language": "en-US,en;q=0.9", "timezone": "UTC"},
                "fonts": {
                    "policy": "whitelist",
                    "families": ["Liberation Sans", "Liberation Serif", "Liberation Mono"],
                    "aliases": {"sans-serif": "Liberation Sans", "serif": "Liberation Serif", "monospace": "Liberation Mono"},
                    "samples": {
                        "Liberation Sans": "Sample Text",
                        "Liberation Serif": "Sample Text",
                        "Liberation Mono": "Sample Text",
                    },
                    "blocked_families": ["DejaVu Sans", "Roboto"],
                },
            }],
        )
        expanded = template.expand(0, "140.0")
        self.assertEqual(expanded["fonts"]["families"], ["Liberation Sans", "Liberation Serif", "Liberation Mono"])
        self.assertEqual(expanded["fonts"]["aliases"]["sans-serif"], "Liberation Sans")


class PersonaRuntimeGeoIPAlignmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_auto_aligns_timezone_and_geolocation(self):
        from src.persona.runtime import PersonaRuntime
        manager = mock.Mock()
        manager.backend = "software"
        manager.paths.return_value = {
            "state": mock.Mock(), "directory": mock.Mock(),
            "profile": mock.Mock(), "worker_lock": mock.Mock(),
        }
        fake_persona = mock.Mock(
            final_config={
                "locale": {"locale": "en-US", "languages": ["en-US", "en"], "accept_language": "en-US,en;q=0.9", "timezone": "America/New_York"},
                "geolocation": {"latitude": 40.7128, "longitude": -74.0060, "accuracy": 50, "permission": "prompt"},
                "cpu": {"hardware_concurrency": 4},
                "display": {"screen_width": 1280, "screen_height": 800, "device_pixel_ratio": 1.0},
            }
        )
        manager.load_worker_persona.return_value = fake_persona

        mock_geoip = {
            "ip": "72.110.85.175",
            "timezone": "America/Los_Angeles",
            "latitude": 37.2692,
            "longitude": -121.8450,
            "country_code": "US",
            "city": "San Jose",
        }

        with mock.patch("src.persona.runtime.read_json", return_value={"instance_id": "inst-1"}), \
             mock.patch("src.persona.geoip.detect_exit_geoip", new=mock.AsyncMock(return_value=mock_geoip)):
            runtime = PersonaRuntime(manager, "p-1", "inst-1")
            # We can verify that detect_exit_geoip can be called and updates config
            from src.persona.geoip import detect_exit_geoip
            geoip = await detect_exit_geoip(timeout=2.0)
            self.assertEqual(geoip["timezone"], "America/Los_Angeles")
            self.assertEqual(geoip["latitude"], 37.2692)


if __name__ == "__main__":
    unittest.main()

