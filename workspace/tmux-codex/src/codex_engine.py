"""Codex command execution and JSONL event parsing helpers."""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass
class CodexRunResult:
    """Result of one codex exec iteration."""

    exit_code: int
    session_id: str | None
    final_message: str
    events: list[dict[str, Any]]
    raw_lines: list[str]


def _try_parse_json(line: str) -> dict[str, Any] | None:
    line = line.strip()
    if not line or not line.startswith("{"):
        return None
    try:
        parsed = json.loads(line)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict):
        return parsed
    return None


def _extract_message_from_event(event: dict[str, Any]) -> str | None:
    event_type = str(event.get("type", ""))
    if event_type == "error":
        msg = event.get("message")
        return str(msg) if msg else None

    item = event.get("item")
    if isinstance(item, dict):
        if "text" in item and isinstance(item["text"], str):
            return item["text"]
        if "message" in item and isinstance(item["message"], str):
            return item["message"]
        content = item.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            chunks = []
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    chunks.append(part["text"])
                elif isinstance(part, str):
                    chunks.append(part)
            if chunks:
                return "\n".join(chunks)

    msg = event.get("message")
    if isinstance(msg, str):
        return msg
    return None


def _is_tool_event(event: dict[str, Any]) -> bool:
    event_type = str(event.get("type", ""))
    if "tool" in event_type or "command" in event_type:
        return True
    item = event.get("item")
    if isinstance(item, dict):
        item_type = str(item.get("type", ""))
        if any(token in item_type for token in ("tool", "command", "exec", "shell")):
            return True
    return False


_SESSION_ID_RE = re.compile(r"^\s*session id:\s*([0-9a-fA-F-]+)\s*$")
_PLAIN_STREAM_PROVIDER_BANNER_RE = re.compile(
    r"^(OpenAI Codex|Claude Code|Gemini CLI|Mistral Code|Qwen Code)(?:\b| v)",
    re.IGNORECASE,
)


def _extract_session_id_from_plain_line(line: str) -> str | None:
    match = _SESSION_ID_RE.match(line)
    if not match:
        return None
    return match.group(1)


def _is_plain_stream_metadata_line(line: str) -> bool:
    normalized = line.strip()
    if not normalized:
        return True

    lower = normalized.lower()
    if normalized == "--------":
        return True
    if lower in {"user", "thinking", "codex"}:
        return True
    if _PLAIN_STREAM_PROVIDER_BANNER_RE.match(normalized):
        return True
    if lower.startswith(
        (
            "workdir:",
            "model:",
            "provider:",
            "approval:",
            "sandbox:",
            "reasoning effort:",
            "reasoning summaries:",
            "session id:",
            "mcp:",
            "mcp startup:",
            "tokens used",
        )
    ):
        return True
    if normalized.startswith("{") and normalized.endswith("}"):
        return True
    if normalized[:4].isdigit() and " WARN " in normalized:
        return True
    return False


def _extract_final_message_from_plain_lines(lines: list[str]) -> str:
    final = ""
    for raw in lines:
        line = raw.strip()
        if _is_plain_stream_metadata_line(line):
            continue
        final = line
    return final


def _global_developer_instruction_args() -> list[str]:
    env_path = os.environ.get("CODEX_GLOBAL_AGENTS_FILE")
    agents_path = Path(env_path) if env_path else Path.home() / ".codex" / "AGENTS.md"
    try:
        text = agents_path.read_text(encoding="utf-8").strip()
    except OSError:
        return []
    if not text:
        return []
    return ["-c", f"developer_instructions={json.dumps(text)}"]


def run_codex_iteration(
    cwd: Path,
    model: str,
    prompt: str,
    session_id: str | None,
    reasoning_effort: str = "high",
    sandbox_mode: str = "workspace-write",
    enable_search: bool = True,
    json_stream: bool = True,
    logger: Callable[[str], None] | None = None,
) -> CodexRunResult:
    """Run one Codex iteration and parse JSONL stream."""
    reasoning_config = f'model_reasoning_effort="{reasoning_effort}"'
    prefix = ["codex"]
    if enable_search:
        prefix.append("--search")
    prefix.extend(_global_developer_instruction_args())
    if sandbox_mode:
        prefix.extend(["-s", sandbox_mode])
    if session_id:
        cmd = prefix + ["exec", "resume"]
        if json_stream:
            cmd.append("--json")
        cmd += ["-m", model, "-c", reasoning_config, session_id, prompt]
    else:
        cmd = prefix + ["exec"]
        if json_stream:
            cmd.append("--json")
        cmd += ["-m", model, "-c", reasoning_config, prompt]

    process = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    parsed_events: list[dict[str, Any]] = []
    raw_lines: list[str] = []
    resolved_session = session_id
    final_message = ""

    assert process.stdout is not None
    for raw in process.stdout:
        line = raw.rstrip("\n")
        raw_lines.append(line)
        if json_stream:
            event = _try_parse_json(line)
            if event is None:
                if logger and line:
                    logger(line)
                continue

            parsed_events.append(event)

            if event.get("type") == "thread.started" and isinstance(event.get("thread_id"), str):
                resolved_session = event["thread_id"]

            maybe_message = _extract_message_from_event(event)
            if maybe_message:
                final_message = maybe_message

            if logger:
                if event.get("type") in {"thread.started", "turn.started"}:
                    continue
                item = event.get("item")
                if isinstance(item, dict):
                    item_type = str(item.get("type", ""))
                    if item_type in {"reasoning"}:
                        continue
                    if item_type in {"agent_message", "message"}:
                        msg = _extract_message_from_event(event)
                        if msg:
                            logger(msg)
                        continue
                msg = _extract_message_from_event(event)
                if msg:
                    logger(msg)
            continue

        session_candidate = _extract_session_id_from_plain_line(line)
        if session_candidate:
            resolved_session = session_candidate

        if logger and line:
            logger(line)

    exit_code = process.wait()

    if json_stream:
        # Keep only tool-related events in a dedicated stream so hooks can subscribe.
        for event in parsed_events:
            if _is_tool_event(event):
                event.setdefault("_hook", "tool_call")
    elif not final_message:
        final_message = _extract_final_message_from_plain_lines(raw_lines)

    return CodexRunResult(
        exit_code=exit_code,
        session_id=resolved_session,
        final_message=final_message,
        events=parsed_events,
        raw_lines=raw_lines,
    )
