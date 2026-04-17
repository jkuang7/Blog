"""Deterministic ranking and next-work selection for mirrored Linear issues."""

from __future__ import annotations

from dataclasses import dataclass

from .mirror import LinearMirrorRepository, MirroredIssueRecord


PRIORITY_HINT_RANK = {
    "urgent": 0,
    "high": 1,
    "normal": 2,
    "low": 3,
}

STATE_TYPE_RANK = {
    "started": 0,
    "unstarted": 1,
    "backlog": 2,
    "completed": 8,
    "canceled": 9,
}


@dataclass(frozen=True)
class RankedIssue:
    issue: MirroredIssueRecord
    sort_key: tuple[object, ...]


@dataclass(frozen=True)
class IssueHierarchyState:
    is_leaf: bool
    has_active_ancestors: bool


class LinearRankingService:
    def __init__(self, repository: LinearMirrorRepository) -> None:
        self.repository = repository

    def rank_issues(self) -> list[RankedIssue]:
        ranked = [
            RankedIssue(issue=issue, sort_key=_sort_key(issue, self.repository))
            for issue in self.repository.list_issues()
        ]
        return sorted(ranked, key=lambda item: item.sort_key)

    def select_next_issue(self) -> MirroredIssueRecord | None:
        for ranked in self.rank_issues():
            if ranked.sort_key[0] == 0:
                return ranked.issue
        return None


def _sort_key(
    issue: MirroredIssueRecord,
    repository: LinearMirrorRepository,
) -> tuple[object, ...]:
    metadata = issue.metadata
    stale = bool(metadata.get("orx_reconciliation_missing_from_snapshot"))
    no_auto_select = bool(metadata.get("no_auto_select"))
    blocked = _is_blocked(metadata)
    state_rank = _state_rank(issue)
    hierarchy = _hierarchy_state(issue, repository)
    manual_rank = _manual_rank(metadata)
    priority_hint_rank = _priority_hint_rank(metadata)
    priority_rank = _priority_rank(issue.priority)
    ineligibility_rank = _ineligibility_rank(
        stale=stale,
        no_auto_select=no_auto_select,
        blocked=blocked,
        state_rank=state_rank,
        hierarchy=hierarchy,
    )

    # Lower tuple values rank earlier.
    return (
        ineligibility_rank,
        0 if hierarchy.is_leaf else 1,
        0 if hierarchy.has_active_ancestors else 1,
        1 if blocked else 0,
        state_rank,
        manual_rank,
        priority_hint_rank,
        priority_rank,
        _descending_timestamp(issue.source_updated_at),
        issue.identifier,
    )


def _is_blocked(metadata: dict[str, object]) -> bool:
    if bool(metadata.get("blocked")):
        return True
    blocked_by = metadata.get("blocked_by")
    return isinstance(blocked_by, list) and len(blocked_by) > 0


def _state_rank(issue: MirroredIssueRecord) -> int:
    if issue.completed_at is not None:
        return STATE_TYPE_RANK["completed"]
    if issue.canceled_at is not None:
        return STATE_TYPE_RANK["canceled"]

    if issue.state_type is not None:
        normalized = issue.state_type.lower()
        if normalized in STATE_TYPE_RANK:
            return STATE_TYPE_RANK[normalized]

    state_name = issue.state_name.lower()
    if "progress" in state_name:
        return STATE_TYPE_RANK["started"]
    if state_name in {"todo", "planned"}:
        return STATE_TYPE_RANK["unstarted"]
    if state_name == "backlog":
        return STATE_TYPE_RANK["backlog"]
    if state_name in {"done", "completed"}:
        return STATE_TYPE_RANK["completed"]
    if state_name in {"canceled", "cancelled"}:
        return STATE_TYPE_RANK["canceled"]
    return 5


def _hierarchy_state(
    issue: MirroredIssueRecord,
    repository: LinearMirrorRepository,
) -> IssueHierarchyState:
    ancestors = repository.get_ancestor_chain(issue)
    return IssueHierarchyState(
        is_leaf=not repository.has_children(issue),
        has_active_ancestors=all(_state_rank(ancestor) < STATE_TYPE_RANK["completed"] for ancestor in ancestors),
    )


def _ineligibility_rank(
    *,
    stale: bool,
    no_auto_select: bool,
    blocked: bool,
    state_rank: int,
    hierarchy: IssueHierarchyState,
) -> int:
    if blocked:
        return 1
    if stale or no_auto_select:
        return 2
    if not hierarchy.has_active_ancestors:
        return 3
    if not hierarchy.is_leaf:
        return 4
    if state_rank >= STATE_TYPE_RANK["completed"]:
        return 5
    return 0


def _manual_rank(metadata: dict[str, object]) -> int:
    value = metadata.get("manual_rank")
    if isinstance(value, int):
        return value
    return 999


def _priority_hint_rank(metadata: dict[str, object]) -> int:
    hint = metadata.get("priority_hint")
    if isinstance(hint, str):
        return PRIORITY_HINT_RANK.get(hint.lower(), 9)
    return 9


def _priority_rank(priority: int | None) -> int:
    if priority is None:
        return 9
    return priority


def _descending_timestamp(value: str) -> tuple[int, str]:
    # Sort more recent timestamps first while keeping string ordering deterministic.
    return (0, "".join(chr(255 - ord(char)) for char in value))
