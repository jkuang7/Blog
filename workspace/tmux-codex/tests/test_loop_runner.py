import json
import os
import subprocess
import sys
import tempfile
import unittest
from itertools import repeat
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
    _submit_runner_prompt,
    build_runner_paths,
    detect_project_stack,
    ensure_gates_file,
    make_codex_exec_loop_script,
    run_interactive_runner_controller,
    run_loop_runner,
)
from src.runner_state import build_runner_state_paths, build_runner_state_paths_for_root, default_runner_state, write_json


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

    def test_parse_loop_invalid_complexity(self):
        with self.assertRaises(ValueError):
            parse_loop_args(["blog", "--complexity", "extreme"])


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
        self.assertIn('exec zsh -l', script)
        self.assertIn('-m gpt-5.1-codex', script)
        self.assertIn('reasoning.effort="high"', script)
        self.assertNotIn('python3 -m src.main __runner-loop', script)

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

    def test_interactive_runner_controller_dispatches_execute_only_run(self):
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
            tmux_instance.has_session.side_effect = [True, False]
            tmux_instance.capture_pane.return_value = "OpenAI Codex\n› Run /review on my current changes\n"
            tmux_instance.get_pane_process.return_value = "node"
            tmux_instance.clear_prompt_line.return_value = True
            tmux_instance.send_keys.return_value = True
            tmux_instance.press_enter.return_value = True
            tmux_instance.send_eof.return_value = True

            marker_path = paths.cycle_prepared_file
            marker_path.write_text("{}", encoding="utf-8")
            initial_marker_mtime = marker_path.stat().st_mtime

            with (
                patch("src.runner_loop.resolve_target_project_root", return_value=project_root),
                patch("src.runner_loop.TmuxClient", return_value=tmux_instance),
                patch("src.runner_loop.time.sleep", return_value=None),
            ):
                tmux_instance.has_session.side_effect = repeat(True)

                def capture_side_effect(*_args, **_kwargs):
                    count = tmux_instance.capture_pane.call_count
                    if count == 1:
                        return "OpenAI Codex\n› Run /review on my current changes\n"
                    if count == 2:
                        return (
                            "OpenAI Codex\n"
                            f"› /prompts:run_execute DEV={dev} PROJECT=blog RUNNER_ID=main "
                            f"PWD={project_root} PROJECT_ROOT={project_root} PHASE=implement\n"
                        )
                    if count == 3:
                        return "OpenAI Codex\n/prompts:run_execute send saved prompt\n"
                    if count == 4:
                        return "Running execute...\n"
                    if count == 5:
                        return "OpenAI Codex\n› \nphase_done=yes\nvalidation=pass\nexiting=yes\n"
                    if count == 6:
                        return "OpenAI Codex\n› \nphase_done=yes\nvalidation=pass\nexiting=yes\n"
                    if count == 7:
                        return (
                            "OpenAI Codex\n"
                            f"› /prompts:run_update DEV={dev} PROJECT=blog RUNNER_ID=main "
                            f"PWD={project_root} PROJECT_ROOT={project_root}\n"
                        )
                    if count == 8:
                        return "OpenAI Codex\n/prompts:run_update send saved prompt\n"
                    if count == 9:
                        marker_path.write_text('{"prepared_at":"later"}', encoding="utf-8")
                        os.utime(marker_path, (initial_marker_mtime + 5, initial_marker_mtime + 5))
                        return "Running update...\n"
                    return "OpenAI Codex\n› \nstate_refreshed=yes\nprepared_marker=yes\nexiting=yes\n"

                tmux_instance.capture_pane.side_effect = capture_side_effect
                tmux_instance.get_pane_process.side_effect = repeat("node")

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
            self.assertEqual(tmux_instance.clear_prompt_line.call_count, 3)
            first_command = tmux_instance.send_keys.call_args_list[0].args[1]
            second_command = tmux_instance.send_keys.call_args_list[1].args[1]
            self.assertIn("/prompts:run_execute", first_command)
            self.assertIn(f"DEV={dev}", first_command)
            self.assertIn("PROJECT=blog", first_command)
            self.assertIn(f"PROJECT_ROOT={project_root}", first_command)
            self.assertIn("RUNNER_ID=main", first_command)
            self.assertIn("PHASE=implement", first_command)
            self.assertIn("/prompts:run_update", second_command)
            self.assertEqual(tmux_instance.press_enter.call_count, 4)
            self.assertEqual(tmux_instance.send_eof.call_count, 2)

    def test_submit_runner_prompt_retries_after_empty_placeholder_expansion(self):
        tmux_instance = unittest.mock.Mock()
        command = (
            "/prompts:run_update "
            "DEV=/tmp/dev "
            "PROJECT=blog "
            "RUNNER_ID=main "
            "PWD=/tmp/dev/Repos/blog "
            "PROJECT_ROOT=/tmp/dev/Repos/blog"
        )
        tmux_instance.clear_prompt_line.return_value = True
        tmux_instance.send_keys.return_value = True
        tmux_instance.press_enter.side_effect = [True, True, True]
        tmux_instance.send_escape.return_value = True
        tmux_instance.capture_pane.side_effect = [
            f"OpenAI Codex\n› {command}\n",
            'OpenAI Codex\n/prompts:run_update DEV="" PROJECT="" RUNNER_ID="" PWD="" PROJECT_ROOT="" send saved prompt\n',
            f"OpenAI Codex\n› {command}\n",
            "OpenAI Codex\n/prompts:run_update send saved prompt\n",
        ]

        ok = _submit_runner_prompt(
            tmux=tmux_instance,
            session_name="runner-blog",
            command=command,
            settle_attempts=1,
            settle_delay_seconds=0,
        )

        self.assertTrue(ok)
        self.assertEqual(tmux_instance.send_keys.call_count, 2)
        first_kwargs = tmux_instance.send_keys.call_args_list[0].kwargs
        second_kwargs = tmux_instance.send_keys.call_args_list[1].kwargs
        self.assertTrue(first_kwargs["force_buffer"])
        self.assertTrue(second_kwargs["force_buffer"])
        self.assertEqual(tmux_instance.press_enter.call_count, 3)
        self.assertEqual(tmux_instance.send_escape.call_count, 2)


class LoopPromptTests(unittest.TestCase):
    def test_prompt_mentions_canonical_runner_state_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_runner_state_paths(tmp, "blog", "main")
            prompt = _build_prompt(project="blog", runner_id="main", paths=paths)

        self.assertIn(f"Runner state file: {paths.state_file}", prompt)
        self.assertIn("Use .memory/runner/RUNNER_EXEC_CONTEXT.json plus .memory/runner/RUNNER_HANDOFF.md and runner state to respect the current phase goal and next task.", prompt)


class LoopLoggingTests(unittest.TestCase):
    def test_log_line_keeps_console_output_unstamped(self):
        with tempfile.TemporaryDirectory() as tmp:
            message = "Iteration 1 running gpt-5.3-codex"

            with patch.dict("os.environ", {"HOME": tmp}):
                paths = build_runner_state_paths(tmp, "blog", "main")
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
