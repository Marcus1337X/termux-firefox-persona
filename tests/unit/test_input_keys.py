"""Keyboard chords produce routable key events, without a browser."""
import unittest
from unittest import mock

from src.input import InputCommands


class KeyInputTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.session = mock.Mock(send=mock.AsyncMock())
        self.commands = InputCommands(self.session)

    async def assert_events(self, key, expected, modifiers=0):
        self.session.send.reset_mock()
        await self.commands.press_key(key, modifiers=modifiers)
        self.assertEqual(self.session.send.await_args_list, [
            mock.call("Input.dispatchKeyEvent", {"type": "rawKeyDown", **expected}),
            mock.call("Input.dispatchKeyEvent", {"type": "keyUp", **expected}),
        ])

    async def test_control_new_window_aliases_resolve_to_key_n(self):
        for chord in ("ctrl+n", "Control+N", " CTRL + n "):
            with self.subTest(chord=chord):
                await self.assert_events(chord, {"key": "n", "code": "KeyN",
                    "windowsVirtualKeyCode": 78, "modifiers": 2})

    async def test_multi_modifier_chord_combines_explicit_bitmask(self):
        await self.assert_events("Alt+Shift+n", {"key": "N", "code": "KeyN",
            "windowsVirtualKeyCode": 78, "modifiers": 11}, modifiers=2)
        await self.assert_events("Meta+1", {"key": "1", "code": "Digit1",
            "windowsVirtualKeyCode": 49, "modifiers": 4})

    async def test_existing_special_keys_and_explicit_modifiers_are_preserved(self):
        await self.assert_events("Enter", {"key": "Enter", "code": "Enter",
            "windowsVirtualKeyCode": 13, "modifiers": 2}, modifiers=2)
        await self.assert_events("Shift+Tab", {"key": "Tab", "code": "Tab",
            "windowsVirtualKeyCode": 9, "modifiers": 8})
        await self.assert_events("Alt+ArrowLeft", {"key": "ArrowLeft", "code": "ArrowLeft",
            "windowsVirtualKeyCode": 37, "modifiers": 1})

    async def test_plus_is_a_literal_single_character(self):
        await self.assert_events("+", {"key": "+", "code": "Equal",
            "windowsVirtualKeyCode": 187, "modifiers": 0})

    async def test_invalid_input_never_dispatches_partial_chord(self):
        for key in ("", None, "ctrl+", "ctrl++n", "ctrl+ctrl+n", "n+ctrl",
                    "Hyper+n", "Ctrl+Shift", "UnknownKey"):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    await self.commands.press_key(key)
        for modifiers in (-1, 16, True, "2"):
            with self.subTest(modifiers=modifiers):
                with self.assertRaises(ValueError):
                    await self.commands.press_key("n", modifiers=modifiers)
        self.session.send.assert_not_awaited()
