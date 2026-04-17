from __future__ import annotations

import unittest

from orx.mirror import MirroredIssueRecord
from orx.ui_policy import classify_ui_routing, evaluate_ui_gate


def _issue(*, title: str, description: str = "", metadata: dict | None = None, labels: tuple[str, ...] = ()) -> MirroredIssueRecord:
    return MirroredIssueRecord(
        linear_id="lin-1",
        identifier="PRO-1",
        title=title,
        description=description,
        team_id="team-1",
        team_name="Projects",
        state_id=None,
        state_name="Todo",
        state_type="unstarted",
        priority=1,
        project_id="project-1",
        project_name="Alpha",
        parent_linear_id=None,
        parent_identifier=None,
        assignee_id=None,
        assignee_name=None,
        labels=labels,
        metadata=metadata or {},
        source_updated_at="2026-04-17T12:00:00+00:00",
        created_at="2026-04-17T12:00:00+00:00",
        completed_at=None,
        canceled_at=None,
        last_synced_at="2026-04-17T12:00:00+00:00",
    )


class UiPolicyTests(unittest.TestCase):
    def test_classify_text_only_ui_request_as_none(self) -> None:
        routing = classify_ui_routing(
            issue=_issue(title="Update CTA copy only on the pricing page"),
            resume_context={},
        )

        self.assertEqual(routing.ui_mode, "none")
        self.assertEqual(routing.design_state, "none")
        self.assertFalse(routing.ui_evidence_required)

    def test_classify_visual_request_as_pending_design(self) -> None:
        routing = classify_ui_routing(
            issue=_issue(title="Redesign the dashboard layout and visual hierarchy"),
            resume_context={},
        )

        self.assertEqual(routing.ui_mode, "visual")
        self.assertEqual(routing.design_state, "pending")
        self.assertFalse(routing.ui_evidence_required)

    def test_evaluate_logic_closeout_requires_playwright(self) -> None:
        gate = evaluate_ui_gate(
            routing=classify_ui_routing(
                issue=_issue(title="Fix modal submit validation bug"),
                resume_context={},
            ),
            payload={
                "verification_surface": "cli",
                "verification_ran": ["pnpm test"],
            },
            interpreted_action="complete",
        )

        self.assertTrue(gate.gate_required)
        self.assertEqual(gate.review_kind, "ui_evidence_missing")

    def test_validation_project_name_does_not_trigger_ui_logic_mode(self) -> None:
        routing = classify_ui_routing(
            issue=_issue(
                title="Disposable wrong-bot ingress proof on validation-os",
                description="Confirm the control-plane lane stayed on the assigned bot.",
            ),
            resume_context={},
        )

        self.assertEqual(routing.ui_mode, "none")
        self.assertEqual(routing.design_state, "none")
        self.assertFalse(routing.ui_evidence_required)

    def test_orx_handoff_appendices_do_not_trigger_ui_logic_mode(self) -> None:
        routing = classify_ui_routing(
            issue=_issue(
                title="Disposable wrong-bot ingress proof 2026-04-17 19:44",
                description=(
                    "Disposable live proof for wrong-bot ingress after project-affinity fix.\n\n"
                    "## Raw Slice Facts\n"
                    "- Touched paths:\n"
                    "  - .codex/proofs/\n\n"
                    "## Latest Handoff\n"
                    "- Summary: missing RUNNER_RESULT block\n\n"
                    "<!-- orx:metadata:start -->\n"
                    "{\"selection_lane\":\"orx_linear\",\"worktree_path\":\"/tmp/pro-136\"}\n"
                    "<!-- orx:metadata:end -->\n"
                ),
            ),
            resume_context={},
        )

        self.assertEqual(routing.ui_mode, "none")
        self.assertEqual(routing.design_state, "none")
        self.assertFalse(routing.ui_evidence_required)


if __name__ == "__main__":
    unittest.main()
