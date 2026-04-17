import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.orx_control import build_issue_execution_brief, prepare_linear_issue_context


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
            self.assertEqual(prepared.snapshot["execution_brief"]["objective_title"], "New runner live smoke")

    def test_build_issue_execution_brief_reads_structured_linear_ticket(self) -> None:
        issue = {
            "identifier": "PRO-204",
            "title": "Fallback title",
            "description": """## Title
Replace the legacy queue wording in the runner picker

## Why
The runner picker still talks about local tasks instead of ORX queue state.

## Goal
Make the picker describe ORX queue state in operator-facing language.

## Scope
### In scope
- Replace the visible task-count wording in the picker.
- Keep the project rows aligned with ORX queue state.

### Out of scope
- Rewriting the runner control loop.

## Requirements
- Keep the selection flow deterministic.

## Acceptance Criteria
- Given the ORX-backed picker
- When the project list is rendered
- Then the operator sees queue language instead of task language.

## Technical Notes
- Touch only tmux-codex surfaces involved in picker rendering.

## Definition of Done
- The picker reads clearly.
- Verification evidence is recorded.

## Execution Context
- Worktree path: `/tmp/worktrees/tmux-codex/pro-204`
- Branch: `linear/pro-204`
""",
        }

        brief = build_issue_execution_brief(issue)

        assert brief is not None
        self.assertEqual(brief["objective_title"], "Replace the legacy queue wording in the runner picker")
        self.assertIn("Replace the visible task-count wording in the picker.", brief["scope_in"])
        self.assertIn("Keep the selection flow deterministic.", brief["constraints"])
        self.assertIn("Worktree path: `/tmp/worktrees/tmux-codex/pro-204`", brief["constraints"])
        self.assertIn(
            "Then the operator sees queue language instead of task language.",
            brief["success_criteria"],
        )

    def test_build_issue_execution_brief_prefers_compact_hidden_metadata(self) -> None:
        issue = {
            "identifier": "PRO-205",
            "title": "Fallback title",
            "metadata": {
                "codex_execution_brief": {
                    "objective_title": "Use the packet worktree for grouped leaves",
                    "problem": "Grouped leaves were reseeding from per-issue worktrees.",
                    "goal": "Keep related leaves in one packet worktree until HIL merge.",
                    "scope_in": ["Use the packet worktree.", "Preserve deterministic routing."],
                    "scope_out": ["Do not auto-merge to main."],
                    "success_criteria": ["Grouped leaves share one packet worktree."],
                    "constraints": ["Repo root: /tmp/workspace/orx", "Branch: linear/pro-71-alpha"],
                }
            },
            "description": "Thin placeholder description",
        }

        brief = build_issue_execution_brief(issue)

        assert brief is not None
        self.assertEqual(brief["objective_title"], "Use the packet worktree for grouped leaves")
        self.assertEqual(brief["goal"], "Keep related leaves in one packet worktree until HIL merge.")
        self.assertIn("Use the packet worktree.", brief["scope_in"])
        self.assertIn("Do not auto-merge to main.", brief["scope_out"])
        self.assertIn("Grouped leaves share one packet worktree.", brief["success_criteria"])


if __name__ == "__main__":
    unittest.main()
