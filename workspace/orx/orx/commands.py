"""Command normalization and queue semantics for ORX."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


CommandDisposition = Literal["queue", "interrupt"]

COMMAND_PRIORITY: dict[str, int] = {
    "steer": 10,
    "stop": 40,
    "pause": 60,
    "resume": 60,
    "approve": 70,
    "deny": 70,
    "focus": 90,
    "run": 100,
    "new": 110,
}

COMMAND_DISPOSITION: dict[str, CommandDisposition] = {
    "steer": "interrupt",
    "stop": "queue",
    "pause": "queue",
    "resume": "queue",
    "approve": "queue",
    "deny": "queue",
    "focus": "queue",
    "run": "queue",
    "new": "queue",
}

REPLACEMENT_FAMILY_BY_KIND: dict[str, str] = {
    "pause": "execution-state",
    "resume": "execution-state",
    "approve": "review-decision",
    "deny": "review-decision",
}


class CommandValidationError(ValueError):
    """Raised when a command request does not meet the shared ORX contract."""


@dataclass(frozen=True)
class NormalizedCommand:
    command_kind: str
    issue_key: str | None
    runner_id: str | None
    priority: int
    disposition: CommandDisposition
    payload: dict[str, Any]
    replacement_key: str | None


def normalize_command(
    command_kind: str,
    *,
    issue_key: str | None = None,
    runner_id: str | None = None,
    payload: dict[str, Any] | None = None,
    replacement_key: str | None = None,
) -> NormalizedCommand:
    kind = command_kind.strip().lower()
    if kind not in COMMAND_PRIORITY:
        raise CommandValidationError(f"Unsupported command kind: {command_kind}")

    issue = _normalize_optional_text(issue_key, field_name="issue_key")
    runner = _normalize_optional_text(runner_id, field_name="runner_id")
    if issue is None and runner is None:
        raise CommandValidationError(
            "Commands must target at least one tmux-backed session scope via issue_key or runner_id."
        )

    resolved_replacement_key = _resolve_replacement_key(
        kind,
        issue_key=issue,
        runner_id=runner,
        explicit_key=replacement_key,
    )
    body = dict(payload or {})
    body.setdefault("session_residency", "tmux")
    body.setdefault("target", {"issue_key": issue, "runner_id": runner})
    body["disposition"] = COMMAND_DISPOSITION[kind]
    if resolved_replacement_key is not None:
        body["replacement_key"] = resolved_replacement_key

    return NormalizedCommand(
        command_kind=kind,
        issue_key=issue,
        runner_id=runner,
        priority=COMMAND_PRIORITY[kind],
        disposition=COMMAND_DISPOSITION[kind],
        payload=body,
        replacement_key=resolved_replacement_key,
    )


def _normalize_optional_text(value: str | None, *, field_name: str) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        raise CommandValidationError(f"{field_name} cannot be empty.")
    return normalized


def _resolve_replacement_key(
    command_kind: str,
    *,
    issue_key: str | None,
    runner_id: str | None,
    explicit_key: str | None,
) -> str | None:
    if explicit_key is not None:
        key = explicit_key.strip()
        if not key:
            raise CommandValidationError("replacement_key cannot be empty.")
        return key

    family = REPLACEMENT_FAMILY_BY_KIND.get(command_kind)
    if family is None:
        return None

    issue = issue_key or "-"
    runner = runner_id or "-"
    return f"{family}:{issue}:{runner}"
