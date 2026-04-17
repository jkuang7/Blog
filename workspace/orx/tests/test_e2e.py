from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta

from orx.config import resolve_runtime_paths
from orx.continuity import ContinuityService
from orx.executor import ExecutorService
from orx.operator import OperatorService
from orx.ownership import OwnershipService
from orx.proposals import ProposalService
from orx.recovery import RecoveryService
from orx.repository import OrxRepository
from orx.storage import Storage
from orx.telegram_adapter import TelegramCommandAdapter

from tests.test_executor import FakeTmuxTransport


class EndToEndFlowTests(unittest.TestCase):
    def test_control_plane_flow_covers_run_remote_commands_proposals_recovery_and_takeover(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(resolve_runtime_paths(temp_dir))
            storage.bootstrap()
            repository = OrxRepository(storage)
            repository.upsert_runner(
                "runner-a",
                transport="tmux-codex",
                display_name="Runner A",
                state="idle",
            )
            transport = FakeTmuxTransport()
            executor = ExecutorService(
                storage=storage,
                repository=repository,
                ownership=OwnershipService(repository),
                transport=transport,
            )
            continuity = ContinuityService(storage)
            proposals = ProposalService(storage, continuity=continuity)
            recovery = RecoveryService(storage, continuity=continuity, proposals=proposals)
            adapter = TelegramCommandAdapter(
                repository=repository,
                continuity=continuity,
                proposals=proposals,
            )
            operator = OperatorService(
                storage=storage,
                repository=repository,
                continuity=continuity,
                proposals=proposals,
                recovery=recovery,
                transport=transport,
            )

            run_request = executor.dispatch_slice(
                issue_key="PRO-29",
                runner_id="runner-a",
                objective="Run integrated ORX verification",
                slice_goal="Verify restart continuity",
                acceptance=["continuity survives restart"],
                validation_plan=["reload continuity from sqlite"],
            )
            executor.submit_slice_result(
                run_request.slice_id,
                {
                    "status": "success",
                    "summary": "Initial run completed",
                    "verified": True,
                    "next_slice": "Verify restart continuity",
                    "artifacts": ["tests/test_e2e.py"],
                    "metrics": {"phase": 1},
                },
            )

            pause = adapter.handle(
                {
                    "command_kind": "pause",
                    "issue_key": "PRO-29",
                    "runner_id": "runner-a",
                    "edit_token": "tg-1",
                }
            )
            steer = adapter.handle(
                {
                    "command_kind": "steer",
                    "issue_key": "PRO-29",
                    "runner_id": "runner-a",
                    "payload": {"instruction": "switch to cutover docs"},
                }
            )
            resume = adapter.handle(
                {
                    "command_kind": "resume",
                    "issue_key": "PRO-29",
                    "runner_id": "runner-a",
                }
            )
            idea = adapter.intake_idea(
                {
                    "issue_key": "PRO-29",
                    "runner_id": "runner-a",
                    "title": "Document remaining cutover risks",
                    "summary": "Carry the last known risks into the docs.",
                }
            )

            restarted_continuity = ContinuityService(storage)
            self.assertEqual(
                restarted_continuity.get_next_slice("PRO-29", "runner-a"),
                "Verify restart continuity",
            )

            stale_request = executor.dispatch_slice(
                issue_key="PRO-29",
                runner_id="runner-a",
                objective="Resume after restart",
                slice_goal="Replay stale slice",
                acceptance=["stale slice recoverable"],
                validation_plan=["scan recovery candidates"],
            )
            stale_timestamp = (
                datetime.now(UTC) - timedelta(minutes=10)
            ).isoformat(timespec="seconds")
            with storage.session() as connection:
                connection.execute(
                    """
                    UPDATE continuity_state
                    SET updated_at = ?
                    WHERE issue_key = ? AND runner_id = ?
                    """,
                    (stale_timestamp, "PRO-29", "runner-a"),
                )

            takeover = operator.takeover_payload(
                issue_key="PRO-29",
                runner_id="runner-a",
                operator_id="jian",
                rationale="Inspect stale runner locally",
            )
            control = operator.control_payload(
                operator_id="jian",
                command_kind="pause",
                issue_key="PRO-29",
                runner_id="runner-a",
                payload={"source": "ssh"},
            )
            status = operator.status_payload(issue_key="PRO-29", runner_id="runner-a")
            recovery_payload = operator.recovery_payload(stale_after_seconds=0)
            returned = operator.return_control_payload(
                issue_key="PRO-29",
                runner_id="runner-a",
                operator_id="jian",
                note="Inspection complete",
            )

            self.assertEqual(pause["mode"], "queued")
            self.assertEqual(steer["mode"], "queued")
            self.assertEqual(steer["command"]["payload"]["disposition"], "interrupt")
            self.assertEqual(resume["mode"], "queued")
            self.assertEqual(idea["proposal"]["proposal_kind"], "improvement-issue")
            self.assertEqual(takeover["takeover"]["status"], "active")
            self.assertEqual(control["command"]["command_kind"], "pause")
            self.assertEqual(status["continuity"]["issue_key"], "PRO-29")
            self.assertTrue(status["proposals"])
            self.assertEqual(recovery_payload["recovery"][0]["active_slice_id"], stale_request.slice_id)
            self.assertEqual(returned["takeover"]["status"], "released")


if __name__ == "__main__":
    unittest.main()
