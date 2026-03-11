"""Codex infinite loop runner helpers and orchestration."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from .codex_engine import CodexRunResult, run_codex_iteration
from .hooks import HookAdapter, LocalHooks, load_agents_bridge
from .runctl import create_runner_state, resolve_target_project_root
from .runner_state import (
    RunnerStatePaths,
    append_ndjson,
    build_phase_prompt_commands,
    coerce_runner_phase,
    build_runner_state_paths_for_root,
    count_open_tasks,
    detect_git_context,
    ensure_memory_dir,
    load_or_init_state,
    read_json,
    update_state,
    utc_now,
    write_json,
)

COMPLEXITY_PROFILE_MAP = {
    "low": ("gpt-5.3-codex", "low"),
    "med": ("gpt-5.3-codex", "medium"),
    "high": ("gpt-5.3-codex", "high"),
    "xhigh": ("gpt-5.3-codex", "xhigh"),
}

ProjectStack = Literal["pnpm", "npm", "python_pyproject", "python_requirements", "go", "cargo", "unknown"]
RUNNER_UPDATE_START_MARKER = "RUNNER_UPDATE_START"
RUNNER_UPDATE_END_MARKER = "RUNNER_UPDATE_END"
REQUIRED_UPDATE_KEYS = (
    "summary",
    "completed",
    "next_task",
    "next_task_reason",
    "blockers",
    "done_candidate",
)
DONE_SIGNAL_TRUE_PATTERNS = (
    re.compile(r"\ball requested (changes|work) (are )?(complete|completed)\b", re.IGNORECASE),
    re.compile(r"\b(all )?implementation (is )?(complete|completed)\b", re.IGNORECASE),
    re.compile(r"\bno (remaining|further) (tasks|changes|work)\b", re.IGNORECASE),
    re.compile(r"\bnothing left to (do|implement|change)\b", re.IGNORECASE),
    re.compile(r"\bready for (handoff|merge|review)\b", re.IGNORECASE),
)
DONE_SIGNAL_FALSE_PATTERNS = (
    re.compile(r"\b(not|isn't|is not) (done|complete)\b", re.IGNORECASE),
    re.compile(r"\b(incomplete|partial)\b", re.IGNORECASE),
    re.compile(r"\b(next task|next step|todo|to do|follow[- ]up)\b", re.IGNORECASE),
)


@dataclass(frozen=True)
class RunnerPaths:
    """Compatibility wrapper around codex runner state paths."""

    state: RunnerStatePaths

    @property
    def memory_dir(self) -> Path:
        return self.state.memory_dir

    @property
    def complete_lock(self) -> Path:
        return self.state.done_lock

    @property
    def stop_file(self) -> Path:
        return self.state.stop_lock

    @property
    def active_lock(self) -> Path:
        return self.state.active_lock

    @property
    def state_file(self) -> Path:
        return self.state.state_file

    @property
    def audit_file(self) -> Path:
        return self.state.ledger_file

    @property
    def gates_file(self) -> Path:
        return self.state.gates_file

    @property
    def runner_log(self) -> Path:
        return self.state.runner_log

    @property
    def runners_log(self) -> Path:
        return self.state.runners_log


def resolve_runner_profile(complexity: str, model_override: str | None) -> tuple[str, str]:
    """Resolve model + reasoning effort from complexity preset."""
    if complexity not in COMPLEXITY_PROFILE_MAP:
        allowed = ", ".join(sorted(COMPLEXITY_PROFILE_MAP))
        raise ValueError(f"Invalid complexity '{complexity}'. Use one of: {allowed}")

    mapped_model, effort = COMPLEXITY_PROFILE_MAP[complexity]
    if model_override:
        return model_override, effort
    return mapped_model, effort


def build_runner_paths(dev: str, project: str, runner_id: str) -> RunnerPaths:
    """Create runner-specific path layout."""
    project_root = resolve_target_project_root(
        dev=dev,
        project=project,
        runner_id=runner_id,
    )
    return RunnerPaths(
        state=build_runner_state_paths_for_root(
            project_root=project_root,
            dev=dev,
            project=project,
            runner_id=runner_id,
        )
    )


def detect_project_stack(project_root: Path) -> ProjectStack:
    """Detect project stack from marker files."""
    if (project_root / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (project_root / "package-lock.json").exists() or (project_root / "package.json").exists():
        return "npm"
    if (project_root / "pyproject.toml").exists():
        return "python_pyproject"
    if (project_root / "requirements.txt").exists():
        return "python_requirements"
    if (project_root / "go.mod").exists():
        return "go"
    if (project_root / "Cargo.toml").exists():
        return "cargo"
    return "unknown"


def render_gates_template(stack: ProjectStack) -> str:
    """Render gates.sh template for the detected stack."""
    project_cd = 'cd "$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"'

    if stack == "pnpm":
        return f"""#!/usr/bin/env bash

