import curses
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.menu import SessionMenu
from src.tmux import TmuxClient


class _Result:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class TmuxClientTests(unittest.TestCase):
    def test_create_session_raises_when_new_session_fails(self):
        client = TmuxClient()
        client._run = Mock(
            side_effect=[
                _Result(returncode=1, stderr="boom"),
                _Result(returncode=1, stderr="ignored"),
            ]
        )

        with self.assertRaises(RuntimeError):
            client.create_session("codex-1", "codex")

    def test_send_keys_short_text_uses_send_keys_literal(self):
        client = TmuxClient()
        client._run = Mock(side_effect=[_Result(0), _Result(0)])

        ok = client.send_keys("runner-blog", "hello world", enter=True, delay_ms=0)

        self.assertTrue(ok)
        first = client._run.call_args_list[0].args
        self.assertEqual(first[:3], ("send-keys", "-t", "runner-blog:0.0"))
        self.assertIn("-l", first)
        self.assertIn("hello world", first)

    def test_send_keys_long_text_uses_buffer_paste(self):
        client = TmuxClient()
        client._run = Mock(side_effect=[_Result(0), _Result(0), _Result(0)])
        long_text = "x" * 600

        ok = client.send_keys("%7", long_text, enter=True, delay_ms=0)

        self.assertTrue(ok)
        self.assertEqual(client._run.call_args_list[0].args[0], "load-buffer")
        self.assertEqual(client._run.call_args_list[1].args[0], "paste-buffer")
        self.assertEqual(client._run.call_args_list[2].args[0], "send-keys")

    def test_send_keys_force_buffer_uses_buffer_paste_for_short_text(self):
        client = TmuxClient()
        client._run = Mock(side_effect=[_Result(0), _Result(0), _Result(0)])

        ok = client.send_keys("runner-blog", "/prompts:run_update DEV=x", enter=True, delay_ms=0, force_buffer=True)

        self.assertTrue(ok)
        self.assertEqual(client._run.call_args_list[0].args[0], "load-buffer")
        self.assertEqual(client._run.call_args_list[1].args[0], "paste-buffer")
        self.assertEqual(client._run.call_args_list[2].args[0], "send-keys")

    def test_list_panes_returns_ids(self):
        client = TmuxClient()
        client._run = Mock(return_value=_Result(0, stdout="%1\n%2\n"))
        self.assertEqual(client.list_panes("runner-blog"), ["%1", "%2"])

    def test_clear_prompt_line_sends_ctrl_u(self):
        client = TmuxClient()
        client._run = Mock(return_value=_Result(0))

        ok = client.clear_prompt_line("runner-blog")

        self.assertTrue(ok)
        client._run.assert_called_once_with("send-keys", "-t", "runner-blog:0.0", "C-u")


