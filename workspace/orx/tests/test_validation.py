from __future__ import annotations

import tempfile
import unittest

from orx.config import resolve_runtime_paths
from orx.repository import OrxRepository
from orx.storage import Storage
from orx.validation import ValidationLedgerService


class ValidationLedgerServiceTests(unittest.TestCase):
    def test_record_and_list_validation_evidence(self) -> None:
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
            ledger = ValidationLedgerService(storage)

            record = ledger.record(
                issue_key="PRO-39",
                runner_id="runner-a",
                surface="cli",
                tool="operator",
                result="passed",
                confidence="confirmed",
                summary="manual validation",
                details={"path": "operator validations"},
                blockers=[],
            )
            latest = ledger.latest(issue_key="PRO-39", runner_id="runner-a")

            self.assertEqual(record.validation_id, 1)
            self.assertIsNotNone(latest)
            self.assertEqual(latest.summary if latest is not None else None, "manual validation")
            self.assertEqual(latest.details["path"] if latest is not None else None, "operator validations")


if __name__ == "__main__":
    unittest.main()
