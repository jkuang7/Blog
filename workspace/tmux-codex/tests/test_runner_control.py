import sqlite3
import tempfile
import unittest
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.runner_control import RunnerControlPlane, reconcile_control_plane
from src.runner_state import (
    build_runner_state_paths_for_root,
    default_kanban_state,
    default_runner_state,
    ensure_memory_dir,
    write_json,
)


class RunnerControlPlaneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dev = Path(self.tmp.name)
        self.project_root = self.dev / "Repos" / "blog"
        self.project_root.mkdir(parents=True)
        self.paths = build_runner_state_paths_for_root(
            project_root=self.project_root,
            dev=str(self.dev),
            project="blog",
            runner_id="main",
        )
        ensure_memory_dir(self.paths)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _active_state(self) -> tuple[dict, dict]:
        state = default_runner_state("blog", "main")
        state["project_root"] = str(self.project_root)
        state["git_branch"] = "feature/issue-9"
        state["git_worktree"] = str(self.project_root)
        state["current_phase"] = "implement"
        kanban_state = default_kanban_state("blog")
        kanban_state["phase"] = "executing"
        kanban_state["active_issue"] = {
            "url": "https://github.com/jkuang7/blog/issues/9",
            "repo": "jkuang7/blog",
            "number": 9,
            "title": "Ship control plane",
            "issue_class": "feature",
            "complexity": "M",
            "routing": "backend",
        }
        kanban_state["active_checkout"] = {
            "repo_root": str(self.project_root),
            "worktree": str(self.project_root),
            "branch": "feature/issue-9",
        }
        return state, kanban_state

    def test_reconcile_persists_run_lease_conditions_and_checkpoint(self) -> None:
        state, kanban_state = self._active_state()

        derived = reconcile_control_plane(
            paths=self.paths,
            state=state,
            kanban_state=kanban_state,
        )

        self.assertTrue(self.paths.control_db.exists())
        self.assertEqual(derived["control"]["run"]["status"], "running")
        self.assertEqual(derived["control"]["lease"]["owner_id"], "runner:blog:main")

        conn = sqlite3.connect(self.paths.control_db)
        runs = conn.execute("SELECT COUNT(*) FROM orchestrator_runs").fetchone()[0]
        conditions = conn.execute("SELECT COUNT(*) FROM orchestrator_conditions").fetchone()[0]
        checkpoints = conn.execute("SELECT COUNT(*) FROM orchestrator_checkpoints").fetchone()[0]
        handoffs = conn.execute("SELECT COUNT(*) FROM orchestrator_handoffs").fetchone()[0]
        condition_row = conn.execute(
            "SELECT condition_key, status, reason, message FROM orchestrator_conditions WHERE condition_key = 'workspace_healthy'"
        ).fetchone()
        handoff_row = conn.execute(
            "SELECT payload_json FROM orchestrator_handoffs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()

        self.assertEqual(runs, 1)
        self.assertGreaterEqual(conditions, 1)
        self.assertEqual(checkpoints, 1)
        self.assertEqual(handoffs, 1)
        self.assertIsNotNone(condition_row)
        self.assertTrue(condition_row[3])
        self.assertIsNotNone(handoff_row)
        self.assertIn('"decision"', handoff_row[0])
        self.assertIn('"resume_point"', handoff_row[0])

    def test_reconcile_recovers_stale_lease_owned_by_other_runner(self) -> None:
        state, kanban_state = self._active_state()
        issue_url = kanban_state["active_issue"]["url"]
        control = RunnerControlPlane(self.paths)

        conn = sqlite3.connect(self.paths.control_db)
        conn.execute(
            """
            INSERT INTO orchestrator_leases(issue_url, owner_id, acquired_at, lease_expires_at, heartbeat_at)
            VALUES(?1, 'runner:blog:other', '2026-04-14T00:00:00Z', '2026-04-14T00:00:01Z', '2026-04-14T00:00:01Z')
            """,
            (issue_url,),
        )
        conn.commit()
        conn.close()

        derived = control.reconcile(state=state, kanban_state=kanban_state, enable_pending_exists=False)

        self.assertEqual(derived["control"]["lease"]["owner_id"], "runner:blog:main")
        conn = sqlite3.connect(self.paths.control_db)
        event_kinds = [row[0] for row in conn.execute("SELECT event_kind FROM orchestrator_run_events ORDER BY id ASC")]
        conn.close()
        self.assertIn("lease_recovered", event_kinds)

    def test_pause_override_is_consumed_and_releases_active_lease(self) -> None:
        state, kanban_state = self._active_state()
        control = RunnerControlPlane(self.paths)
        control.reconcile(state=state, kanban_state=kanban_state, enable_pending_exists=False)

        control.request_override(action="pause", requested_by="telegram", reason="operator pause")
        derived = control.reconcile(state=state, kanban_state=kanban_state, enable_pending_exists=False)

        self.assertEqual(derived["reconcile"]["actions"][1]["action"], "wait_for_human_approval")
        self.assertIsNone(derived["control"]["lease"])
        self.assertEqual(derived["control"]["run"]["status"], "paused")

        conn = sqlite3.connect(self.paths.control_db)
        pending = conn.execute(
            "SELECT COUNT(*) FROM orchestrator_operator_overrides WHERE status = 'pending'"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(pending, 0)

    def test_reconcile_surfaces_followup_mutation_audit_state(self) -> None:
        state, kanban_state = self._active_state()
        kanban_state["phase"] = "blocked"
        kanban_state["blocker"] = {
            "is_blocked": True,
            "category": "dependency",
            "reason": "waiting on migration",
            "needs": "dependency_clear",
            "resume_from": "execution",
            "external": True,
        }
        control = RunnerControlPlane(self.paths)

        derived = control.reconcile(state=state, kanban_state=kanban_state, enable_pending_exists=False)

        self.assertEqual(derived["control"]["run"]["status"], "blocked")
        mutation = derived["control"]["mutation"]["pending"][0]
        self.assertEqual(mutation["operation"], "create_issue")
        self.assertEqual(mutation["parent_issue_url"], "https://github.com/jkuang7/blog/issues/9")
        self.assertEqual(derived["control"]["mutation"]["last_event"]["event_kind"], "followup_requested")

    def test_force_override_promotes_target_issue_into_active_issue(self) -> None:
        state, kanban_state = self._active_state()
        kanban_state["active_issue"] = None
        kanban_state["phase"] = "selecting"
        control = RunnerControlPlane(self.paths)
        control.request_override(
            action="force",
            requested_by="telegram",
            target_issue={
                "url": "https://github.com/jkuang7/blog/issues/12",
                "repo": "jkuang7/blog",
                "number": 12,
                "title": "Forced issue",
                "issue_class": "feature",
                "complexity": "S",
                "routing": "backend",
            },
        )

        derived = control.reconcile(state=state, kanban_state=kanban_state, enable_pending_exists=False)

        self.assertEqual(derived["active_issue"]["url"], "https://github.com/jkuang7/blog/issues/12")
        self.assertEqual(derived["phase"], "executing")

    def test_reconcile_recovers_active_run_when_kanban_state_is_idle(self) -> None:
        state, kanban_state = self._active_state()
        control = RunnerControlPlane(self.paths)
        control.reconcile(state=state, kanban_state=kanban_state, enable_pending_exists=False)

        kanban_state["active_issue"] = None
        kanban_state["phase"] = "selecting"
        derived = control.reconcile(state=state, kanban_state=kanban_state, enable_pending_exists=False)

        self.assertEqual(derived["active_issue"]["url"], "https://github.com/jkuang7/blog/issues/9")
        self.assertEqual(derived["control"]["run"]["issue_url"], "https://github.com/jkuang7/blog/issues/9")

    def test_reconcile_yields_non_executable_recovered_run_to_ready_board_issue(self) -> None:
        state, kanban_state = self._active_state()
        kanban_state["active_issue"]["complexity"] = "XL"
        control = RunnerControlPlane(self.paths)
        first = control.reconcile(state=state, kanban_state=kanban_state, enable_pending_exists=False)
        self.assertEqual(first["conditions"]["ready_for_execution"]["reason"], "enhance_required")

        control._upsert_issue_snapshot(
            {
                "url": "https://github.com/jkuang7/blog/issues/9",
                "repo": "jkuang7/blog",
                "number": 9,
                "title": "Ship control plane",
                "issue_class": "feature",
                "complexity": "XL",
                "routing": "backend",
                "Status": "Ready",
            },
            phase="executing",
        )
        control._upsert_issue_snapshot(
            {
                "url": "https://github.com/jkuang7/blog/issues/12",
                "repo": "jkuang7/blog",
                "number": 12,
                "title": "Ready child",
                "issue_class": "feature",
                "complexity": "S",
                "routing": "backend",
                "Status": "Ready",
            },
            phase="selecting",
        )

        kanban_state["active_issue"] = None
        kanban_state["phase"] = "selecting"
        derived = control.reconcile(state=state, kanban_state=kanban_state, enable_pending_exists=False)

        self.assertEqual(derived["active_issue"]["url"], "https://github.com/jkuang7/blog/issues/12")
        self.assertEqual(derived["control"]["diagnostics"]["metrics"]["stale_run_yielded"], 1)

    def test_reconcile_yields_non_executable_active_issue_without_completing_old_run(self) -> None:
        state, kanban_state = self._active_state()
        control = RunnerControlPlane(self.paths)
        control.import_github_item(
            {
                "itemId": "PVTI_active_bad",
                "repo": "jkuang7/blog",
                "number": 9,
                "title": "Ship control plane",
                "url": "https://github.com/jkuang7/blog/issues/9",
                "fields": {"Status": "Ready", "Priority": "P0", "Type": "Feature"},
            },
            issue_thread={"body": "", "comments": []},
        )
        first = control.reconcile(state=state, kanban_state=kanban_state, enable_pending_exists=False)
        self.assertEqual(first["control"]["run"]["status"], "running")

        control._upsert_issue_snapshot(
            {
                "url": "https://github.com/jkuang7/blog/issues/9",
                "repo": "jkuang7/blog",
                "number": 9,
                "title": "Ship control plane",
                "issue_class": "feature",
                "complexity": "XL",
                "routing": "backend",
                "Status": "Ready",
            },
            phase="executing",
        )
        control._upsert_issue_snapshot(
            {
                "url": "https://github.com/jkuang7/blog/issues/12",
                "repo": "jkuang7/blog",
                "number": 12,
                "title": "Ready child",
                "issue_class": "feature",
                "complexity": "S",
                "routing": "backend",
                "Status": "Ready",
            },
            phase="selecting",
        )
        kanban_state["active_issue"]["complexity"] = "XL"
        derived = control.reconcile(state=state, kanban_state=kanban_state, enable_pending_exists=False)

        self.assertEqual(derived["active_issue"]["url"], "https://github.com/jkuang7/blog/issues/12")
        conn = sqlite3.connect(self.paths.control_db)
        previous_status = conn.execute(
            """
            SELECT status FROM orchestrator_runs
            WHERE issue_url = 'https://github.com/jkuang7/blog/issues/9'
            ORDER BY updated_at DESC
            LIMIT 1
            """
        ).fetchone()[0]
        previous_lease = conn.execute(
            """
            SELECT COUNT(*) FROM orchestrator_leases
            WHERE issue_url = 'https://github.com/jkuang7/blog/issues/9'
            """
        ).fetchone()[0]
        conn.close()
        self.assertEqual(previous_status, "paused")
        self.assertEqual(previous_lease, 0)

    def test_reconcile_yields_active_issue_when_ready_condition_requires_enhance(self) -> None:
        state, kanban_state = self._active_state()
        control = RunnerControlPlane(self.paths)
        control.reconcile(state=state, kanban_state=kanban_state, enable_pending_exists=False)
        conn = sqlite3.connect(self.paths.control_db)
        conn.execute(
            """
            UPDATE orchestrator_conditions
            SET status = 0, reason = 'enhance_required', message = 'needs refine'
            WHERE issue_url = 'https://github.com/jkuang7/blog/issues/9'
              AND condition_key = 'ready_for_execution'
            """
        )
        conn.commit()
        conn.close()
        control._upsert_issue_snapshot(
            {
                "url": "https://github.com/jkuang7/blog/issues/12",
                "repo": "jkuang7/blog",
                "number": 12,
                "title": "Ready child",
                "issue_class": "feature",
                "complexity": "S",
                "routing": "backend",
                "Status": "Ready",
            },
            phase="selecting",
        )

        derived = control.reconcile(state=state, kanban_state=kanban_state, enable_pending_exists=False)

        self.assertEqual(derived["active_issue"]["url"], "https://github.com/jkuang7/blog/issues/12")

    def test_active_refinement_ticket_does_not_ping_pong_to_peer_candidate(self) -> None:
        state, kanban_state = self._active_state()
        control = RunnerControlPlane(self.paths)
        control.import_github_item(
            {
                "itemId": "PVTI_active",
                "repo": "jkuang7/blog",
                "number": 9,
                "title": "Refine current child",
                "url": "https://github.com/jkuang7/blog/issues/9",
                "fields": {"Status": "Inbox", "Priority": "P1", "Type": "Feature"},
            },
            issue_thread={
                "body": """
## Execution Routing
- Branch: issue-9
- Worktree: blog@issue-9
- Merge into: main
""",
                "comments": [],
            },
        )
        control.import_github_item(
            {
                "itemId": "PVTI_peer",
                "repo": "jkuang7/blog",
                "number": 12,
                "title": "Peer child",
                "url": "https://github.com/jkuang7/blog/issues/12",
                "fields": {"Status": "Inbox", "Priority": "P1", "Type": "Feature"},
            },
            issue_thread={
                "body": """
## Execution Routing
- Branch: issue-12
- Worktree: blog@issue-12
- Merge into: main
""",
                "comments": [],
            },
        )

        derived = control.reconcile(state=state, kanban_state=kanban_state, enable_pending_exists=False)

        self.assertEqual(derived["active_issue"]["url"], "https://github.com/jkuang7/blog/issues/9")
        self.assertNotIn("stale_run_yielded", derived["control"]["diagnostics"]["metrics"])

    def test_active_issue_in_closeout_with_enhance_required_yields_to_board(self) -> None:
        state, kanban_state = self._active_state()
        state["current_phase"] = "closeout"
        state["done_gate_status"] = "failed"
        control = RunnerControlPlane(self.paths)
        control.import_github_item(
            {
                "itemId": "PVTI_active",
                "repo": "jkuang7/blog",
                "number": 9,
                "title": "Smoke placeholder",
                "url": "https://github.com/jkuang7/blog/issues/9",
                "fields": {"Status": "Inbox", "Priority": "P1", "Type": "Feature"},
            },
            issue_thread={
                "body": """
## Execution Routing
- Branch: issue-9
- Worktree: blog@issue-9
- Merge into: main
""",
                "comments": [],
            },
        )
        control.import_github_item(
            {
                "itemId": "PVTI_peer",
                "repo": "jkuang7/blog",
                "number": 12,
                "title": "Actual executable child",
                "url": "https://github.com/jkuang7/blog/issues/12",
                "fields": {"Status": "Inbox", "Priority": "P1", "Type": "Feature"},
            },
            issue_thread={
                "body": """
## Execution Routing
- Branch: issue-12
- Worktree: blog@issue-12
- Merge into: main
""",
                "comments": [],
            },
        )

        control.reconcile(state=state, kanban_state=kanban_state, enable_pending_exists=False)
        conn = sqlite3.connect(self.paths.control_db)
        conn.execute(
            """
            UPDATE orchestrator_conditions
            SET status = 0, reason = 'enhance_required', message = 'needs refine'
            WHERE issue_url = 'https://github.com/jkuang7/blog/issues/9'
              AND condition_key = 'ready_for_execution'
            """
        )
        conn.execute(
            """
            UPDATE orchestrator_conditions
            SET status = 0, reason = 'phase_not_advanced', message = 'needs refine before planning'
            WHERE issue_url = 'https://github.com/jkuang7/blog/issues/9'
              AND condition_key = 'planning_satisfied'
            """
        )
        conn.commit()
        conn.close()

        first = control.reconcile(state=state, kanban_state=kanban_state, enable_pending_exists=False)
        self.assertEqual(first["active_issue"]["url"], "https://github.com/jkuang7/blog/issues/12")
        self.assertEqual(first["control"]["diagnostics"]["metrics"]["stale_run_yielded"], 1)
        conn = sqlite3.connect(self.paths.control_db)
        paused_status = conn.execute(
            """
            SELECT status FROM orchestrator_runs
            WHERE issue_url = 'https://github.com/jkuang7/blog/issues/9'
            ORDER BY updated_at DESC
            LIMIT 1
            """
        ).fetchone()[0]
        conn.close()
        self.assertEqual(paused_status, "paused")

    def test_resume_run_override_restores_exact_run(self) -> None:
        state, kanban_state = self._active_state()
        control = RunnerControlPlane(self.paths)
        first = control.reconcile(state=state, kanban_state=kanban_state, enable_pending_exists=False)
        run_id = first["control"]["run"]["run_id"]

        kanban_state["active_issue"] = None
        kanban_state["phase"] = "selecting"
        control.request_override(action="resume_run", requested_by="telegram", target_run_id=run_id)
        derived = control.reconcile(state=state, kanban_state=kanban_state, enable_pending_exists=False)

        self.assertEqual(derived["active_issue"]["url"], "https://github.com/jkuang7/blog/issues/9")
        self.assertEqual(derived["phase"], "executing")

    def test_idle_reconcile_selects_local_executable_issue_before_parent_tracker(self) -> None:
        state = default_runner_state("blog", "main")
        state["project_root"] = str(self.project_root)
        state["git_branch"] = "main"
        state["git_worktree"] = str(self.project_root)
        kanban_state = default_kanban_state("blog")
        control = RunnerControlPlane(self.paths)
        control._upsert_issue_snapshot(
            {
                "url": "https://github.com/jkuang7/blog/issues/20",
                "repo": "jkuang7/blog",
                "number": 20,
                "title": "Umbrella tracker",
                "issue_class": "phase_parent",
                "complexity": "M",
                "routing": "backend",
                "Status": "Ready",
                "children": ["https://github.com/jkuang7/blog/issues/21"],
            },
            phase="selecting",
        )
        control._upsert_issue_snapshot(
            {
                "url": "https://github.com/jkuang7/blog/issues/21",
                "repo": "jkuang7/blog",
                "number": 21,
                "title": "Executable child",
                "issue_class": "feature",
                "complexity": "S",
                "routing": "backend",
                "Status": "Ready",
                "parent": "https://github.com/jkuang7/blog/issues/20",
            },
            phase="selecting",
        )

        derived = control.reconcile(state=state, kanban_state=kanban_state, enable_pending_exists=False)

        self.assertEqual(derived["active_issue"]["url"], "https://github.com/jkuang7/blog/issues/21")
        self.assertEqual(derived["control"]["diagnostics"]["metrics"]["selection_count"], 1)

    def test_idle_selection_skips_feature_snapshot_that_has_children(self) -> None:
        state = default_runner_state("blog", "main")
        state["project_root"] = str(self.project_root)
        state["git_branch"] = "main"
        state["git_worktree"] = str(self.project_root)
        kanban_state = default_kanban_state("blog")
        control = RunnerControlPlane(self.paths)
        control._upsert_issue_snapshot(
            {
                "url": "https://github.com/jkuang7/blog/issues/40",
                "repo": "jkuang7/blog",
                "number": 40,
                "title": "Tracker-shaped feature",
                "issue_class": "feature",
                "complexity": "M",
                "routing": "backend",
                "Status": "Ready",
                "Children": ["https://github.com/jkuang7/blog/issues/41"],
            },
            phase="selecting",
        )
        control._upsert_issue_snapshot(
            {
                "url": "https://github.com/jkuang7/blog/issues/41",
                "repo": "jkuang7/blog",
                "number": 41,
                "title": "Actual child",
                "issue_class": "feature",
                "complexity": "S",
                "routing": "backend",
                "Status": "Ready",
                "Parent": "https://github.com/jkuang7/blog/issues/40",
            },
            phase="selecting",
        )

        derived = control.reconcile(state=state, kanban_state=kanban_state, enable_pending_exists=False)

        self.assertEqual(derived["active_issue"]["url"], "https://github.com/jkuang7/blog/issues/41")

    def test_active_issue_upsert_preserves_imported_board_metadata(self) -> None:
        state, kanban_state = self._active_state()
        control = RunnerControlPlane(self.paths)
        control.import_github_item(
            {
                "itemId": "PVTI_active",
                "repo": "jkuang7/blog",
                "number": 9,
                "title": "Ship control plane",
                "url": "https://github.com/jkuang7/blog/issues/9",
                "fields": {"Status": "Ready", "Priority": "P1", "Type": "Feature"},
            },
            issue_thread={
                "body": """
## Ticket Relations
- Parent: https://github.com/jkuang7/blog/issues/8
""",
                "comments": [],
            },
        )

        control.reconcile(state=state, kanban_state=kanban_state, enable_pending_exists=False)

        stored = control._issue_snapshot("https://github.com/jkuang7/blog/issues/9")
        assert stored is not None
        payload = stored["payload"]
        self.assertEqual(payload["status"], "Ready")
        self.assertEqual(payload["priority"], "P1")
        self.assertEqual(payload["parent"], "https://github.com/jkuang7/blog/issues/8")

    def test_idle_selection_skips_blocked_snapshot_and_keeps_ticket_metadata(self) -> None:
        state = default_runner_state("blog", "main")
        state["project_root"] = str(self.project_root)
        state["git_branch"] = "main"
        state["git_worktree"] = str(self.project_root)
        kanban_state = default_kanban_state("blog")
        control = RunnerControlPlane(self.paths)
        control._upsert_issue_snapshot(
            {
                "url": "https://github.com/jkuang7/blog/issues/30",
                "repo": "jkuang7/blog",
                "number": 30,
                "title": "Blocked child",
                "issue_class": "feature",
                "complexity": "S",
                "routing": "backend",
                "Status": "Ready",
                "Blocked by": "https://github.com/jkuang7/blog/issues/29",
            },
            phase="selecting",
        )
        control._upsert_issue_snapshot(
            {
                "url": "https://github.com/jkuang7/blog/issues/31",
                "repo": "jkuang7/blog",
                "number": 31,
                "title": "Ready child",
                "issue_class": "feature",
                "complexity": "S",
                "routing": "backend",
                "Status": "Ready",
                "Parent": "https://github.com/jkuang7/blog/issues/28",
                "Resume from": "blog@issue-31",
                "Merge into": "main",
            },
            phase="selecting",
        )

        derived = control.reconcile(state=state, kanban_state=kanban_state, enable_pending_exists=False)

        self.assertEqual(derived["active_issue"]["url"], "https://github.com/jkuang7/blog/issues/31")
        self.assertEqual(derived["active_issue"]["parent"], "https://github.com/jkuang7/blog/issues/28")
        self.assertEqual(derived["active_issue"]["resume_from"], "blog@issue-31")

    def test_import_github_item_preserves_ticket_relations_and_routing(self) -> None:
        control = RunnerControlPlane(self.paths)

        snapshot = control.import_github_item(
            {
                "itemId": "PVTI_123",
                "repo": "jkuang7/blog",
                "number": 41,
                "title": "Import me",
                "url": "https://github.com/jkuang7/blog/issues/41",
                "fields": {
                    "Status": "Ready",
                    "Priority": "P0",
                    "Type": "Feature",
                    "Routing": "backend",
                },
            },
            issue_thread={
                "body": """
## Ticket Relations
- Parent: https://github.com/jkuang7/blog/issues/40
- Children: https://github.com/jkuang7/blog/issues/42
- Blocked by: none
- Unblocks: https://github.com/jkuang7/blog/issues/43

## Execution Routing
- Worktree: blog@issue-41
- Branch: feature/issue-41
- Merge into: main
""",
                "comments": [
                    {
                        "createdAt": "2026-04-14T12:00:00Z",
                        "body": "Picking this up now.\n\nResume from: verification",
                    }
                ],
            },
        )

        self.assertEqual(snapshot["github"]["project_status"], "Ready")
        self.assertEqual(snapshot["priority"], "P0")
        self.assertEqual(snapshot["parent"], "https://github.com/jkuang7/blog/issues/40")
        self.assertEqual(snapshot["children"], ["https://github.com/jkuang7/blog/issues/42"])
        self.assertEqual(snapshot["resume_from"], "verification")
        self.assertEqual(snapshot["worktree"], "blog@issue-41")

        stored = control._issue_snapshot("https://github.com/jkuang7/blog/issues/41")
        assert stored is not None
        payload = stored["payload"]
        self.assertEqual(payload["branch"], "feature/issue-41")
        self.assertEqual(payload["merge_into"], "main")

    def test_idle_selection_prefers_ready_child_imported_from_github_metadata(self) -> None:
        state = default_runner_state("blog", "main")
        state["project_root"] = str(self.project_root)
        state["git_branch"] = "main"
        state["git_worktree"] = str(self.project_root)
        kanban_state = default_kanban_state("blog")
        control = RunnerControlPlane(self.paths)

        control.import_github_item(
            {
                "itemId": "PVTI_parent",
                "repo": "jkuang7/blog",
                "number": 50,
                "title": "Umbrella tracker",
                "url": "https://github.com/jkuang7/blog/issues/50",
                "fields": {"Status": "Ready", "Priority": "P0", "Type": "Umbrella"},
            },
            issue_thread={
                "body": """
## Ticket Relations
- Children: https://github.com/jkuang7/blog/issues/51
""",
                "comments": [],
            },
        )
        control.import_github_item(
            {
                "itemId": "PVTI_child",
                "repo": "jkuang7/blog",
                "number": 51,
                "title": "Executable child",
                "url": "https://github.com/jkuang7/blog/issues/51",
                "fields": {"Status": "Ready", "Priority": "P1", "Type": "Feature"},
            },
            issue_thread={
                "body": """
## Ticket Relations
- Parent: https://github.com/jkuang7/blog/issues/50

## Execution Routing
- Resume from: implementation
""",
                "comments": [],
            },
        )
        control.import_github_item(
            {
                "itemId": "PVTI_blocked",
                "repo": "jkuang7/blog",
                "number": 52,
                "title": "Blocked sibling",
                "url": "https://github.com/jkuang7/blog/issues/52",
                "fields": {"Status": "Ready", "Priority": "P0", "Type": "Feature"},
            },
            issue_thread={
                "body": """
## Ticket Relations
- Blocked by: https://github.com/jkuang7/blog/issues/49
""",
                "comments": [],
            },
        )

        derived = control.reconcile(state=state, kanban_state=kanban_state, enable_pending_exists=False)

        self.assertEqual(derived["active_issue"]["url"], "https://github.com/jkuang7/blog/issues/51")
        self.assertEqual(derived["active_issue"]["parent"], "https://github.com/jkuang7/blog/issues/50")
        self.assertEqual(derived["active_issue"]["resume_from"], "implementation")

    def test_diagnostics_include_replay_and_failure_classification(self) -> None:
        state, kanban_state = self._active_state()
        kanban_state["blocker"] = {
            "is_blocked": True,
            "category": "verification",
            "reason": "tests failing",
            "needs": "fix_tests",
            "resume_from": "verification",
            "external": False,
        }
        control = RunnerControlPlane(self.paths)
        derived = control.reconcile(state=state, kanban_state=kanban_state, enable_pending_exists=False)

        diagnostics = derived["control"]["diagnostics"]
        self.assertEqual(diagnostics["failure_classification"]["category"], "verification_passing")
        self.assertTrue(diagnostics["replay"]["events"])


if __name__ == "__main__":
    unittest.main()
