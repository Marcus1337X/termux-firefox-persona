"""Unit checks for per-instance resource isolation.

These tests intentionally stay at the stdlib/unittest level; starting Firefox
and Xvfb belongs to the device integration suite.
"""

import asyncio
import os
import tempfile
import unittest
from unittest import mock

from src.browser import BrowserPilot
from src.lock import SessionLock
from src.native import NativeFirefoxSession, PreferenceConflictError


class SessionLockTests(unittest.TestCase):
    def test_flock_blocks_second_owner_and_allows_reacquire(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "persona.lock")
            first = SessionLock(path)
            second = SessionLock(path)
            first.acquire()
            try:
                with self.assertRaises(RuntimeError):
                    second.acquire()
            finally:
                first.release()
            second.acquire()
            second.release()

    def test_lock_file_is_reusable_after_release(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "persona.lock")
            lock = SessionLock(path)
            lock.acquire()
            lock.release()
            self.assertTrue(os.path.exists(path))
            reused = SessionLock(path)
            reused.acquire()
            reused.release()


class BrowserIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_xvfb_rejects_occupied_display_without_kill_or_unlink(self):
        pilot = BrowserPilot(display=":321", browser_type="firefox")
        with mock.patch("src.browser.os.path.exists", return_value=True), \
                mock.patch("src.browser.asyncio.create_subprocess_exec") as spawn:
            with self.assertRaises(RuntimeError):
                await pilot._start_xvfb()
        spawn.assert_not_called()

    async def test_xvfb_uses_private_environment_and_single_wm(self):
        pilot = BrowserPilot(
            display=":322", browser_type="firefox",
            launch_env={"TBP_PERSONA": "a", "TMPDIR": "/persona-tmp"},
            window_size="1280,720", screen_size="2560,1440",
        )
        fake_x = mock.Mock(returncode=None, pid=10)
        fake_wm = mock.Mock(returncode=None, pid=11)
        calls = []

        async def spawn(*args, **kwargs):
            calls.append((args, kwargs))
            return fake_x if len(calls) == 1 else fake_wm

        with mock.patch("src.browser.os.path.exists", return_value=False), \
                mock.patch("src.browser.shutil.which", return_value="openbox"), \
                mock.patch("src.browser.asyncio.create_subprocess_exec", side_effect=spawn), \
                mock.patch("src.browser.asyncio.sleep", new=mock.AsyncMock()):
            await pilot._start_xvfb()
        self.assertEqual(len(calls), 2)
        self.assertIn("2560x1440x24", calls[0][0])
        self.assertEqual(calls[0][1]["env"]["DISPLAY"], ":322")
        self.assertEqual(calls[0][1]["env"]["TBP_PERSONA"], "a")
        self.assertEqual(calls[1][1]["env"]["DISPLAY"], ":322")

    async def test_xvfb_checks_termux_tmpdir_markers(self):
        pilot = BrowserPilot(
            display=":324", browser_type="firefox",
            launch_env={"TMPDIR": "/persona-tmp"},
        )
        seen = []

        def exists(path):
            seen.append(path)
            return path == "/persona-tmp/.X324-lock"

        with mock.patch("src.browser.os.path.exists", side_effect=exists), \
                mock.patch("src.browser.asyncio.create_subprocess_exec") as spawn:
            with self.assertRaises(RuntimeError):
                await pilot._start_xvfb()
        self.assertIn("/persona-tmp/.X324-lock", seen)
        spawn.assert_not_called()


