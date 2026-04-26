import curses
import subprocess
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
    def test_list_sessions_only_returns_codex_prefix_for_codex_listing(self):
        client = TmuxClient()
        client._run = Mock(return_value=_Result(0, stdout="codex-2\nrunner-blog\nmisc\ncodex-1\n"))

        sessions = client.list_sessions(prefix="codex")

        self.assertEqual(sessions, ["codex-1", "codex-2"])

    def test_create_session_raises_when_new_session_fails(self):
        client = TmuxClient()
        client._run = Mock(side_effect=[_Result(returncode=1, stderr="boom"), _Result(returncode=1)])

        with self.assertRaises(RuntimeError):
            client.create_session("codex-1", "codex")

    def test_send_keys_short_text_uses_send_keys_literal(self):
        client = TmuxClient()
        client._run = Mock(side_effect=[_Result(0), _Result(0)])

        ok = client.send_keys("codex-1", "hello world", enter=True, delay_ms=0)

        self.assertTrue(ok)
        first = client._run.call_args_list[0].args
        self.assertEqual(first[:3], ("send-keys", "-t", "codex-1:0.0"))
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

    def test_clear_prompt_line_sends_ctrl_u(self):
        client = TmuxClient()
        client._run = Mock(return_value=_Result(0))

        ok = client.clear_prompt_line("codex-1")

        self.assertTrue(ok)
        client._run.assert_called_once_with("send-keys", "-t", "codex-1:0.0", "C-u")


class SessionMenuTests(unittest.TestCase):
    def _make_menu(self):
        tmux = Mock()
        tmux.list_sessions.return_value = []
        tmux.get_pane_title.return_value = None
        return SessionMenu(tmux)

    def test_run_reports_curses_error_without_second_menu(self):
        menu = self._make_menu()
        with patch("src.menu.curses.wrapper", side_effect=curses.error("bad-term")) as wrapper:
            with patch.object(menu.tmux, "attach"), patch("builtins.print") as print_mock:
                result = menu.run()

        self.assertIsNone(result)
        self.assertEqual(wrapper.call_count, 1)
        printed = "\n".join(str(call.args[0]) for call in print_mock.call_args_list if call.args)
        self.assertIn("Unable to open interactive session menu", printed)

    def test_categorize_sessions_keeps_telecodex_runners_status_only(self):
        menu = self._make_menu()
        menu.sessions = ["codex-1"]
        runner = Mock()
        menu.telecodex_runners = [runner]

        regular, runners = menu._categorize_sessions()

        self.assertEqual(regular, [(0, "codex-1")])
        self.assertEqual(runners, [runner])

    def test_create_runner_view_session_opens_telecodex_viewer(self):
        menu = self._make_menu()
        menu.sessions = ["codex-1"]
        menu.pane_titles = [None]
        menu.tmux.next_session_name.return_value = "codex-2"
        runner = Mock(
            status_icon="R",
            display_title="runner | active task",
            db_path=Path("/Users/jian/Dev/workspace/telecodex/.telecodex/profiles/runner/data/telecodex.sqlite3"),
            chat_id=100,
            thread_id=20,
            codex_thread_id="019dcb27-0853-7053-b3eb-97d8e6f07189",
            cwd="/Users/jian/Dev/workspace/telecodex",
        )
        menu.telecodex_runners = [runner]

        result = menu._create_runner_view_session(runner)

        self.assertEqual(result, "codex-2")
        menu.tmux.create_session.assert_called_once()
        session_name, command = menu.tmux.create_session.call_args.args
        self.assertEqual(session_name, "codex-2")
        self.assertIn("python3 -m src.telecodex_viewer", command)
        self.assertIn("--chat-id 100", command)
        self.assertIn("--thread-id 20", command)

    def test_create_runner_view_session_returns_none_without_db_path(self):
        menu = self._make_menu()
        runner = Mock(db_path=None, cwd="", display_title="runner")

        result = menu._create_runner_view_session(runner)

        self.assertIsNone(result)
        menu.tmux.create_session.assert_not_called()


if __name__ == "__main__":
    unittest.main()
