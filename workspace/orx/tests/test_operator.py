from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from orx.config import resolve_runtime_paths
from orx.executor import ExecutorService
from orx.linear_client import LinearIssue
from orx.mirror import LinearMirrorRepository
from orx.operator import OperatorService
from orx.ownership import OwnershipService
from orx.proposal_materialization import ProposalMaterializationService
from orx.proposals import ProposalService
from orx.repository import OrxRepository
from orx.runtime_state import DaemonStateService
from orx.storage import Storage

from tests.test_executor import FakeTmuxTransport
from tests.test_proposal_materialization import FakeLinearClient


REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = REPO_ROOT / "bin" / "orx"


class OperatorServiceTests(unittest.TestCase):
    def test_operator_service_exposes_attach_target_pane_and_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage, repository, transport = _operator_fixture(temp_dir)
            service = OperatorService(
                storage=storage,
                repository=repository,
                transport=transport,
            )

            attach = service.attach_target_payload(runner_id="runner-a")
            pane = service.pane_payload(runner_id="runner-a", lines=25)
            recovery = service.recovery_payload(stale_after_seconds=0)

            self.assertEqual(attach["attach_target"], "runner-runner-a")
            self.assertIn("capture:", pane["pane"])
            self.assertEqual(len(recovery["recovery"]), 1)

    def test_operator_cli_emits_json_for_runner_queue_status_and_attach(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _operator_fixture(temp_dir)

            runners = self._run(temp_dir, "operator", "runners")
            daemon = self._run(temp_dir, "operator", "daemon")
            queue = self._run(temp_dir, "operator", "queue", "--runner-id", "runner-a")
            status = self._run(
                temp_dir,
                "operator",
                "status",
                "--issue-key",
                "PRO-27",
                "--runner-id",
                "runner-a",
            )
            attach = self._run(temp_dir, "operator", "attach-target", "--runner-id", "runner-a")
            recovery = self._run(temp_dir, "operator", "recovery", "--stale-after-seconds", "0")

            self.assertEqual(runners["runners"][0]["runner"]["runner_id"], "runner-a")
            self.assertEqual(daemon["daemon"]["tick"], "idle")
            self.assertEqual(queue["queue"][0]["command_kind"], "pause")
            self.assertEqual(status["continuity"]["issue_key"], "PRO-27")
            self.assertEqual(status["daemon"]["tick"], "idle")
            self.assertEqual(attach["attach_target"], "runner-runner-a")
            self.assertEqual(recovery["recovery"][0]["issue_key"], "PRO-27")

    def test_operator_service_can_materialize_proposal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage, repository, transport = _operator_fixture(temp_dir)
            proposals = ProposalService(storage)
            mirror = LinearMirrorRepository(storage)
            phase = mirror.upsert_issue(
                linear_id="phase-10",
                identifier="PRO-10",
                title="Continuity",
                description="",
                team_id="team-1",
                team_name="Projects",
                state_name="In Progress",
                state_type="started",
                project_id="project-1",
                project_name="ORX",
                source_updated_at="2026-04-15T22:10:00+00:00",
            )
            mirror.upsert_issue(
                linear_id="leaf-27",
                identifier="PRO-27",
                title="Operator control",
                description="",
                team_id=phase.team_id,
                team_name=phase.team_name,
                state_name="In Progress",
                state_type="started",
                project_id=phase.project_id,
                project_name=phase.project_name,
                parent_linear_id=phase.linear_id,
                parent_identifier=phase.identifier,
                source_updated_at="2026-04-15T22:11:00+00:00",
            )
            proposal = proposals.route(
                "PRO-27",
                "runner-a",
                improvement_title="Polish operator proposal handoff",
                context={"suggested_phase_issue_key": "PRO-10"},
            )
            materializer = ProposalMaterializationService(
                storage,
                proposals=proposals,
                mirror=mirror,
                client=FakeLinearClient(),
            )
            service = OperatorService(
                storage=storage,
                repository=repository,
                transport=transport,
                proposals=proposals,
                materializer=materializer,
            )

            payload = service.materialize_proposal_payload(proposal_id=proposal.proposal_id)
            materialized = service.proposals_payload(issue_key="PRO-27", status="materialized")

            self.assertFalse(payload["idempotent"])
            self.assertEqual(payload["created_issue"]["identifier"], "PRO-90")
            self.assertEqual(payload["proposal"]["status"], "materialized")
            self.assertEqual(materialized["proposals"][0]["status"], "materialized")

    def test_operator_cli_can_record_and_list_validation_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _operator_fixture(temp_dir)

            recorded = self._run(
                temp_dir,
                "operator",
                "record-validation",
                "--issue-key",
                "PRO-27",
                "--runner-id",
                "runner-a",
                "--surface",
                "cli",
                "--tool",
                "operator",
                "--result",
                "passed",
                "--confidence",
                "confirmed",
                "--summary",
                "manual operator validation",
                "--details-json",
                '{"path":"operator record-validation"}',
            )
            listing = self._run(
                temp_dir,
                "operator",
                "validations",
                "--issue-key",
                "PRO-27",
                "--runner-id",
                "runner-a",
            )
            status = self._run(
                temp_dir,
                "operator",
                "status",
                "--issue-key",
                "PRO-27",
                "--runner-id",
                "runner-a",
            )

            self.assertTrue(recorded["ok"])
            self.assertEqual(recorded["record"]["result"], "passed")
            self.assertEqual(listing["validation"][0]["surface"], "cli")
            self.assertEqual(status["validation"]["summary"], "manual operator validation")

    def test_operator_service_can_apply_linear_issue_crud(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage, repository, transport = _operator_fixture(temp_dir)
            client = FakeLinearCrudClient()
            service = OperatorService(
                storage=storage,
                repository=repository,
                transport=transport,
                linear_client=client,
            )

            created = service.linear_issue_create_payload(
                team_id="team-1",
                title="CRUD validation",
                description="create",
            )
            fetched = service.linear_issue_get_payload(issue_ref="PRO-501")
            updated = service.linear_issue_update_payload(
                issue_ref="PRO-501",
                title="CRUD validation updated",
            )
            archived = service.linear_issue_archive_payload(issue_ref="PRO-501", trash=True)
            deleted = service.linear_issue_delete_payload(issue_ref="PRO-501")

            self.assertTrue(created["ok"])
            self.assertEqual(created["issue"]["title"], "CRUD validation")
            self.assertEqual(fetched["issue"]["identifier"], "PRO-501")
            self.assertEqual(updated["issue"]["title"], "CRUD validation updated")
            self.assertEqual(archived["issue"]["identifier"], "PRO-501")
            self.assertEqual(deleted["issue"]["identifier"], "PRO-501")
            self.assertEqual(
                client.calls,
                [
                    "create",
                    "get",
                    "update",
                    "archive",
                    "delete",
                ],
            )

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


def _operator_fixture(temp_dir: str) -> tuple[Storage, OrxRepository, FakeTmuxTransport]:
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
    request = executor.dispatch_slice(
        issue_key="PRO-27",
        runner_id="runner-a",
        objective="Inspect tmux-backed runner state",
        slice_goal="Expose operator CLI state",
        acceptance=["operator state visible"],
        validation_plan=["read operator cli json"],
    )
    executor.repository.enqueue_command(
        "pause",
        issue_key="PRO-27",
        runner_id="runner-a",
        payload={"source": "operator-test"},
        priority=60,
    )
    stale_timestamp = (datetime.now(UTC) - timedelta(minutes=5)).isoformat(timespec="seconds")
    with storage.session() as connection:
        connection.execute(
            """
            UPDATE continuity_state
            SET updated_at = ?
            WHERE issue_key = ? AND runner_id = ?
            """,
            (stale_timestamp, "PRO-27", "runner-a"),
        )
    DaemonStateService(storage).record_last_tick(
        {
            "home": temp_dir,
            "db_path": str(storage.paths.db_path),
            "schema_version": storage.current_version(),
            "tick": "idle",
            "proposal_materialization": {
                "status": "idle",
                "eligible": 0,
                "materialized": 0,
                "idempotent": 0,
                "failed": 0,
                "disabled_reason": None,
                "errors": [],
            },
        }
    )
    return storage, repository, transport


class FakeLinearCrudClient:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.issue = LinearIssue(
            linear_id="issue-501",
            identifier="PRO-501",
            title="CRUD validation",
            description="create",
            url="https://linear.example/PRO-501",
            team_id="team-1",
            team_name="Projects",
            state_id="state-1",
            state_name="Backlog",
            state_type="unstarted",
            parent_id=None,
            parent_identifier=None,
            project_id=None,
            project_name=None,
        )

    def get_issue(self, *, issue_ref: str) -> LinearIssue | None:
        self.calls.append("get")
        return self.issue if issue_ref in {"PRO-501", "issue-501"} else None

    def create_issue(
        self,
        *,
        team_id: str,
        title: str,
        description: str,
        parent_id: str | None = None,
        project_id: str | None = None,
    ) -> LinearIssue:
        self.calls.append("create")
        self.issue = LinearIssue(
            linear_id="issue-501",
            identifier="PRO-501",
            title=title,
            description=description,
            url="https://linear.example/PRO-501",
            team_id=team_id,
            team_name="Projects",
            state_id="state-1",
            state_name="Backlog",
            state_type="unstarted",
            parent_id=parent_id,
            parent_identifier=None,
            project_id=project_id,
            project_name=None,
        )
        return self.issue

    def update_issue(
        self,
        *,
        issue_ref: str,
        title: str | None = None,
        description: str | None = None,
        state_id: str | None = None,
    ) -> LinearIssue:
        self.calls.append("update")
        self.issue = LinearIssue(
            linear_id=self.issue.linear_id,
            identifier=self.issue.identifier,
            title=title or self.issue.title,
            description=description or self.issue.description,
            url=self.issue.url,
            team_id=self.issue.team_id,
            team_name=self.issue.team_name,
            state_id=state_id or self.issue.state_id,
            state_name=self.issue.state_name,
            state_type=self.issue.state_type,
            parent_id=self.issue.parent_id,
            parent_identifier=self.issue.parent_identifier,
            project_id=self.issue.project_id,
            project_name=self.issue.project_name,
        )
        return self.issue

    def archive_issue(self, *, issue_ref: str, trash: bool = False) -> LinearIssue:
        self.calls.append("archive")
        return self.issue

    def delete_issue(self, *, issue_ref: str) -> LinearIssue:
        self.calls.append("delete")
        return self.issue


if __name__ == "__main__":
    unittest.main()
