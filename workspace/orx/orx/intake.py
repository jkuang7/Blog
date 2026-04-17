"""Project-aware Telegram intake planning and approval-gated Linear creation."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .linear_client import LinearClientError, LinearGraphQLClient, LinearIssue
from .metadata import parse_orx_metadata
from .mirror import LinearMirrorRepository, MirroredIssueRecord
from .registry import ProjectRegistration, ProjectRegistry
from .storage import Storage
from .tier_contract import build_stage_contract, flatten_stage_contract

_BULLET_PATTERN = re.compile(r"^\s*(?:[-*]|\d+[.)])\s+(?P<value>.+?)\s*$")
_METADATA_START = "<!-- orx:metadata:start -->"
_METADATA_END = "<!-- orx:metadata:end -->"


@dataclass(frozen=True)
class IntakeItem:
    item_key: str
    source_text: str
    title: str
    description: str
    draft_ticket: dict[str, Any]
    project_key: str | None
    project_display_name: str | None
    rationale: str
    routing_mode: str
    needs_clarification: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_key": self.item_key,
            "source_text": self.source_text,
            "title": self.title,
            "description": self.description,
            "draft_ticket": self.draft_ticket,
            "project_key": self.project_key,
            "project_display_name": self.project_display_name,
            "rationale": self.rationale,
            "routing_mode": self.routing_mode,
            "needs_clarification": self.needs_clarification,
        }


@dataclass(frozen=True)
class IntakeRecord:
    intake_id: int
    intake_key: str
    ingress_bot: str
    ingress_chat_id: int | None
    ingress_thread_id: int | None
    explicit_project_key: str | None
    default_project_key: str | None
    request_text: str
    status: str
    plan: dict[str, Any]
    planning_stage: str
    planning_model: str
    planning_reasoning_effort: str
    decomposition_model: str
    decomposition_reasoning_effort: str
    execution_model: str
    execution_reasoning_effort: str
    confidence: str
    requires_hil: bool
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class IntakeApprovalResult:
    intake: IntakeRecord
    created_issues: tuple[LinearIssue, ...]


@dataclass(frozen=True)
class _ResolvedTarget:
    project: ProjectRegistration
    team_id: str
    project_id: str | None


class IntakeService:
    def __init__(
        self,
        storage: Storage,
        *,
        registry: ProjectRegistry | None = None,
        mirror: LinearMirrorRepository | None = None,
        linear_client: LinearGraphQLClient | None = None,
    ) -> None:
        self.storage = storage
        self.registry = registry or ProjectRegistry(storage)
        self.mirror = mirror or LinearMirrorRepository(storage)
        self.linear_client_error: LinearClientError | None = None
        if linear_client is not None:
            self.linear_client = linear_client
        else:
            try:
                self.linear_client = LinearGraphQLClient.from_env()
            except LinearClientError as error:
                self.linear_client = None
                self.linear_client_error = error

    def submit(
        self,
        *,
        request_text: str,
        ingress_bot: str,
        ingress_chat_id: int | None = None,
        ingress_thread_id: int | None = None,
        explicit_project_key: str | None = None,
    ) -> IntakeRecord:
        normalized_text = _normalize_required(request_text, field_name="request_text")
        normalized_bot = _normalize_required(ingress_bot, field_name="ingress_bot")
        explicit_project = self.registry.get_project(explicit_project_key) if explicit_project_key else None
        if explicit_project_key is not None and explicit_project is None:
            raise ValueError(f"Unknown explicit project {explicit_project_key!r}.")
        default_project = explicit_project or self.registry.get_project_for_bot(normalized_bot)
        plan = self._build_plan(
            request_text=normalized_text,
            ingress_bot=normalized_bot,
            explicit_project=explicit_project,
            default_project=default_project,
        )
        status = "pending_approval" if not plan["needs_clarification"] else "clarification_required"
        return self._save_record(
            intake_key=_new_intake_key(),
            ingress_bot=normalized_bot,
            ingress_chat_id=ingress_chat_id,
            ingress_thread_id=ingress_thread_id,
            explicit_project_key=explicit_project.project_key if explicit_project is not None else None,
            default_project_key=default_project.project_key if default_project is not None else None,
            request_text=normalized_text,
            status=status,
            plan=plan,
        )

    def get_intake(
        self,
        *,
        intake_id: int | None = None,
        intake_key: str | None = None,
    ) -> IntakeRecord | None:
        if intake_id is None and intake_key is None:
            raise ValueError("get_intake requires intake_id or intake_key.")
        query = "SELECT * FROM intake_requests WHERE "
        params: tuple[object, ...]
        if intake_id is not None:
            query += "intake_id = ?"
            params = (intake_id,)
        else:
            query += "intake_key = ?"
            params = (_normalize_required(intake_key, field_name="intake_key"),)
        with self.storage.session() as connection:
            row = connection.execute(query, params).fetchone()
        return _row_to_intake(row) if row is not None else None

    def reject(
        self,
        *,
        intake_id: int | None = None,
        intake_key: str | None = None,
        note: str | None = None,
    ) -> IntakeRecord:
        record = self._require_intake(intake_id=intake_id, intake_key=intake_key)
        plan = dict(record.plan)
        if note is not None and note.strip():
            plan["rejection_note"] = note.strip()
        return self._update_record(
            intake_key=record.intake_key,
            status="rejected",
            plan=plan,
        )

    def approve(
        self,
        *,
        intake_id: int | None = None,
        intake_key: str | None = None,
    ) -> IntakeApprovalResult:
        record = self._require_intake(intake_id=intake_id, intake_key=intake_key)
        if record.status != "pending_approval":
            raise ValueError(f"Intake {record.intake_key} is {record.status!r}, not pending approval.")
        if bool(record.plan.get("needs_clarification")):
            raise ValueError(f"Intake {record.intake_key} still needs clarification before approval.")
        client = self._require_linear_client()
        stage_metadata = _stage_metadata_from_plan(record.plan)
        created_issues: list[LinearIssue] = []
        created_payloads: list[dict[str, Any]] = []
        created_by_capsule: dict[str, LinearIssue] = {}
        capsules = _plan_capsules(record.plan)
        for capsule in capsules:
            if capsule.get("ticket_role") == "leaf" and capsule.get("project_key") is None:
                raise ValueError(f"Leaf capsule {capsule.get('capsule_key')} has no project assignment.")
            target = self._resolve_capsule_target(capsule, capsules)
            parent_capsule_key = _normalize_optional(capsule.get("parent_capsule_key"))
            parent_issue = created_by_capsule.get(parent_capsule_key) if parent_capsule_key else None
            description = _build_capsule_issue_description(
                intake=record,
                capsule=capsule,
                target=target.project if target is not None else None,
            )
            created = client.create_issue(
                team_id=target.team_id if target is not None else self._resolve_default_team_id(capsules),
                title=str(capsule["title"]),
                description=description,
                parent_id=parent_issue.linear_id if parent_issue is not None else None,
                project_id=_capsule_project_id(capsule, target),
            )
            enhanced_description = _decorate_created_capsule_description(
                intake=record,
                capsule=capsule,
                target=target.project if target is not None else None,
                issue=created,
                parent_issue=parent_issue,
                base_description=description,
            )
            if enhanced_description != description:
                created = client.update_issue(
                    issue_ref=created.identifier or created.linear_id,
                    description=enhanced_description,
                )
            mirrored_team_id = created.team_id or (
                target.team_id if target is not None else self._resolve_default_team_id(capsules)
            )
            mirrored_team_name = created.team_name or "Projects"
            source_updated_at = _utc_now()
            self.mirror.upsert_issue(
                linear_id=created.linear_id,
                identifier=created.identifier or created.linear_id,
                title=created.title,
                description=created.description,
                team_id=mirrored_team_id,
                team_name=mirrored_team_name,
                state_id=created.state_id,
                state_name=created.state_name,
                state_type=created.state_type,
                parent_linear_id=created.parent_id,
                parent_identifier=created.parent_identifier,
                project_id=created.project_id or _capsule_project_id(capsule, target),
                project_name=created.project_name
                or (target.project.display_name if target is not None else None),
                source_updated_at=source_updated_at,
                metadata={
                    **parse_orx_metadata(created.description).metadata,
                    "project_key": capsule.get("project_key"),
                    "orx_ticket_role": capsule.get("ticket_role"),
                    "no_auto_select": capsule.get("ticket_role") != "leaf",
                    "runnable": capsule.get("ticket_role") == "leaf",
                    "capsule_key": capsule.get("capsule_key"),
                    "parent_capsule_key": capsule.get("parent_capsule_key"),
                    "source_item_keys": list(capsule.get("source_item_keys") or []),
                    "planning_stage": stage_metadata["planning_stage"],
                    "planning_model": stage_metadata["planning_model"],
                    "planning_reasoning_effort": stage_metadata["planning_reasoning_effort"],
                    "decomposition_model": stage_metadata["decomposition_model"],
                    "decomposition_reasoning_effort": stage_metadata["decomposition_reasoning_effort"],
                    "execution_model": stage_metadata["execution_model"],
                    "execution_reasoning_effort": stage_metadata["execution_reasoning_effort"],
                    "confidence": stage_metadata["confidence"],
                    "requires_hil": stage_metadata["requires_hil"],
                },
            )
            created_issues.append(created)
            created_by_capsule[str(capsule["capsule_key"])] = created
            created_payloads.append(
                {
                    "capsule_key": capsule["capsule_key"],
                    "ticket_role": capsule["ticket_role"],
                    "runnable": capsule["ticket_role"] == "leaf",
                    "item_keys": list(capsule.get("source_item_keys") or []),
                    "project_key": capsule.get("project_key"),
                    "linear_id": created.linear_id,
                    "identifier": created.identifier,
                    "title": created.title,
                    "url": created.url,
                    "parent_identifier": created.parent_identifier,
                }
            )
            if (
                capsule.get("ticket_role") == "leaf"
                and target is not None
                and target.project.assigned_bot is not None
                and target.project.owning_bot != record.ingress_bot
            ):
                self.registry.create_notification(
                    project_key=target.project.project_key,
                    target_bot=target.project.owning_bot,
                    ingress_bot=record.ingress_bot,
                    issue_key=created.identifier,
                    kind="intake-created",
                    payload={
                        "message": (
                            f"Created `{created.identifier}` from intake `{record.intake_key}`.\n"
                            f"Title: {created.title}"
                        ),
                        "intake_key": record.intake_key,
                        "project_key": target.project.project_key,
                        "target_chat_id": target.project.owner_chat_id,
                        "target_thread_id": _project_execution_thread_id(target.project),
                        "control_thread_id": target.project.owner_thread_id,
                        "execution_thread_id": _project_execution_thread_id(target.project),
                    },
                )
        plan = dict(record.plan)
        plan["created_issues"] = created_payloads
        updated = self._update_record(
            intake_key=record.intake_key,
            status="materialized",
            plan=plan,
        )
        return IntakeApprovalResult(
            intake=updated,
            created_issues=tuple(created_issues),
        )

    def _build_plan(
        self,
        *,
        request_text: str,
        ingress_bot: str,
        explicit_project: ProjectRegistration | None,
        default_project: ProjectRegistration | None,
    ) -> dict[str, Any]:
        projects = self.registry.list_projects()
        project_aliases = {
            project.project_key: _project_aliases(project)
            for project in projects
        }
        items_text = _split_labeled_request_items(request_text, aliases=project_aliases)
        if not items_text:
            items_text = _split_request_items(request_text)
        oversized = _is_oversized_intake(request_text=request_text, items_text=items_text)
        items: list[IntakeItem] = []
        for index, item_text in enumerate(items_text, start=1):
            assigned_project = explicit_project
            matched_keys = set()
            leading_project_key: str | None = None
            if assigned_project is None:
                leading_project_key = _leading_project_match(item_text, aliases=project_aliases)
                if leading_project_key is not None:
                    matched_keys = {leading_project_key}
                else:
                    matched_keys = _match_projects(item_text, projects=projects, aliases=project_aliases)
                if len(matched_keys) == 1:
                    assigned_project = self.registry.get_project(next(iter(matched_keys)))
                elif not matched_keys:
                    assigned_project = default_project

            needs_clarification = assigned_project is None
            routing_mode = "explicit-project" if explicit_project is not None else "owner-bot-default"
            rationale = "Defaulted to the receiving bot's project."
            if matched_keys:
                if len(matched_keys) == 1 and assigned_project is not None:
                    if leading_project_key is not None:
                        if default_project is not None and assigned_project.project_key != default_project.project_key:
                            routing_mode = "rerouted-project"
                            rationale = (
                                f"Matched leading project label for `{assigned_project.project_key}` instead of the bot default."
                            )
                        else:
                            routing_mode = "explicit-project"
                            rationale = f"Matched leading project label for `{assigned_project.project_key}`."
                    elif default_project is not None and assigned_project.project_key != default_project.project_key:
                        routing_mode = "rerouted-project"
                        rationale = (
                            f"Matched explicit project reference for `{assigned_project.project_key}` instead of the bot default."
                        )
                    else:
                        routing_mode = "explicit-project"
                        rationale = f"Matched explicit project reference for `{assigned_project.project_key}`."
                else:
                    routing_mode = "clarification-required"
                    rationale = "Matched more than one project reference in the same intake item."
                    needs_clarification = True
                    assigned_project = None
            elif assigned_project is None:
                routing_mode = "clarification-required"
                rationale = "No default or explicit project match was available for this intake item."
            elif default_project is None and explicit_project is None:
                routing_mode = "clarification-required"
                rationale = "The ingress bot is not registered to a default project."
                needs_clarification = True
                assigned_project = None

            items.append(
                IntakeItem(
                    item_key=f"item-{index}",
                    source_text=item_text.strip(),
                    title=_title_from_request(item_text),
                    description=item_text.strip(),
                    draft_ticket=_build_draft_ticket(
                        source_text=item_text,
                        title=_title_from_request(item_text),
                        project=assigned_project,
                        routing_mode=routing_mode,
                        rationale=rationale,
                        needs_clarification=needs_clarification,
                    ),
                    project_key=None if assigned_project is None else assigned_project.project_key,
                    project_display_name=None if assigned_project is None else assigned_project.display_name,
                    rationale=rationale,
                    routing_mode=routing_mode,
                    needs_clarification=needs_clarification,
                )
            )

        groups: list[dict[str, Any]] = []
        for project in projects:
            grouped_items = [item.to_dict() for item in items if item.project_key == project.project_key]
            if not grouped_items:
                continue
            groups.append(
                {
                    "project_key": project.project_key,
                    "display_name": project.display_name,
                    "items": grouped_items,
                }
            )

        unassigned = [item.to_dict() for item in items if item.project_key is None]
        if unassigned:
            groups.append(
                {
                    "project_key": None,
                    "display_name": "Clarification required",
                    "items": unassigned,
                }
            )
        assigned_project_count = len({item.project_key for item in items if item.project_key is not None})
        needs_clarification = any(item.needs_clarification for item in items)
        stage_contract = build_stage_contract(
            item_count=len(items),
            project_count=assigned_project_count,
            needs_clarification=needs_clarification,
            oversized=oversized,
        )
        planning_result = _build_planning_result(
            request_text=request_text,
            items=items,
            groups=groups,
            stage_contract=stage_contract,
            oversized=oversized,
        )
        decomposition = _build_decomposition_plan(
            request_text=request_text,
            items=items,
            stage_contract=stage_contract,
            planning_result=planning_result,
        )
        return {
            "ingress_bot": ingress_bot,
            "routing_mode": "owner-bot-default" if explicit_project is None else "explicit-project",
            "default_project_key": None if default_project is None else default_project.project_key,
            "needs_clarification": needs_clarification,
            "stage_contract": stage_contract,
            "planning_result": planning_result,
            "decomposition": decomposition,
            "items": [item.to_dict() for item in items],
            "groups": groups,
        }

    def _save_record(
        self,
        *,
        intake_key: str,
        ingress_bot: str,
        ingress_chat_id: int | None,
        ingress_thread_id: int | None,
        explicit_project_key: str | None,
        default_project_key: str | None,
        request_text: str,
        status: str,
        plan: dict[str, Any],
    ) -> IntakeRecord:
        now = _utc_now()
        with self.storage.session() as connection:
            stage_metadata = _stage_metadata_from_plan(plan)
            connection.execute(
                """
                INSERT INTO intake_requests(
                    intake_key,
                    ingress_bot,
                    ingress_chat_id,
                    ingress_thread_id,
                    explicit_project_key,
                    default_project_key,
                    request_text,
                    status,
                    plan_json,
                    planning_stage,
                    planning_model,
                    planning_reasoning_effort,
                    decomposition_model,
                    decomposition_reasoning_effort,
                    execution_model,
                    execution_reasoning_effort,
                    confidence,
                    requires_hil,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    intake_key,
                    ingress_bot,
                    ingress_chat_id,
                    ingress_thread_id,
                    explicit_project_key,
                    default_project_key,
                    request_text,
                    status,
                    json.dumps(plan, sort_keys=True),
                    stage_metadata["planning_stage"],
                    stage_metadata["planning_model"],
                    stage_metadata["planning_reasoning_effort"],
                    stage_metadata["decomposition_model"],
                    stage_metadata["decomposition_reasoning_effort"],
                    stage_metadata["execution_model"],
                    stage_metadata["execution_reasoning_effort"],
                    stage_metadata["confidence"],
                    1 if stage_metadata["requires_hil"] else 0,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM intake_requests WHERE intake_key = ?",
                (intake_key,),
            ).fetchone()
        assert row is not None
        return _row_to_intake(row)

    def _update_record(
        self,
        *,
        intake_key: str,
        status: str,
        plan: dict[str, Any],
    ) -> IntakeRecord:
        now = _utc_now()
        with self.storage.session() as connection:
            stage_metadata = _stage_metadata_from_plan(plan)
            connection.execute(
                """
                UPDATE intake_requests
                SET status = ?,
                    plan_json = ?,
                    planning_stage = ?,
                    planning_model = ?,
                    planning_reasoning_effort = ?,
                    decomposition_model = ?,
                    decomposition_reasoning_effort = ?,
                    execution_model = ?,
                    execution_reasoning_effort = ?,
                    confidence = ?,
                    requires_hil = ?,
                    updated_at = ?
                WHERE intake_key = ?
                """,
                (
                    status,
                    json.dumps(plan, sort_keys=True),
                    stage_metadata["planning_stage"],
                    stage_metadata["planning_model"],
                    stage_metadata["planning_reasoning_effort"],
                    stage_metadata["decomposition_model"],
                    stage_metadata["decomposition_reasoning_effort"],
                    stage_metadata["execution_model"],
                    stage_metadata["execution_reasoning_effort"],
                    stage_metadata["confidence"],
                    1 if stage_metadata["requires_hil"] else 0,
                    now,
                    intake_key,
                ),
            )
            row = connection.execute(
                "SELECT * FROM intake_requests WHERE intake_key = ?",
                (intake_key,),
            ).fetchone()
        assert row is not None
        return _row_to_intake(row)

    def _require_intake(
        self,
        *,
        intake_id: int | None = None,
        intake_key: str | None = None,
    ) -> IntakeRecord:
        record = self.get_intake(intake_id=intake_id, intake_key=intake_key)
        if record is None:
            raise ValueError("Unknown intake.")
        return record

    def _require_linear_client(self) -> LinearGraphQLClient:
        if self.linear_client is None:
            reason = (
                str(self.linear_client_error)
                if self.linear_client_error is not None
                else "Set ORX_LINEAR_API_KEY or LINEAR_API_KEY to create intake tickets in Linear."
            )
            raise LinearClientError(reason)
        return self.linear_client

    def _resolve_target(self, project_key: str) -> _ResolvedTarget:
        registration = self.registry.get_project(project_key)
        if registration is None:
            raise ValueError(f"Unknown project {project_key!r}.")
        metadata = dict(registration.metadata)
        team_id = metadata.get("linear_team_id")
        project_id = metadata.get("linear_project_id")
        if isinstance(team_id, str) and team_id.strip():
            return _ResolvedTarget(project=registration, team_id=team_id.strip(), project_id=_normalize_optional(project_id))

        issues = [
            issue
            for issue in self.mirror.list_issues()
            if _issue_project_key(issue) == registration.project_key
        ]
        if not issues:
            raise ValueError(
                f"Cannot infer Linear target for project `{registration.project_key}`. "
                "Register linear_team_id metadata or sync at least one issue for that project."
            )
        issue = issues[0]
        return _ResolvedTarget(project=registration, team_id=issue.team_id, project_id=issue.project_id)

    def _resolve_capsule_target(
        self,
        capsule: dict[str, Any],
        capsules: list[dict[str, Any]],
    ) -> _ResolvedTarget | None:
        project_key = _normalize_optional(capsule.get("project_key"))
        if project_key is not None:
            return self._resolve_target(project_key)
        for candidate in capsules:
            candidate_project = _normalize_optional(candidate.get("project_key"))
            if candidate_project is not None:
                return self._resolve_target(candidate_project)
        return None

    def _resolve_default_team_id(self, capsules: list[dict[str, Any]]) -> str:
        target = self._resolve_capsule_target({"project_key": None}, capsules)
        if target is None:
            raise ValueError("Unable to infer a Linear team for grouped intake materialization.")
        return target.team_id


