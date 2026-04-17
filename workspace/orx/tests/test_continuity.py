from __future__ import annotations

import tempfile
import unittest

from orx.config import resolve_runtime_paths
from orx.continuity import ContinuityService
from orx.executor import ExecutorService
from orx.ownership import OwnershipService
from orx.repository import OrxRepository
from orx.storage import Storage
from tests.test_executor import FakeTmuxTransport


class ContinuityServiceTests(unittest.TestCase):
    def test_dispatch_persists_continuity_across_restart(self) -> None:
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
            service = ExecutorService(
                storage=storage,
                repository=repository,
                ownership=OwnershipService(repository),
                transport=FakeTmuxTransport(),
            )

            request = service.dispatch_slice(
                issue_key="PRO-21",
                runner_id="runner-a",
                objective="Recover exact next slice after restart",
                slice_goal="Persist continuity state",
                acceptance=["continuity state saved"],
                validation_plan=["reload continuity from sqlite"],
                blockers=["none"],
                discovered_gaps=["replay not wired yet"],
                idempotency_key="pro-21-continuity-1",
                resume_context={"source": "unit-test"},
            )

            continuity = ContinuityService(storage)
            state = continuity.get_state("PRO-21", "runner-a")

            assert state is not None
            self.assertEqual(request.slice_id, state.active_slice_id)
            self.assertEqual(state.next_slice, "Persist continuity state")
            self.assertEqual(
                state.validation_plan,
                ("reload continuity from sqlite",),
            )
            self.assertEqual(state.resume_context["source"], "unit-test")
            self.assertEqual(continuity.get_next_slice("PRO-21", "runner-a"), state.next_slice)

    def test_submit_slice_result_updates_verified_delta_and_recovery_state(self) -> None:
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
            service = ExecutorService(
                storage=storage,
                repository=repository,
                ownership=OwnershipService(repository),
                transport=transport,
            )
            request = service.dispatch_slice(
                issue_key="PRO-21",
                runner_id="runner-a",
                objective="Recover exact next slice after restart",
                slice_goal="Persist continuity state",
                acceptance=["continuity state saved"],
                validation_plan=["reload continuity from sqlite"],
            )

            continuity = ContinuityService(storage)
            self.assertEqual(len(continuity.list_recovery_candidates()), 1)

            service.submit_slice_result(
                request.slice_id,
                {
                    "status": "success",
                    "summary": "Stored continuity in sqlite",
                    "verified": True,
                    "next_slice": "Route oversized work into proposal flow",
                    "artifacts": ["orx/continuity.py", "tests/test_continuity.py"],
                    "metrics": {"continuity_updates": 1},
                },
            )

            state = continuity.get_state("PRO-21", "runner-a")
            assert state is not None
            self.assertIsNone(state.active_slice_id)
            self.assertEqual(state.verified_delta, "Stored continuity in sqlite")
            self.assertEqual(state.next_slice, "Route oversized work into proposal flow")
            self.assertEqual(
                state.artifact_pointers,
                ("orx/continuity.py", "tests/test_continuity.py"),
            )
            self.assertEqual(state.last_result_status, "success")
            self.assertEqual(continuity.list_recovery_candidates(), [])

    def test_recovery_candidates_survive_service_recreation(self) -> None:
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
            ExecutorService(
                storage=storage,
                repository=repository,
                ownership=OwnershipService(repository),
                transport=FakeTmuxTransport(),
            ).dispatch_slice(
                issue_key="PRO-21",
                runner_id="runner-a",
                objective="Recover exact next slice after restart",
                slice_goal="Persist continuity state",
                acceptance=["continuity state saved"],
                validation_plan=["reload continuity from sqlite"],
            )

            continuity = ContinuityService(storage)
            candidates = continuity.list_recovery_candidates()

            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0].issue_key, "PRO-21")
            self.assertEqual(candidates[0].slice_goal, "Persist continuity state")


if __name__ == "__main__":
    unittest.main()
