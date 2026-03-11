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
from src.main import _best_effort_reap_zombie, _cleanup_stale_watchdogs, _pid_is_alive


def _make_runner_paths(base: Path, project: str) -> SimpleNamespace:
    memory_dir = base / project / ".memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        memory_dir=memory_dir,
        stop_file=memory_dir / "RUNNER_STOP.lock",
        active_lock=memory_dir / "RUNNER_ACTIVE.lock",
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
                patch("src.main._find_runner_watchdogs", return_value=[(111, "cmd")]),
                patch("src.main._terminate_pids", return_value=(1, 0, 0)) as terminate_mock,
            ):
                stop_loop_session("blog", "main")

            tmux_instance.kill_session.assert_called_once_with("runner-blog")
            terminate_mock.assert_called_once_with([111])
            self.assertFalse(paths.stop_file.exists())
            self.assertFalse(paths.active_lock.exists())

    def test_stop_loop_session_retains_stop_lock_when_watchdog_remains(self):
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
                patch("src.main._find_runner_watchdogs", return_value=[(222, "cmd")]),
                patch("src.main._terminate_pids", return_value=(0, 0, 1)),
            ):
                stop_loop_session("blog", "main")

            self.assertTrue(paths.stop_file.exists())
            self.assertFalse(paths.active_lock.exists())

    def test_stop_all_stops_watchdogs_without_tmux_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            paths_by_project = {"blog": _make_runner_paths(tmp_root, "blog")}

            def build_paths(*, dev: str, project: str, runner_id: str):  # noqa: ARG001
                return paths_by_project[project]

            tmux_instance = MagicMock()
            tmux_instance.list_sessions.return_value = []

            watchdog_cmd = (
                "python -m src.runner_watchdog "
                "--session runner-blog --project blog --runner-id main"
            )
            with (
                patch("src.main.get_tmux_config", return_value=None),
                patch("src.main.TmuxClient", return_value=tmux_instance),
                patch("src.main.build_runner_paths", side_effect=build_paths),
                patch("src.main._find_runner_watchdogs", return_value=[(333, watchdog_cmd)]),
                patch("src.main._terminate_pids", return_value=(1, 0, 0)) as terminate_mock,
            ):
                stop_all_loop_sessions()

            terminate_mock.assert_called_once_with([333])
            tmux_instance.kill_session.assert_not_called()
            self.assertFalse(paths_by_project["blog"].stop_file.exists())
            self.assertFalse(paths_by_project["blog"].active_lock.exists())

    def test_pid_is_alive_treats_zombie_as_inactive(self):
        with (
            patch("src.main.os.kill") as kill_mock,
            patch("src.main._pid_process_state", return_value="Z+"),
            patch("src.main._best_effort_reap_zombie") as reap_mock,
        ):
            self.assertFalse(_pid_is_alive(12345))
            kill_mock.assert_called_once_with(12345, 0)
            reap_mock.assert_called_once_with(12345)

    def test_best_effort_reap_zombie_only_targets_watchdog_parent(self):
        with (
            patch("src.main._pid_parent_pid", return_value=777),
            patch("src.main._pid_command", return_value="python -m src.runner_watchdog --project blog"),
            patch("src.main.os.kill") as kill_mock,
        ):
            self.assertTrue(_best_effort_reap_zombie(12345))
            kill_mock.assert_called_once()
            self.assertEqual(kill_mock.call_args[0][0], 777)

    def test_best_effort_reap_zombie_skips_non_watchdog_parent(self):
        with (
            patch("src.main._pid_parent_pid", return_value=777),
            patch("src.main._pid_command", return_value="/bin/zsh"),
            patch("src.main.os.kill") as kill_mock,
        ):
            self.assertFalse(_best_effort_reap_zombie(12345))
            kill_mock.assert_not_called()

    def test_cleanup_stale_watchdogs_terminates_orphaned_watchdogs(self):
        tmux_instance = MagicMock()
        tmux_instance.has_session.return_value = False
        cmd = "python -m src.runner_watchdog --session runner-blog --project blog --runner-id main"
        with (
            patch("src.main._find_runner_watchdogs", return_value=[(333, cmd)]),
            patch("src.main._pid_process_state", return_value="S"),
            patch("src.main._terminate_pids", return_value=(1, 0, 0)) as terminate_mock,
        ):
            summary = _cleanup_stale_watchdogs(tmux=tmux_instance, project="blog", runner_id="main")
        terminate_mock.assert_called_once_with([333], grace_seconds=0.8, poll_seconds=0.05)
        self.assertEqual(summary["stale"], 1)
        self.assertEqual(summary["orphans"], 1)
        self.assertEqual(summary["zombies"], 0)

    def test_cleanup_stale_watchdogs_terminates_zombies(self):
        tmux_instance = MagicMock()
        cmd = "python -m src.runner_watchdog --session runner-blog --project blog --runner-id main"
        with (
            patch("src.main._find_runner_watchdogs", return_value=[(444, cmd)]),
            patch("src.main._pid_process_state", return_value="Z"),
            patch("src.main._terminate_pids", return_value=(0, 0, 0)) as terminate_mock,
        ):
            summary = _cleanup_stale_watchdogs(tmux=tmux_instance, project="blog", runner_id="main")
        terminate_mock.assert_called_once_with([444], grace_seconds=0.8, poll_seconds=0.05)
        self.assertEqual(summary["stale"], 1)
        self.assertEqual(summary["orphans"], 0)
        self.assertEqual(summary["zombies"], 1)

    def test_create_loop_session_resets_lingering_watchdogs_before_spawn(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            project_root = tmp_root / "worktree"
            project_root.mkdir(parents=True, exist_ok=True)
            tmux_instance = MagicMock()
            tmux_instance.list_sessions.return_value = ["runner-blog"]
            tmux_instance.kill_session.return_value = True

            fake_paths = SimpleNamespace(
                complete_lock=tmp_root / "RUNNER_DONE.lock",
                stop_file=tmp_root / "RUNNER_STOP.lock",
            )

            with (
                patch("src.main.get_tmux_config", return_value=None),
                patch("src.main.TmuxClient", return_value=tmux_instance),
                patch("src.main.resolve_target_project_root", return_value=project_root),
                patch("src.main.ensure_gates_file", return_value=(project_root / ".memory" / "gates.sh", False)),
                patch("src.main.create_runner_state", return_value={"ok": True, "enable_token": None}),
                patch("src.main._prepare_loop_runner", return_value=("runner-blog", fake_paths, "echo run")),
                patch("src.main._cleanup_stale_watchdogs", return_value={"stale": 0, "zombies": 0, "orphans": 0, "terminated": 0, "forced": 0, "remaining": 0}),
                patch("src.main._find_runner_watchdogs", return_value=[(555, "cmd")]),
                patch("src.main._terminate_pids", return_value=(1, 0, 0)) as terminate_mock,
                patch("src.main.spawn_runner_watchdog") as spawn_watchdog,
            ):
                from src.main import create_loop_session

                create_loop_session(
                    project="blog",
                    runner_id="main",
                    model="gpt-5.3-codex",
                    reasoning_effort="high",
                    hil_mode="setup-only",
                    runner_mode="interactive-watchdog",
                )

            tmux_instance.kill_session.assert_called_once_with("runner-blog")
            terminate_mock.assert_called_once_with([555], grace_seconds=1.0, poll_seconds=0.05)
            spawn_watchdog.assert_called_once()


if __name__ == "__main__":
    unittest.main()