def _plan_capsules(plan: dict[str, Any]) -> list[dict[str, Any]]:
    decomposition = plan.get("decomposition")
    if isinstance(decomposition, dict):
        raw_capsules = decomposition.get("capsules")
        if isinstance(raw_capsules, list) and raw_capsules:
            return [dict(capsule) for capsule in raw_capsules if isinstance(capsule, dict)]
    stage_metadata = _stage_metadata_from_plan(plan)
    items = _plan_items(plan)
    planning_result = plan.get("planning_result") if isinstance(plan.get("planning_result"), dict) else {}
    return _build_decomposition_plan(
        request_text=str(plan.get("request_text") or ""),
        items=items,
        stage_contract={
            "stages": [
                {
                    "stage": "planning",
                    "selected_model": stage_metadata["planning_model"],
                    "selected_reasoning_effort": stage_metadata["planning_reasoning_effort"],
                },
                {
                    "stage": "decomposition",
                    "selected_model": stage_metadata["decomposition_model"],
                    "selected_reasoning_effort": stage_metadata["decomposition_reasoning_effort"],
                },
                {
                    "stage": "execution",
                    "selected_model": stage_metadata["execution_model"],
                    "selected_reasoning_effort": stage_metadata["execution_reasoning_effort"],
                },
            ],
            "confidence": stage_metadata["confidence"],
            "requires_hil": stage_metadata["requires_hil"],
        },
        planning_result=planning_result,
    )["capsules"]


