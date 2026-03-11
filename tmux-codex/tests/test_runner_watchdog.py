import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.runner_watchdog import CycleProgressSnapshot, RunnerWatchdogConfig, run_runner_watchdog


class _FakeTmux:
    def __init__(self, outputs=None, process_name="codex", has_session=True, respawn_ok=True):
        self.outputs = list(outputs or [""])
        self.process_name = process_name
        self.has_session_value = has_session
        self.respawn_ok = respawn_ok
        self.send_keys_calls = []
        self.send_interrupt_calls = []
        self.clear_line_calls = []
        self.respawn_calls = []

    def has_session(self, _session):
        return self.has_session_value

    def list_panes(self, _session):
        return ["%1"]

    def capture_pane(self, _session, lines=50):
        if self.outputs:
            return self.outputs.pop(0)
        return ""

    def get_pane_process(self, _session):
        return self.process_name

    def send_keys(self, target, text, enter=True, delay_ms=120):
        self.send_keys_calls.append((target, text, enter, delay_ms))
        return True

    def send_interrupt(self, target):
        self.send_interrupt_calls.append(target)
        return True

    def clear_prompt_line(self, target):
        self.clear_line_calls.append(target)
        return True

    def respawn_pane(self, target, cmd, kill=True):
        self.respawn_calls.append((target, cmd, kill))
        return self.respawn_ok


