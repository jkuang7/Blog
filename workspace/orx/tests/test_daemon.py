from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from orx.config import resolve_runtime_paths
from orx.daemon import OrxDaemon
from orx.dispatch import GlobalDispatchService
from orx.linear_client import LinearClientError, LinearCreatedIssue
from orx.mirror import LinearMirrorRepository
from orx.registry import ProjectRegistry
from orx.proposal_materialization import ProposalMaterializationService
from orx.runtime_state import DaemonStateService
from orx.storage import Storage

from tests.test_executor import FakeTmuxTransport
from tests.test_proposal_materialization import _materialization_fixture


class DaemonTests(unittest.TestCase):
    def test_run_once_materializes_open_leaf_ticket_proposals(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage, proposals, mirror, client = _materialization_fixture(temp_dir)
            proposal = proposals.route(
                "PRO-22",
                "runner-a",
                improvement_title="Automate proposal ticket handoff",
                context={"suggested_phase_issue_key": "PRO-10"},
            )
            daemon = OrxDaemon(
                paths=resolve_runtime_paths(temp_dir),
                storage=storage,
                materializer=ProposalMaterializationService(
                    storage,
                    proposals=proposals,
                    mirror=mirror,
                    client=client,
                ),
            )

            snapshot = daemon.run_once()
            updated = proposals.get_proposal(proposal_id=proposal.proposal_id)

            self.assertEqual(snapshot.tick, "materialized")
            self.assertEqual(snapshot.proposal_materialization["status"], "ok")
            self.assertEqual(snapshot.proposal_materialization["materialized"], 1)
            self.assertEqual(updated.status if updated is not None else None, "materialized")
            persisted = DaemonStateService(storage).get_last_tick()
            self.assertIsNotNone(persisted)
            self.assertEqual(persisted.value["tick"], "materialized")
            self.assertEqual(persisted.value["proposal_materialization"]["materialized"], 1)

    def test_run_once_degrades_cleanly_when_linear_client_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage, proposals, mirror, _ = _materialization_fixture(temp_dir)
            proposals.route(
                "PRO-22",
                "runner-a",
                improvement_title="Automate proposal ticket handoff",
                context={"suggested_phase_issue_key": "PRO-10"},
            )
            with patch.dict(
                "os.environ",
                {
                    "ORX_ENV_DISABLE": "1",
                    "ORX_LINEAR_API_KEY": "",
                    "LINEAR_API_KEY": "",
                },
                clear=False,
            ):
                daemon = OrxDaemon(
                    paths=resolve_runtime_paths(temp_dir),
                    storage=storage,
                    materializer=ProposalMaterializationService(
                        storage,
                        proposals=proposals,
                        mirror=mirror,
                    ),
                )
                snapshot = daemon.run_once()

            self.assertEqual(snapshot.tick, "degraded")
            self.assertEqual(snapshot.proposal_materialization["status"], "disabled")
            self.assertEqual(snapshot.proposal_materialization["eligible"], 1)
            self.assertIn("ORX_LINEAR_API_KEY", snapshot.proposal_materialization["disabled_reason"])

    def test_run_once_continues_after_materialization_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage, proposals, mirror, _ = _materialization_fixture(temp_dir)
            proposal = proposals.route(
                "PRO-22",
                "runner-a",
                improvement_title="Automate proposal ticket handoff",
                context={"suggested_phase_issue_key": "PRO-10"},
            )
            daemon = OrxDaemon(
                paths=resolve_runtime_paths(temp_dir),
                storage=storage,
                materializer=ProposalMaterializationService(
                    storage,
                    proposals=proposals,
                    mirror=mirror,
                    client=FailingLinearClient(),
                ),
            )

            snapshot = daemon.run_once()

            self.assertEqual(snapshot.tick, "warning")
            self.assertEqual(snapshot.proposal_materialization["status"], "partial")
            self.assertEqual(snapshot.proposal_materialization["failed"], 1)
            self.assertEqual(
                snapshot.proposal_materialization["errors"][0]["proposal_key"],
                proposal.proposal_key,
            )

    def test_run_once_drains_idle_registered_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(resolve_runtime_paths(temp_dir))
            storage.bootstrap()
            registry = ProjectRegistry(storage)
            transport = FakeTmuxTransport()
            dispatch = GlobalDispatchService(
                storage=storage,
                registry=registry,
                transport_factory=lambda: transport,
            )
            mirror = LinearMirrorRepository(storage)
            dispatch.register_project(
                project_key="alpha",
                display_name="Alpha",
                repo_root="/tmp/alpha",
                owning_bot="alpha_bot",
            )
            mirror.upsert_issue(
                linear_id="lin-alpha-1",
                identifier="PRO-710",
                title="Drain this issue",
                description="Run daemon drain",
                team_id="team-1",
                team_name="Projects",
                state_name="Todo",
                state_type="unstarted",
                priority=1,
                project_id="project-alpha",
                project_name="Alpha",
                source_updated_at="2026-04-16T12:10:00+00:00",
                metadata={"project_key": "alpha"},
            )
            daemon = OrxDaemon(
                paths=resolve_runtime_paths(temp_dir),
                storage=storage,
                dispatch=dispatch,
            )

            snapshot = daemon.run_once()

            self.assertEqual(snapshot.tick, "drained")
            self.assertEqual(snapshot.drained_projects[0]["project_key"], "alpha")
            self.assertEqual(snapshot.drained_projects[0]["issue_key"], "PRO-710")
            self.assertEqual(snapshot.drained_projects[0]["action"], "started")
            self.assertEqual(snapshot.drifted_projects, [])
            persisted = DaemonStateService(storage).get_last_tick()
            self.assertIsNotNone(persisted)
            self.assertEqual(persisted.value["tick"], "drained")

    def test_run_once_marks_tick_degraded_when_project_is_skipped_for_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(resolve_runtime_paths(temp_dir))
            storage.bootstrap()
            registry = ProjectRegistry(storage)
            dispatch = GlobalDispatchService(
                storage=storage,
                registry=registry,
                transport_factory=FakeTmuxTransport,
            )
            mirror = LinearMirrorRepository(storage)
            repo_root = Path(temp_dir) / "alpha-repo"
            repo_root.mkdir(parents=True, exist_ok=True)
            dispatch.register_project(
                project_key="alpha",
                display_name="Alpha",
                repo_root=str(repo_root),
                owning_bot="alpha_bot",
                owner_chat_id=101,
                owner_thread_id=202,
            )
            mirror.upsert_issue(
                linear_id="lin-alpha-2",
                identifier="PRO-711",
                title="Degrade on drift",
                description="Run daemon once after drift",
                team_id="team-1",
                team_name="Projects",
                state_name="Todo",
                state_type="unstarted",
                priority=1,
                project_id="project-alpha",
                project_name="Alpha",
                source_updated_at="2026-04-16T12:20:00+00:00",
                metadata={"project_key": "alpha"},
            )
            dispatch.dispatch_run(ingress_bot="alpha_bot")
            runtime = dispatch._runtime_service(registry.get_project("alpha"))  # type: ignore[arg-type]
            with runtime.storage.session() as connection:
                connection.execute(
                    "UPDATE continuity_state SET resume_context_json = ? WHERE issue_key = ? AND runner_id = ?",
                    ('{\"project_key\":\"beta\"}', "PRO-711", "main"),
                )

            daemon = OrxDaemon(
                paths=resolve_runtime_paths(temp_dir),
                storage=storage,
                dispatch=dispatch,
            )

            snapshot = daemon.run_once()

            self.assertEqual(snapshot.tick, "degraded")
            self.assertEqual(snapshot.drained_projects, [])
            self.assertEqual(snapshot.drifted_projects[0]["project_key"], "alpha")
            self.assertTrue(
                any(
                    "resume_context project_key" in blocker
                    for blocker in snapshot.drifted_projects[0]["blockers"]
                )
            )


class FailingLinearClient:
    def create_issue(
        self,
        *,
        team_id: str,
        title: str,
        description: str,
        parent_id: str | None = None,
        project_id: str | None = None,
    ) -> LinearCreatedIssue:
        raise LinearClientError("Synthetic Linear failure for daemon regression test.")


if __name__ == "__main__":
    unittest.main()