def _capsule_project_id(
    capsule: dict[str, Any],
    target: _ResolvedTarget | None,
) -> str | None:
    if target is None:
        return None
    if capsule.get("ticket_role") == "umbrella" and capsule.get("project_key") is None:
        return None
    return target.project_id


def _build_capsule_issue_description(
    *,
    intake: IntakeRecord,
    capsule: dict[str, Any],
    target: ProjectRegistration | None,
) -> str:
    stage_metadata = _stage_metadata_from_plan(intake.plan)
    draft_ticket = dict(capsule.get("draft_ticket") or {})
    lines = _draft_ticket_lines(draft_ticket)
    technical_notes_index = _find_section(lines, "## Technical Notes")
    if technical_notes_index is not None:
        insert_at = technical_notes_index + 1
        while insert_at < len(lines) and not lines[insert_at].startswith("## "):
            insert_at += 1
        notes = [
            f"- Source intake: `{intake.intake_key}`",
            f"- Ingress bot: `{intake.ingress_bot}`",
            f"- Ingress chat: `{intake.ingress_chat_id or 'unknown'}`",
            f"- Ingress thread: `{intake.ingress_thread_id or 'main'}`",
            f"- Ticket role: `{capsule.get('ticket_role')}`",
            f"- Planning tier: `{stage_metadata['planning_model']}` / `{stage_metadata['planning_reasoning_effort']}`",
        ]
        if capsule.get("ticket_role") == "leaf":
            notes.append(
                f"- Execution recommendation: `{stage_metadata['execution_model']}` / `{stage_metadata['execution_reasoning_effort']}`"
            )
        lines[insert_at:insert_at] = notes + [""]
    return "\n".join(lines)


