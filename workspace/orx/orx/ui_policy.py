"""Deterministic UI routing and closeout gates for ORX-managed work."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .mirror import MirroredIssueRecord
from .metadata import METADATA_END, METADATA_START

UI_MODE_VALUES = {"none", "logic", "visual"}
DESIGN_STATE_VALUES = {"none", "pending", "approved"}
CONTRACT_STATE_VALUES = {"none", "pending", "approved"}
VERIFICATION_SURFACE_VALUES = {"none", "cli", "playwright", "mixed"}
REVIEW_KIND_VALUES = {"standard", "design_review_required", "contract_review_required", "ui_evidence_missing"}

_TEXT_ONLY_KEYWORDS = (
    "copy",
    "copy only",
    "copy-only",
    "content only",
    "content-only",
    "text only",
    "text-only",
    "wording",
    "rename",
    "reword",
    "label",
    "placeholder text",
    "cta text",
)
_VISUAL_KEYWORDS = (
    "visual",
    "design",
    "redesign",
    "restructure",
    "layout",
    "styling",
    "style refresh",
    "spacing",
    "hierarchy",
    "typography",
    "color",
    "responsive",
    "screen",
    "landing page",
    "page shell",
    "premium",
)
_VISUAL_METADATA_HINTS = {
    "ui_visual",
    "design",
    "redesign",
    "new_screen",
    "visual_refresh",
}
_LOGIC_KEYWORDS = (
    "ui bug",
    "frontend bug",
    "behavior",
    "state",
    "routing",
    "submit",
    "form validation",
    "input validation",
    "client validation",
    "keyboard",
    "focus",
    "selection",
    "navigation",
    "accessibility",
    "a11y",
    "form",
    "dropdown",
    "modal",
    "tooltip",
    "table",
)
_LOGIC_METADATA_HINTS = {
    "ui_logic",
    "ui",
    "frontend",
    "a11y",
    "form",
}
_ORX_LATEST_HANDOFF_RE = re.compile(r"(?ms)^## Latest Handoff\s*\n.*?(?=^## |\Z)")
_ORX_RAW_SLICE_FACTS_RE = re.compile(r"(?ms)^## Raw Slice Facts\s*\n.*?(?=^## |\Z)")
_ORX_METADATA_RE = re.compile(
    re.escape(METADATA_START) + r"\s*.*?\s*" + re.escape(METADATA_END),
    re.DOTALL,
)


@dataclass(frozen=True)
class UiRoutingDecision:
    ui_mode: str
    ui_reason: str
    design_state: str
    contract_state: str
    ui_evidence_required: bool
    design_reference: str | None
    contract_reference: str | None


@dataclass(frozen=True)
class UiGateDecision:
    gate_required: bool
    review_kind: str
    reason: str
    design_state: str
    contract_state: str
    design_reference: str | None
    design_artifacts: tuple[str, ...]
    contract_reference: str | None
    contract_artifacts: tuple[str, ...]
    verification_surface: str
    design_review_requested: bool
    contract_review_requested: bool


def classify_ui_routing(
    *,
    issue: MirroredIssueRecord,
    resume_context: dict[str, Any] | None = None,
) -> UiRoutingDecision:
    metadata = issue.metadata if isinstance(issue.metadata, dict) else {}
    resume = resume_context if isinstance(resume_context, dict) else {}
    explicit_mode = _normalize_ui_mode(metadata.get("ui_mode")) or _normalize_ui_mode(resume.get("ui_mode"))
    design_reference = _clean_line(resume.get("design_reference")) or _clean_line(metadata.get("design_reference"))
    contract_reference = _clean_line(resume.get("contract_reference")) or _clean_line(metadata.get("contract_reference"))
    design_state = _normalize_design_state(resume.get("design_state")) or _normalize_design_state(metadata.get("design_state"))
    contract_state = _normalize_contract_state(resume.get("contract_state")) or _normalize_contract_state(metadata.get("contract_state"))
    if explicit_mode is not None:
        ui_mode = explicit_mode
        ui_reason = f"Using explicit issue metadata ui_mode `{ui_mode}`."
    else:
        ui_mode, ui_reason = _classify_from_issue_text(issue=issue, metadata=metadata)

    if ui_mode == "visual":
        if design_state is None:
            design_state = "pending"
        if design_state == "approved":
            if contract_state is None:
                contract_state = "pending"
        else:
            contract_state = "none"
            contract_reference = None
    else:
        design_state = "none"
        design_reference = None
        contract_state = "none"
        contract_reference = None

    ui_evidence_required = ui_mode == "logic" or (
        ui_mode == "visual" and design_state == "approved" and contract_state == "approved"
    )
    return UiRoutingDecision(
        ui_mode=ui_mode,
        ui_reason=ui_reason,
        design_state=design_state,
        contract_state=contract_state,
        ui_evidence_required=ui_evidence_required,
        design_reference=design_reference,
        contract_reference=contract_reference,
    )


def evaluate_ui_gate(
    *,
    routing: UiRoutingDecision,
    payload: dict[str, Any],
    interpreted_action: str,
) -> UiGateDecision:
    design_artifacts = tuple(_clean_list(payload.get("design_artifacts")))
    contract_artifacts = tuple(_clean_list(payload.get("contract_artifacts")))
    artifacts = tuple(_clean_list(payload.get("artifacts")))
    design_reference = routing.design_reference or next(iter(design_artifacts), None) or next(iter(artifacts), None)
    contract_reference = (
        routing.contract_reference
        or _clean_line(payload.get("contract_reference"))
        or next(iter(contract_artifacts), None)
        or next(iter(artifacts), None)
    )
    design_review_requested = bool(payload.get("design_review_requested"))
    contract_review_requested = bool(payload.get("contract_review_requested"))
    verification_surface = _normalize_verification_surface(payload.get("verification_surface"))
    if verification_surface is None:
        verification_surface = _infer_verification_surface(
            verification_ran=_clean_list(payload.get("verification_ran")),
        )

    if routing.ui_mode == "visual" and routing.design_state == "pending" and interpreted_action not in {
        "blocked",
        "reroute",
        "replan",
        "needs_human_help",
    }:
        if design_review_requested and design_artifacts:
            reason = "Visual work produced design artifacts and now requires design review before implementation."
        elif design_artifacts:
            reason = "Visual work cannot continue until ORX reviews the generated design artifacts."
        else:
            reason = "Visual work requires a design-prep slice with Stitch artifacts before implementation can continue."
        return UiGateDecision(
            gate_required=True,
            review_kind="design_review_required",
            reason=reason,
            design_state="pending",
            contract_state=routing.contract_state,
            design_reference=design_reference,
            design_artifacts=design_artifacts or artifacts,
            contract_reference=contract_reference,
            contract_artifacts=contract_artifacts,
            verification_surface=verification_surface,
            design_review_requested=design_review_requested,
            contract_review_requested=contract_review_requested,
        )

    if routing.ui_mode == "visual" and routing.design_state == "approved" and routing.contract_state == "pending" and interpreted_action not in {
        "blocked",
        "reroute",
        "replan",
        "needs_human_help",
    }:
        if contract_review_requested and contract_artifacts:
            reason = "Visual work produced a /ui-contracts candidate and now requires contract review before owner or app promotion."
        elif contract_artifacts:
            reason = "Visual work cannot continue until ORX reviews the /ui-contracts contract candidate."
        else:
            reason = "Visual work requires a /ui-contracts contract-prep slice before implementation can continue."
        return UiGateDecision(
            gate_required=True,
            review_kind="contract_review_required",
            reason=reason,
            design_state=routing.design_state,
            contract_state="pending",
            design_reference=design_reference,
            design_artifacts=design_artifacts,
            contract_reference=contract_reference,
            contract_artifacts=contract_artifacts or artifacts,
            verification_surface=verification_surface,
            design_review_requested=design_review_requested,
            contract_review_requested=contract_review_requested,
        )

    if routing.ui_evidence_required and interpreted_action == "complete" and verification_surface not in {
        "playwright",
        "mixed",
    }:
        return UiGateDecision(
            gate_required=True,
            review_kind="ui_evidence_missing",
            reason="UI closeout requires Playwright evidence before ORX can mark the issue complete.",
            design_state=routing.design_state,
            contract_state=routing.contract_state,
            design_reference=design_reference,
            design_artifacts=design_artifacts,
            contract_reference=contract_reference,
            contract_artifacts=contract_artifacts,
            verification_surface=verification_surface,
            design_review_requested=design_review_requested,
            contract_review_requested=contract_review_requested,
        )

    return UiGateDecision(
        gate_required=False,
        review_kind="standard",
        reason="No UI-specific review gate is active for this slice.",
        design_state=routing.design_state,
        contract_state=routing.contract_state,
        design_reference=design_reference,
        design_artifacts=design_artifacts,
        contract_reference=contract_reference,
        contract_artifacts=contract_artifacts,
        verification_surface=verification_surface,
        design_review_requested=design_review_requested,
        contract_review_requested=contract_review_requested,
    )


def _classify_from_issue_text(
    *,
    issue: MirroredIssueRecord,
    metadata: dict[str, Any],
) -> tuple[str, str]:
    metadata_hints = {
        _normalize_simple(metadata.get("issue_class")),
        _normalize_simple(metadata.get("type")),
        _normalize_simple(metadata.get("routing")),
    }
    labels = {_normalize_simple(label) for label in issue.labels}
    description = _description_without_orx_sections(issue.description)
    text = " ".join(part for part in (issue.title, description) if part).lower()

    if any(keyword in text for keyword in _TEXT_ONLY_KEYWORDS):
        return "none", "Issue text describes a content-only UI change, so ORX keeps it off the UI routing path."
    if metadata_hints & _VISUAL_METADATA_HINTS or labels & {"design", "ux", "ui-visual", "redesign"}:
        return "visual", "Issue metadata marks this as visual UI work."
    if any(keyword in text for keyword in _VISUAL_KEYWORDS):
        return "visual", "Issue text indicates layout or visual design work, so ORX requires a design-first path."
    if metadata_hints & _LOGIC_METADATA_HINTS or labels & {"frontend", "ui", "a11y"}:
        return "logic", "Issue metadata indicates UI logic work that still needs live UI verification."
    if any(keyword in text for keyword in _LOGIC_KEYWORDS):
        return "logic", "Issue text indicates UI behavior work, so ORX requires Playwright evidence before closeout."
    return "none", "No UI-specific routing hints were detected."


def _description_without_orx_sections(description: str) -> str:
    text = str(description or "")
    if not text:
        return ""
    text = _ORX_METADATA_RE.sub("", text)
    text = _ORX_RAW_SLICE_FACTS_RE.sub("", text)
    text = _ORX_LATEST_HANDOFF_RE.sub("", text)
    return text.strip()


def _infer_verification_surface(*, verification_ran: list[str]) -> str:
    if not verification_ran:
        return "none"
    has_playwright = any("playwright" in item.lower() for item in verification_ran)
    if has_playwright and len(verification_ran) > 1:
        return "mixed"
    if has_playwright:
        return "playwright"
    return "cli"


def _normalize_ui_mode(value: Any) -> str | None:
    text = _normalize_simple(value)
    if text in UI_MODE_VALUES:
        return text
    return None


def _normalize_design_state(value: Any) -> str | None:
    text = _normalize_simple(value)
    if text in DESIGN_STATE_VALUES:
        return text
    return None


def _normalize_contract_state(value: Any) -> str | None:
    text = _normalize_simple(value)
    if text in CONTRACT_STATE_VALUES:
        return text
    return None


def _normalize_verification_surface(value: Any) -> str | None:
    text = _normalize_simple(value)
    if text in VERIFICATION_SURFACE_VALUES:
        return text
    return None


def _normalize_simple(value: Any) -> str | None:
    cleaned = _clean_line(value)
    return None if cleaned is None else cleaned.lower()


def _clean_line(value: Any) -> str | None:
    text = " ".join(str(value or "").strip().split())
    return text or None


def _clean_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    cleaned: list[str] = []
    for item in value:
        text = " ".join(str(item or "").strip().split())
        if text:
            cleaned.append(text)
    return cleaned
