import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.main import _seed_kanban_state_for_background_runner, stop_all_loop_sessions, stop_loop_session
from src.runner_control import RunnerControlPlane
from src.runner_state import build_runner_state_paths_for_root, read_json, write_json


def _make_runner_paths(base: Path, project: str) -> SimpleNamespace:
    memory_dir = base / project / ".memory"
    locks_dir = memory_dir / "runner" / "locks"
    locks_dir.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        memory_dir=memory_dir,
        stop_file=locks_dir / "RUNNER_STOP.lock",
        active_lock=locks_dir / "RUNNER_ACTIVE.lock",
    )


def _fake_issue_snapshot(project_root: Path, *, identifier: str, title: str, state_name: str = "In Progress") -> dict[str, object]:
    return {
        "url": f"https://linear.app/jkprojects/issue/{identifier}",
        "external_url": f"https://linear.app/jkprojects/issue/{identifier}",
        "repo": "blog",
        "number": None,
        "identifier": identifier,
        "linear_id": f"lin-{identifier.lower()}",
        "title": title,
        "description": f"{title} description",
        "project_key": "blog",
        "project_name": "Blog",
        "repo_root": str(project_root),
        "worktree": str(project_root),
        "branch": f"linear/{identifier.lower()}",
        "state_name": state_name,
        "state_type": "started",
        "metadata": {},
    }