def _decorate_created_capsule_description(
    *,
    intake: IntakeRecord,
    capsule: dict[str, Any],
    target: ProjectRegistration | None,
    issue: LinearIssue,
    parent_issue: LinearIssue | None,
    base_description: str,
) -> str:
    stage_metadata = _stage_metadata_from_plan(intake.plan)
    identifier = issue.identifier or issue.linear_id
    packet_context = _packet_execution_context(
        capsule=capsule,
        target=target,
        issue=issue,
        parent_issue=parent_issue,
    )
    metadata = {
        "branch": packet_context["branch"],
        "capsule_key": capsule.get("capsule_key"),
        "codex_execution_brief": _compact_codex_execution_brief(
            capsule=capsule,
            packet_context=packet_context,
            repo_root=None if target is None else target.repo_root,
        ),
        "codex_context_goal": (capsule.get("draft_ticket") or {}).get("goal"),
        "codex_context_scope_in": (
            ((capsule.get("draft_ticket") or {}).get("scope") or {}).get("in_scope")
            if isinstance((capsule.get("draft_ticket") or {}).get("scope"), dict)
            else []
        ),
        "codex_context_scope_out": (
            ((capsule.get("draft_ticket") or {}).get("scope") or {}).get("out_of_scope")
            if isinstance((capsule.get("draft_ticket") or {}).get("scope"), dict)
            else []
        ),
        "codex_context_why": (capsule.get("draft_ticket") or {}).get("why"),
        "codex_context_requirements": (capsule.get("draft_ticket") or {}).get("requirements"),
        "codex_context_acceptance_criteria": (capsule.get("draft_ticket") or {}).get("acceptance_criteria"),
        "codex_context_technical_notes": (capsule.get("draft_ticket") or {}).get("technical_notes"),
        "codex_context_dependencies_risks": (capsule.get("draft_ticket") or {}).get("dependencies_risks"),
        "codex_context_definition_of_done": (capsule.get("draft_ticket") or {}).get("definition_of_done"),
        "codex_execution_model": stage_metadata["execution_model"],
        "codex_execution_reasoning_effort": stage_metadata["execution_reasoning_effort"],
        "decomposition_model": stage_metadata["decomposition_model"],
        "decomposition_reasoning_effort": stage_metadata["decomposition_reasoning_effort"],
        "intake_confidence": stage_metadata["confidence"],
        "ingress_bot": intake.ingress_bot,
        "ingress_chat_id": intake.ingress_chat_id,
        "ingress_thread_id": intake.ingress_thread_id,
        "linear_identifier": identifier,
        "linear_url": issue.url,
        "merge_into": packet_context["merge_into"],
        "orx_ticket_role": capsule.get("ticket_role"),
        "packet_branch": packet_context["branch"],
        "packet_key": packet_context["packet_key"],
        "packet_scope": packet_context["scope"],
        "packet_worktree_path": packet_context["worktree_path"],
        "project_key": capsule.get("project_key"),
        "project_name": None if target is None else target.display_name,
        "repo_root": None if target is None else target.repo_root,
        "runnable": capsule.get("ticket_role") == "leaf",
        "runner_id": "main",
        "selection_lane": "orx_linear",
        "source_intake_key": intake.intake_key,
        "source_item_keys": list(capsule.get("source_item_keys") or []),
        "source_request_texts": [str(text).strip() for text in (capsule.get("source_request_texts") or []) if str(text).strip()],
        "worktree_path": packet_context["worktree_path"],
    }
    source_request_texts = [str(text).strip() for text in (capsule.get("source_request_texts") or []) if str(text).strip()]
    lines = base_description.splitlines()
    lines.extend(
        [
            "",
            "## Source Request",
            "",
            f"- Source intake: `{intake.intake_key}`",
            f"- Original request: {'; '.join(source_request_texts) or intake.request_text}",
            f"- Ingress bot: `{intake.ingress_bot}`",
            f"- Ingress chat: `{intake.ingress_chat_id or 'unknown'}`",
            f"- Ingress thread: `{intake.ingress_thread_id or 'main'}`",
            "",
            "## Project Context",
            "",
            f"- Ticket role: `{capsule.get('ticket_role')}`",
            f"- Project key: `{capsule.get('project_key') or 'cross-project'}`",
        ]
    )
    if target is not None:
        lines.extend(
            [
                f"- Project: `{target.display_name}`",
                f"- Repo root: `{target.repo_root}`",
                "- Runner lane: `main`",
                "- Selection lane: `orx_linear`",
            ]
        )
    if capsule.get("ticket_role") == "leaf":
        lines.extend(
            [
                "",
                "## Execution Context",
                "",
                f"- Linear issue: `{identifier}`",
                "- Stateless execution: `yes`",
                f"- Worktree path: `{packet_context['worktree_path']}`",
                f"- Branch: `{packet_context['branch']}`",
                f"- Repo root: `{target.repo_root}`",
                f"- Execution recommendation: `{stage_metadata['execution_model']}` / `{stage_metadata['execution_reasoning_effort']}`",
            ]
        )
        if issue.url:
            lines.append(f"- Linear URL: {issue.url}")
        lines.extend(
            [
                "",
                "## Packet Context",
                "",
                f"- Packet scope: `{packet_context['scope']}`",
                f"- Packet key: `{packet_context['packet_key']}`",
                f"- Merge strategy: `{packet_context['merge_strategy']}`",
                f"- Merge target: `{packet_context['merge_into']}`",
            ]
        )
        lines.extend(
            [
                "",
                "## Codex Execution Brief",
                "",
                f"- Primary outcome: {(capsule.get('draft_ticket') or {}).get('goal') or capsule.get('title')}",
                f"- Why it matters: {(capsule.get('draft_ticket') or {}).get('why') or capsule.get('problem')}",
                f"- Keep scope: {_brief_list((capsule.get('draft_ticket') or {}).get('scope', {}).get('in_scope') if isinstance((capsule.get('draft_ticket') or {}).get('scope'), dict) else [], default='Stay within the requested project and outcome.')}",
                f"- Avoid scope: {_brief_list((capsule.get('draft_ticket') or {}).get('scope', {}).get('out_of_scope') if isinstance((capsule.get('draft_ticket') or {}).get('scope'), dict) else [], default='Do not widen the ticket beyond the stated outcome.')}",
                f"- Verify by: {_brief_list((capsule.get('draft_ticket') or {}).get('acceptance_criteria'), default='Record the requested verification before closing the work.')}",
                f"- Done when: {_brief_list((capsule.get('draft_ticket') or {}).get('definition_of_done'), default='The requested outcome is implemented and verified.')}",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "## Coordination Notes",
                "",
                "- This umbrella ticket is coordination-only and should not be selected for execution.",
                f"- Child capsules: {', '.join(str(key) for key in capsule.get('child_capsule_keys') or [])}",
            ]
        )
    return _merge_issue_metadata("\n".join(lines), metadata)


def _normalize_required(value: str | None, *, field_name: str) -> str:
    normalized = _normalize_optional(value)
    if normalized is None:
        raise ValueError(f"{field_name} is required.")
    return normalized


