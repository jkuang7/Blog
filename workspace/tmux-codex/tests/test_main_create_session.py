import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.main import create_session, session_profile_overrides


class CreateSessionTests(unittest.TestCase):
    def test_session_profile_overrides_are_empty_for_plain_chat_sessions(self):
        self.assertEqual(session_profile_overrides([]), [])
        self.assertEqual(session_profile_overrides(["status update"]), [])

    def test_session_profile_overrides_force_high_for_planning_and_execution(self):
        overrides = session_profile_overrides(["/plan"])
        self.assertIn('model="gpt-5.4"', overrides)
        self.assertIn('model_reasoning_effort="high"', overrides)
        self.assertIn('plan_mode_reasoning_effort="high"', overrides)

        kanban_overrides = session_profile_overrides(["continue kanban"])
        self.assertIn('model="gpt-5.4"', kanban_overrides)

        workflow_overrides = session_profile_overrides(["continuous workflow"])
        self.assertIn('model="gpt-5.4"', workflow_overrides)

    def test_create_session_creates_and_attaches_tmux_session(self):
        tmux_instance = MagicMock()

        with (
            patch("src.main.get_tmux_config", return_value=None),
            patch("src.main.TmuxClient", return_value=tmux_instance),
            patch("src.main.os.getcwd", return_value="/tmp/project"),
            patch("src.main.print"),
        ):
            create_session([])

        tmux_instance.create_session.assert_called_once()
        tmux_instance.attach.assert_called_once()

    def test_create_session_promotes_execution_commands_to_high_profile(self):
        tmux_instance = MagicMock()

        with (
            patch("src.main.get_tmux_config", return_value=None),
            patch("src.main.TmuxClient", return_value=tmux_instance),
            patch("src.main.os.getcwd", return_value="/tmp/project"),
            patch("src.main.print"),
        ):
            create_session(["/kanban"])

        created_command = tmux_instance.create_session.call_args[0][1]
        self.assertIn('model="gpt-5.4"', created_command)
        self.assertIn('model_reasoning_effort="high"', created_command)
        self.assertIn('plan_mode_reasoning_effort="high"', created_command)

    def test_create_session_leaves_plain_sessions_on_global_default_profile(self):
        tmux_instance = MagicMock()

        with (
            patch("src.main.get_tmux_config", return_value=None),
            patch("src.main.TmuxClient", return_value=tmux_instance),
            patch("src.main.os.getcwd", return_value="/tmp/project"),
            patch("src.main.print"),
        ):
            create_session([])

        created_command = tmux_instance.create_session.call_args[0][1]
        self.assertNotIn('model="gpt-5.4"', created_command)
        self.assertNotIn('model_reasoning_effort="high"', created_command)


if __name__ == "__main__":
    unittest.main()
