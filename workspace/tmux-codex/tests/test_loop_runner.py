import json
import subprocess
import sys
import tempfile
import unittest
from itertools import repeat
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.codex_engine import CodexRunResult
from src.codex_threads import archive_runner_threads_for_cwd, is_runner_thread
from src.main import parse_loop_args
from src.runner_loop import (
    _extract_open_runner_backlog,
    _build_prompt,
    _log_line,
    _parse_orx_runner_result,
    _runner_idle_grace_seconds,
    _submit_runner_prompt,
    build_runner_paths,
    detect_project_stack,
    ensure_gates_file,
    make_codex_exec_loop_script,
    resolve_active_seam_execution_profile,
    run_interactive_runner_controller,
    run_loop_runner,
)
from src.runner_state import build_runner_state_paths, build_runner_state_paths_for_root, default_runner_state, read_json, write_json


def _seed_orx_execution_packet(project_root: Path, *, project: str = "blog", issue_key: str = "PRO-9") -> dict[str, object]:
    packet = {
        "project_key": project,
        "project_display_name": project.title(),
        "packet_key": issue_key,
        "packet_scope": "single_leaf",
        "packet_revision": f"{issue_key.lower()}-packet-rev",
        "issue_key": issue_key,
        "issue_title": f"{issue_key} execution packet",
        "issue_url": f"https://linear.app/jkprojects/issue/{issue_key}",
        "repo_root": str(project_root),
        "worktree_path": str(project_root),
        "branch": f"linear/{issue_key.lower()}",
        "execution_model": "gpt-5.4",
        "execution_reasoning_effort": "medium",
        "active_slice_id": f"{issue_key.lower()}-slice-1",
    }
    write_json(
        project_root / ".memory" / "runner" / "KANBAN_STATE.json",
        {
            "project": project,
            "mode": "ticket_native",
            "version": 1,
            "phase": "executing",
            "active_issue": {
                "identifier": issue_key,
                "title": packet["issue_title"],
                "url": packet["issue_url"],
                "external_url": packet["issue_url"],
                "project_key": project,
                "project_name": project.title(),
                "worktree": str(project_root),
                "branch": packet["branch"],
                "execution_packet": packet,
            },
        },
    )
    return packet


class LoopArgsTests(unittest.TestCase):
    def test_parse_loop_defaults(self):
        parsed = parse_loop_args(["blog"])
        self.assertEqual(parsed["project"], "blog")
        self.assertEqual(parsed["complexity"], "med")
        self.assertEqual(parsed["model"], "gpt-5.3-codex")
        self.assertEqual(parsed["reasoning_effort"], "medium")
        self.assertEqual(parsed["runner_id"], "main")

    def test_parse_loop_complexity_high(self):
        parsed = parse_loop_args(["blog", "--complexity", "high"])
        self.assertEqual(parsed["model"], "gpt-5.3-codex")
        self.assertEqual(parsed["reasoning_effort"], "high")

    def test_parse_loop_complexity_xhigh(self):
        parsed = parse_loop_args(["blog", "--complexity", "xhigh"])
        self.assertEqual(parsed["model"], "gpt-5.3-codex")
        self.assertEqual(parsed["reasoning_effort"], "xhigh")

    def test_parse_loop_model_override(self):
        parsed = parse_loop_args(["blog", "--model", "openai/gpt-5.1-codex-max"])
        self.assertEqual(parsed["model"], "openai/gpt-5.1-codex-max")

    def test_parse_loop_runner_id_rejects_non_main(self):
        with self.assertRaises(ValueError):
            parse_loop_args(["blog", "--runner-id", "alpha"])

    def test_parse_loop_runner_id_main(self):
        parsed = parse_loop_args(["blog", "--runner-id", "main"])
        self.assertEqual(parsed["runner_id"], "main")

    def test_parse_loop_accepts_project_root_override(self):
        parsed = parse_loop_args(["blog", "--project-root", "/tmp/blog-worktree"])
        self.assertEqual(parsed["project_root"], "/tmp/blog-worktree")

    def test_parse_loop_invalid_complexity(self):
        with self.assertRaises(ValueError):
            parse_loop_args(["blog", "--complexity", "extreme"])

    def test_runner_idle_grace_disabled_for_zero_poll(self):
        self.assertEqual(_runner_idle_grace_seconds(1.0, 0), 0.0)

    def test_runner_idle_grace_enabled_for_real_poll(self):
        self.assertEqual(_runner_idle_grace_seconds(1.0, 0.75), 1.0)


