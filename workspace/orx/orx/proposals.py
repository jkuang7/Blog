"""Deterministic decomposition routing and durable proposal persistence for ORX."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .continuity import ContinuityRecord, ContinuityService
from .storage import Storage


PROPOSAL_KINDS = {
    "same-scope-continuation",
    "child-issue",
    "dependency-issue",
    "improvement-issue",
    "hil-proposal",
}

DECOMPOSITION_CLASSES = {
    "same-scope-continuation": "same_scope_continuation",
    "child-issue": "child_issue",
    "dependency-issue": "dependency_issue",
    "improvement-issue": "improvement_issue",
    "hil-proposal": "hil_proposal",
}

WORKFLOW_MODES = {
    "same-scope-continuation": "same-issue",
    "child-issue": "leaf-ticket",
    "dependency-issue": "leaf-ticket",
    "improvement-issue": "leaf-ticket",
    "hil-proposal": "hil",
}


@dataclass(frozen=True)
class ProposalDecision:
    proposal_kind: str
    title: str
    rationale: str
    decomposition_class: str
    workflow_mode: str
    context: dict[str, Any]


@dataclass(frozen=True)
class ProposalRecord:
    proposal_id: int
    proposal_key: str
    issue_key: str
    runner_id: str
    proposal_kind: str
    status: str
    title: str
    rationale: str
    context: dict[str, Any]
    created_at: str
    updated_at: str

    @property
    def decomposition_class(self) -> str:
        value = self.context.get("decomposition_class")
        if isinstance(value, str) and value:
            return value
        return DECOMPOSITION_CLASSES[self.proposal_kind]

    @property
    def workflow_mode(self) -> str:
        value = self.context.get("workflow_mode")
        if isinstance(value, str) and value:
            return value
        return WORKFLOW_MODES[self.proposal_kind]

    @property
    def suggested_parent_issue_key(self) -> str | None:
        value = self.context.get("suggested_parent_issue_key")
        return value if isinstance(value, str) and value else None

    @property
    def suggested_phase_issue_key(self) -> str | None:
        value = self.context.get("suggested_phase_issue_key")
        return value if isinstance(value, str) and value else None

    @property
    def target_issue_key(self) -> str | None:
        value = self.context.get("target_issue_key")
        return value if isinstance(value, str) and value else None

    @property
    def materialized_issue_id(self) -> str | None:
        issue = self.context.get("materialized_linear_issue")
        if isinstance(issue, dict):
            value = issue.get("linear_id")
            return value if isinstance(value, str) and value else None
        return None

    @property
    def materialized_issue_identifier(self) -> str | None:
        issue = self.context.get("materialized_linear_issue")
        if isinstance(issue, dict):
            value = issue.get("identifier")
            return value if isinstance(value, str) and value else None
        return None

    @property
    def materialized_issue_url(self) -> str | None:
        issue = self.context.get("materialized_linear_issue")
        if isinstance(issue, dict):
            value = issue.get("url")
            return value if isinstance(value, str) and value else None
        return None


class ProposalStore:
    def __init__(self, storage: Storage) -> None:
        self.storage = storage

    def upsert_open_proposal(
        self,
        *,
        proposal_key: str,
        issue_key: str,
        runner_id: str,
        proposal_kind: str,
        title: str,
        rationale: str,
        context: dict[str, Any],
    ) -> ProposalRecord:
        now = _utc_now()
        with self.storage.session() as connection:
            connection.execute(
                """
                INSERT INTO continuity_proposals(
                    proposal_key,
                    issue_key,
                    runner_id,
                    proposal_kind,
                    status,
                    title,
                    rationale,
                    context_json,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?)
                ON CONFLICT(proposal_key) DO UPDATE SET
                    proposal_kind = excluded.proposal_kind,
                    status = excluded.status,
                    title = excluded.title,
                    rationale = excluded.rationale,
                    context_json = excluded.context_json,
                    updated_at = excluded.updated_at
                """,
                (
                    proposal_key,
                    issue_key,
                    runner_id,
                    proposal_kind,
                    title,
                    rationale,
                    json.dumps(context, sort_keys=True),
                    now,
                    now,
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM continuity_proposals
                WHERE proposal_key = ?
                """,
                (proposal_key,),
            ).fetchone()
        assert row is not None
        return _row_to_proposal(row)

    def list_open_proposals(self, *, issue_key: str | None = None) -> list[ProposalRecord]:
        return self.list_proposals(issue_key=issue_key, status="open")

    def get_proposal(
        self,
        *,
        proposal_id: int | None = None,
        proposal_key: str | None = None,
    ) -> ProposalRecord | None:
        if proposal_id is None and proposal_key is None:
            raise ValueError("get_proposal requires proposal_id or proposal_key.")

        query = "SELECT * FROM continuity_proposals WHERE "
        params: list[object] = []
        if proposal_id is not None:
            query += "proposal_id = ?"
            params.append(proposal_id)
        else:
            query += "proposal_key = ?"
            params.append(proposal_key)
        with self.storage.session() as connection:
            row = connection.execute(query, tuple(params)).fetchone()
        return _row_to_proposal(row) if row is not None else None

    def list_proposals(
        self,
        *,
        issue_key: str | None = None,
        status: str | None = None,
    ) -> list[ProposalRecord]:
        query = """
            SELECT * FROM continuity_proposals
            WHERE 1 = 1
        """
        params: list[object] = []
        if status is not None:
            query += " AND status = ?"
            params.append(status)
        if issue_key is not None:
            query += " AND issue_key = ?"
            params.append(issue_key)
        query += " ORDER BY proposal_id ASC"
        with self.storage.session() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [_row_to_proposal(row) for row in rows]

    def update_proposal(
        self,
        *,
        proposal_key: str,
        status: str | None = None,
        context_patch: dict[str, Any] | None = None,
    ) -> ProposalRecord:
        existing = self.get_proposal(proposal_key=proposal_key)
        if existing is None:
            raise ValueError(f"Unknown proposal_key {proposal_key!r}.")

        merged_context = dict(existing.context)
        if context_patch:
            merged_context.update(context_patch)

        now = _utc_now()
        with self.storage.session() as connection:
            connection.execute(
                """
                UPDATE continuity_proposals
                SET status = ?,
                    context_json = ?,
                    updated_at = ?
                WHERE proposal_key = ?
                """,
                (
                    status or existing.status,
                    json.dumps(merged_context, sort_keys=True),
                    now,
                    proposal_key,
                ),
            )
            row = connection.execute(
                "SELECT * FROM continuity_proposals WHERE proposal_key = ?",
                (proposal_key,),
            ).fetchone()
        assert row is not None
        return _row_to_proposal(row)


