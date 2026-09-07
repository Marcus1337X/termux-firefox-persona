"""Native settings and startup override contract, without launching Firefox."""
import unittest
from unittest.mock import AsyncMock

from src.persona.runtime import apply_browser_overrides, firefox_settings


class SettingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_geolocation_is_installed_for_future_tabs_without_granting_permission(self):
        bidi = AsyncMock()
        coordinates = {"latitude": 31.2304, "longitude": 121.4737, "accuracy": 50}
        result = await apply_browser_overrides(bidi, {"geolocation": {**coordinates, "permission": "prompt"}})
        bidi.send.assert_awaited_once_with("emulation.setGeolocationOverride", {
            "coordinates": coordinates, "userContexts": ["default"],
        })
        self.assertEqual(result["geolocation"]["permission_policy"], "native-site-consent")

    async def test_unsupported_command_aborts_startup(self):
        bidi = AsyncMock()
        bidi.send.side_effect = RuntimeError("unknown command")
        with self.assertRaisesRegex(RuntimeError, "unknown command"):
            await apply_browser_overrides(bidi, {"geolocation": {
                "latitude": 40.7128, "longitude": -74.0060, "accuracy": 50}})

    async def test_older_persona_does_not_acquire_new_overrides(self):
        bidi = AsyncMock()
        self.assertEqual(await apply_browser_overrides(bidi, {}), {})
        bidi.send.assert_not_awaited()

    def test_region_appearance_uses_native_prefs_and_leaves_permission_defaults(self):
        config = {"locale": {"timezone": "America/New_York", "languages": ["en-US", "en"],
                             "locale": "en-US"}, "cpu": {"hardware_concurrency": 2},
                  "display": {"device_pixel_ratio": 1},
                  "appearance": {"color_scheme": "dark", "reduced_motion": True,
                                 "contrast": "no-preference", "forced_colors": False}}
        prefs, env = firefox_settings(config)
        self.assertEqual(env["TZ"], "America/New_York")
        self.assertEqual(prefs["ui.systemUsesDarkTheme"], 1)
        self.assertEqual(prefs["ui.prefersReducedMotion"], 1)
        self.assertEqual(prefs["browser.display.document_color_use"], 1)
        self.assertFalse(any("permission" in key or "geo.prompt" in key for key in prefs))
        self.assertNotIn("gfx.x11-egl.force-disabled", prefs)
        config["graphics"] = {"context_backend": "glx"}
        glx_prefs, _ = firefox_settings(config)
        self.assertIs(glx_prefs["gfx.x11-egl.force-disabled"], True)


if __name__ == "__main__":
    unittest.main()
