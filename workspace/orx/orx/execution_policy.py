"""Deterministic execution-tier routing for ORX-managed work."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .mirror import MirroredIssueRecord

DEFAULT_EXECUTION_MODEL = "gpt-5.4"
DEFAULT_EXECUTION_EFFORT = "medium"
_EFFORT_ORDER = {"medium": 0, "high": 1, "xhigh": 2}


@dataclass(frozen=True)
class ExecutionRoute:
    model: str
    reasoning_effort: str
    source: str
    reason: str
    escalation_trigger: str | None


def determine_execution_route(
    *,
    issue: MirroredIssueRecord,
    payload: dict[str, Any],
    interpreted_action: str,
) -> ExecutionRoute:
    metadata = issue.metadata if isinstance(issue.metadata, dict) else {}
    base_model = _clean_line(metadata.get("codex_execution_model")) or DEFAULT_EXECUTION_MODEL
    base_effort = _normalize_effort(metadata.get("codex_execution_reasoning_effort"))
    issue_class = _clean_line(metadata.get("issue_class")) or _clean_line(metadata.get("type"))
    complexity = _clean_line(metadata.get("complexity"))
    blockers = _clean_list(payload.get("blockers"))
    risks = _clean_list(payload.get("risks"))
    verification_failed = _clean_list(payload.get("verification_failed"))
    owner_mismatch = _clean_line(payload.get("owner_mismatch"))
    scope_mismatch = _clean_line(payload.get("scope_mismatch"))
    needs_human_help = bool(payload.get("needs_human_help"))

    effort = base_effort
    source = "ticket_default"
    trigger: str | None = None
    reason = (
        "Using the ticket-default execution tier for a runnable leaf."
        if base_effort == DEFAULT_EXECUTION_EFFORT
        else f"Using the ticket-declared execution tier `{base_effort}`."
    )

    if issue_class in {"migration", "schema_change", "cross_repo"} or complexity in {"high", "xhigh"}:
        effort = _max_effort(effort, "high")
        source = "ticket_complexity"
        trigger = "ticket_complexity"
        reason = "Ticket metadata marks this work as higher-complexity, so ORX keeps execution at least `high`."

    if scope_mismatch or interpreted_action == "replan":
        effort = _max_effort(effort, "xhigh")
        source = "handoff_escalation"
        trigger = "scope_mismatch"
        reason = "Execution uncovered broader scope or ambiguity, so ORX escalates interpretation to `xhigh` before continuing."
    elif needs_human_help:
        effort = _max_effort(effort, "xhigh")
        source = "handoff_escalation"
        trigger = "needs_human_help"
        reason = "Execution explicitly requested human help, so ORX escalates planning to `xhigh` instead of redispatching medium blindly."
    elif owner_mismatch or interpreted_action == "reroute":
        effort = _max_effort(effort, "high")
        source = "handoff_escalation"
        trigger = "owner_mismatch"
        reason = "Execution found an ownership mismatch, so ORX escalates to `high` while it reroutes or spawns the owning follow-up."
    elif verification_failed:
        effort = _max_effort(effort, "high")
        source = "handoff_escalation"
        trigger = "verification_failed"
        reason = "Verification failed on the last slice, so ORX escalates to `high` for the next bounded attempt."
    elif blockers:
        effort = _max_effort(effort, "high")
        source = "handoff_escalation"
        trigger = "blockers"
        reason = "Execution surfaced concrete blockers, so ORX escalates to `high` before deciding whether to continue or split."
    elif len(risks) >= 2:
        effort = _max_effort(effort, "high")
        source = "handoff_escalation"
        trigger = "multiple_risks"
        reason = "Multiple execution risks are active, so ORX escalates to `high` instead of keeping a medium retry loop."

    return ExecutionRoute(
        model=base_model,
        reasoning_effort=effort,
        source=source,
        reason=reason,
        escalation_trigger=trigger,
    )


def _max_effort(current: str, desired: str) -> str:
    current_norm = _normalize_effort(current)
    desired_norm = _normalize_effort(desired)
    return desired_norm if _EFFORT_ORDER[desired_norm] > _EFFORT_ORDER[current_norm] else current_norm


def _normalize_effort(value: Any) -> str:
    text = _clean_line(value)
    if text in {"medium", "high", "xhigh"}:
        return text
    return DEFAULT_EXECUTION_EFFORT


def _clean_line(value: Any) -> str | None:
    text = " ".join(str(value or "").strip().split())
    return text or None


def _clean_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    cleaned: list[str] = []
    for item in value:
        text = _clean_line(item)
        if text:
            cleaned.append(text)
    return cleaned
