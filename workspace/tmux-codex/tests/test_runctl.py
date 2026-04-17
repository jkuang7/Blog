import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.runctl import RUNNER_PROMPT_NAMES, clear_runner_state, create_runner_state, inspect_runner_start_state, parse_args, run
from src.runner_graph import _load_graph_config
from src.runner_state import build_runner_state_paths, build_runner_state_paths_for_root, compute_worktree_fingerprint, read_json, write_json


class RunctlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dev = Path(self.tmp.name)
        (self.dev / "Repos" / "blog").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _paths(self):
        return build_runner_state_paths(
            dev=str(self.dev),
            project="blog",
            runner_id="main",
        )

    def _write_tasks(self, tasks: list[dict]) -> None:
        paths = self._paths()
        payload = {
            "objective_id": "OBJ-TEST",
            "tasks": tasks,
        }
        write_json(paths.tasks_json, payload)

    def _write_context_pack(self, payload: dict) -> None:
        project_root = self.dev / "Repos" / "blog"
        codex_dir = project_root / ".codex"
        codex_dir.mkdir(parents=True, exist_ok=True)
        write_json(codex_dir / "context-pack.json", payload)

    def test_setup_creates_canonical_files_and_removes_legacy_views(self):
        paths = self._paths()
        paths.memory_dir.mkdir(parents=True, exist_ok=True)
        paths.runner_dir.mkdir(parents=True, exist_ok=True)
        (paths.memory_dir / "GOALS.md").write_text("# legacy\n", encoding="utf-8")
        paths.legacy_refactor_status_file.write_text("# old status\n", encoding="utf-8")
        (paths.runner_dir / "RUNNER_NEXT.md").write_text("legacy\n", encoding="utf-8")
        (paths.runner_dir / "RUNNER_DOD.md").write_text("legacy\n", encoding="utf-8")
        (paths.runner_dir / "RUNNER_PLAN.md").write_text("legacy\n", encoding="utf-8")
        (paths.runner_dir / "PRD.md").write_text("legacy\n", encoding="utf-8")
        (paths.runner_dir / "TASKS.md").write_text("legacy\n", encoding="utf-8")

        created = create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        self.assertTrue(created["ok"])

        self.assertTrue(paths.state_file.exists())
        self.assertTrue(paths.objective_json.exists())
        self.assertTrue(paths.seams_json.exists())
        self.assertTrue(paths.gaps_json.exists())
        self.assertTrue(paths.tasks_json.exists())
        self.assertTrue(paths.prd_json.exists())
        self.assertTrue(paths.kanban_state_json.exists())
        self.assertTrue(paths.runner_parity_json.exists())
        self.assertTrue(paths.exec_context_json.exists())
        self.assertTrue(paths.active_backlog_json.exists())
        self.assertTrue(paths.runner_status_json.exists())
        self.assertTrue(paths.action_queue_json.exists())
        self.assertTrue(paths.reconcile_result_json.exists())
        self.assertTrue(paths.ledger_file.exists())
        self.assertTrue(paths.project_prd_file.exists())
        self.assertTrue(paths.runner_handoff_file.exists())
        self.assertFalse(paths.dep_graph_json.exists())
        self.assertFalse(paths.graph_active_slice_json.exists())
        self.assertFalse(paths.graph_boundaries_json.exists())
        self.assertFalse(paths.graph_hotspots_json.exists())

        self.assertFalse((paths.memory_dir / "GOALS.md").exists())
        self.assertFalse(paths.legacy_refactor_status_file.exists())
        self.assertFalse((paths.runner_dir / "RUNNER_NEXT.md").exists())
        self.assertFalse((paths.runner_dir / "RUNNER_DOD.md").exists())
        self.assertFalse((paths.runner_dir / "RUNNER_PLAN.md").exists())
        self.assertFalse((paths.runner_dir / "PRD.md").exists())
        self.assertFalse((paths.runner_dir / "TASKS.md").exists())

        state = read_json(paths.state_file)
        self.assertIsNotNone(state)
        assert state is not None
        self.assertNotIn("conversation_digest_key", state)
        self.assertNotIn("conversation_digest_source", state)
        self.assertNotIn("conversation_digest_version", state)
        self.assertNotIn("conversation_seed_confidence", state)
        self.assertTrue(str(state.get("next_task_id", "")).strip())
        self.assertTrue(str(state.get("next_task", "")).strip())
        self.assertEqual(state["current_phase"], "verify")
        self.assertEqual(state["phase_status"], "active")
        self.assertEqual(state["phase_budget_minutes"], 45)

        exec_context = read_json(paths.exec_context_json)
        self.assertIsNotNone(exec_context)
        assert exec_context is not None
        self.assertEqual(exec_context["phase"], "verify")
        self.assertFalse(exec_context["graph_enabled"])
        self.assertFalse(exec_context["parity_enabled"])
        self.assertEqual(exec_context["parity_surface_ids"], [])
        self.assertIn("context_delta", exec_context)
        self.assertIn("context_sources", exec_context)
        source_paths = {
            str(Path(str(entry.get("path", ""))).resolve())
            for entry in exec_context["context_sources"]
            if isinstance(entry, dict) and str(entry.get("path", "")).strip()
        }
        self.assertIn(str(paths.project_prd_file.resolve()), source_paths)
        self.assertIn(str(paths.kanban_state_json.resolve()), source_paths)
        self.assertNotIn(str(paths.runner_handoff_file.resolve()), source_paths)
        self.assertIn("Next task:", paths.runner_handoff_file.read_text(encoding="utf-8"))
        active_backlog = read_json(paths.active_backlog_json)
        self.assertIsNotNone(active_backlog)
        assert active_backlog is not None
        self.assertEqual(active_backlog["next_task_id"], state["next_task_id"])
        self.assertIsNone(active_backlog["selected_task"]["graph_cluster_label"])
        self.assertFalse(active_backlog["selected_task"]["parity_enabled"])
        self.assertEqual(active_backlog["runtime_policy"]["task_source"], "orx_linear_project_issues")
        self.assertEqual(active_backlog["kanban"]["mode"], "ticket_native")
        self.assertEqual(active_backlog["kanban"]["continue_until"], "board_complete_or_all_blocked")
        kanban_state = read_json(paths.kanban_state_json)
        self.assertIsNotNone(kanban_state)
        assert kanban_state is not None
        self.assertEqual(kanban_state["mode"], "ticket_native")
        self.assertEqual(kanban_state["phase"], "selecting")
        self.assertEqual(
            Path(kanban_state["active_checkout"]["repo_root"]).resolve(),
            (self.dev / "Repos" / "blog").resolve(),
        )
        self.assertEqual(exec_context["runtime_policy"]["task_source"], "orx_linear_project_issues")
        self.assertEqual(exec_context["kanban_mode"], "ticket_native")
        self.assertEqual(exec_context["kanban_phase"], "selecting")
        self.assertEqual(exec_context["kanban_continue_until"], "board_complete_or_all_blocked")
        self.assertEqual(Path(exec_context["kanban_state_file"]).resolve(), paths.kanban_state_json.resolve())
        runner_status = read_json(paths.runner_status_json)
        self.assertIsNotNone(runner_status)
        assert runner_status is not None
        self.assertEqual(runner_status["runtime_policy"]["task_source"], "orx_linear_project_issues")
        self.assertEqual(runner_status["kanban"]["phase"], "selecting")
        self.assertEqual(runner_status["kanban"]["continue_until"], "board_complete_or_all_blocked")
        self.assertEqual(Path(runner_status["kanban"]["checkout_root"]).resolve(), (self.dev / "Repos" / "blog").resolve())
        self.assertEqual(runner_status["architecture"]["work_definition"], "orx_linear_project_issues")
        self.assertEqual(runner_status["architecture"]["execution_engine"], "tmux-codex")
        self.assertEqual(runner_status["architecture"]["operator_shell"], "telecodex")
        self.assertIn("unmet_conditions", runner_status)
        self.assertIn("reconcile", runner_status)
        self.assertIn("control", runner_status)
        action_queue = read_json(paths.action_queue_json)
        self.assertIsNotNone(action_queue)
        assert action_queue is not None
        self.assertTrue(action_queue["actions"])
        reconcile_result = read_json(paths.reconcile_result_json)
        self.assertIsNotNone(reconcile_result)
        assert reconcile_result is not None
        self.assertIn("stage_results", reconcile_result)
        self.assertIn("unmet_conditions", reconcile_result)

    def test_inspect_runner_start_state_requires_setup(self):
        result = inspect_runner_start_state(str(self.dev), "blog", "main")
        self.assertFalse(result["ok"])
        self.assertIn("Run /run_setup first", result["error"])

    def test_inspect_runner_start_state_passes_after_enable_approval(self):
        initial = create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        token = initial["enable_token"]
        approved = create_runner_state(str(self.dev), "blog", "main", approve_enable=token)
        self.assertTrue(approved["ok"])

        result = inspect_runner_start_state(str(self.dev), "blog", "main")
        self.assertTrue(result["ok"])
        self.assertEqual(result["next_task_id"], "TT-001")

    def test_setup_refresh_preserves_enablement_after_approval(self):
        initial = create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        token = initial["enable_token"]
        approved = create_runner_state(str(self.dev), "blog", "main", approve_enable=token)
        self.assertTrue(approved["ok"])

        refreshed = create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        self.assertTrue(refreshed["ok"])

        paths = self._paths()
        state = read_json(paths.state_file)
        self.assertIsNotNone(state)
        assert state is not None
        self.assertTrue(state["enabled"])
        self.assertEqual(state["status"], "ready")
        self.assertFalse(paths.enable_pending.exists())

    def test_inspect_runner_start_state_auto_repairs_runner_prompt_install_drift(self):
        initial = create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        token = initial["enable_token"]
        approved = create_runner_state(str(self.dev), "blog", "main", approve_enable=token)
        self.assertTrue(approved["ok"])

        with patch(
            "src.runctl._validate_runner_prompt_install",
            side_effect=[
                "Installed runner prompt is not linked to canonical source.",
                None,
            ],
        ), patch("src.runctl._repair_runner_prompt_install", return_value=None) as repair_prompt_install:
            result = inspect_runner_start_state(str(self.dev), "blog", "main")

        self.assertTrue(result["ok"])
        repair_prompt_install.assert_called_once()

    def test_inspect_runner_start_state_fails_when_runner_prompt_auto_repair_does_not_fix_drift(self):
        initial = create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        token = initial["enable_token"]
        approved = create_runner_state(str(self.dev), "blog", "main", approve_enable=token)
        self.assertTrue(approved["ok"])

        with patch(
            "src.runctl._validate_runner_prompt_install",
            return_value="Installed runner command is not linked to canonical source.",
        ), patch(
            "src.runctl._repair_runner_prompt_install",
            return_value="Installed runner command is not linked to canonical source.",
        ):
            result = inspect_runner_start_state(str(self.dev), "blog", "main")

        self.assertFalse(result["ok"])
        self.assertIn("not linked to canonical source", result["error"])

    def test_inspect_runner_start_state_does_not_attempt_runner_prompt_auto_repair_when_canonical_prompt_missing(self):
        initial = create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        token = initial["enable_token"]
        approved = create_runner_state(str(self.dev), "blog", "main", approve_enable=token)
        self.assertTrue(approved["ok"])

        with patch(
            "src.runctl._validate_runner_prompt_install",
            return_value="Canonical runner command missing: /tmp/run_execute.md",
        ), patch("src.runctl._repair_runner_prompt_install") as repair_prompt_install:
            result = inspect_runner_start_state(str(self.dev), "blog", "main")

        self.assertFalse(result["ok"])
        self.assertIn("Canonical runner command missing", result["error"])
        repair_prompt_install.assert_not_called()

    def test_runner_prompt_validation_covers_all_canonical_runner_prompts(self):
        self.assertEqual(set(RUNNER_PROMPT_NAMES), {"run_setup", "run_execute", "run_govern", "add"})

    def test_inspect_runner_start_state_blocks_when_stop_lock_is_present(self):
        initial = create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        token = initial["enable_token"]
        approved = create_runner_state(str(self.dev), "blog", "main", approve_enable=token)
        self.assertTrue(approved["ok"])

        paths = self._paths()
        paths.stop_lock.write_text(
            "requested_at=2026-03-14T00:00:00Z\n"
            "source=runner_no_progress\n"
            "reason=no_durable_progress_since_cycle_baseline\n",
            encoding="utf-8",
        )

        result = inspect_runner_start_state(str(self.dev), "blog", "main")

        self.assertFalse(result["ok"])
        self.assertIn("no durable progress", result["error"])
        self.assertIn("no_durable_progress_since_cycle_baseline", result["error"])

    def test_inspect_runner_start_state_auto_recovers_runner_update_failure_when_refresh_finds_actionable_task(self):
        initial = create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        token = initial["enable_token"]
        approved = create_runner_state(str(self.dev), "blog", "main", approve_enable=token)
        self.assertTrue(approved["ok"])

        project_root = self.dev / "Repos" / "blog"
        self._write_tasks(
            [
                {
                    "task_id": "TT-001",
                    "title": "Already complete",
                    "status": "done",
                    "priority": "p1",
                    "depends_on": [],
                    "project_root": str(project_root),
                    "target_branch": "main",
                    "acceptance": ["done"],
                    "validation": ["verify"],
                    "updated_at": "2026-03-01T00:00:00Z",
                },
                {
                    "task_id": "TT-002",
                    "title": "Recovered actionable task",
                    "status": "open",
                    "priority": "p1",
                    "depends_on": ["TT-001"],
                    "project_root": str(project_root),
                    "target_branch": "main",
                    "acceptance": ["done"],
                    "validation": ["verify"],
                    "updated_at": "2026-03-02T00:00:00Z",
                },
            ]
        )
        paths = self._paths()
        paths.stop_lock.write_text(
            "requested_at=2026-03-18T00:00:00Z\n"
            "source=runner_update_failure\n"
            "reason=update_exited_without_prepared_marker\n",
            encoding="utf-8",
        )

        result = inspect_runner_start_state(str(self.dev), "blog", "main")

        self.assertTrue(result["ok"])
        self.assertEqual(result["next_task_id"], "TT-002")
        self.assertFalse(paths.stop_lock.exists())

    def test_inspect_runner_start_state_keeps_update_failure_stop_lock_when_refresh_finds_no_actionable_task(self):
        initial = create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        token = initial["enable_token"]
        approved = create_runner_state(str(self.dev), "blog", "main", approve_enable=token)
        self.assertTrue(approved["ok"])

        project_root = self.dev / "Repos" / "blog"
        self._write_tasks(
            [
                {
                    "task_id": "TT-001",
                    "title": "Blocked downstream task",
                    "status": "open",
                    "priority": "p1",
                    "depends_on": ["TT-999"],
                    "project_root": str(project_root),
                    "target_branch": "main",
                    "acceptance": ["done"],
                    "validation": ["verify"],
                    "updated_at": "2026-03-01T00:00:00Z",
                },
            ]
        )
        paths = self._paths()
        paths.stop_lock.write_text(
            "requested_at=2026-03-18T00:00:00Z\n"
            "source=runner_update_failure\n"
            "reason=update_exited_without_prepared_marker\n",
            encoding="utf-8",
        )

        result = inspect_runner_start_state(str(self.dev), "blog", "main")

        self.assertFalse(result["ok"])
        self.assertIn("missing next-task info", result["error"])
        self.assertTrue(paths.stop_lock.exists())

    def test_inspect_runner_start_state_rejects_blocked_next_task(self):
        initial = create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        token = initial["enable_token"]
        approved = create_runner_state(str(self.dev), "blog", "main", approve_enable=token)
        self.assertTrue(approved["ok"])

        paths = self._paths()
        tasks_payload = read_json(paths.tasks_json)
        state = read_json(paths.state_file)
        self.assertIsNotNone(tasks_payload)
        self.assertIsNotNone(state)
        assert tasks_payload is not None
        assert state is not None
        tasks_payload["tasks"][0]["status"] = "blocked"
        tasks_payload["tasks"][0]["blocked_reason"] = "branch_enforcement_failed: expected main"
        write_json(paths.tasks_json, tasks_payload)
        state["next_task_id"] = tasks_payload["tasks"][0]["task_id"]
        state["next_task"] = tasks_payload["tasks"][0]["title"]
        write_json(paths.state_file, state)

        result = inspect_runner_start_state(str(self.dev), "blog", "main")

        self.assertFalse(result["ok"])
        self.assertIn("is blocked", result["error"])
        self.assertIn("branch_enforcement_failed: expected main", result["error"])

    def test_inspect_runner_start_state_repairs_stale_done_next_task(self):
        initial = create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        token = initial["enable_token"]
        approved = create_runner_state(str(self.dev), "blog", "main", approve_enable=token)
        self.assertTrue(approved["ok"])

        project_root = self.dev / "Repos" / "blog"
        self._write_tasks(
            [
                {
                    "task_id": "TT-001",
                    "title": "Already complete",
                    "status": "done",
                    "priority": "p1",
                    "depends_on": [],
                    "project_root": str(project_root),
                    "target_branch": "main",
                    "acceptance": ["done"],
                    "validation": ["verify"],
                    "updated_at": "2026-03-01T00:00:00Z",
                },
                {
                    "task_id": "TT-002",
                    "title": "Current open task",
                    "status": "open",
                    "priority": "p1",
                    "depends_on": [],
                    "project_root": str(project_root),
                    "target_branch": "main",
                    "acceptance": ["done"],
                    "validation": ["verify"],
                    "updated_at": "2026-03-02T00:00:00Z",
                },
            ]
        )

        paths = self._paths()
        state = read_json(paths.state_file)
        self.assertIsNotNone(state)
        assert state is not None
        state["next_task_id"] = "TT-001"
        state["next_task"] = "Already complete"
        write_json(paths.state_file, state)

        result = inspect_runner_start_state(str(self.dev), "blog", "main")
        self.assertTrue(result["ok"])
        self.assertEqual(result["next_task_id"], "TT-002")
        refreshed_state = read_json(paths.state_file)
        self.assertIsNotNone(refreshed_state)
        assert refreshed_state is not None
        self.assertEqual(refreshed_state["next_task_id"], "TT-002")

    def test_setup_seeds_concrete_tasks_from_prd_when_missing(self):
        paths = self._paths()
        write_json(
            paths.prd_json,
            {
                "objective_id": "OBJ-TEST",
                "title": "Time-track architecture migration",
                "scope_in": [
                    "Remove Tailwind-era styling from desktop core surfaces.",
                    "Move shared primitive styling ownership into Panda recipes.",
                ],
                "scope_out": ["Unscoped cleanup not named above"],
                "success_criteria": ["New architecture owns the touched surfaces without generic fallback tasks."],
                "constraints": ["Single runner_id=main"],
                "project_root": str((self.dev / "Repos" / "blog").resolve()),
                "updated_at": "2026-03-21T00:00:00Z",
            },
        )

        created = create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        self.assertTrue(created["ok"])

        tasks_payload = read_json(paths.tasks_json)
        self.assertIsNotNone(tasks_payload)
        assert tasks_payload is not None
        tasks = tasks_payload.get("tasks", [])
        self.assertEqual([task["task_id"] for task in tasks], ["TT-001", "TT-002"])
        self.assertEqual(tasks[0]["title"], "Remove Tailwind-era styling from desktop core surfaces.")
        self.assertEqual(tasks[1]["title"], "Move shared primitive styling ownership into Panda recipes.")
        self.assertNotIn("Execute the next concrete validated slice toward the active objective.", {task["title"] for task in tasks})

    def test_setup_selects_next_task_deterministically_from_tasks_json(self):
        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        project_root = self.dev / "Repos" / "blog"

        self._write_tasks(
            [
                {
                    "task_id": "TT-010",
                    "title": "Lower priority task",
                    "status": "open",
                    "priority": "p1",
                    "depends_on": [],
                    "project_root": str(project_root),
                    "target_branch": "main",
                    "acceptance": ["done"],
                    "validation": ["verify"],
                    "updated_at": "2026-03-01T00:00:00Z",
                },
                {
                    "task_id": "TT-020",
                    "title": "Highest priority oldest",
                    "status": "open",
                    "priority": "p0",
                    "depends_on": [],
                    "project_root": str(project_root),
                    "target_branch": "main",
                    "acceptance": ["done"],
                    "validation": ["verify"],
                    "updated_at": "2026-03-01T00:00:00Z",
                },
                {
                    "task_id": "TT-030",
                    "title": "Highest priority newer",
                    "status": "open",
                    "priority": "p0",
                    "depends_on": [],
                    "project_root": str(project_root),
                    "target_branch": "main",
                    "acceptance": ["done"],
                    "validation": ["verify"],
                    "updated_at": "2026-03-03T00:00:00Z",
                },
                {
                    "task_id": "TT-040",
                    "title": "Unresolved dependency",
                    "status": "open",
                    "priority": "p0",
                    "depends_on": ["TT-999"],
                    "project_root": str(project_root),
                    "target_branch": "main",
                    "acceptance": ["done"],
                    "validation": ["verify"],
                    "updated_at": "2026-02-28T00:00:00Z",
                },
            ]
        )

        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        state = read_json(self._paths().state_file)
        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state["next_task_id"], "TT-020")
        self.assertEqual(state["next_task"], "Highest priority oldest")

    def test_setup_blocks_unresolved_dependency_tasks_and_keeps_only_actionable_next_task_open(self):
        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        project_root = self.dev / "Repos" / "blog"

        self._write_tasks(
            [
                {
                    "task_id": "TT-001",
                    "title": "First actionable task",
                    "status": "open",
                    "priority": "p1",
                    "depends_on": [],
                    "project_root": str(project_root),
                    "target_branch": "main",
                    "acceptance": ["done"],
                    "validation": ["verify"],
                    "updated_at": "2026-03-01T00:00:00Z",
                },
                {
                    "task_id": "TT-002",
                    "title": "Downstream task",
                    "status": "open",
                    "priority": "p0",
                    "depends_on": ["TT-001"],
                    "project_root": str(project_root),
                    "target_branch": "main",
                    "acceptance": ["done"],
                    "validation": ["verify"],
                    "updated_at": "2026-03-01T00:00:00Z",
                },
            ]
        )

        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        paths = self._paths()
        tasks_payload = read_json(paths.tasks_json)
        state = read_json(paths.state_file)
        self.assertIsNotNone(tasks_payload)
        self.assertIsNotNone(state)
        assert tasks_payload is not None
        assert state is not None
        task_map = {task["task_id"]: task for task in tasks_payload["tasks"]}
        self.assertEqual(task_map["TT-001"]["status"], "open")
        self.assertEqual(task_map["TT-002"]["status"], "blocked")
        self.assertEqual(task_map["TT-002"]["blocked_reason"], "waiting_on: TT-001")
        self.assertEqual(state["next_task_id"], "TT-001")

    def test_setup_reopens_dependency_blocked_task_when_dependency_is_done(self):
        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        project_root = self.dev / "Repos" / "blog"

        self._write_tasks(
            [
                {
                    "task_id": "TT-001",
                    "title": "Already complete",
                    "status": "done",
                    "priority": "p1",
                    "depends_on": [],
                    "project_root": str(project_root),
                    "target_branch": "main",
                    "acceptance": ["done"],
                    "validation": ["verify"],
                    "updated_at": "2026-03-01T00:00:00Z",
                },
                {
                    "task_id": "TT-002",
                    "title": "Now actionable",
                    "status": "blocked",
                    "priority": "p0",
                    "depends_on": ["TT-001"],
                    "blocked_reason": "waiting_on: TT-001",
                    "project_root": str(project_root),
                    "target_branch": "main",
                    "acceptance": ["done"],
                    "validation": ["verify"],
                    "updated_at": "2026-03-01T00:00:00Z",
                },
            ]
        )

        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        paths = self._paths()
        tasks_payload = read_json(paths.tasks_json)
        state = read_json(paths.state_file)
        self.assertIsNotNone(tasks_payload)
        self.assertIsNotNone(state)
        assert tasks_payload is not None
        assert state is not None
        task_map = {task["task_id"]: task for task in tasks_payload["tasks"]}
        self.assertEqual(task_map["TT-002"]["status"], "open")
        self.assertNotIn("blocked_reason", task_map["TT-002"])
        self.assertEqual(state["next_task_id"], "TT-002")

    def test_setup_preserves_task_profile_metadata_and_writes_exec_context_scope(self):
        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        project_root = self.dev / "Repos" / "blog"

        self._write_tasks(
            [
                {
                    "task_id": "TT-010h1",
                    "title": "Migrate runtime-context family",
                    "status": "open",
                    "priority": "p1",
                    "depends_on": [],
                    "project_root": str(project_root),
                    "target_branch": "main",
                    "acceptance": ["Move runtime-context ownership into app-shell public seams."],
                    "validation": ["Run targeted runtime-context tests."],
                    "model_profile": "mini",
                    "profile_reason": "bounded app-shell runtime-context family",
                    "touch_paths": [
                        "desktop/src/app-layout/selectAppLayoutRuntimeContext*",
                        "desktop/src/app-layout/useAppLayoutRuntimeContext*",
                    ],
                    "validation_commands": [
                        "pnpm -C desktop exec vitest run src/app-layout/runtime-context.test.ts",
                    ],
                    "deprecation_phase": "consumer_migration",
                    "fanout_risk": "low",
                    "spillover_paths": ["desktop/src/app-store/**"],
                    "coupling_notes": ["Crossing into app-store selectors means the slice should be split or escalated."],
                    "updated_at": "2026-03-17T00:00:00Z",
                }
            ]
        )

        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)

        paths = self._paths()
        tasks_payload = read_json(paths.tasks_json)
        exec_context = read_json(paths.exec_context_json)
        self.assertIsNotNone(tasks_payload)
        self.assertIsNotNone(exec_context)
        assert tasks_payload is not None
        assert exec_context is not None

        task = tasks_payload["tasks"][0]
        self.assertEqual(task["model_profile"], "mini")
        self.assertEqual(task["profile_reason"], "bounded app-shell runtime-context family")
        self.assertEqual(task["touch_paths"][0], "desktop/src/app-layout/selectAppLayoutRuntimeContext*")
        self.assertEqual(task["validation_commands"][0], "pnpm -C desktop exec vitest run src/app-layout/runtime-context.test.ts")
        self.assertEqual(task["coupling_notes"][0], "Crossing into app-store selectors means the slice should be split or escalated.")
        self.assertEqual(exec_context["model_profile"], "mini")
        self.assertEqual(exec_context["deprecation_phase"], "consumer_migration")
        self.assertEqual(exec_context["fanout_risk"], "low")
        self.assertEqual(exec_context["touch_paths"][0], "desktop/src/app-layout/selectAppLayoutRuntimeContext*")
        self.assertEqual(exec_context["validation_commands"][0], "pnpm -C desktop exec vitest run src/app-layout/runtime-context.test.ts")
        self.assertEqual(exec_context["coupling_notes"][0], "Crossing into app-store selectors means the slice should be split or escalated.")

    def test_setup_preserves_parity_metadata_and_resolves_compact_exec_context_scope(self):
        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        project_root = self.dev / "Repos" / "blog"
        paths = self._paths()

        write_json(
            paths.runner_parity_json,
            {
                "objective_id": "OBJ-TEST",
                "baseline_ref": "35ddf4f",
                "baseline_label": "protected desktop parity baseline",
                "baseline_git_head": "35ddf4f",
                "policy": {
                    "fail_closed": True,
                    "audit_mode": "targeted",
                    "default_max_surface_checks_mini": 1,
                    "default_max_surface_checks_high": 2,
                    "diff_first": True,
                    "mcp_when": ["desktop_layout", "interactive_ui"],
                },
                "surfaces": [
                    {
                        "surface_id": "desktop-main-panes",
                        "runtime": "desktop",
                        "kind": "layout_geometry",
                        "owner_paths": ["desktop/src/app-layout/**"],
                        "watch_paths": ["desktop/src/app-layout/**", "libs/desktop/app-shell/src/store/**"],
                        "preferred_mcp": ["playwright", "tauri"],
                        "baseline_expectation": "Pane sizing and layout geometry match the protected baseline.",
                        "harness_commands": [
                            "pnpm -C desktop exec vitest run src/app-layout/paneSizing.test.ts",
                        ],
                    },
                    {
                        "surface_id": "desktop-tools-toggle",
                        "runtime": "desktop",
                        "kind": "interaction_flow",
                        "owner_paths": ["desktop/src/features/layout/**"],
                        "watch_paths": ["desktop/src/features/layout/**"],
                        "preferred_mcp": ["playwright", "tauri"],
                        "baseline_expectation": "Tools toggle behavior matches the protected baseline.",
                        "harness_commands": [
                            "pnpm -C desktop exec vitest run src/features/layout/toolsToggle.test.ts",
                        ],
                    },
                ],
            },
        )

        self._write_tasks(
            [
                {
                    "task_id": "TT-020",
                    "title": "Harden pane sizing parity checks",
                    "status": "open",
                    "priority": "p1",
                    "depends_on": [],
                    "project_root": str(project_root),
                    "target_branch": "main",
                    "acceptance": ["Preserve pane sizing and tools toggle parity against the protected baseline."],
                    "validation": ["Run targeted pane sizing regression checks."],
                    "model_profile": "high",
                    "profile_reason": "Layout-affecting refactor needs targeted parity enforcement.",
                    "touch_paths": [
                        "desktop/src/app-layout/**",
                        "desktop/src/features/layout/**",
                    ],
                    "validation_commands": [
                        "pnpm -C desktop exec vitest run src/app-layout/paneSizing.test.ts src/features/layout/toolsToggle.test.ts",
                    ],
                    "deprecation_phase": "convergence",
                    "fanout_risk": "high",
                    "parity_baseline_ref": "35ddf4f",
                    "parity_surface_ids": ["desktop-main-panes", "desktop-tools-toggle"],
                    "parity_audit_mode": "targeted",
                    "parity_harness_commands": [
                        "pnpm -C desktop exec vitest run src/app-layout/paneSizing.test.ts",
                    ],
                    "parity_notes": "Use diff-first parity checks and then inspect the affected desktop surfaces only.",
                    "updated_at": "2026-03-19T00:00:00Z",
                }
            ]
        )

        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)

        tasks_payload = read_json(paths.tasks_json)
        exec_context = read_json(paths.exec_context_json)
        active_backlog = read_json(paths.active_backlog_json)
        parity_payload = read_json(paths.runner_parity_json)
        self.assertIsNotNone(tasks_payload)
        self.assertIsNotNone(exec_context)
        self.assertIsNotNone(active_backlog)
        self.assertIsNotNone(parity_payload)
        assert tasks_payload is not None
        assert exec_context is not None
        assert active_backlog is not None
        assert parity_payload is not None

        task = tasks_payload["tasks"][0]
        self.assertEqual(task["parity_baseline_ref"], "35ddf4f")
        self.assertEqual(task["parity_surface_ids"], ["desktop-main-panes", "desktop-tools-toggle"])
        self.assertEqual(task["parity_audit_mode"], "targeted")
        self.assertEqual(task["parity_harness_commands"][0], "pnpm -C desktop exec vitest run src/app-layout/paneSizing.test.ts")

        self.assertTrue(exec_context["parity_enabled"])
        self.assertEqual(exec_context["parity_baseline_ref"], "35ddf4f")
        self.assertEqual(exec_context["parity_surface_ids"], ["desktop-main-panes", "desktop-tools-toggle"])
        self.assertEqual(exec_context["parity_audit_mode"], "targeted")
        self.assertEqual(exec_context["parity_trigger_reason"], "task declares parity surfaces; baseline=35ddf4f")
        self.assertEqual(exec_context["parity_surfaces"][0]["surface_id"], "desktop-main-panes")
        self.assertEqual(exec_context["parity_harness_commands"][0], "pnpm -C desktop exec vitest run src/app-layout/paneSizing.test.ts")
        self.assertEqual(exec_context["parity_policy"]["default_max_surface_checks_high"], 2)

        self.assertTrue(active_backlog["selected_task"]["parity_enabled"])
        self.assertEqual(active_backlog["selected_task"]["parity_baseline_ref"], "35ddf4f")
        self.assertEqual(active_backlog["selected_task"]["parity_surface_ids_top3"], ["desktop-main-panes", "desktop-tools-toggle"])
        self.assertEqual(parity_payload["baseline_ref"], "35ddf4f")

    def test_setup_generates_graph_artifacts_when_graph_mode_is_enabled(self):
        project_root = self.dev / "Repos" / "blog"
        (project_root / "src").mkdir(parents=True, exist_ok=True)
        (project_root / "package.json").write_text('{"name":"blog","private":true}\n', encoding="utf-8")
        (project_root / ".dependency-cruiser.cjs").write_text("module.exports = { forbidden: [], options: {} };\n", encoding="utf-8")
        (project_root / "src" / "alpha.ts").write_text('import "./beta";\nexport const alpha = 1;\n', encoding="utf-8")
        (project_root / "src" / "beta.ts").write_text("export const beta = 2;\n", encoding="utf-8")
        self._write_context_pack(
            {
                "repoId": "blog",
                "graphConfig": {
                    "enabled": True,
                    "graph_mode": "dependency_cruiser",
                    "toolCommand": "pnpm exec depcruise",
                    "configPath": ".dependency-cruiser.cjs",
                    "graph_roots": ["src"],
                    "graph_grouping": "folder_prefix",
                    "graph_entry_policy": "source_only",
                    "boundaries": [
                        {"label": "app", "prefixes": ["src/"]},
                    ],
                },
            }
        )
        self._write_tasks(
            [
                {
                    "task_id": "TT-001",
                    "title": "Graph-backed task",
                    "status": "open",
                    "priority": "p1",
                    "depends_on": [],
                    "project_root": str(project_root),
                    "target_branch": "main",
                    "acceptance": ["done"],
                    "validation": ["verify"],
                    "touch_paths": ["src/alpha.ts"],
                    "updated_at": "2026-03-01T00:00:00Z",
                }
            ]
        )

        depcruise_payload = {
            "modules": [
                {
                    "source": "src/alpha.ts",
                    "dependencies": [
                        {"resolved": "src/beta.ts"},
                    ],
                },
                {
                    "source": "src/beta.ts",
                    "dependencies": [],
                },
            ]
        }
        with patch("src.runner_graph._run_dependency_cruiser", return_value=depcruise_payload):
            created = create_runner_state(str(self.dev), "blog", "main", approve_enable=None)

        self.assertTrue(created["ok"])
        paths = self._paths()
        self.assertTrue(paths.dep_graph_json.exists())
        self.assertTrue(paths.graph_active_slice_json.exists())
        self.assertTrue(paths.graph_boundaries_json.exists())
        self.assertTrue(paths.graph_hotspots_json.exists())
        exec_context = read_json(paths.exec_context_json)
        active_backlog = read_json(paths.active_backlog_json)
        self.assertIsNotNone(exec_context)
        self.assertIsNotNone(active_backlog)
        assert exec_context is not None
        assert active_backlog is not None
        self.assertTrue(exec_context["graph_enabled"])
        self.assertTrue(exec_context["graph_digest"])
        self.assertTrue(exec_context["graph_active_slice_digest"])
        self.assertEqual(exec_context["graph_cluster_label"], "app")
        self.assertTrue(exec_context["graph_slice_reason"])
        self.assertEqual(exec_context["graph_adjacent_families_top3"], [])
        self.assertEqual(active_backlog["selected_task"]["graph_cluster_label"], "app")
        self.assertTrue(active_backlog["selected_task"]["graph_slice_reason"])

    def test_graph_enabled_setup_leaves_one_open_frontier(self):
        project_root = self.dev / "Repos" / "blog"
        (project_root / "src" / "app").mkdir(parents=True, exist_ok=True)
        (project_root / "src" / "store").mkdir(parents=True, exist_ok=True)
        (project_root / "package.json").write_text('{"name":"blog","private":true}\n', encoding="utf-8")
        (project_root / ".dependency-cruiser.cjs").write_text("module.exports = { forbidden: [], options: {} };\n", encoding="utf-8")
        (project_root / "src" / "app" / "alpha.ts").write_text("export const alpha = 1;\n", encoding="utf-8")
        (project_root / "src" / "store" / "beta.ts").write_text("export const beta = 1;\n", encoding="utf-8")
        self._write_context_pack(
            {
                "repoId": "blog",
                "graphConfig": {
                    "enabled": True,
                    "graph_mode": "dependency_cruiser",
                    "toolCommand": "pnpm exec depcruise",
                    "configPath": ".dependency-cruiser.cjs",
                    "graph_roots": ["src"],
                    "boundaries": [
                        {"label": "app", "prefixes": ["src/app/"]},
                        {"label": "store", "prefixes": ["src/store/"]},
                    ],
                    "slicePriority": ["app", "store"],
                    "actionableFrontier": "one_open",
                },
            }
        )
        self._write_tasks(
            [
                {
                    "task_id": "TT-001",
                    "title": "Finish app slice",
                    "status": "open",
                    "priority": "p1",
                    "depends_on": [],
                    "project_root": str(project_root),
                    "target_branch": "main",
                    "acceptance": ["done"],
                    "validation": ["verify"],
                    "touch_paths": ["src/app/**"],
                    "deprecation_phase": "seam",
                    "fanout_risk": "low",
                    "model_profile": "mini",
                    "updated_at": "2026-03-01T00:00:00Z",
                },
                {
                    "task_id": "TT-002",
                    "title": "Finish store slice",
                    "status": "open",
                    "priority": "p1",
                    "depends_on": [],
                    "project_root": str(project_root),
                    "target_branch": "main",
                    "acceptance": ["done"],
                    "validation": ["verify"],
                    "touch_paths": ["src/store/**"],
                    "deprecation_phase": "seam",
                    "fanout_risk": "low",
                    "model_profile": "mini",
                    "updated_at": "2026-03-01T00:00:00Z",
                },
            ]
        )
        depcruise_payload = {
            "modules": [
                {"source": "src/app/alpha.ts", "dependencies": []},
                {"source": "src/store/beta.ts", "dependencies": []},
            ]
        }

        with patch("src.runner_graph._run_dependency_cruiser", return_value=depcruise_payload):
            created = create_runner_state(str(self.dev), "blog", "main", approve_enable=None)

        self.assertTrue(created["ok"])
        tasks = read_json(self._paths().tasks_json)["tasks"]
        by_id = {task["task_id"]: task for task in tasks}
        self.assertEqual(by_id["TT-001"]["status"], "open")
        self.assertEqual(by_id["TT-002"]["status"], "blocked")
        self.assertEqual(by_id["TT-002"]["blocked_reason"], "graph_frontier_waiting_on: TT-001")

    def test_graph_enabled_setup_prefers_dominant_dirty_cluster_in_hybrid_mode(self):
        project_root = self.dev / "Repos" / "blog"
        (project_root / "src" / "app").mkdir(parents=True, exist_ok=True)
        (project_root / "src" / "store").mkdir(parents=True, exist_ok=True)
        (project_root / "package.json").write_text('{"name":"blog","private":true}\n', encoding="utf-8")
        (project_root / ".dependency-cruiser.cjs").write_text("module.exports = { forbidden: [], options: {} };\n", encoding="utf-8")
        (project_root / "src" / "app" / "alpha.ts").write_text("export const alpha = 1;\n", encoding="utf-8")
        (project_root / "src" / "store" / "beta.ts").write_text("export const beta = 1;\n", encoding="utf-8")
        self._write_context_pack(
            {
                "repoId": "blog",
                "graphConfig": {
                    "enabled": True,
                    "graph_mode": "dependency_cruiser",
                    "toolCommand": "pnpm exec depcruise",
                    "configPath": ".dependency-cruiser.cjs",
                    "graph_roots": ["src"],
                    "boundaries": [
                        {"label": "app", "prefixes": ["src/app/"]},
                        {"label": "store", "prefixes": ["src/store/"]},
                    ],
                    "slicePriority": ["store", "app"],
                    "graphPolicy": "hybrid",
                    "actionableFrontier": "one_open",
                },
            }
        )
        self._write_tasks(
            [
                {
                    "task_id": "TT-001",
                    "title": "Finish app slice",
                    "status": "open",
                    "priority": "p1",
                    "depends_on": [],
                    "project_root": str(project_root),
                    "target_branch": "main",
                    "acceptance": ["done"],
                    "validation": ["verify"],
                    "touch_paths": ["src/app/**"],
                    "deprecation_phase": "seam",
                    "fanout_risk": "low",
                    "model_profile": "mini",
                    "updated_at": "2026-03-01T00:00:00Z",
                },
                {
                    "task_id": "TT-002",
                    "title": "Finish store slice",
                    "status": "open",
                    "priority": "p1",
                    "depends_on": [],
                    "project_root": str(project_root),
                    "target_branch": "main",
                    "acceptance": ["done"],
                    "validation": ["verify"],
                    "touch_paths": ["src/store/**"],
                    "deprecation_phase": "seam",
                    "fanout_risk": "low",
                    "model_profile": "mini",
                    "updated_at": "2026-03-01T00:00:00Z",
                },
            ]
        )
        depcruise_payload = {
            "modules": [
                {"source": "src/app/alpha.ts", "dependencies": []},
                {"source": "src/store/beta.ts", "dependencies": []},
            ]
        }

        with patch("src.runner_graph._run_dependency_cruiser", return_value=depcruise_payload), patch(
            "src.runctl._list_dirty_paths",
            return_value=["src/app/alpha.ts"],
        ):
            created = create_runner_state(str(self.dev), "blog", "main", approve_enable=None)

        self.assertTrue(created["ok"])
        state = read_json(self._paths().state_file)
        exec_context = read_json(self._paths().exec_context_json)
        self.assertEqual(state["next_task_id"], "TT-001")
        self.assertEqual(exec_context["graph_cluster_label"], "app")
        self.assertIn("dirty path", exec_context["graph_slice_reason"])

    def test_setup_reuses_cached_graph_when_graph_digest_is_unchanged(self):
        project_root = self.dev / "Repos" / "blog"
        (project_root / "src").mkdir(parents=True, exist_ok=True)
        (project_root / "package.json").write_text('{"name":"blog","private":true}\n', encoding="utf-8")
        (project_root / ".dependency-cruiser.cjs").write_text("module.exports = { forbidden: [], options: {} };\n", encoding="utf-8")
        (project_root / "src" / "alpha.ts").write_text("export const alpha = 1;\n", encoding="utf-8")
        self._write_context_pack(
            {
                "repoId": "blog",
                "graphConfig": {
                    "enabled": True,
                    "graph_mode": "dependency_cruiser",
                    "toolCommand": "pnpm exec depcruise",
                    "configPath": ".dependency-cruiser.cjs",
                    "graph_roots": ["src"],
                },
            }
        )
        self._write_tasks(
            [
                {
                    "task_id": "TT-001",
                    "title": "Graph-backed task",
                    "status": "open",
                    "priority": "p1",
                    "depends_on": [],
                    "project_root": str(project_root),
                    "target_branch": "main",
                    "acceptance": ["done"],
                    "validation": ["verify"],
                    "touch_paths": ["src/alpha.ts"],
                    "updated_at": "2026-03-01T00:00:00Z",
                }
            ]
        )
        depcruise_payload = {
            "modules": [
                {
                    "source": "src/alpha.ts",
                    "dependencies": [],
                }
            ]
        }
        with patch("src.runner_graph._run_dependency_cruiser", return_value=depcruise_payload) as mocked_run:
            create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
            create_runner_state(str(self.dev), "blog", "main", approve_enable=None)

        self.assertEqual(mocked_run.call_count, 1)

    def test_setup_falls_back_cleanly_when_graph_tool_is_unavailable(self):
        project_root = self.dev / "Repos" / "blog"
        (project_root / "src").mkdir(parents=True, exist_ok=True)
        (project_root / "package.json").write_text('{"name":"blog","private":true}\n', encoding="utf-8")
        (project_root / ".dependency-cruiser.cjs").write_text("module.exports = { forbidden: [], options: {} };\n", encoding="utf-8")
        (project_root / "src" / "alpha.ts").write_text("export const alpha = 1;\n", encoding="utf-8")
        self._write_context_pack(
            {
                "repoId": "blog",
                "graphConfig": {
                    "enabled": True,
                    "graph_mode": "dependency_cruiser",
                    "toolCommand": "pnpm exec depcruise",
                    "configPath": ".dependency-cruiser.cjs",
                    "graph_roots": ["src"],
                },
            }
        )

        with patch("src.runner_graph._run_dependency_cruiser", return_value=None):
            created = create_runner_state(str(self.dev), "blog", "main", approve_enable=None)

        self.assertTrue(created["ok"])
        paths = self._paths()
        self.assertFalse(paths.dep_graph_json.exists())
        self.assertFalse(paths.graph_active_slice_json.exists())
        exec_context = read_json(paths.exec_context_json)
        self.assertIsNotNone(exec_context)
        assert exec_context is not None
        self.assertFalse(exec_context["graph_enabled"])

    def test_graph_config_preserves_hidden_config_path(self):
        project_root = self.dev / "Repos" / "blog"
        self._write_context_pack(
            {
                "repoId": "blog",
                "graphConfig": {
                    "enabled": True,
                    "graph_mode": "dependency_cruiser",
                    "configPath": ".dependency-cruiser.cjs",
                    "graph_roots": ["src"],
                },
            }
        )

        config = _load_graph_config(project_root)
        self.assertIsNotNone(config)
        assert config is not None
        self.assertEqual(config["config_path"], ".dependency-cruiser.cjs")

    def test_setup_skips_open_superseded_task_when_actionable_slice_exists(self):
        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        project_root = self.dev / "Repos" / "blog"

        self._write_tasks(
            [
                {
                    "task_id": "TT-010",
                    "title": "Move desktop app-shell ownership (SUPERSEDED)",
                    "status": "open",
                    "priority": "p0",
                    "depends_on": [],
                    "project_root": str(project_root),
                    "target_branch": "main",
                    "acceptance": ["superseded"],
                    "validation": ["verify"],
                    "updated_at": "2026-03-17T00:00:00Z",
                },
                {
                    "task_id": "TT-010h1",
                    "title": "Migrate runtime-context family",
                    "status": "open",
                    "priority": "p1",
                    "depends_on": [],
                    "project_root": str(project_root),
                    "target_branch": "main",
                    "acceptance": ["done"],
                    "validation": ["verify"],
                    "updated_at": "2026-03-17T00:01:00Z",
                },
            ]
        )

        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        state = read_json(self._paths().state_file)
        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state["next_task_id"], "TT-010h1")

    def test_setup_hardens_parity_task_acceptance_and_validation(self):
        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        project_root = self.dev / "Repos" / "blog"

        self._write_tasks(
            [
                {
                    "task_id": "TT-010",
                    "title": "Restore styling parity for archive panel",
                    "status": "open",
                    "priority": "p1",
                    "depends_on": [],
                    "project_root": str(project_root),
                    "target_branch": "main",
                    "acceptance": ["Matches old styling"],
                    "validation": ["Run tests"],
                    "updated_at": "2026-03-01T00:00:00Z",
                }
            ]
        )

        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)

        tasks_payload = read_json(self._paths().tasks_json)
        exec_context = read_json(self._paths().exec_context_json)
        self.assertIsNotNone(tasks_payload)
        self.assertIsNotNone(exec_context)
        assert tasks_payload is not None
        assert exec_context is not None
        task = tasks_payload["tasks"][0]
        acceptance_text = " ".join(task["acceptance"]).lower()
        validation_text = " ".join(task["validation"]).lower()
        self.assertIn("fail-closed", acceptance_text)
        self.assertIn("explicit baseline comparison", acceptance_text)
        self.assertIn("side-by-side comparison", validation_text)
        self.assertIn("keep the task open", validation_text)
        self.assertIn("parity_tasks_fail_closed_until_no_known_delta_remains", exec_context["hard_rules"])

    def test_setup_builds_phase_exec_context_from_repo_contract_order(self):
        project_root = self.dev / "Repos" / "blog"
        (project_root / "docs" / "llm").mkdir(parents=True)
        (project_root / ".codex").mkdir(parents=True)
        (project_root / "AGENTS.md").write_text(
            "# Repo Agent Harness\n\n## Required Context Load\n\n1. `harness.config.json`\n2. `docs/llm/golden-path.md`\n3. `.codex/context-pack.md`\n",
            encoding="utf-8",
        )
        (project_root / "harness.config.json").write_text('{"repoId":"blog"}\n', encoding="utf-8")
        (project_root / "docs" / "llm" / "golden-path.md").write_text("# Golden Path\n\n- keep tests green\n", encoding="utf-8")
        (project_root / ".codex" / "context-pack.md").write_text("# Context Pack\n\n- package: blog\n", encoding="utf-8")
        (project_root / ".codex" / "context-pack.json").write_text(
            '{"repoId":"blog","architectureRules":["no-cycle"],"doneCriteria":["pnpm run verify"],"packages":[{"name":"web"}]}\n',
            encoding="utf-8",
        )

        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)

        state = read_json(self._paths().state_file)
        exec_context = read_json(self._paths().exec_context_json)
        self.assertIsNotNone(state)
        self.assertIsNotNone(exec_context)
        assert state is not None
        assert exec_context is not None
        self.assertTrue(str(state.get("phase_context_digest", "")).strip())
        source_paths = [Path(item["path"]).name for item in exec_context["context_sources"]]
        self.assertEqual(
            source_paths,
            [
                "AGENTS.md",
                "harness.config.json",
                "golden-path.md",
                "context-pack.md",
                "context-pack.json",
                "PRD.md",
                "KANBAN_STATE.json",
                "RUNNER_PARITY.json",
            ],
        )
        self.assertEqual(exec_context["phase"], "verify")

    def test_setup_prefers_context_pack_when_required_context_omits_it(self):
        project_root = self.dev / "Repos" / "blog"
        (project_root / "docs" / "llm").mkdir(parents=True)
        (project_root / ".codex").mkdir(parents=True)
        (project_root / "AGENTS.md").write_text(
            "# Repo Agent Harness\n\n## Required Context Load\n\n1. `harness.config.json`\n2. `docs/llm/golden-path.md`\n",
            encoding="utf-8",
        )
        (project_root / "harness.config.json").write_text('{"repoId":"blog"}\n', encoding="utf-8")
        (project_root / "docs" / "llm" / "golden-path.md").write_text("# Golden Path\n\n- keep tests green\n", encoding="utf-8")
        (project_root / ".codex" / "context-pack.md").write_text("# Context Pack\n\n- package: blog\n", encoding="utf-8")
        (project_root / ".codex" / "context-pack.json").write_text(
            '{"repoId":"blog","architectureRules":["no-cycle"],"doneCriteria":["pnpm run verify"],"packages":[{"name":"web"}]}\n',
            encoding="utf-8",
        )

        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)

        exec_context = read_json(self._paths().exec_context_json)
        self.assertIsNotNone(exec_context)
        assert exec_context is not None
        source_paths = [Path(item["path"]).name for item in exec_context["context_sources"]]
        self.assertEqual(
            source_paths,
            [
                "AGENTS.md",
                "context-pack.md",
                "context-pack.json",
                "harness.config.json",
                "golden-path.md",
                "PRD.md",
                "KANBAN_STATE.json",
                "RUNNER_PARITY.json",
            ],
        )

    def test_setup_promotes_verify_phase_when_blocker_surface_is_validation(self):
        project_root = self.dev / "Repos" / "blog"
        (project_root / ".codex").mkdir(parents=True)
        (project_root / ".codex" / "context-pack.md").write_text("# Context Pack\n\n- package: blog\n", encoding="utf-8")

        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        paths = self._paths()
        state = read_json(paths.state_file)
        self.assertIsNotNone(state)
        assert state is not None
        state["blockers"] = ["Desktop typecheck is failing in verify."]
        write_json(paths.state_file, state)

        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        refreshed = read_json(paths.state_file)
        exec_context = read_json(paths.exec_context_json)
        self.assertIsNotNone(refreshed)
        self.assertIsNotNone(exec_context)
        assert refreshed is not None
        assert exec_context is not None
        self.assertEqual(refreshed["current_phase"], "verify")
        self.assertEqual(exec_context["phase"], "verify")

    def test_done_lock_cleared_when_pending_tasks_exist(self):
        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        paths = self._paths()
        paths.done_lock.write_text("done\n", encoding="utf-8")
        self.assertTrue(paths.done_lock.exists())

        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        self.assertFalse(paths.done_lock.exists())
        state = read_json(paths.state_file)
        self.assertIsNotNone(state)
        assert state is not None
        self.assertNotEqual(state["status"], "done")
        self.assertFalse(bool(state["done_candidate"]))

    def test_done_lock_preserved_when_done_closeout_history_exists(self):
        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        paths = self._paths()

        write_json(
            paths.tasks_json,
            {
                "objective_id": "OBJ-TEST",
                "tasks": [
                    {
                        "task_id": "TT-001",
                        "title": "Already landed task",
                        "status": "done",
                        "priority": "p1",
                        "depends_on": [],
                        "project_root": str((self.dev / "Repos" / "blog").resolve()),
                        "target_branch": "main",
                        "acceptance": ["done"],
                        "validation": ["verify"],
                        "updated_at": "2026-03-01T00:00:00Z",
                    },
                    {
                        "task_id": "TT-002",
                        "title": "Run final done-closeout `run_gates` check before closing the objective.",
                        "status": "done",
                        "priority": "p1",
                        "depends_on": ["TT-001"],
                        "project_root": str((self.dev / "Repos" / "blog").resolve()),
                        "target_branch": "main",
                        "acceptance": [
                            "Complete: Final done-closeout `run_gates` validation passes before the runner writes `RUNNER_DONE.lock`.",
                        ],
                        "validation": ["Run final done-state gate check (`run_gates`)."],
                        "updated_at": "2026-03-01T00:00:00Z",
                    },
                ],
            },
        )
        paths.done_lock.write_text("done\n", encoding="utf-8")

        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        self.assertTrue(paths.done_lock.exists())
        state = read_json(paths.state_file)
        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state["status"], "done")
        self.assertTrue(bool(state["done_candidate"]))
        self.assertEqual(state["done_gate_status"], "passed")
        self.assertEqual(state["dod_status"], "done")

    def test_setup_creates_explicit_closeout_task_before_marking_done(self):
        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        paths = self._paths()
        paths.gates_file.parent.mkdir(parents=True, exist_ok=True)
        paths.gates_file.write_text("#!/usr/bin/env bash\nrun_gates(){ return 0; }\n", encoding="utf-8")

        tasks_payload = read_json(paths.tasks_json)
        self.assertIsNotNone(tasks_payload)
        assert tasks_payload is not None
        for task in tasks_payload.get("tasks", []):
            if isinstance(task, dict):
                task["status"] = "done"
        write_json(paths.tasks_json, tasks_payload)

        result = create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        self.assertTrue(result["ok"])
        self.assertFalse(paths.done_lock.exists())
        state = read_json(paths.state_file)
        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state["status"], "ready")
        self.assertFalse(bool(state["done_candidate"]))
        self.assertEqual(state["done_gate_status"], "pending")
        self.assertEqual(state["blockers"], [])
        self.assertEqual(state["current_phase"], "closeout")
        self.assertEqual(state["phase_status"], "active")
        self.assertEqual(state["dod_status"], "in_progress")
        self.assertIsNotNone(state["next_task_id"])
        self.assertIn("done-closeout", state["next_task"])
        tasks_payload = read_json(paths.tasks_json)
        self.assertIsNotNone(tasks_payload)
        assert tasks_payload is not None
        open_tasks = [task for task in tasks_payload["tasks"] if task["status"] == "open"]
        self.assertEqual(len(open_tasks), 1)
        self.assertIn("done-closeout", open_tasks[0]["title"])

    def test_setup_creates_pending_closeout_task_when_tasks_collapse_to_zero_and_gates_fail(self):
        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        paths = self._paths()
        paths.gates_file.parent.mkdir(parents=True, exist_ok=True)
        paths.gates_file.write_text("#!/usr/bin/env bash\nrun_gates(){ echo nope; return 1; }\n", encoding="utf-8")

        tasks_payload = read_json(paths.tasks_json)
        self.assertIsNotNone(tasks_payload)
        assert tasks_payload is not None
        for task in tasks_payload.get("tasks", []):
            if isinstance(task, dict):
                task["status"] = "done"
        write_json(paths.tasks_json, tasks_payload)

        result = create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        self.assertTrue(result["ok"])
        self.assertFalse(paths.done_lock.exists())
        state = read_json(paths.state_file)
        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state["status"], "ready")
        self.assertFalse(bool(state["done_candidate"]))
        self.assertEqual(state["done_gate_status"], "pending")
        self.assertIn("done-closeout", state["next_task"])
        self.assertEqual(state["blockers"], [])
        self.assertEqual(state["current_phase"], "closeout")
        self.assertEqual(state["dod_status"], "in_progress")

    def test_setup_clears_stale_done_dod_status_when_runner_is_not_complete(self):
        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        paths = self._paths()
        state = read_json(paths.state_file)
        self.assertIsNotNone(state)
        assert state is not None
        state["status"] = "ready"
        state["dod_status"] = "done"
        write_json(paths.state_file, state)

        result = create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        self.assertTrue(result["ok"])

        refreshed = read_json(paths.state_file)
        self.assertIsNotNone(refreshed)
        assert refreshed is not None
        self.assertNotEqual(refreshed["status"], "done")
        self.assertEqual(refreshed["dod_status"], "in_progress")

    def test_setup_keeps_explicit_closeout_task_open_even_when_run_gates_is_green(self):
        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        paths = self._paths()
        paths.gates_file.parent.mkdir(parents=True, exist_ok=True)
        paths.gates_file.write_text("#!/usr/bin/env bash\nrun_gates(){ return 0; }\n", encoding="utf-8")
        write_json(
            paths.tasks_json,
            {
                "objective_id": "OBJ-TEST",
                "tasks": [
                    {
                        "task_id": "TT-001",
                        "title": "Already landed task",
                        "status": "done",
                        "priority": "p1",
                        "depends_on": [],
                        "project_root": str((self.dev / "Repos" / "blog").resolve()),
                        "target_branch": "main",
                        "acceptance": ["done"],
                        "validation": ["verify"],
                        "updated_at": "2026-03-01T00:00:00Z",
                    },
                    {
                        "task_id": "TT-002",
                        "title": "Run final done-state `run_gates` check after verify passes.",
                        "status": "open",
                        "priority": "p1",
                        "depends_on": ["TT-001"],
                        "project_root": str((self.dev / "Repos" / "blog").resolve()),
                        "target_branch": "main",
                        "acceptance": [
                            "Complete: Full closeout verification remains green (`pnpm run verify`).",
                            "Run final done-state gate check (`run_gates`) and close the objective if clean.",
                        ],
                        "validation": ["Run run_gates."],
                        "updated_at": "2026-03-01T00:00:00Z",
                    },
                ],
            },
        )

        result = create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        self.assertTrue(result["ok"])
        self.assertFalse(paths.done_lock.exists())
        state = read_json(paths.state_file)
        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state["status"], "ready")
        self.assertEqual(state["done_gate_status"], "passed")
        self.assertEqual(state["next_task_id"], "TT-002")
        self.assertEqual(state["current_phase"], "closeout")
        tasks_payload = read_json(paths.tasks_json)
        self.assertIsNotNone(tasks_payload)
        assert tasks_payload is not None
        task_statuses = {task["task_id"]: task["status"] for task in tasks_payload["tasks"]}
        self.assertEqual(task_statuses["TT-002"], "open")

    def test_clear_is_two_phase_and_deletes_runner_managed_project_prd(self):
        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        paths = self._paths()
        goals_file = paths.memory_dir / "GOALS.md"
        goals_file.write_text("# user note\n", encoding="utf-8")
        paths.project_prd_file.write_text("# objective\n", encoding="utf-8")
        paths.legacy_refactor_status_file.write_text("# stale objective\n", encoding="utf-8")

        pending = clear_runner_state(str(self.dev), "blog", "main", confirm=None)
        self.assertTrue(pending["ok"])
        done = clear_runner_state(str(self.dev), "blog", "main", confirm=pending["confirm_token"])
        self.assertTrue(done["ok"])
        self.assertFalse(paths.runner_dir.exists())
        self.assertTrue(goals_file.exists())
        self.assertFalse(paths.project_prd_file.exists())
        self.assertFalse(paths.legacy_refactor_status_file.exists())
        self.assertFalse(paths.runner_handoff_file.exists())
        self.assertFalse(paths.active_backlog_json.exists())

    def test_setup_refresh_drops_legacy_conversation_digest_fields(self):
        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        paths = self._paths()
        state = read_json(paths.state_file)
        self.assertIsNotNone(state)
        assert state is not None
        state["conversation_digest_key"] = "abc"
        state["conversation_digest_source"] = "codex_thread"
        state["conversation_digest_version"] = "v1"
        state["conversation_seed_confidence"] = 0.1
        write_json(paths.state_file, state)

        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        refreshed = read_json(paths.state_file)
        self.assertIsNotNone(refreshed)
        assert refreshed is not None
        self.assertNotIn("conversation_digest_key", refreshed)
        self.assertNotIn("conversation_digest_source", refreshed)
        self.assertNotIn("conversation_digest_version", refreshed)
        self.assertNotIn("conversation_seed_confidence", refreshed)

    def test_setup_event_coalesces_when_identical_and_rapid(self):
        paths = self._paths()
        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)

        entries = []
        for line in paths.ledger_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            entries.append(json.loads(line))
        setup_events = [entry for entry in entries if entry.get("event") == "runner.setup"]
        self.assertEqual(len(setup_events), 1)

    def test_run_defaults_to_setup_when_action_missing(self):
        code = run(["blog", "--runner-id", "main", "--dev", str(self.dev)])
        self.assertEqual(code, 0)
        self.assertTrue(self._paths().state_file.exists())

    def test_run_setup_accepts_project_root_flag(self):
        custom_root = self.dev / "custom-root" / "acme-app"
        custom_root.mkdir(parents=True)
        code = run(
            [
                "--setup",
                "--project-root",
                str(custom_root),
                "--runner-id",
                "main",
                "--dev",
                str(self.dev),
            ]
        )
        self.assertEqual(code, 0)
        self.assertTrue((custom_root / ".memory" / "runner" / "runtime" / "RUNNER_STATE.json").exists())

    def test_run_setup_accepts_project_and_project_root_together(self):
        worktree_root = self.dev / "worktrees" / "blog" / "pro-12"
        worktree_root.mkdir(parents=True)
        code = run(
            [
                "--setup",
                "--project",
                "blog",
                "--project-root",
                str(worktree_root),
                "--runner-id",
                "main",
                "--dev",
                str(self.dev),
            ]
        )
        self.assertEqual(code, 0)
        self.assertTrue((worktree_root / ".memory" / "runner" / "runtime" / "RUNNER_STATE.json").exists())
        context = read_json(self.dev / "Repos" / "blog" / ".memory" / "RUNNER_CONTEXT.json")
        self.assertEqual(context["runners"]["main"]["project_root"], str(worktree_root.resolve()))

    def test_run_setup_quiet_prints_minimal_output(self):
        with patch("sys.stdout", new=io.StringIO()) as mocked_stdout:
            code = run(["--setup", "--project", "blog", "--runner-id", "main", "--dev", str(self.dev), "--quiet"])
        self.assertEqual(code, 0)
        self.assertTrue(mocked_stdout.getvalue().strip().startswith("ok=1"))

    def test_run_setup_uses_saved_runner_context_root_for_project_shorthand(self):
        worktree_root = self.dev / "worktrees" / "wt1" / "blog"
        worktree_root.mkdir(parents=True)
        create_runner_state(
            str(self.dev),
            "blog",
            "main",
            approve_enable=None,
            project_root=worktree_root,
        )

        code = run(["--setup", "--project", "blog", "--runner-id", "main", "--dev", str(self.dev)])
        self.assertEqual(code, 0)
        self.assertTrue((worktree_root / ".memory" / "runner" / "runtime" / "RUNNER_STATE.json").exists())

    def test_prepare_cycle_writes_canonical_marker(self):
        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        paths = self._paths()
        paths.cycle_prepared_file.unlink(missing_ok=True)
        state = read_json(paths.state_file)
        assert state is not None
        state["next_task"] = "Narrow blocker to desktop typecheck drift fixture mismatch."
        state["status"] = "blocked"
        write_json(paths.state_file, state)

        code = run(["--prepare-cycle", "--project", "blog", "--runner-id", "main", "--dev", str(self.dev)])
        self.assertEqual(code, 0)
        self.assertTrue(paths.cycle_prepared_file.exists())
        marker = read_json(paths.cycle_prepared_file)
        self.assertIsNotNone(marker)
        assert marker is not None
        self.assertEqual(marker["project"], "blog")
        self.assertEqual(marker["runner_id"], "main")
        self.assertEqual(marker["phase"], "verify")
        self.assertEqual(marker["handoff_reason"], "blocked")
        self.assertFalse(bool(marker["budget_exhausted"]))
        self.assertEqual(marker["git_worktree"], str((self.dev / "Repos" / "blog").resolve()))
        self.assertTrue(str(marker.get("prepared_at", "")).strip())

    def test_prepare_cycle_quiet_prints_minimal_output(self):
        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        paths = self._paths()
        state = read_json(paths.state_file)
        assert state is not None
        state["next_task_id"] = "TT-002"
        state["next_task"] = "Execute narrowed follow-up task."
        write_json(paths.state_file, state)
        with patch("sys.stdout", new=io.StringIO()) as mocked_stdout:
            code = run(["--prepare-cycle", "--project", "blog", "--runner-id", "main", "--dev", str(self.dev), "--quiet"])
        self.assertEqual(code, 0)
        self.assertEqual(mocked_stdout.getvalue().strip(), "ok=1")

    def test_prepare_cycle_preserves_cycle_progress_baseline_across_setup_refresh_task_advance(self):
        project_root = self.dev / "Repos" / "blog"
        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        self._write_tasks(
            [
                {
                    "task_id": "TT-001",
                    "title": "First task",
                    "status": "open",
                    "priority": "p0",
                    "depends_on": [],
                    "project_root": str(project_root),
                    "target_branch": "main",
                    "acceptance": ["done"],
                    "validation": ["verify"],
                    "updated_at": "2026-03-01T00:00:00Z",
                },
                {
                    "task_id": "TT-002",
                    "title": "Second task",
                    "status": "open",
                    "priority": "p1",
                    "depends_on": ["TT-001"],
                    "project_root": str(project_root),
                    "target_branch": "main",
                    "acceptance": ["done"],
                    "validation": ["verify"],
                    "updated_at": "2026-03-02T00:00:00Z",
                },
            ]
        )

        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        paths = self._paths()
        initial_exec_context = read_json(paths.exec_context_json)
        self.assertIsNotNone(initial_exec_context)
        assert initial_exec_context is not None
        initial_cycle_baseline = initial_exec_context.get("cycle_progress_baseline")
        self.assertIsInstance(initial_cycle_baseline, dict)

        tasks_payload = read_json(paths.tasks_json)
        self.assertIsNotNone(tasks_payload)
        assert tasks_payload is not None
        tasks_payload["tasks"][0]["status"] = "done"
        tasks_payload["tasks"][0]["updated_at"] = "2026-03-03T00:00:00Z"
        write_json(paths.tasks_json, tasks_payload)

        setup_code = run(["--setup", "--project", "blog", "--runner-id", "main", "--dev", str(self.dev), "--quiet"])
        self.assertEqual(setup_code, 0)

        refreshed_exec_context = read_json(paths.exec_context_json)
        self.assertIsNotNone(refreshed_exec_context)
        assert refreshed_exec_context is not None
        self.assertEqual(refreshed_exec_context["next_task_id"], "TT-002")
        self.assertEqual(refreshed_exec_context["cycle_progress_baseline"], initial_cycle_baseline)

        prepare_code = run(["--prepare-cycle", "--project", "blog", "--runner-id", "main", "--dev", str(self.dev)])
        self.assertEqual(prepare_code, 0)

        consumed_exec_context = read_json(paths.exec_context_json)
        self.assertIsNotNone(consumed_exec_context)
        assert consumed_exec_context is not None
        self.assertEqual(consumed_exec_context["cycle_progress_baseline"]["next_task_id"], "TT-002")

    def test_prepare_cycle_preserves_cycle_progress_baseline_across_phase_change(self):
        project_root = self.dev / "Repos" / "blog"
        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        self._write_tasks(
            [
                {
                    "task_id": "TT-001",
                    "title": "Finish the only task.",
                    "status": "open",
                    "priority": "p0",
                    "depends_on": [],
                    "project_root": str(project_root),
                    "target_branch": "main",
                    "acceptance": ["done"],
                    "validation": ["verify"],
                    "updated_at": "2026-03-01T00:00:00Z",
                }
            ]
        )
        paths = self._paths()
        paths.gates_file.write_text("#!/usr/bin/env bash\nrun_gates(){ return 0; }\n", encoding="utf-8")

        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        initial_exec_context = read_json(paths.exec_context_json)
        self.assertIsNotNone(initial_exec_context)
        assert initial_exec_context is not None
        initial_cycle_baseline = initial_exec_context.get("cycle_progress_baseline")
        self.assertIsInstance(initial_cycle_baseline, dict)

        tasks_payload = read_json(paths.tasks_json)
        self.assertIsNotNone(tasks_payload)
        assert tasks_payload is not None
        tasks_payload["tasks"][0]["status"] = "done"
        tasks_payload["tasks"][0]["updated_at"] = "2026-03-03T00:00:00Z"
        write_json(paths.tasks_json, tasks_payload)

        setup_code = run(["--setup", "--project", "blog", "--runner-id", "main", "--dev", str(self.dev), "--quiet"])
        self.assertEqual(setup_code, 0)

        refreshed_exec_context = read_json(paths.exec_context_json)
        self.assertIsNotNone(refreshed_exec_context)
        assert refreshed_exec_context is not None
        self.assertEqual(refreshed_exec_context["phase"], "closeout")
        self.assertEqual(refreshed_exec_context["cycle_progress_baseline"], initial_cycle_baseline)

        prepare_code = run(["--prepare-cycle", "--project", "blog", "--runner-id", "main", "--dev", str(self.dev)])
        self.assertEqual(prepare_code, 0)

    def test_setup_closeout_failure_prefers_final_error_line(self):
        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        paths = self._paths()
        paths.gates_file.parent.mkdir(parents=True, exist_ok=True)
        paths.gates_file.write_text(
            "#!/usr/bin/env bash\n"
            "echo 'run_gates: PASS'\n"
            "echo 'gates.sh must define run_gates: fake' >&2\n"
            "exit 1\n",
            encoding="utf-8",
        )

        write_json(
            paths.tasks_json,
            {
                "objective_id": "OBJ-TEST",
                "tasks": [
                    {
                        "task_id": "TT-001",
                        "title": "Already landed task",
                        "status": "done",
                        "priority": "p1",
                        "depends_on": [],
                        "project_root": str((self.dev / "Repos" / "blog").resolve()),
                        "target_branch": "main",
                        "acceptance": ["done"],
                        "validation": ["verify"],
                        "updated_at": "2026-03-01T00:00:00Z",
                    },
                    {
                        "task_id": "TT-002",
                        "title": "Run final done-closeout `run_gates` check before closing the objective.",
                        "status": "open",
                        "priority": "p1",
                        "depends_on": ["TT-001"],
                        "project_root": str((self.dev / "Repos" / "blog").resolve()),
                        "target_branch": "main",
                        "acceptance": [
                            "Complete: Final done-closeout `run_gates` validation passes before the runner writes `RUNNER_DONE.lock`.",
                        ],
                        "validation": ["Run final done-state gate check (`run_gates`)."],
                        "updated_at": "2026-03-01T00:00:00Z",
                    },
                ],
            },
        )

        result = create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        self.assertTrue(result["ok"])
        state = read_json(paths.state_file)
        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state["done_gate_status"], "failed")
        self.assertIn("gates.sh must define run_gates", state["blockers"][0])
        self.assertNotIn("run_gates: PASS", state["blockers"][0])

    def test_prepare_cycle_rejects_unchanged_progress_since_exec_context(self):
        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        paths = self._paths()
        paths.cycle_prepared_file.unlink(missing_ok=True)

        with patch("sys.stdout", new=io.StringIO()) as mocked_stdout:
            code = run(["--prepare-cycle", "--project", "blog", "--runner-id", "main", "--dev", str(self.dev)])
        self.assertEqual(code, 1)
        self.assertIn("no durable progress", mocked_stdout.getvalue())
        self.assertFalse(paths.cycle_prepared_file.exists())

    def test_prepare_cycle_accepts_same_next_task_when_active_backlog_digest_changes(self):
        project_root = self.dev / "Repos" / "blog"
        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        self._write_tasks(
            [
                {
                    "task_id": "TT-001",
                    "title": "Primary active task",
                    "status": "open",
                    "priority": "p1",
                    "depends_on": [],
                    "project_root": str(project_root),
                    "target_branch": "main",
                    "acceptance": ["original acceptance"],
                    "validation": ["original validation"],
                    "updated_at": "2026-03-18T00:00:00Z",
                },
                {
                    "task_id": "TT-002",
                    "title": "Downstream task",
                    "status": "blocked",
                    "priority": "p1",
                    "depends_on": ["TT-001"],
                    "blocked_reason": "waiting_on: TT-001",
                    "project_root": str(project_root),
                    "target_branch": "main",
                    "acceptance": ["later"],
                    "validation": ["later verify"],
                    "updated_at": "2026-03-18T00:00:00Z",
                },
            ]
        )
        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        paths = self._paths()
        before_exec_context = read_json(paths.exec_context_json)
        self.assertIsNotNone(before_exec_context)
        assert before_exec_context is not None
        baseline = before_exec_context.get("cycle_progress_baseline")
        self.assertIsInstance(baseline, dict)

        tasks_payload = read_json(paths.tasks_json)
        self.assertIsNotNone(tasks_payload)
        assert tasks_payload is not None
        tasks_payload["tasks"][0]["acceptance"] = ["rewritten acceptance after semantic split"]
        tasks_payload["tasks"][1]["title"] = "Blocked child split from same active task"
        write_json(paths.tasks_json, tasks_payload)

        setup_code = run(["--setup", "--project", "blog", "--runner-id", "main", "--dev", str(self.dev), "--quiet"])
        self.assertEqual(setup_code, 0)

        refreshed_exec_context = read_json(paths.exec_context_json)
        self.assertIsNotNone(refreshed_exec_context)
        assert refreshed_exec_context is not None
        refreshed_baseline = refreshed_exec_context.get("cycle_progress_baseline")
        refreshed_progress = refreshed_exec_context.get("progress_baseline")
        self.assertIsInstance(refreshed_baseline, dict)
        self.assertIsInstance(refreshed_progress, dict)
        self.assertEqual(refreshed_exec_context["next_task_id"], "TT-001")
        self.assertEqual(refreshed_baseline["next_task_id"], baseline["next_task_id"])
        self.assertEqual(refreshed_baseline["worktree_fingerprint"], baseline["worktree_fingerprint"])
        self.assertEqual(refreshed_baseline["active_backlog_digest"], baseline["active_backlog_digest"])
        self.assertNotEqual(refreshed_progress["active_backlog_digest"], baseline["active_backlog_digest"])

        prepare_code = run(["--prepare-cycle", "--project", "blog", "--runner-id", "main", "--dev", str(self.dev)])
        self.assertEqual(prepare_code, 0)
        self.assertTrue(paths.cycle_prepared_file.exists())

    def test_prepare_cycle_accepts_same_next_task_when_semantic_task_fields_change(self):
        project_root = self.dev / "Repos" / "blog"
        self._write_tasks(
            [
                {
                    "task_id": "TT-001",
                    "title": "Primary active task",
                    "status": "open",
                    "priority": "p1",
                    "depends_on": [],
                    "project_root": str(project_root),
                    "target_branch": "main",
                    "acceptance": ["Keep the primitive contract tight."],
                    "validation": ["Run shared UI tests."],
                    "validation_commands": ["pnpm test"],
                    "profile_reason": "Initial bounded slice.",
                    "spillover_paths": ["libs/web/ui/**"],
                    "updated_at": "2026-03-18T00:00:00Z",
                }
            ]
        )
        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        paths = self._paths()
        before_exec_context = read_json(paths.exec_context_json)
        self.assertIsNotNone(before_exec_context)
        assert before_exec_context is not None
        baseline = before_exec_context.get("cycle_progress_baseline")
        self.assertIsInstance(baseline, dict)

        tasks_payload = read_json(paths.tasks_json)
        self.assertIsNotNone(tasks_payload)
        assert tasks_payload is not None
        tasks_payload["tasks"][0]["validation_commands"] = ["pnpm test", "pnpm lint"]
        tasks_payload["tasks"][0]["profile_reason"] = "Scope narrowed after backlog repair."
        tasks_payload["tasks"][0]["spillover_paths"] = ["libs/web/ui/**", "libs/desktop/ui/**"]
        write_json(paths.tasks_json, tasks_payload)

        setup_code = run(["--setup", "--project", "blog", "--runner-id", "main", "--dev", str(self.dev), "--quiet"])
        self.assertEqual(setup_code, 0)

        refreshed_exec_context = read_json(paths.exec_context_json)
        self.assertIsNotNone(refreshed_exec_context)
        assert refreshed_exec_context is not None
        refreshed_baseline = refreshed_exec_context.get("cycle_progress_baseline")
        refreshed_progress = refreshed_exec_context.get("progress_baseline")
        self.assertIsInstance(refreshed_baseline, dict)
        self.assertIsInstance(refreshed_progress, dict)
        self.assertEqual(refreshed_exec_context["next_task_id"], "TT-001")
        self.assertEqual(refreshed_baseline["next_task_id"], baseline["next_task_id"])
        self.assertEqual(refreshed_baseline["worktree_fingerprint"], baseline["worktree_fingerprint"])
        self.assertEqual(refreshed_baseline["active_backlog_digest"], baseline["active_backlog_digest"])
        self.assertNotEqual(refreshed_progress["active_backlog_digest"], baseline["active_backlog_digest"])

        prepare_code = run(["--prepare-cycle", "--project", "blog", "--runner-id", "main", "--dev", str(self.dev)])
        self.assertEqual(prepare_code, 0)
        self.assertTrue(paths.cycle_prepared_file.exists())

    def test_worktree_fingerprint_changes_for_untracked_file_content_edits(self):
        project_root = self.dev / "Repos" / "blog"
        subprocess.run(["git", "-C", str(project_root), "init", "-b", "main"], check=True, capture_output=True, text=True)
        target_file = project_root / "scratch.txt"
        target_file.write_text("alpha=todo\n", encoding="utf-8")
        before = compute_worktree_fingerprint(project_root)
        target_file.write_text("alpha=done\n", encoding="utf-8")
        after = compute_worktree_fingerprint(project_root)
        self.assertIsNotNone(before)
        self.assertIsNotNone(after)
        self.assertNotEqual(before, after)

    def test_task_add_queues_work_without_touching_current_cycle_state(self):
        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        paths = self._paths()
        before_state = read_json(paths.state_file)
        self.assertIsNotNone(before_state)
        assert before_state is not None

        with patch("sys.stdout", new=io.StringIO()) as mocked_stdout:
            code = run(
                [
                    "--task",
                    "add",
                    "--project",
                    "blog",
                    "--runner-id",
                    "main",
                    "--dev",
                    str(self.dev),
                    "--title",
                    "Add export command palette shortcut.",
                ]
            )
        self.assertEqual(code, 0)
        response = json.loads(mocked_stdout.getvalue())
        queued = response["queued_task"]
        self.assertEqual(queued["task_id"], "TT-002")
        self.assertEqual(queued["depends_on"], ["TT-001"])
        self.assertTrue(bool(response["will_not_preempt_current_cycle"]))

        tasks_payload = read_json(paths.tasks_json)
        self.assertIsNotNone(tasks_payload)
        assert tasks_payload is not None
        self.assertEqual(len(tasks_payload["tasks"]), 1)

        state_after = read_json(paths.state_file)
        self.assertEqual(state_after, before_state)

    def test_setup_merges_queued_tasks_and_keeps_current_next_task_until_dependency_clears(self):
        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        paths = self._paths()

        queue_code = run(
            [
                "--task",
                "add",
                "--project",
                "blog",
                "--runner-id",
                "main",
                "--dev",
                str(self.dev),
                "--title",
                "Implement CSV export drawer.",
                "--acceptance",
                "Drawer opens from reports screen.",
                "--validation",
                "Run targeted export tests.",
            ]
        )
        self.assertEqual(queue_code, 0)

        setup_code = run(["--setup", "--project", "blog", "--runner-id", "main", "--dev", str(self.dev), "--quiet"])
        self.assertEqual(setup_code, 0)
        tasks_payload = read_json(paths.tasks_json)
        self.assertIsNotNone(tasks_payload)
        assert tasks_payload is not None
        task_map = {task["task_id"]: task for task in tasks_payload["tasks"]}
        self.assertIn("TT-002", task_map)
        self.assertEqual(task_map["TT-002"]["depends_on"], ["TT-001"])

        state = read_json(paths.state_file)
        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state["next_task_id"], "TT-001")
        self.assertFalse(paths.task_intake_file.exists())

        task_map["TT-001"]["status"] = "done"
        write_json(paths.tasks_json, tasks_payload)
        second_setup = run(["--setup", "--project", "blog", "--runner-id", "main", "--dev", str(self.dev), "--quiet"])
        self.assertEqual(second_setup, 0)

        refreshed_state = read_json(paths.state_file)
        self.assertIsNotNone(refreshed_state)
        assert refreshed_state is not None
        self.assertEqual(refreshed_state["next_task_id"], "TT-002")

    def test_task_queue_lists_pending_intake_entries(self):
        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        run(
            [
                "--task",
                "add",
                "--project",
                "blog",
                "--runner-id",
                "main",
                "--dev",
                str(self.dev),
                "--title",
                "Queue follow-up polish.",
            ]
        )

        with patch("sys.stdout", new=io.StringIO()) as mocked_stdout:
            code = run(["--task", "queue", "--project", "blog", "--runner-id", "main", "--dev", str(self.dev)])
        self.assertEqual(code, 0)
        payload = json.loads(mocked_stdout.getvalue())
        self.assertEqual(len(payload["tasks"]), 1)
        self.assertEqual(payload["tasks"][0]["title"], "Queue follow-up polish.")

    def test_operator_pause_persists_override_and_refreshes_runner_status(self):
        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        paths = self._paths()
        kanban_state = read_json(paths.kanban_state_json)
        assert kanban_state is not None
        kanban_state["phase"] = "executing"
        kanban_state["active_issue"] = {
            "url": "https://github.com/jkuang7/blog/issues/9",
            "repo": "jkuang7/blog",
            "number": 9,
            "title": "Pause me",
            "issue_class": "feature",
            "complexity": "S",
            "routing": "backend",
        }
        write_json(paths.kanban_state_json, kanban_state)

        with patch("sys.stdout", new=io.StringIO()) as mocked_stdout:
            code = run(
                [
                    "--operator",
                    "pause",
                    "--project",
                    "blog",
                    "--runner-id",
                    "main",
                    "--dev",
                    str(self.dev),
                    "--reason",
                    "manual pause",
                    "--requested-by",
                    "telegram",
                ]
            )
        self.assertEqual(code, 0)
        payload = json.loads(mocked_stdout.getvalue())
        self.assertEqual(payload["requested"]["action"], "pause")

        status = read_json(paths.runner_status_json)
        assert status is not None
        self.assertEqual(status["conditions"]["human_approval_required"], True)

    def test_operator_force_promotes_target_issue_into_queue_state(self):
        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        paths = self._paths()

        with patch("sys.stdout", new=io.StringIO()) as mocked_stdout:
            code = run(
                [
                    "--operator",
                    "force",
                    "--project",
                    "blog",
                    "--runner-id",
                    "main",
                    "--dev",
                    str(self.dev),
                    "--issue-url",
                    "https://github.com/jkuang7/blog/issues/12",
                    "--issue-repo",
                    "jkuang7/blog",
                    "--issue-number",
                    "12",
                    "--issue-title",
                    "Forced issue",
                    "--requested-by",
                    "telegram",
                ]
            )
        self.assertEqual(code, 0)
        payload = json.loads(mocked_stdout.getvalue())
        self.assertEqual(payload["requested"]["action"], "force")

        kanban_state = read_json(paths.kanban_state_json)
        assert kanban_state is not None
        self.assertEqual(kanban_state["active_issue"]["url"], "https://github.com/jkuang7/blog/issues/12")
        self.assertEqual(kanban_state["phase"], "executing")

    def test_operator_block_marks_active_issue_blocked(self):
        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        paths = self._paths()
        kanban_state = read_json(paths.kanban_state_json)
        assert kanban_state is not None
        kanban_state["phase"] = "executing"
        kanban_state["active_issue"] = {
            "url": "https://github.com/jkuang7/blog/issues/9",
            "repo": "jkuang7/blog",
            "number": 9,
            "title": "Pause me",
            "issue_class": "feature",
            "complexity": "S",
            "routing": "backend",
        }
        write_json(paths.kanban_state_json, kanban_state)

        with patch("sys.stdout", new=io.StringIO()) as mocked_stdout:
            code = run(
                [
                    "--operator",
                    "block",
                    "--project",
                    "blog",
                    "--runner-id",
                    "main",
                    "--dev",
                    str(self.dev),
                    "--reason",
                    "waiting on review",
                    "--requested-by",
                    "telegram",
                ]
            )
        self.assertEqual(code, 0)
        payload = json.loads(mocked_stdout.getvalue())
        self.assertEqual(payload["requested"]["action"], "mark_blocked")

        kanban_state = read_json(paths.kanban_state_json)
        assert kanban_state is not None
        self.assertEqual(kanban_state["phase"], "blocked")
        self.assertEqual(kanban_state["blocker"]["category"], "operator")

    def test_parse_args_rejects_obsolete_create_flag(self):
        with patch("sys.stderr", new=io.StringIO()):
            with self.assertRaises(SystemExit):
                parse_args(["--create", "--project", "blog"])

    def test_run_rejects_non_main_runner_id(self):
        code = run(["--setup", "--project", "blog", "--runner-id", "alpha", "--dev", str(self.dev)])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
