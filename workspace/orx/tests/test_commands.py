from __future__ import annotations

import unittest

from orx.commands import CommandValidationError, normalize_command


class CommandNormalizationTests(unittest.TestCase):
    def test_steer_is_interrupt_priority_command(self) -> None:
        command = normalize_command(
            "steer",
            issue_key="PRO-15",
            runner_id="runner-a",
            payload={"instruction": "stay in tmux session"},
        )

        self.assertEqual(command.command_kind, "steer")
        self.assertEqual(command.disposition, "interrupt")
        self.assertEqual(command.priority, 10)
        self.assertEqual(command.payload["session_residency"], "tmux")
        self.assertIsNone(command.replacement_key)

    def test_pause_resume_share_auto_replacement_family(self) -> None:
        pause = normalize_command(
            "pause",
            issue_key="PRO-15",
            runner_id="runner-a",
        )
        resume = normalize_command(
            "resume",
            issue_key="PRO-15",
            runner_id="runner-a",
        )

        self.assertEqual(pause.replacement_key, resume.replacement_key)
        self.assertEqual(
            pause.replacement_key,
            "execution-state:PRO-15:runner-a",
        )

    def test_explicit_replacement_key_overrides_auto_family(self) -> None:
        command = normalize_command(
            "run",
            issue_key="PRO-15",
            runner_id="runner-a",
            replacement_key="telegram:message:42",
        )

        self.assertEqual(command.replacement_key, "telegram:message:42")

    def test_command_requires_session_scope(self) -> None:
        with self.assertRaises(CommandValidationError):
            normalize_command("run")


if __name__ == "__main__":
    unittest.main()
