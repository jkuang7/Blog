"""Helpers for mutable ORX handoff sections and execution packets."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from .metadata import METADATA_END, METADATA_START

LATEST_HANDOFF_HEADING = "## Latest Handoff"
RAW_SLICE_FACTS_HEADING = "## Raw Slice Facts"
_LATEST_HANDOFF_RE = re.compile(
    r"(?ms)^## Latest Handoff\s*\n(?P<body>.*?)(?=^## |\Z)"
)
_RAW_SLICE_FACTS_RE = re.compile(
    r"(?ms)^## Raw Slice Facts\s*\n(?P<body>.*?)(?=^## |\Z)"
)


def extract_latest_handoff(description: str) -> str | None:
    match = _LATEST_HANDOFF_RE.search(_description_without_metadata(description))
    if match is None:
        return None
    body = match.group(0).strip()
    return body or None


def latest_handoff_revision(description: str) -> str | None:
    handoff = extract_latest_handoff(description)
    if not handoff:
        return None
    return hashlib.sha256(handoff.encode("utf-8")).hexdigest()[:16]


def extract_raw_slice_facts(description: str) -> str | None:
    match = _RAW_SLICE_FACTS_RE.search(_description_without_metadata(description))
    if match is None:
        return None
    body = match.group(0).strip()
    return body or None


def replace_latest_handoff(description: str, handoff_markdown: str) -> str:
    body, metadata_block = _split_description_and_metadata(description)
    stripped = body.strip()
    section = handoff_markdown.strip()
    if not section.startswith(LATEST_HANDOFF_HEADING):
        section = f"{LATEST_HANDOFF_HEADING}\n{section}".strip()

    if _LATEST_HANDOFF_RE.search(stripped):
        updated = _LATEST_HANDOFF_RE.sub(section + "\n\n", stripped, count=1).strip()
    elif stripped:
        updated = f"{stripped}\n\n{section}".strip()
    else:
        updated = section

    if metadata_block:
        return f"{updated}\n\n{metadata_block}\n"
    return updated.rstrip() + "\n"


def replace_raw_slice_facts(description: str, facts_markdown: str) -> str:
    body, metadata_block = _split_description_and_metadata(description)
    stripped = body.strip()
    section = facts_markdown.strip()
    if not section.startswith(RAW_SLICE_FACTS_HEADING):
        section = f"{RAW_SLICE_FACTS_HEADING}\n{section}".strip()

    if _RAW_SLICE_FACTS_RE.search(stripped):
        updated = _RAW_SLICE_FACTS_RE.sub(section + "\n\n", stripped, count=1).strip()
    elif stripped:
        updated = f"{stripped}\n\n{section}".strip()
    else:
        updated = section

    if metadata_block:
        return f"{updated}\n\n{metadata_block}\n"
    return updated.rstrip() + "\n"


def build_raw_slice_facts_section(
    *,
    issue_key: str,
    payload: dict[str, Any],
) -> str:
    status = _clean_line(payload.get("status")) or "unknown"
    summary = _clean_line(payload.get("summary")) or f"Advance {issue_key}."
    verified = bool(payload.get("verified"))
    blockers = _clean_list(payload.get("blockers"))
    risks = _clean_list(payload.get("risks"))
    lessons = _clean_list(payload.get("lessons"))
    verification_ran = _clean_list(payload.get("verification_ran"))
    verification_failed = _clean_list(payload.get("verification_failed"))
    touched_paths = _clean_list(payload.get("touched_paths"))
    artifacts = _clean_list(payload.get("artifacts"))
    lines = [
        RAW_SLICE_FACTS_HEADING,
        f"- Status: {status}",
        f"- Summary: {summary}",
        f"- Verification: {'verified' if verified else 'not yet verified'}",
    ]
    if blockers:
        lines.append("- Blockers:")
        lines.extend(f"  - {item}" for item in blockers)
    if risks:
        lines.append("- Risks:")
        lines.extend(f"  - {item}" for item in risks)
    if lessons:
        lines.append("- Lessons:")
        lines.extend(f"  - {item}" for item in lessons)
    if verification_ran:
        lines.append("- Verification ran:")
        lines.extend(f"  - {item}" for item in verification_ran)
    if verification_failed:
        lines.append("- Verification failed:")
        lines.extend(f"  - {item}" for item in verification_failed)
    if touched_paths:
        lines.append("- Touched paths:")
        lines.extend(f"  - {item}" for item in touched_paths[:8])
    if artifacts:
        lines.append("- Artifacts:")
        lines.extend(f"  - {item}" for item in artifacts[:8])
    return "\n".join(lines).rstrip()


def build_latest_handoff_section(
    *,
    issue_key: str,
    payload: dict[str, Any],
    continuity_summary: str | None,
) -> str:
    summary = _clean_line(payload.get("summary")) or f"Advance {issue_key}."
    status = _clean_line(payload.get("status")) or "unknown"
    verified = bool(payload.get("verified"))
    blockers = _clean_list(payload.get("blockers"))
    risks = _clean_list(payload.get("risks"))
    lessons = _clean_list(payload.get("lessons"))
    verification_ran = _clean_list(payload.get("verification_ran"))
    verification_failed = _clean_list(payload.get("verification_failed"))
    touched_paths = _clean_list(payload.get("touched_paths"))
    artifacts = _clean_list(payload.get("artifacts"))
    next_step_hint = _clean_line(payload.get("next_step_hint"))
    execution_reasoning_effort = _clean_line(payload.get("execution_reasoning_effort"))
    execution_reasoning_reason = _clean_line(payload.get("execution_reasoning_reason"))
    owner_mismatch = _clean_line(payload.get("owner_mismatch"))
    scope_mismatch = _clean_line(payload.get("scope_mismatch"))
    needs_human_help = bool(payload.get("needs_human_help"))
    follow_ups = _clean_follow_up_titles(payload.get("follow_ups"))

    interpreted_status = _interpret_status(
        status=status,
        verified=verified,
        blockers=blockers,
        owner_mismatch=owner_mismatch,
        scope_mismatch=scope_mismatch,
        needs_human_help=needs_human_help,
    )
    next_direction = _next_direction(
        interpreted_status=interpreted_status,
        next_step_hint=next_step_hint,
        continuity_summary=continuity_summary,
        blockers=blockers,
        owner_mismatch=owner_mismatch,
        scope_mismatch=scope_mismatch,
        needs_human_help=needs_human_help,
    )

    lines = [
        LATEST_HANDOFF_HEADING,
        f"- Status: {interpreted_status}",
        f"- Summary: {summary}",
        f"- Verification: {'verified' if verified else 'not yet verified'}",
    ]
    if execution_reasoning_effort:
        tier_line = f"- Execution tier: {execution_reasoning_effort}"
        if execution_reasoning_reason:
            tier_line += f" ({execution_reasoning_reason})"
        lines.append(tier_line)
    if next_direction:
        lines.append(f"- Next direction: {next_direction}")
    if owner_mismatch:
        lines.append(f"- Ownership mismatch: {owner_mismatch}")
    if scope_mismatch:
        lines.append(f"- Scope mismatch: {scope_mismatch}")
    if blockers:
        lines.append("- Blockers:")
        lines.extend(f"  - {item}" for item in blockers)
    if risks:
        lines.append("- Risks:")
        lines.extend(f"  - {item}" for item in risks)
    if lessons:
        lines.append("- Lessons learned:")
        lines.extend(f"  - {item}" for item in lessons)
    if verification_ran:
        lines.append("- Verification ran:")
        lines.extend(f"  - {item}" for item in verification_ran)
    if verification_failed:
        lines.append("- Verification failed:")
        lines.extend(f"  - {item}" for item in verification_failed)
    if follow_ups:
        lines.append("- Follow-up work:")
        lines.extend(f"  - {item}" for item in follow_ups)
    if touched_paths:
        lines.append("- Touched paths:")
        lines.extend(f"  - {item}" for item in touched_paths[:8])
    if artifacts:
        lines.append("- Artifacts:")
        lines.extend(f"  - {item}" for item in artifacts[:8])
    return "\n".join(lines).rstrip()


def _split_description_and_metadata(description: str) -> tuple[str, str | None]:
    text = str(description or "").strip()
    if not text:
        return "", None
    start = text.find(METADATA_START)
    end = text.find(METADATA_END)
    if start == -1 or end == -1 or end < start:
        return text, None
    metadata_end = end + len(METADATA_END)
    body = text[:start].rstrip()
    metadata_block = text[start:metadata_end].strip()
    return body, metadata_block or None


def _description_without_metadata(description: str) -> str:
    body, _ = _split_description_and_metadata(description)
    return body


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


def _clean_follow_up_titles(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    titles: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        title = _clean_line(item.get("title"))
        if title:
            titles.append(title)
    return titles


def _interpret_status(
    *,
    status: str,
    verified: bool,
    blockers: list[str],
    owner_mismatch: str | None,
    scope_mismatch: str | None,
    needs_human_help: bool,
) -> str:
    if status == "complete":
        return "verified slice landed"
    if status == "continue":
        return "verified slice landed" if verified else "slice landed; verification still pending"
    if status == "reroute":
        return "blocked on owner mismatch" if owner_mismatch else "needs reroute"
    if status == "replan":
        return "needs replan"
    if status == "needs_human_help":
        return "needs human help"
    if status == "blocked":
        return "blocked"
    if owner_mismatch:
        return "blocked on owner mismatch"
    if scope_mismatch:
        return "needs replan"
    if blockers:
        return "blocked"
    if needs_human_help:
        return "needs human help"
    if status == "success" and verified:
        return "verified slice landed"
    if status == "success":
        return "slice landed; verification still pending"
    if status == "failed":
        return "slice failed"
    return "slice blocked"


def _next_direction(
    *,
    interpreted_status: str,
    next_step_hint: str | None,
    continuity_summary: str | None,
    blockers: list[str],
    owner_mismatch: str | None,
    scope_mismatch: str | None,
    needs_human_help: bool,
) -> str | None:
    if next_step_hint:
        return next_step_hint
    if owner_mismatch:
        return "Create or route the owning follow-up ticket before redispatch."
    if scope_mismatch:
        return "Re-scope the ticket before asking tmux-codex for another slice."
    if blockers:
        return "Interpret the blocker set and decide whether to unblock, split, or spawn follow-up work."
    if needs_human_help:
        return "Pause autonomous execution and request human help."
    if continuity_summary:
        return continuity_summary
    if interpreted_status == "verified slice landed":
        return "Decide whether to finalize this ticket or dispatch the next bounded slice."
    return None
