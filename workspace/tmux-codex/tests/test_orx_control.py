import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.orx_control import prepare_linear_issue_context


class PrepareLinearIssueContextTests(unittest.TestCase):
    def test_accepts_nested_project_record_from_orx_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp)
            repo_root = dev / "workspace" / "tmux-codex"
            repo_root.mkdir(parents=True, exist_ok=True)
            project_context = {
                "project": {
                    "project_key": "tmux-codex",
                    "display_name": "tmux-codex",
                    "repo_root": str(repo_root),
                }
            }
            issue = {
                "identifier": "PRO-102",
                "title": "New runner live smoke",
                "description": "Smoke this issue from ORX.",
                "project_name": "tmux-codex",
                "state_type": "backlog",
            }

            with (
                patch("src.orx_control.ensure_issue_worktree") as ensure_worktree,
                patch("src.orx_control.update_linear_issue", return_value=None),
            ):
                prepared = prepare_linear_issue_context(
                    dev=str(dev),
                    project_key="tmux-codex",
                    project_context=project_context,
                    issue=issue,
                    runner_id="main",
                )

            expected_worktree = dev / "worktrees" / "tmux-codex" / "pro-102"
            ensure_worktree.assert_called_once()
            self.assertEqual(prepared.project_root, repo_root.resolve())
            self.assertEqual(prepared.worktree_path, expected_worktree.resolve())
            self.assertEqual(prepared.snapshot["repo_root"], str(repo_root.resolve()))
            self.assertEqual(prepared.snapshot["worktree"], str(expected_worktree.resolve()))
            self.assertEqual(prepared.snapshot["project_name"], "tmux-codex")


if __name__ == "__main__":
    unittest.main()
