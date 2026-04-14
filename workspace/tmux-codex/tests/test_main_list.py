import io
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.main import list_sessions


class ListSessionsTests(unittest.TestCase):
    def test_list_sessions_attaches_regular_session_by_number(self):
        tmux_instance = MagicMock()
        tmux_instance.list_sessions.return_value = ["codex-1", "codex-2", "runner-blog"]

        with (
            patch("src.main.get_tmux_config", return_value=None),
            patch("src.main.TmuxClient", return_value=tmux_instance),
            patch("src.main.os.chdir"),
        ):
            list_sessions("1")

        tmux_instance.attach.assert_called_once_with("codex-1")

    def test_list_sessions_attaches_runner_session_by_letter(self):
        tmux_instance = MagicMock()
        tmux_instance.list_sessions.return_value = ["codex-1", "runner-blog", "runner-time-track"]

        with (
            patch("src.main.get_tmux_config", return_value=None),
            patch("src.main.TmuxClient", return_value=tmux_instance),
            patch("src.main.os.chdir"),
        ):
            list_sessions("b")

        tmux_instance.attach.assert_called_once_with("runner-time-track")

    def test_list_sessions_reports_invalid_selector_without_opening_menu(self):
        tmux_instance = MagicMock()
        tmux_instance.list_sessions.return_value = ["codex-1"]
        stdout = io.StringIO()

        with (
            patch("src.main.get_tmux_config", return_value=None),
            patch("src.main.TmuxClient", return_value=tmux_instance),
            patch("src.main.os.chdir"),
            patch("sys.stdout", stdout),
        ):
            list_sessions("9")

        tmux_instance.attach.assert_not_called()
        self.assertIn("Session selector not found: 9", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