def _normalize_optional(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _new_intake_key() -> str:
    return f"intake-{uuid4().hex[:12]}"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _title_from_request(text: str) -> str:
    normalized = _normalize_request_subject(text)
    if not normalized:
        return "New intake item"
    return normalized[0].upper() + normalized[1:]


def _normalize_request_subject(text: str) -> str:
    normalized = " ".join(text.strip().split())
    normalized = re.sub(r"^[A-Za-z0-9_-]+\s*:\s*", "", normalized)
    return normalized


def _build_draft_ticket(
    *,
    source_text: str,
    title: str,
    project: ProjectRegistration | None,
    routing_mode: str,
    rationale: str,
    needs_clarification: bool,
) -> dict[str, Any]:
    project_label = project.display_name if project is not None else "the correct project"
    normalized_request = _normalize_request_subject(source_text) or source_text.strip() or "Clarify the requested work."
    clean_title = title.strip() or "New intake item"
    why = _draft_ticket_why(
        normalized_request=normalized_request,
        clean_title=clean_title,
        project_label=project_label,
        needs_clarification=needs_clarification,
    )
    goal = _draft_ticket_goal(
        normalized_request=normalized_request,
        clean_title=clean_title,
        project_label=project_label,
        needs_clarification=needs_clarification,
    )
    in_scope = [_draft_ticket_scope_summary(normalized_request=normalized_request, clean_title=clean_title, needs_clarification=needs_clarification)]
    if project is not None:
        in_scope.append(
            f"Touch only the code, docs, tests, and runtime wiring inside `{project_label}` that are needed for this outcome."
        )
    out_of_scope = [
        "Unrelated cleanup or refactors that are not required for this outcome.",
        "Cross-project changes unless they are split into their own tickets.",
    ]
    requirements = [
        f"Keep the change scoped to `{project_label}`." if project is not None else "Resolve the correct owning project before implementation.",
        "Preserve existing behavior outside the requested scope unless this ticket explicitly changes it.",
        "Update tests, documentation, or operational notes where they are needed to support the requested outcome.",
    ]
    exact_output_required = [
        f"Code changes for `{clean_title}` in `{project_label}`." if project is not None else "A single-project implementation-ready ticket with a clear owner.",
        "Updated or added tests and verification evidence.",
        "A concise implementation summary with any remaining blockers or follow-up work called out explicitly.",
    ]
    ordered_steps = [
        "Read the ticket body, source request, project context, execution context, and latest handoff before changing code.",
        f"Confirm the current behavior or failure mode for `{clean_title}` in the narrowest truthful way.",
        f"Implement the smallest valid change that makes `{clean_title}` true without widening scope.",
        "Run the declared verification surface and update tests or docs where the outcome requires it.",
        "Re-check the ticket against scope, constraints, and success criteria before marking the slice complete.",
    ]
    verification = [
        "Run the narrowest truthful test or verification command that proves the requested outcome.",
        "Record what was verified, what was not verified, and why.",
        "Fail the slice if the requested outcome cannot be shown with concrete evidence.",
    ]
    stopping_conditions = [
        "Stop if the required owner repo, file family, or dependency is not available in the current execution context.",
        "Stop if more than one architectural path appears valid and the ticket does not settle the choice.",
        "Stop if verification fails outside the intended scope and the failure cannot be resolved without widening the ticket.",
    ]
    blocked_escalation = [
        "Do not guess when blocked. Update the ticket handoff with the exact blocker, lessons learned, and current state.",
        "Escalate to a higher-tier planning pass if the work now requires architecture, decomposition, or cross-project routing judgment.",
        "Create or request a follow-up ticket when a prerequisite, owner mismatch, or new narrowly scoped dependency is discovered.",
    ]
    acceptance_criteria = [
        f"Given the current problem described above in `{project_label}`" if project is not None else "Given the current request and project ambiguity",
        "When this ticket is completed",
        f"Then `{clean_title}` is true and verified in `{project_label}`." if project is not None else "Then the work has a single clear owner and an implementation-ready ticket description.",
    ]
    technical_notes = [
        f"Routing mode: `{routing_mode}`",
        f"Routing rationale: {rationale}",
        f"Original request: {source_text.strip()}",
    ]
    dependencies_risks = [
        rationale if needs_clarification else "If implementation reveals additional project owners, split that follow-up into separate tickets instead of broadening this one in place.",
    ]
    definition_of_done = [
        f"The requested outcome is implemented in `{project_label}`." if project is not None else "The correct owning project is identified and the ticket is implementation-ready.",
        "Verification for the requested outcome is recorded.",
        "The final change stays within the scope and requirements above.",
    ]
    return {
        "title": clean_title,
        "why": why,
        "objective": goal,
        "goal": goal,
        "scope": {
            "in_scope": in_scope,
            "out_of_scope": out_of_scope,
        },
        "constraints": requirements,
        "requirements": requirements,
        "success_criteria": acceptance_criteria,
        "exact_output_required": exact_output_required,
        "ordered_steps": ordered_steps,
        "verification": verification,
        "stopping_conditions": stopping_conditions,
        "blocked_escalation": blocked_escalation,
        "acceptance_criteria": acceptance_criteria,
        "technical_notes": technical_notes,
        "dependencies_risks": dependencies_risks,
        "definition_of_done": definition_of_done,
    }


def _draft_ticket_why(
    *,
    normalized_request: str,
    clean_title: str,
    project_label: str,
    needs_clarification: bool,
) -> str:
    request = normalized_request.rstrip(".")
    if needs_clarification:
        return (
            f"The request is not safely executable yet because ownership is still ambiguous. "
            f"`{clean_title}` needs a single clear project owner before implementation starts."
        )
    lowered = request.lower()
    if lowered.startswith(("audit ", "verify ", "investigate ", "check ")):
        return (
            f"There is not enough confidence that {request} in `{project_label}` is correct today, "
            "which makes the current workflow harder to trust."
        )
    if lowered.startswith(("replace ", "remove ", "rename ", "migrate ")):
        return (
            f"`{project_label}` still has outdated behavior around {request}, which makes the current workflow "
            "harder for operators to understand."
        )
    if lowered.startswith(("tighten ", "harden ", "stabilize ", "fix ")):
        return (
            f"The current behavior around {request} in `{project_label}` is still weaker than it should be, "
            "which creates avoidable operator or runtime risk."
        )
    return (
        f"`{project_label}` still needs this outcome: {request}. Until that is true, the current workflow remains incomplete."
    )


def _draft_ticket_goal(
    *,
    normalized_request: str,
    clean_title: str,
    project_label: str,
    needs_clarification: bool,
) -> str:
    if needs_clarification:
        return "Leave this work with one clear owning project and an implementation-ready ticket that can be executed without ambiguity."
    lowered = normalized_request.lower()
    if lowered.startswith(("audit ", "verify ", "investigate ", "check ")):
        return (
            f"Establish whether `{clean_title}` is already true in `{project_label}` and, if not, identify and land the exact follow-up needed to make it true."
        )
    return f"Make `{clean_title}` true in `{project_label}` and leave behind a verified, execution-ready change."


def _draft_ticket_scope_summary(
    *,
    normalized_request: str,
    clean_title: str,
    needs_clarification: bool,
) -> str:
    if needs_clarification:
        return "Clarify ownership and reshape this request into an implementation-ready ticket."
    summary = (normalized_request or clean_title).strip().rstrip(".")
    if not summary:
        return f"Deliver `{clean_title}`."
    return summary[0].upper() + summary[1:] + "."


def _is_oversized_intake(*, request_text: str, items_text: list[str]) -> bool:
    normalized = _normalize_request_subject(request_text)
    if len(normalized) >= 280:
        return True
    if len(items_text) >= 3:
        return True
    return any(len(item.strip()) >= 180 for item in items_text)


def _build_planning_result(
    *,
    request_text: str,
    items: list[IntakeItem],
    groups: list[dict[str, Any]],
    stage_contract: dict[str, Any],
    oversized: bool,
) -> dict[str, Any]:
    stage_metadata = flatten_stage_contract(stage_contract)
    leaf_count = len(items)
    project_count = len({item.project_key for item in items if item.project_key is not None})
    needs_clarification = bool(stage_metadata["requires_hil"])
    recommendation = "single_ticket"
    if needs_clarification:
        recommendation = "clarification_required"
    elif leaf_count > 1 or project_count > 1:
        recommendation = "split_ticket_set"

    complexity_signals: list[str] = []
    if oversized:
        complexity_signals.append("oversized")
    if leaf_count > 1:
        complexity_signals.append("multi_item")
    if project_count > 1:
        complexity_signals.append("multi_project")
    if needs_clarification:
        complexity_signals.append("clarification_required")

    grouped_work_items: list[dict[str, Any]] = []
    for index, group in enumerate(groups, start=1):
        group_items = group.get("items") if isinstance(group.get("items"), list) else []
        group_key = group.get("project_key") or f"clarification-{index}"
        item_keys = [str(item.get("item_key")) for item in group_items if isinstance(item, dict)]
        rationales = sorted(
            {
                str(item.get("rationale")).strip()
                for item in group_items
                if isinstance(item, dict) and str(item.get("rationale", "")).strip()
            }
        )
        grouped_work_items.append(
            {
                "group_key": f"group-{group_key}",
                "project_key": group.get("project_key"),
                "display_name": group.get("display_name"),
                "item_keys": item_keys,
                "leaf_count": len(item_keys),
                "recommended_parent": leaf_count > 1,
                "issue_shape": "umbrella_with_leaves" if leaf_count > 1 else "single_leaf",
                "routing_summary": rationales[0]
                if rationales
                else "No routing rationale was recorded for this group.",
            }
        )

    if recommendation == "clarification_required":
        split_recommendation = (
            "Stop before Linear mutation and ask for clarification because at least one intake item "
            "still has ambiguous ownership."
        )
    elif recommendation == "split_ticket_set":
        split_recommendation = (
            "Create one grouped ticket set with leaf tickets per work item so ORX can preserve "
            "project routing and review the whole packet at approval time."
        )
    else:
        split_recommendation = "Create one leaf ticket directly after approval."

    return {
        "summary": _planning_summary(
            request_text=request_text,
            recommendation=recommendation,
            leaf_count=leaf_count,
            project_count=project_count,
        ),
        "recommendation": recommendation,
        "split_recommendation": split_recommendation,
        "confidence": stage_metadata["confidence"],
        "requires_hil": needs_clarification,
        "approval_gate": "required",
        "complexity_signals": complexity_signals,
        "grouped_work_items": grouped_work_items,
    }


def _planning_summary(
    *,
    request_text: str,
    recommendation: str,
    leaf_count: int,
    project_count: int,
) -> str:
    subject = _normalize_request_subject(request_text) or request_text.strip() or "the intake request"
    if recommendation == "clarification_required":
        return (
            f"ORX cannot safely create Linear work for `{subject}` yet because the request still "
            "needs clarification."
        )
    if recommendation == "split_ticket_set":
        return (
            f"ORX plans to split `{subject}` into {leaf_count} leaf tickets across "
            f"{project_count or 1} project lanes."
        )
    return f"ORX plans to create one runnable ticket for `{subject}`."


def _build_decomposition_plan(
    *,
    request_text: str,
    items: list[IntakeItem],
    stage_contract: dict[str, Any],
    planning_result: dict[str, Any],
) -> dict[str, Any]:
    stage_metadata = flatten_stage_contract(stage_contract)
    capsules = [
        _leaf_capsule_from_item(item, stage_metadata=stage_metadata)
        for item in items
    ]
    root_capsule: dict[str, Any] | None = None
    dependency_edges: list[dict[str, str]] = []
    if len(capsules) > 1:
        root_capsule = _build_root_capsule(
            request_text=request_text,
            leaf_capsules=capsules,
            planning_result=planning_result,
            stage_metadata=stage_metadata,
        )
        for capsule in capsules:
            capsule["parent_capsule_key"] = root_capsule["capsule_key"]
            dependency_edges.append(
                {
                    "from_capsule_key": root_capsule["capsule_key"],
                    "to_capsule_key": capsule["capsule_key"],
                    "relationship": "parent_child",
                }
            )
        root_capsule["child_capsule_keys"] = [capsule["capsule_key"] for capsule in capsules]
    ordered_capsules = [root_capsule] + capsules if root_capsule is not None else capsules
    return {
        "materialization_mode": "grouped_ticket_set" if root_capsule is not None else "single_leaf",
        "root_capsule_key": None if root_capsule is None else root_capsule["capsule_key"],
        "leaf_capsule_keys": [capsule["capsule_key"] for capsule in capsules],
        "dependency_edges": dependency_edges,
        "capsules": ordered_capsules,
    }


def _leaf_capsule_from_item(
    item: IntakeItem,
    *,
    stage_metadata: dict[str, object],
) -> dict[str, Any]:
    scope = item.draft_ticket.get("scope") if isinstance(item.draft_ticket.get("scope"), dict) else {}
    return {
        "capsule_key": f"leaf-{item.item_key}",
        "ticket_role": "leaf",
        "runnable": True,
        "project_key": item.project_key,
        "project_display_name": item.project_display_name,
        "parent_capsule_key": None,
        "source_item_keys": [item.item_key],
        "source_request_texts": [item.source_text],
        "title": item.title,
        "problem": item.draft_ticket.get("why") or item.source_text,
        "goal": item.draft_ticket.get("goal") or item.title,
        "constraints": list(item.draft_ticket.get("requirements") or []),
        "dependencies": [],
        "acceptance_criteria": list(item.draft_ticket.get("acceptance_criteria") or []),
        "verification_notes": list(item.draft_ticket.get("definition_of_done") or []),
        "recommended_execution_model": stage_metadata["execution_model"],
        "recommended_execution_reasoning_effort": stage_metadata["execution_reasoning_effort"],
        "confidence": stage_metadata["confidence"],
        "draft_ticket": item.draft_ticket,
        "out_of_scope": list(scope.get("out_of_scope") or []),
        "rationale": item.rationale,
        "routing_mode": item.routing_mode,
    }


def _build_root_capsule(
    *,
    request_text: str,
    leaf_capsules: list[dict[str, Any]],
    planning_result: dict[str, Any],
    stage_metadata: dict[str, object],
) -> dict[str, Any]:
    project_keys = sorted(
        {
            str(capsule.get("project_key"))
            for capsule in leaf_capsules
            if capsule.get("project_key") is not None
        }
    )
    single_project = project_keys[0] if len(project_keys) == 1 else None
    title = _root_capsule_title(request_text=request_text, project_keys=project_keys)
    summary = planning_result.get("summary") or "Coordinate the grouped ORX intake packet."
    draft_ticket = {
        "title": title,
        "why": (
            f"This intake expands into {len(leaf_capsules)} related work items that should be reviewed "
            "together before ORX hands the leaves to execution."
        ),
        "goal": "Keep the grouped intake packet coherent while the leaf tickets execute independently.",
        "scope": {
            "in_scope": [
                "Capture the shared context for this intake packet.",
                "Point each child ticket at the correct project and execution lane.",
            ],
            "out_of_scope": [
                "Do not execute work directly in the umbrella ticket.",
                "Do not collapse distinct leaf tickets back into one broad ticket.",
            ],
        },
        "requirements": [
            "Every child ticket must stay attached to this umbrella until the packet is complete.",
            "Only leaf tickets are runnable by ORX.",
        ],
        "acceptance_criteria": [
            "Given the grouped intake packet",
            "When ORX materializes it",
            "Then every leaf ticket is attached to this umbrella and keeps its own project routing.",
        ],
        "technical_notes": [
            f"Planning summary: {summary}",
            f"Planning confidence: {stage_metadata['confidence']}",
        ],
        "dependencies_risks": [
            "If one child turns out to need additional owners, split that child further instead of widening the umbrella.",
        ],
        "definition_of_done": [
            "The umbrella exists only as coordination state for the child tickets.",
            "Each child ticket is materialized with the correct project and execution metadata.",
        ],
    }
    return {
        "capsule_key": "umbrella-root",
        "ticket_role": "umbrella",
        "runnable": False,
        "project_key": single_project,
        "project_display_name": single_project,
        "parent_capsule_key": None,
        "child_capsule_keys": [],
        "source_item_keys": [key for capsule in leaf_capsules for key in capsule["source_item_keys"]],
        "source_request_texts": [
            text
            for capsule in leaf_capsules
            for text in (capsule.get("source_request_texts") or [])
            if str(text).strip()
        ],
        "title": title,
        "problem": draft_ticket["why"],
        "goal": draft_ticket["goal"],
        "constraints": list(draft_ticket["requirements"]),
        "dependencies": [],
        "acceptance_criteria": list(draft_ticket["acceptance_criteria"]),
        "verification_notes": list(draft_ticket["definition_of_done"]),
        "recommended_execution_model": None,
        "recommended_execution_reasoning_effort": None,
        "confidence": stage_metadata["confidence"],
        "draft_ticket": draft_ticket,
        "rationale": summary,
        "routing_mode": "grouped-ticket-set",
    }


def _root_capsule_title(*, request_text: str, project_keys: list[str]) -> str:
    subject = _normalize_request_subject(request_text) or "ORX intake packet"
    if len(project_keys) == 1:
        return f"{project_keys[0]}: grouped intake packet for {subject}"
    if project_keys:
        return f"Cross-project intake packet for {subject}"
    return f"Clarify grouped intake packet for {subject}"


def _draft_ticket_lines(draft_ticket: dict[str, Any]) -> list[str]:
    scope = draft_ticket.get("scope") if isinstance(draft_ticket.get("scope"), dict) else {}
    in_scope = scope.get("in_scope") if isinstance(scope, dict) else []
    out_of_scope = scope.get("out_of_scope") if isinstance(scope, dict) else []
    lines = [
        "## Title",
        str(draft_ticket.get("title") or "New intake item"),
        "",
        "## Why",
        str(draft_ticket.get("why") or "No problem statement was provided."),
        "",
        "## Objective",
        str(draft_ticket.get("objective") or draft_ticket.get("goal") or "Define the desired end state for this work."),
        "",
        "## Goal",
        str(draft_ticket.get("goal") or "Define the desired end state for this work."),
        "",
        "## Scope",
        "### In scope",
        *_bullet_lines(in_scope),
        "",
        "### Out of scope",
        *_bullet_lines(out_of_scope),
        "",
        "## Constraints",
        *_bullet_lines(draft_ticket.get("constraints") or draft_ticket.get("requirements")),
        "",
        "## Success Criteria",
        *_given_when_then_lines(draft_ticket.get("success_criteria") or draft_ticket.get("acceptance_criteria")),
        "",
        "## Exact Output Required",
        *_bullet_lines(draft_ticket.get("exact_output_required")),
        "",
        "## Ordered Steps",
        *_ordered_lines(draft_ticket.get("ordered_steps")),
        "",
        "## Verification",
        *_bullet_lines(draft_ticket.get("verification")),
        "",
        "## Stopping Conditions",
        *_bullet_lines(draft_ticket.get("stopping_conditions")),
        "",
        "## Blocked / Escalation",
        *_ordered_lines(draft_ticket.get("blocked_escalation")),
        "",
        "## Requirements",
        *_bullet_lines(draft_ticket.get("requirements")),
        "",
        "## Acceptance Criteria",
        *_given_when_then_lines(draft_ticket.get("acceptance_criteria")),
        "",
        "## Technical Notes",
        *_bullet_lines(draft_ticket.get("technical_notes")),
        "",
        "## Dependencies / Risks",
        *_bullet_lines(draft_ticket.get("dependencies_risks")),
        "",
        "## Definition of Done",
        *_bullet_lines(draft_ticket.get("definition_of_done")),
    ]
    return lines


def _bullet_lines(values: Any) -> list[str]:
    if not isinstance(values, list):
        return ["-"]
    lines = [f"- {str(value).strip()}" for value in values if str(value).strip()]
    return lines or ["-"]


def _ordered_lines(values: Any) -> list[str]:
    if not isinstance(values, list):
        return ["1. "]
    lines = [f"{index}. {str(value).strip()}" for index, value in enumerate(values, start=1) if str(value).strip()]
    return lines or ["1. "]


def _brief_list(values: Any, *, default: str) -> str:
    if not isinstance(values, list):
        return default
    parts = [str(value).strip() for value in values if str(value).strip()]
    if not parts:
        return default
    return "; ".join(parts[:3])


def _given_when_then_lines(values: Any) -> list[str]:
    if not isinstance(values, list):
        return ["- Given ...", "- When ...", "- Then ..."]
    labels = ["Given", "When", "Then"]
    lines: list[str] = []
    for index, value in enumerate(values):
        text = str(value).strip()
        if not text:
            continue
        label = labels[index] if index < len(labels) else "And"
        prefix = f"{label} "
        if text.startswith(prefix):
            lines.append(f"- {text}")
        else:
            lines.append(f"- {prefix}{text}")
    return lines or ["- Given ...", "- When ...", "- Then ..."]


def _find_section(lines: list[str], heading: str) -> int | None:
    for index, line in enumerate(lines):
        if line == heading:
            return index
    return None


def _split_request_items(text: str) -> list[str]:
    items = [
        match.group("value").strip()
        for line in text.splitlines()
        if (match := _BULLET_PATTERN.match(line))
    ]
    if items:
        return items
    paragraphs = [chunk.strip() for chunk in re.split(r"\n\s*\n+", text.strip()) if chunk.strip()]
    if len(paragraphs) > 1:
        return paragraphs
    return [text.strip()]


def _split_labeled_request_items(
    text: str,
    *,
    aliases: dict[str, set[str]],
) -> list[str]:
    candidates = sorted(
        {alias.strip() for values in aliases.values() for alias in values if alias.strip()},
        key=len,
        reverse=True,
    )
    if not candidates:
        return []
    pattern = re.compile(
        rf"(?im)(?:^|[\n;])\s*((?:{'|'.join(re.escape(candidate) for candidate in candidates)}))\s*:"
    )
    matches = list(pattern.finditer(text))
    if len(matches) < 2:
        return []
    items: list[str] = []
    for index, match in enumerate(matches):
        start = match.start(1)
        end = matches[index + 1].start(1) if index + 1 < len(matches) else len(text)
        chunk = text[start:end].strip(" \n;")
        if chunk:
            items.append(chunk)
    return items


def _project_aliases(project: ProjectRegistration) -> set[str]:
    aliases = {
        project.project_key.lower(),
        project.display_name.lower(),
    }
    repo_name = Path(project.repo_root).name.strip().lower()
    if repo_name:
        aliases.add(repo_name)
    return {alias for alias in aliases if alias}


def _leading_project_match(
    text: str,
    *,
    aliases: dict[str, set[str]],
) -> str | None:
    normalized = str(text or "")
    candidates = sorted(
        ((project_key, alias) for project_key, values in aliases.items() for alias in values if alias.strip()),
        key=lambda entry: len(entry[1]),
        reverse=True,
    )
    for project_key, alias in candidates:
        if re.match(rf"^\s*(?:[-*]\s*)?{re.escape(alias)}\s*:", normalized, flags=re.IGNORECASE):
            return project_key
    return None


def _match_projects(
    text: str,
    *,
    projects: list[ProjectRegistration],
    aliases: dict[str, set[str]],
) -> set[str]:
    normalized = text.lower()
    matches: set[str] = set()
    for project in projects:
        for alias in aliases[project.project_key]:
            if re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", normalized):
                matches.add(project.project_key)
                break
    return matches


def _issue_project_key(issue: MirroredIssueRecord) -> str:
    metadata_key = issue.metadata.get("project_key")
    if isinstance(metadata_key, str) and metadata_key.strip():
        return metadata_key.strip().lower()
    if issue.project_name:
        return issue.project_name.strip().lower().replace(" ", "-")
    if issue.project_id:
        return issue.project_id.strip().lower()
    return issue.team_name.strip().lower().replace(" ", "-")


def _project_execution_thread_id(project: ProjectRegistration) -> int | None:
    raw = project.metadata.get("execution_thread_id") if isinstance(project.metadata, dict) else None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        try:
            return int(raw)
        except ValueError:
            return project.owner_thread_id
    return project.owner_thread_id


def _default_issue_worktree(*, project_key: str, identifier: str) -> Path:
    dev_root = Path(os.environ.get("DEV", str(Path.home() / "Dev"))).expanduser().resolve()
    return dev_root / "worktrees" / project_key / identifier.lower()


def _default_packet_worktree(*, project_key: str, packet_key: str) -> Path:
    dev_root = Path(os.environ.get("DEV", str(Path.home() / "Dev"))).expanduser().resolve()
    return dev_root / "worktrees" / project_key / packet_key.lower()


def _issue_branch_name(identifier: str) -> str:
    slug = re.sub(r"[^a-z0-9._/-]+", "-", identifier.strip().lower()).strip("-")
    return f"linear/{slug or 'issue'}"


def _packet_branch_name(*, packet_key: str) -> str:
    slug = re.sub(r"[^a-z0-9._/-]+", "-", packet_key.strip().lower()).strip("-")
    return f"linear/{slug or 'packet'}"


def _packet_execution_context(
    *,
    capsule: dict[str, Any],
    target: ProjectRegistration | None,
    issue: LinearIssue,
    parent_issue: LinearIssue | None,
) -> dict[str, str | None]:
    identifier = issue.identifier or issue.linear_id
    if capsule.get("ticket_role") != "leaf" or target is None:
        return {
            "branch": _issue_branch_name(identifier),
            "merge_into": "main",
            "merge_strategy": "hil_merge_to_main",
            "packet_key": identifier,
            "scope": "single_leaf",
            "worktree_path": None,
        }

    if parent_issue is None:
        return {
            "branch": _issue_branch_name(identifier),
            "merge_into": "main",
            "merge_strategy": "hil_merge_to_main",
            "packet_key": identifier,
            "scope": "single_leaf",
            "worktree_path": str(_default_issue_worktree(project_key=target.project_key, identifier=identifier)),
        }

    parent_identifier = parent_issue.identifier or parent_issue.linear_id
    packet_key = f"{parent_identifier}-{target.project_key}"
    return {
        "branch": _packet_branch_name(packet_key=packet_key),
        "merge_into": "main",
        "merge_strategy": "hil_merge_to_main",
        "packet_key": packet_key,
        "scope": "shared_packet",
        "worktree_path": str(_default_packet_worktree(project_key=target.project_key, packet_key=packet_key)),
    }


def _compact_codex_execution_brief(
    *,
    capsule: dict[str, Any],
    packet_context: dict[str, str | None],
    repo_root: str | None,
) -> dict[str, Any]:
    draft_ticket = dict(capsule.get("draft_ticket") or {})
    scope = draft_ticket.get("scope") if isinstance(draft_ticket.get("scope"), dict) else {}
    scope_in = [str(item).strip() for item in (scope.get("in_scope") or []) if str(item).strip()][:2]
    scope_out = [str(item).strip() for item in (scope.get("out_of_scope") or []) if str(item).strip()][:2]
    success_criteria = [
        str(item).strip()
        for item in (draft_ticket.get("success_criteria") or draft_ticket.get("acceptance_criteria") or [])
        if str(item).strip()
    ][:3]
    definition_of_done = [
        str(item).strip()
        for item in (draft_ticket.get("definition_of_done") or [])
        if str(item).strip()
    ][:1]
    success_criteria.extend(f"Definition of done: {item}" for item in definition_of_done)
    constraints = [
        str(item).strip()
        for item in (draft_ticket.get("constraints") or draft_ticket.get("requirements") or [])
        if str(item).strip()
    ][:2]
    ordered_steps = [
        str(item).strip()
        for item in (draft_ticket.get("ordered_steps") or [])
        if str(item).strip()
    ][:4]
    verification = [
        str(item).strip()
        for item in (draft_ticket.get("verification") or [])
        if str(item).strip()
    ][:3]
    stopping_conditions = [
        str(item).strip()
        for item in (draft_ticket.get("stopping_conditions") or [])
        if str(item).strip()
    ][:3]
    blocked_escalation = [
        str(item).strip()
        for item in (draft_ticket.get("blocked_escalation") or [])
        if str(item).strip()
    ][:3]
    if repo_root:
        constraints.append(f"Repo root: {repo_root}")
    if packet_context.get("worktree_path"):
        constraints.append(f"Worktree: {packet_context['worktree_path']}")
    if packet_context.get("branch"):
        constraints.append(f"Branch: {packet_context['branch']}")
    return {
        "objective_title": draft_ticket.get("title") or capsule.get("title"),
        "problem": draft_ticket.get("why"),
        "goal": draft_ticket.get("goal") or capsule.get("title"),
        "scope_in": scope_in,
        "scope_out": scope_out,
        "success_criteria": success_criteria,
        "constraints": constraints,
        "ordered_steps": ordered_steps,
        "verification": verification,
        "stopping_conditions": stopping_conditions,
        "blocked_escalation": blocked_escalation,
    }


def _merge_issue_metadata(description: str, metadata: dict[str, Any]) -> str:
    block = f"{_METADATA_START}\n{json.dumps(metadata, indent=2, sort_keys=True)}\n{_METADATA_END}"
    clean = str(description or "").strip()
    pattern = re.escape(_METADATA_START) + r"\s*(.*?)\s*" + re.escape(_METADATA_END)
    if re.search(pattern, clean, flags=re.DOTALL):
        clean = re.sub(pattern, block, clean, flags=re.DOTALL)
        return clean.rstrip() + "\n"
    if clean:
        return clean.rstrip() + "\n\n" + block + "\n"
    return block + "\n"


def _row_to_intake(row: Any) -> IntakeRecord:
    if row is None:
        raise ValueError("Cannot convert empty intake row.")
    plan = json.loads(row["plan_json"])
    stage_metadata = _stage_metadata_from_row(row, plan=plan)
    return IntakeRecord(
        intake_id=int(row["intake_id"]),
        intake_key=str(row["intake_key"]),
        ingress_bot=str(row["ingress_bot"]),
        ingress_chat_id=int(row["ingress_chat_id"]) if row["ingress_chat_id"] is not None else None,
        ingress_thread_id=int(row["ingress_thread_id"]) if row["ingress_thread_id"] is not None else None,
        explicit_project_key=_normalize_optional(row["explicit_project_key"]),
        default_project_key=_normalize_optional(row["default_project_key"]),
        request_text=str(row["request_text"]),
        status=str(row["status"]),
        plan=plan,
        planning_stage=str(stage_metadata["planning_stage"]),
        planning_model=str(stage_metadata["planning_model"]),
        planning_reasoning_effort=str(stage_metadata["planning_reasoning_effort"]),
        decomposition_model=str(stage_metadata["decomposition_model"]),
        decomposition_reasoning_effort=str(stage_metadata["decomposition_reasoning_effort"]),
        execution_model=str(stage_metadata["execution_model"]),
        execution_reasoning_effort=str(stage_metadata["execution_reasoning_effort"]),
        confidence=str(stage_metadata["confidence"]),
        requires_hil=bool(stage_metadata["requires_hil"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _stage_metadata_from_plan(plan: dict[str, Any]) -> dict[str, object]:
    return flatten_stage_contract(plan.get("stage_contract") if isinstance(plan, dict) else None)


def _stage_metadata_from_row(row: Any, *, plan: dict[str, Any]) -> dict[str, object]:
    stage_metadata = _stage_metadata_from_plan(plan)
    row_keys = set(row.keys()) if hasattr(row, "keys") else set()
    if "planning_model" not in row_keys:
        return stage_metadata
    return {
        "planning_stage": _normalize_optional(row["planning_stage"]) or stage_metadata["planning_stage"],
        "planning_model": _normalize_optional(row["planning_model"]) or stage_metadata["planning_model"],
        "planning_reasoning_effort": _normalize_optional(row["planning_reasoning_effort"])
        or stage_metadata["planning_reasoning_effort"],
        "decomposition_model": _normalize_optional(row["decomposition_model"])
        or stage_metadata["decomposition_model"],
        "decomposition_reasoning_effort": _normalize_optional(row["decomposition_reasoning_effort"])
        or stage_metadata["decomposition_reasoning_effort"],
        "execution_model": _normalize_optional(row["execution_model"]) or stage_metadata["execution_model"],
        "execution_reasoning_effort": _normalize_optional(row["execution_reasoning_effort"])
        or stage_metadata["execution_reasoning_effort"],
        "confidence": _normalize_optional(row["confidence"]) or stage_metadata["confidence"],
        "requires_hil": bool(row["requires_hil"])
        if row["requires_hil"] is not None
        else bool(stage_metadata["requires_hil"]),
    }


def _plan_items(plan: dict[str, Any]) -> list[IntakeItem]:
    raw_items = plan.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("Intake plan is missing items.")
    items: list[IntakeItem] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise ValueError("Intake plan item is not an object.")
        items.append(
            IntakeItem(
                item_key=str(raw["item_key"]),
                source_text=str(raw["source_text"]),
                title=str(raw["title"]),
                description=str(raw["description"]),
                draft_ticket=dict(raw.get("draft_ticket") or {}),
                project_key=_normalize_optional(raw.get("project_key")),
                project_display_name=_normalize_optional(raw.get("project_display_name")),
                rationale=str(raw["rationale"]),
                routing_mode=str(raw["routing_mode"]),
                needs_clarification=bool(raw["needs_clarification"]),
            )
        )
    return items
