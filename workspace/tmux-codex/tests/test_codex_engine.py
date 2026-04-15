import unittest
from pathlib import Path
from unittest.mock import patch

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.codex_engine import run_codex_iteration


class _FakeProcess:
    def __init__(self, lines, rc=0):
        self._lines = lines
        self._rc = rc
        self.stdout = iter(lines)

    def wait(self):
        return self._rc


class CodexEngineTests(unittest.TestCase):
    def test_parses_json_stream_and_extracts_session(self):
        lines = [
            "warn line\n",
            '{"type":"thread.started","thread_id":"thread-123"}\n',
            '{"type":"item.completed","item":{"type":"tool_call","message":"run test"}}\n',
            '{"type":"item.completed","item":{"type":"message","text":"All done"}}\n',
        ]

        with patch("src.codex_engine.subprocess.Popen", return_value=_FakeProcess(lines)) as popen:
            result = run_codex_iteration(
                cwd=Path("/tmp"),
                model="gpt-5.3-codex",
                prompt="do work",
                session_id=None,
            )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.session_id, "thread-123")
        self.assertEqual(result.final_message, "All done")
        self.assertEqual(len(result.events), 3)
        self.assertTrue(any(event.get("_hook") == "tool_call" for event in result.events))

        called_cmd = popen.call_args.args[0]
        self.assertEqual(called_cmd[:7], ["codex", "--search", "-s", "workspace-write", "exec", "--json", "-m"])
        self.assertIn("-c", called_cmd)
        self.assertIn('model_reasoning_effort="high"', called_cmd)

    def test_resume_command_uses_session_id(self):
        lines = ['{"type":"item.completed","item":{"type":"message","text":"ok"}}\n']

        with patch("src.codex_engine.subprocess.Popen", return_value=_FakeProcess(lines)) as popen:
            run_codex_iteration(
                cwd=Path("/tmp"),
                model="gpt-5.3-codex",
                prompt="continue",
                session_id="thread-xyz",
            )

        called_cmd = popen.call_args.args[0]
        self.assertEqual(called_cmd[:6], ["codex", "--search", "-s", "workspace-write", "exec", "resume"])
        self.assertIn("thread-xyz", called_cmd)
        self.assertIn('model_reasoning_effort="high"', called_cmd)

    def test_plain_stream_extracts_session_and_final_message(self):
        lines = [
            "OpenAI Codex v0.107.0 (research preview)\n",
            "session id: 019cb4cf-367a-77c3-8cb7-c37eed7ee30e\n",
            "thinking\n",
            "codex\n",
            "TEST_OK\n",
            "tokens used\n",
        ]

        with patch("src.codex_engine.subprocess.Popen", return_value=_FakeProcess(lines)) as popen:
            result = run_codex_iteration(
                cwd=Path("/tmp"),
                model="gpt-5.3-codex",
                prompt="do work",
                session_id=None,
                json_stream=False,
            )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.session_id, "019cb4cf-367a-77c3-8cb7-c37eed7ee30e")
        self.assertEqual(result.final_message, "TEST_OK")

        called_cmd = popen.call_args.args[0]
        self.assertNotIn("--json", called_cmd)

    def test_plain_stream_ignores_claude_code_metadata(self):
        lines = [
            "Claude Code v1.2.0\n",
            "workdir: /tmp/project\n",
            "model: claude-sonnet-4\n",
            "provider: anthropic\n",
            "approval: never\n",
            "sandbox: workspace-write\n",
            "thinking\n",
            "Implemented the remaining formatter cleanup.\n",
            "tokens used\n",
        ]

        with patch("src.codex_engine.subprocess.Popen", return_value=_FakeProcess(lines)):
            result = run_codex_iteration(
                cwd=Path("/tmp"),
                model="claude-sonnet-4",
                prompt="do work",
                session_id=None,
                json_stream=False,
            )

        self.assertEqual(result.final_message, "Implemented the remaining formatter cleanup.")

    def test_plain_stream_ignores_gemini_cli_metadata(self):
        lines = [
            "Gemini CLI v0.1.14\n",
            "workdir: /tmp/project\n",
            "model: gemini-2.5-pro\n",
            "provider: google\n",
            "reasoning effort: high\n",
            "reasoning summaries: auto\n",
            "All provider formatters are now normalized.\n",
            "tokens used\n",
        ]

        with patch("src.codex_engine.subprocess.Popen", return_value=_FakeProcess(lines)):
            result = run_codex_iteration(
                cwd=Path("/tmp"),
                model="gemini-2.5-pro",
                prompt="do work",
                session_id=None,
                json_stream=False,
            )

        self.assertEqual(result.final_message, "All provider formatters are now normalized.")

    def test_supports_custom_sandbox_mode(self):
        lines = ['{"type":"item.completed","item":{"type":"message","text":"ok"}}\n']

        with patch("src.codex_engine.subprocess.Popen", return_value=_FakeProcess(lines)) as popen:
            run_codex_iteration(
                cwd=Path("/tmp"),
                model="gpt-5.3-codex",
                prompt="do work",
                session_id=None,
                sandbox_mode="read-only",
            )

        called_cmd = popen.call_args.args[0]
        self.assertEqual(called_cmd[:4], ["codex", "--search", "-s", "read-only"])


if __name__ == "__main__":
    unittest.main()
