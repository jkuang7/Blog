import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RunDocsContractTests(unittest.TestCase):
    def test_run_spec_mentions_dynamic_gate_expansion(self):
        text = (ROOT / "run.md").read_text(encoding="utf-8")
        self.assertIn("may add or tighten validation gates", text)
        self.assertIn("extra validation prompt run", text)
        self.assertIn(".memory/RUNNER_DONE.lock", text)
        self.assertIn("/prompts:runner-discover ... MODE=execute_only", text)
        self.assertIn("/prompts:runner-implement ... MODE=execute_only", text)
        self.assertIn("/prompts:run ... MODE=execute_only PHASE=<phase>", text)
        self.assertIn("TASKS.json", text)
        self.assertIn("RUNNER_WATCHDOG.json", text)
        self.assertIn("RUNNER_CYCLE_PREPARED.json", text)
        self.assertIn("RUNNER_TASK_INTAKE.json", text)
        self.assertIn("runctl --quiet", text)
        self.assertIn("runctl --task add", text)
        self.assertIn("runctl --task queue", text)
        self.assertIn("/prompts:add <task>", text)
        self.assertIn("Do not allow a read-only inspection cycle to hand off.", text)
        self.assertIn("do not refresh + prepare + exit with identical task wording", text)
        self.assertIn("deprecated legacy view files", text)
        self.assertNotIn("GOALS.md is generated", text)
        self.assertIn("phase-scoped runner state", text)

    def test_run_prompt_is_setup_only_and_mentions_done_lock(self):
        text = (ROOT / "prompts" / "run.md").read_text(encoding="utf-8")
        self.assertIn("runctl setup/clear", text)
        self.assertIn("Do not execute implementation work here", text)
        self.assertIn(".memory/RUNNER_DONE.lock", text)
        self.assertIn("/prompts:runner-discover", text)
        self.assertIn("PHASE=<phase>", text)
        self.assertIn("context_sources", text)
        self.assertIn("RUNNER_TASK_INTAKE.json", text)
        self.assertIn("runctl --task add", text)
        self.assertNotIn("REFRACTOR_STATUS.md", text)

    def test_add_prompt_exists_and_queues_runner_task_intake(self):
        text = (ROOT / "prompts" / "add.md").read_text(encoding="utf-8")
        self.assertIn("runctl --task add", text)
        self.assertIn("RUNNER_STATE.json", text)
        self.assertIn("TASKS.json", text)
        self.assertIn("Do not create or refresh runner setup from `/add`.", text)
        self.assertIn("`--allow-preempt` only if the human explicitly asks", text)
        self.assertIn("If more than one plausible runner root exists, ask the human", text)

    def test_runner_cycle_prompt_mentions_prepare_marker_and_exit(self):
        text = (ROOT / "prompts" / "runner-cycle.md").read_text(encoding="utf-8")
        self.assertIn("RUNNER_CYCLE_PREPARED.json", text)
        self.assertIn("runctl --prepare-cycle", text)
        self.assertIn("--quiet", text)
        self.assertIn("Fail closed on zero-progress", text)
        self.assertIn("do not write `RUNNER_CYCLE_PREPARED.json` if `next_task_id` / `next_task` would be handed back unchanged", text)
        self.assertIn("update `TASKS.json` first so the next handoff is narrower and concrete", text)
        self.assertIn("Terminate this Codex chat session immediately", text)
        self.assertIn('"phase": "implement"', text)

    def test_phase_prompts_exist_and_reference_exec_context(self):
        for prompt_name in ("runner-discover.md", "runner-implement.md", "runner-verify.md", "runner-closeout.md"):
            text = (ROOT / "prompts" / prompt_name).read_text(encoding="utf-8")
            self.assertIn("RUNNER_EXEC_CONTEXT.json", text)
            self.assertIn("RUNNER_CYCLE_PREPARED.json", text)

    def test_install_script_links_add_prompt(self):
        text = (ROOT / "scripts" / "install-codex-run-prompt.sh").read_text(encoding="utf-8")
        self.assertIn("run add runner-cycle", text)


if __name__ == "__main__":
    unittest.main()