class ProposalService:
    def __init__(self, storage: Storage, continuity: ContinuityService | None = None) -> None:
        self.store = ProposalStore(storage)
        self.continuity = continuity or ContinuityService(storage)

    def route(
        self,
        issue_key: str,
        runner_id: str,
        *,
        oversized: bool = False,
        dependency_issue: str | None = None,
        improvement_title: str | None = None,
        hil_reason: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> ProposalRecord:
        state = self.continuity.get_state(issue_key, runner_id)
        if state is None:
            raise ValueError(
                f"No continuity state exists for issue {issue_key} and runner {runner_id}."
            )
        decision = classify_decomposition(
            state,
            oversized=oversized,
            dependency_issue=dependency_issue,
            improvement_title=improvement_title,
            hil_reason=hil_reason,
            context=context or {},
        )
        proposal_key = _proposal_key(state, decision)
        return self.store.upsert_open_proposal(
            proposal_key=proposal_key,
            issue_key=issue_key,
            runner_id=runner_id,
            proposal_kind=decision.proposal_kind,
            title=decision.title,
            rationale=decision.rationale,
            context=decision.context,
        )

    def list_open_proposals(self, *, issue_key: str | None = None) -> list[ProposalRecord]:
        return self.store.list_open_proposals(issue_key=issue_key)

    def get_proposal(
        self,
        *,
        proposal_id: int | None = None,
        proposal_key: str | None = None,
    ) -> ProposalRecord | None:
        return self.store.get_proposal(proposal_id=proposal_id, proposal_key=proposal_key)

    def list_proposals(
        self,
        *,
        issue_key: str | None = None,
        status: str | None = None,
    ) -> list[ProposalRecord]:
        return self.store.list_proposals(issue_key=issue_key, status=status)

    def update_proposal(
        self,
        *,
        proposal_key: str,
        status: str | None = None,
        context_patch: dict[str, Any] | None = None,
    ) -> ProposalRecord:
        return self.store.update_proposal(
            proposal_key=proposal_key,
            status=status,
            context_patch=context_patch,
        )


def classify_decomposition(
    state: ContinuityRecord,
    *,
    oversized: bool = False,
    dependency_issue: str | None = None,
    improvement_title: str | None = None,
    hil_reason: str | None = None,
    context: dict[str, Any] | None = None,
) -> ProposalDecision:
    base_context = {
        "issue_key": state.issue_key,
        "runner_id": state.runner_id,
        "objective": state.objective,
        "slice_goal": state.slice_goal,
        "next_slice": state.next_slice,
        "blockers": list(state.blockers),
        "discovered_gaps": list(state.discovered_gaps),
        "failure_signatures": list(state.failure_signatures),
        "artifact_pointers": list(state.artifact_pointers),
        "resume_context": state.resume_context,
    }
    if context:
        base_context.update(context)

    dependency_target = _normalize_optional(dependency_issue)
    improvement_target = _normalize_optional(improvement_title)
    hil_target = _normalize_optional(hil_reason)

    if hil_target is not None:
        return _build_decision(
            state,
            proposal_kind="hil-proposal",
            title=f"HIL for {state.issue_key}: {_short_summary(hil_target)}",
            rationale=hil_target,
            base_context=base_context,
        )

    if dependency_target is not None or state.blockers:
        dependency_summary = dependency_target or state.blockers[0]
        return _build_decision(
            state,
            proposal_kind="dependency-issue",
            title=f"Dependency for {state.issue_key}: {_short_summary(dependency_summary)}",
            rationale=f"Blocked by dependency: {dependency_summary}",
            base_context=base_context,
        )

    if improvement_target is not None:
        return _build_decision(
            state,
            proposal_kind="improvement-issue",
            title=improvement_target,
            rationale=f"Improvement follow-up for {state.issue_key}.",
            base_context=base_context,
        )

    if oversized:
        target = state.next_slice or state.slice_goal
        return _build_decision(
            state,
            proposal_kind="child-issue",
            title=f"Split follow-up from {state.issue_key}: {_short_summary(target)}",
            rationale="Current scope is too large for same-scope continuation.",
            base_context=base_context,
        )

    target = state.next_slice or state.slice_goal
    return _build_decision(
        state,
        proposal_kind="same-scope-continuation",
        title=f"Continue {state.issue_key}: {_short_summary(target)}",
        rationale="Work can continue within the same issue scope.",
        base_context=base_context,
    )


def _build_decision(
    state: ContinuityRecord,
    *,
    proposal_kind: str,
    title: str,
    rationale: str,
    base_context: dict[str, Any],
) -> ProposalDecision:
    decomposition_class = DECOMPOSITION_CLASSES[proposal_kind]
    workflow_mode = WORKFLOW_MODES[proposal_kind]
    context = dict(base_context)

    suggested_parent_issue_key = _normalize_optional_context_key(
        context,
        "suggested_parent_issue_key",
        "parent_issue_key",
    )
    suggested_phase_issue_key = _normalize_optional_context_key(
        context,
        "suggested_phase_issue_key",
        "phase_issue_key",
    )

    if proposal_kind == "same-scope-continuation":
        context["target_issue_key"] = state.issue_key
    elif proposal_kind != "hil-proposal":
        context["suggested_parent_issue_key"] = suggested_parent_issue_key or state.issue_key
        context["suggested_phase_issue_key"] = (
            suggested_phase_issue_key
            or context["suggested_parent_issue_key"]
        )

    context["source_issue_key"] = state.issue_key
    context["proposal_kind"] = proposal_kind
    context["decomposition_class"] = decomposition_class
    context["workflow_mode"] = workflow_mode

    return ProposalDecision(
        proposal_kind=proposal_kind,
        title=title,
        rationale=rationale,
        decomposition_class=decomposition_class,
        workflow_mode=workflow_mode,
        context=context,
    )


def _proposal_key(state: ContinuityRecord, decision: ProposalDecision) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", decision.title.lower()).strip("-")
    return f"{state.idempotency_key}:{decision.proposal_kind}:{slug}"[:160]


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_optional_context_key(
    context: dict[str, Any],
    *keys: str,
) -> str | None:
    for key in keys:
        value = context.get(key)
        if isinstance(value, str):
            normalized = value.strip()
            if normalized:
                return normalized
    return None


def _short_summary(value: str) -> str:
    summary = value.strip()
    return summary[:72]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _row_to_proposal(row: Any) -> ProposalRecord:
    return ProposalRecord(
        proposal_id=int(row["proposal_id"]),
        proposal_key=row["proposal_key"],
        issue_key=row["issue_key"],
        runner_id=row["runner_id"],
        proposal_kind=row["proposal_kind"],
        status=row["status"],
        title=row["title"],
        rationale=row["rationale"],
        context=json.loads(row["context_json"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