class NativeIsolationTests(unittest.TestCase):
    def test_environment_does_not_mutate_process_display(self):
        old = os.environ.get("DISPLAY")
        session = NativeFirefoxSession(
            display=":323", launch_env={"TBP_PERSONA": "b"}, backend="native"
        )
        env = session._process_env()
        self.assertEqual(env["DISPLAY"], ":323")
        self.assertEqual(env["TBP_PERSONA"], "b")
        self.assertNotEqual(os.environ.get("DISPLAY"), ":323")
        self.assertEqual(os.environ.get("DISPLAY"), old)
        self.assertNotIn("LIBGL_ALWAYS_SOFTWARE", env)

    def test_user_and_session_restore_files_are_preserved(self):
        with tempfile.TemporaryDirectory() as profile:
            restore = os.path.join(profile, "sessionstore.jsonlz4")
            with open(restore, "wb") as f:
                f.write(b"recovery")
            user_js = os.path.join(profile, "user.js")
            with open(user_js, "w", encoding="utf-8") as f:
                f.write('user_pref("dom.popup_maximum", 3);\n')
            session = NativeFirefoxSession(
                user_data_dir=profile, prefs={"dom.popup_maximum": 99}
            )
            with self.assertRaises(PreferenceConflictError):
                session._cleanup_profile_locks()
            persona = NativeFirefoxSession(
                user_data_dir=profile, prefs={"dom.popup_maximum": 99},
                prefs_policy="enforce", profile_kind="persona",
            )
            persona._cleanup_profile_locks()
            self.assertTrue(os.path.exists(restore))
            with open(user_js, encoding="utf-8") as f:
                text = f.read()
            self.assertIn('user_pref("dom.popup_maximum", 3);', text)
            self.assertIn('user_pref("dom.popup_maximum", 99);', text)

    def test_js_evaluator_hook(self):
        async def evaluator(js, timeout=60):
            return {"js": js, "timeout": timeout}

        async def run():
            session = NativeFirefoxSession()
            session.bind_javascript_evaluator(evaluator)
            return await session._exec_js("2 + 2", timeout=4)

        result = asyncio.run(run())
        self.assertEqual(result, {"js": "2 + 2", "timeout": 4})

    def test_persona_download_directory_is_profile_scoped(self):
        with tempfile.TemporaryDirectory() as profile:
            session = NativeFirefoxSession(
                user_data_dir=profile, profile_kind="persona"
            )
            session._cleanup_profile_locks()
            with open(os.path.join(profile, "user.js"), encoding="utf-8") as f:
                text = f.read()
            self.assertIn(
                f'user_pref("browser.download.dir", "{os.path.join(profile, "downloads")}");',
                text,
            )
            self.assertTrue(os.path.isdir(os.path.join(profile, "downloads")))

    def test_process_group_signal_requires_owned_session_leader(self):
        proc = mock.Mock(pid=4321, returncode=None)
        with mock.patch("src.native.os.getpgid", return_value=4321), \
                mock.patch("src.native.os.killpg") as killpg:
            self.assertTrue(
                NativeFirefoxSession._signal_owned_group(proc, 9)
            )
        killpg.assert_called_once_with(4321, 9)

        proc = mock.Mock(pid=4322, returncode=None)
        with mock.patch("src.native.os.getpgid", return_value=1), \
                mock.patch("src.native.os.killpg") as killpg:
            self.assertFalse(
                NativeFirefoxSession._signal_owned_group(proc, 9)
            )
        killpg.assert_not_called()

    def test_firefox_launch_uses_private_process_session(self):
        with tempfile.TemporaryDirectory() as td:
            async def run():
                session = NativeFirefoxSession(
                    display=":325", user_data_dir=td,
                    firefox_bin="firefox-test",
                )
                fake = mock.Mock(pid=5432, returncode=None, stderr=None)
                fake.wait = mock.AsyncMock(return_value=0)
                calls = []

                async def spawn(*args, **kwargs):
                    calls.append((args, kwargs))
                    return fake

                with mock.patch("src._utils.require_binaries"), \
                        mock.patch("src.native.asyncio.create_subprocess_exec", side_effect=spawn), \
                        mock.patch("src.native.asyncio.sleep", new=mock.AsyncMock()), \
                        mock.patch.object(session, "_find_main_window", new=mock.AsyncMock()):
                    await session.connect()
                    await session.close()
                return calls

            calls = asyncio.run(run())
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0][1]["start_new_session"])
        self.assertIs(calls[0][1]["stderr"], asyncio.subprocess.PIPE)

    def test_main_window_search_is_pid_scoped_and_visible_only(self):
        class Result:
            def __init__(self, output):
                self.output = output

            async def communicate(self):
                return self.output, b""

        async def run():
            session = NativeFirefoxSession(display=":326")
            session._firefox_proc = mock.Mock(pid=6543, returncode=None)
            calls = []

            async def spawn(*args, **kwargs):
                calls.append(args)
                if args[1] == "search":
                    # xdotool's --onlyvisible excludes the hidden helper.
                    return Result(b"200\n")
                return Result(b"Example - Mozilla Firefox\n")

            with mock.patch(
                "src.native.asyncio.create_subprocess_exec",
                side_effect=spawn,
            ):
                result = await session._find_main_window()
            return result, calls

        result, calls = asyncio.run(run())
        self.assertEqual(result, "200")
        self.assertIn(
            ("xdotool", "search", "--onlyvisible", "--pid", "6543"),
            calls,
        )


if __name__ == "__main__":
    unittest.main()
