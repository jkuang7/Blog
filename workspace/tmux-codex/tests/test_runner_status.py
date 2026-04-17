import unittest
from unittest.mock import patch

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.runner_status import detect_runner_state, is_codex_runtime_process


class RunnerStatusTests(unittest.TestCase):
    @patch("src.runner_status.time.time", return_value=100.0)
    def test_detects_idle_from_prompt_variants(self, _mock_time):
        out = "Tip text\n❯ /run blog --runner-id main\n"
        state = detect_runner_state(out, process_name="codex", last_activity_ts=80.0)
        self.assertEqual(state, "idle")

        out2 = "footer\n› Implement {feature}\n"
        state2 = detect_runner_state(out2, process_name="codex", last_activity_ts=80.0)
        self.assertEqual(state2, "idle")

    def test_detects_working_from_interrupt_markers(self):
        out = "• Working (5s • esc to interrupt)\n"
        state = detect_runner_state(out, process_name="codex", last_activity_ts=None)
        self.assertEqual(state, "working")

    @patch("src.runner_status.time.time", return_value=100.0)
    def test_node_wrapper_with_codex_prompt_is_idle(self, _mock_time):
        out = "OpenAI Codex\n› Implement {feature}\ngpt-5.3-codex xhigh · 100% left · ~/Dev\n"
        state = detect_runner_state(out, process_name="node", last_activity_ts=90.0)
        self.assertEqual(state, "idle")

    @patch("src.runner_status.time.time", return_value=100.0)
    def test_mcp_child_process_with_codex_prompt_is_still_idle(self, _mock_time):
        out = "OpenAI Codex\n› Improve documentation in @filename\ngpt-5.3-codex high · 100% left · ~/Dev\n"
        state = detect_runner_state(
            out,
            process_name="npm exec @upstash/context7-mcp@latest",
            last_activity_ts=95.0,
        )
        self.assertEqual(state, "idle")

    def test_node_wrapper_with_working_marker_is_working(self):
        out = "OpenAI Codex\n• Working (5s • esc to interrupt)\n"
        state = detect_runner_state(out, process_name="node", last_activity_ts=None)
        self.assertEqual(state, "working")

    def test_prompt_does_not_override_working_marker(self):
        out = "\n".join(
            [
                "OpenAI Codex",
                "› Write tests for @filename",
                "## Output",
                "Keep output compact and operational.",
                "• Working (1s • esc to interrupt)",
                "gpt-5.3-codex high · 100% left · ~/Dev/Repos/time-track",
            ]
        )
        state = detect_runner_state(out, process_name="node", last_activity_ts=None)
        self.assertEqual(state, "working")

    @patch("src.runner_status.time.time", return_value=100.0)
    def test_mcp_startup_marker_keeps_runner_working_even_with_stale_prompt(self, _mock_time):
        out = "\n".join(
            [
                "OpenAI Codex",
                "› /run_execute PHASE=implement",
                "mcp startup: initializing servers: playwright, panda, context7",
            ]
        )
        state = detect_runner_state(out, process_name="node", last_activity_ts=99.5)
        self.assertEqual(state, "working")

    @patch("src.runner_status.time.time", return_value=100.0)
    def test_mcp_startup_interrupted_banner_is_not_treated_as_idle(self, _mock_time):
        out = "\n".join(
            [
                "OpenAI Codex",
                "⚠ MCP startup interrupted. The following servers were not initialized:",
                "playwright, panda, context7",
            ]
        )
        state = detect_runner_state(out, process_name="node", last_activity_ts=99.5)
        self.assertEqual(state, "working")

    @patch("src.runner_status.time.time", return_value=100.0)
    def test_error_text_does_not_override_idle_prompt(self, _mock_time):
        out = "zsh:1: no such file or directory: /run\n❯ /run blog --runner-id main\n"
        state = detect_runner_state(out, process_name="codex", last_activity_ts=70.0)
        self.assertEqual(state, "idle")

    def test_detects_sleeping_from_backoff_markers(self):
        out = "[16:51:58] restarting runner in 3s\n"
        state = detect_runner_state(out, process_name="zsh", last_activity_ts=None)
        self.assertEqual(state, "sleeping")

    @patch("src.runner_status.time.time", return_value=100.0)
    def test_stale_waiting_text_in_scrollback_does_not_force_sleeping(self, _mock_time):
        out = "\n".join(
            [
                "older logs: waiting for background terminal",
                "older logs: waiting for dependency install",
                "OpenAI Codex",
                "› Run /review on my current changes",
            ]
        )
        state = detect_runner_state(out, process_name="node", last_activity_ts=95.0)
        self.assertEqual(state, "idle")

    def test_shell_without_codex_evidence_is_exited(self):
        out = "zsh prompt\n"
        state = detect_runner_state(out, process_name="zsh", last_activity_ts=None)
        self.assertEqual(state, "exited")

    def test_node_without_codex_ui_is_not_codex_runtime(self):
        out = "some node app logs\n"
        self.assertFalse(is_codex_runtime_process("node", out))

    @patch("src.runner_status.time.time", return_value=100.0)
    def test_stale_non_codex_python_process_is_exited(self, _mock_time):
        out = "python worker output without codex ui\n"
        state = detect_runner_state(out, process_name="python3", last_activity_ts=80.0)
        self.assertEqual(state, "exited")


if __name__ == "__main__":
    unittest.main()
