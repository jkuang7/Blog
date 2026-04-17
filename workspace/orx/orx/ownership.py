"""Ownership and anti-hijack checks for ORX."""

from __future__ import annotations

from dataclasses import dataclass

from .repository import LeaseConflictError, LeaseRecord, OrxRepository, RunnerRecord


class OwnershipConflictError(RuntimeError):
    """Base error for ORX ownership conflicts."""


class UnknownOwnershipRunnerError(OwnershipConflictError):
    """Raised when ownership checks target an unregistered runner."""

    def __init__(self, runner_id: str) -> None:
        super().__init__(f"Runner {runner_id} is not registered for ownership checks.")
        self.runner_id = runner_id


class IssueOwnershipConflictError(OwnershipConflictError):
    """Raised when a different runner already owns the issue lease."""

    def __init__(self, issue_key: str, active_runner_id: str) -> None:
        super().__init__(
            f"Issue {issue_key} is already owned by runner {active_runner_id}."
        )
        self.issue_key = issue_key
        self.active_runner_id = active_runner_id


class ProtectedScopeConflictError(OwnershipConflictError):
    """Raised when a protected tmux-backed scope is already owned by another issue."""

    def __init__(
        self,
        scope: str,
        *,
        active_issue_key: str,
        active_runner_id: str,
    ) -> None:
        super().__init__(
            f"Protected scope {scope} is already owned by issue {active_issue_key} "
            f"through runner {active_runner_id}."
        )
        self.scope = scope
        self.active_issue_key = active_issue_key
        self.active_runner_id = active_runner_id


@dataclass(frozen=True)
class OwnershipSnapshot:
    issue_key: str
    runner_id: str
    protected_scopes: tuple[str, ...]


class OwnershipService:
    def __init__(self, repository: OrxRepository) -> None:
        self.repository = repository

    def claim_issue(self, issue_key: str, runner_id: str) -> LeaseRecord:
        runner = self._require_runner(runner_id)
        self._assert_scope_available(issue_key, runner_id, runner)

        try:
            return self.repository.acquire_issue_lease(issue_key, runner_id)
        except LeaseConflictError as error:
            raise IssueOwnershipConflictError(
                issue_key=error.issue_key,
                active_runner_id=error.active_runner_id,
            ) from error

    def inspect_claim(self, issue_key: str, runner_id: str) -> OwnershipSnapshot:
        runner = self._require_runner(runner_id)
        return OwnershipSnapshot(
            issue_key=issue_key,
            runner_id=runner_id,
            protected_scopes=tuple(_protected_scopes(runner)),
        )

    def _assert_scope_available(
        self,
        issue_key: str,
        runner_id: str,
        runner: RunnerRecord,
    ) -> None:
        requested_scopes = set(_protected_scopes(runner))
        if not requested_scopes:
            return

        active_leases = self.repository.list_active_leases()
        for lease in active_leases:
            if lease.issue_key == issue_key:
                continue

            active_runner = self.repository.get_runner(lease.runner_id)
            if active_runner is None:
                continue

            for scope in requested_scopes.intersection(_protected_scopes(active_runner)):
                raise ProtectedScopeConflictError(
                    scope,
                    active_issue_key=lease.issue_key,
                    active_runner_id=lease.runner_id,
                )

    def _require_runner(self, runner_id: str) -> RunnerRecord:
        runner = self.repository.get_runner(runner_id)
        if runner is None:
            raise UnknownOwnershipRunnerError(runner_id)
        return runner


def _protected_scopes(runner: RunnerRecord) -> tuple[str, ...]:
    raw_scopes = runner.metadata.get("protected_scopes", [])
    if not isinstance(raw_scopes, list):
        return ()

    scopes: list[str] = []
    for entry in raw_scopes:
        if isinstance(entry, str):
            normalized = entry.strip()
            if normalized:
                scopes.append(normalized)
    return tuple(scopes)
