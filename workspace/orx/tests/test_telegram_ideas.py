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
from orx.telegram_adapter import TelegramCommandAdapter

from tests.test_executor import FakeTmuxTransport


class TelegramIdeaIntakeTests(unittest.TestCase):
    def test_rough_idea_creates_durable_improvement_proposal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter, proposals = _idea_fixture(temp_dir)

            payload = adapter.intake_idea(
                {
                    "issue_key": "PRO-26",
                    "runner_id": "runner-a",
                    "title": "Capture a lightweight operator note stream",
                    "summary": "Useful follow-up for Telegram workflows.",
                }
            )

            self.assertEqual(payload["mode"], "idea")
            self.assertEqual(payload["proposal"]["proposal_kind"], "improvement-issue")
            self.assertEqual(len(proposals.list_open_proposals(issue_key="PRO-26")), 1)

    def test_rough_idea_can_request_hil_proposal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter, proposals = _idea_fixture(temp_dir)

            payload = adapter.intake_idea(
                {
                    "issue_key": "PRO-26",
                    "runner_id": "runner-a",
                    "title": "Need a human decision",
                    "summary": "Human should decide whether to split this workflow.",
                    "requires_hil": True,
                }
            )

            self.assertEqual(payload["proposal"]["proposal_kind"], "hil-proposal")
            self.assertEqual(proposals.list_open_proposals(issue_key="PRO-26")[0].proposal_kind, "hil-proposal")


def _idea_fixture(temp_dir: str) -> tuple[TelegramCommandAdapter, ProposalService]:
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
        issue_key="PRO-26",
        runner_id="runner-a",
        objective="Accept rough Telegram ideas",
        slice_goal="Persist durable proposal handoff",
        acceptance=["rough idea stored"],
        validation_plan=["query proposal journal"],
    )
    executor.submit_slice_result(
        request.slice_id,
        {
            "status": "success",
            "summary": "Rough-idea context persisted",
            "verified": True,
            "next_slice": "Persist durable proposal handoff",
            "artifacts": ["orx/telegram_adapter.py"],
            "metrics": {"ideas": 0},
        },
    )
    continuity = ContinuityService(storage)
    proposals = ProposalService(storage, continuity=continuity)
    return (
        TelegramCommandAdapter(
            repository=repository,
            continuity=continuity,
            proposals=proposals,
        ),
        proposals,
    )


if __name__ == "__main__":
    unittest.main()
