from __future__ import annotations

import unittest

from orx.codex_interpreter import CodexInterpretation
from orx.handoff_interpreter import interpret_slice_handoff
from orx.mirror import MirroredIssueRecord


class _FakeCodexInterpreter:
    def __init__(self, interpretation: CodexInterpretation) -> None:
        self.interpretation = interpretation

    def interpret(self, *, context):  # noqa: ANN001
        return self.interpretation


class HandoffInterpreterTests(unittest.TestCase):
    def test_codex_advice_can_override_next_action_and_follow_up_shape(self) -> None:
        issue = MirroredIssueRecord(
            linear_id="issue-1",
            identifier="PRO-1",
            title="Interpret handoff",
            description="## Goal\nInterpret handoff\n",
            team_id="team-1",
            team_name="Projects",
            state_id="state-1",
            state_name="Todo",
            state_type="unstarted",
            priority=0,
            project_id="project-1",
            project_name="Alpha",
            parent_linear_id=None,
            parent_identifier=None,
            assignee_id=None,
            assignee_name=None,
            labels=(),
            metadata={},
            source_updated_at="2026-04-17T00:00:00+00:00",
            last_synced_at="2026-04-17T00:00:00+00:00",
            created_at=None,
            completed_at=None,
            canceled_at=None,
        )
        interpreted = interpret_slice_handoff(
            issue=issue,
            payload={
                "status": "blocked",
                "summary": "Blocked on prerequisite",
                "verified": False,
                "next_slice": "keep retrying",
                "artifacts": ["proof.txt"],
                "metrics": {},
                "blockers": ["Missing prerequisite"],
            },
            continuity=None,
            codex_interpreter=_FakeCodexInterpreter(
                CodexInterpretation(
                    action="reroute",
                    next_slice=None,
                    next_step_hint="Create the prerequisite ticket before redispatch.",
                    follow_ups=(
                        {
                            "title": "Create prerequisite ticket",
                            "why": "Prerequisite missing",
                            "goal": "Land prerequisite first",
                        },
                    ),
                    reasoning="Owner mismatch inferred from blocker context.",
                )
            ),
        )

        self.assertEqual(interpreted.action, "reroute")
        self.assertIsNone(interpreted.next_slice)
        self.assertEqual(
            interpreted.payload["next_step_hint"],
            "Create the prerequisite ticket before redispatch.",
        )
        self.assertEqual(interpreted.payload["codex_reasoning"], "Owner mismatch inferred from blocker context.")
        self.assertEqual(interpreted.follow_ups[0]["title"], "Create prerequisite ticket")

    def test_owner_mismatch_escalates_execution_tier_and_tags_follow_up(self) -> None:
        issue = MirroredIssueRecord(
            linear_id="issue-2",
            identifier="PRO-2",
            title="Route owner work",
            description="## Goal\nRoute owner work\n",
            team_id="team-1",
            team_name="Projects",
            state_id="state-1",
            state_name="Todo",
            state_type="unstarted",
            priority=0,
            project_id="project-1",
            project_name="Alpha",
            parent_linear_id=None,
            parent_identifier=None,
            assignee_id=None,
            assignee_name=None,
            labels=(),
            metadata={"codex_execution_reasoning_effort": "medium"},
            source_updated_at="2026-04-17T00:00:00+00:00",
            last_synced_at="2026-04-17T00:00:00+00:00",
            created_at=None,
            completed_at=None,
            canceled_at=None,
        )
        interpreted = interpret_slice_handoff(
            issue=issue,
            payload={
                "status": "blocked",
                "summary": "Wrong repo owner",
                "verified": False,
                "owner_mismatch": "The owning implementation lives in another repo.",
            },
            continuity=None,
        )

        self.assertEqual(interpreted.action, "reroute")
        self.assertEqual(interpreted.payload["execution_reasoning_effort"], "high")
        self.assertEqual(interpreted.payload["execution_escalation_trigger"], "owner_mismatch")
        self.assertEqual(interpreted.follow_ups[0]["follow_up_class"], "owner_reroute")
        self.assertEqual(interpreted.follow_ups[0]["relationship"], "blocked_by")

    def test_scope_mismatch_escalates_to_xhigh(self) -> None:
        issue = MirroredIssueRecord(
            linear_id="issue-3",
            identifier="PRO-3",
            title="Split broad ticket",
            description="## Goal\nSplit broad ticket\n",
            team_id="team-1",
            team_name="Projects",
            state_id="state-1",
            state_name="Todo",
            state_type="unstarted",
            priority=0,
            project_id="project-1",
            project_name="Alpha",
            parent_linear_id=None,
            parent_identifier=None,
            assignee_id=None,
            assignee_name=None,
            labels=(),
            metadata={"codex_execution_reasoning_effort": "medium"},
            source_updated_at="2026-04-17T00:00:00+00:00",
            last_synced_at="2026-04-17T00:00:00+00:00",
            created_at=None,
            completed_at=None,
            canceled_at=None,
        )
        interpreted = interpret_slice_handoff(
            issue=issue,
            payload={
                "status": "blocked",
                "summary": "Too broad",
                "verified": False,
                "scope_mismatch": "This execution slice uncovered multiple independent work surfaces.",
            },
            continuity=None,
        )

        self.assertEqual(interpreted.action, "replan")
        self.assertEqual(interpreted.payload["execution_reasoning_effort"], "xhigh")
        self.assertEqual(interpreted.payload["execution_escalation_trigger"], "scope_mismatch")
