from __future__ import annotations

import tempfile
import unittest

from orx.commands import normalize_command
from orx.config import resolve_runtime_paths
from orx.ownership import (
    IssueOwnershipConflictError,
    OwnershipService,
    ProtectedScopeConflictError,
)
from orx.repository import LeaseConflictError, OrxRepository
from orx.storage import Storage


class RepositoryTests(unittest.TestCase):
    def test_runner_lease_and_queue_operations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(resolve_runtime_paths(temp_dir))
            storage.bootstrap()
            repository = OrxRepository(storage)

            runner_a = repository.upsert_runner(
                "runner-a",
                transport="tmux-codex",
                display_name="Runner A",
                state="idle",
                metadata={"host": "mac-mini"},
            )
            runner_b = repository.upsert_runner(
                "runner-b",
                transport="telecodex",
                display_name="Runner B",
                state="idle",
            )

            self.assertEqual(runner_a.metadata["host"], "mac-mini")
            self.assertEqual(runner_b.transport, "telecodex")

            first_lease = repository.acquire_issue_lease("PRO-14", "runner-a")
            idempotent_lease = repository.acquire_issue_lease("PRO-14", "runner-a")

            self.assertEqual(first_lease.lease_id, idempotent_lease.lease_id)
            self.assertEqual(len(repository.list_active_leases()), 1)

            with self.assertRaises(LeaseConflictError):
                repository.acquire_issue_lease("PRO-14", "runner-b")

            released = repository.release_issue_lease("PRO-14", "runner-a")
            assert released is not None
            self.assertIsNotNone(released.released_at)
            self.assertEqual(repository.list_active_leases(), [])

            next_lease = repository.acquire_issue_lease("PRO-14", "runner-b")
            self.assertEqual(next_lease.runner_id, "runner-b")

            repository.enqueue_command(
                "run",
                issue_key="PRO-14",
                runner_id="runner-b",
                payload={"slice": "storage"},
                priority=20,
            )
            repository.enqueue_command(
                "status",
                issue_key="PRO-14",
                runner_id="runner-b",
                priority=5,
            )

            commands = repository.list_commands()
            self.assertEqual([command.command_kind for command in commands], ["status", "run"])
            self.assertEqual(commands[0].priority, 5)
            self.assertEqual(commands[1].payload["slice"], "storage")

    def test_enqueue_normalized_command_supersedes_matching_pending_commands(self) -> None:
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

            pause_command = normalize_command(
                "pause",
                issue_key="PRO-15",
                runner_id="runner-a",
            )
            resume_command = normalize_command(
                "resume",
                issue_key="PRO-15",
                runner_id="runner-a",
            )

            first = repository.enqueue_normalized_command(pause_command)
            second = repository.enqueue_normalized_command(resume_command)

            pending = repository.list_commands(status="pending")
            superseded = repository.list_commands(status="superseded")

            self.assertEqual([command.command_id for command in pending], [second.command_id])
            self.assertEqual([command.command_id for command in superseded], [first.command_id])
            self.assertEqual(pending[0].payload["replacement_key"], superseded[0].payload["replacement_key"])

    def test_ownership_service_rejects_protected_scope_collisions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(resolve_runtime_paths(temp_dir))
            storage.bootstrap()
            repository = OrxRepository(storage)
            ownership = OwnershipService(repository)

            repository.upsert_runner(
                "runner-a",
                transport="tmux-codex",
                display_name="Runner A",
                state="idle",
                metadata={"protected_scopes": ["tmux:workspace:/Users/jian/Dev/workspace/orx"]},
            )
            repository.upsert_runner(
                "runner-b",
                transport="tmux-codex",
                display_name="Runner B",
                state="idle",
                metadata={"protected_scopes": ["tmux:workspace:/Users/jian/Dev/workspace/orx"]},
            )

            lease = ownership.claim_issue("PRO-15", "runner-a")
            self.assertEqual(lease.issue_key, "PRO-15")

            with self.assertRaises(ProtectedScopeConflictError):
                ownership.claim_issue("PRO-16", "runner-b")

    def test_ownership_service_rejects_issue_hijack_and_allows_separate_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(resolve_runtime_paths(temp_dir))
            storage.bootstrap()
            repository = OrxRepository(storage)
            ownership = OwnershipService(repository)

            repository.upsert_runner(
                "runner-a",
                transport="tmux-codex",
                display_name="Runner A",
                state="idle",
                metadata={"protected_scopes": ["tmux:workspace:/Users/jian/Dev/workspace/orx"]},
            )
            repository.upsert_runner(
                "runner-b",
                transport="telecodex",
                display_name="Runner B",
                state="idle",
                metadata={"protected_scopes": ["tmux:workspace:/Users/jian/Dev/workspace/other"]},
            )

            first = ownership.claim_issue("PRO-16", "runner-a")
            second = ownership.claim_issue("PRO-16", "runner-a")
            self.assertEqual(first.lease_id, second.lease_id)

            with self.assertRaises(IssueOwnershipConflictError):
                ownership.claim_issue("PRO-16", "runner-b")

            separate = ownership.claim_issue("PRO-17", "runner-b")
            self.assertEqual(separate.runner_id, "runner-b")


if __name__ == "__main__":
    unittest.main()
