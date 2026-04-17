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


class TelegramCommandAdapterTests(unittest.TestCase):
    def test_status_returns_continuity_and_proposals_without_queueing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter, repository = _telegram_fixture(temp_dir)

            payload = adapter.handle(
                {
                    "command_kind": "status",
                    "issue_key": "PRO-25",
                    "runner_id": "runner-a",
                }
            )

            self.assertEqual(payload["mode"], "status")
            self.assertEqual(payload["continuity"]["issue_key"], "PRO-25")
            self.assertEqual(len(payload["proposals"]), 1)
            self.assertEqual(payload["proposals"][0]["workflow_mode"], "leaf-ticket")
            self.assertEqual(payload["proposals"][0]["suggested_parent_issue_key"], "PRO-25")
            self.assertEqual(repository.list_commands(), [])

    def test_edit_token_replaces_pending_command_until_consumed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter, repository = _telegram_fixture(temp_dir)

            first = adapter.handle(
                {
                    "command_kind": "run",
                    "issue_key": "PRO-25",
                    "runner_id": "runner-a",
                    "edit_token": "chat-1:msg-9",
                    "payload": {"text": "first"},
                }
            )
            second = adapter.handle(
                {
                    "command_kind": "run",
                    "issue_key": "PRO-25",
                    "runner_id": "runner-a",
                    "edit_token": "chat-1:msg-9",
                    "payload": {"text": "second"},
                }
            )

            pending = repository.list_commands(status="pending", issue_key="PRO-25", runner_id="runner-a")
            superseded = repository.list_commands(
                status="superseded",
                issue_key="PRO-25",
                runner_id="runner-a",
            )

            self.assertEqual(first["mode"], "queued")
            self.assertEqual(second["mode"], "queued")
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0].payload["text"], "second")
            self.assertEqual(len(superseded), 1)

    def test_steer_command_preserves_interrupt_disposition(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter, repository = _telegram_fixture(temp_dir)

            payload = adapter.handle(
                {
                    "command_kind": "steer",
                    "issue_key": "PRO-25",
                    "runner_id": "runner-a",
                    "payload": {"instruction": "change direction"},
                }
            )
            pending = repository.list_commands(status="pending", issue_key="PRO-25", runner_id="runner-a")

            self.assertEqual(payload["mode"], "queued")
            self.assertEqual(pending[0].payload["disposition"], "interrupt")


def _telegram_fixture(temp_dir: str) -> tuple[TelegramCommandAdapter, OrxRepository]:
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
        issue_key="PRO-25",
        runner_id="runner-a",
        objective="Accept Telegram commands",
        slice_goal="Expose queue state",
        acceptance=["queue state visible"],
        validation_plan=["read continuity state"],
    )
    executor.submit_slice_result(
        request.slice_id,
        {
            "status": "success",
            "summary": "Continuity available for Telegram status",
            "verified": True,
            "next_slice": "Expose queue state",
            "artifacts": ["orx/telegram_adapter.py"],
            "metrics": {"telegram": 1},
        },
    )
    continuity = ContinuityService(storage)
    proposals = ProposalService(storage, continuity=continuity)
    proposals.route(
        "PRO-25",
        "runner-a",
        improvement_title="Polish Telegram status output",
    )
    return (
        TelegramCommandAdapter(
            repository=repository,
            continuity=continuity,
            proposals=proposals,
        ),
        repository,
    )


if __name__ == "__main__":
    unittest.main()
