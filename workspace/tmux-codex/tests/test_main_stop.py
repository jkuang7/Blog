import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.main import stop_all_loop_sessions, stop_loop_session


def _make_runner_paths(base: Path, project: str) -> SimpleNamespace:
    memory_dir = base / project / ".memory"
    locks_dir = memory_dir / "runner" / "locks"
    locks_dir.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        memory_dir=memory_dir,
        stop_file=locks_dir / "RUNNER_STOP.lock",
        active_lock=locks_dir / "RUNNER_ACTIVE.lock",
    )


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
                patch("src.main.TmuxClient", return_value=tmux_instance),
                patch("src.main.resolve_target_project_root", return_value=project_root),
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
                patch("src.main.TmuxClient", return_value=tmux_instance),
                patch("src.main.resolve_target_project_root", return_value=project_root),
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


if __name__ == "__main__":
    unittest.main()