class SessionMenuRunTests(unittest.TestCase):
    def _make_menu(self):
        tmux = Mock()
        tmux.list_sessions.return_value = []
        tmux.get_pane_title.return_value = None
        return SessionMenu(tmux)

    def test_run_uses_fallback_on_curses_error(self):
        menu = self._make_menu()
        with patch("src.menu.curses.wrapper", side_effect=curses.error("bad-term")) as wrapper:
            with patch.object(menu, "_fallback_menu", side_effect=[("continue",), None]):
                with patch.object(menu.tmux, "attach"):
                    menu.run()

        self.assertEqual(wrapper.call_count, 2)

    def test_runner_start_bootstraps_gates_before_session_create(self):
        menu = self._make_menu()
        menu.tmux.create_session = Mock(return_value="%1")
        menu.tmux.socket = "/tmp/tmux-codex-test.sock"

        with patch("src.menu.curses.wrapper", return_value=(["Blog"], "high")), patch(
            "src.menu.ensure_gates_file", return_value=(Path("/tmp/gates.sh"), True)
        ) as ensure_gates, patch(
            "src.menu.inspect_runner_start_state", return_value={"ok": True}
        ), patch("src.menu.build_runner_paths", return_value=object()), patch(
            "src.menu.make_codex_exec_loop_script", return_value="echo runner"
        ), patch("src.menu.print"):
            attach_name = menu._start_runner_session()

        ensure_gates.assert_called_once()
        kwargs = ensure_gates.call_args.kwargs
        self.assertEqual(kwargs.get("dev"), "/Users/jian/Dev")
        self.assertEqual(kwargs.get("project"), "Blog")
        menu.tmux.create_session.assert_called_once()
        self.assertEqual(attach_name, "runner-Blog")

    def test_runner_start_attaches_first_when_multiple_projects_selected(self):
        menu = self._make_menu()
        menu.tmux.create_session = Mock(return_value="%1")
        menu.tmux.socket = "/tmp/tmux-codex-test.sock"

        with patch("src.menu.curses.wrapper", return_value=(["Blog", "Shop"], "high")), patch(
            "src.menu.ensure_gates_file", return_value=(Path("/tmp/gates.sh"), True)
        ), patch(
            "src.menu.inspect_runner_start_state", return_value={"ok": True}
        ), patch("src.menu.build_runner_paths", return_value=object()), patch(
            "src.menu.make_codex_exec_loop_script", return_value="echo runner"
        ), patch("src.menu.print"):
            attach_name = menu._start_runner_session()

        self.assertEqual(menu.tmux.create_session.call_count, 2)
        self.assertEqual(attach_name, "runner-Blog")

    def test_runner_start_skips_project_when_runner_already_active(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp)
            active_lock = dev / "Repos" / "Blog" / ".memory" / "runner" / "locks" / "RUNNER_ACTIVE.lock"
            active_lock.parent.mkdir(parents=True, exist_ok=True)
            active_lock.write_text("active\n")

            tmux = Mock()
            tmux.list_sessions.return_value = ["runner-Blog"]
            tmux.get_pane_title.return_value = None
            tmux.capture_pane.return_value = "esc to interrupt"
            tmux.get_pane_process.return_value = "codex"
            with patch.dict(os.environ, {"DEV": str(dev)}):
                menu = SessionMenu(tmux)
            with patch.dict(os.environ, {"DEV": str(dev)}), patch(
                "src.menu.curses.wrapper", return_value=(["Blog"], "high")
            ), patch("src.menu.ensure_gates_file") as ensure_gates, patch(
                "src.menu.inspect_runner_start_state"
            ) as state_check, patch("src.menu.print"):
                attach_name = menu._start_runner_session()

            self.assertIsNone(attach_name)
            ensure_gates.assert_not_called()
            state_check.assert_not_called()
            menu.tmux.create_session.assert_not_called()

    def test_stale_active_lock_is_cleared_when_no_runner_session_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp)
            active_lock = dev / "Repos" / "Blog" / ".memory" / "runner" / "locks" / "RUNNER_ACTIVE.lock"
            active_lock.parent.mkdir(parents=True, exist_ok=True)
            active_lock.write_text("stale\n")

            tmux = Mock()
            tmux.list_sessions.return_value = []
            tmux.get_pane_title.return_value = None

            with patch.dict(os.environ, {"DEV": str(dev)}):
                menu = SessionMenu(tmux)
                is_active = menu._project_has_running_runner("Blog")

            self.assertFalse(is_active)
            self.assertFalse(active_lock.exists())

    def test_runner_start_skips_unprepared_project(self):
        menu = self._make_menu()
        menu.tmux.create_session = Mock(return_value="%1")
        menu.tmux.socket = "/tmp/tmux-codex-test.sock"

        with patch("src.menu.curses.wrapper", return_value=(["Blog"], "high")), patch(
            "src.menu.ensure_gates_file", return_value=(Path("/tmp/gates.sh"), False)
        ), patch(
            "src.menu.inspect_runner_start_state",
            return_value={"ok": False, "error": "Runner is not set up. Run /prompts:run_setup first."},
        ), patch("src.menu.print") as print_mock:
            attach_name = menu._start_runner_session()

        self.assertIsNone(attach_name)
        menu.tmux.create_session.assert_not_called()
        printed = "\n".join(str(call.args[0]) for call in print_mock.call_args_list if call.args)
        self.assertIn("Run /prompts:run_setup first", printed)

    def test_idle_runner_shell_session_is_not_treated_as_running(self):
        menu = self._make_menu()
        menu.tmux.list_sessions.return_value = ["runner-Blog"]
        menu.tmux.get_pane_process.return_value = "zsh"

        is_active = menu._project_has_running_runner("Blog")
        self.assertFalse(is_active)

    def test_fallback_selector_excludes_running_projects_from_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp)
            tmux = Mock()
            tmux.list_sessions.return_value = []
            tmux.get_pane_title.return_value = None

            with patch.dict(os.environ, {"DEV": str(dev), "HOME": str(dev)}):
                menu = SessionMenu(tmux)
                with patch.object(menu, "_get_all_projects", return_value=[("Blog", 0), ("Shop", 0)]), patch.object(
                    menu, "_active_runner_projects", return_value={"Blog"}
                ), patch("builtins.input", side_effect=[""]):
                    result = menu._fallback_project_selector()

            self.assertEqual(result, (["Shop"], "high"))

    def test_fallback_selector_supports_arrow_navigation_and_space_toggle(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp)
            tmux = Mock()
            tmux.list_sessions.return_value = []
            tmux.get_pane_title.return_value = None

            with patch.dict(os.environ, {"DEV": str(dev), "HOME": str(dev)}):
                menu = SessionMenu(tmux)
                with patch.object(
                    menu,
                    "_get_all_projects",
                    return_value=[("Blog", 0), ("Shop", 0)],
                ), patch.object(
                    menu,
                    "_active_runner_projects",
                    return_value=set(),
                ), patch.object(
                    menu,
                    "_read_fallback_project_selector_input",
                    side_effect=["__down__", "__toggle__", ""],
                ):
                    result = menu._fallback_project_selector()

            self.assertEqual(result, (["Blog"], "high"))

    def test_persists_prefs_and_tags_into_global_codex_namespace(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp)

            tmux = Mock()
            tmux.list_sessions.return_value = []
            tmux.get_pane_title.return_value = None

            with patch.dict(os.environ, {"DEV": str(dev), "HOME": str(dev)}):
                menu = SessionMenu(tmux)
                menu._save_runner_prefs({"Blog"}, "high")
                menu._save_tags({"codex-1": "Session"})

            self.assertTrue((dev / ".codex" / "config" / "runner-prefs.json").exists())
            self.assertTrue((dev / ".codex" / "session-tags.json").exists())

    def test_runner_complexity_pref_is_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp)
            tmux = Mock()
            tmux.list_sessions.return_value = []
            tmux.get_pane_title.return_value = None

            with patch.dict(os.environ, {"DEV": str(dev), "HOME": str(dev)}):
                menu = SessionMenu(tmux)
                menu._save_runner_prefs({"Blog"}, "xhigh")
                self.assertEqual(menu._load_runner_complexity(), "xhigh")
                self.assertEqual(menu._load_runner_prefs(), {"Blog"})

    def test_fallback_selector_persists_toggles_before_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp)
            tmux = Mock()
            tmux.list_sessions.return_value = []
            tmux.get_pane_title.return_value = None

            with patch.dict(os.environ, {"DEV": str(dev), "HOME": str(dev)}):
                menu = SessionMenu(tmux)
                with patch.object(menu, "_get_all_projects", return_value=[("Banksy", 1), ("Blog", 1)]), patch.object(
                    menu, "_active_runner_projects", return_value=set()
                ), patch("builtins.input", side_effect=["1", "q"]):
                    result = menu._fallback_project_selector()

                self.assertIsNone(result)
                self.assertEqual(menu._load_runner_prefs(), {"Blog"})
                self.assertEqual(menu._load_runner_complexity(), "high")


