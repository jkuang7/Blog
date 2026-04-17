"""Deterministic stage/tier contract for ORX intake planning and execution."""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_MODEL = "gpt-5.4"

PERSISTENCE_FIELDS = (
    "planning_stage",
    "planning_model",
    "planning_reasoning_effort",
    "decomposition_model",
    "decomposition_reasoning_effort",
    "execution_model",
    "execution_reasoning_effort",
    "confidence",
    "requires_hil",
)


@dataclass(frozen=True)
class StageTierContract:
    stage: str
    responsibility: str
    default_model: str
    default_reasoning_effort: str
    selected_model: str
    selected_reasoning_effort: str
    selection_mode: str
    selection_reason: str

    def to_dict(self) -> dict[str, str]:
        return {
            "stage": self.stage,
            "responsibility": self.responsibility,
            "default_model": self.default_model,
            "default_reasoning_effort": self.default_reasoning_effort,
            "selected_model": self.selected_model,
            "selected_reasoning_effort": self.selected_reasoning_effort,
            "selection_mode": self.selection_mode,
            "selection_reason": self.selection_reason,
        }


def build_stage_contract(
    *,
    item_count: int,
    project_count: int,
    needs_clarification: bool,
    oversized: bool = False,
) -> dict[str, object]:
    planning_contract = _planning_contract(
        item_count=item_count,
        project_count=project_count,
        needs_clarification=needs_clarification,
        oversized=oversized,
    )
    decomposition_contract = StageTierContract(
        stage="decomposition",
        responsibility=(
            "Turn an approved intake plan into implementation-ready Linear ticket capsules, "
            "including boundaries, acceptance criteria, and execution recommendations."
        ),
        default_model=DEFAULT_MODEL,
        default_reasoning_effort="high",
        selected_model=DEFAULT_MODEL,
        selected_reasoning_effort="high",
        selection_mode="default",
        selection_reason=(
            "Decomposition stays at `high` so ORX can refine scope and dependencies without "
            "paying xhigh cost on every ticket."
        ),
    )
    execution_contract = StageTierContract(
        stage="execution",
        responsibility=(
            "Execute a decision-complete Linear leaf ticket in tmux-codex with the issue "
            "context already selected by ORX."
        ),
        default_model=DEFAULT_MODEL,
        default_reasoning_effort="medium",
        selected_model=DEFAULT_MODEL,
        selected_reasoning_effort="medium",
        selection_mode="default",
        selection_reason=(
            "Execution-ready leaves run at `medium`; ORX escalates only when the work stops "
            "being a narrow execution problem."
        ),
    )
    return {
        "stage_order": ["planning", "decomposition", "execution"],
        "stages": [
            planning_contract.to_dict(),
            decomposition_contract.to_dict(),
            execution_contract.to_dict(),
        ],
        "persistence_fields": list(PERSISTENCE_FIELDS),
        "confidence": _contract_confidence(planning_contract.selection_mode),
        "requires_hil": needs_clarification,
    }


def flatten_stage_contract(stage_contract: dict[str, object] | None) -> dict[str, object]:
    stages = {}
    if isinstance(stage_contract, dict):
        raw_stages = stage_contract.get("stages")
        if isinstance(raw_stages, list):
            stages = {
                str(stage.get("stage")): stage
                for stage in raw_stages
                if isinstance(stage, dict) and stage.get("stage")
            }
    planning = stages.get("planning", {})
    decomposition = stages.get("decomposition", {})
    execution = stages.get("execution", {})
    confidence = stage_contract.get("confidence") if isinstance(stage_contract, dict) else None
    if not isinstance(confidence, str) or not confidence.strip():
        confidence = _contract_confidence(str(planning.get("selection_mode") or "default"))
    requires_hil = bool(stage_contract.get("requires_hil")) if isinstance(stage_contract, dict) else False
    return {
        "planning_stage": "planning",
        "planning_model": str(planning.get("selected_model") or DEFAULT_MODEL),
        "planning_reasoning_effort": str(planning.get("selected_reasoning_effort") or "xhigh"),
        "decomposition_model": str(decomposition.get("selected_model") or DEFAULT_MODEL),
        "decomposition_reasoning_effort": str(
            decomposition.get("selected_reasoning_effort") or "high"
        ),
        "execution_model": str(execution.get("selected_model") or DEFAULT_MODEL),
        "execution_reasoning_effort": str(execution.get("selected_reasoning_effort") or "medium"),
        "confidence": confidence.strip(),
        "requires_hil": requires_hil,
    }


def _contract_confidence(selection_mode: str) -> str:
    if selection_mode == "simple_intake_downgrade":
        return "high"
    if selection_mode == "clarification_required":
        return "low"
    return "medium"


def _planning_contract(
    *,
    item_count: int,
    project_count: int,
    needs_clarification: bool,
    oversized: bool,
) -> StageTierContract:
    default_model = DEFAULT_MODEL
    default_reasoning = "xhigh"
    if needs_clarification:
        return StageTierContract(
            stage="planning",
            responsibility=(
                "Interpret a raw `/add` request, decide project routing, decide whether the "
                "request must split into multiple tickets, and determine whether human review "
                "is required before ORX mutates Linear."
            ),
            default_model=default_model,
            default_reasoning_effort=default_reasoning,
            selected_model=default_model,
            selected_reasoning_effort=default_reasoning,
            selection_mode="clarification_required",
            selection_reason=(
                "Planning stays at `xhigh` whenever the intake is ambiguous or spans multiple "
                "possible owners."
            ),
        )
    if oversized:
        return StageTierContract(
            stage="planning",
            responsibility=(
                "Interpret a raw `/add` request, decide project routing, decide whether the "
                "request must split into multiple tickets, and determine whether human review "
                "is required before ORX mutates Linear."
            ),
            default_model=default_model,
            default_reasoning_effort=default_reasoning,
            selected_model=default_model,
            selected_reasoning_effort=default_reasoning,
            selection_mode="oversized_intake",
            selection_reason=(
                "Planning stays at `xhigh` when the intake is large enough that ORX should "
                "reconstruct the whole request before decomposition."
            ),
        )
    if item_count == 1 and project_count <= 1:
        return StageTierContract(
            stage="planning",
            responsibility=(
                "Interpret a raw `/add` request, decide project routing, decide whether the "
                "request must split into multiple tickets, and determine whether human review "
                "is required before ORX mutates Linear."
            ),
            default_model=default_model,
            default_reasoning_effort=default_reasoning,
            selected_model=default_model,
            selected_reasoning_effort="high",
            selection_mode="simple_intake_downgrade",
            selection_reason=(
                "A single-ticket intake routed to one project can skip `xhigh` and plan at "
                "`high`."
            ),
        )
    return StageTierContract(
        stage="planning",
        responsibility=(
            "Interpret a raw `/add` request, decide project routing, decide whether the "
            "request must split into multiple tickets, and determine whether human review "
            "is required before ORX mutates Linear."
        ),
        default_model=default_model,
        default_reasoning_effort=default_reasoning,
        selected_model=default_model,
        selected_reasoning_effort=default_reasoning,
        selection_mode="default",
        selection_reason=(
            "Planning stays at `xhigh` for multi-ticket or multi-project intake so ORX can "
            "reconstruct the whole work packet before decomposition."
        ),
    )
