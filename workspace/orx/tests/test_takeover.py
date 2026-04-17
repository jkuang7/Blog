from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from orx.config import resolve_runtime_paths
from orx.executor import ExecutorService
from orx.operator import OperatorService
from orx.ownership import OwnershipService
from orx.repository import OrxRepository
from orx.storage import Storage
from orx.takeover import TakeoverConflictError

from tests.test_executor import FakeTmuxTransport


REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = REPO_ROOT / "bin" / "orx"


class TakeoverServiceTests(unittest.TestCase):
    def test_control_requires_active_takeover_and_can_be_returned(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage, repository = _takeover_fixture(temp_dir)
            service = OperatorService(storage=storage, repository=repository)

            with self.assertRaises(TakeoverConflictError):
                service.control_payload(
                    operator_id="jian",
                    command_kind="pause",
                    issue_key="PRO-28",
                    runner_id="runner-a",
                    payload=None,
                )

            takeover = service.takeover_payload(
                issue_key="PRO-28",
                runner_id="runner-a",
                operator_id="jian",
                rationale="Investigate local issue",
            )
            command = service.control_payload(
                operator_id="jian",
                command_kind="pause",
                issue_key="PRO-28",
                runner_id="runner-a",
                payload={"source": "ssh"},
            )
            returned = service.return_control_payload(
                issue_key="PRO-28",
                runner_id="runner-a",
                operator_id="jian",
                note="Done investigating",
            )

            self.assertEqual(takeover["takeover"]["status"], "active")
            self.assertEqual(command["command"]["payload"]["takeover"]["operator_id"], "jian")
            self.assertEqual(returned["takeover"]["status"], "released")

    def test_operator_cli_can_journal_takeover_and_queue_control(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _takeover_fixture(temp_dir)

            takeover = self._run(
                temp_dir,
                "operator",
                "takeover",
                "--issue-key",
                "PRO-28",
                "--runner-id",
                "runner-a",
                "--operator-id",
                "jian",
                "--reason",
                "Investigate local issue",
            )
            takeovers = self._run(temp_dir, "operator", "takeovers")
            command = self._run(
                temp_dir,
                "operator",
                "control",
                "--kind",
                "steer",
                "--issue-key",
                "PRO-28",
                "--runner-id",
                "runner-a",
                "--operator-id",
                "jian",
                "--payload-json",
                '{"instruction":"switch plan"}',
            )
            returned = self._run(
                temp_dir,
                "operator",
                "return-control",
                "--issue-key",
                "PRO-28",
                "--runner-id",
                "runner-a",
                "--operator-id",
                "jian",
                "--note",
                "Done investigating",
            )

            self.assertEqual(takeover["takeover"]["status"], "active")
            self.assertEqual(len(takeovers["takeovers"]), 1)
            self.assertEqual(command["command"]["command_kind"], "steer")
            self.assertEqual(returned["takeover"]["status"], "released")

    def _run(self, temp_dir: str, *args: str) -> dict[str, object]:
        completed = subprocess.run(
            [sys.executable, str(CLI), "--json", "--home", temp_dir, *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)


def _takeover_fixture(temp_dir: str) -> tuple[Storage, OrxRepository]:
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
        issue_key="PRO-28",
        runner_id="runner-a",
        objective="Protect local control mutations",
        slice_goal="Require explicit takeover",
        acceptance=["takeover journal stored"],
        validation_plan=["list active takeovers"],
    )
    return storage, repository


if __name__ == "__main__":
    unittest.main()