class StopRunnerControlsTests(unittest.TestCase):
    def test_stop_loop_session_clears_transient_locks_after_hard_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            project_root = tmp_root / "worktree"
            project_root.mkdir(parents=True, exist_ok=True)
            paths = _make_runner_paths(tmp_root, "blog")

            tmux_instance = MagicMock()
            tmux_instance.list_sessions.return_value = ["runner-blog"]
            tmux_instance.kill_session.return_value = True

            with (
                patch("src.main.get_tmux_config", return_value=None),
                patch("src.main.TmuxClient", return_value=tmux_instance),
                patch("src.main.resolve_target_project_root", return_value=project_root),
                patch("src.main.build_runner_paths", return_value=paths),
            ):
                stop_loop_session("blog", "main")

            tmux_instance.kill_session.assert_called_once_with("runner-blog")
            self.assertFalse(paths.stop_file.exists())
            self.assertFalse(paths.active_lock.exists())

    def test_stop_loop_session_clears_locks_when_session_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            project_root = tmp_root / "worktree"
            project_root.mkdir(parents=True, exist_ok=True)
            paths = _make_runner_paths(tmp_root, "blog")

            tmux_instance = MagicMock()
            tmux_instance.list_sessions.return_value = []
            tmux_instance.kill_session.return_value = False

            with (
                patch("src.main.get_tmux_config", return_value=None),
                patch("src.main.TmuxClient", return_value=tmux_instance),
                patch("src.main.resolve_target_project_root", return_value=project_root),
                patch("src.main.build_runner_paths", return_value=paths),
            ):
                stop_loop_session("blog", "main")

            self.assertFalse(paths.stop_file.exists())
            self.assertFalse(paths.active_lock.exists())

    def test_stop_all_stops_tmux_sessions_and_clears_locks(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            paths_by_project = {"blog": _make_runner_paths(tmp_root, "blog")}

            def build_paths(*, dev: str, project: str, runner_id: str):  # noqa: ARG001
                return paths_by_project[project]

            tmux_instance = MagicMock()
            tmux_instance.list_sessions.return_value = ["runner-blog"]
            with (
                patch("src.main.get_tmux_config", return_value=None),
                patch("src.main.TmuxClient", return_value=tmux_instance),
                patch("src.main.build_runner_paths", side_effect=build_paths),
            ):
                stop_all_loop_sessions()

            tmux_instance.kill_session.assert_called_once_with("runner-blog")
            self.assertFalse(paths_by_project["blog"].stop_file.exists())
            self.assertFalse(paths_by_project["blog"].active_lock.exists())

    def test_create_loop_session_restarts_existing_tmux_session_before_spawn(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            project_root = tmp_root / "worktree"
            project_root.mkdir(parents=True, exist_ok=True)
            tmux_instance = MagicMock()
            tmux_instance.list_sessions.return_value = ["runner-blog"]
            tmux_instance.kill_session.return_value = True

            fake_paths = SimpleNamespace(
                complete_lock=tmp_root / "runner" / "locks" / "RUNNER_DONE.lock",
                stop_file=tmp_root / "runner" / "locks" / "RUNNER_STOP.lock",
            )

            with (
                patch("src.main.get_tmux_config", return_value=None),
                patch("src.main.ensure_runner_prompt_install", return_value=None),
                patch("src.main.TmuxClient", return_value=tmux_instance),
                patch("src.main.resolve_target_project_root", return_value=project_root),
                patch("src.main._seed_kanban_state_for_background_runner", return_value=None),
                patch("src.main.ensure_gates_file", return_value=(project_root / ".memory" / "gates.sh", False)),
                patch("src.main._ensure_runner_ready_for_start", return_value=True),
                patch("src.main._prepare_loop_runner", return_value=("runner-blog", fake_paths, "echo run")),
            ):
                from src.main import create_loop_session

                create_loop_session(
                    project="blog",
                    runner_id="main",
                    model="gpt-5.3-codex",
                    reasoning_effort="high",
                )

            tmux_instance.kill_session.assert_called_once_with("runner-blog")

    def test_create_loop_session_stops_when_runner_not_prepared(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            project_root = tmp_root / "worktree"
            project_root.mkdir(parents=True, exist_ok=True)
            tmux_instance = MagicMock()
            tmux_instance.list_sessions.return_value = []

            with (
                patch("src.main.get_tmux_config", return_value=None),
                patch("src.main.ensure_runner_prompt_install", return_value=None),
                patch("src.main.TmuxClient", return_value=tmux_instance),
                patch("src.main.resolve_target_project_root", return_value=project_root),
                patch("src.main._seed_kanban_state_for_background_runner", return_value=None),
                patch("src.main.ensure_gates_file", return_value=(project_root / ".memory" / "gates.sh", False)),
                patch("src.main._ensure_runner_ready_for_start", return_value=False),
                patch("src.main._prepare_loop_runner") as prepare_mock,
            ):
                from src.main import create_loop_session

                create_loop_session(
                    project="blog",
                    runner_id="main",
                    model="gpt-5.3-codex",
                    reasoning_effort="high",
                )

            tmux_instance.create_session.assert_not_called()
            prepare_mock.assert_not_called()

    def test_start_loop_session_can_skip_tmux_attach_for_background_control(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            project_root = tmp_root / "worktree"
            project_root.mkdir(parents=True, exist_ok=True)
            tmux_instance = MagicMock()
            tmux_instance.list_sessions.return_value = []

            fake_paths = SimpleNamespace(
                complete_lock=tmp_root / "runner" / "locks" / "RUNNER_DONE.lock",
                stop_file=tmp_root / "runner" / "locks" / "RUNNER_STOP.lock",
            )

            with (
                patch("src.main.get_tmux_config", return_value=None),
                patch("src.main.ensure_runner_prompt_install", return_value=None),
                patch("src.main.TmuxClient", return_value=tmux_instance),
                patch("src.main.resolve_target_project_root", return_value=project_root),
                patch("src.main._seed_kanban_state_for_background_runner", return_value=None),
                patch("src.main.ensure_gates_file", return_value=(project_root / ".memory" / "gates.sh", False)),
                patch("src.main._ensure_runner_ready_for_start", return_value=True),
                patch("src.main._prepare_loop_runner", return_value=("runner-blog", fake_paths, "echo run")),
            ):
                from src.main import start_loop_session

                start_loop_session(
                    project="blog",
                    runner_id="main",
                    model="gpt-5.3-codex",
                    reasoning_effort="high",
                    attach=False,
                )

            tmux_instance.create_session.assert_called_once_with("runner-blog", "echo run")
            tmux_instance.attach.assert_not_called()

    def test_start_loop_session_auto_setup_repairs_runner_before_background_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            project_root = tmp_root / "worktree"
            project_root.mkdir(parents=True, exist_ok=True)
            tmux_instance = MagicMock()
            tmux_instance.list_sessions.return_value = []

            fake_paths = SimpleNamespace(
                complete_lock=tmp_root / "runner" / "locks" / "RUNNER_DONE.lock",
                stop_file=tmp_root / "runner" / "locks" / "RUNNER_STOP.lock",
            )

            with (
                patch("src.main.get_tmux_config", return_value=None),
                patch("src.main.ensure_runner_prompt_install", return_value=None),
                patch("src.main.TmuxClient", return_value=tmux_instance),
                patch("src.main.resolve_target_project_root", return_value=project_root),
                patch("src.main._seed_kanban_state_for_background_runner", return_value=None),
                patch("src.main.ensure_gates_file", return_value=(project_root / ".memory" / "gates.sh", False)),
                patch("src.main.create_runner_state", side_effect=[
                    {"ok": True, "enable_token": "token-123", "enable_pending_file": "pending.json"},
                    {"ok": True},
                ]) as create_state_mock,
                patch("src.main._ensure_runner_ready_for_start", return_value=True),
                patch("src.main._prepare_loop_runner", return_value=("runner-blog", fake_paths, "echo run")),
            ):
                from src.main import start_loop_session

                start_loop_session(
                    project="blog",
                    runner_id="main",
                    model="gpt-5.3-codex",
                    reasoning_effort="high",
                    attach=False,
                    auto_setup=True,
                )

            self.assertEqual(create_state_mock.call_count, 2)
            tmux_instance.create_session.assert_called_once_with("runner-blog", "echo run")
            tmux_instance.attach.assert_not_called()

    def test_seed_kanban_state_for_background_runner_selects_active_issue(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp)
            project_root = dev / "Repos" / "blog"
            project_root.mkdir(parents=True, exist_ok=True)
            snapshot = _fake_issue_snapshot(
                project_root,
                identifier="PRO-12",
                title="Fix status sync",
            )
            prepared = SimpleNamespace(snapshot=snapshot, phase="executing", worktree_path=project_root)

            with (
                patch("src.main.fetch_project_context", return_value={"issue": {"identifier": "PRO-12"}}),
                patch("src.main.prepare_linear_issue_context", return_value=prepared),
            ):
                issue = _seed_kanban_state_for_background_runner(
                    dev=str(dev),
                    project="blog",
                    runner_id="main",
                    project_root=project_root,
                )

            self.assertIsNotNone(issue)
            paths = build_runner_state_paths_for_root(
                project_root=project_root,
                dev=str(dev),
                project="blog",
                runner_id="main",
            )
            kanban_state = read_json(paths.kanban_state_json)
            self.assertEqual(kanban_state["phase"], "executing")
            self.assertEqual(kanban_state["active_issue"]["url"], "https://linear.app/jkprojects/issue/PRO-12")
            self.assertEqual(kanban_state["board"]["last_known_status"], "In Progress")
            self.assertEqual(kanban_state["board"]["snapshot_count"], 1)
            self.assertEqual(kanban_state["loop"]["resume_source"], "orx_linear_context")

    def test_seed_kanban_state_preserves_existing_active_issue(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp)
            project_root = dev / "Repos" / "blog"
            project_root.mkdir(parents=True, exist_ok=True)
            paths = build_runner_state_paths_for_root(
                project_root=project_root,
                dev=str(dev),
                project="blog",
                runner_id="main",
            )
            paths.memory_dir.mkdir(parents=True, exist_ok=True)
            write_json(
                paths.kanban_state_json,
                {
                    "project": "blog",
                    "mode": "ticket_native",
                    "version": 1,
                    "phase": "executing",
                    "active_issue": {
                        "url": "https://linear.app/jkprojects/issue/PRO-99",
                        "repo": "blog",
                        "number": None,
                        "identifier": "PRO-99",
                        "title": "Keep current frontier",
                    },
                },
            )
            snapshot = _fake_issue_snapshot(
                project_root,
                identifier="PRO-12",
                title="Fix status sync",
            )
            prepared = SimpleNamespace(snapshot=snapshot, phase="executing", worktree_path=project_root)

            with (
                patch("src.main.fetch_project_context", return_value={"issue": {"identifier": "PRO-12"}}),
                patch("src.main.prepare_linear_issue_context", return_value=prepared),
            ):
                issue = _seed_kanban_state_for_background_runner(
                    dev=str(dev),
                    project="blog",
                    runner_id="main",
                    project_root=project_root,
                )

            self.assertIsNotNone(issue)
            kanban_state = read_json(paths.kanban_state_json)
            self.assertEqual(kanban_state["active_issue"]["url"], "https://linear.app/jkprojects/issue/PRO-99")
            self.assertEqual(kanban_state["board"]["last_known_status"], "In Progress")

    def test_seed_kanban_state_replaces_stale_existing_active_issue_with_board_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp)
            project_root = dev / "Repos" / "blog"
            project_root.mkdir(parents=True, exist_ok=True)
            paths = build_runner_state_paths_for_root(
                project_root=project_root,
                dev=str(dev),
                project="blog",
                runner_id="main",
            )
            paths.memory_dir.mkdir(parents=True, exist_ok=True)
            write_json(
                paths.kanban_state_json,
                {
                    "project": "blog",
                    "mode": "ticket_native",
                    "version": 1,
                    "phase": "executing",
                    "active_issue": {
                        "url": "https://linear.app/jkprojects/issue/PRO-99",
                        "repo": "blog",
                        "number": None,
                        "identifier": "PRO-99",
                        "title": "Stale current frontier",
                    },
                },
            )
            snapshot = _fake_issue_snapshot(
                project_root,
                identifier="PRO-12",
                title="Fix status sync",
                state_name="Inbox",
            )
            prepared = SimpleNamespace(snapshot=snapshot, phase="selecting", worktree_path=project_root)

            with (
                patch("src.main.fetch_project_context", return_value={"next_candidate": {"identifier": "PRO-12"}}),
                patch("src.main.prepare_linear_issue_context", return_value=prepared),
                patch("src.main._active_issue_requires_runtime_reset", return_value=True),
            ):
                issue = _seed_kanban_state_for_background_runner(
                    dev=str(dev),
                    project="blog",
                    runner_id="main",
                    project_root=project_root,
                )

            self.assertIsNotNone(issue)
            kanban_state = read_json(paths.kanban_state_json)
            self.assertEqual(kanban_state["active_issue"]["url"], "https://linear.app/jkprojects/issue/PRO-12")
            self.assertEqual(kanban_state["board"]["last_known_status"], "Inbox")
            self.assertEqual(kanban_state["loop"]["resume_source"], "runtime_recovery_reset")

    def test_seed_kanban_state_clears_closeout_issue_that_still_needs_refine(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp)
            project_root = dev / "Repos" / "blog"
            project_root.mkdir(parents=True, exist_ok=True)
            paths = build_runner_state_paths_for_root(
                project_root=project_root,
                dev=str(dev),
                project="blog",
                runner_id="main",
            )
            paths.memory_dir.mkdir(parents=True, exist_ok=True)
            write_json(
                paths.kanban_state_json,
                {
                    "project": "blog",
                    "mode": "ticket_native",
                    "version": 1,
                    "phase": "executing",
                    "active_issue": {
                        "url": "https://linear.app/jkprojects/issue/PRO-99",
                        "repo": "blog",
                        "number": None,
                        "identifier": "PRO-99",
                        "title": "Stale closeout frontier",
                    },
                },
            )
            write_json(
                paths.runner_status_json,
                {
                    "current_phase": "closeout",
                    "done_gate_status": "failed",
                },
            )
            write_json(
                paths.state_file,
                {
                    "current_phase": "closeout",
                    "phase_status": "active",
                    "done_gate_status": "failed",
                    "next_task": "Resolve final done-closeout gate failure.",
                    "next_task_reason": "Final done-closeout validation is failing while the closeout task remains open.",
                },
            )
            write_json(paths.exec_context_json, {"phase": "closeout"})

            control = RunnerControlPlane(paths)
            control._sync_conditions(
                "https://linear.app/jkprojects/issue/PRO-99",
                {
                    "ready_for_execution": {
                        "status": False,
                        "reason": "enhance_required",
                        "message": "needs refine",
                    },
                    "planning_satisfied": {
                        "status": False,
                        "reason": "phase_not_advanced",
                        "message": "planning cannot proceed yet",
                    },
                },
            )
            snapshot = _fake_issue_snapshot(
                project_root,
                identifier="PRO-12",
                title="Next board candidate",
                state_name="Inbox",
            )
            prepared = SimpleNamespace(snapshot=snapshot, phase="selecting", worktree_path=project_root)

            with (
                patch("src.main.fetch_project_context", return_value={"next_candidate": {"identifier": "PRO-12"}}),
                patch("src.main.prepare_linear_issue_context", return_value=prepared),
                patch("src.main._active_issue_requires_runtime_reset", return_value=True),
            ):
                issue = _seed_kanban_state_for_background_runner(
                    dev=str(dev),
                    project="blog",
                    runner_id="main",
                    project_root=project_root,
                )

            self.assertIsNotNone(issue)
            kanban_state = read_json(paths.kanban_state_json)
            self.assertEqual(kanban_state["active_issue"]["url"], "https://linear.app/jkprojects/issue/PRO-12")
            self.assertEqual(kanban_state["loop"]["resume_source"], "runtime_recovery_reset")
            refreshed_state = read_json(paths.state_file)
            refreshed_status = read_json(paths.runner_status_json)
            refreshed_exec = read_json(paths.exec_context_json)
            self.assertEqual(refreshed_state["current_phase"], "discover")
            self.assertEqual(refreshed_state["done_gate_status"], "pending")
            self.assertEqual(refreshed_status["current_phase"], "discover")
            self.assertEqual(refreshed_status["done_gate_status"], "pending")
            self.assertEqual(refreshed_exec["phase"], "discover")


if __name__ == "__main__":
    unittest.main()
