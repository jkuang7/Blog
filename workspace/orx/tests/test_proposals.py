from __future__ import annotations

import tempfile
import unittest

from orx.config import resolve_runtime_paths
from orx.continuity import ContinuityService
from orx.executor import ExecutorService
from orx.ownership import OwnershipService
from orx.proposals import ProposalService
from orx.repository import OrxRepository
from orx.storage import Storage

from tests.test_executor import FakeTmuxTransport


class ProposalServiceTests(unittest.TestCase):
    def test_route_same_scope_continuation_from_stored_continuity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = _proposal_fixture(temp_dir)
            proposal = service.route("PRO-22", "runner-a")

            self.assertEqual(proposal.proposal_kind, "same-scope-continuation")
            self.assertIn("Continue PRO-22", proposal.title)
            self.assertEqual(proposal.context["next_slice"], "Persist proposal records")
            self.assertEqual(proposal.decomposition_class, "same_scope_continuation")
            self.assertEqual(proposal.workflow_mode, "same-issue")
            self.assertEqual(proposal.target_issue_key, "PRO-22")
            self.assertIsNone(proposal.suggested_parent_issue_key)

    def test_route_child_dependency_improvement_and_hil_deterministically(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = _proposal_fixture(temp_dir, blockers=["blocked-on-api"])

            hil = service.route("PRO-22", "runner-a", hil_reason="Need human approval for scope change")
            dependency = service.route("PRO-22", "runner-a")
            improvement = service.route(
                "PRO-22",
                "runner-a",
                improvement_title="Tighten proposal status journal",
                dependency_issue=None,
            )
            child = service.route(
                "PRO-22",
                "runner-a",
                oversized=True,
                dependency_issue=None,
                improvement_title=None,
                hil_reason=None,
                context={"force": "child"},
            )

            self.assertEqual(hil.proposal_kind, "hil-proposal")
            self.assertEqual(dependency.proposal_kind, "dependency-issue")
            self.assertEqual(improvement.proposal_kind, "dependency-issue")
            self.assertEqual(child.proposal_kind, "dependency-issue")
            self.assertEqual(hil.decomposition_class, "hil_proposal")
            self.assertEqual(hil.workflow_mode, "hil")
            self.assertEqual(dependency.suggested_parent_issue_key, "PRO-22")
            self.assertEqual(dependency.suggested_phase_issue_key, "PRO-22")

    def test_route_unblocked_leaf_proposals_include_handoff_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = _proposal_fixture(temp_dir, blockers=[])

            improvement = service.route(
                "PRO-22",
                "runner-a",
                improvement_title="Refine proposal lifecycle CLI",
                context={"suggested_phase_issue_key": "PRO-10"},
            )
            child = service.route(
                "PRO-22",
                "runner-a",
                oversized=True,
                context={"suggested_parent_issue_key": "PRO-10", "suggested_phase_issue_key": "PRO-10"},
            )

            self.assertEqual(improvement.proposal_kind, "improvement-issue")
            self.assertEqual(improvement.decomposition_class, "improvement_issue")
            self.assertEqual(improvement.workflow_mode, "leaf-ticket")
            self.assertEqual(improvement.suggested_parent_issue_key, "PRO-22")
            self.assertEqual(improvement.suggested_phase_issue_key, "PRO-10")
            self.assertEqual(child.proposal_kind, "child-issue")
            self.assertEqual(child.decomposition_class, "child_issue")
            self.assertEqual(child.workflow_mode, "leaf-ticket")
            self.assertEqual(child.suggested_parent_issue_key, "PRO-10")
            self.assertEqual(child.suggested_phase_issue_key, "PRO-10")

    def test_route_persists_open_proposals_across_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = _proposal_fixture(temp_dir, blockers=[])
            first = service.route(
                "PRO-22",
                "runner-a",
                improvement_title="Refine proposal lifecycle CLI",
            )
            second = ProposalService(
                Storage(resolve_runtime_paths(temp_dir)),
                continuity=ContinuityService(Storage(resolve_runtime_paths(temp_dir))),
            )
            proposals = second.list_open_proposals(issue_key="PRO-22")

            self.assertEqual(len(proposals), 1)
            self.assertEqual(proposals[0].proposal_key, first.proposal_key)
            self.assertEqual(proposals[0].proposal_kind, "improvement-issue")
            self.assertEqual(proposals[0].decomposition_class, "improvement_issue")
            self.assertEqual(proposals[0].workflow_mode, "leaf-ticket")
            self.assertEqual(proposals[0].suggested_parent_issue_key, "PRO-22")


def _proposal_fixture(
    temp_dir: str,
    *,
    blockers: list[str] | None = None,
) -> ProposalService:
    storage = Storage(resolve_runtime_paths(temp_dir))
    storage.bootstrap()
    repository = OrxRepository(storage)
    repository.upsert_runner(
        "runner-a",
        transport="tmux-codex",
        display_name="Runner A",
        state="idle",
    )
    executor = ExecutorService(
        storage=storage,
        repository=repository,
        ownership=OwnershipService(repository),
        transport=FakeTmuxTransport(),
    )
    request = executor.dispatch_slice(
        issue_key="PRO-22",
        runner_id="runner-a",
        objective="Route decomposition decisions from continuity",
        slice_goal="Persist proposal records",
        acceptance=["proposal stored"],
        validation_plan=["reload proposal from sqlite"],
        blockers=blockers or [],
    )
    executor.submit_slice_result(
        request.slice_id,
        {
            "status": "success",
            "summary": "Continuity state ready for proposal routing",
            "verified": True,
            "next_slice": "Persist proposal records",
            "artifacts": ["orx/proposals.py"],
            "metrics": {"proposals": 0},
        },
    )
    return ProposalService(storage, continuity=ContinuityService(storage))


if __name__ == "__main__":
    unittest.main()