class LoopScriptTests(unittest.TestCase):
    def test_runner_paths_are_runner_scoped(self):
        with patch("src.runner_loop.resolve_target_project_root", return_value=Path("/Users/jian/Dev/Repos/blog")):
            paths = build_runner_paths(
                dev="/Users/jian/Dev",
                project="blog",
                runner_id="main",
            )
        self.assertTrue(str(paths.complete_lock).endswith("/Repos/blog/.memory/runner/locks/RUNNER_DONE.lock"))
        self.assertTrue(str(paths.stop_file).endswith("/Repos/blog/.memory/runner/locks/RUNNER_STOP.lock"))
        self.assertTrue(str(paths.active_lock).endswith("/Repos/blog/.memory/runner/locks/RUNNER_ACTIVE.lock"))
        self.assertTrue(str(paths.state_file).endswith("/Repos/blog/.memory/runner/runtime/RUNNER_STATE.json"))
        self.assertTrue(str(paths.audit_file).endswith("/Repos/blog/.memory/runner/runtime/RUNNER_LEDGER.ndjson"))
        self.assertTrue(str(paths.runner_log).endswith("/.codex/logs/runners/runner-blog.log"))

    def test_exec_script_runs_interactive_controller(self):
        with patch("src.runner_loop.resolve_target_project_root", return_value=Path("/Users/jian/Dev/Repos/blog")):
            paths = build_runner_paths(
                dev="/Users/jian/Dev",
                project="blog",
                runner_id="main",
            )
        script = make_codex_exec_loop_script(
            dev="/Users/jian/Dev",
            project="blog",
            runner_id="main",
            model="gpt-5.1-codex",
            reasoning_effort="high",
            paths=paths,
        )

        self.assertIn('STOP_LOCK=', script)
        self.assertIn('DONE_LOCK=', script)
        self.assertIn('cd /Users/jian/Dev/workspace/tmux-codex', script)
        self.assertIn('PYTHONPATH=/Users/jian/Dev/workspace/tmux-codex${PYTHONPATH:+:$PYTHONPATH} python3 -m src.main __runner-controller', script)
        self.assertIn('while true; do', script)
        self.assertIn('codex --search --dangerously-bypass-approvals-and-sandbox', script)
        self.assertIn('cycle controller pid=', script)
        self.assertIn('cycle ended codex_rc=', script)
        self.assertIn('python3 -m src.main __runner-archive', script)
        self.assertIn('ARCHIVE_SUMMARY=', script)
        self.assertIn('exec zsh -l', script)
        self.assertIn('python3 -m src.main __runner-profile', script)
        self.assertIn('RUNNER_MODEL=gpt-5.1-codex', script)
        self.assertIn('RUNNER_REASONING_EFFORT=high', script)
        self.assertIn('-m "$RUNNER_MODEL"', script)
        self.assertIn('model_reasoning_effort="$RUNNER_REASONING_EFFORT"', script)
        self.assertNotIn('python3 -m src.main __runner-loop', script)

    def test_resolve_active_seam_execution_profile_prefers_exec_context_profile(self):
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
            write_json(paths.exec_context_json, {"task_id": "TT-101", "model_profile": "mini", "profile_reason": "bounded file family"})
            write_json(paths.state_file, {"next_task_id": "TT-101", "next_task": "Migrate runtime-context family"})

            profile = resolve_active_seam_execution_profile(
                paths=paths,
                fallback_model="gpt-5.3-codex",
                fallback_reasoning_effort="medium",
            )

            self.assertEqual(profile["model_profile"], "mini")
            self.assertEqual(profile["model"], "gpt-5.4-mini")
            self.assertEqual(profile["reasoning_effort"], "medium")
            self.assertEqual(profile["seam_id"], "TT-101")

    def test_resolve_active_seam_execution_profile_prefers_orx_execution_packet(self):
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
                    "active_issue": {
                        "identifier": "PRO-108",
                        "title": "Fix runner contamination",
                        "project_key": "blog",
                        "execution_packet": {
                            "active_slice_id": "slice-108",
                            "issue_key": "PRO-108",
                            "issue_title": "Fix runner contamination",
                            "execution_model": "gpt-5.4-mini",
                            "execution_reasoning_effort": "medium",
                        },
                    }
                },
            )
            write_json(paths.exec_context_json, {"task_id": "TT-101", "model_profile": "high"})

            profile = resolve_active_seam_execution_profile(
                paths=paths,
                fallback_model="gpt-5.4",
                fallback_reasoning_effort="high",
            )

            self.assertEqual(profile["source"], "orx_execution_packet")
            self.assertEqual(profile["model"], "gpt-5.4-mini")
            self.assertEqual(profile["reasoning_effort"], "medium")
            self.assertEqual(profile["seam_id"], "PRO-108")

    def test_parse_orx_runner_result_accepts_structured_result_block(self):
        payload, error = _parse_orx_runner_result(
            "\n".join(
                [
                    "working",
                    "RUNNER_RESULT_START",
                    json.dumps(
                        {
                            "status": "success",
                            "summary": "Implemented the slice.",
                            "verified": True,
                            "next_slice": None,
                            "artifacts": ["tests/test_loop_runner.py"],
                            "metrics": {"source": "test"},
                            "blockers": [],
                            "risks": [],
                            "lessons": ["Keep the runner packet authoritative."],
                            "verification_ran": ["pytest tests/test_loop_runner.py -q"],
                            "verification_failed": [],
                            "design_artifacts": [".codex/stitch/run-1/DESIGN.md"],
                            "design_reference": ".codex/stitch/run-1/DESIGN.md",
                            "design_review_requested": True,
                            "verification_surface": "playwright",
                            "touched_paths": ["src/runner_loop.py"],
                            "next_step_hint": "Continue with the next bounded slice only if ORX requests it.",
                            "owner_mismatch": False,
                            "scope_mismatch": False,
                            "needs_human_help": False,
                        }
                    ),
                    "RUNNER_RESULT_END",
                ]
            )
        )

        self.assertIsNone(error)
        assert payload is not None
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["artifacts"], ["tests/test_loop_runner.py"])
        self.assertEqual(payload["verification_surface"], "playwright")
        self.assertTrue(payload["design_review_requested"])

    def test_parse_orx_runner_result_prefers_last_marker_pair_with_tui_chrome(self):
        example_payload = json.dumps(
            {
                "status": "success",
                "summary": "Implemented the bounded slice and updated the affected files.",
                "verified": False,
                "next_slice": None,
                "artifacts": ["path/to/file"],
                "metrics": {},
                "blockers": [],
                "risks": [],
                "lessons": [],
                "verification_ran": [],
                "verification_failed": [],
                "touched_paths": ["path/to/file"],
                "next_step_hint": "",
                "owner_mismatch": False,
                "scope_mismatch": False,
                "needs_human_help": False,
            }
        )
        actual_payload = json.dumps(
            {
                "status": "success",
                "summary": "Confirmed package.json exists at the repo root.",
                "verified": True,
                "next_slice": None,
                "artifacts": [],
                "metrics": {"package_json_exists": True},
                "blockers": [],
                "risks": [],
                "lessons": [],
                "verification_ran": ["Checked package.json at the repo root."],
                "verification_failed": [],
                "touched_paths": [],
                "next_step_hint": "",
                "owner_mismatch": False,
                "scope_mismatch": False,
                "needs_human_help": False,
            }
        )
        payload, error = _parse_orx_runner_result(
            "\n".join(
                [
                    "Prompt example",
                    "RUNNER_RESULT_START",
                    example_payload,
                    "RUNNER_RESULT_END",
                    "",
                    "• RUNNER_RESULT_START",
                    actual_payload,
                    "RUNNER_RESULT_END › Run /review on my current changes",
                ]
            )
        )

        self.assertIsNone(error)
        assert payload is not None
        self.assertEqual(payload["summary"], "Confirmed package.json exists at the repo root.")
        self.assertEqual(payload["metrics"], {"package_json_exists": True})

    def test_parse_orx_runner_result_ignores_trailing_unpaired_start_marker(self):
        actual_payload = json.dumps(
            {
                "status": "success",
                "summary": "Recorded the disposable wrong-bot ingress proof.",
                "verified": True,
                "next_slice": None,
                "artifacts": [".codex/proofs/PRO-136-proof.md"],
                "metrics": {"proof_written": True},
                "blockers": [],
                "risks": [],
                "lessons": [],
                "verification_ran": ["git branch --show-current", "git rev-parse --show-toplevel"],
                "verification_failed": [],
                "touched_paths": [".codex/proofs/PRO-136-proof.md"],
                "next_step_hint": "",
                "owner_mismatch": False,
                "scope_mismatch": False,
                "needs_human_help": False,
            }
        )
        payload, error = _parse_orx_runner_result(
            "\n".join(
                [
                    "compact proof",
                    "RUNNER_RESULT_START",
                    actual_payload,
                    "RUNNER_RESULT_END",
                    "• RUNNER_RESULT_START › Implement {feature}",
                ]
            )
        )

        self.assertIsNone(error)
        assert payload is not None
        self.assertEqual(payload["summary"], "Recorded the disposable wrong-bot ingress proof.")
        self.assertEqual(payload["metrics"], {"proof_written": True})

    def test_parse_orx_runner_result_repairs_literal_control_chars_inside_strings(self):
        payload, error = _parse_orx_runner_result(
            "\n".join(
                [
                    "working",
                    "RUNNER_RESULT_START",
                    '{"status":"blocked","summary":"Hit a gap\nand stopped.","verified":false,"next_slice":"Retry after ORX fills the gap.","artifacts":[],"metrics":{},"blockers":["Missing context\tfrom ORX"],"risks":[],"lessons":[],"verification_ran":[],"verification_failed":[],"touched_paths":[],"next_step_hint":"","owner_mismatch":false,"scope_mismatch":false,"needs_human_help":true}',
                    "RUNNER_RESULT_END",
                ]
            )
        )

        self.assertIsNone(error)
        assert payload is not None
        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["summary"], "Hit a gap\nand stopped.")
        self.assertEqual(payload["blockers"], ["Missing context\tfrom ORX"])

    def test_parse_orx_runner_result_rejects_placeholder_summary(self):
        payload, error = _parse_orx_runner_result(
            "\n".join(
                [
                    "working",
                    "RUNNER_RESULT_START",
                    '{"status":"success","summary":"...","verified":false,"next_slice":null,"artifacts":[],"metrics":{},"blockers":[],"risks":[],"lessons":[],"verification_ran":[],"verification_failed":[],"touched_paths":[],"next_step_hint":"","owner_mismatch":false,"scope_mismatch":false,"needs_human_help":false}',
                    "RUNNER_RESULT_END",
                ]
            )
        )

        self.assertIsNone(payload)
        self.assertEqual(error, "runner result summary must not be a placeholder")

    def test_run_loop_runner_switches_model_between_iterations_from_active_task_profile(self):
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
            paths.gates_file.write_text("#!/usr/bin/env bash\nrun_gates(){ return 0; }\n", encoding="utf-8")
            state = default_runner_state("blog", "main")
            state["enabled"] = True
            state["next_task_id"] = "TT-101"
            state["next_task"] = "Runtime-context family"
            write_json(paths.state_file, state)
            write_json(
                paths.tasks_json,
                {
                    "objective_id": "OBJ-TEST",
                    "tasks": [
                        {"task_id": "TT-101", "title": "Runtime-context family", "status": "open", "model_profile": "mini"},
                        {"task_id": "TT-102", "title": "Core-store seam", "status": "open", "model_profile": "high"},
                    ],
                },
            )
            write_json(
                paths.exec_context_json,
                {
                    "task_id": "TT-101",
                    "task_title": "Runtime-context family",
                    "model_profile": "mini",
                    "profile_reason": "bounded file family",
                },
            )
            _seed_orx_execution_packet(project_root)

            calls: list[tuple[str, str]] = []

            def fake_run_codex_iteration(**kwargs):
                calls.append((kwargs["model"], kwargs["reasoning_effort"]))
                if len(calls) == 1:
                    write_json(
                        paths.exec_context_json,
                        {
                            "task_id": "TT-102",
                            "task_title": "Core-store seam",
                            "model_profile": "high",
                            "profile_reason": "cross-subsystem seam work",
                        },
                    )
                else:
                    paths.stop_lock.write_text("stop\n", encoding="utf-8")
                return CodexRunResult(
                    exit_code=0,
                    final_message="completed slice",
                    session_id="session-1",
                    events=[],
                    raw_lines=[],
                )

            fake_update = (
                {
                    "summary": "completed slice",
                    "completed": [],
                    "completed_task_ids": [],
                    "next_task": "Continue",
                    "next_task_reason": "Still active",
                    "blockers": [],
                    "remaining_gaps": [],
                    "done_candidate": False,
                },
                "runner_update",
                None,
                None,
            )

            with (
                patch("src.runner_loop.resolve_target_project_root", return_value=project_root),
                patch("src.runner_loop.create_runner_state", return_value={"ok": True}),
                patch("src.runner_loop.ensure_gates_file", return_value=(paths.gates_file, False)),
                patch("src.runner_loop._validate_gates_contract", return_value=(True, "")),
                patch("src.runner_loop.run_codex_iteration", side_effect=fake_run_codex_iteration),
                patch("src.runner_loop._resolve_iteration_update", return_value=fake_update),
                patch("src.runner_loop.time.sleep", return_value=None),
            ):
                rc = run_loop_runner(
                    dev=str(dev),
                    project="blog",
                    runner_id="main",
                    model="gpt-5.3-codex",
                    reasoning_effort="medium",
                    session_name="runner-blog",
                    backoff_seconds=0,
                )

            self.assertEqual(rc, 0)
            self.assertEqual(calls, [("gpt-5.4", "medium"), ("gpt-5.4", "medium")])

    def test_interactive_runner_controller_writes_runner_status_snapshot_when_session_missing(self):
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
            state = default_runner_state("blog", "main")
            state["project_root"] = str(project_root)
            write_json(paths.state_file, state)

            tmux_instance = unittest.mock.Mock()
            tmux_instance.has_session.return_value = False

            with (
                patch("src.runner_loop.resolve_target_project_root", return_value=project_root),
                patch("src.runner_loop.TmuxClient", return_value=tmux_instance),
            ):
                rc = run_interactive_runner_controller(
                    [
                        "--project",
                        "blog",
                        "--runner-id",
                        "main",
                        "--session-name",
                        "runner-blog",
                        "--dev",
                        str(dev),
                        "--poll-seconds",
                        "0",
                    ]
                )

            self.assertEqual(rc, 0)

    def test_interactive_runner_controller_submits_orx_slice_result_and_restarts_fresh(self):
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
            write_json(paths.state_file, default_runner_state("blog", "main"))
            write_json(
                paths.kanban_state_json,
                {
                    "active_issue": {
                        "identifier": "PRO-108",
                        "title": "Fix runner contamination",
                        "project_key": "blog",
                        "execution_packet": {
                            "active_slice_id": "slice-108",
                            "issue_key": "PRO-108",
                            "issue_title": "Fix runner contamination",
                            "execution_model": "gpt-5.4-mini",
                            "execution_reasoning_effort": "medium",
                        },
                    }
                },
            )
            result_output = "\n".join(
                [
                    "working",
                    "RUNNER_RESULT_START",
                    json.dumps(
                        {
                            "status": "success",
                            "summary": "Implemented the ORX-managed slice.",
                            "verified": True,
                            "next_slice": None,
                            "artifacts": ["src/runner_loop.py"],
                            "metrics": {"source": "test"},
                            "blockers": [],
                            "risks": [],
                            "lessons": [],
                            "verification_ran": ["pytest tests/test_loop_runner.py -q"],
                            "verification_failed": [],
                            "touched_paths": ["src/runner_loop.py"],
                            "next_step_hint": "",
                            "owner_mismatch": False,
                            "scope_mismatch": False,
                            "needs_human_help": False,
                        }
                    ),
                    "RUNNER_RESULT_END",
                ]
            )
            tmux = SimpleNamespace(
                has_session=lambda _name: True,
                capture_pane=MagicMock(side_effect=["", result_output]),
                get_pane_process=MagicMock(return_value="codex"),
                send_eof=MagicMock(return_value=True),
            )

            with (
                patch("src.runner_loop.resolve_target_project_root", return_value=project_root),
                patch("src.runner_loop.TmuxClient", return_value=tmux),
                patch(
                    "src.runner_loop._orx_execution_packet",
                    return_value={
                        "active_slice_id": "slice-108",
                        "issue_key": "PRO-108",
                        "issue_title": "Fix runner contamination",
                        "packet_key": "packet-108",
                        "packet_revision": "packet-rev-108",
                        "latest_handoff_revision": "handoff-rev-108",
                        "continuity_revision": "continuity-rev-108",
                        "decision_epoch": "slice-108",
                        "execution_model": "gpt-5.4-mini",
                        "execution_reasoning_effort": "medium",
                    },
                ),
                patch("src.runner_loop.detect_runner_state", side_effect=["idle", "idle"]),
                patch("src.runner_loop._submit_runner_prompt", return_value=(True, None)),
                patch("src.runner_loop._has_completion_directive_for_step", return_value=True),
                patch("src.runner_loop.submit_slice_result", return_value={"ok": True}) as submit_result,
                patch("src.runner_loop.time.sleep", return_value=None),
            ):
                rc = run_interactive_runner_controller(
                    [
                        "--project",
                        "blog",
                        "--runner-id",
                        "main",
                        "--session-name",
                        "runner-blog",
                        "--dev",
                        str(dev),
                        "--poll-seconds",
                        "0",
                    ]
                )

            self.assertEqual(rc, 0)
            submit_result.assert_called_once()
            self.assertEqual(submit_result.call_args.kwargs["project_key"], "blog")
            self.assertEqual(submit_result.call_args.kwargs["slice_id"], "slice-108")
            submitted_payload = submit_result.call_args.kwargs["payload"]
            self.assertEqual(submitted_payload["issue_key"], "PRO-108")
            self.assertEqual(submitted_payload["packet_key"], "packet-108")
            self.assertEqual(submitted_payload["packet_revision"], "packet-rev-108")
            self.assertEqual(submitted_payload["latest_handoff_revision"], "handoff-rev-108")
            self.assertEqual(submitted_payload["continuity_revision"], "continuity-rev-108")
            self.assertEqual(submitted_payload["decision_epoch"], "slice-108")
            runner_status = read_json(paths.runner_status_json)
            self.assertEqual(runner_status.get("project"), "blog")
            self.assertEqual(runner_status.get("status"), "running")

    def test_archive_runner_threads_for_cwd_archives_only_runner_threads(self):
        threads = [
            {
                "id": "runner-new",
                "cwd": "/tmp/blog",
                "preview": "Use this command to execute exactly one medium bounded infinite-runner work slice.",
                "updatedAt": 20,
                "createdAt": 10,
            },
            {
                "id": "runner-old",
                "cwd": "/tmp/blog",
                "preview": "Use this command to govern infinite-runner state after one execute slice finishes.",
                "updatedAt": 10,
                "createdAt": 9,
            },
            {
                "id": "manual-thread",
                "cwd": "/tmp/blog",
                "preview": "Investigate prod bug",
                "updatedAt": 30,
                "createdAt": 30,
            },
        ]

        archived_ids: list[str] = []

        with (
            patch("src.codex_threads.list_threads_for_cwd", return_value=threads),
            patch("src.codex_threads.archive_thread", side_effect=lambda thread_id: archived_ids.append(thread_id)),
        ):
            summary = archive_runner_threads_for_cwd(cwd=Path("/tmp/blog"), keep=1)

        self.assertTrue(is_runner_thread(threads[0]))
        self.assertTrue(is_runner_thread(threads[1]))
        self.assertFalse(is_runner_thread(threads[2]))
        self.assertEqual(summary["matched"], 2)
        self.assertEqual(summary["kept"], 1)
        self.assertEqual(summary["archived"], 1)
        self.assertEqual(archived_ids, ["runner-old"])

    def test_interactive_runner_controller_submits_runner_event_when_orx_result_block_missing(self):
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
            write_json(paths.state_file, default_runner_state("blog", "main"))
            write_json(
                paths.exec_context_json,
                {
                    "phase": "discover",
                    "orx_execution_packet": {
                        "project_key": "blog",
                        "issue_key": "PRO-109",
                        "issue_title": "Missing result block",
                        "execution_model": "gpt-5.4-mini",
                        "execution_reasoning_effort": "medium",
                    },
                },
            )
            invalid_output = "\n".join(["working", "no structured result here", "OpenAI Codex"])
            tmux = SimpleNamespace(
                has_session=lambda _name: True,
                capture_pane=MagicMock(side_effect=["", invalid_output]),
                get_pane_process=MagicMock(return_value="codex"),
                send_eof=MagicMock(return_value=True),
            )

            with (
                patch("src.runner_loop.resolve_target_project_root", return_value=project_root),
                patch("src.runner_loop.TmuxClient", return_value=tmux),
                patch(
                    "src.runner_loop._orx_execution_packet",
                    return_value={
                        "active_slice_id": "slice-109",
                        "issue_key": "PRO-109",
                        "issue_title": "Missing result block",
                        "packet_key": "packet-109",
                        "packet_revision": "packet-rev-109",
                        "latest_handoff_revision": "handoff-rev-109",
                        "continuity_revision": "continuity-rev-109",
                        "decision_epoch": "slice-109",
                        "execution_model": "gpt-5.4-mini",
                        "execution_reasoning_effort": "medium",
                    },
                ),
                patch("src.runner_loop.detect_runner_state", side_effect=["idle", "idle"]),
                patch("src.runner_loop._submit_runner_prompt", return_value=(True, None)),
                patch("src.runner_loop._has_completion_directive_for_step", return_value=True),
                patch("src.runner_loop.submit_runner_event", return_value={"ok": True}) as submit_event,
                patch("src.runner_loop.time.sleep", return_value=None),
            ):
                rc = run_interactive_runner_controller(
                    [
                        "--project",
                        "blog",
                        "--runner-id",
                        "main",
                        "--session-name",
                        "runner-blog",
                        "--dev",
                        str(dev),
                        "--poll-seconds",
                        "0",
                    ]
                )

            self.assertEqual(rc, 1)
            submit_event.assert_called_once()
            self.assertEqual(submit_event.call_args.kwargs["project_key"], "blog")
            self.assertEqual(submit_event.call_args.kwargs["event_kind"], "result_missing")
            self.assertEqual(submit_event.call_args.kwargs["issue_key"], "PRO-109")
            self.assertIn("missing RUNNER_RESULT", submit_event.call_args.kwargs["reason"])

    def test_module_entrypoint_executes_main(self):
        result = subprocess.run(
            [sys.executable, "-m", "src.main", "--help"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0)
        self.assertIn("Usage:", result.stdout)
        self.assertIn("cl loop <project>", result.stdout)

    def test_interactive_runner_controller_runs_scripted_refresh_after_execute_when_no_update_is_needed(self):
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
            write_json(paths.state_file, default_runner_state("blog", "main"))
            write_json(paths.exec_context_json, {"phase": "implement"})

            tmux_instance = unittest.mock.Mock()
            tmux_instance.has_session.side_effect = repeat(True)
            tmux_instance.get_pane_process.side_effect = repeat("node")
            tmux_instance.send_eof.return_value = True

            def fake_refresh(**_kwargs):
                refreshed_state = default_runner_state("blog", "main")
                refreshed_state.update(
                    {
                        "status": "ready",
                        "current_step": "",
                        "current_phase": "verify",
                        "phase_status": "active",
                        "next_task_id": "TT-002",
                        "next_task": "Resolve final geometry threshold drift.",
                        "next_task_reason": "Deterministic selection after scripted refresh.",
                    }
                )
                write_json(paths.state_file, refreshed_state)
                return True, None

            with (
                patch("src.runner_loop.resolve_target_project_root", return_value=project_root),
                patch("src.runner_loop.TmuxClient", return_value=tmux_instance),
                patch("src.runner_loop._submit_runner_prompt", return_value=(True, None)) as submit_prompt,
                patch("src.runner_loop._run_scripted_cycle_refresh", side_effect=fake_refresh),
                patch("src.runner_loop.time.sleep", return_value=None),
            ):
                tmux_instance.capture_pane.side_effect = [
                    "OpenAI Codex\n› Run /review on my current changes\n",
                    "Running execute...\n",
                    "OpenAI Codex\n› \nphase_done=yes\nvalidation=pass\nneeds_update=no\nexiting=yes\n",
                ]

                rc = run_interactive_runner_controller(
                    [
                        "--project",
                        "blog",
                        "--runner-id",
                        "main",
                        "--session-name",
                        "runner-blog",
                        "--dev",
                        str(dev),
                        "--poll-seconds",
                        "0",
                    ]
                )

            self.assertEqual(rc, 0)
            self.assertEqual(submit_prompt.call_count, 1)
            first_command = submit_prompt.call_args.kwargs["command"]
            self.assertTrue(first_command.startswith("RUNNER_DISPATCH mode=run_execute"))
            self.assertIn("PHASE=implement", first_command)
            self.assertTrue(tmux_instance.send_eof.called)
            state = read_json(paths.state_file)
            self.assertEqual(state.get("status"), "ready")
            self.assertEqual(state.get("current_step"), "")
            self.assertEqual(state.get("next_task_id"), "TT-002")
            self.assertEqual(state.get("next_task"), "Resolve final geometry threshold drift.")
            ledger_events = [
                json.loads(line)["event"]
                for line in paths.ledger_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertIn("runner.execute_complete", ledger_events)
            self.assertIn("runner.scripted_refresh_complete", ledger_events)
            self.assertIn("runner.chat_exit_requested", ledger_events)
            self.assertNotIn("runner.update_dispatch_start", ledger_events)
            log_text = paths.runner_log.read_text(encoding="utf-8")
            self.assertIn("Scripted refresh completed", log_text)
            self.assertIn("next loop will relaunch fresh", log_text)

    def test_interactive_runner_controller_continues_same_session_update_when_scripted_refresh_fails(self):
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
            write_json(paths.state_file, default_runner_state("blog", "main"))
            write_json(paths.exec_context_json, {"phase": "implement"})

            tmux_instance = unittest.mock.Mock()
            tmux_instance.has_session.side_effect = repeat(True)
            tmux_instance.get_pane_process.side_effect = repeat("node")
            tmux_instance.send_eof.return_value = True

            def fake_refresh(**_kwargs):
                call_index = fake_refresh.call_count
                fake_refresh.call_count += 1
                if call_index == 0:
                    return False, "prepare_failed:no durable progress"
                refreshed_state = default_runner_state("blog", "main")
                refreshed_state.update(
                    {
                        "status": "ready",
                        "current_step": "",
                        "current_phase": "implement",
                        "phase_status": "active",
                        "next_task_id": "TT-001",
                        "next_task": "Keep tightening archive ownership seams.",
                    }
                )
                write_json(paths.state_file, refreshed_state)
                return True, None

            fake_refresh.call_count = 0

            with (
                patch("src.runner_loop.resolve_target_project_root", return_value=project_root),
                patch("src.runner_loop.TmuxClient", return_value=tmux_instance),
                patch("src.runner_loop._submit_runner_prompt", return_value=(True, None)) as submit_prompt,
                patch(
                    "src.runner_loop._run_scripted_cycle_refresh",
                    side_effect=fake_refresh,
                ),
                patch("src.runner_loop.time.sleep", return_value=None),
            ):
                tmux_instance.capture_pane.side_effect = [
                    "OpenAI Codex\n› Run /review on my current changes\n",
                    "Running execute...\n",
                    "OpenAI Codex\n› \nphase_done=no\nvalidation=pass\nneeds_update=no\nexiting=yes\n",
                    "OpenAI Codex\n› \nphase_done=no\nvalidation=pass\nneeds_update=no\nexiting=yes\n",
                    "Running update...\n",
                    "OpenAI Codex\n› \nstate_repaired=yes\nscope_status=narrow\nexiting=yes\n",
                ]

                rc = run_interactive_runner_controller(
                    [
                        "--project",
                        "blog",
                        "--runner-id",
                        "main",
                        "--session-name",
                        "runner-blog",
                        "--dev",
                        str(dev),
                        "--poll-seconds",
                        "0",
                    ]
                )

            self.assertEqual(rc, 0)
            self.assertEqual(submit_prompt.call_count, 2)
            self.assertIn("RUNNER_DISPATCH mode=run_execute", submit_prompt.call_args_list[0].kwargs["command"])
            self.assertIn("RUNNER_DISPATCH mode=run_govern", submit_prompt.call_args_list[1].kwargs["command"])
            self.assertTrue(tmux_instance.send_eof.called)
            state = read_json(paths.state_file)
            self.assertEqual(state.get("status"), "ready")
            self.assertEqual(state.get("current_step"), "")
            ledger_events = [
                json.loads(line)
                for line in paths.ledger_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertTrue(any(entry.get("event") == "runner.execute_complete" for entry in ledger_events))
            self.assertTrue(any(entry.get("event") == "runner.scripted_refresh_failed" for entry in ledger_events))
            self.assertTrue(any(entry.get("event") == "runner.update_requested_same_session" and entry.get("source_step") == "execute" for entry in ledger_events))
            self.assertTrue(any(entry.get("event") == "runner.update_dispatch_start" for entry in ledger_events))
            self.assertTrue(any(entry.get("event") == "runner.update_complete" for entry in ledger_events))

    def test_interactive_runner_controller_defaults_semantic_update_to_mini(self):
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
            write_json(paths.state_file, default_runner_state("blog", "main"))
            write_json(paths.exec_context_json, {"phase": "implement"})

            tmux_instance = unittest.mock.Mock()
            tmux_instance.has_session.side_effect = repeat(True)
            tmux_instance.get_pane_process.side_effect = repeat("node")
            tmux_instance.send_eof.return_value = True

            def fake_refresh(**_kwargs):
                refreshed_state = default_runner_state("blog", "main")
                refreshed_state.update(
                    {
                        "status": "ready",
                        "current_step": "",
                        "current_phase": "implement",
                        "phase_status": "active",
                        "next_task_id": "TT-001",
                        "next_task": "Keep tightening archive ownership seams.",
                    }
                )
                write_json(paths.state_file, refreshed_state)
                return True, None

            with (
                patch("src.runner_loop.resolve_target_project_root", return_value=project_root),
                patch("src.runner_loop.TmuxClient", return_value=tmux_instance),
                patch("src.runner_loop._submit_runner_prompt", return_value=(True, None)) as submit_prompt,
                patch("src.runner_loop._run_scripted_cycle_refresh", side_effect=fake_refresh) as scripted_refresh,
                patch("src.runner_loop.time.sleep", return_value=None),
            ):
                tmux_instance.capture_pane.side_effect = [
                    "OpenAI Codex\n› Run /review on my current changes\n",
                    "Running execute...\n",
                    "OpenAI Codex\n› \nphase_done=no\nvalidation=pass\nneeds_update=yes\nexiting=yes\n",
                    "OpenAI Codex\n› \nphase_done=no\nvalidation=pass\nneeds_update=yes\nexiting=yes\n",
                    "Running update...\n",
                    "OpenAI Codex\n› \nstate_repaired=yes\nscope_status=narrow\nexiting=yes\n",
                ]

                rc = run_interactive_runner_controller(
                    [
                        "--project",
                        "blog",
                        "--runner-id",
                        "main",
                        "--session-name",
                        "runner-blog",
                        "--dev",
                        str(dev),
                        "--poll-seconds",
                        "0",
                    ]
                )

            self.assertEqual(rc, 0)
            self.assertEqual(submit_prompt.call_count, 2)
            self.assertIn("RUNNER_DISPATCH mode=run_execute", submit_prompt.call_args_list[0].kwargs["command"])
            self.assertIn("RUNNER_DISPATCH mode=run_govern", submit_prompt.call_args_list[1].kwargs["command"])
            self.assertTrue(tmux_instance.send_eof.called)
            scripted_refresh.assert_called_once()
            state = read_json(paths.state_file)
            self.assertEqual(state.get("status"), "ready")
            self.assertEqual(state.get("current_step"), "")
            ledger_events = [
                json.loads(line)
                for line in paths.ledger_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertTrue(any(entry.get("event") == "runner.update_requested" and entry.get("update_profile") == "mini" for entry in ledger_events))
            self.assertTrue(any(entry.get("event") == "runner.update_requested_same_session" and entry.get("update_profile") == "mini" for entry in ledger_events))
            self.assertTrue(any(entry.get("event") == "runner.update_dispatch_start" for entry in ledger_events))
            self.assertTrue(any(entry.get("event") == "runner.chat_exit_requested" for entry in ledger_events))

    def test_interactive_runner_controller_forces_update_when_closeout_reports_phase_incomplete(self):
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
            state = default_runner_state("blog", "main")
            state["current_phase"] = "closeout"
            write_json(paths.state_file, state)
            write_json(paths.exec_context_json, {"phase": "closeout", "model_profile": "mini"})

            tmux_instance = unittest.mock.Mock()
            tmux_instance.has_session.side_effect = repeat(True)
            tmux_instance.get_pane_process.side_effect = repeat("node")
            tmux_instance.send_eof.return_value = True

            def fake_refresh(**_kwargs):
                refreshed_state = default_runner_state("blog", "main")
                refreshed_state.update(
                    {
                        "status": "ready",
                        "current_step": "",
                        "current_phase": "closeout",
                        "phase_status": "active",
                        "next_task_id": "TT-099",
                        "next_task": "Resolve final DMG closeout blocker.",
                        "done_gate_status": "pending",
                        "done_candidate": False,
                    }
                )
                write_json(paths.state_file, refreshed_state)
                return True, None

            with (
                patch("src.runner_loop.resolve_target_project_root", return_value=project_root),
                patch("src.runner_loop.TmuxClient", return_value=tmux_instance),
                patch("src.runner_loop._submit_runner_prompt", return_value=(True, None)) as submit_prompt,
                patch("src.runner_loop._run_scripted_cycle_refresh", side_effect=fake_refresh) as scripted_refresh,
                patch("src.runner_loop.time.sleep", return_value=None),
            ):
                tmux_instance.capture_pane.side_effect = [
                    "OpenAI Codex\n› Run /review on my current changes\n",
                    "Running execute...\n",
                    "OpenAI Codex\n› \nphase_done=no\nvalidation=pass\nneeds_update=no\nscope_status=ok\nexiting=yes\n",
                    "OpenAI Codex\n› \nphase_done=no\nvalidation=pass\nneeds_update=no\nscope_status=ok\nexiting=yes\n",
                    "Running update...\n",
                    "OpenAI Codex\n› \nstate_repaired=yes\nscope_status=narrow\nexiting=yes\n",
                ]

                rc = run_interactive_runner_controller(
                    [
                        "--project",
                        "blog",
                        "--runner-id",
                        "main",
                        "--session-name",
                        "runner-blog",
                        "--dev",
                        str(dev),
                        "--poll-seconds",
                        "0",
                    ]
                )

            self.assertEqual(rc, 0)
            self.assertEqual(submit_prompt.call_count, 2)
            self.assertIn("RUNNER_DISPATCH mode=run_execute", submit_prompt.call_args_list[0].kwargs["command"])
            self.assertIn("RUNNER_DISPATCH mode=run_govern", submit_prompt.call_args_list[1].kwargs["command"])
            scripted_refresh.assert_called_once()
            ledger_events = [
                json.loads(line)
                for line in paths.ledger_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertTrue(any(entry.get("event") == "runner.update_requested" and entry.get("update_reason") == "closeout_phase_incomplete" for entry in ledger_events))
            self.assertTrue(any(entry.get("event") == "runner.update_requested_same_session" and entry.get("reason") == "closeout_phase_incomplete" for entry in ledger_events))
            state = read_json(paths.state_file)
            self.assertEqual(state.get("status"), "ready")
            self.assertEqual(state.get("next_task_id"), "TT-099")

    def test_interactive_runner_controller_uses_high_only_when_execute_requests_high_update(self):
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
            write_json(paths.state_file, default_runner_state("blog", "main"))
            write_json(paths.exec_context_json, {"phase": "implement"})

            tmux_instance = unittest.mock.Mock()
            tmux_instance.has_session.side_effect = repeat(True)
            tmux_instance.get_pane_process.side_effect = repeat("node")
            tmux_instance.send_eof.return_value = True

            def fake_refresh(**_kwargs):
                refreshed_state = default_runner_state("blog", "main")
                refreshed_state.update(
                    {
                        "status": "ready",
                        "current_step": "",
                        "current_phase": "implement",
                        "phase_status": "active",
                        "next_task_id": "TT-001",
                        "next_task": "Keep tightening archive ownership seams.",
                    }
                )
                write_json(paths.state_file, refreshed_state)
                return True, None

            with (
                patch("src.runner_loop.resolve_target_project_root", return_value=project_root),
                patch("src.runner_loop.TmuxClient", return_value=tmux_instance),
                patch("src.runner_loop._submit_runner_prompt", return_value=(True, None)) as submit_prompt,
                patch("src.runner_loop._run_scripted_cycle_refresh", side_effect=fake_refresh) as scripted_refresh,
                patch("src.runner_loop.time.sleep", return_value=None),
            ):
                tmux_instance.capture_pane.side_effect = [
                    "OpenAI Codex\n› Run /review on my current changes\n",
                    "Running execute...\n",
                    "OpenAI Codex\n› \nphase_done=no\nvalidation=pass\nneeds_update=yes\nupdate_profile=high\nexiting=yes\n",
                    "OpenAI Codex\n› \nphase_done=no\nvalidation=pass\nneeds_update=yes\nupdate_profile=high\nexiting=yes\n",
                    "Running update...\n",
                    "OpenAI Codex\n› \nstate_repaired=yes\nscope_status=reseed\nexiting=yes\n",
                ]

                rc = run_interactive_runner_controller(
                    [
                        "--project",
                        "blog",
                        "--runner-id",
                        "main",
                        "--session-name",
                        "runner-blog",
                        "--dev",
                        str(dev),
                        "--poll-seconds",
                        "0",
                    ]
                )

            self.assertEqual(rc, 0)
            self.assertEqual(submit_prompt.call_count, 2)
            self.assertIn("RUNNER_DISPATCH mode=run_execute", submit_prompt.call_args_list[0].kwargs["command"])
            self.assertIn("RUNNER_DISPATCH mode=run_govern", submit_prompt.call_args_list[1].kwargs["command"])
            self.assertTrue(tmux_instance.send_eof.called)
            scripted_refresh.assert_called_once()
            state = read_json(paths.state_file)
            self.assertEqual(state.get("status"), "ready")
            self.assertEqual(state.get("current_step"), "")
            ledger_entries = [
                json.loads(line)
                for line in paths.ledger_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertTrue(any(entry.get("event") == "runner.update_requested" and entry.get("update_profile") == "high" for entry in ledger_entries))
            self.assertTrue(any(entry.get("event") == "runner.update_requested_same_session" and entry.get("update_profile") == "high" for entry in ledger_entries))

    def test_interactive_runner_controller_relaunches_for_high_update_when_current_session_is_mini(self):
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
            write_json(paths.state_file, default_runner_state("blog", "main"))
            write_json(paths.exec_context_json, {"phase": "implement", "model_profile": "mini"})

            tmux_instance = unittest.mock.Mock()
            tmux_instance.has_session.side_effect = repeat(True)
            tmux_instance.get_pane_process.side_effect = repeat("node")
            tmux_instance.send_eof.return_value = True

            with (
                patch("src.runner_loop.resolve_target_project_root", return_value=project_root),
                patch("src.runner_loop.TmuxClient", return_value=tmux_instance),
                patch("src.runner_loop._submit_runner_prompt", return_value=(True, None)) as submit_prompt,
                patch("src.runner_loop._run_scripted_cycle_refresh") as scripted_refresh,
                patch("src.runner_loop.time.sleep", return_value=None),
            ):
                tmux_instance.capture_pane.side_effect = [
                    "OpenAI Codex\n› Run /review on my current changes\n",
                    "Running execute...\n",
                    "OpenAI Codex\n› \nphase_done=no\nvalidation=pass\nneeds_update=yes\nupdate_profile=high\nexiting=yes\n",
                ]

                rc = run_interactive_runner_controller(
                    [
                        "--project",
                        "blog",
                        "--runner-id",
                        "main",
                        "--session-name",
                        "runner-blog",
                        "--dev",
                        str(dev),
                        "--poll-seconds",
                        "0",
                    ]
                )

            self.assertEqual(rc, 0)
            self.assertEqual(submit_prompt.call_count, 1)
            self.assertIn("RUNNER_DISPATCH mode=run_execute", submit_prompt.call_args.kwargs["command"])
            self.assertTrue(tmux_instance.send_eof.called)
            scripted_refresh.assert_not_called()
            state = read_json(paths.state_file)
            self.assertEqual(state.get("current_step"), "update_pending:high")
            ledger_entries = [
                json.loads(line)
                for line in paths.ledger_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertTrue(any(entry.get("event") == "runner.update_requested" and entry.get("update_profile") == "high" for entry in ledger_entries))
            self.assertFalse(any(entry.get("event") == "runner.update_requested_same_session" for entry in ledger_entries))
            self.assertTrue(any(entry.get("event") == "runner.chat_exit_requested" for entry in ledger_entries))

    def test_interactive_runner_controller_dispatches_pending_update_in_fresh_session(self):
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
            state = default_runner_state("blog", "main")
            state["current_step"] = "update_pending:mini"
            write_json(paths.state_file, state)
            write_json(paths.exec_context_json, {"phase": "implement"})

            tmux_instance = unittest.mock.Mock()
            tmux_instance.has_session.side_effect = repeat(True)
            tmux_instance.get_pane_process.side_effect = repeat("node")
            tmux_instance.send_eof.return_value = True

            def fake_refresh(**_kwargs):
                refreshed_state = default_runner_state("blog", "main")
                refreshed_state.update(
                    {
                        "status": "done",
                        "current_step": "",
                        "current_phase": "closeout",
                        "phase_status": "handoff_ready",
                        "done_gate_status": "passed",
                        "done_candidate": True,
                        "next_task": "No open tasks remain in TASKS.json.",
                        "next_task_id": None,
                        "blockers": [],
                    }
                )
                write_json(paths.state_file, refreshed_state)
                return True, None

            with (
                patch("src.runner_loop.resolve_target_project_root", return_value=project_root),
                patch("src.runner_loop.TmuxClient", return_value=tmux_instance),
                patch("src.runner_loop._submit_runner_prompt", return_value=(True, None)) as submit_prompt,
                patch("src.runner_loop._run_scripted_cycle_refresh", side_effect=fake_refresh) as scripted_refresh,
                patch("src.runner_loop.time.sleep", return_value=None),
            ):
                tmux_instance.capture_pane.side_effect = [
                    "OpenAI Codex\n› Run /review on my current changes\n",
                    "Running update...\n",
                    "OpenAI Codex\n› \nstate_repaired=yes\nscope_status=narrow\nexiting=yes\n",
                ]

                rc = run_interactive_runner_controller(
                    [
                        "--project",
                        "blog",
                        "--runner-id",
                        "main",
                        "--session-name",
                        "runner-blog",
                        "--dev",
                        str(dev),
                        "--poll-seconds",
                        "0",
                    ]
                )

            self.assertEqual(rc, 0)
            self.assertEqual(submit_prompt.call_count, 1)
            self.assertIn("RUNNER_DISPATCH mode=run_govern", submit_prompt.call_args.kwargs["command"])
            scripted_refresh.assert_called_once()
            self.assertTrue(tmux_instance.send_eof.called)
            state = read_json(paths.state_file)
            self.assertEqual(state.get("status"), "done")
            self.assertEqual(state.get("current_step"), "")
            self.assertEqual(state.get("phase_status"), "handoff_ready")
            self.assertEqual(state.get("done_gate_status"), "passed")
            self.assertEqual(state.get("next_task"), "No open tasks remain in TASKS.json.")
            self.assertEqual(state.get("blockers"), [])
            ledger_events = [
                json.loads(line)["event"]
                for line in paths.ledger_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertIn("runner.update_dispatch_start", ledger_events)
            self.assertIn("runner.update_dispatch_sent", ledger_events)
            self.assertIn("runner.update_complete", ledger_events)
            self.assertIn("runner.chat_exit_requested", ledger_events)

    def test_interactive_runner_controller_retries_high_update_when_mini_update_refresh_still_fails(self):
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
            state = default_runner_state("blog", "main")
            state["current_step"] = "update_pending:mini"
            write_json(paths.state_file, state)
            write_json(paths.exec_context_json, {"phase": "verify"})

            tmux_instance = unittest.mock.Mock()
            tmux_instance.has_session.side_effect = repeat(True)
            tmux_instance.get_pane_process.side_effect = repeat("node")
            tmux_instance.send_eof.return_value = True

            with (
                patch("src.runner_loop.resolve_target_project_root", return_value=project_root),
                patch("src.runner_loop.TmuxClient", return_value=tmux_instance),
                patch("src.runner_loop._submit_runner_prompt", return_value=(True, None)) as submit_prompt,
                patch(
                    "src.runner_loop._run_scripted_cycle_refresh",
                    return_value=(False, "prepare_failed:ERROR: refusing to prepare cycle marker because no durable progress was detected since RUNNER_EXEC_CONTEXT.json"),
                ) as scripted_refresh,
                patch("src.runner_loop.time.sleep", return_value=None),
            ):
                tmux_instance.capture_pane.side_effect = [
                    "OpenAI Codex\n› Run /review on my current changes\n",
                    "Running update...\n",
                    "OpenAI Codex\n› \nstate_repaired=yes\nscope_status=reseed\nexiting=yes\n",
                ]

                rc = run_interactive_runner_controller(
                    [
                        "--project",
                        "blog",
                        "--runner-id",
                        "main",
                        "--session-name",
                        "runner-blog",
                        "--dev",
                        str(dev),
                        "--poll-seconds",
                        "0",
                    ]
                )

            self.assertEqual(rc, 0)
            self.assertEqual(submit_prompt.call_count, 1)
            self.assertIn("RUNNER_DISPATCH mode=run_govern", submit_prompt.call_args.kwargs["command"])
            scripted_refresh.assert_called_once()
            self.assertTrue(tmux_instance.send_eof.called)
            state = read_json(paths.state_file)
            self.assertEqual(state.get("status"), "running")
            self.assertEqual(state.get("current_step"), "update_pending:high")
            self.assertFalse(paths.stop_lock.exists())
            ledger_events = [
                json.loads(line)["event"]
                for line in paths.ledger_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertIn("runner.update_dispatch_start", ledger_events)
            self.assertIn("runner.update_dispatch_sent", ledger_events)
            self.assertIn("runner.update_prepare_failed", ledger_events)
            self.assertIn("runner.update_retry_requested", ledger_events)
            self.assertIn("runner.chat_exit_requested", ledger_events)

    def test_interactive_runner_controller_queues_recovery_task_when_high_update_refresh_has_no_durable_progress(self):
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
            state = default_runner_state("blog", "main")
            state["current_step"] = "update_pending:high"
            write_json(paths.state_file, state)
            write_json(paths.exec_context_json, {"phase": "verify"})

            tmux_instance = unittest.mock.Mock()
            tmux_instance.has_session.side_effect = repeat(True)
            tmux_instance.get_pane_process.side_effect = repeat("node")
            tmux_instance.send_eof.return_value = True

            with (
                patch("src.runner_loop.resolve_target_project_root", return_value=project_root),
                patch("src.runner_loop.TmuxClient", return_value=tmux_instance),
                patch("src.runner_loop._submit_runner_prompt", return_value=(True, None)) as submit_prompt,
                patch(
                    "src.runner_loop._run_scripted_cycle_refresh",
                    side_effect=[
                        (False, "prepare_failed:ERROR: refusing to prepare cycle marker because no durable progress was detected since RUNNER_EXEC_CONTEXT.json"),
                        (True, None),
                    ],
                ) as scripted_refresh,
                patch("src.runner_loop.time.sleep", return_value=None),
            ):
                tmux_instance.capture_pane.side_effect = [
                    "OpenAI Codex\n› Run /review on my current changes\n",
                    "Running update...\n",
                    "OpenAI Codex\n› \nstate_repaired=yes\nscope_status=reseed\nexiting=yes\n",
                ]

                rc = run_interactive_runner_controller(
                    [
                        "--project",
                        "blog",
                        "--runner-id",
                        "main",
                        "--session-name",
                        "runner-blog",
                        "--dev",
                        str(dev),
                        "--poll-seconds",
                        "0",
                    ]
                )

            self.assertEqual(rc, 0)
            self.assertEqual(submit_prompt.call_count, 1)
            self.assertIn("RUNNER_DISPATCH mode=run_govern", submit_prompt.call_args.kwargs["command"])
            self.assertEqual(scripted_refresh.call_count, 2)
            self.assertTrue(tmux_instance.send_eof.called)
            self.assertFalse(paths.stop_lock.exists())
            intake_payload = read_json(paths.task_intake_file)
            self.assertIsInstance(intake_payload, dict)
            queued_tasks = intake_payload.get("tasks")
            self.assertIsInstance(queued_tasks, list)
            self.assertEqual(len(queued_tasks), 1)
            queued_task = queued_tasks[0]
            self.assertEqual(queued_task.get("priority"), "p0")
            self.assertIn("Diagnose stalled runner recovery for", str(queued_task.get("title")))
            ledger_events = [
                json.loads(line)["event"]
                for line in paths.ledger_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertIn("runner.update_prepare_failed", ledger_events)
            self.assertIn("runner.no_progress_recovery_queued", ledger_events)
            self.assertIn("runner.no_progress_recovery_ready", ledger_events)
            self.assertIn("runner.chat_exit_requested", ledger_events)

    def test_interactive_runner_controller_stops_with_error_when_high_update_refresh_fails_for_non_recoverable_reason(self):
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
            state = default_runner_state("blog", "main")
            state["current_step"] = "update_pending:high"
            write_json(paths.state_file, state)
            write_json(paths.exec_context_json, {"phase": "verify"})

            tmux_instance = unittest.mock.Mock()
            tmux_instance.has_session.side_effect = repeat(True)
            tmux_instance.get_pane_process.side_effect = repeat("node")
            tmux_instance.send_eof.return_value = True

            with (
                patch("src.runner_loop.resolve_target_project_root", return_value=project_root),
                patch("src.runner_loop.TmuxClient", return_value=tmux_instance),
                patch("src.runner_loop._submit_runner_prompt", return_value=(True, None)) as submit_prompt,
                patch(
                    "src.runner_loop._run_scripted_cycle_refresh",
                    return_value=(False, "prepare_failed:ERROR: setup state is inconsistent"),
                ) as scripted_refresh,
                patch("src.runner_loop.time.sleep", return_value=None),
            ):
                tmux_instance.capture_pane.side_effect = [
                    "OpenAI Codex\n› Run /review on my current changes\n",
                    "Running update...\n",
                    "OpenAI Codex\n› \nstate_repaired=yes\nscope_status=reseed\nexiting=yes\n",
                ]

                rc = run_interactive_runner_controller(
                    [
                        "--project",
                        "blog",
                        "--runner-id",
                        "main",
                        "--session-name",
                        "runner-blog",
                        "--dev",
                        str(dev),
                        "--poll-seconds",
                        "0",
                    ]
                )

            self.assertEqual(rc, 1)
            self.assertEqual(submit_prompt.call_count, 1)
            self.assertIn("RUNNER_DISPATCH mode=run_govern", submit_prompt.call_args.kwargs["command"])
            scripted_refresh.assert_called_once()
            self.assertTrue(tmux_instance.send_eof.called)
            state = read_json(paths.state_file)
            self.assertEqual(state.get("status"), "error")
            self.assertEqual(state.get("current_step"), "")
            self.assertTrue(paths.stop_lock.exists())
            stop_text = paths.stop_lock.read_text(encoding="utf-8")
            self.assertIn("source=runner_update_failure", stop_text)
            self.assertIn("reason=prepare_failed:ERROR: setup state is inconsistent", stop_text)
            ledger_events = [
                json.loads(line)["event"]
                for line in paths.ledger_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertIn("runner.update_prepare_failed", ledger_events)
            self.assertIn("runner.update_retry_exhausted", ledger_events)
            self.assertNotIn("runner.no_progress_recovery_queued", ledger_events)
            self.assertIn("runner.chat_exit_requested", ledger_events)

    def test_submit_runner_prompt_retries_after_empty_placeholder_expansion(self):
        tmux_instance = unittest.mock.Mock()
        command = "/run_govern"
        tmux_instance.clear_prompt_line.return_value = True
        tmux_instance.send_keys.return_value = True
        tmux_instance.press_enter.side_effect = [True, True, True]
        tmux_instance.send_escape.return_value = True
        tmux_instance.capture_pane.side_effect = [
            f"OpenAI Codex\n› {command}\n",
            'OpenAI Codex\n/run_govern DEV="" PROJECT="" RUNNER_ID="" PWD="" PROJECT_ROOT="" send saved prompt\n',
            f"OpenAI Codex\n› {command}\n",
            "OpenAI Codex\n/run_govern send saved prompt\n",
        ]

        with patch("src.runner_loop._ensure_runner_prompt_active", return_value=True):
            ok, reason = _submit_runner_prompt(
                tmux=tmux_instance,
                session_name="runner-blog",
                command=command,
                settle_attempts=1,
                settle_delay_seconds=0,
            )

        self.assertTrue(ok)
        self.assertIsNone(reason)
        self.assertEqual(tmux_instance.send_keys.call_count, 2)
        first_kwargs = tmux_instance.send_keys.call_args_list[0].kwargs
        second_kwargs = tmux_instance.send_keys.call_args_list[1].kwargs
        self.assertTrue(first_kwargs["force_buffer"])
        self.assertTrue(second_kwargs["force_buffer"])
        self.assertEqual(tmux_instance.press_enter.call_count, 3)
        self.assertEqual(tmux_instance.send_escape.call_count, 2)

    def test_submit_runner_prompt_activates_prompt_before_typing(self):
        tmux_instance = unittest.mock.Mock()
        command = "/run_execute PHASE=implement"
        tmux_instance.clear_prompt_line.return_value = True
        tmux_instance.send_keys.return_value = True
        tmux_instance.press_enter.side_effect = [True, True]
        tmux_instance.send_escape.return_value = True
        tmux_instance.capture_pane.side_effect = [
            "OpenAI Codex\nNotes panel still focused\n",
            "OpenAI Codex\n› Run /review on my current changes\n",
            f"OpenAI Codex\n› {command}\n",
            "OpenAI Codex\n/run_execute send saved prompt\n",
        ]

        ok, reason = _submit_runner_prompt(
            tmux=tmux_instance,
            session_name="runner-blog",
            command=command,
            settle_attempts=1,
            settle_delay_seconds=0,
        )

        self.assertTrue(ok)
        self.assertIsNone(reason)
        self.assertEqual(tmux_instance.send_escape.call_count, 1)
        self.assertEqual(tmux_instance.send_keys.call_count, 1)

    def test_submit_runner_prompt_fails_closed_when_prompt_never_activates(self):
        tmux_instance = unittest.mock.Mock()
        command = "/run_govern"
        tmux_instance.send_escape.return_value = True
        tmux_instance.capture_pane.side_effect = [
            "OpenAI Codex\nSide panel active\n",
            "OpenAI Codex\nSide panel active\n",
            "OpenAI Codex\nSide panel active\n",
            "OpenAI Codex\nSide panel active\n",
            "OpenAI Codex\nSide panel active\n",
            "OpenAI Codex\nSide panel active\n",
        ]

        ok, reason = _submit_runner_prompt(
            tmux=tmux_instance,
            session_name="runner-blog",
            command=command,
            settle_attempts=1,
            settle_delay_seconds=0,
        )

        self.assertFalse(ok)
        self.assertEqual(reason, "prompt_not_active")
        self.assertEqual(tmux_instance.send_escape.call_count, 3)
        self.assertFalse(tmux_instance.send_keys.called)
        self.assertFalse(tmux_instance.press_enter.called)

    def test_submit_runner_prompt_reports_saved_prompt_expansion_failure_reason(self):
        tmux_instance = unittest.mock.Mock()
        command = "/run_govern"
        tmux_instance.clear_prompt_line.return_value = True
        tmux_instance.send_keys.return_value = True
        tmux_instance.press_enter.return_value = True
        tmux_instance.send_escape.return_value = True
        tmux_instance.capture_pane.side_effect = [
            "OpenAI Codex\n› Run /review on my current changes\n",
            f"OpenAI Codex\n› {command}\n",
            "OpenAI Codex\nstill waiting for expansion\n",
            "OpenAI Codex\nstill waiting for expansion\n",
        ]

        ok, reason = _submit_runner_prompt(
            tmux=tmux_instance,
            session_name="runner-blog",
            command=command,
            settle_attempts=1,
            settle_delay_seconds=0,
            submit_attempts=1,
        )

        self.assertFalse(ok)
        self.assertEqual(reason, "saved_prompt_expansion_not_ready")

    def test_submit_runner_prompt_accepts_direct_runner_prompt_submission_without_saved_prompt_banner(self):
        tmux_instance = unittest.mock.Mock()
        command = "/run_execute PHASE=implement"
        tmux_instance.clear_prompt_line.return_value = True
        tmux_instance.send_keys.return_value = True
        tmux_instance.press_enter.return_value = True
        tmux_instance.send_escape.return_value = True
        tmux_instance.capture_pane.side_effect = [
            "OpenAI Codex\n› Run /review on my current changes\n",
            f"OpenAI Codex\n› {command}\n",
            "Use this command to execute exactly one medium bounded infinite-runner work slice.\n\n## Scope First\n",
        ]

        ok, reason = _submit_runner_prompt(
            tmux=tmux_instance,
            session_name="runner-blog",
            command=command,
            settle_attempts=1,
            settle_delay_seconds=0,
            submit_attempts=1,
        )

        self.assertTrue(ok)
        self.assertIsNone(reason)
        self.assertEqual(tmux_instance.press_enter.call_count, 1)

    def test_interactive_runner_controller_records_dispatch_failure_reason(self):
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
            write_json(paths.state_file, default_runner_state("blog", "main"))
            write_json(paths.exec_context_json, {"phase": "implement"})

            tmux_instance = unittest.mock.Mock()
            tmux_instance.clear_prompt_line.return_value = True
            tmux_instance.send_keys.return_value = True
            tmux_instance.press_enter.return_value = True
            tmux_instance.send_escape.return_value = True

            with (
                patch("src.runner_loop.resolve_target_project_root", return_value=project_root),
                patch("src.runner_loop.TmuxClient", return_value=tmux_instance),
                patch("src.runner_loop.time.sleep", return_value=None),
            ):
                tmux_instance.has_session.side_effect = repeat(True)
                tmux_instance.get_pane_process.return_value = "python3"
                def capture_side_effect(*_args, **_kwargs):
                    count = tmux_instance.capture_pane.call_count
                    if count == 1:
                        return "OpenAI Codex\n› Run /review on my current changes\n"
                    if count in {2, 3, 4, 5, 6}:
                        return "OpenAI Codex\nSide panel active\n"
                    tmux_instance.has_session.side_effect = [False]
                    return "OpenAI Codex\nSide panel active\n"

                tmux_instance.capture_pane.side_effect = capture_side_effect

                rc = run_interactive_runner_controller(
                    [
                        "--project",
                        "blog",
                        "--runner-id",
                        "main",
                        "--session-name",
                        "runner-blog",
                        "--dev",
                        str(dev),
                        "--poll-seconds",
                        "0",
                    ]
                )

            self.assertEqual(rc, 0)
            ledger = [
                json.loads(line)
                for line in paths.ledger_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            dispatch_failed = [entry for entry in ledger if entry.get("event") == "runner.dispatch_failed"]
            self.assertTrue(dispatch_failed)
            self.assertEqual(dispatch_failed[-1].get("reason"), "prompt_not_active")
            log_text = paths.runner_log.read_text(encoding="utf-8")
            self.assertIn("reason=prompt_not_active", log_text)


class LoopPromptTests(unittest.TestCase):
    def test_prompt_mentions_canonical_runner_state_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_runner_state_paths(tmp, "blog", "main")
            _seed_orx_execution_packet(paths.memory_dir.parent)
            prompt = _build_prompt(project="blog", runner_id="main", paths=paths)

        self.assertIn(f"Runner state file: {paths.state_file}", prompt)
        self.assertIn(
            "Treat the ORX execution packet as the only live objective source for this cycle.",
            prompt,
        )
        self.assertIn(f"Active backlog file: {paths.active_backlog_json}", prompt)
        self.assertIn("Treat RUNNER_HANDOFF.md as human/manual recovery context", prompt)

    def test_prompt_prefers_orx_execution_packet_over_stale_local_backlog(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_runner_state_paths(tmp, "blog", "main")
            paths.runner_dir.mkdir(parents=True, exist_ok=True)
            write_json(
                paths.kanban_state_json,
                {
                    "active_issue": {
                        "identifier": "PRO-OLD",
                        "title": "Stale local issue",
                        "url": "https://linear.example/PRO-OLD",
                    }
                },
            )
            write_json(
                paths.active_backlog_json,
                {
                    "kanban": {
                        "active_issue_identifier": "PRO-OLD",
                        "active_issue_title": "Stale local issue",
                    },
                    "active_backlog": [{"identifier": "PRO-OLD", "title": "Stale local issue"}],
                },
            )
            with patch(
                "src.runner_loop.fetch_execution_packet",
                return_value={
                    "issue_key": "PRO-NEW",
                    "issue_title": "Fresh ORX issue",
                    "issue_url": "https://linear.example/PRO-NEW",
                    "project_key": "blog",
                    "project_display_name": "Blog",
                    "worktree_path": "/tmp/worktree",
                    "branch": "linear/pro-new",
                    "active_slice_id": "slice-1",
                },
            ):
                prompt = _build_prompt(project="blog", runner_id="main", paths=paths)

        self.assertIn("PRO-NEW", prompt)
        self.assertIn("Fresh ORX issue", prompt)
        self.assertNotIn("Stale local issue", prompt)
        self.assertIn('"active_backlog_top4": []', prompt)

    def test_open_runner_backlog_prefers_orx_execution_packet_over_stale_local_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "blog"
            runner_dir = project_root / ".memory" / "runner"
            runner_dir.mkdir(parents=True, exist_ok=True)
            write_json(
                runner_dir / "RUNNER_ACTIVE_BACKLOG.json",
                {
                    "kanban": {
                        "active_issue_identifier": "PRO-OLD",
                        "active_issue_title": "Stale local issue",
                    },
                    "active_backlog": [{"identifier": "PRO-OLD", "title": "Stale local issue"}],
                },
            )
            with patch(
                "src.runner_loop.fetch_execution_packet",
                return_value={
                    "issue_key": "PRO-NEW",
                    "issue_title": "Fresh ORX issue",
                    "project_key": "blog",
                    "active_slice_id": "slice-1",
                },
            ):
                items, label = _extract_open_runner_backlog(project_root, project="blog")

        self.assertEqual(label, "ORX execution packet")
        self.assertEqual(items, ["PRO-NEW: Fresh ORX issue"])


class LoopLoggingTests(unittest.TestCase):
    def test_log_line_keeps_console_output_unstamped(self):
        with tempfile.TemporaryDirectory() as tmp:
            message = "Iteration 1 running gpt-5.3-codex"
            log_path = Path(tmp) / "runner.log"
            paths = SimpleNamespace(runner_log=log_path)
            with patch("builtins.print") as mocked_print:
                _log_line(paths, message)

            mocked_print.assert_called_once_with(message, flush=True)
            log_line = log_path.read_text().strip().splitlines()[-1]
            self.assertRegex(log_line, r"^\[\d{2}:\d{2}:\d{2}\] Iteration 1 running gpt-5.3-codex$")


class StackDetectionTests(unittest.TestCase):
    def test_detect_pnpm(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'")
            self.assertEqual(detect_project_stack(root), "pnpm")

    def test_detect_npm(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package-lock.json").write_text("{}")
            self.assertEqual(detect_project_stack(root), "npm")

    def test_detect_python(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='demo'\n")
            self.assertEqual(detect_project_stack(root), "python_pyproject")

    def test_detect_go_and_cargo(self):
        with tempfile.TemporaryDirectory() as tmp:
            go_root = Path(tmp) / "go"
            cargo_root = Path(tmp) / "cargo"
            go_root.mkdir(parents=True)
            cargo_root.mkdir(parents=True)
            (go_root / "go.mod").write_text("module example.com/demo\n")
            (cargo_root / "Cargo.toml").write_text("[package]\nname='demo'\nversion='0.1.0'\n")
            self.assertEqual(detect_project_stack(go_root), "go")
            self.assertEqual(detect_project_stack(cargo_root), "cargo")

    def test_detect_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(detect_project_stack(Path(tmp)), "unknown")


class EnsureGatesFileTests(unittest.TestCase):
    def test_creates_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp)
            project_root = dev / "Repos" / "blog"
            project_root.mkdir(parents=True)
            (project_root / "package.json").write_text("{}")

            gates_path, created_now = ensure_gates_file(str(dev), "blog")

            self.assertTrue(created_now)
            self.assertTrue(gates_path.exists())
            self.assertTrue((project_root / ".memory" / "lessons.md").exists())
            content = gates_path.read_text()
            self.assertIn("run_gates()", content)
            self.assertIn("set -euo pipefail", content)

    def test_does_not_overwrite_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp)
            gates_path = dev / "Repos" / "blog" / ".memory" / "gates.sh"
            gates_path.parent.mkdir(parents=True)
            gates_path.write_text("#!/usr/bin/env bash\nrun_gates() { echo custom; }\n")

            returned_path, created_now = ensure_gates_file(str(dev), "blog")

            self.assertFalse(created_now)
            self.assertEqual(returned_path.resolve(), gates_path.resolve())
            self.assertEqual(gates_path.read_text(), "#!/usr/bin/env bash\nrun_gates() { echo custom; }\n")
            self.assertTrue((dev / "Repos" / "blog" / ".memory" / "lessons.md").exists())

    def test_unknown_template_is_explicitly_failing(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp)
            (dev / "Repos" / "unknown-project").mkdir(parents=True)

            gates_path, created_now = ensure_gates_file(str(dev), "unknown-project")

            self.assertTrue(created_now)
            content = gates_path.read_text()
            self.assertIn("unknown project stack", content)
            self.assertIn("return 1", content)


class CompletionEnforcementTests(unittest.TestCase):
    def _setup_project(self):
        tmp = tempfile.TemporaryDirectory()
        dev = Path(tmp.name)
        project_root = dev / "Repos" / "blog"
        memory = project_root / ".memory"
        memory.mkdir(parents=True)
        (memory / "gates.sh").write_text("#!/usr/bin/env bash\nrun_gates(){ return 0; }\n")
        return tmp, dev, project_root

    def test_done_lock_plus_passing_gates_exits_and_preserves_lock(self):
        tmp, dev, _project_root = self._setup_project()
        try:
            paths = build_runner_state_paths(str(dev), "blog", "alpha")
            state = default_runner_state("blog", "alpha")
            state["enabled"] = True
            write_json(paths.state_file, state)
            paths.done_lock.parent.mkdir(parents=True, exist_ok=True)
            paths.done_lock.touch()

            with patch("src.runner_loop.create_runner_state", return_value={"ok": True}), patch(
                "src.runner_loop._run_gates", return_value=(True, "")
            ), patch("src.runner_loop.time.sleep", return_value=None):
                rc = run_loop_runner(
                    dev=str(dev),
                    project="blog",
                    runner_id="alpha",
                    model="gpt-5.3-codex",
                    session_name="runner-blog-alpha",
                    backoff_seconds=0,
                )

            self.assertEqual(rc, 0)
            self.assertTrue(paths.done_lock.exists())
            current_state = json.loads(paths.state_file.read_text())
            self.assertEqual(current_state["status"], "done")
        finally:
            tmp.cleanup()

    def test_done_lock_with_failing_gates_is_removed_and_loop_continues(self):
        tmp, dev, _project_root = self._setup_project()
        try:
            paths = build_runner_state_paths(str(dev), "blog", "alpha")
            state = default_runner_state("blog", "alpha")
            state["enabled"] = True
            write_json(paths.state_file, state)
            _seed_orx_execution_packet(paths.memory_dir.parent)
            paths.done_lock.parent.mkdir(parents=True, exist_ok=True)
            paths.done_lock.touch()

            codex_result = CodexRunResult(
                exit_code=0,
                session_id="thread-1",
                final_message="",
                events=[],
                raw_lines=[
                    "RUNNER_UPDATE_START",
                    json.dumps(
                        {
                            "summary": "iter update",
                            "completed": ["done one"],
                            "completed_task_ids": [],
                            "next_task": "continue",
                            "next_task_reason": "follow-up",
                            "blockers": [],
                            "remaining_gaps": [],
                            "done_candidate": False,
                        }
                    ),
                    "RUNNER_UPDATE_END",
                ],
            )

            with patch("src.runner_loop.create_runner_state", return_value={"ok": True}), patch(
                "src.runner_loop._run_gates", return_value=(False, "gates failed")
            ), patch(
                "src.runner_loop.time.sleep", return_value=None
            ), patch("src.runner_loop.run_codex_iteration") as mocked_run:
                def _once_then_stop(*_args, **_kwargs):
                    paths.stop_lock.write_text("stop\n")
                    return codex_result

                mocked_run.side_effect = _once_then_stop
                rc = run_loop_runner(
                    dev=str(dev),
                    project="blog",
                    runner_id="alpha",
                    model="gpt-5.3-codex",
                    session_name="runner-blog-alpha",
                    backoff_seconds=0,
                )

            self.assertEqual(rc, 0)
            self.assertFalse(paths.done_lock.exists())
            current_state = json.loads(paths.state_file.read_text())
            self.assertEqual(current_state["status"], "manual_stop")
        finally:
            tmp.cleanup()

    def test_done_marker_creates_lock_and_exits_done(self):
        tmp, dev, _project_root = self._setup_project()
        try:
            paths = build_runner_state_paths(str(dev), "blog", "alpha")
            state = default_runner_state("blog", "alpha")
            state["enabled"] = True
            write_json(paths.state_file, state)
            _seed_orx_execution_packet(paths.memory_dir.parent)

            codex_result = CodexRunResult(
                exit_code=0,
                session_id="thread-1",
                final_message="All tasks complete",
                events=[],
                raw_lines=[
                    "RUNNER_UPDATE_START",
                    json.dumps(
                        {
                            "summary": "all done",
                            "completed": ["finished task"],
                            "completed_task_ids": [],
                            "next_task": "none",
                            "next_task_reason": "all goals complete",
                            "blockers": [],
                            "remaining_gaps": [],
                            "done_candidate": True,
                        }
                    ),
                    "RUNNER_UPDATE_END",
                ],
            )

            with patch("src.runner_loop.create_runner_state", return_value={"ok": True}), patch(
                "src.runner_loop.run_codex_iteration", return_value=codex_result
            ), patch(
                "src.runner_loop._extract_open_runner_backlog",
                return_value=([], "ORX execution packet"),
            ), patch("src.runner_loop.time.sleep", return_value=None):
                rc = run_loop_runner(
                    dev=str(dev),
                    project="blog",
                    runner_id="alpha",
                    model="gpt-5.3-codex",
                    session_name="runner-blog-alpha",
                    backoff_seconds=0,
                )

            self.assertEqual(rc, 0)
            self.assertTrue(paths.done_lock.exists())
            current_state = json.loads(paths.state_file.read_text())
            self.assertEqual(current_state["status"], "done")
            ledger_lines = paths.ledger_file.read_text().splitlines()
            self.assertTrue(any("runner.done_lock_created" in line for line in ledger_lines))
        finally:
            tmp.cleanup()

    def test_done_marker_rejected_when_tasks_open(self):
        tmp, dev, project_root = self._setup_project()
        try:
            paths = build_runner_state_paths(str(dev), "blog", "alpha")
            state = default_runner_state("blog", "alpha")
            state["enabled"] = True
            write_json(paths.state_file, state)
            _seed_orx_execution_packet(paths.memory_dir.parent)
            tasks_file = paths.runner_dir / "TASKS.json"
            tasks_file.parent.mkdir(parents=True, exist_ok=True)
            tasks_file.write_text(
                json.dumps(
                    {
                        "objective_id": "OBJ-TEST",
                        "tasks": [
                            {
                                "task_id": "TT-001",
                                "title": "Refactor remaining slice.",
                                "status": "open",
                                "priority": "p1",
                                "depends_on": [],
                                "project_root": str(project_root),
                                "target_branch": "main",
                                "acceptance": ["Complete remaining slice"],
                                "validation": ["run_gates"],
                                "updated_at": "2026-03-04T00:00:00Z",
                            }
                        ],
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )

            codex_result = CodexRunResult(
                exit_code=0,
                session_id="thread-1",
                final_message="All tasks complete",
                events=[],
                raw_lines=[
                    "RUNNER_UPDATE_START",
                    json.dumps(
                        {
                            "summary": "all done",
                            "completed": ["finished task"],
                            "completed_task_ids": [],
                            "next_task": "none",
                            "next_task_reason": "all goals complete",
                            "blockers": [],
                            "remaining_gaps": [],
                            "done_candidate": True,
                        }
                    ),
                    "RUNNER_UPDATE_END",
                ],
            )

            first_call = {"done": False}

            def _once_then_stop(*_args, **_kwargs):
                if not first_call["done"]:
                    first_call["done"] = True
                    paths.stop_lock.write_text("stop\n", encoding="utf-8")
                    return codex_result
                return codex_result

            with patch("src.runner_loop.create_runner_state", return_value={"ok": True}), patch(
                "src.runner_loop.run_codex_iteration", side_effect=_once_then_stop
            ), patch(
                "src.runner_loop._extract_open_runner_backlog",
                return_value=(["TT-001: Refactor remaining slice."], "TASKS.json"),
            ), patch("src.runner_loop.time.sleep", return_value=None):
                rc = run_loop_runner(
                    dev=str(dev),
                    project="blog",
                    runner_id="alpha",
                    model="gpt-5.3-codex",
                    session_name="runner-blog-alpha",
                    backoff_seconds=0,
                )

            self.assertEqual(rc, 0)
            self.assertFalse(paths.done_lock.exists())
            current_state = json.loads(paths.state_file.read_text())
            self.assertEqual(current_state["done_gate_status"], "failed")
            self.assertFalse(current_state["done_candidate"])
        finally:
            tmp.cleanup()

    def test_done_marker_rejected_when_self_review_reports_remaining_gaps(self):
        tmp, dev, _project_root = self._setup_project()
        try:
            paths = build_runner_state_paths(str(dev), "blog", "alpha")
            state = default_runner_state("blog", "alpha")
            state["enabled"] = True
            write_json(paths.state_file, state)
            _seed_orx_execution_packet(paths.memory_dir.parent)

            codex_result = CodexRunResult(
                exit_code=0,
                session_id="thread-1",
                final_message="Looks close.",
                events=[],
                raw_lines=[
                    "RUNNER_UPDATE_START",
                    json.dumps(
                        {
                            "summary": "Implemented the slice but one UX edge remains.",
                            "completed": ["Adjusted the panel layout"],
                            "completed_task_ids": [],
                            "next_task": "Polish the remaining input focus behavior",
                            "next_task_reason": "Self-review found one remaining UX gap.",
                            "blockers": [],
                            "remaining_gaps": ["Input focus ring is still clipped in the compact panel."],
                            "done_candidate": True,
                        }
                    ),
                    "RUNNER_UPDATE_END",
                ],
            )

            with patch("src.runner_loop.create_runner_state", return_value={"ok": True}), patch(
                "src.runner_loop.time.sleep", return_value=None
            ):
                def _once_then_stop(*_args, **_kwargs):
                    paths.stop_lock.parent.mkdir(parents=True, exist_ok=True)
                    paths.stop_lock.write_text("stop\n", encoding="utf-8")
                    return codex_result

                src_runner = __import__("src.runner_loop", fromlist=["run_codex_iteration"])
                with patch.object(src_runner, "run_codex_iteration", side_effect=_once_then_stop):
                    rc = run_loop_runner(
                        dev=str(dev),
                        project="blog",
                        runner_id="alpha",
                        model="gpt-5.3-codex",
                        session_name="runner-blog-alpha",
                        backoff_seconds=0,
                    )

            self.assertEqual(rc, 0)
            self.assertFalse(paths.done_lock.exists())
            current_state = json.loads(paths.state_file.read_text())
            self.assertFalse(current_state["done_candidate"])
            self.assertEqual(current_state["done_gate_status"], "pending")
            self.assertTrue(
                any(
                    "Self-review gap: Input focus ring is still clipped in the compact panel."
                    in blocker
                    for blocker in current_state["blockers"]
                )
            )
        finally:
            tmp.cleanup()

    def test_parse_fallback_stays_fail_closed_when_completion_was_only_inferred(self):
        tmp, dev, _project_root = self._setup_project()
        try:
            paths = build_runner_state_paths(str(dev), "blog", "alpha")
            state = default_runner_state("blog", "alpha")
            state["enabled"] = True
            write_json(paths.state_file, state)
            _seed_orx_execution_packet(paths.memory_dir.parent)

            main_result = CodexRunResult(
                exit_code=0,
                session_id="thread-main",
                final_message="Implementation complete. All requested changes are completed.",
                events=[],
                raw_lines=["Implemented and verified."],
            )
            probe_result = CodexRunResult(
                exit_code=0,
                session_id="thread-probe",
                final_message="",
                events=[],
                raw_lines=["still not parseable"],
            )

            with patch("src.runner_loop.create_runner_state", return_value={"ok": True}), patch(
                "src.runner_loop.time.sleep", return_value=None
            ):
                first_call = {"done": False}

                def _runs(*_args, **_kwargs):
                    if not first_call["done"]:
                        first_call["done"] = True
                        return main_result
                    paths.stop_lock.parent.mkdir(parents=True, exist_ok=True)
                    paths.stop_lock.write_text("stop\n", encoding="utf-8")
                    return probe_result

                src_runner = __import__("src.runner_loop", fromlist=["run_codex_iteration"])
                with patch.object(src_runner, "run_codex_iteration", side_effect=_runs):
                    rc = run_loop_runner(
                        dev=str(dev),
                        project="blog",
                        runner_id="alpha",
                        model="gpt-5.3-codex",
                        session_name="runner-blog-alpha",
                        backoff_seconds=0,
                    )

            self.assertEqual(rc, 0)
            self.assertFalse(paths.done_lock.exists())
            current_state = json.loads(paths.state_file.read_text())
            self.assertFalse(current_state["done_candidate"])
            self.assertEqual(current_state["done_gate_status"], "pending")
            self.assertTrue(
                any(
                    "Structured self-review was unavailable because the runner update payload could not be parsed."
                    in blocker
                    for blocker in current_state["blockers"]
                )
            )
        finally:
            tmp.cleanup()

    def test_parse_failure_triggers_finalize_hook_probe_and_done_lock(self):
        tmp, dev, _project_root = self._setup_project()
        try:
            paths = build_runner_state_paths(str(dev), "blog", "alpha")
            state = default_runner_state("blog", "alpha")
            state["enabled"] = True
            write_json(paths.state_file, state)
            _seed_orx_execution_packet(paths.memory_dir.parent)

            main_result = CodexRunResult(
                exit_code=0,
                session_id="thread-main",
                final_message="Implementation complete. All requested changes are completed.",
                events=[],
                raw_lines=["Implemented and verified."],
            )
            probe_result = CodexRunResult(
                exit_code=0,
                session_id="thread-probe",
                final_message="",
                events=[],
                raw_lines=[
                    "RUNNER_UPDATE_START",
                    json.dumps(
                        {
                            "summary": "Completed implementation and verification.",
                            "completed": ["Implemented requested changes", "Verified with gates"],
                            "completed_task_ids": [],
                            "next_task": "No further implementation work; ready to exit.",
                            "next_task_reason": "Completion confirmed by finalize hook.",
                            "blockers": [],
                            "remaining_gaps": [],
                            "done_candidate": True,
                        }
                    ),
                    "RUNNER_UPDATE_END",
                ],
            )

            with patch("src.runner_loop.create_runner_state", return_value={"ok": True}), patch(
                "src.runner_loop.run_codex_iteration",
                side_effect=[main_result, probe_result],
            ), patch(
                "src.runner_loop._extract_open_runner_backlog",
                return_value=([], "ORX execution packet"),
            ), patch("src.runner_loop.time.sleep", return_value=None):
                rc = run_loop_runner(
                    dev=str(dev),
                    project="blog",
                    runner_id="alpha",
                    model="gpt-5.3-codex",
                    session_name="runner-blog-alpha",
                    backoff_seconds=0,
                )

            self.assertEqual(rc, 0)
            self.assertTrue(paths.done_lock.exists())
            current_state = json.loads(paths.state_file.read_text())
            self.assertEqual(current_state["status"], "done")
            hooks_lines = paths.hooks_log.read_text().splitlines()
            self.assertTrue(any('"event": "on_finalize"' in line for line in hooks_lines))
            ledger_lines = paths.ledger_file.read_text().splitlines()
            self.assertTrue(any("iteration.finalize_probe.start" in line for line in ledger_lines))
            self.assertTrue(any("runner.done_lock_created" in line for line in ledger_lines))
        finally:
            tmp.cleanup()

    def test_setup_only_uses_fresh_session_each_iteration(self):
        tmp, dev, _project_root = self._setup_project()
        try:
            paths = build_runner_state_paths(str(dev), "blog", "alpha")
            state = default_runner_state("blog", "alpha")
            state["enabled"] = True
            state["session_id"] = "previous-thread"
            write_json(paths.state_file, state)
            _seed_orx_execution_packet(paths.memory_dir.parent)
            paths.stop_lock.parent.mkdir(parents=True, exist_ok=True)
            paths.stop_lock.write_text("stop now\n")

            codex_result = CodexRunResult(
                exit_code=0,
                session_id="thread-new",
                final_message="updated",
                events=[],
                raw_lines=[
                    "RUNNER_UPDATE_START",
                    json.dumps(
                        {
                            "summary": "updated",
                            "completed": ["a"],
                            "completed_task_ids": [],
                            "next_task": "b",
                            "next_task_reason": "c",
                            "blockers": [],
                            "remaining_gaps": [],
                            "done_candidate": False,
                        }
                    ),
                    "RUNNER_UPDATE_END",
                ],
            )

            with patch("src.runner_loop.create_runner_state", return_value={"ok": True}), patch(
                "src.runner_loop.run_codex_iteration", return_value=codex_result
            ) as mocked_run, patch("src.runner_loop.time.sleep", return_value=None):
                # Clear stop lock after first pass so one iteration executes and loop exits.
                paths.stop_lock.unlink(missing_ok=True)
                # Force manual stop on second loop by creating stop lock via side effect.
                def _inject_stop(*args, **kwargs):
                    paths.stop_lock.write_text("stop\n")
                    return codex_result

                mocked_run.side_effect = _inject_stop
                rc = run_loop_runner(
                    dev=str(dev),
                    project="blog",
                    runner_id="alpha",
                    model="gpt-5.3-codex",
                    session_name="runner-blog-alpha",
                    backoff_seconds=0,
                )

            self.assertEqual(rc, 0)
            call_kwargs = mocked_run.call_args.kwargs
            self.assertIsNone(call_kwargs["session_id"])
        finally:
            tmp.cleanup()

    def test_iteration_exception_finalizes_state_and_logs_runner_end(self):
        tmp, dev, _project_root = self._setup_project()
        try:
            paths = build_runner_state_paths(str(dev), "blog", "alpha")
            state = default_runner_state("blog", "alpha")
            state["enabled"] = True
            write_json(paths.state_file, state)

            with patch("src.runner_loop.create_runner_state", return_value={"ok": True}), patch(
                "src.runner_loop.run_codex_iteration", side_effect=RuntimeError("boom")
            ), patch("src.runner_loop.time.sleep", return_value=None):
                rc = run_loop_runner(
                    dev=str(dev),
                    project="blog",
                    runner_id="alpha",
                    model="gpt-5.3-codex",
                    session_name="runner-blog-alpha",
                    backoff_seconds=0,
                )

            self.assertEqual(rc, 1)
            current_state = json.loads(paths.state_file.read_text())
            self.assertEqual(current_state["status"], "error")
            self.assertEqual(current_state["current_step"], "")
            self.assertFalse(paths.active_lock.exists())

            ledger_lines = paths.ledger_file.read_text().splitlines()
            self.assertTrue(any("runner.exception" in line for line in ledger_lines))
            self.assertTrue(any('"event": "runner.end"' in line and '"status": "error"' in line for line in ledger_lines))

            runners_log = paths.runners_log.read_text().strip().splitlines()
            self.assertTrue(runners_log)
            last_fields = runners_log[-1].split(",")
            self.assertEqual(last_fields[0], "runner-blog-alpha")
            self.assertTrue(len(last_fields) >= 3 and last_fields[2].strip())
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