class RunnerWatchdogTests(unittest.TestCase):
    def _config(self, dev: str) -> RunnerWatchdogConfig:
        return RunnerWatchdogConfig(
            session="runner-blog",
            project="blog",
            runner_id="main",
            dev=dev,
            project_root=None,
            socket="/tmp/tmux-codex-test.sock",
            model="gpt-5.3-codex",
            reasoning_effort="high",
            poll_seconds=0,
            idle_cooldown_seconds=8,
        )

    @staticmethod
    def _non_empty_send_texts(tmux: _FakeTmux) -> list[str]:
        return [call[1] for call in tmux.send_keys_calls if call[1]]

    @staticmethod
    def _snapshot(
        *,
        phase: str = "implement",
        phase_status: str = "active",
        task_id: str = "TT-009",
        task: str = "Clear desktop typecheck drift and finish refactor closeout verification.",
        status: str = "ready",
        git_head: str = "abc123",
        worktree_fingerprint: str = "clean",
    ) -> CycleProgressSnapshot:
        return CycleProgressSnapshot(
            phase=phase,
            phase_status=phase_status,
            next_task_id=task_id,
            next_task=task,
            status=status,
            git_head=git_head,
            worktree_fingerprint=worktree_fingerprint,
        )

    def test_injects_discover_prompt_when_idle_without_exec_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp)
            (dev / "Repos" / "blog" / ".memory" / "runner").mkdir(parents=True)
            tmux = _FakeTmux(
                outputs=["OpenAI Codex\n› Implement {feature}\ngpt-5.3-codex xhigh · 100% left · ~/Dev\n"],
                process_name="node",
            )
            cfg = self._config(str(dev))

            now_ticks = iter([100.0, 100.0])
            run_runner_watchdog(
                cfg,
                tmux=tmux,
                sleep_fn=lambda _s: None,
                now_fn=lambda: next(now_ticks, 100.0),
                max_polls=1,
            )

            sent = self._non_empty_send_texts(tmux)
            self.assertEqual(len(sent), 1)
            self.assertIn("/prompts:runner-discover", sent[0])
            self.assertIn(f"DEV={dev}", sent[0])
            self.assertIn("PROJECT=blog", sent[0])
            self.assertIn("RUNNER_ID=main", sent[0])
            self.assertIn("PWD=", sent[0])
            self.assertIn("PROJECT_ROOT=", sent[0])
            self.assertIn("MODE=execute_only", sent[0])

    def test_injects_phase_prompt_from_exec_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp)
            runner_dir = dev / "Repos" / "blog" / ".memory" / "runner"
            runner_dir.mkdir(parents=True)
            (runner_dir / "RUNNER_EXEC_CONTEXT.json").write_text(
                json.dumps({"phase": "verify"}),
                encoding="utf-8",
            )
            tmux = _FakeTmux(outputs=["OpenAI Codex\n› Verify\n"], process_name="node")
            cfg = self._config(str(dev))

            run_runner_watchdog(
                cfg,
                tmux=tmux,
                sleep_fn=lambda _s: None,
                now_fn=lambda: 100.0,
                max_polls=1,
            )

            sent = self._non_empty_send_texts(tmux)
            self.assertEqual(len(sent), 1)
            self.assertIn("/prompts:runner-verify", sent[0])

    def test_accepts_directory_trust_prompt_before_injecting_runner_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp)
            (dev / "Repos" / "blog" / ".memory" / "runner").mkdir(parents=True)
            tmux = _FakeTmux(
                outputs=[
                    "> You are in /tmp/blog\n\nDo you trust the contents of this directory?\n› 1. Yes, continue\n  2. No, quit\n\nPress enter to continue\n",
                ],
                process_name="node",
            )
            cfg = self._config(str(dev))

            run_runner_watchdog(
                cfg,
                tmux=tmux,
                sleep_fn=lambda _s: None,
                now_fn=lambda: 100.0,
                max_polls=1,
            )

            sent = self._non_empty_send_texts(tmux)
            self.assertEqual(sent, [])
            empty_enters = [call for call in tmux.send_keys_calls if call[1] == "" and call[2]]
            self.assertEqual(len(empty_enters), 1)

    def test_records_cycle_progress_baseline_before_injection(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp)
            runner_dir = dev / "Repos" / "blog" / ".memory" / "runner"
            runner_dir.mkdir(parents=True)
            (runner_dir / "RUNNER_STATE.json").write_text(
                json.dumps(
                    {
                        "current_phase": "discover",
                        "phase_status": "active",
                        "next_task_id": "TT-001",
                        "next_task": "First task",
                        "status": "ready",
                        "phase_started_at": "2026-03-11T00:00:00Z",
                        "phase_budget_minutes": 20,
                    }
                ),
                encoding="utf-8",
            )
            (runner_dir / "RUNNER_EXEC_CONTEXT.json").write_text(json.dumps({"phase": "discover"}), encoding="utf-8")
            tmux = _FakeTmux(outputs=["OpenAI Codex\n› Discover\n"], process_name="node")
            cfg = self._config(str(dev))

            run_runner_watchdog(
                cfg,
                tmux=tmux,
                sleep_fn=lambda _s: None,
                now_fn=lambda: 100.0,
                max_polls=1,
            )

            exec_context = json.loads((runner_dir / "RUNNER_EXEC_CONTEXT.json").read_text(encoding="utf-8"))
            self.assertEqual(exec_context["cycle_progress_baseline"]["next_task_id"], "TT-001")
            self.assertEqual(exec_context["progress_baseline"]["phase"], "discover")

    def test_cooldown_prevents_duplicate_idle_injection(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp)
            (dev / "Repos" / "blog" / ".memory" / "runner").mkdir(parents=True)
            tmux = _FakeTmux(outputs=["OpenAI Codex\n› one\n", "OpenAI Codex\n› one\n"], process_name="node")
            cfg = RunnerWatchdogConfig(**{**self._config(str(dev)).__dict__, "idle_cooldown_seconds": 10})

            now_ticks = iter([100.0, 101.0, 101.0])
            run_runner_watchdog(
                cfg,
                tmux=tmux,
                sleep_fn=lambda _s: None,
                now_fn=lambda: next(now_ticks, 101.0),
                max_polls=2,
            )

            sent = self._non_empty_send_texts(tmux)
            self.assertEqual(len(sent), 1)

    def test_state_transition_is_logged(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp)
            (dev / "Repos" / "blog" / ".memory" / "runner").mkdir(parents=True)
            tmux = _FakeTmux(outputs=["OpenAI Codex\n› Implement {feature}\n"], process_name="node")
            cfg = self._config(str(dev))

            run_runner_watchdog(
                cfg,
                tmux=tmux,
                sleep_fn=lambda _s: None,
                now_fn=lambda: 100.0,
                max_polls=1,
            )

            log_path = dev / ".codex" / "logs" / "runners" / "runner-blog.log"
            content = log_path.read_text()
            self.assertIn("watchdog state init -> idle", content)

    def test_unknown_with_explicit_prompt_uses_safety_injection(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp)
            (dev / "Repos" / "blog" / ".memory" / "runner").mkdir(parents=True)
            tmux = _FakeTmux(outputs=["OpenAI Codex\n› Implement {feature}\n"], process_name="python")
            cfg = self._config(str(dev))

            run_runner_watchdog(
                cfg,
                tmux=tmux,
                sleep_fn=lambda _s: None,
                now_fn=lambda: 100.0,
                max_polls=1,
            )

            sent = self._non_empty_send_texts(tmux)
            self.assertEqual(len(sent), 1)
            self.assertIn("/prompts:runner-discover", sent[0])
            self.assertIn("PROJECT=blog", sent[0])

    def test_phase_prompt_unknown_command_falls_back_to_run_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp)
            (dev / "Repos" / "blog" / ".memory" / "runner").mkdir(parents=True)
            tmux = _FakeTmux(
                outputs=[
                    "OpenAI Codex\n› Implement {feature}\n",
                    "OpenAI Codex\n› waiting\n",
                    "Unrecognized command '/prompts:runner-discover'. Type \"/\" for a list.\n› /prompts:runner-discover DEV=/tmp PROJECT=blog RUNNER_ID=main\n",
                ],
                process_name="node",
            )
            cfg = RunnerWatchdogConfig(**{**self._config(str(dev)).__dict__, "idle_cooldown_seconds": 1})

            now_ticks = iter([100.0, 102.0, 102.0])
            run_runner_watchdog(
                cfg,
                tmux=tmux,
                sleep_fn=lambda _s: None,
                now_fn=lambda: next(now_ticks, 102.0),
                max_polls=2,
            )

            sent = self._non_empty_send_texts(tmux)
            self.assertGreaterEqual(len(sent), 1)
            self.assertIn("/prompts:runner-discover", sent[0])
            self.assertIn("/prompts:run", sent[-1])
            self.assertIn("PHASE=discover", sent[-1])
            self.assertTrue(all("!python3" not in item for item in sent))

    def test_run_prompt_unknown_command_does_not_inject_shell_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp)
            (dev / "Repos" / "blog" / ".memory" / "runner").mkdir(parents=True)
            tmux = _FakeTmux(
                outputs=[
                    "OpenAI Codex\n› Implement {feature}\n",
                    "Unrecognized command '/prompts:runner-discover'. Type \"/\" for a list.\n› /prompts:runner-discover DEV=/tmp PROJECT=blog RUNNER_ID=main\n",
                    "OpenAI Codex\n› waiting\n",
                    "Unrecognized command '/prompts:run'. Type \"/\" for a list.\n› /prompts:run DEV=/tmp PROJECT=blog RUNNER_ID=main PHASE=discover\n",
                ],
                process_name="node",
            )
            cfg = RunnerWatchdogConfig(**{**self._config(str(dev)).__dict__, "idle_cooldown_seconds": 1})

            now_ticks = iter([100.0, 102.0, 104.0, 104.0])
            run_runner_watchdog(
                cfg,
                tmux=tmux,
                sleep_fn=lambda _s: None,
                now_fn=lambda: next(now_ticks, 104.0),
                max_polls=3,
            )

            sent = self._non_empty_send_texts(tmux)
            self.assertGreaterEqual(len(sent), 2)
            self.assertIn("/prompts:runner-discover", sent[0])
            self.assertTrue(all("/prompts:run" in item for item in sent[1:]))
            self.assertTrue(all("!python3" not in item for item in sent))

    def test_after_phase_prompt_failure_next_idle_retries_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp)
            (dev / "Repos" / "blog" / ".memory" / "runner").mkdir(parents=True)
            tmux = _FakeTmux(
                outputs=[
                    "OpenAI Codex\n› Implement {feature}\n",
                    "• Working (1s • esc to interrupt)\n",
                    "Unrecognized command '/prompts:runner-discover'. Type \"/\" for a list.\n› /prompts:runner-discover DEV=/tmp PROJECT=blog RUNNER_ID=main\n",
                    "• Working (1s • esc to interrupt)\n",
                    "• Working (1s • esc to interrupt)\n",
                    "OpenAI Codex\n› Implement {feature}\n",
                ],
                process_name="node",
            )
            cfg = RunnerWatchdogConfig(**{**self._config(str(dev)).__dict__, "idle_cooldown_seconds": 1})

            now_ticks = iter([100.0, 102.0, 103.0, 105.0, 105.0])
            run_runner_watchdog(
                cfg,
                tmux=tmux,
                sleep_fn=lambda _s: None,
                now_fn=lambda: next(now_ticks, 105.0),
                max_polls=4,
            )

            sent = self._non_empty_send_texts(tmux)
            self.assertGreaterEqual(len(sent), 2)
            self.assertIn("/prompts:runner-discover", sent[0])
            self.assertIn("/prompts:run", sent[-1])
            self.assertIn("PHASE=discover", sent[-1])
            self.assertTrue(all("!python3" not in item for item in sent))

    def test_send_with_enter_retry_when_command_still_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp)
            (dev / "Repos" / "blog" / ".memory" / "runner").mkdir(parents=True)
            tmux = _FakeTmux(
                outputs=[
                    "OpenAI Codex\n› Implement {feature}\n",
                    "OpenAI Codex\n› /prompts:runner-discover DEV=/tmp PROJECT=blog RUNNER_ID=main\n",
                ],
                process_name="node",
            )
            cfg = self._config(str(dev))

            run_runner_watchdog(
                cfg,
                tmux=tmux,
                sleep_fn=lambda _s: None,
                now_fn=lambda: 100.0,
                max_polls=1,
            )

            sent = self._non_empty_send_texts(tmux)
            self.assertEqual(len(sent), 1)
            self.assertIn("/prompts:runner-discover", sent[0])
            # Retry path sends at least one empty Enter submit.
            empty_enters = [call for call in tmux.send_keys_calls if call[1] == ""]
            self.assertGreaterEqual(len(empty_enters), 1)

    def test_stop_lock_triggers_interrupt_and_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp)
            runner_dir = dev / "Repos" / "blog" / ".memory" / "runner"
            runner_dir.mkdir(parents=True)
            stop_lock = dev / "Repos" / "blog" / ".memory" / "RUNNER_STOP.lock"
            stop_lock.write_text("stop\n")

            tmux = _FakeTmux(outputs=["❯\n"], process_name="codex")
            cfg = self._config(str(dev))

            run_runner_watchdog(
                cfg,
                tmux=tmux,
                sleep_fn=lambda _s: None,
                now_fn=lambda: 100.0,
                max_polls=1,
            )

            self.assertEqual(tmux.send_interrupt_calls, ["%1"])
            self.assertFalse(stop_lock.exists())

    def test_respawns_codex_when_process_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp)
            (dev / "Repos" / "blog" / ".memory" / "runner").mkdir(parents=True)
            tmux = _FakeTmux(outputs=[""], process_name="zsh")
            cfg = self._config(str(dev))

            run_runner_watchdog(
                cfg,
                tmux=tmux,
                sleep_fn=lambda _s: None,
                now_fn=lambda: 100.0,
                max_polls=1,
            )

            self.assertEqual(len(tmux.respawn_calls), 1)
            _, cmd, _ = tmux.respawn_calls[0]
            self.assertIn("cd ", cmd)
            self.assertIn("codex --search --dangerously-bypass-approvals-and-sandbox", cmd)

    def test_rotates_to_fresh_session_after_completed_iteration(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp)
            (dev / "Repos" / "blog" / ".memory" / "runner").mkdir(parents=True)
            tmux = _FakeTmux(
                outputs=[
                    "OpenAI Codex\n› Implement {feature}\n",
                    "OpenAI Codex\nSubmitting /prompts:run...\n",
                    "• Working (1m 00s • esc to interrupt)\n",
                    "OpenAI Codex\n› Run /review on my current changes\n",
                ],
                process_name="node",
            )
            cfg = self._config(str(dev))

            now_ticks = iter([100.0, 101.0, 110.0, 110.0])
            with mock.patch(
                "src.runner_watchdog._capture_cycle_progress_snapshot",
                side_effect=[
                    self._snapshot(task_id="TT-009", git_head="abc123", worktree_fingerprint="clean"),
                    self._snapshot(task_id="TT-010", git_head="abc123", worktree_fingerprint="clean"),
                ],
            ):
                run_runner_watchdog(
                    cfg,
                    tmux=tmux,
                    sleep_fn=lambda _s: None,
                    now_fn=lambda: next(now_ticks, 110.0),
                    max_polls=3,
                )

            sent = self._non_empty_send_texts(tmux)
            self.assertGreaterEqual(len(sent), 1)
            self.assertIn("/prompts:runner-discover", sent[0])
            self.assertEqual(len(tmux.respawn_calls), 1)
            self.assertEqual(tmux.send_interrupt_calls, ["%1"])

    def test_does_not_rotate_without_any_post_injection_work_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp)
            (dev / "Repos" / "blog" / ".memory" / "runner").mkdir(parents=True)
            tmux = _FakeTmux(
                outputs=[
                    "OpenAI Codex\n› Implement {feature}\n",
                    "OpenAI Codex\n› Implement {feature}\n",
                    "OpenAI Codex\n› Implement {feature}\n",
                    "OpenAI Codex\n› Implement {feature}\n",
                ],
                process_name="node",
            )
            cfg = RunnerWatchdogConfig(
                **{
                    **self._config(str(dev)).__dict__,
                    "idle_cooldown_seconds": 1,
                }
            )

            now_ticks = iter([100.0, 102.0, 102.0])
            run_runner_watchdog(
                cfg,
                tmux=tmux,
                sleep_fn=lambda _s: None,
                now_fn=lambda: next(now_ticks, 102.0),
                max_polls=2,
            )

            self.assertEqual(len(tmux.respawn_calls), 0)
            self.assertEqual(len(tmux.send_interrupt_calls), 0)

    def test_skips_setup_refresh_when_fresh_prepared_marker_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp)
            runner_dir = dev / "Repos" / "blog" / ".memory" / "runner"
            runner_dir.mkdir(parents=True)
            marker = runner_dir / "RUNNER_CYCLE_PREPARED.json"
            marker.write_text(
                json.dumps(
                    {
                        "prepared_at": "2026-03-04T17:00:00Z",
                        "project": "blog",
                        "runner_id": "main",
                        "git_worktree": str((dev / "Repos" / "blog").resolve()),
                        "next_task": "Example task",
                    }
                ),
                encoding="utf-8",
            )
            tmux = _FakeTmux(outputs=[""], process_name="zsh")
            cfg = RunnerWatchdogConfig(**{**self._config(str(dev)).__dict__, "project_root": str((dev / "Repos" / "blog").resolve())})

            with mock.patch("src.runner_watchdog.create_runner_state") as refresh_mock:
                run_runner_watchdog(
                    cfg,
                    tmux=tmux,
                    sleep_fn=lambda _s: None,
                    now_fn=lambda: 1_772_643_600.0,
                    max_polls=1,
                )
                refresh_mock.assert_not_called()
            self.assertFalse(marker.exists())
            self.assertEqual(len(tmux.respawn_calls), 1)

    def test_consumes_prepared_marker_even_if_respawn_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp)
            runner_dir = dev / "Repos" / "blog" / ".memory" / "runner"
            runner_dir.mkdir(parents=True)
            marker = runner_dir / "RUNNER_CYCLE_PREPARED.json"
            marker.write_text(
                json.dumps(
                    {
                        "prepared_at": "2026-03-04T17:00:00Z",
                        "project": "blog",
                        "runner_id": "main",
                        "git_worktree": str((dev / "Repos" / "blog").resolve()),
                        "next_task": "Example task",
                    }
                ),
                encoding="utf-8",
            )
            tmux = _FakeTmux(outputs=[""], process_name="zsh", respawn_ok=False)
            cfg = RunnerWatchdogConfig(
                **{
                    **self._config(str(dev)).__dict__,
                    "project_root": str((dev / "Repos" / "blog").resolve()),
                }
            )

            run_runner_watchdog(
                cfg,
                tmux=tmux,
                sleep_fn=lambda _s: None,
                now_fn=lambda: 1_772_643_600.0,
                max_polls=1,
            )

            self.assertFalse(marker.exists())

    def test_refreshes_setup_when_prepared_marker_missing_on_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp)
            (dev / "Repos" / "blog" / ".memory" / "runner").mkdir(parents=True)
            tmux = _FakeTmux(outputs=[""], process_name="zsh")
            cfg = self._config(str(dev))

            with mock.patch("src.runner_watchdog.create_runner_state") as refresh_mock:
                refresh_mock.return_value = {"ok": True}
                run_runner_watchdog(
                    cfg,
                    tmux=tmux,
                    sleep_fn=lambda _s: None,
                    now_fn=lambda: 100.0,
                    max_polls=1,
                )
                refresh_mock.assert_called_once()

    def test_throttles_repeated_setup_refresh_on_rapid_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp)
            (dev / "Repos" / "blog" / ".memory" / "runner").mkdir(parents=True)
            tmux = _FakeTmux(outputs=["", "", ""], process_name="zsh")
            cfg = self._config(str(dev))

            now_ticks = iter([100.0, 101.0, 102.0, 102.0])
            with mock.patch("src.runner_watchdog.create_runner_state") as refresh_mock:
                refresh_mock.return_value = {"ok": True}
                run_runner_watchdog(
                    cfg,
                    tmux=tmux,
                    sleep_fn=lambda _s: None,
                    now_fn=lambda: next(now_ticks, 102.0),
                    max_polls=3,
                )
                self.assertEqual(refresh_mock.call_count, 1)

    def test_forces_respawn_when_prepared_marker_stalls_without_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp)
            runner_dir = dev / "Repos" / "blog" / ".memory" / "runner"
            runner_dir.mkdir(parents=True)
            marker = runner_dir / "RUNNER_CYCLE_PREPARED.json"
            marker.write_text(
                json.dumps(
                    {
                        "prepared_at": "2026-03-04T17:00:00Z",
                        "project": "blog",
                        "runner_id": "main",
                        "git_worktree": str((dev / "Repos" / "blog").resolve()),
                        "next_task": "Example task",
                    }
                ),
                encoding="utf-8",
            )
            tmux = _FakeTmux(
                outputs=[
                    "OpenAI Codex\n› Implement {feature}\n",
                    "OpenAI Codex\n› /prompts:runner-discover DEV=/tmp PROJECT=blog RUNNER_ID=main\n",
                    "OpenAI Codex\n› waiting for wrapper\n",
                ],
                process_name="node",
            )
            cfg = RunnerWatchdogConfig(
                **{
                    **self._config(str(dev)).__dict__,
                    "idle_cooldown_seconds": 1,
                    "project_root": str((dev / "Repos" / "blog").resolve()),
                }
            )

            now_ticks = iter([1_772_643_600.0, 1_772_643_608.0, 1_772_643_608.0])
            with mock.patch(
                "src.runner_watchdog._capture_cycle_progress_snapshot",
                side_effect=[
                    self._snapshot(task_id="TT-009", git_head="abc123", worktree_fingerprint="clean"),
                    self._snapshot(task_id="TT-010", git_head="abc123", worktree_fingerprint="clean"),
                ],
            ):
                run_runner_watchdog(
                    cfg,
                    tmux=tmux,
                    sleep_fn=lambda _s: None,
                    now_fn=lambda: next(now_ticks, 1_772_643_608.0),
                    max_polls=2,
                )

            self.assertGreaterEqual(len(tmux.send_interrupt_calls), 1)
            self.assertEqual(len(tmux.respawn_calls), 1)
            self.assertFalse(marker.exists())

    def test_no_progress_prepared_marker_cycle_backs_off_without_respawn(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp)
            project_root = (dev / "Repos" / "blog").resolve()
            runner_dir = project_root / ".memory" / "runner"
            runner_dir.mkdir(parents=True)
            tmux = _FakeTmux(
                outputs=[
                    "OpenAI Codex\n› Implement {feature}\n",
                    "• Working (1s • esc to interrupt)\n",
                    "• Working (1s • esc to interrupt)\n",
                    "• Working (1s • esc to interrupt)\n",
                    "OpenAI Codex\n› Implement {feature}\n",
                ],
                process_name="node",
            )
            cfg = RunnerWatchdogConfig(
                **{
                    **self._config(str(dev)).__dict__,
                    "idle_cooldown_seconds": 1,
                    "project_root": str(project_root),
                }
            )
            marker_payload = {
                "prepared_at": "2026-03-04T17:00:00Z",
                "project": "blog",
                "runner_id": "main",
                "git_worktree": str(project_root),
                "next_task": "Example task",
            }
            stable_snapshot = self._snapshot()

            with (
                mock.patch(
                    "src.runner_watchdog._load_fresh_prepared_marker",
                    side_effect=[None, marker_payload, marker_payload, None],
                ),
                mock.patch(
                    "src.runner_watchdog._capture_cycle_progress_snapshot",
                    side_effect=[stable_snapshot, stable_snapshot, stable_snapshot],
                ),
            ):
                now_ticks = iter([100.0, 108.0, 115.0, 116.0])
                run_runner_watchdog(
                    cfg,
                    tmux=tmux,
                    sleep_fn=lambda _s: None,
                    now_fn=lambda: next(now_ticks, 116.0),
                    max_polls=4,
                )

            sent = self._non_empty_send_texts(tmux)
            self.assertEqual(len(sent), 1)
            self.assertIn("/prompts:runner-discover", sent[0])
            self.assertEqual(len(tmux.respawn_calls), 0)
            self.assertEqual(tmux.send_interrupt_calls, ["%1"])

            log_path = dev / ".codex" / "logs" / "runners" / "runner-blog.log"
            content = log_path.read_text(encoding="utf-8")
            self.assertIn(
                "watchdog suppressed fresh-session handoff because runner state/worktree did not advance",
                content,
            )

    def test_progressing_prepared_marker_cycle_still_respawns(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp)
            project_root = (dev / "Repos" / "blog").resolve()
            runner_dir = project_root / ".memory" / "runner"
            runner_dir.mkdir(parents=True)
            tmux = _FakeTmux(
                outputs=[
                    "OpenAI Codex\n› Implement {feature}\n",
                    "• Working (1s • esc to interrupt)\n",
                    "• Working (1s • esc to interrupt)\n",
                ],
                process_name="node",
            )
            cfg = RunnerWatchdogConfig(
                **{
                    **self._config(str(dev)).__dict__,
                    "idle_cooldown_seconds": 1,
                    "project_root": str(project_root),
                }
            )
            marker_payload = {
                "prepared_at": "2026-03-04T17:00:00Z",
                "project": "blog",
                "runner_id": "main",
                "git_worktree": str(project_root),
                "next_task": "Example task",
            }
            baseline_snapshot = self._snapshot(task_id="TT-009", git_head="abc123", worktree_fingerprint="clean")
            advanced_snapshot = self._snapshot(task_id="TT-010", git_head="abc123", worktree_fingerprint="clean")

            with (
                mock.patch(
                    "src.runner_watchdog._load_fresh_prepared_marker",
                    side_effect=[None, marker_payload, marker_payload],
                ),
                mock.patch(
                    "src.runner_watchdog._capture_cycle_progress_snapshot",
                    side_effect=[baseline_snapshot, advanced_snapshot],
                ),
            ):
                now_ticks = iter([100.0, 108.0, 115.0])
                run_runner_watchdog(
                    cfg,
                    tmux=tmux,
                    sleep_fn=lambda _s: None,
                    now_fn=lambda: next(now_ticks, 115.0),
                    max_polls=3,
                )

            self.assertEqual(len(tmux.respawn_calls), 1)

    def test_budget_expiry_rotates_without_prepared_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp)
            project_root = (dev / "Repos" / "blog").resolve()
            runner_dir = project_root / ".memory" / "runner"
            runner_dir.mkdir(parents=True)
            (runner_dir / "RUNNER_STATE.json").write_text(
                json.dumps(
                    {
                        "project": "blog",
                        "runner_id": "main",
                        "current_phase": "implement",
                        "phase_status": "active",
                        "phase_started_at": "1970-01-01T00:00:00Z",
                        "phase_budget_minutes": 20,
                        "next_task_id": "TT-009",
                        "next_task": "Long-running task",
                        "status": "ready",
                    }
                ),
                encoding="utf-8",
            )
            (runner_dir / "RUNNER_EXEC_CONTEXT.json").write_text(json.dumps({"phase": "implement"}), encoding="utf-8")
            tmux = _FakeTmux(outputs=["OpenAI Codex\n› waiting\n"], process_name="node")
            cfg = RunnerWatchdogConfig(
                **{
                    **self._config(str(dev)).__dict__,
                    "project_root": str(project_root),
                }
            )

            run_runner_watchdog(
                cfg,
                tmux=tmux,
                sleep_fn=lambda _s: None,
                now_fn=lambda: 2_000.0,
                max_polls=1,
            )

            self.assertEqual(len(tmux.respawn_calls), 1)
            state = json.loads((runner_dir / "RUNNER_STATE.json").read_text(encoding="utf-8"))
            self.assertNotEqual(state["phase_started_at"], "1970-01-01T00:00:00Z")
            log_path = dev / ".codex" / "logs" / "runners" / "runner-blog.log"
            content = log_path.read_text(encoding="utf-8")
            self.assertIn("phase budget expired", content)


if __name__ == "__main__":
    unittest.main()
