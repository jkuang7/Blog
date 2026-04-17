"""Telegram-facing command adapter for ORX queue semantics."""

from __future__ import annotations

from typing import Any

from .commands import normalize_command
from .continuity import ContinuityService
from .proposals import ProposalService, ProposalRecord
from .repository import OrxRepository


class TelegramCommandAdapter:
    def __init__(
        self,
        *,
        repository: OrxRepository,
        continuity: ContinuityService,
        proposals: ProposalService,
    ) -> None:
        self.repository = repository
        self.continuity = continuity
        self.proposals = proposals

    def handle(self, body: dict[str, Any]) -> dict[str, Any]:
        command_kind = str(body["command_kind"]).strip().lower()
        issue_key = _normalize_optional(body.get("issue_key"))
        runner_id = _normalize_optional(body.get("runner_id"))

        if command_kind == "status":
            return {
                "ok": True,
                "mode": "status",
                "issue_key": issue_key,
                "runner_id": runner_id,
                "proposals": [
                    _serialize_proposal(record)
                    for record in self.proposals.list_open_proposals(issue_key=issue_key)
                ],
                "continuity": _serialize_continuity(
                    self.continuity.get_state(issue_key, runner_id)
                    if issue_key is not None and runner_id is not None
                    else None
                ),
            }

        payload = dict(body.get("payload") or {})
        telegram_context = {
            key: value
            for key, value in {
                "chat_id": body.get("chat_id"),
                "thread_id": body.get("thread_id"),
                "message_id": body.get("message_id"),
                "edit_token": body.get("edit_token"),
            }.items()
            if value is not None
        }
        if telegram_context:
            payload["telegram"] = telegram_context

        normalized = normalize_command(
            command_kind,
            issue_key=issue_key,
            runner_id=runner_id,
            payload=payload,
            replacement_key=_replacement_key(body),
        )
        record = self.repository.enqueue_normalized_command(normalized)
        return {
            "ok": True,
            "mode": "queued",
            "command": {
                "command_id": record.command_id,
                "issue_key": record.issue_key,
                "runner_id": record.runner_id,
                "command_kind": record.command_kind,
                "payload": record.payload,
                "status": record.status,
                "priority": record.priority,
            },
        }

    def intake_idea(self, body: dict[str, Any]) -> dict[str, Any]:
        issue_key = _normalize_required(body.get("issue_key"), field_name="issue_key")
        runner_id = _normalize_required(body.get("runner_id"), field_name="runner_id")
        title = _normalize_required(body.get("title"), field_name="title")
        summary = _normalize_optional(body.get("summary"))
        requires_hil = bool(body.get("requires_hil"))
        split_requested = bool(body.get("split_requested"))
        dependency_issue = _normalize_optional(body.get("dependency_issue"))
        proposal = self.proposals.route(
            issue_key,
            runner_id,
            oversized=split_requested,
            dependency_issue=dependency_issue,
            improvement_title=None if requires_hil or split_requested or dependency_issue else title,
            hil_reason=summary if requires_hil else None,
            context={
                "source": "telegram-rough-idea",
                "idea_title": title,
                "idea_summary": summary,
            },
        )
        return {
            "ok": True,
            "mode": "idea",
            "proposal": _serialize_full_proposal(proposal),
        }


def _replacement_key(body: dict[str, Any]) -> str | None:
    value = body.get("edit_token")
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        raise ValueError("edit_token cannot be empty.")
    return f"telegram-edit:{normalized}"


def _normalize_optional(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _normalize_required(value: Any, *, field_name: str) -> str:
    normalized = _normalize_optional(value)
    if normalized is None:
        raise ValueError(f"{field_name} is required.")
    return normalized


def _serialize_continuity(record: Any) -> dict[str, Any] | None:
    if record is None:
        return None
    return {
        "issue_key": record.issue_key,
        "runner_id": record.runner_id,
        "next_slice": record.next_slice,
        "last_result_status": record.last_result_status,
        "no_delta_count": record.no_delta_count,
        "consecutive_failure_count": record.consecutive_failure_count,
    }


def _serialize_proposal(record: Any) -> dict[str, Any]:
    return {
        "proposal_id": record.proposal_id,
        "proposal_kind": record.proposal_kind,
        "decomposition_class": record.decomposition_class,
        "workflow_mode": record.workflow_mode,
        "suggested_parent_issue_key": record.suggested_parent_issue_key,
        "suggested_phase_issue_key": record.suggested_phase_issue_key,
        "target_issue_key": record.target_issue_key,
        "title": record.title,
        "rationale": record.rationale,
        "status": record.status,
    }


def _serialize_full_proposal(record: ProposalRecord) -> dict[str, Any]:
    return {
        "proposal_id": record.proposal_id,
        "proposal_key": record.proposal_key,
        "proposal_kind": record.proposal_kind,
        "decomposition_class": record.decomposition_class,
        "workflow_mode": record.workflow_mode,
        "suggested_parent_issue_key": record.suggested_parent_issue_key,
        "suggested_phase_issue_key": record.suggested_phase_issue_key,
        "target_issue_key": record.target_issue_key,
        "title": record.title,
        "rationale": record.rationale,
        "status": record.status,
        "context": record.context,
    }
