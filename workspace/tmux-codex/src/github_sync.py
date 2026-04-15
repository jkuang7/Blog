"""Normalize GitHub issue and project metadata for durable runner snapshots."""

from __future__ import annotations

import re
from typing import Any

ISSUE_URL_RE = re.compile(r"https://github\.com/[^/\s]+/[^/\s]+/issues/\d+")
WORKFLOW_COMMENT_PREFIXES = (
    "Picking this up now.",
    "Ready for review.",
    "Done.",
    "Adjusting course based on feedback.",
    "Blocked on ",
)
SECTION_LABEL_ALIASES = {
    "parent": "parent",
    "children": "children",
    "blocked by": "blocked_by",
    "depends on": "depends_on",
    "unblocks": "unblocks",
    "worktree": "worktree",
    "branch": "branch",
    "merge into": "merge_into",
    "resume from": "resume_from",
    "type": "issue_class",
    "routing": "routing",
    "complexity": "complexity",
    "size": "complexity",
}
STATUS_TO_PHASE = {
    "In Progress": "executing",
    "Review": "review",
    "Done": "done",
    "Blocked": "blocked",
    "Ready": "selecting",
    "Inbox": "selecting",
}
TRACKER_TYPE_MARKERS = {"coordination", "phase parent", "phase_parent", "umbrella", "tracker"}


def normalize_github_issue_import(
    *,
    item: dict[str, Any],
    issue: dict[str, Any] | None = None,
    issue_thread: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str | None]:
    """Build a canonical durable snapshot from GitHub project and issue payloads."""
    issue = issue if isinstance(issue, dict) else {}
    issue_thread = issue_thread if isinstance(issue_thread, dict) else {}
    project_fields = item.get("fields") if isinstance(item.get("fields"), dict) else {}
    body = _as_text(issue_thread.get("body")) or _as_text(issue.get("body")) or ""
    comments = issue_thread.get("comments") if isinstance(issue_thread.get("comments"), list) else []

    parsed_body = _parse_labeled_metadata(body)
    parsed_comments = _parse_latest_workflow_metadata(comments)
    merged = dict(parsed_body)
    merged.update(parsed_comments)

    issue_type = _normalized_issue_type(
        _first_text(project_fields.get("Type"), merged.get("issue_class"), merged.get("type"))
    )
    complexity = _normalized_complexity(
        _first_text(project_fields.get("Complexity"), project_fields.get("Size"), merged.get("complexity"))
    )
    routing = _first_text(project_fields.get("Routing"), merged.get("routing"))
    status = _first_text(project_fields.get("Status"))
    priority = _first_text(project_fields.get("Priority"))

    children = _coerce_issue_links(merged.get("children"))
    blocked_by_values = _coerce_issue_links(_first_value(merged, "blocked_by", "depends_on"))
    depends_on_values = _coerce_issue_links(_first_value(merged, "depends_on", "blocked_by"))

    payload: dict[str, Any] = {
        "url": _first_text(item.get("url"), issue.get("url"), issue_thread.get("url")),
        "repo": _first_text(item.get("repo"), issue.get("repo")),
        "number": item.get("number") or issue.get("number") or issue_thread.get("number"),
        "title": _first_text(item.get("title"), issue.get("title"), issue_thread.get("title")),
        "issue_class": issue_type,
        "complexity": complexity,
        "routing": routing,
        "parent": _first_text(merged.get("parent")),
        "children": children,
        "blocked_by": _one_or_many(blocked_by_values),
        "depends_on": _one_or_many(depends_on_values),
        "unblocks": _one_or_many(_coerce_issue_links(merged.get("unblocks"))),
        "worktree": _first_text(merged.get("worktree")),
        "branch": _first_text(merged.get("branch")),
        "merge_into": _first_text(merged.get("merge_into")),
        "resume_from": _first_text(merged.get("resume_from")),
        "status": status,
        "priority": priority,
        "github": {
            "item_id": _first_text(item.get("itemId")),
            "project_status": status,
            "project_priority": priority,
            "source": "github_project_item",
        },
    }
    return payload, STATUS_TO_PHASE.get(status)


def _parse_labeled_metadata(body: str) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("## "):
            continue
        line = line.lstrip("-").strip()
        if ":" not in line:
            continue
        label, value = line.split(":", 1)
        canonical = SECTION_LABEL_ALIASES.get(label.strip().lower())
        if not canonical:
            continue
        _assign_metadata_value(metadata, canonical, value.strip())
    return metadata


def _parse_latest_workflow_metadata(comments: list[dict[str, Any]]) -> dict[str, Any]:
    workflow_comments = [
        comment
        for comment in comments
        if isinstance(comment, dict)
        and _is_workflow_comment(_as_text(comment.get("body")) or "")
    ]
    if not workflow_comments:
        return {}
    latest = max(workflow_comments, key=lambda comment: _as_text(comment.get("createdAt")) or "")
    return _parse_labeled_metadata(_as_text(latest.get("body")) or "")


def _assign_metadata_value(metadata: dict[str, Any], key: str, raw_value: str) -> None:
    if key == "children":
        metadata[key] = _coerce_issue_links(raw_value)
        return
    if key in {"blocked_by", "depends_on", "unblocks"}:
        metadata[key] = _coerce_issue_links(raw_value)
        return
    metadata[key] = _as_text(raw_value)


def _coerce_issue_links(value: Any) -> list[str]:
    if isinstance(value, list):
        return [item for item in (_as_text(entry) for entry in value) if item]
    text = _as_text(value) or ""
    urls = ISSUE_URL_RE.findall(text)
    if urls:
        return list(dict.fromkeys(urls))
    parts = [
        item.strip()
        for item in re.split(r"[,\n]", text)
        if item.strip() and item.strip().lower() != "none"
    ]
    return list(dict.fromkeys(parts))


def _is_workflow_comment(body: str) -> bool:
    stripped = body.strip()
    return any(stripped.startswith(prefix) for prefix in WORKFLOW_COMMENT_PREFIXES)


def _normalized_issue_type(value: str | None) -> str | None:
    text = _as_text(value)
    if not text:
        return None
    lowered = text.lower()
    if lowered in TRACKER_TYPE_MARKERS:
        return lowered.replace(" ", "_")
    return lowered


def _normalized_complexity(value: str | None) -> str | None:
    text = _as_text(value)
    if not text:
        return None
    return text.upper()


def _one_or_many(values: list[str]) -> str | list[str] | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return values


def _first_text(*values: Any) -> str | None:
    for value in values:
        text = _as_text(value)
        if text:
            return text
    return None


def _first_value(metadata: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in metadata:
            return metadata[key]
    return None


def _as_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None
