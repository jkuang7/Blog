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
    def test_list_sessions_hides_orx_sessions_from_codex_listing(self):
        client = TmuxClient()
        client._run = Mock(
            return_value=_Result(
                0,
                stdout="codex-2\norx-tmux-codex-pro-99-main\nrunner-blog\nmisc\n",
            )
        )

        sessions = client.list_sessions(prefix="codex")

        self.assertEqual(sessions, ["codex-2", "runner-blog"])

    def test_list_sessions_hides_matching_orx_session_even_when_runner_exists(self):
        client = TmuxClient()
        client._run = Mock(
            return_value=_Result(
                0,
                stdout="codex-2\norx-tmux-codex-pro-99-main\nrunner-tmux-codex\nmisc\n",
            )
        )

        sessions = client.list_sessions(prefix="codex")

        self.assertEqual(sessions, ["codex-2", "runner-tmux-codex"])

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

        ok = client.send_keys("runner-blog", "/run_govern DEV=x", enter=True, delay_ms=0, force_buffer=True)

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

    def test_runner_start_launches_loop_bg_through_main_module(self):
        menu = self._make_menu()
        menu.tmux.socket = "/tmp/tmux-codex-test.sock"
        menu.tmux.list_sessions.side_effect = [[], ["runner-Blog"]]

        with patch("src.menu.curses.wrapper", return_value=(["Blog"], "high")), patch(
            "src.menu.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=["python3", "-m", "src.main", "loop-bg", "Blog"],
                returncode=0,
                stdout="Started runner-Blog\n",
                stderr="",
            ),
        ) as run_mock, patch("src.menu.print") as print_mock:
            attach_name = menu._start_runner_session()

        command = run_mock.call_args.args[0]
        self.assertIn("-m", command)
        self.assertIn("src.main", command)
        self.assertIn("loop-bg", command)
        self.assertIn("Blog", command)
        self.assertEqual(attach_name, "runner-Blog")
        print_mock.assert_any_call("  Started runner-Blog")

    def test_runner_start_attaches_first_when_multiple_projects_selected(self):
        menu = self._make_menu()
        menu.tmux.socket = "/tmp/tmux-codex-test.sock"
        menu.tmux.list_sessions.side_effect = [
            [],
            ["runner-Blog"],
            ["runner-Blog"],
            ["runner-Blog", "runner-Shop"],
        ]

        with patch("src.menu.curses.wrapper", return_value=(["Blog", "Shop"], "high")), patch(
            "src.menu.subprocess.run",
            side_effect=[
                subprocess.CompletedProcess(
                    args=["python3", "-m", "src.main", "loop-bg", "Blog"],
                    returncode=0,
                    stdout="Started runner-Blog\n",
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=["python3", "-m", "src.main", "loop-bg", "Shop"],
                    returncode=0,
                    stdout="Started runner-Shop\n",
                    stderr="",
                ),
            ],
        ), patch("src.menu.print"):
            attach_name = menu._start_runner_session()

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

    def test_done_lock_with_zero_tasks_is_treated_as_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp)
            done_lock = dev / "Repos" / "Blog" / ".memory" / "runner" / "locks" / "RUNNER_DONE.lock"
            done_lock.parent.mkdir(parents=True, exist_ok=True)
            done_lock.write_text("done\n")

            tmux = Mock()
            tmux.list_sessions.return_value = []
            tmux.get_pane_title.return_value = None

            with patch.dict(os.environ, {"DEV": str(dev)}):
                menu = SessionMenu(tmux)
                state = menu._project_runner_display_state("Blog", todo_count=0)

            self.assertEqual(state, "done")

    def test_done_lock_is_resolved_from_active_worktree_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp)
            canonical_done_lock = dev / "Repos" / "Blog" / ".memory" / "runner" / "locks" / "RUNNER_DONE.lock"
            canonical_done_lock.parent.mkdir(parents=True, exist_ok=True)
            worktree_root = dev / "worktrees" / "w1" / "Blog"
            done_lock = worktree_root / ".memory" / "runner" / "locks" / "RUNNER_DONE.lock"
            done_lock.parent.mkdir(parents=True, exist_ok=True)
            done_lock.write_text("done\n")

            tmux = Mock()
            tmux.list_sessions.return_value = []
            tmux.get_pane_title.return_value = None

            with patch.dict(os.environ, {"DEV": str(dev)}), patch(
                "src.menu.resolve_target_project_root",
                return_value=worktree_root,
            ):
                menu = SessionMenu(tmux)
                state = menu._project_runner_display_state("Blog", todo_count=0)

            self.assertEqual(state, "done")
            self.assertFalse(canonical_done_lock.exists())

    def test_zero_tasks_without_done_marker_is_idle_not_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp)
            tmux = Mock()
            tmux.list_sessions.return_value = []
            tmux.get_pane_title.return_value = None

            with patch.dict(os.environ, {"DEV": str(dev)}):
                menu = SessionMenu(tmux)
                state = menu._project_runner_display_state("Blog", todo_count=0)

            self.assertEqual(state, "idle")

    def test_done_runner_state_is_ignored_when_pending_tasks_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp)
            state_file = dev / "Repos" / "Blog" / ".memory" / "runner" / "runtime" / "RUNNER_STATE.json"
            state_file.parent.mkdir(parents=True, exist_ok=True)
            state_file.write_text('{"status":"done"}\n', encoding="utf-8")

            tmux = Mock()
            tmux.list_sessions.return_value = []
            tmux.get_pane_title.return_value = None

            with patch.dict(os.environ, {"DEV": str(dev)}):
                menu = SessionMenu(tmux)
                state = menu._project_runner_display_state("Blog", todo_count=2)

            self.assertEqual(state, "pending")

    def test_get_all_projects_reads_queue_depth_from_orx_dashboard(self):
        tmux = Mock()
        tmux.list_sessions.return_value = []
        tmux.get_pane_title.return_value = None

        with patch(
            "src.menu.fetch_dashboard",
            return_value={
                "projects": [
                    {
                        "project": {"project_key": "Blog", "assigned_bot": "AlphaBot"},
                        "queue_depth": 1,
                        "active_issue_key": "PRO-101",
                        "health_state": "busy",
                    }
                ]
            },
        ):
            menu = SessionMenu(tmux)
            projects = menu._get_all_projects()

        self.assertEqual(projects, [("Blog", 1)])
        self.assertEqual(menu._runner_picker_detail_text("Blog"), "active PRO-101 | bot AlphaBot")

    def test_runner_start_reports_launch_failure(self):
        menu = self._make_menu()
        menu.tmux.socket = "/tmp/tmux-codex-test.sock"
        menu.tmux.list_sessions.return_value = []

        with patch("src.menu.curses.wrapper", return_value=(["Blog"], "high")), patch(
            "src.menu.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=["python3", "-m", "src.main", "loop-bg", "Blog"],
                returncode=1,
                stdout="",
                stderr="runner setup failed",
            ),
        ), patch("src.menu.print") as print_mock:
            attach_name = menu._start_runner_session()

        self.assertIsNone(attach_name)
        printed = "\n".join(str(call.args[0]) for call in print_mock.call_args_list if call.args)
        self.assertIn("runner setup failed", printed)

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
                    menu,
                    "_project_runner_display_state",
                    side_effect=lambda name, todo_count=None, existing_sessions=None: {
                        "Blog": "running",
                        "Shop": "idle",
                    }[name],
                ), patch.object(menu, "_load_runner_pref_selection", return_value=(set(), False)), patch.object(
                    menu, "_load_runner_complexity", return_value="high"
                ), patch("src.menu.sys.stdin.isatty", return_value=False), patch(
                    "src.menu.os.open", side_effect=OSError
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
                    "_load_runner_pref_selection",
                    return_value=({"Blog", "Shop"}, True),
                ), patch.object(
                    menu,
                    "_load_runner_complexity",
                    return_value="high",
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

    def test_fallback_selector_a_toggles_all_off_when_everything_is_selected(self):
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
                    "_load_runner_pref_selection",
                    return_value=({"Blog", "Shop"}, True),
                ), patch.object(
                    menu,
                    "_load_runner_complexity",
                    return_value="high",
                ), patch.object(
                    menu,
                    "_active_runner_projects",
                    return_value=set(),
                ), patch.object(
                    menu,
                    "_persist_runner_picker_state",
                ) as persist_state, patch.object(
                    menu,
                    "_read_fallback_project_selector_input",
                    side_effect=["a", "q"],
                ):
                    result = menu._fallback_project_selector()

            self.assertIsNone(result)
            persist_state.assert_called_once_with(set(), "high")

    def test_fallback_selector_reads_arrows_from_dev_tty_when_stdin_is_not_tty(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp)
            tmux = Mock()
            tmux.list_sessions.return_value = []
            tmux.get_pane_title.return_value = None

            with patch.dict(os.environ, {"DEV": str(dev), "HOME": str(dev)}):
                menu = SessionMenu(tmux)
                with (
                    patch("src.menu.sys.stdin.isatty", return_value=False),
                    patch("src.menu.os.open", return_value=42),
                    patch("src.menu.os.read", side_effect=[b"\x1b", b"[", b"B"]),
                    patch("src.menu.os.close"),
                    patch("src.menu.termios.tcgetattr", return_value=("settings",)),
                    patch("src.menu.termios.tcsetattr"),
                    patch("src.menu.tty.setraw"),
                ):
                    choice = menu._read_fallback_project_selector_input()

            self.assertEqual(choice, "__down__")

    def test_persists_prefs_and_tags_into_global_codex_namespace(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp)

            tmux = Mock()
            tmux.list_sessions.return_value = []
            tmux.get_pane_title.return_value = None

            codex_home = dev / ".codex"
            with patch.dict(os.environ, {"DEV": str(dev), "HOME": str(dev), "CODEX_HOME": str(codex_home)}):
                menu = SessionMenu(tmux)
                menu._save_runner_prefs({"Blog"}, "high")
                menu._save_tags({"codex-1": "Session"})

            self.assertTrue((codex_home / "config" / "runner-prefs.json").exists())
            self.assertTrue((codex_home / "session-tags.json").exists())

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

    def test_runner_prefs_preserve_explicit_empty_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp)
            tmux = Mock()
            tmux.list_sessions.return_value = []
            tmux.get_pane_title.return_value = None

            with patch.dict(os.environ, {"DEV": str(dev), "HOME": str(dev)}):
                menu = SessionMenu(tmux)
                menu._save_runner_prefs(set(), "high")
                self.assertEqual(menu._load_runner_pref_selection(), (set(), True))
                self.assertEqual(menu._load_runner_prefs(), set())

    def test_project_selector_honors_saved_all_off_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp)
            tmux = Mock()
            tmux.list_sessions.return_value = []
            tmux.get_pane_title.return_value = None

            with patch.dict(os.environ, {"DEV": str(dev), "HOME": str(dev)}):
                menu = SessionMenu(tmux)
                menu._save_runner_prefs(set(), "high")
                fake = _FakeScreen(keys=[27])
                with patch.object(menu, "_get_all_projects", return_value=[("time-track", 3), ("repo", 0)]), patch.object(
                    menu, "_project_runner_display_state", return_value="pending"
                ), patch(
                    "src.menu.curses.curs_set", side_effect=curses.error("unsupported")
                ), patch(
                    "src.menu.curses.use_default_colors", side_effect=curses.error("unsupported")
                ):
                    result = menu._run_project_selector(fake)

            self.assertIsNone(result)
            rendered = "\n".join(fake.lines)
            self.assertIn("[ ] time-track", rendered)
            self.assertIn("[ ] repo", rendered)

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
                ), patch.object(menu, "_load_runner_pref_selection", return_value=(set(), False)), patch.object(
                    menu, "_load_runner_complexity", return_value="high"
                ), patch("src.menu.sys.stdin.isatty", return_value=False), patch(
                    "src.menu.os.open", side_effect=OSError
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
    def __init__(self, *, height: int = 40, width: int = 120, keys: list[int] | None = None):
        self.lines = []
        self.height = height
        self.width = width
        self.keys = list(keys or [])

    def clear(self):
        return None

    def addstr(self, _row, _col, text, *_attrs):
        self.lines.append(text)

    def refresh(self):
        return None

    def getmaxyx(self):
        return (self.height, self.width)

    def timeout(self, _value):
        return None

    def getch(self):
        if self.keys:
            return self.keys.pop(0)
        return -1


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

    def test_project_selector_does_not_throw_on_narrow_terminal(self):
        menu = self._menu()
        fake = _FakeScreen(height=8, width=24, keys=[27])
        with patch.object(menu, "_get_all_projects", return_value=[("time-track", 3), ("repo", 0)]), patch.object(
            menu, "_load_runner_pref_selection", return_value=({"time-track"}, True)
        ), patch.object(menu, "_load_runner_complexity", return_value="high"), patch.object(
            menu, "_active_runner_projects", return_value=set()
        ), patch("src.menu.curses.curs_set", side_effect=curses.error("unsupported")), patch(
            "src.menu.curses.use_default_colors", side_effect=curses.error("unsupported")
        ):
            result = menu._run_project_selector(fake)

        self.assertIsNone(result)

    def test_project_selector_a_toggles_all_off_when_everything_is_selected(self):
        menu = self._menu()
        fake = _FakeScreen(keys=[ord("a"), 27])
        with patch.object(menu, "_get_all_projects", return_value=[("time-track", 3), ("repo", 0)]), patch.object(
            menu, "_load_runner_pref_selection", return_value=({"time-track", "repo"}, True)
        ), patch.object(menu, "_load_runner_complexity", return_value="high"), patch.object(
            menu, "_active_runner_projects", return_value=set()
        ), patch.object(menu, "_persist_runner_picker_state") as persist_state, patch(
            "src.menu.curses.curs_set", side_effect=curses.error("unsupported")
        ), patch(
            "src.menu.curses.use_default_colors", side_effect=curses.error("unsupported")
        ):
            result = menu._run_project_selector(fake)

        self.assertIsNone(result)
        persist_state.assert_called_once_with(set(), "high")

    def test_project_selector_marks_completed_projects_done_and_uses_neutral_help_when_idle(self):
        menu = self._menu()
        fake = _FakeScreen(keys=[27])
        with patch.object(menu, "_get_all_projects", return_value=[("time-track", 0), ("repo", 1)]), patch.object(
            menu, "_load_runner_pref_selection", return_value=({"time-track"}, True)
        ), patch.object(menu, "_load_runner_complexity", return_value="high"), patch.object(
            menu,
            "_project_runner_display_state",
            side_effect=lambda name, todo_count=None, existing_sessions=None: {
                "time-track": "done",
                "repo": "pending",
            }[name],
        ), patch(
            "src.menu.curses.curs_set", side_effect=curses.error("unsupported")
        ), patch(
            "src.menu.curses.use_default_colors", side_effect=curses.error("unsupported")
        ):
            result = menu._run_project_selector(fake)

        self.assertIsNone(result)
        rendered = "\n".join(fake.lines)
        self.assertIn("(queue 0) [done]", rendered)
        self.assertNotIn("(queue 0) [running]", rendered)
        self.assertNotIn("locked=running", rendered)
        self.assertNotIn("running projects locked", rendered)

    def test_project_selector_help_mentions_locking_only_when_projects_are_running(self):
        menu = self._menu()
        fake = _FakeScreen(keys=[27])
        with patch.object(menu, "_get_all_projects", return_value=[("time-track", 0), ("repo", 1)]), patch.object(
            menu, "_load_runner_pref_selection", return_value=({"time-track"}, True)
        ), patch.object(menu, "_load_runner_complexity", return_value="high"), patch.object(
            menu,
            "_project_runner_display_state",
            side_effect=lambda name, todo_count=None, existing_sessions=None: {
                "time-track": "running",
                "repo": "pending",
            }[name],
        ), patch(
            "src.menu.curses.curs_set", side_effect=curses.error("unsupported")
        ), patch(
            "src.menu.curses.use_default_colors", side_effect=curses.error("unsupported")
        ):
            result = menu._run_project_selector(fake)

        self.assertIsNone(result)
        rendered = "\n".join(fake.lines)
        self.assertIn("(running projects locked)", rendered)

    def test_completed_project_remains_selectable_in_project_selector(self):
        menu = self._menu()
        fake = _FakeScreen(keys=[10])
        with patch.object(menu, "_get_all_projects", return_value=[("time-track", 0)]), patch.object(
            menu, "_load_runner_pref_selection", return_value=({"time-track"}, True)
        ), patch.object(menu, "_load_runner_complexity", return_value="high"), patch.object(
            menu, "_project_runner_display_state", return_value="done"
        ), patch(
            "src.menu.curses.curs_set", side_effect=curses.error("unsupported")
        ), patch(
            "src.menu.curses.use_default_colors", side_effect=curses.error("unsupported")
        ):
            result = menu._run_project_selector(fake)

        self.assertEqual(result, (["time-track"], "high"))


if __name__ == "__main__":
    unittest.main()