run_gates() {{
    set -euo pipefail
    {project_cd}

    command -v node >/dev/null 2>&1 || {{ echo \"run_gates: node is required\"; return 1; }}
    command -v pnpm >/dev/null 2>&1 || {{ echo \"run_gates: pnpm is required\"; return 1; }}
    [[ -f package.json ]] || {{ echo \"run_gates: package.json not found\"; return 1; }}

    for script_name in lint test build; do
        node -e \"const fs=require('fs'); const p=JSON.parse(fs.readFileSync('package.json','utf8')); process.exit(p.scripts && p.scripts['$script_name'] ? 0 : 1);\" >/dev/null 2>&1 || {{
            echo \"run_gates: missing required package.json script: $script_name\"
            return 1
        }}
    done

    pnpm run lint
    pnpm run test
    pnpm run build
}}
"""

    if stack == "npm":
        return f"""#!/usr/bin/env bash

run_gates() {{
    set -euo pipefail
    {project_cd}

    command -v node >/dev/null 2>&1 || {{ echo \"run_gates: node is required\"; return 1; }}
    command -v npm >/dev/null 2>&1 || {{ echo \"run_gates: npm is required\"; return 1; }}
    [[ -f package.json ]] || {{ echo \"run_gates: package.json not found\"; return 1; }}

    for script_name in lint test build; do
        node -e \"const fs=require('fs'); const p=JSON.parse(fs.readFileSync('package.json','utf8')); process.exit(p.scripts && p.scripts['$script_name'] ? 0 : 1);\" >/dev/null 2>&1 || {{
            echo \"run_gates: missing required package.json script: $script_name\"
            return 1
        }}
    done

    npm run lint
    npm run test
    npm run build
}}
"""

    if stack in ("python_pyproject", "python_requirements"):
        return f"""#!/usr/bin/env bash

run_gates() {{
    set -euo pipefail
    {project_cd}

    command -v pytest >/dev/null 2>&1 || {{ echo \"run_gates: pytest is required\"; return 1; }}

    if command -v ruff >/dev/null 2>&1; then
        ruff check .
    elif command -v flake8 >/dev/null 2>&1; then
        flake8 .
    else
        echo \"run_gates: install ruff or flake8 before marking complete\"
        return 1
    fi

    pytest
}}
"""

    if stack == "go":
        return f"""#!/usr/bin/env bash

run_gates() {{
    set -euo pipefail
    {project_cd}

    command -v go >/dev/null 2>&1 || {{ echo \"run_gates: go is required\"; return 1; }}

    go test ./...
    go vet ./...
}}
"""

    if stack == "cargo":
        return f"""#!/usr/bin/env bash

run_gates() {{
    set -euo pipefail
    {project_cd}

    command -v cargo >/dev/null 2>&1 || {{ echo \"run_gates: cargo is required\"; return 1; }}

    cargo test
    cargo clippy -- -D warnings
}}
"""

    return f"""#!/usr/bin/env bash

run_gates() {{
    set -euo pipefail
    {project_cd}
    echo "run_gates: unknown project stack"
    echo "TODO: edit .memory/gates.sh and implement build/lint/test/smoke checks"
    return 1
}}
"""


def ensure_gates_file(
    dev: str,
    project: str,
    runner_id: str = "main",
    project_root: Path | None = None,
) -> tuple[Path, bool]:
    """Create stack-aware gates.sh only when missing."""
    resolved_root = project_root or resolve_target_project_root(
        dev=dev,
        project=project,
        runner_id=runner_id,
    )
    memory_dir = resolved_root / ".memory"
    gates_file = memory_dir / "gates.sh"
    lessons_file = memory_dir / "lessons.md"

    memory_dir.mkdir(parents=True, exist_ok=True)
    if not lessons_file.exists():
        lessons_file.write_text("# Lessons\n\n")

    if gates_file.exists():
        return gates_file, False

    stack = detect_project_stack(resolved_root)
    gates_file.write_text(render_gates_template(stack))
    os.chmod(gates_file, 0o755)
    return gates_file, True


def _repo_home() -> Path:
    override = os.environ.get("TMUX_CLI_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


def make_codex_chat_loop_script(
    dev: str,
    project: str,
    runner_id: str,
    session_name: str,
    model: str,
    reasoning_effort: str,
    hil_mode: str,
    paths: RunnerPaths,
) -> str:
    """Generate wrapper script for interactive Codex chat UI.

    The pane stays chat-first (same UX class as `n=new`).
    In interactive-watchdog mode this script is intentionally single-run:
    watchdog owns respawn/restart and iteration handoff.
    """
    project_root = paths.memory_dir.parent
    repo_home = _repo_home()
    q_dev = shlex.quote(dev)
    q_model = shlex.quote(model)
    q_reasoning = shlex.quote(f'reasoning.effort="{reasoning_effort}"')
    q_project = shlex.quote(project)
    q_runner_id = shlex.quote(runner_id)
    q_runner_log = shlex.quote(str(paths.runner_log))
    q_stop_lock = shlex.quote(str(paths.stop_file))
    q_done_lock = shlex.quote(str(paths.complete_lock))
    q_project_root = shlex.quote(str(project_root))
    state = read_json(paths.state_file) or {}
    phase = coerce_runner_phase(state.get("current_phase"), default="discover")
    execute_only_cmd = build_phase_prompt_commands(
        dev=dev,
        project=project,
        runner_id=runner_id,
        project_root=project_root,
        phase=phase,
    )[0]
    q_execute_only_cmd = execute_only_cmd.replace('"', '\\"')
    return f'''\
cd {q_project_root} || exit 1
export TMUX_CLI_HOME="${{TMUX_CLI_HOME:-{repo_home}}}"
RUNNER_LOG={q_runner_log}
PROJECT_ROOT={q_project_root}
STOP_LOCK={q_stop_lock}
DONE_LOCK={q_done_lock}
mkdir -p "$(dirname "$RUNNER_LOG")"
TARGET_BRANCH=""
if [[ -f "$PROJECT_ROOT/.memory/runner/RUNNER_STATE.json" ]]; then
  TARGET_BRANCH="$(sed -n 's/.*"git_branch":[[:space:]]*"\\([^"]*\\)".*/\\1/p' "$PROJECT_ROOT/.memory/runner/RUNNER_STATE.json" | head -n 1)"
fi
if [[ -n "$TARGET_BRANCH" ]]; then
  CURRENT_BRANCH="$(git -C "$PROJECT_ROOT" branch --show-current 2>/dev/null || true)"
  if [[ "$CURRENT_BRANCH" != "$TARGET_BRANCH" ]]; then
    git -C "$PROJECT_ROOT" checkout "$TARGET_BRANCH" >/dev/null 2>&1 || true
  fi
fi
log_supervisor() {{
  echo "[$(date +%H:%M:%S)] $1" | tee -a "$RUNNER_LOG"
}}

if [[ -f "$STOP_LOCK" ]]; then
  log_supervisor "manual stop lock detected; exiting interactive runner pane bootstrap"
  rm -f "$STOP_LOCK"
  echo ""
  echo "[Session idle - detach with Ctrl+B D]"
  exec zsh -l
fi
if [[ -f "$DONE_LOCK" ]]; then
  log_supervisor "done lock detected; exiting interactive runner pane bootstrap"
  echo ""
  echo "[Session idle - detach with Ctrl+B D]"
  exec zsh -l
fi

log_supervisor "starting interactive runner chat for {q_project} ({hil_mode})"
log_supervisor "watchdog seeds {q_execute_only_cmd} when idle"
codex --search --dangerously-bypass-approvals-and-sandbox -m {q_model} -c {q_reasoning}
codex_rc=$?
log_supervisor "interactive codex session exited rc=$codex_rc"

echo ""
echo "[Session idle - detach with Ctrl+B D]"
exec zsh -l
'''


def make_codex_exec_loop_script(
    dev: str,
    project: str,
    runner_id: str,
    model: str,
    reasoning_effort: str,
    hil_mode: str,
    paths: RunnerPaths,
) -> str:
    """Generate legacy exec-loop launcher (deprecated compatibility mode)."""
    repo_home = _repo_home()
    q_dev = shlex.quote(dev)
    q_project = shlex.quote(project)
    q_model = shlex.quote(model)
    q_reasoning = shlex.quote(f'reasoning.effort="{reasoning_effort}"')
    q_hil_mode = shlex.quote(hil_mode)
    q_runner_log = shlex.quote(str(paths.runner_log))
    q_stop_lock = shlex.quote(str(paths.stop_file))
    q_done_lock = shlex.quote(str(paths.complete_lock))
    project_root = paths.memory_dir.parent
    q_project_root = shlex.quote(str(project_root))
    runctl_cmd = (
        f"python3 {repo_home / 'bin' / 'runctl'} "
        f"--setup --project-root {project_root} --runner-id {runner_id}"
    )
    q_runner_prompt = shlex.quote(
        "Continue one autonomous runner cycle. "
        f"Project: {project}. "
        f"Worktree root: {project_root}. "
        "Slash commands like /run are unavailable in this non-interactive cycle. "
        "Use the equivalent setup command only if runner state is missing or unreadable: "
        f"{runctl_cmd}. "
        "Then read .memory/runner/RUNNER_STATE.json, .memory/runner/TASKS.json, and .memory/lessons.md, "
        "execute exactly one meaningful validated step from next_task, and provide concise verification evidence."
    )
    return f'''\
cd {q_dev} || exit 1
export TMUX_CLI_HOME="${{TMUX_CLI_HOME:-{repo_home}}}"
RUNNER_LOG={q_runner_log}
STOP_LOCK={q_stop_lock}
DONE_LOCK={q_done_lock}
RUNNER_PROMPT={q_runner_prompt}
PROJECT_ROOT={q_project_root}
mkdir -p "$(dirname "$RUNNER_LOG")"
log_supervisor() {{
  echo "[$(date +%H:%M:%S)] $1" | tee -a "$RUNNER_LOG"
}}

while true; do
  if [[ -f "$STOP_LOCK" ]]; then
    log_supervisor "manual stop lock detected; exiting interactive runner loop"
    rm -f "$STOP_LOCK"
    break
  fi
  if [[ -f "$DONE_LOCK" ]]; then
    log_supervisor "done lock detected; exiting interactive runner loop"
    break
  fi

  TARGET_BRANCH=""
  if [[ -f "$PROJECT_ROOT/.memory/runner/RUNNER_STATE.json" ]]; then
    TARGET_BRANCH="$(sed -n 's/.*"git_branch":[[:space:]]*"\\([^"]*\\)".*/\\1/p' "$PROJECT_ROOT/.memory/runner/RUNNER_STATE.json" | head -n 1)"
  fi
  if [[ -n "$TARGET_BRANCH" ]]; then
    CURRENT_BRANCH="$(git -C "$PROJECT_ROOT" branch --show-current 2>/dev/null || true)"
    if [[ "$CURRENT_BRANCH" != "$TARGET_BRANCH" ]]; then
      git -C "$PROJECT_ROOT" checkout "$TARGET_BRANCH" >/dev/null 2>&1 || true
    fi
  fi

  log_supervisor "starting interactive runner chat for {q_project} ({q_hil_mode})"
  codex --search --dangerously-bypass-approvals-and-sandbox exec -C "$PROJECT_ROOT" -m {q_model} -c {q_reasoning} "$RUNNER_PROMPT"
  codex_rc=$?
  log_supervisor "interactive codex session exited rc=$codex_rc"

  if [[ -f "$STOP_LOCK" || -f "$DONE_LOCK" ]]; then
    log_supervisor "runner lock detected after codex exit; stopping restart loop"
    [[ -f "$STOP_LOCK" ]] && rm -f "$STOP_LOCK"
    break
  fi

  log_supervisor "restarting interactive runner chat in 3s"
  sleep 3
done

echo ""
echo "[Session idle - detach with Ctrl+B D]"
exec zsh -l
'''


def _validate_gates_contract(gates_file: Path) -> tuple[bool, str]:
    if not gates_file.exists():
        return False, f"Missing gates file: {gates_file}"

    cmd = f'set -euo pipefail; source "{gates_file}"; declare -F run_gates >/dev/null'
    result = subprocess.run(["bash", "-lc", cmd], capture_output=True, text=True)
    if result.returncode != 0:
        return False, f"gates.sh must define run_gates: {gates_file}"
    return True, ""


def _run_gates(gates_file: Path, project_root: Path, runner_id: str) -> tuple[bool, str]:
    cmd = f'set -euo pipefail; cd "{project_root}"; source "{gates_file}"; run_gates'
    env = os.environ.copy()
    env["RUNNER_ID"] = runner_id
    env["MEMORY_DIR"] = str(project_root / ".memory")
    result = subprocess.run(["bash", "-lc", cmd], capture_output=True, text=True, env=env)
    if result.returncode == 0:
        return True, result.stdout.strip()
    output = (result.stdout or "") + (result.stderr or "")
    return False, output.strip()


def _extract_open_tasks(tasks_file: Path, max_items: int = 40) -> list[str]:
    payload = read_json(tasks_file)
    if not isinstance(payload, dict):
        return []
    tasks = payload.get("tasks")
    if not isinstance(tasks, list):
        return []

    items: list[str] = []
    for raw in tasks:
        if not isinstance(raw, dict):
            continue
        status = str(raw.get("status", "")).strip().lower()
        if status not in {"open", "in_progress", "blocked"}:
            continue
        task_id = str(raw.get("task_id", "")).strip() or "(id)"
        title = str(raw.get("title", "")).strip() or "(untitled)"
        items.append(f"{task_id}: {title}"[:220])
        if len(items) >= max_items:
            break
    return items


def _append_ledger(paths: RunnerStatePaths, event: str, **payload: object) -> None:
    append_ndjson(paths.ledger_file, {"ts": utc_now(), "event": event, **payload})


def _mark_runner_end_time(paths: RunnerStatePaths, session_name: str) -> None:
    """Backfill end timestamp for the active session in runners.log."""
    if not paths.runners_log.exists():
        return

    end_ts = int(time.time())
    lines: list[str] = []
    for line in paths.runners_log.read_text().splitlines():
        parts = line.split(",")
        if len(parts) >= 2 and parts[0] == session_name and (len(parts) < 3 or not parts[2]):
            lines.append(f"{parts[0]},{parts[1]},{end_ts}")
        else:
            lines.append(line)
    paths.runners_log.write_text("\n".join(lines) + ("\n" if lines else ""))


def _log_line(paths: RunnerStatePaths, message: str) -> None:
    stamped = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
    paths.runner_log.parent.mkdir(parents=True, exist_ok=True)
    with paths.runner_log.open("a", encoding="utf-8") as handle:
        handle.write(stamped + "\n")
    # Keep file logs timestamped but preserve a CLI-like pane view.
    print(message, flush=True)


def _build_prompt(project: str, runner_id: str, paths: RunnerStatePaths) -> str:
    state = read_json(paths.state_file) or {}

    def _compact_list(raw: object, *, limit: int = 3, item_chars: int = 140) -> list[str]:
        if not isinstance(raw, list):
            return []
        compact: list[str] = []
        for item in raw:
            text = str(item).strip()
            if not text:
                continue
            compact.append(text[:item_chars])
            if len(compact) >= limit:
                break
        return compact

    plan_items = _compact_list(state.get("implementation_plan"), limit=2, item_chars=160)
    completed_items = _compact_list(state.get("completed_recent"), limit=3, item_chars=120)
    blocker_items = _compact_list(state.get("blockers"), limit=3, item_chars=120)
    snapshot = {
        "objective_id": str(state.get("objective_id", "")).strip(),
        "next_task_id": str(state.get("next_task_id", "")).strip(),
        "current_goal": str(state.get("current_goal", "")).strip(),
        "next_task": str(state.get("next_task", "")).strip(),
        "next_task_reason": str(state.get("next_task_reason", "")).strip(),
        "implementation_plan_top2": plan_items,
        "completed_recent_top3": completed_items,
        "blockers_top3": blocker_items,
        "last_iteration_summary": str(state.get("last_iteration_summary", "")).strip(),
        "done_candidate": bool(state.get("done_candidate", False)),
        "done_gate_status": str(state.get("done_gate_status", "pending")).strip(),
        "status": str(state.get("status", "ready")).strip(),
    }
    state_context = json.dumps(snapshot, indent=2)

    return f"""Continue work for project {project}.

Completion protocol:
- Runner ID: {runner_id}
- Run scope: /run {project} --runner-id {runner_id}
- Runner state file: {paths.state_file}
- Done lock path: {paths.done_lock}
- Stop lock path: {paths.stop_lock}
- Gates file: {paths.gates_file}

Rules:
1. Execute exactly one meaningful unit of progress this iteration.
2. Update code and tests as needed.
3. Do not create the done lock early.
4. Before creating done lock, source .memory/gates.sh and run run_gates.
5. Only create done lock if run_gates succeeds and .memory/runner/TASKS.json has zero tasks in open|in_progress|blocked.
6. Use .memory/runner/RUNNER_EXEC_CONTEXT.json plus runner state to respect the current phase goal and next task.
7. End your response with this exact machine-parsable block:
RUNNER_UPDATE_START
{{"summary":"...","completed":["..."],"next_task":"...","next_task_reason":"...","blockers":["..."],"done_candidate":false}}
RUNNER_UPDATE_END
8. The JSON must be valid and include all required keys exactly once.

Runner state snapshot:
{state_context}
"""


def _extract_update_payload(raw_lines: list[str]) -> tuple[dict[str, object] | None, str | None]:
    """Parse the final deterministic RUNNER_UPDATE payload block."""
    start_idx = -1
    end_idx = -1
    for idx, raw in enumerate(raw_lines):
        line = raw.strip()
        if line == RUNNER_UPDATE_START_MARKER:
            start_idx = idx
            end_idx = -1
            continue
        if line == RUNNER_UPDATE_END_MARKER and start_idx != -1:
            end_idx = idx

    if start_idx == -1 or end_idx == -1 or end_idx <= start_idx + 1:
        return None, "missing RUNNER_UPDATE_START/END block"

    payload_text = "\n".join(raw_lines[start_idx + 1 : end_idx]).strip()
    if not payload_text:
        return None, "empty runner update payload"

    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        return None, f"invalid runner update json: {exc}"

    if not isinstance(payload, dict):
        return None, "runner update payload must be a JSON object"

    for key in REQUIRED_UPDATE_KEYS:
        if key not in payload:
            return None, f"runner update missing required key: {key}"

    summary = payload.get("summary")
    next_task = payload.get("next_task")
    next_task_reason = payload.get("next_task_reason")
    completed = payload.get("completed")
    blockers = payload.get("blockers")
    done_candidate = payload.get("done_candidate")

    if not isinstance(summary, str) or not summary.strip():
        return None, "runner update summary must be a non-empty string"
    if not isinstance(next_task, str) or not next_task.strip():
        return None, "runner update next_task must be a non-empty string"
    if not isinstance(next_task_reason, str) or not next_task_reason.strip():
        return None, "runner update next_task_reason must be a non-empty string"
    if not isinstance(done_candidate, bool):
        return None, "runner update done_candidate must be a boolean"
    if not isinstance(completed, list) or not all(isinstance(item, str) for item in completed):
        return None, "runner update completed must be a string array"
    if not isinstance(blockers, list) or not all(isinstance(item, str) for item in blockers):
        return None, "runner update blockers must be a string array"

    normalized: dict[str, object] = {
        "summary": summary.strip(),
        "completed": [item.strip() for item in completed if item.strip()],
        "next_task": next_task.strip(),
        "next_task_reason": next_task_reason.strip(),
        "blockers": [item.strip() for item in blockers if item.strip()],
        "done_candidate": done_candidate,
    }
    return normalized, None


def _infer_done_candidate_from_text(final_message: str, raw_lines: list[str]) -> bool:
    text = "\n".join([final_message, *raw_lines[-40:]])
    if any(pattern.search(text) for pattern in DONE_SIGNAL_FALSE_PATTERNS):
        return False
    return any(pattern.search(text) for pattern in DONE_SIGNAL_TRUE_PATTERNS)


def _build_finalize_hook_prompt(
    final_message: str,
    parse_error: str,
    state: dict[str, object],
) -> str:
    safe_message = final_message.strip() or "(no final message)"
    current_next = str(state.get("next_task", "")).strip() or "Define the next smallest validated change."
    current_reason = str(state.get("next_task_reason", "")).strip() or "Carry forward prior plan context."
    return f"""The previous runner response failed strict parsing.

Parse failure:
{parse_error}

Last assistant message:
{safe_message}

Return ONLY this block format:
RUNNER_UPDATE_START
{{"summary":"...","completed":["..."],"next_task":"...","next_task_reason":"...","blockers":["..."],"done_candidate":false}}
RUNNER_UPDATE_END

Rules:
- Keep JSON valid with all required keys.
- If the work is clearly complete, set "done_candidate": true.
- If uncertain, set "done_candidate": false.
- Preserve continuity with current next task when no better signal exists:
  next_task="{current_next}"
  next_task_reason="{current_reason}"
"""


def _fallback_update_payload(
    state: dict[str, object],
    final_message: str,
    parse_error: str,
    probe_error: str | None,
    raw_lines: list[str],
) -> dict[str, object]:
    summary = final_message.strip() or "Iteration completed without structured runner update."
    done_candidate = _infer_done_candidate_from_text(final_message=final_message, raw_lines=raw_lines)
    prior_completed = state.get("completed_recent", [])
    completed: list[str] = []
    if isinstance(prior_completed, list):
        completed = [str(item).strip() for item in prior_completed if str(item).strip()]

    blockers: list[str] = []
    prior_blockers = state.get("blockers", [])
    if isinstance(prior_blockers, list):
        blockers.extend([str(item).strip() for item in prior_blockers if str(item).strip()])
    blockers.append(f"runner update parse fallback used: {parse_error}")
    if probe_error:
        blockers.append(f"finalize hook parse failure: {probe_error}")

    if done_candidate:
        next_task = "No further implementation work; completion candidate pending gate validation."
        next_task_reason = "Completion inferred from final assistant output; gates will confirm."
    else:
        next_task = str(state.get("next_task", "")).strip() or "Define the next smallest validated change."
        next_task_reason = str(state.get("next_task_reason", "")).strip() or (
            "Structured runner update missing; carrying forward previous next step."
        )

    return {
        "summary": summary,
        "completed": completed,
        "next_task": next_task,
        "next_task_reason": next_task_reason,
        "blockers": blockers,
        "done_candidate": done_candidate,
    }


def _resolve_iteration_update(
    paths: RunnerStatePaths,
    project_root: Path,
    model: str,
    reasoning_effort: str,
    state: dict[str, object],
    result: CodexRunResult,
    iteration: int,
) -> tuple[dict[str, object], str, str | None, str | None]:
    update_payload, parse_error = _extract_update_payload(result.raw_lines)
    finalize_mode = "direct"
    finalize_probe_error: str | None = None
    if parse_error is None and update_payload is not None:
        return update_payload, finalize_mode, None, None

    _append_ledger(paths, "iteration.update_parse_failed", iteration=iteration, error=parse_error)
    _log_line(paths, f"Runner update parse failed: {parse_error}")
    finalize_mode = "probe"
    _append_ledger(paths, "iteration.finalize_probe.start", iteration=iteration)
    probe_result = run_codex_iteration(
        cwd=project_root,
        model=model,
        prompt=_build_finalize_hook_prompt(
            final_message=result.final_message,
            parse_error=str(parse_error),
            state=state,
        ),
        session_id=None,
        reasoning_effort=reasoning_effort,
        json_stream=False,
        logger=lambda line: _log_line(paths, f"[finalize-hook] {line}"),
    )
    _append_ledger(
        paths,
        "iteration.finalize_probe.finish",
        iteration=iteration,
        exit_code=probe_result.exit_code,
        final_message=probe_result.final_message,
    )
    probe_payload, probe_error = _extract_update_payload(probe_result.raw_lines)
    if probe_payload is not None:
        return probe_payload, finalize_mode, None, None

    finalize_mode = "fallback"
    finalize_probe_error = probe_error
    fallback_payload = _fallback_update_payload(
        state=state,
        final_message=result.final_message,
        parse_error=str(parse_error),
        probe_error=probe_error,
        raw_lines=result.raw_lines,
    )
    _append_ledger(
        paths,
        "iteration.update_fallback_used",
        iteration=iteration,
        parse_error=parse_error,
        probe_error=probe_error,
        inferred_done=bool(fallback_payload.get("done_candidate", False)),
    )
    return fallback_payload, finalize_mode, parse_error, finalize_probe_error


def _apply_iteration_update(
    paths: RunnerStatePaths,
    state: dict[str, object],
    update_payload: dict[str, object],
    hil_mode: str,
    session_id: str | None,
) -> dict[str, object]:
    completed_recent = list(update_payload.get("completed", []))
    blockers = list(update_payload.get("blockers", []))
    next_task = str(update_payload.get("next_task", "")).strip()
    next_task_reason = str(update_payload.get("next_task_reason", "")).strip()
    summary = str(update_payload.get("summary", "")).strip()
    done_candidate = bool(update_payload.get("done_candidate", False))
    state = update_state(
        paths.state_file,
        state,
        session_id=session_id,
        current_goal=next_task,
        last_iteration_summary=summary,
        completed_recent=completed_recent,
        next_task=next_task,
        next_task_reason=next_task_reason,
        blockers=blockers,
        done_candidate=done_candidate,
        done_gate_status="pending",
        runtime_policy={
            "hil_mode": hil_mode.replace("-", "_"),
            "session_mode": "phase_persistent",
        },
    )
    return state


def run_loop_runner(
    dev: str,
    project: str,
    runner_id: str,
    model: str,
    session_name: str,
    reasoning_effort: str = "high",
    hil_mode: str = "setup-only",
    backoff_seconds: int = 2,
) -> int:
    """Run codex loop until stop/done criteria are met."""
    if hil_mode != "setup-only":
        print(f"ERROR: invalid hil mode: {hil_mode}")
        return 2

    project_root = resolve_target_project_root(
        dev=dev,
        project=project,
        runner_id=runner_id,
    )
    if not project_root.exists():
        print(f"ERROR: project not found: {project_root}")
        return 2

    ensure_gates_file(
        dev=dev,
        project=project,
        runner_id=runner_id,
        project_root=project_root,
    )

    paths = build_runner_state_paths_for_root(
        project_root=project_root,
        dev=dev,
        project=project,
        runner_id=runner_id,
    )
    ensure_memory_dir(paths)

    create_result = create_runner_state(
        dev=dev,
        project=project,
        runner_id=runner_id,
        approve_enable=None,
        project_root=project_root,
    )
    if not create_result.get("ok"):
        print(f"ERROR: {create_result.get('error')}")
        return 2

    state = load_or_init_state(paths=paths, project=project, runner_id=runner_id)
    state = update_state(
        paths.state_file,
        state,
        **detect_git_context(project_root),
        runtime_policy={
            "hil_mode": hil_mode.replace("-", "_"),
            "session_mode": "phase_persistent",
        },
    )

    ok, err = _validate_gates_contract(paths.gates_file)
    if not ok:
        _append_ledger(paths, "runner.invalid_gates", message=err)
        _log_line(paths, f"ERROR {err}")
        update_state(paths.state_file, state, status="invalid_gates")
        return 2

    if not bool(state.get("enabled")):
        token = create_result.get("enable_token")
        pending = create_result.get("enable_pending_file")
        _append_ledger(paths, "runner.blocked_enable", token=token, pending_file=pending)
        _log_line(paths, "Runner disabled until HIL enable approval is provided.")
        if pending and token:
            runctl_path = _repo_home() / "bin" / "runctl"
            _log_line(
                paths,
                (
                    f"Run: python3 {runctl_path} --setup --project-root {project_root} "
                    f"--runner-id {runner_id} --approve-enable {token}"
                ),
            )
        update_state(paths.state_file, state, status="blocked_enable")
        return 3

    handlers = [LocalHooks(memory_dir=paths.runner_dir, hooks_log=paths.hooks_log)]
    agents = load_agents_bridge()
    if agents is not None:
        handlers.append(agents)
    hooks = HookAdapter(handlers)

    _append_ledger(
        paths,
        "runner.start",
        model=model,
        reasoning_effort=reasoning_effort,
        hil_mode=hil_mode,
        session_name=session_name,
    )
    hooks.emit(
        "on_start",
        utc_now(),
        project,
        runner_id,
        int(state.get("iteration", 0)),
        {"model": model, "reasoning_effort": reasoning_effort},
    )
    write_json(
        paths.active_lock,
        {
            "project": project,
            "runner_id": runner_id,
            "session_name": session_name,
            "pid": os.getpid(),
            "started_at": utc_now(),
        },
    )

    status = "running"
    session_id = state.get("session_id") if isinstance(state.get("session_id"), str) else None
    iteration = int(state.get("iteration", 0))
    return_code = 0
    runner_error: str | None = None

    try:
        with paths.runners_log.open("a", encoding="utf-8") as handle:
            handle.write(f"{session_name},{int(time.time())},\n")

        while True:
            if paths.stop_lock.exists():
                status = "manual_stop"
                _append_ledger(paths, "runner.manual_stop", stop_lock=str(paths.stop_lock))
                _log_line(paths, f"Manual stop detected: {paths.stop_lock}")
                break

            if paths.done_lock.exists():
                _log_line(paths, "Done lock detected; validating gates + TASKS.json")
                gates_ok, gates_output = _run_gates(paths.gates_file, project_root, runner_id)
                open_tasks = _extract_open_tasks(project_root / ".memory" / "runner" / "TASKS.json")
                tasks_ok = len(open_tasks) == 0
                _append_ledger(
                    paths,
                    "runner.done_candidate",
                    gates_ok=gates_ok,
                    tasks_ok=tasks_ok,
                    open_tasks_count=len(open_tasks),
                )
                if not gates_ok or not tasks_ok:
                    _log_line(paths, "Validation failed for done candidate; deleting done lock")
                    if gates_output:
                        _log_line(paths, gates_output)
                    if not tasks_ok:
                        _log_line(
                            paths,
                            "Done candidate rejected: TASKS.json has open/in_progress/blocked tasks "
                            f"({len(open_tasks)} remaining)",
                        )
                    paths.done_lock.unlink(missing_ok=True)
                    reasons: list[str] = []
                    if not gates_ok:
                        reasons.append("gates_failed")
                    if not tasks_ok:
                        reasons.append("tasks_open")
                    _append_ledger(
                        paths,
                        "runner.done_rejected",
                        reason=",".join(reasons) or "unknown",
                        open_tasks=open_tasks[:8],
                    )
                    state = update_state(
                        paths.state_file,
                        state,
                        done_gate_status="failed",
                        done_candidate=False,
                    )
                    time.sleep(backoff_seconds)
                    continue
                state = update_state(paths.state_file, state, done_gate_status="passed")
                status = "done"
                _log_line(paths, "Done lock accepted; exiting loop")
                break

            iteration += 1
            state = load_or_init_state(paths=paths, project=project, runner_id=runner_id)
            state = update_state(
                paths.state_file,
                state,
                status="running",
                iteration=iteration,
                current_step=f"iteration_{iteration}",
                session_id=session_id,
                **detect_git_context(project_root),
                runtime_policy={
                    "hil_mode": hil_mode.replace("-", "_"),
                    "session_mode": "phase_persistent",
                },
            )

            prompt = _build_prompt(project=project, runner_id=runner_id, paths=paths)
            _log_line(paths, f"Iteration {iteration} running {model}")
            _append_ledger(paths, "iteration.start", iteration=iteration, model=model)
            hooks.emit(
                "on_step",
                utc_now(),
                project,
                runner_id,
                iteration,
                {"model": model, "reasoning_effort": reasoning_effort, "hil_mode": hil_mode},
            )

            resume_session_id = None
            result = run_codex_iteration(
                cwd=project_root,
                model=model,
                prompt=prompt,
                session_id=resume_session_id,
                reasoning_effort=reasoning_effort,
                json_stream=False,
                logger=lambda line: _log_line(paths, line),
            )

            session_id = result.session_id or session_id
            state = update_state(paths.state_file, state, session_id=session_id)

            for event in result.events:
                if event.get("_hook") == "tool_call":
                    hooks.emit("on_tool_call", utc_now(), project, runner_id, iteration, {"event": event})

            _append_ledger(
                paths,
                "iteration.finish",
                iteration=iteration,
                exit_code=result.exit_code,
                final_message=result.final_message,
                session_id=session_id,
                hil_mode=hil_mode,
            )

            if result.exit_code != 0:
                hooks.emit(
                    "on_error",
                    utc_now(),
                    project,
                    runner_id,
                    iteration,
                    {
                        "exit_code": result.exit_code,
                        "final_message": result.final_message,
                        "hil_mode": hil_mode,
                    },
                )

            update_payload, finalize_mode, parse_error, finalize_probe_error = _resolve_iteration_update(
                paths=paths,
                project_root=project_root,
                model=model,
                reasoning_effort=reasoning_effort,
                state=state,
                result=result,
                iteration=iteration,
            )
            hooks.emit(
                "on_finish",
                utc_now(),
                project,
                runner_id,
                iteration,
                {
                    "final_message": str(update_payload.get("summary", result.final_message)),
                    "exit_code": result.exit_code,
                    "hil_mode": hil_mode,
                    "runner_update": update_payload,
                    "done_candidate": bool(update_payload.get("done_candidate", False)),
                    "finalize_mode": finalize_mode,
                    "parse_error": parse_error,
                    "finalize_probe_error": finalize_probe_error,
                },
            )

            state = _apply_iteration_update(
                paths=paths,
                state=state,
                update_payload=update_payload,
                hil_mode=hil_mode,
                session_id=session_id,
            )
            _append_ledger(
                paths,
                "iteration.update_applied",
                iteration=iteration,
                done_candidate=bool(update_payload.get("done_candidate", False)),
                finalize_mode=finalize_mode,
            )
            completion_action = "none"
            if bool(update_payload.get("done_candidate", False)):
                _log_line(paths, "Done candidate update detected; validating gates + TASKS.json before lock creation")
                gates_ok, gates_output = _run_gates(paths.gates_file, project_root, runner_id)
                open_tasks = _extract_open_tasks(project_root / ".memory" / "runner" / "TASKS.json")
                tasks_ok = len(open_tasks) == 0
                _append_ledger(
                    paths,
                    "runner.done_candidate.update",
                    iteration=iteration,
                    gates_ok=gates_ok,
                    tasks_ok=tasks_ok,
                    open_tasks_count=len(open_tasks),
                )
                if gates_ok and tasks_ok:
                    state = update_state(paths.state_file, state, done_gate_status="passed", done_candidate=True)
                    paths.done_lock.write_text(
                        f"created_at={utc_now()}\nproject={project}\nrunner_id={runner_id}\niteration={iteration}\n"
                    )
                    _append_ledger(paths, "runner.done_lock_created", iteration=iteration)
                    _log_line(paths, f"Done lock created: {paths.done_lock}")
                    completion_action = "done_lock_created"
                else:
                    state = update_state(paths.state_file, state, done_gate_status="failed", done_candidate=False)
                    if gates_output:
                        _log_line(paths, gates_output)
                    if not tasks_ok:
                        _log_line(
                            paths,
                            "Done candidate rejected: TASKS.json has open/in_progress/blocked tasks "
                            f"({len(open_tasks)} remaining)",
                        )
                    _log_line(paths, "Done candidate rejected because validation failed")
                    completion_action = "done_rejected_validation"
            else:
                state = update_state(paths.state_file, state, done_candidate=False, done_gate_status="pending")
                completion_action = "not_candidate"

            hooks.emit(
                "on_finalize",
                utc_now(),
                project,
                runner_id,
                iteration,
                {
                    "finalize_mode": finalize_mode,
                    "done_candidate": bool(update_payload.get("done_candidate", False)),
                    "completion_action": completion_action,
                    "parse_error": parse_error,
                    "finalize_probe_error": finalize_probe_error,
                    "runner_update": update_payload,
                },
            )

            time.sleep(backoff_seconds)
    except KeyboardInterrupt:
        status = "interrupted"
        return_code = 130
        runner_error = "KeyboardInterrupt"
        _append_ledger(paths, "runner.interrupted", iteration=iteration)
        _log_line(paths, "Runner interrupted by keyboard signal")
        hooks.emit(
            "on_error",
            utc_now(),
            project,
            runner_id,
            iteration,
            {
                "error": runner_error,
                "hil_mode": hil_mode,
            },
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        status = "error"
        return_code = 1
        runner_error = f"{type(exc).__name__}: {exc}"
        _append_ledger(
            paths,
            "runner.exception",
            iteration=iteration,
            error=runner_error,
            traceback=traceback.format_exc(),
        )
        _log_line(paths, f"Runner exception: {runner_error}")
        hooks.emit(
            "on_error",
            utc_now(),
            project,
            runner_id,
            iteration,
            {
                "error": runner_error,
                "hil_mode": hil_mode,
            },
        )
    finally:
        try:
            paths.stop_lock.unlink(missing_ok=True)
            _mark_runner_end_time(paths, session_name)

            state = update_state(paths.state_file, state, status=status, current_step="")
            payload: dict[str, object] = {"status": status, "iteration": iteration}
            if runner_error:
                payload["error"] = runner_error
            _append_ledger(paths, "runner.end", **payload)

            if runner_error:
                _log_line(paths, f"Runner finished status={status} iteration={iteration} error={runner_error}")
            else:
                _log_line(paths, f"Runner finished status={status} iteration={iteration}")
        finally:
            paths.active_lock.unlink(missing_ok=True)

    return return_code


def parse_loop_worker_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Internal loop worker")
    parser.add_argument("--project", required=True)
    parser.add_argument("--runner-id", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument(
        "--reasoning-effort",
        default="high",
        choices=("low", "medium", "high", "xhigh"),
    )
    parser.add_argument(
        "--hil-mode",
        default="setup-only",
        choices=("setup-only",),
    )
    parser.add_argument("--session-name", required=True)
    parser.add_argument("--backoff-seconds", type=int, default=2)
    parser.add_argument("--dev", default=os.environ.get("DEV", "/Users/jian/Dev"))
    return parser.parse_args(argv)


def run_loop_worker(argv: list[str]) -> int:
    args = parse_loop_worker_args(argv)
    return run_loop_runner(
        dev=args.dev,
        project=args.project,
        runner_id=args.runner_id,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        hil_mode=args.hil_mode,
        session_name=args.session_name,
        backoff_seconds=args.backoff_seconds,
    )
