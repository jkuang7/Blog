"""Structured ORX interpretation of factual slice handoffs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .codex_interpreter import CodexHandoffInterpreter
from .execution_policy import determine_execution_route
from .mirror import MirroredIssueRecord


@dataclass(frozen=True)
class InterpretedHandoff:
    action: str
    status_label: str
    next_slice: str | None
    blockers: tuple[str, ...]
    discovered_gaps: tuple[str, ...]
    follow_ups: tuple[dict[str, Any], ...]
    resume_context_updates: dict[str, Any]
    payload: dict[str, Any]


def interpret_slice_handoff(
    *,
    issue: MirroredIssueRecord,
    payload: dict[str, Any],
    continuity: Any,
    codex_interpreter: CodexHandoffInterpreter | None = None,
) -> InterpretedHandoff:
    status = _clean_line(payload.get("status")) or "unknown"
    verified = bool(payload.get("verified"))
    summary = _clean_line(payload.get("summary")) or f"Advance {issue.identifier}."
    blockers = tuple(_clean_list(payload.get("blockers")))
    risks = tuple(_clean_list(payload.get("risks")))
    lessons = tuple(_clean_list(payload.get("lessons")))
    verification_ran = tuple(_clean_list(payload.get("verification_ran")))
    verification_failed = tuple(_clean_list(payload.get("verification_failed")))
    touched_paths = tuple(_clean_list(payload.get("touched_paths")))
    artifacts = tuple(_clean_list(payload.get("artifacts")))
    owner_mismatch = _clean_line(payload.get("owner_mismatch"))
    scope_mismatch = _clean_line(payload.get("scope_mismatch"))
    needs_human_help = bool(payload.get("needs_human_help"))
    next_slice_hint = _clean_line(payload.get("next_slice"))
    next_step_hint = _clean_line(payload.get("next_step_hint"))
    continuity_next_slice = _clean_line(getattr(continuity, "next_slice", None))
    inferred_gaps = _merge_unique(
        _clean_list(payload.get("discovered_gaps")),
        [owner_mismatch] if owner_mismatch else [],
        [scope_mismatch] if scope_mismatch else [],
    )

    action = "continue"
    status_label = "slice in progress"
    next_slice = next_slice_hint or continuity_next_slice

    if owner_mismatch:
        action = "reroute"
        status_label = "blocked on owner mismatch"
        next_slice = None
    elif scope_mismatch:
        action = "replan"
        status_label = "needs replan"
        next_slice = None
    elif needs_human_help:
        action = "needs_human_help"
        status_label = "needs human help"
        next_slice = None
    elif blockers:
        action = "blocked"
        status_label = "blocked"
        next_slice = None
    elif status == "success" and verified and next_slice_hint is None:
        action = "complete"
        status_label = "verified slice landed"
        next_slice = None
    elif status == "success" and verified:
        action = "continue"
        status_label = "verified slice landed"
    elif status == "success":
        action = "continue"
        status_label = "slice landed; verification still pending"
    elif status == "failed":
        action = "blocked"
        status_label = "slice failed"
        next_slice = None

    follow_ups = _normalize_follow_ups(payload.get("follow_ups"))
    if not follow_ups:
        follow_ups = _synthesize_follow_ups(
            issue=issue,
            owner_mismatch=owner_mismatch,
            scope_mismatch=scope_mismatch,
            blockers=blockers,
        )

    codex_advice = _interpret_with_codex(
        issue=issue,
        payload=payload,
        continuity=continuity,
        codex_interpreter=codex_interpreter,
    )
    if codex_advice is not None:
        if codex_advice.action is not None:
            action = codex_advice.action
            status_label = _status_for_action(action=action, verified=verified)
            if action in {"blocked", "reroute", "replan", "needs_human_help", "complete"}:
                next_slice = None
        if codex_advice.next_slice is not None and action == "continue":
            next_slice = codex_advice.next_slice
        if codex_advice.follow_ups:
            follow_ups = codex_advice.follow_ups

    execution_route = determine_execution_route(
        issue=issue,
        payload=payload,
        interpreted_action=action,
    )
    interpreted_next_direction = _next_direction(
        action=action,
        next_step_hint=(
            codex_advice.next_step_hint if codex_advice is not None else next_step_hint
        ),
        next_slice=next_slice,
        owner_mismatch=owner_mismatch,
        scope_mismatch=scope_mismatch,
        needs_human_help=needs_human_help,
        blockers=blockers,
    )

    normalized_payload = {
        **payload,
        "status": action,
        "summary": summary,
        "verified": verified,
        "blockers": list(blockers),
        "risks": list(risks),
        "lessons": list(lessons),
        "verification_ran": list(verification_ran),
        "verification_failed": list(verification_failed),
        "touched_paths": list(touched_paths),
        "artifacts": list(artifacts),
        "owner_mismatch": owner_mismatch,
        "scope_mismatch": scope_mismatch,
        "needs_human_help": needs_human_help,
        "next_slice": next_slice,
        "next_step_hint": interpreted_next_direction,
        "follow_ups": [dict(item) for item in follow_ups],
        "interpreted_action": action,
        "interpreted_status": status_label,
        "execution_model": execution_route.model,
        "execution_reasoning_effort": execution_route.reasoning_effort,
        "execution_reasoning_source": execution_route.source,
        "execution_reasoning_reason": execution_route.reason,
        "execution_escalation_trigger": execution_route.escalation_trigger,
        "codex_reasoning": codex_advice.reasoning if codex_advice is not None else None,
    }

    return InterpretedHandoff(
        action=action,
        status_label=status_label,
        next_slice=next_slice,
        blockers=blockers,
        discovered_gaps=tuple(inferred_gaps),
        follow_ups=follow_ups,
        resume_context_updates={
            "interpreted_action": action,
            "interpreted_status": status_label,
            "interpreted_next_direction": interpreted_next_direction,
            "execution_model": execution_route.model,
            "execution_reasoning_effort": execution_route.reasoning_effort,
            "execution_reasoning_source": execution_route.source,
            "execution_reasoning_reason": execution_route.reason,
            "execution_escalation_trigger": execution_route.escalation_trigger,
            "owner_mismatch": owner_mismatch,
            "scope_mismatch": scope_mismatch,
        },
        payload=normalized_payload,
    )


def _interpret_with_codex(
    *,
    issue: MirroredIssueRecord,
    payload: dict[str, Any],
    continuity: Any,
    codex_interpreter: CodexHandoffInterpreter | None,
):
    interpreter = codex_interpreter or CodexHandoffInterpreter.from_env()
    if interpreter is None:
        return None
    try:
        return interpreter.interpret(
            context={
                "issue": {
                    "identifier": issue.identifier,
                    "title": issue.title,
                    "project_name": issue.project_name,
                    "description": issue.description,
                },
                "latest_handoff": getattr(continuity, "resume_context", {}).get(
                    "interpreted_next_direction"
                ),
                "continuity": {
                    "objective": getattr(continuity, "objective", None),
                    "next_slice": getattr(continuity, "next_slice", None),
                    "blockers": list(getattr(continuity, "blockers", ()) or ()),
                    "discovered_gaps": list(getattr(continuity, "discovered_gaps", ()) or ()),
                },
                "slice_result": payload,
            }
        )
    except Exception:
        return None


def _normalize_follow_ups(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list):
        return ()
    normalized: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict):
            continue
        title = _clean_line(raw.get("title"))
        if not title or title in seen_titles:
            continue
        seen_titles.add(title)
        entry = {key: raw[key] for key in raw if raw[key] not in (None, "", [], {})}
        entry["title"] = title
        normalized.append(entry)
    return tuple(normalized)


def _synthesize_follow_ups(
    *,
    issue: MirroredIssueRecord,
    owner_mismatch: str | None,
    scope_mismatch: str | None,
    blockers: tuple[str, ...],
) -> tuple[dict[str, Any], ...]:
    if owner_mismatch:
        return (
            {
                "title": f"Route owning work for {issue.identifier} into the correct repo",
                "follow_up_class": "owner_reroute",
                "relationship": "blocked_by",
                "why": owner_mismatch,
                "goal": f"Put the missing owner work for {issue.identifier} onto the correct repo or project lane.",
                "scope_in": [owner_mismatch],
                "acceptance": [
                    f"The owning repo or project work needed by {issue.identifier} exists as linked follow-up work."
                ],
            },
        )
    if scope_mismatch:
        return (
            {
                "title": f"Re-scope {issue.identifier} after execution uncovered broader work",
                "follow_up_class": "ticket_split",
                "relationship": "parent_child",
                "why": scope_mismatch,
                "goal": f"Split or rewrite {issue.identifier} so the remaining execution work is narrow and runnable.",
                "scope_in": [scope_mismatch],
                "acceptance": [
                    f"{issue.identifier} is narrowed or split into execution-ready work."
                ],
            },
        )
    if blockers:
        first = blockers[0]
        return (
            {
                "title": f"Unblock {issue.identifier} prerequisite discovered during execution",
                "follow_up_class": "prerequisite",
                "relationship": "blocked_by",
                "why": first,
                "goal": f"Resolve the prerequisite preventing {issue.identifier} from continuing.",
                "scope_in": list(blockers),
                "acceptance": [
                    f"The blocker preventing {issue.identifier} is resolved or captured as linked prerequisite work."
                ],
            },
        )
    return ()


def _next_direction(
    *,
    action: str,
    next_step_hint: str | None,
    next_slice: str | None,
    owner_mismatch: str | None,
    scope_mismatch: str | None,
    needs_human_help: bool,
    blockers: tuple[str, ...],
) -> str | None:
    if next_step_hint:
        return next_step_hint
    if action == "complete":
        return "Finalize this ticket or advance the next packet leaf."
    if owner_mismatch:
        return "Create or route the owning follow-up ticket before redispatch."
    if scope_mismatch:
        return "Re-scope or split this ticket before asking tmux-codex for another slice."
    if needs_human_help:
        return "Pause autonomous execution and request human help."
    if blockers:
        return "Interpret the blocker set and decide whether to unblock, split, or create prerequisite work."
    if next_slice:
        return next_slice
    return None


def _status_for_action(*, action: str, verified: bool) -> str:
    if action == "continue":
        return "verified slice landed" if verified else "slice landed; verification still pending"
    if action == "complete":
        return "verified slice landed"
    if action == "reroute":
        return "needs reroute"
    if action == "replan":
        return "needs replan"
    if action == "needs_human_help":
        return "needs human help"
    if action == "blocked":
        return "blocked"
    return "slice in progress"


def _merge_unique(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            if item and item not in seen:
                seen.add(item)
                merged.append(item)
    return merged


def _clean_line(value: Any) -> str | None:
    text = " ".join(str(value or "").split())
    return text or None


def _clean_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    cleaned: list[str] = []
    for item in value:
        normalized = _clean_line(item)
        if normalized:
            cleaned.append(normalized)
    return cleaned
