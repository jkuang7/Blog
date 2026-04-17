from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta

from orx.config import resolve_runtime_paths
from orx.executor import ExecutorService
from orx.ownership import OwnershipService
from orx.proposals import ProposalService
from orx.recovery import RecoveryService
from orx.repository import OrxRepository
from orx.storage import Storage

from tests.test_executor import FakeTmuxTransport


class RecoveryServiceTests(unittest.TestCase):
    def test_assess_requires_verification_after_repeated_no_delta_slices(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage, executor = _recovery_fixture(temp_dir)
            initial = executor.dispatch_slice(
                issue_key="PRO-23",
                runner_id="runner-a",
                objective="Prevent no-delta spinning",
                slice_goal="Validate exact delta",
                acceptance=["verified delta tracked"],
                validation_plan=["compare continuity verified delta"],
            )
            executor.submit_slice_result(
                initial.slice_id,
                {
                    "status": "success",
                    "summary": "Verified delta stored",
                    "verified": True,
                    "next_slice": "Validate exact delta",
                    "artifacts": ["orx/continuity.py"],
                    "metrics": {"delta": 1},
                },
            )
            for _ in range(2):
                request = executor.dispatch_slice(
                    issue_key="PRO-23",
                    runner_id="runner-a",
                    objective="Prevent no-delta spinning",
                    slice_goal="Validate exact delta",
                    acceptance=["verified delta tracked"],
                    validation_plan=["compare continuity verified delta"],
                )
                executor.submit_slice_result(
                    request.slice_id,
                    {
                        "status": "success",
                        "summary": "Verified delta stored",
                        "verified": True,
                        "next_slice": "Validate exact delta",
                        "artifacts": ["orx/continuity.py"],
                        "metrics": {"delta": 1},
                    },
                )

            decision = RecoveryService(storage).assess("PRO-23", "runner-a")

            self.assertEqual(decision.action, "verify")

    def test_assess_escalates_to_hil_after_repeated_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage, executor = _recovery_fixture(temp_dir)
            for _ in range(2):
                request = executor.dispatch_slice(
                    issue_key="PRO-23",
                    runner_id="runner-a",
                    objective="Recover from repeated failures",
                    slice_goal="Retry failed step",
                    acceptance=["failure signatures tracked"],
                    validation_plan=["inspect failure signature count"],
                )
                executor.submit_slice_result(
                    request.slice_id,
                    {
                        "status": "failed",
                        "summary": "tmux session lost output",
                        "verified": False,
                        "next_slice": "Retry failed step",
                        "artifacts": ["orx/recovery.py"],
                        "metrics": {"retries": 1},
                    },
                )

            proposals = ProposalService(storage)
            decision = RecoveryService(storage, proposals=proposals).assess("PRO-23", "runner-a")

            self.assertEqual(decision.action, "hil")
            self.assertIsNotNone(decision.proposal_key)
            self.assertEqual(len(proposals.list_open_proposals(issue_key="PRO-23")), 1)

    def test_list_stale_recovery_candidates_finds_old_inflight_slice(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage, executor = _recovery_fixture(temp_dir)
            request = executor.dispatch_slice(
                issue_key="PRO-23",
                runner_id="runner-a",
                objective="Resume stale slice",
                slice_goal="Replay in-flight slice",
                acceptance=["stale slice listed"],
                validation_plan=["scan continuity candidates"],
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
                    (stale_timestamp, "PRO-23", "runner-a"),
                )

            recovery = RecoveryService(storage)
            candidates = recovery.list_stale_recovery_candidates(
                stale_after_seconds=300,
                now=datetime.now(UTC),
            )
            decision = recovery.assess("PRO-23", "runner-a")

            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0].active_slice_id, request.slice_id)
            self.assertEqual(decision.action, "resume")


def _recovery_fixture(temp_dir: str) -> tuple[Storage, ExecutorService]:
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
    return storage, executor


if __name__ == "__main__":
    unittest.main()
