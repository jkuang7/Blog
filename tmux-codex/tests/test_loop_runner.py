import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.codex_engine import CodexRunResult
from src.main import parse_loop_args
from src.runner_loop import (
    _build_prompt,
    _log_line,
    build_runner_paths,
    detect_project_stack,
    ensure_gates_file,
    make_codex_chat_loop_script,
    make_codex_exec_loop_script,
    run_loop_runner,
)
from src.runner_state import build_runner_state_paths, default_runner_state, write_json


class LoopArgsTests(unittest.TestCase):
    def test_parse_loop_defaults(self):
        parsed = parse_loop_args(["blog"])
        self.assertEqual(parsed["project"], "blog")
        self.assertEqual(parsed["complexity"], "med")
        self.assertEqual(parsed["model"], "gpt-5.3-codex")
        self.assertEqual(parsed["reasoning_effort"], "medium")
        self.assertEqual(parsed["hil_mode"], "setup-only")
        self.assertEqual(parsed["runner_mode"], "interactive-watchdog")
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

    def test_parse_loop_invalid_complexity(self):
        with self.assertRaises(ValueError):
            parse_loop_args(["blog", "--complexity", "extreme"])

    def test_parse_loop_hil_mode_rejects_strict(self):
        with self.assertRaises(ValueError):
            parse_loop_args(["blog", "--hil-mode", "strict"])

    def test_parse_loop_invalid_hil_mode(self):
        with self.assertRaises(ValueError):
            parse_loop_args(["blog", "--hil-mode", "auto"])

    def test_parse_loop_runner_mode_exec(self):
        parsed = parse_loop_args(["blog", "--runner-mode", "exec"])
        self.assertEqual(parsed["runner_mode"], "exec")

    def test_parse_loop_runner_mode_invalid(self):
        with self.assertRaises(ValueError):
            parse_loop_args(["blog", "--runner-mode", "bogus"])


class LoopScriptTests(unittest.TestCase):
    def test_runner_paths_are_runner_scoped(self):
        with patch("src.runner_loop.resolve_target_project_root", return_value=Path("/Users/jian/Dev/Repos/blog")):
            paths = build_runner_paths(
                dev="/Users/jian/Dev",
                project="blog",
                runner_id="main",
            )
        self.assertTrue(str(paths.complete_lock).endswith("/Repos/blog/.memory/RUNNER_DONE.lock"))
        self.assertTrue(str(paths.stop_file).endswith("/Repos/blog/.memory/RUNNER_STOP.lock"))
        self.assertTrue(str(paths.active_lock).endswith("/Repos/blog/.memory/RUNNER_ACTIVE.lock"))
        self.assertTrue(str(paths.state_file).endswith("/Repos/blog/.memory/runner/RUNNER_STATE.json"))
        self.assertTrue(str(paths.audit_file).endswith("/Repos/blog/.memory/runner/RUNNER_LEDGER.ndjson"))
        self.assertTrue(str(paths.runner_log).endswith("/.codex/logs/runners/runner-blog.log"))

    def test_script_runs_interactive_runner_chat(self):
        with patch("src.runner_loop.resolve_target_project_root", return_value=Path("/Users/jian/Dev/Repos/blog")):
            paths = build_runner_paths(
                dev="/Users/jian/Dev",
                project="blog",
                runner_id="main",
            )
        script = make_codex_chat_loop_script(
            dev="/Users/jian/Dev",
            project="blog",
            runner_id="main",
            session_name="runner-blog",
            model="gpt-5.1-codex",
            reasoning_effort="high",
            hil_mode="setup-only",
            paths=paths,
        )

        self.assertIn('cd /Users/jian/Dev/Repos/blog || exit 1', script)
        self.assertIn(
            "watchdog seeds /prompts:runner-discover DEV=/Users/jian/Dev PROJECT=blog RUNNER_ID=main PWD=/Users/jian/Dev/Repos/blog PROJECT_ROOT=/Users/jian/Dev/Repos/blog MODE=execute_only when idle",
            script,
        )
        self.assertIn('git -C "$PROJECT_ROOT" checkout "$TARGET_BRANCH"', script)
        self.assertIn("codex --search --dangerously-bypass-approvals-and-sandbox -m gpt-5.1-codex", script)
        self.assertIn('reasoning.effort="high"', script)
        self.assertNotIn("codex --search exec", script)
        self.assertNotIn("while true; do", script)
        self.assertNotIn('python3 "$RUNCTL" --setup --project-root "$PROJECT_ROOT" --runner-id main', script)
        self.assertNotIn("__runner-loop", script)

    def test_exec_script_retains_restart_and_lock_handling(self):
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
            hil_mode="setup-only",
            paths=paths,
        )

        self.assertIn('if [[ -f "$STOP_LOCK" ]]; then', script)
        self.assertIn('if [[ -f "$DONE_LOCK" ]]; then', script)
        self.assertIn("restarting interactive runner chat in 3s", script)
        self.assertIn("sleep 3", script)
        self.assertIn(
            'codex --search --dangerously-bypass-approvals-and-sandbox exec -C "$PROJECT_ROOT" -m gpt-5.1-codex',
            script,
        )
        self.assertIn('git -C "$PROJECT_ROOT" checkout "$TARGET_BRANCH"', script)


class LoopPromptTests(unittest.TestCase):
    def test_prompt_mentions_canonical_runner_state_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_runner_state_paths(tmp, "blog", "main")
            prompt = _build_prompt(project="blog", runner_id="main", paths=paths)

        self.assertIn(f"Runner state file: {paths.state_file}", prompt)
        self.assertIn("Use .memory/runner/RUNNER_EXEC_CONTEXT.json plus runner state to respect the current phase goal and next task.", prompt)


class LoopLoggingTests(unittest.TestCase):
    def test_log_line_keeps_console_output_unstamped(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_runner_state_paths(tmp, "blog", "main")
            message = "Iteration 1 running gpt-5.3-codex"

            with patch("builtins.print") as mocked_print:
                _log_line(paths, message)

            mocked_print.assert_called_once_with(message, flush=True)
            log_line = paths.runner_log.read_text().strip()
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
                            "next_task": "continue",
                            "next_task_reason": "follow-up",
                            "blockers": [],
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
                            "next_task": "none",
                            "next_task_reason": "all goals complete",
                            "blockers": [],
                            "done_candidate": True,
                        }
                    ),
                    "RUNNER_UPDATE_END",
                ],
            )

            with patch("src.runner_loop.create_runner_state", return_value={"ok": True}), patch(
                "src.runner_loop.run_codex_iteration", return_value=codex_result
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
                            "next_task": "none",
                            "next_task_reason": "all goals complete",
                            "blockers": [],
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

    def test_parse_failure_triggers_finalize_hook_probe_and_done_lock(self):
        tmp, dev, _project_root = self._setup_project()
        try:
            paths = build_runner_state_paths(str(dev), "blog", "alpha")
            state = default_runner_state("blog", "alpha")
            state["enabled"] = True
            write_json(paths.state_file, state)

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
                            "next_task": "No further implementation work; ready to exit.",
                            "next_task_reason": "Completion confirmed by finalize hook.",
                            "blockers": [],
                            "done_candidate": True,
                        }
                    ),
                    "RUNNER_UPDATE_END",
                ],
            )

            with patch("src.runner_loop.create_runner_state", return_value={"ok": True}), patch(
                "src.runner_loop.run_codex_iteration",
                side_effect=[main_result, probe_result],
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
                            "next_task": "b",
                            "next_task_reason": "c",
                            "blockers": [],
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
