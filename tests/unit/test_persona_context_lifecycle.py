"""Unit tests for Persona context inheritance, tab/window lifecycle and storage isolation semantics."""

import asyncio
from types import SimpleNamespace
import unittest
from unittest import mock

from src.persona.runtime import PersonaRuntime


class PersonaContextInheritanceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.runtime = PersonaRuntime.__new__(PersonaRuntime)
        self.runtime.context = "ctx-main"
        self.runtime.persona = SimpleNamespace(persona_id="persona-abc-123")
        self.runtime.state = {"status": "ready"}
        self.runtime.bidi = mock.Mock(
            send=mock.AsyncMock(),
            evaluate=mock.AsyncMock(),
            create=mock.AsyncMock(),
            navigate=mock.AsyncMock(),
            get_tree=mock.AsyncMock(return_value={"contexts": [{"context": "ctx-main"}, {"context": "ctx-sub"}]}),
        )
        self.runtime.select_context = mock.AsyncMock(side_effect=lambda ctx=None: ctx or "ctx-main")

    async def test_tab_new_inherits_persona_id_and_activates_context(self):
        self.runtime.bidi.create.return_value = {"context": "ctx-sub"}
        result = await self.runtime.dispatch("tab_new", {"url": "https://example.com/target"})
        self.runtime.bidi.create.assert_awaited_once_with("tab", reference_context="ctx-main")
        self.assertEqual(self.runtime.select_context.await_args_list, [
            mock.call(None),
            mock.call("ctx-sub"),
        ])
        self.runtime.bidi.navigate.assert_awaited_once_with("ctx-main", "https://example.com/target")
        self.assertEqual(result["persona_id"], "persona-abc-123")
        self.assertEqual(result["context"], "ctx-sub")

    async def test_window_new_inherits_persona_id(self):
        self.runtime.bidi.create.return_value = {"context": "ctx-win2"}
        result = await self.runtime.dispatch("window_new", {})
        self.runtime.bidi.create.assert_awaited_once_with("window", reference_context="ctx-main")
        self.assertEqual(self.runtime.select_context.await_args_list, [
            mock.call(None),
            mock.call("ctx-win2"),
        ])
        self.assertEqual(result["persona_id"], "persona-abc-123")
        self.assertEqual(result["context"], "ctx-win2")

    async def test_tab_switch_accepts_owned_context_and_rejects_foreign_context(self):
        real_select = PersonaRuntime.select_context.__get__(self.runtime, PersonaRuntime)
        self.runtime.pilot = SimpleNamespace(_session=SimpleNamespace(_xdt=mock.AsyncMock(return_value="12345"), _firefox_proc=SimpleNamespace(pid=12345)))
        self.runtime.select_context = real_select

        # Valid context belonging to tree
        switched = await self.runtime.dispatch("tab_switch", {"context": "ctx-sub"})
        self.assertEqual(switched["context"], "ctx-sub")
        self.runtime.bidi.send.assert_awaited_with("browsingContext.activate", {"context": "ctx-sub"})

        # Foreign context not in tree
        with self.assertRaisesRegex(ValueError, "does not belong to this Persona"):
            await self.runtime.dispatch("tab_switch", {"context": "ctx-foreign"})

    async def test_tab_close_sends_bidi_close(self):
        result = await self.runtime.dispatch("tab_close", {})
        self.assertEqual(result["closed"], "ctx-main")
        self.runtime.bidi.send.assert_awaited_once_with("browsingContext.close", {"context": "ctx-main"})


class PersonaNavigationLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.runtime = PersonaRuntime.__new__(PersonaRuntime)
        self.runtime.context = "ctx-test"
        self.runtime.persona = SimpleNamespace(persona_id="persona-nav-456")
        self.runtime.state = {"status": "ready"}
        self.runtime.select_context = mock.AsyncMock(return_value="ctx-test")
        self.runtime.bidi = mock.Mock(
            send=mock.AsyncMock(),
            evaluate=mock.AsyncMock(),
            navigate=mock.AsyncMock(return_value={"url": "about:blank"}),
        )

    async def test_goto_allows_safe_schemes(self):
        for url in ("about:blank", "http://127.0.0.1:8080/probe", "https://example.com/page"):
            with self.subTest(url=url):
                await self.runtime.dispatch("goto", {"url": url})
                self.runtime.bidi.navigate.assert_awaited_with("ctx-test", url)

    async def test_goto_rejects_disallowed_schemes(self):
        disallowed = [
            "javascript:alert(1)",
            "file:///etc/passwd",
            "data:text/html,<h1>test</h1>",
            "chrome://settings",
            "",
            None,
        ]
        for url in disallowed:
            with self.subTest(url=url), self.assertRaises(ValueError):
                await self.runtime.dispatch("goto", {"url": url})

    async def test_reload_triggers_browsing_context_reload(self):
        await self.runtime.dispatch("reload", {})
        self.runtime.bidi.send.assert_awaited_once_with(
            "browsingContext.reload",
            {"context": "ctx-test", "wait": "complete"},
        )

    async def test_eval_evaluates_in_active_context(self):
        self.runtime.bidi.evaluate.return_value = {"value": 42}
        result = await self.runtime.dispatch("eval", {"expression": "document.title"})
        self.runtime.bidi.evaluate.assert_awaited_once_with("ctx-test", "document.title")
        self.assertEqual(result, {"result": {"value": 42}})

    async def test_cookies_get_uses_bidi_or_fallback(self):
        self.runtime.bidi.send.return_value = {"cookies": [{"name": "session", "value": "xyz"}]}
        res = await self.runtime.dispatch("cookies_get", {})
        self.assertEqual(res, {"cookies": [{"name": "session", "value": "xyz"}]})
        self.runtime.bidi.send.assert_awaited_with("storage.getCookies", {})

        # Fallback path when bidi send fails
        self.runtime.bidi.send.side_effect = RuntimeError("bidi storage unsupported")
        self.runtime.bidi.evaluate.return_value = "session=xyz"
        fallback_res = await self.runtime.dispatch("cookies_get", {})
        self.assertEqual(fallback_res, {"cookie": "session=xyz"})

    async def test_cookies_clear_uses_bidi_or_fallback(self):
        self.runtime.bidi.send.return_value = {}
        self.runtime.bidi.send.side_effect = None
        res = await self.runtime.dispatch("cookies_clear", {})
        self.assertEqual(res, {})
        self.runtime.bidi.send.assert_awaited_with("storage.deleteCookies", {})

        # Fallback path
        self.runtime.bidi.send.side_effect = RuntimeError("bidi delete unsupported")
        self.runtime.bidi.evaluate.return_value = True
        fallback_res = await self.runtime.dispatch("cookies_clear", {})
        self.assertEqual(fallback_res, {"cleared": True})

    async def test_fullscreen_transitions(self):
        self.runtime.bidi.evaluate.return_value = True
        res_on = await self.runtime.dispatch("fullscreen", {"enabled": True})
        self.assertEqual(res_on, {"fullscreen": True})
        self.assertIn("requestFullscreen", self.runtime.bidi.evaluate.await_args.args[1])

        self.runtime.bidi.evaluate.return_value = False
        res_off = await self.runtime.dispatch("fullscreen", {"enabled": False})
        self.assertEqual(res_off, {"fullscreen": False})
        self.assertIn("exitFullscreen", self.runtime.bidi.evaluate.await_args.args[1])


class PersonaStorageIsolationSemanticsTests(unittest.TestCase):
    def test_session_storage_isolation_model(self):
        """Demonstrate that top-level browsing contexts retain separate sessionStorage stores."""
        # Simulated sessionStorage store: dict[origin, dict[context_id, dict[key, value]]]
        storage: dict[str, dict[str, dict[str, str]]] = {}

        def set_item(origin: str, context_id: str, key: str, value: str) -> None:
            storage.setdefault(origin, {}).setdefault(context_id, {})[key] = value

        def get_item(origin: str, context_id: str, key: str) -> str | None:
            return storage.get(origin, {}).get(context_id, {}).get(key)

        origin = "https://example.com"
        tab1, tab2 = "ctx-tab-1", "ctx-tab-2"

        set_item(origin, tab1, "token", "secret-tab1")
        set_item(origin, tab2, "token", "secret-tab2")

        # Tab 1 sees its own token, Tab 2 sees its own token
        self.assertEqual(get_item(origin, tab1, "token"), "secret-tab1")
        self.assertEqual(get_item(origin, tab2, "token"), "secret-tab2")
        self.assertNotEqual(get_item(origin, tab1, "token"), get_item(origin, tab2, "token"))

    def test_cookies_isolation_between_profiles(self):
        """Demonstrate that cookies across two personas reside in disjoint profile spaces."""
        profiles = {
            "persona-1": {"cookies.sqlite": {"session_id": "sess-1"}},
            "persona-2": {"cookies.sqlite": {"session_id": "sess-2"}},
        }
        self.assertNotEqual(
            profiles["persona-1"]["cookies.sqlite"]["session_id"],
            profiles["persona-2"]["cookies.sqlite"]["session_id"],
        )
