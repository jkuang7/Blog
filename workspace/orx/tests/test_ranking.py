from __future__ import annotations

import tempfile
import unittest

from orx.config import resolve_runtime_paths
from orx.mirror import LinearMirrorRepository
from orx.ranking import LinearRankingService
from orx.storage import Storage


class LinearRankingServiceTests(unittest.TestCase):
    def test_rank_issues_uses_metadata_state_priority_and_tie_breaks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(resolve_runtime_paths(temp_dir))
            storage.bootstrap()
            repository = LinearMirrorRepository(storage)

            repository.upsert_issue(
                linear_id="3",
                identifier="PRO-30",
                title="Blocked started work",
                description="",
                team_id="team-1",
                team_name="Projects",
                state_name="In Progress",
                state_type="started",
                priority=2,
                metadata={"blocked": True},
                source_updated_at="2026-04-15T19:30:00+00:00",
            )
            repository.upsert_issue(
                linear_id="2",
                identifier="PRO-20",
                title="Todo high priority",
                description="",
                team_id="team-1",
                team_name="Projects",
                state_name="Todo",
                state_type="unstarted",
                priority=1,
                metadata={"priority_hint": "high"},
                source_updated_at="2026-04-15T19:31:00+00:00",
            )
            repository.upsert_issue(
                linear_id="1",
                identifier="PRO-10",
                title="Started work",
                description="",
                team_id="team-1",
                team_name="Projects",
                state_name="In Progress",
                state_type="started",
                priority=2,
                metadata={"priority_hint": "high"},
                source_updated_at="2026-04-15T19:32:00+00:00",
            )
            repository.upsert_issue(
                linear_id="4",
                identifier="PRO-40",
                title="Stale mirror",
                description="",
                team_id="team-1",
                team_name="Projects",
                state_name="In Progress",
                state_type="started",
                priority=1,
                metadata={"orx_reconciliation_missing_from_snapshot": True},
                source_updated_at="2026-04-15T19:33:00+00:00",
            )

            service = LinearRankingService(repository)
            ranked = service.rank_issues()

            self.assertEqual(
                [item.issue.identifier for item in ranked],
                ["PRO-10", "PRO-20", "PRO-30", "PRO-40"],
            )
            self.assertEqual(service.select_next_issue().identifier, "PRO-10")

    def test_rank_issues_prefers_runnable_leaf_over_rollups_and_inactive_parents(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(resolve_runtime_paths(temp_dir))
            storage.bootstrap()
            repository = LinearMirrorRepository(storage)

            repository.upsert_issue(
                linear_id="umbrella",
                identifier="PRO-5",
                title="Umbrella",
                description="",
                team_id="team-1",
                team_name="Projects",
                state_name="In Progress",
                state_type="started",
                source_updated_at="2026-04-15T20:00:00+00:00",
            )
            repository.upsert_issue(
                linear_id="phase-open",
                identifier="PRO-8",
                title="Open phase",
                description="",
                team_id="team-1",
                team_name="Projects",
                state_name="In Progress",
                state_type="started",
                parent_linear_id="umbrella",
                parent_identifier="PRO-5",
                source_updated_at="2026-04-15T20:01:00+00:00",
            )
            repository.upsert_issue(
                linear_id="phase-closed",
                identifier="PRO-13",
                title="Closed phase",
                description="",
                team_id="team-1",
                team_name="Projects",
                state_name="Done",
                state_type="completed",
                parent_linear_id="umbrella",
                parent_identifier="PRO-5",
                source_updated_at="2026-04-15T20:01:30+00:00",
                completed_at="2026-04-15T20:01:30+00:00",
            )
            repository.upsert_issue(
                linear_id="leaf-open",
                identifier="PRO-31",
                title="Runnable leaf",
                description="",
                team_id="team-1",
                team_name="Projects",
                state_name="In Progress",
                state_type="started",
                parent_linear_id="phase-open",
                parent_identifier="PRO-8",
                priority=2,
                source_updated_at="2026-04-15T20:02:00+00:00",
            )
            repository.upsert_issue(
                linear_id="leaf-blocked",
                identifier="PRO-32",
                title="Blocked leaf",
                description="",
                team_id="team-1",
                team_name="Projects",
                state_name="Todo",
                state_type="unstarted",
                parent_linear_id="phase-open",
                parent_identifier="PRO-8",
                metadata={"blocked_by": ["PRO-31"]},
                priority=1,
                source_updated_at="2026-04-15T20:03:00+00:00",
            )
            repository.upsert_issue(
                linear_id="leaf-inactive-parent",
                identifier="PRO-33",
                title="Closed parent leaf",
                description="",
                team_id="team-1",
                team_name="Projects",
                state_name="In Progress",
                state_type="started",
                parent_linear_id="phase-closed",
                parent_identifier="PRO-13",
                priority=1,
                source_updated_at="2026-04-15T20:04:00+00:00",
            )

            service = LinearRankingService(repository)
            ranked = service.rank_issues()

            self.assertEqual(
                [item.issue.identifier for item in ranked],
                ["PRO-31", "PRO-32", "PRO-33", "PRO-8", "PRO-5", "PRO-13"],
            )
            self.assertEqual(service.select_next_issue().identifier, "PRO-31")

    def test_manual_rank_and_identifier_tie_breaks_are_stable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(resolve_runtime_paths(temp_dir))
            storage.bootstrap()
            repository = LinearMirrorRepository(storage)

            repository.upsert_issue(
                linear_id="b",
                identifier="PRO-51",
                title="Later identifier",
                description="",
                team_id="team-1",
                team_name="Projects",
                state_name="Todo",
                state_type="unstarted",
                priority=2,
                metadata={"manual_rank": 1},
                source_updated_at="2026-04-15T19:32:00+00:00",
            )
            repository.upsert_issue(
                linear_id="a",
                identifier="PRO-50",
                title="Earlier identifier",
                description="",
                team_id="team-1",
                team_name="Projects",
                state_name="Todo",
                state_type="unstarted",
                priority=2,
                metadata={"manual_rank": 1},
                source_updated_at="2026-04-15T19:32:00+00:00",
            )

            service = LinearRankingService(repository)
            ranked = service.rank_issues()

            self.assertEqual([item.issue.identifier for item in ranked], ["PRO-50", "PRO-51"])


if __name__ == "__main__":
    unittest.main()
