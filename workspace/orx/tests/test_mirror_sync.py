from __future__ import annotations

import tempfile
import unittest

from orx.config import resolve_runtime_paths
from orx.mirror import LinearMirrorRepository
from orx.mirror_sync import MirrorSyncService
from orx.storage import Storage


class MirrorSyncServiceTests(unittest.TestCase):
    def test_ingest_issue_payload_parses_metadata_into_mirror(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(resolve_runtime_paths(temp_dir))
            storage.bootstrap()
            service = MirrorSyncService(LinearMirrorRepository(storage))

            record = service.ingest_issue_payload(
                {
                    "id": "PRO-19",
                    "title": "Webhook ingest",
                    "description": """
                    Mirror update body
                    <!-- orx:metadata:start -->
                    {"selection_lane": "linear", "acceptance": ["tests"]}
                    <!-- orx:metadata:end -->
                    """,
                    "teamId": "team-1",
                    "team": "Projects",
                    "status": "In Progress",
                    "priority": 2,
                    "updatedAt": "2026-04-15T19:29:00+00:00",
                }
            )

            self.assertEqual(record.identifier, "PRO-19")
            self.assertEqual(record.metadata["selection_lane"], "linear")

    def test_reconcile_snapshot_marks_missing_and_clears_flag_when_restored(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(resolve_runtime_paths(temp_dir))
            storage.bootstrap()
            repository = LinearMirrorRepository(storage)
            service = MirrorSyncService(repository)

            result = service.reconcile_snapshot(
                [
                    {
                        "id": "PRO-19",
                        "title": "Webhook ingest",
                        "description": "Current issue",
                        "teamId": "team-1",
                        "team": "Projects",
                        "status": "In Progress",
                        "updatedAt": "2026-04-15T19:30:00+00:00",
                    },
                    {
                        "id": "PRO-20",
                        "title": "Ranking",
                        "description": "Current issue",
                        "teamId": "team-1",
                        "team": "Projects",
                        "status": "Todo",
                        "updatedAt": "2026-04-15T19:30:00+00:00",
                    },
                ]
            )
            self.assertEqual(result.marked_missing, ())

            result = service.reconcile_snapshot(
                [
                    {
                        "id": "PRO-20",
                        "title": "Ranking",
                        "description": "Current issue",
                        "teamId": "team-1",
                        "team": "Projects",
                        "status": "Todo",
                        "updatedAt": "2026-04-15T19:31:00+00:00",
                    }
                ]
            )

            missing = repository.get_issue(identifier="PRO-19")
            restored = repository.get_issue(identifier="PRO-20")
            assert missing is not None
            assert restored is not None
            self.assertTrue(missing.metadata["orx_reconciliation_missing_from_snapshot"])
            self.assertNotIn("orx_reconciliation_missing_from_snapshot", restored.metadata)
            self.assertEqual([record.identifier for record in result.marked_missing], ["PRO-19"])

            service.reconcile_snapshot(
                [
                    {
                        "id": "PRO-19",
                        "title": "Webhook ingest",
                        "description": "Current issue",
                        "teamId": "team-1",
                        "team": "Projects",
                        "status": "In Progress",
                        "updatedAt": "2026-04-15T19:32:00+00:00",
                    },
                    {
                        "id": "PRO-20",
                        "title": "Ranking",
                        "description": "Current issue",
                        "teamId": "team-1",
                        "team": "Projects",
                        "status": "Todo",
                        "updatedAt": "2026-04-15T19:32:00+00:00",
                    },
                ]
            )

            restored_missing = repository.get_issue(identifier="PRO-19")
            assert restored_missing is not None
            self.assertNotIn("orx_reconciliation_missing_from_snapshot", restored_missing.metadata)


if __name__ == "__main__":
    unittest.main()
