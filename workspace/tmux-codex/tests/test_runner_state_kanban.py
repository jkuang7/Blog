import tempfile
import unittest
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.runner_state import (
    build_action_idempotency_key,
    build_action_queue_payload,
    build_runner_state_paths_for_root,
    build_reconcile_result_payload,
    derive_kanban_runtime_view,
    default_kanban_state,
    default_runner_state,
    load_or_init_kanban_state,
    managed_runner_files,
    normalize_kanban_state,
    normalize_runner_state,
    write_json,
)


class KanbanRunnerStateTests(unittest.TestCase):
    def test_default_runner_state_includes_ticket_native_migration_flags(self):
        state = default_runner_state("blog", "main")

        self.assertEqual(state["runtime_policy"]["runner_mode"], "exec")
        self.assertEqual(state["runtime_policy"]["task_source"], "github_mcp_project_issues")
        self.assertEqual(state["runtime_policy"]["completion_policy"], "tasks_done_and_gates_green")
        self.assertEqual(state["runtime_policy"]["kanban_enabled"], True)

    def test_default_kanban_state_is_ticket_native_and_selecting(self):
        state = default_kanban_state("blog")

        self.assertEqual(state["mode"], "ticket_native")
        self.assertEqual(state["phase"], "selecting")
        self.assertEqual(state["loop"]["continue_until"], "board_complete_or_all_blocked")
        self.assertEqual(state["active_issue"], None)
        self.assertIn("human_approval_required", state["conditions"])
        self.assertEqual(state["reconcile"]["actions"][0]["action"], "sync_issue")

    def test_normalize_runner_state_backfills_new_runtime_policy_fields(self):
        normalized, changed = normalize_runner_state(
            {"project": "blog", "runner_id": "main", "runtime_policy": {"runner_mode": "kanban"}},
            project="blog",
            runner_id="main",
        )

        self.assertTrue(changed)
        self.assertEqual(normalized["runtime_policy"]["runner_mode"], "kanban")
        self.assertEqual(normalized["runtime_policy"]["task_source"], "github_mcp_project_issues")
        self.assertEqual(normalized["runtime_policy"]["completion_policy"], "tasks_done_and_gates_green")
        self.assertEqual(normalized["runtime_policy"]["kanban_enabled"], True)

    def test_normalize_kanban_state_requires_exact_issue_url(self):
        normalized, changed = normalize_kanban_state(
            {
                "project": "blog",
                "phase": "blocked",
                "active_issue": {"repo": "jkuang7/blog", "number": 3},
                "dependencies": {"depends_on": ["  issue-1  ", "", 7]},
            },
            project="blog",
        )

        self.assertTrue(changed)
        self.assertIsNone(normalized["active_issue"])
        self.assertEqual(normalized["dependencies"]["depends_on"], ["issue-1"])
        self.assertEqual(normalized["phase"], "blocked")

    def test_normalize_kanban_state_backfills_conditions_reconcile_and_drift(self):
        normalized, changed = normalize_kanban_state(
            {
                "project": "blog",
                "active_issue": {"url": "https://github.com/jkuang7/blog/issues/3"},
            },
            project="blog",
        )

        self.assertTrue(changed)
        self.assertIn("conditions", normalized)
        self.assertIn("drift", normalized)
        self.assertIn("reconcile", normalized)
        self.assertIn("human_approval_required", normalized["conditions"])
        self.assertIn("message", normalized["conditions"]["human_approval_required"])
        self.assertEqual(normalized["drift"]["github"]["detected"], False)
        self.assertIsInstance(normalized["reconcile"]["actions"], list)

    def test_load_or_init_kanban_state_creates_and_normalizes_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp)
            project_root = dev / "Repos" / "blog"
            project_root.mkdir(parents=True)
            paths = build_runner_state_paths_for_root(
                project_root=project_root,
                dev=str(dev),
                project="blog",
                runner_id="main",
            )
            paths.runner_dir.mkdir(parents=True, exist_ok=True)
            write_json(
                paths.kanban_state_json,
                {
                    "project": "blog",
                    "phase": "unknown",
                    "active_issue": {"url": "https://github.com/jkuang7/blog/issues/3", "repo": "jkuang7/blog"},
                },
            )

            state = load_or_init_kanban_state(paths, "blog")

            self.assertEqual(state["phase"], "selecting")
            self.assertEqual(state["active_issue"]["url"], "https://github.com/jkuang7/blog/issues/3")

    def test_managed_runner_files_includes_kanban_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp)
            project_root = dev / "Repos" / "blog"
            project_root.mkdir(parents=True)
            paths = build_runner_state_paths_for_root(
                project_root=project_root,
                dev=str(dev),
                project="blog",
                runner_id="main",
            )

            files = managed_runner_files(paths)

            self.assertIn(paths.kanban_state_json, files)
            self.assertIn(paths.runner_status_json, files)
            self.assertIn(paths.action_queue_json, files)
            self.assertIn(paths.reconcile_result_json, files)
            self.assertIn(paths.control_db, files)

    def test_derive_kanban_runtime_view_sets_human_approval_condition_and_action(self):
        runner_state = default_runner_state("blog", "main")
        kanban_state = default_kanban_state("blog")

        derived = derive_kanban_runtime_view(
            state=runner_state,
            kanban_state=kanban_state,
            enable_pending_exists=True,
        )

        self.assertEqual(derived["conditions"]["human_approval_required"]["status"], True)
        self.assertTrue(derived["conditions"]["human_approval_required"]["message"])
        self.assertEqual(
            derived["conditions"]["ready_for_execution"]["reason"],
            "no_active_issue",
        )
        self.assertEqual(
            derived["reconcile"]["actions"][1]["action"],
            "wait_for_human_approval",
        )

    def test_derive_kanban_runtime_view_detects_workspace_drift_and_repairs_before_execution(self):
        runner_state = default_runner_state("blog", "main")
        runner_state["git_branch"] = "feature/issue-3"
        runner_state["git_worktree"] = "/tmp/worktrees/blog"

        kanban_state = default_kanban_state("blog")
        kanban_state["phase"] = "executing"
        kanban_state["active_issue"] = {
            "url": "https://github.com/jkuang7/blog/issues/3",
            "repo": "jkuang7/blog",
            "number": 3,
            "title": "Ship it",
        }
        kanban_state["active_checkout"] = {
            "repo_root": "/tmp/repos/blog",
            "worktree": "/tmp/worktrees/blog-stale",
            "branch": "feature/issue-3-stale",
        }

        derived = derive_kanban_runtime_view(
            state=runner_state,
            kanban_state=kanban_state,
            enable_pending_exists=False,
        )

        self.assertEqual(derived["drift"]["workspace"]["detected"], True)
        self.assertEqual(derived["drift"]["workspace"]["reason"], "worktree_mismatch")
        self.assertEqual(derived["conditions"]["workspace_healthy"]["status"], False)
        self.assertEqual(derived["reconcile"]["actions"][1]["action"], "repair_workspace")
        self.assertEqual(derived["reconcile"]["actions"][1]["stage"], "recover")
        self.assertTrue(derived["reconcile"]["actions"][1]["idempotency_key"])

    def test_derive_kanban_runtime_view_requires_enhance_for_large_unrouted_ticket(self):
        runner_state = default_runner_state("blog", "main")
        kanban_state = default_kanban_state("blog")
        kanban_state["phase"] = "executing"
        kanban_state["active_issue"] = {
            "url": "https://github.com/jkuang7/blog/issues/11",
            "repo": "jkuang7/blog",
            "number": 11,
            "title": "Big migration",
            "complexity": "XL",
            "routing": "unresolved",
        }
        kanban_state["active_checkout"] = {
            "repo_root": "/tmp/repos/blog",
            "worktree": "/tmp/worktrees/blog",
            "branch": "feature/issue-11",
        }

        derived = derive_kanban_runtime_view(
            state=runner_state,
            kanban_state=kanban_state,
        )

        self.assertEqual(derived["reconcile"]["stage_results"]["classify"]["decision"], "enhance_required")
        self.assertEqual(derived["conditions"]["planning_satisfied"]["status"], False)
        self.assertEqual(derived["conditions"]["ready_for_execution"]["reason"], "enhance_required")
        self.assertIn("readiness", derived["reconcile"]["controller"])
        self.assertEqual(derived["reconcile"]["actions"][1]["action"], "spawn_refinement_agent")

    def test_derive_kanban_runtime_view_requires_enhance_for_phase_parent_ticket(self):
        runner_state = default_runner_state("blog", "main")
        kanban_state = default_kanban_state("blog")
        kanban_state["phase"] = "executing"
        kanban_state["active_issue"] = {
            "url": "https://github.com/jkuang7/blog/issues/12",
            "repo": "jkuang7/blog",
            "number": 12,
            "title": "Umbrella tracker",
            "issue_class": "phase_parent",
            "complexity": "M",
            "routing": "backend",
        }
        kanban_state["active_checkout"] = {
            "repo_root": "/tmp/repos/blog",
            "worktree": "/tmp/worktrees/blog",
            "branch": "feature/issue-12",
        }

        derived = derive_kanban_runtime_view(
            state=runner_state,
            kanban_state=kanban_state,
        )

        self.assertEqual(derived["reconcile"]["stage_results"]["classify"]["decision"], "enhance_required")
        self.assertEqual(derived["reconcile"]["actions"][1]["action"], "spawn_refinement_agent")

    def test_derive_kanban_runtime_view_prefers_child_ticket_over_parent_tracker(self):
        runner_state = default_runner_state("blog", "main")
        kanban_state = default_kanban_state("blog")
        kanban_state["phase"] = "executing"
        kanban_state["active_issue"] = {
            "url": "https://github.com/jkuang7/blog/issues/12",
            "repo": "jkuang7/blog",
            "number": 12,
            "title": "Umbrella tracker",
            "issue_class": "phase_parent",
            "complexity": "M",
            "routing": "backend",
        }
        kanban_state["dependencies"]["children"] = ["https://github.com/jkuang7/blog/issues/13"]
        kanban_state["active_checkout"] = {
            "repo_root": "/tmp/repos/blog",
            "worktree": "/tmp/worktrees/blog",
            "branch": "feature/issue-12",
        }

        derived = derive_kanban_runtime_view(state=runner_state, kanban_state=kanban_state)

        self.assertEqual(derived["reconcile"]["actions"][1]["action"], "select_next_issue")
        self.assertEqual(derived["reconcile"]["controller"]["selection_policy"]["prefer_executable_child"], True)

    def test_derive_kanban_runtime_view_requests_followup_for_blocked_ticket_without_existing_followup(self):
        runner_state = default_runner_state("blog", "main")
        kanban_state = default_kanban_state("blog")
        kanban_state["phase"] = "blocked"
        kanban_state["active_issue"] = {
            "url": "https://github.com/jkuang7/blog/issues/14",
            "repo": "jkuang7/blog",
            "number": 14,
            "title": "Blocked task",
            "issue_class": "feature",
            "complexity": "S",
            "routing": "backend",
        }
        kanban_state["blocker"] = {
            "is_blocked": True,
            "category": "dependency",
            "reason": "waiting on migration",
            "needs": "dependency_clear",
            "resume_from": "execution",
            "external": True,
        }
        kanban_state["active_checkout"] = {
            "repo_root": "/tmp/repos/blog",
            "worktree": "/tmp/worktrees/blog",
            "branch": "feature/issue-14",
        }

        derived = derive_kanban_runtime_view(state=runner_state, kanban_state=kanban_state)

        self.assertEqual(derived["reconcile"]["actions"][1]["action"], "create_followup_ticket")
        self.assertEqual(derived["reconcile"]["controller"]["followup_policy"]["required"], True)
        mutation = derived["reconcile"]["actions"][1]["payload"]["mutation"]
        self.assertEqual(mutation["operation"], "create_issue")
        self.assertEqual(mutation["parent_issue_url"], "https://github.com/jkuang7/blog/issues/14")
        self.assertIn("Follow-up: Blocked task", mutation["title"])
        self.assertIn("## Ticket Relations", mutation["body"])
        self.assertTrue(mutation["dedupe_key"].startswith("github:create_issue:jkuang7/blog:"))

    def test_derive_kanban_runtime_view_surfaces_ticket_relations_from_structured_metadata(self):
        runner_state = default_runner_state("blog", "main")
        kanban_state = default_kanban_state("blog")
        kanban_state["phase"] = "executing"
        kanban_state["active_issue"] = {
            "url": "https://github.com/jkuang7/blog/issues/15",
            "repo": "jkuang7/blog",
            "number": 15,
            "title": "Child task",
            "issue_class": "feature",
            "complexity": "S",
            "routing": "backend",
            "parent": "https://github.com/jkuang7/blog/issues/10",
            "blocked_by": "none",
            "unblocks": "https://github.com/jkuang7/blog/issues/16",
            "depends_on": "none",
            "merge_into": "main",
            "resume_from": "blog@issue-15",
        }
        kanban_state["active_checkout"] = {
            "repo_root": "/tmp/repos/blog",
            "worktree": "/tmp/worktrees/blog",
            "branch": "feature/issue-15",
        }

        derived = derive_kanban_runtime_view(state=runner_state, kanban_state=kanban_state)

        relations = derived["reconcile"]["controller"]["ticket_relations"]
        self.assertEqual(relations["parent"], "https://github.com/jkuang7/blog/issues/10")
        self.assertEqual(relations["unblocks"], "https://github.com/jkuang7/blog/issues/16")
        self.assertEqual(relations["merge_into"], "main")
        self.assertEqual(relations["resume_from"], "blog@issue-15")

    def test_derive_kanban_runtime_view_builds_blocker_comment_mutation_for_dependency_block(self):
        runner_state = default_runner_state("blog", "main")
        kanban_state = default_kanban_state("blog")
        kanban_state["phase"] = "executing"
        kanban_state["active_issue"] = {
            "url": "https://github.com/jkuang7/blog/issues/18",
            "repo": "jkuang7/blog",
            "number": 18,
            "title": "Child task",
            "issue_class": "feature",
            "complexity": "S",
            "routing": "backend",
        }
        kanban_state["dependencies"]["blocked_by"] = ["https://github.com/jkuang7/blog/issues/17"]
        kanban_state["active_checkout"] = {
            "repo_root": "/tmp/repos/blog",
            "worktree": "/tmp/worktrees/blog",
            "branch": "feature/issue-18",
        }

        derived = derive_kanban_runtime_view(state=runner_state, kanban_state=kanban_state)

        self.assertEqual(derived["reconcile"]["actions"][1]["action"], "write_blocker_comment")
        mutation = derived["reconcile"]["actions"][1]["payload"]["mutation"]
        self.assertEqual(mutation["operation"], "create_issue_comment")
        self.assertEqual(mutation["repo"], "jkuang7/blog")
        self.assertIn("tmux-codex:blocker-comment", mutation["body"])
        self.assertTrue(mutation["dedupe_key"].startswith("github:create_issue_comment:jkuang7/blog:"))

    def test_build_action_idempotency_key_is_stable(self):
        first = build_action_idempotency_key(
            project="blog",
            runner_id="main",
            phase="verify",
            action="repair_workspace",
            issue_url="https://github.com/jkuang7/blog/issues/3",
        )
        second = build_action_idempotency_key(
            project="blog",
            runner_id="main",
            phase="verify",
            action="repair_workspace",
            issue_url="https://github.com/jkuang7/blog/issues/3",
        )
        self.assertEqual(first, second)

    def test_build_action_queue_and_reconcile_result_payloads_are_derived_from_reconcile_state(self):
        runner_state = default_runner_state("blog", "main")
        kanban_state = default_kanban_state("blog")
        kanban_state["phase"] = "executing"
        kanban_state["active_issue"] = {
            "url": "https://github.com/jkuang7/blog/issues/9",
            "repo": "jkuang7/blog",
            "number": 9,
            "title": "Ship it",
        }
        kanban_state["active_checkout"] = {
            "repo_root": "/tmp/repos/blog",
            "worktree": "/tmp/worktrees/blog",
            "branch": "feature/issue-9",
        }
        derived = derive_kanban_runtime_view(
            state=runner_state,
            kanban_state=kanban_state,
        )

        action_queue = build_action_queue_payload(
            project="blog",
            runner_id="main",
            kanban_state=derived,
        )
        reconcile_result = build_reconcile_result_payload(
            project="blog",
            runner_id="main",
            kanban_state=derived,
        )

        self.assertEqual(action_queue["desired_state"], "advance_active_issue")
        self.assertTrue(action_queue["actions"])
        self.assertIn("mutation_intents", action_queue)
        self.assertEqual(action_queue["mutation_intents"], [])
        self.assertIn("dispatch", reconcile_result["stage_results"])
        self.assertIn("controller", reconcile_result)
        self.assertIn("unmet_conditions", reconcile_result)
        self.assertIn("mutation_intents", reconcile_result)


if __name__ == "__main__":
    unittest.main()