class TmuxConfigTests(unittest.TestCase):
    def test_tmux_config_load_has_no_invalid_environment_variable_errors(self):
        if shutil.which("tmux") is None:
            self.skipTest("tmux not installed")

        config = ROOT / "config" / "tmux" / "tmux.conf"
        with tempfile.TemporaryDirectory() as tmp:
            socket = Path(tmp) / "tmux-test.sock"
            start = subprocess.run(
                ["tmux", "-S", str(socket), "-f", str(config), "start-server"],
                capture_output=True,
                text=True,
            )
            output = (start.stdout or "") + (start.stderr or "")
            self.assertEqual(start.returncode, 0, msg=output)
            self.assertNotIn("invalid environment variable", output.lower(), msg=output)
            subprocess.run(
                ["tmux", "-S", str(socket), "kill-server"],
                capture_output=True,
                text=True,
            )


class _FakeScreen:
    def __init__(self):
        self.lines = []

    def clear(self):
        return None

    def addstr(self, _row, _col, text, *_attrs):
        self.lines.append(text)

    def refresh(self):
        return None


class SessionMenuUiTests(unittest.TestCase):
    def _menu(self):
        tmux = Mock()
        tmux.list_sessions.return_value = ["codex-1"]
        tmux.get_pane_title.return_value = "Demo"
        tmux.capture_pane.return_value = ""
        tmux.get_pane_process.return_value = "codex"
        return SessionMenu(tmux)

    def test_normal_mode_help_contains_tag_action(self):
        menu = self._menu()
        fake = _FakeScreen()
        with patch.object(menu, "_count_todo_tasks", return_value=0), patch.object(
            menu, "_get_runner_elapsed", return_value=None
        ):
            menu._draw_menu(fake, mode="normal")
        rendered = "\n".join(fake.lines)
        self.assertIn("n=new | r=runner | t=tag | k=kill | q=quit", rendered)

    def test_tag_mode_prompt_text(self):
        menu = self._menu()
        fake = _FakeScreen()
        with patch.object(menu, "_count_todo_tasks", return_value=0), patch.object(
            menu, "_get_runner_elapsed", return_value=None
        ), patch.object(menu, "_load_tags", return_value={}):
            menu._draw_menu(fake, mode="tag_input", tag_session="codex-1", tag_input="Session")
        rendered = "\n".join(fake.lines)
        self.assertIn("Tag: Session_", rendered)

    def test_tagged_title_format(self):
        menu = self._menu()
        menu.pane_titles = ["Demo"]
        with patch.object(menu, "_load_tags", return_value={"codex-1": "Session"}):
            title = menu._get_display_title(0, "codex-1")
        self.assertEqual(title, "Session: Demo")


if __name__ == "__main__":
    unittest.main()
