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

from src.runctl import clear_runner_state, create_runner_state, inspect_runner_start_state, parse_args, run
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
        self.assertTrue(paths.tasks_json.exists())
        self.assertTrue(paths.prd_json.exists())
        self.assertTrue(paths.exec_context_json.exists())
        self.assertTrue(paths.ledger_file.exists())
        self.assertTrue(paths.project_prd_file.exists())
        self.assertTrue(paths.runner_handoff_file.exists())

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
        self.assertEqual(state["current_phase"], "discover")
        self.assertEqual(state["phase_status"], "active")
        self.assertEqual(state["phase_budget_minutes"], 45)

        exec_context = read_json(paths.exec_context_json)
        self.assertIsNotNone(exec_context)
        assert exec_context is not None
        self.assertEqual(exec_context["phase"], "discover")
        self.assertIn("context_delta", exec_context)
        self.assertIn("context_sources", exec_context)
        source_paths = {
            str(Path(str(entry.get("path", ""))).resolve())
            for entry in exec_context["context_sources"]
            if isinstance(entry, dict) and str(entry.get("path", "")).strip()
        }
        self.assertIn(str(paths.project_prd_file.resolve()), source_paths)
        self.assertIn(str(paths.runner_handoff_file.resolve()), source_paths)
        self.assertIn("Next task:", paths.runner_handoff_file.read_text(encoding="utf-8"))

    def test_inspect_runner_start_state_requires_setup(self):
        result = inspect_runner_start_state(str(self.dev), "blog", "main")
        self.assertFalse(result["ok"])
        self.assertIn("Run /prompts:run_setup first", result["error"])

    def test_inspect_runner_start_state_passes_after_enable_approval(self):
        initial = create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        token = initial["enable_token"]
        approved = create_runner_state(str(self.dev), "blog", "main", approve_enable=token)
        self.assertTrue(approved["ok"])

        result = inspect_runner_start_state(str(self.dev), "blog", "main")
        self.assertTrue(result["ok"])
        self.assertEqual(result["next_task_id"], "TT-001")

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

    def test_setup_seeds_default_tasks_when_missing(self):
        created = create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        self.assertTrue(created["ok"])

        tasks_payload = read_json(self._paths().tasks_json)
        self.assertIsNotNone(tasks_payload)
        assert tasks_payload is not None
        tasks = tasks_payload.get("tasks", [])
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["task_id"], "TT-001")
        self.assertEqual(tasks[0]["status"], "open")

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
            ["AGENTS.md", "harness.config.json", "golden-path.md", "context-pack.md", "context-pack.json", "PRD.md", "RUNNER_HANDOFF.md"],
        )
        self.assertEqual(exec_context["phase"], "discover")

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
            ["AGENTS.md", "context-pack.md", "context-pack.json", "harness.config.json", "golden-path.md", "PRD.md", "RUNNER_HANDOFF.md"],
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

    def test_done_lock_preserved_when_all_tasks_done(self):
        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        paths = self._paths()

        tasks_payload = read_json(paths.tasks_json)
        self.assertIsNotNone(tasks_payload)
        assert tasks_payload is not None
        for task in tasks_payload.get("tasks", []):
            if isinstance(task, dict):
                task["status"] = "done"
        write_json(paths.tasks_json, tasks_payload)
        paths.done_lock.write_text("done\n", encoding="utf-8")

        create_runner_state(str(self.dev), "blog", "main", approve_enable=None)
        self.assertTrue(paths.done_lock.exists())
        state = read_json(paths.state_file)
        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state["status"], "done")
        self.assertTrue(bool(state["done_candidate"]))
        self.assertEqual(state["done_gate_status"], "passed")

    def test_setup_auto_closes_out_done_state_when_tasks_done_and_gates_pass(self):
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
        self.assertTrue(paths.done_lock.exists())
        self.assertFalse(paths.enable_pending.exists())
        state = read_json(paths.state_file)
        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state["status"], "done")
        self.assertTrue(bool(state["done_candidate"]))
        self.assertEqual(state["done_gate_status"], "passed")
        self.assertEqual(state["blockers"], [])
        self.assertEqual(state["current_phase"], "closeout")

    def test_setup_keeps_runner_open_when_done_closeout_gates_fail(self):
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
        self.assertEqual(state["done_gate_status"], "failed")
        self.assertEqual(state["next_task"], "Resolve final done-closeout gate failure.")
        self.assertIn("Final done closeout blocked:", state["blockers"][0])
        self.assertEqual(state["current_phase"], "closeout")

    def test_setup_auto_completes_final_run_gates_task_when_it_is_only_open_work(self):
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
        self.assertTrue(paths.done_lock.exists())
        state = read_json(paths.state_file)
        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state["status"], "done")
        self.assertIsNone(state["next_task_id"])
        self.assertEqual(state["current_phase"], "closeout")
        tasks_payload = read_json(paths.tasks_json)
        self.assertIsNotNone(tasks_payload)
        assert tasks_payload is not None
        task_statuses = {task["task_id"]: task["status"] for task in tasks_payload["tasks"]}
        self.assertEqual(task_statuses["TT-002"], "done")

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

    def test_prepare_cycle_uses_cycle_progress_baseline_across_setup_refresh(self):
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

        tasks_payload = read_json(paths.tasks_json)
        self.assertIsNotNone(tasks_payload)
        assert tasks_payload is not None
        for task in tasks_payload.get("tasks", []):
            if isinstance(task, dict):
                task["status"] = "done"
        write_json(paths.tasks_json, tasks_payload)

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

    def test_parse_args_rejects_obsolete_create_flag(self):
        with patch("sys.stderr", new=io.StringIO()):
            with self.assertRaises(SystemExit):
                parse_args(["--create", "--project", "blog"])

    def test_run_rejects_non_main_runner_id(self):
        code = run(["--setup", "--project", "blog", "--runner-id", "alpha", "--dev", str(self.dev)])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
