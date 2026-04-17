from __future__ import annotations

import http.client
import json
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from orx.api import OrxApiServer, OrxApiService
from orx.config import resolve_project_runtime_paths, resolve_runtime_paths
from orx.continuity import ContinuityService
from orx.executor import ExecutorService
from orx.linear_client import LinearIssue
from orx.mirror import LinearMirrorRepository
from orx.ownership import OwnershipService
from orx.proposal_materialization import ProposalMaterializationService
from orx.proposals import ProposalService
from orx.repository import OrxRepository
from orx.runtime_state import DaemonStateService
from orx.storage import CURRENT_SCHEMA_VERSION, Storage

from tests.test_executor import FakeTmuxTransport
from tests.test_proposal_materialization import FakeLinearClient


class ApiContractTests(unittest.TestCase):
    def test_cli_api_serve_with_max_requests_returns_http_response(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                host, port = listener.getsockname()

            process = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).resolve().parents[1] / "bin" / "orx"),
                    "--json",
                    "--home",
                    temp_dir,
                    "api",
                    "serve",
                    "--host",
                    str(host),
                    "--port",
                    str(port),
                    "--max-requests",
                    "1",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                payload = _request_http_with_retry(
                    str(host),
                    int(port),
                    "GET",
                    "/health",
                )

                stdout, stderr = process.communicate(timeout=5)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=5)

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["schema_version"], CURRENT_SCHEMA_VERSION)
            self.assertEqual(process.returncode, 0, stderr)
            result = json.loads(stdout)
            self.assertEqual(result["stopped"], "max-requests")

    def test_health_and_status_endpoints_return_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            server = _api_server_fixture(temp_dir)
            response = _request(server, "GET", "/health")
            status = _request(server, "GET", "/status?issue_key=PRO-24&runner_id=runner-a")

            self.assertEqual(response["schema_version"], CURRENT_SCHEMA_VERSION)
            self.assertTrue(response["ok"])
            self.assertEqual(response["daemon"]["tick"], "idle")
            self.assertEqual(status["continuity"]["issue_key"], "PRO-24")
            self.assertEqual(len(status["queue"]), 0)
            self.assertEqual(status["runners"][0]["runner_id"], "runner-a")
            self.assertEqual(status["daemon"]["tick"], "idle")

            _stop_server(server)

    def test_registry_project_delete_removes_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            server = _api_server_fixture(temp_dir)
            try:
                created = _request(
                    server,
                    "POST",
                    "/registry/projects",
                    {
                        "project_key": "alpha",
                        "display_name": "Alpha",
                        "repo_root": "/tmp/alpha",
                        "owning_bot": "alpha_bot",
                    },
                )
                deleted = _request(
                    server,
                    "DELETE",
                    "/registry/projects?project_key=alpha",
                )
                dashboard = _request(server, "GET", "/dashboard")
            finally:
                _stop_server(server)

            self.assertTrue(created["ok"])
            self.assertTrue(deleted["ok"])
            self.assertEqual(deleted["project"]["project_key"], "alpha")
            self.assertEqual(dashboard["projects"], [])

    def test_daemon_endpoint_returns_persisted_degraded_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            server = _api_server_fixture(
                temp_dir,
                daemon_state={
                    "home": temp_dir,
                    "db_path": f"{temp_dir}/orx.sqlite3",
                    "schema_version": CURRENT_SCHEMA_VERSION,
                    "tick": "degraded",
                    "proposal_materialization": {
                        "status": "disabled",
                        "eligible": 1,
                        "materialized": 0,
                        "idempotent": 0,
                        "failed": 0,
                        "disabled_reason": "Set ORX_LINEAR_API_KEY or LINEAR_API_KEY to materialize proposals into Linear.",
                        "errors": [],
                    },
                },
            )
            payload = _request(server, "GET", "/daemon")

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["daemon"]["tick"], "degraded")
            self.assertEqual(payload["daemon"]["proposal_materialization"]["status"], "disabled")

            _stop_server(server)

    def test_post_commands_enqueues_normalized_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            server = _api_server_fixture(temp_dir)
            payload = _request(
                server,
                "POST",
                "/commands",
                {
                    "command_kind": "pause",
                    "issue_key": "PRO-24",
                    "runner_id": "runner-a",
                    "payload": {"source": "telegram"},
                },
            )
            status = _request(server, "GET", "/status?issue_key=PRO-24&runner_id=runner-a")

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["command"]["command_kind"], "pause")
            self.assertEqual(payload["command"]["status"], "pending")

    def test_slice_results_finalize_issue_and_mark_it_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            client = FakeLinearCrudClient()
            server = _api_server_fixture(temp_dir, linear_client=client)
            storage = Storage(resolve_runtime_paths(temp_dir))
            mirror = LinearMirrorRepository(storage)
            try:
                registered = _request(
                    server,
                    "POST",
                    "/registry/projects",
                    {
                        "project_key": "alpha",
                        "display_name": "Alpha",
                        "repo_root": "/tmp/alpha",
                        "owning_bot": "alpha_bot",
                    },
                )
                self.assertTrue(registered["ok"])
                mirror.upsert_issue(
                    linear_id="issue-501",
                    identifier="PRO-501",
                    title="API CRUD validation",
                    description="created over http",
                    team_id="team-1",
                    team_name="Projects",
                    state_id="state-1",
                    state_name="Backlog",
                    state_type="unstarted",
                    project_id="project-alpha",
                    project_name="Alpha",
                    source_updated_at="2026-04-16T12:20:00+00:00",
                    metadata={"project_key": "alpha"},
                )
                dispatch = _request(
                    server,
                    "POST",
                    "/dispatch/run",
                    {
                        "ingress_bot": "alpha_bot",
                    },
                )
                self.assertTrue(dispatch["ok"])
                runtime_storage = Storage(
                    resolve_project_runtime_paths("alpha", home=resolve_runtime_paths(temp_dir).home)
                )
                active = ContinuityService(runtime_storage).get_state("PRO-501", "main")
                self.assertIsNotNone(active)
                payload = _request(
                    server,
                    "POST",
                    "/slice-results",
                    {
                        "project_key": "alpha",
                        "slice_id": active.active_slice_id,
                        "payload": {
                            "status": "success",
                            "summary": "HTTP finalize works",
                            "verified": True,
                            "next_slice": None,
                            "artifacts": ["tests/test_api.py"],
                            "metrics": {"source": "api"},
                        },
                    },
                )
                status = _request(server, "GET", "/control/status?project_key=alpha")
            finally:
                _stop_server(server)

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["result"]["apply_status"], "applied")
            self.assertTrue(payload["result"]["finalized"])
            self.assertTrue(payload["result"]["linear_completed"])
            self.assertIsNone(status["active_issue_key"])
            completed = mirror.get_issue(identifier="PRO-501")
            self.assertIsNotNone(completed)
            self.assertEqual(completed.state_type, "completed")
            self.assertIsNotNone(completed.completed_at)
            self.assertIn("complete", client.calls)

    def test_proposals_endpoint_returns_durable_open_proposals(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            server = _api_server_fixture(temp_dir)
            payload = _request(server, "GET", "/proposals?issue_key=PRO-24")

            self.assertEqual(len(payload["proposals"]), 1)
            self.assertEqual(payload["proposals"][0]["proposal_kind"], "improvement-issue")
            self.assertEqual(payload["proposals"][0]["decomposition_class"], "improvement_issue")
            self.assertEqual(payload["proposals"][0]["workflow_mode"], "leaf-ticket")
            self.assertEqual(payload["proposals"][0]["suggested_parent_issue_key"], "PRO-24")

            _stop_server(server)

    def test_post_materialize_proposal_creates_linear_issue(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            client = FakeLinearClient()
            server = _api_server_fixture(temp_dir, linear_client=client)

            proposals = _request(server, "GET", "/proposals?issue_key=PRO-24")
            proposal_id = proposals["proposals"][0]["proposal_id"]
            payload = _request(
                server,
                "POST",
                "/proposals/materialize",
                {"proposal_id": proposal_id},
            )
            open_after = _request(server, "GET", "/proposals?issue_key=PRO-24")

            self.assertTrue(payload["ok"])
            self.assertFalse(payload["idempotent"])
            self.assertEqual(payload["created_issue"]["identifier"], "PRO-90")
            self.assertEqual(payload["proposal"]["status"], "materialized")
            self.assertEqual(open_after["proposals"], [])
            self.assertEqual(len(client.calls), 1)

            _stop_server(server)

    def test_validation_endpoint_records_and_lists_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            server = _api_server_fixture(temp_dir)

            payload = _request(
                server,
                "POST",
                "/validation",
                {
                    "issue_key": "PRO-24",
                    "runner_id": "runner-a",
                    "surface": "api",
                    "tool": "http",
                    "result": "passed",
                    "confidence": "confirmed",
                    "summary": "manual api validation",
                    "details": {"path": "/validation"},
                    "blockers": [],
                },
            )
            status = _request(server, "GET", "/status?issue_key=PRO-24&runner_id=runner-a")
            listing = _request(server, "GET", "/validation?issue_key=PRO-24&runner_id=runner-a")

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["record"]["surface"], "api")
            self.assertEqual(status["validation"]["summary"], "manual api validation")
            self.assertEqual(listing["validation"][0]["tool"], "http")

            _stop_server(server)

    def test_linear_issue_endpoints_apply_crud(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            client = FakeLinearCrudClient()
            server = _api_server_fixture(temp_dir, linear_client=client)
            storage = Storage(resolve_runtime_paths(temp_dir))
            mirror = LinearMirrorRepository(storage)

            created = _request(
                server,
                "POST",
                "/linear/issues",
                {
                    "team_id": "team-1",
                    "title": "API CRUD validation",
                    "description": "created over http",
                },
            )
            fetched = _request(server, "GET", "/linear/issues?issue=PRO-501")
            updated = _request(
                server,
                "PATCH",
                "/linear/issues",
                {
                    "issue": "PRO-501",
                    "title": "API CRUD validation updated",
                },
            )
            mirror.upsert_issue(
                linear_id=created["issue"]["linear_id"],
                identifier=created["issue"]["identifier"],
                title=updated["issue"]["title"],
                description=created["issue"]["description"],
                team_id="team-1",
                team_name="Projects",
                state_id=updated["issue"]["state_id"],
                state_name=updated["issue"]["state_name"],
                state_type=updated["issue"]["state_type"],
                project_id=updated["issue"]["project_id"],
                project_name=updated["issue"]["project_name"],
                parent_linear_id=updated["issue"]["parent_id"],
                parent_identifier=updated["issue"]["parent_identifier"],
                assignee_id=None,
                assignee_name=None,
                labels=[],
                metadata={},
                source_updated_at="2026-04-17T03:00:00+00:00",
                created_at="2026-04-17T03:00:00+00:00",
                completed_at=None,
                canceled_at=None,
            )
            archived = _request(
                server,
                "POST",
                "/linear/issues/archive",
                {"issue": "PRO-501", "trash": True},
            )
            deleted = _request(server, "DELETE", "/linear/issues?issue=PRO-501")
            missing = _request(server, "GET", "/linear/issues?issue=PRO-501")

            self.assertTrue(created["ok"])
            self.assertEqual(created["issue"]["title"], "API CRUD validation")
            self.assertEqual(fetched["issue"]["identifier"], "PRO-501")
            self.assertEqual(updated["issue"]["title"], "API CRUD validation updated")
            self.assertEqual(archived["issue"]["identifier"], "PRO-501")
            self.assertIsNone(mirror.get_issue(identifier="PRO-501"))
            self.assertEqual(deleted["issue"]["identifier"], "PRO-501")
            self.assertFalse(missing["ok"])
            self.assertIsNone(missing["issue"])
            self.assertEqual(
                client.calls,
                ["create", "get", "update", "archive", "delete", "get"],
            )

            _stop_server(server)

    def test_dispatch_and_control_endpoints_cover_registry_handoff_and_notifications(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            server = _api_server_fixture(temp_dir)

            _request(
                server,
                "POST",
                "/registry/projects",
                {
                    "project_key": "alpha",
                    "display_name": "Alpha",
                    "repo_root": "/tmp/alpha",
                    "owning_bot": "alpha_bot",
                },
            )
            _request(
                server,
                "POST",
                "/registry/projects",
                {
                    "project_key": "orx",
                    "display_name": "ORX",
                    "repo_root": "/tmp/orx",
                    "owning_bot": "orx_bot",
                    "metadata": {"linear_team_id": "team-orx", "linear_project_id": "project-orx"},
                },
            )
            _request(
                server,
                "POST",
                "/registry/projects",
                {
                    "project_key": "beta",
                    "display_name": "Beta",
                    "repo_root": "/tmp/beta",
                    "owning_bot": "beta_bot",
                    "metadata": {"linear_team_id": "team-beta", "linear_project_id": "project-beta"},
                },
            )
            dispatched = _request(
                server,
                "POST",
                "/dispatch/run",
                {
                    "ingress_bot": "alpha_bot",
                },
            )
            queue = _request(server, "GET", "/control/queue?project_key=orx")
            pause = _request(
                server,
                "POST",
                "/control/pause",
                {"project_key": "orx", "payload": {"source": "api-test"}},
            )
            notifications = _request(
                server,
                "GET",
                "/notifications?bot=orx_bot",
            )
            ack = _request(
                server,
                "POST",
                "/notifications/ack",
                {"notification_ids": [notifications["notifications"][0]["notification_id"]]},
            )
            dashboard = _request(server, "GET", "/dashboard")

            self.assertEqual(dispatched["dispatch"]["project_key"], "orx")
            self.assertTrue(dispatched["dispatch"]["handoff_required"])
            self.assertEqual(queue["active_issue_key"], "PRO-24")
            self.assertEqual(pause["command"]["command_kind"], "pause")
            self.assertEqual(len(notifications["notifications"]), 1)
            self.assertEqual(ack["acknowledged"], [notifications["notifications"][0]["notification_id"]])
            self.assertEqual(len(dashboard["projects"]), 3)
            orx_entry = next(
                project for project in dashboard["projects"] if project["project"]["project_key"] == "orx"
            )
            self.assertEqual(orx_entry["health_state"], "busy")
            self.assertTrue(orx_entry["drift"]["ok"])
            self.assertEqual(orx_entry["project"]["execution_thread_id"], orx_entry["project"]["owner_thread_id"])

            _stop_server(server)

    def test_control_context_endpoint_returns_restart_safe_project_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            server = _api_server_fixture(temp_dir)
            storage = Storage(resolve_runtime_paths(temp_dir))
            mirror = LinearMirrorRepository(storage)
            try:
                _request(
                    server,
                    "POST",
                    "/registry/projects",
                    {
                        "project_key": "alpha",
                        "display_name": "Alpha",
                        "repo_root": "/tmp/alpha",
                        "owning_bot": "alpha_bot",
                        "owner_chat_id": 101,
                        "owner_thread_id": 202,
                        "metadata": {"linear_project_id": "project-alpha"},
                    },
                )
                mirror.upsert_issue(
                    linear_id="issue-710",
                    identifier="PRO-710",
                    title="Crash continuity",
                    description="Persist enough to resume after a crash",
                    team_id="team-1",
                    team_name="Projects",
                    state_id="state-1",
                    state_name="Todo",
                    state_type="unstarted",
                    project_id="project-alpha",
                    project_name="Alpha",
                    source_updated_at="2026-04-16T13:05:00+00:00",
                    metadata={"project_key": "alpha"},
                )
                _request(
                    server,
                    "POST",
                    "/dispatch/run",
                    {
                        "ingress_bot": "alpha_bot",
                        "project_key": "alpha",
                    },
                )
                payload = _request(server, "GET", "/control/context?project_key=alpha")
            finally:
                _stop_server(server)

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["context"]["project"]["project_key"], "alpha")
            self.assertEqual(payload["context"]["runtime"]["active_issue_key"], "PRO-710")
            self.assertEqual(payload["context"]["issue"]["identifier"], "PRO-710")
            self.assertEqual(payload["context"]["continuity"]["issue_key"], "PRO-710")
            self.assertEqual(payload["context"]["continuity"]["resume_context"]["project_key"], "alpha")
            self.assertEqual(
                payload["context"]["active_slice_request"]["request"]["context"]["project_key"],
                "alpha",
            )
            self.assertEqual(payload["context"]["execution_packet"]["issue_key"], "PRO-710")
            self.assertEqual(payload["context"]["execution_packet"]["execution_reasoning_effort"], "medium")
            self.assertEqual(payload["context"]["recovery"]["action"], "resume")
            self.assertTrue(payload["context"]["drift"]["ok"])
            self.assertEqual(payload["context"]["project"]["execution_thread_id"], 202)

    def test_slice_results_rewrite_latest_handoff_and_create_follow_up_issue(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            client = FakeLinearCrudClient()
            server = _api_server_fixture(temp_dir, linear_client=client)
            storage = Storage(resolve_runtime_paths(temp_dir))
            mirror = LinearMirrorRepository(storage)
            try:
                registered = _request(
                    server,
                    "POST",
                    "/registry/projects",
                    {
                        "project_key": "alpha",
                        "display_name": "Alpha",
                        "repo_root": "/tmp/alpha",
                        "owning_bot": "alpha_bot",
                    },
                )
                self.assertTrue(registered["ok"])
                mirror.upsert_issue(
                    linear_id="issue-710",
                    identifier="PRO-710",
                    title="Keep handoff current",
                    description="## Goal\nKeep the ticket current.\n",
                    team_id="team-1",
                    team_name="Projects",
                    state_id="state-1",
                    state_name="Todo",
                    state_type="unstarted",
                    project_id="project-alpha",
                    project_name="Alpha",
                    source_updated_at="2026-04-16T13:05:00+00:00",
                    metadata={"project_key": "alpha"},
                )
                linear_issue = LinearIssue(
                    linear_id="issue-710",
                    identifier="PRO-710",
                    title="Keep handoff current",
                    description="## Goal\nKeep the ticket current.\n",
                    url="https://linear.example/PRO-710",
                    team_id="team-1",
                    team_name="Projects",
                    state_id="state-1",
                    state_name="Todo",
                    state_type="unstarted",
                    parent_id=None,
                    parent_identifier=None,
                    project_id="project-alpha",
                    project_name="Alpha",
                )
                client.issues_by_ref[linear_issue.identifier] = linear_issue
                client.issues_by_ref[linear_issue.linear_id] = linear_issue
                _request(
                    server,
                    "POST",
                    "/dispatch/run",
                    {
                        "ingress_bot": "alpha_bot",
                        "project_key": "alpha",
                    },
                )
                runtime_storage = Storage(
                    resolve_project_runtime_paths("alpha", home=resolve_runtime_paths(temp_dir).home)
                )
                active = ContinuityService(runtime_storage).get_state("PRO-710", "main")
                self.assertIsNotNone(active)
                payload = _request(
                    server,
                    "POST",
                    "/slice-results",
                    {
                        "project_key": "alpha",
                        "slice_id": active.active_slice_id,
                        "payload": {
                            "status": "blocked",
                            "summary": "Work is blocked by missing ownership in another repo.",
                            "verified": False,
                            "next_slice": "Wait for the owner repo fix before retrying.",
                            "artifacts": ["src/dispatch.py"],
                            "metrics": {"source": "api"},
                            "blockers": ["The owner repo is not in scope for this checkout."],
                            "lessons": ["Do not keep retrying the wrong repo."],
                            "follow_ups": [
                                {
                                    "title": "Create owner-repo prerequisite for PRO-710",
                                    "why": "The missing owner work must land before this issue can proceed.",
                                    "goal": "Get the owner repo into a runnable state for PRO-710.",
                                    "scope_in": ["Prepare the prerequisite owner-repo change."],
                                    "acceptance": ["Owner repo prerequisite exists and is linked back to PRO-710."],
                                }
                            ],
                        },
                    },
                )
            finally:
                _stop_server(server)

            self.assertTrue(payload["ok"])
            mirrored = mirror.get_issue(identifier="PRO-710")
            self.assertIsNotNone(mirrored)
            self.assertIn("## Latest Handoff", mirrored.description)
            self.assertIn("The owner repo is not in scope for this checkout.", mirrored.description)
            self.assertIn("Do not keep retrying the wrong repo.", mirrored.description)
            self.assertIn("Create owner-repo prerequisite for PRO-710", mirrored.description)
            self.assertIn("create", client.calls)

    def test_slice_results_interpret_owner_mismatch_into_reroute_and_synthesized_follow_up(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            client = FakeLinearCrudClient()
            server = _api_server_fixture(temp_dir, linear_client=client)
            storage = Storage(resolve_runtime_paths(temp_dir))
            mirror = LinearMirrorRepository(storage)
            try:
                _request(
                    server,
                    "POST",
                    "/registry/projects",
                    {
                        "project_key": "alpha",
                        "display_name": "Alpha",
                        "repo_root": "/tmp/alpha",
                        "owning_bot": "alpha_bot",
                    },
                )
                mirror.upsert_issue(
                    linear_id="issue-711",
                    identifier="PRO-711",
                    title="Route owner work",
                    description="## Goal\nRoute owner work correctly.\n",
                    team_id="team-1",
                    team_name="Projects",
                    state_id="state-1",
                    state_name="Todo",
                    state_type="unstarted",
                    project_id="project-alpha",
                    project_name="Alpha",
                    source_updated_at="2026-04-16T13:05:00+00:00",
                    metadata={"project_key": "alpha"},
                )
                linear_issue = LinearIssue(
                    linear_id="issue-711",
                    identifier="PRO-711",
                    title="Route owner work",
                    description="## Goal\nRoute owner work correctly.\n",
                    url="https://linear.example/PRO-711",
                    team_id="team-1",
                    team_name="Projects",
                    state_id="state-1",
                    state_name="Todo",
                    state_type="unstarted",
                    parent_id=None,
                    parent_identifier=None,
                    project_id="project-alpha",
                    project_name="Alpha",
                )
                client.issues_by_ref[linear_issue.identifier] = linear_issue
                client.issues_by_ref[linear_issue.linear_id] = linear_issue
                _request(
                    server,
                    "POST",
                    "/dispatch/run",
                    {
                        "ingress_bot": "alpha_bot",
                        "project_key": "alpha",
                    },
                )
                runtime_storage = Storage(
                    resolve_project_runtime_paths("alpha", home=resolve_runtime_paths(temp_dir).home)
                )
                active = ContinuityService(runtime_storage).get_state("PRO-711", "main")
                self.assertIsNotNone(active)
                payload = _request(
                    server,
                    "POST",
                    "/slice-results",
                    {
                        "project_key": "alpha",
                        "slice_id": active.active_slice_id,
                        "payload": {
                            "status": "blocked",
                            "summary": "This checkout cannot land the owning repo change.",
                            "verified": False,
                            "next_slice": "Keep trying the same repo",
                            "artifacts": ["src/runtime.py"],
                            "metrics": {"source": "api"},
                            "owner_mismatch": "The required implementation owner lives in another repo.",
                            "lessons": ["Do not keep retrying the current repo when ownership is wrong."],
                        },
                    },
                )
                continuity = ContinuityService(runtime_storage).get_state("PRO-711", "main")
                context = _request(server, "GET", "/control/context?project_key=alpha")
            finally:
                _stop_server(server)

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["result"]["status"], "reroute")
            self.assertIsNone(payload["result"]["next_slice"])
            self.assertIsNotNone(continuity)
            self.assertIsNone(continuity.next_slice)
            self.assertEqual(continuity.resume_context["interpreted_action"], "reroute")
            self.assertEqual(continuity.resume_context["execution_reasoning_effort"], "high")
            self.assertIn("create", client.calls)
            mirrored = mirror.get_issue(identifier="PRO-711")
            self.assertIsNotNone(mirrored)
            self.assertIn("Ownership mismatch", mirrored.description)
            self.assertEqual(context["context"]["execution_packet"]["interpreted_action"], "reroute")
            self.assertEqual(context["context"]["execution_packet"]["execution_reasoning_effort"], "high")
            self.assertEqual(
                context["context"]["execution_packet"]["execution_escalation_trigger"],
                "owner_mismatch",
            )

    def test_replayed_follow_up_blocker_does_not_create_duplicate_child_issue(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            client = FakeLinearCrudClient()
            server = _api_server_fixture(temp_dir, linear_client=client)
            storage = Storage(resolve_runtime_paths(temp_dir))
            mirror = LinearMirrorRepository(storage)
            try:
                _request(
                    server,
                    "POST",
                    "/registry/projects",
                    {
                        "project_key": "alpha",
                        "display_name": "Alpha",
                        "repo_root": "/tmp/alpha",
                        "owning_bot": "alpha_bot",
                    },
                )
                mirror.upsert_issue(
                    linear_id="issue-712",
                    identifier="PRO-712",
                    title="Replay blocker safely",
                    description="## Goal\nReplay blocker safely.\n",
                    team_id="team-1",
                    team_name="Projects",
                    state_id="state-1",
                    state_name="Todo",
                    state_type="unstarted",
                    project_id="project-alpha",
                    project_name="Alpha",
                    source_updated_at="2026-04-16T13:05:00+00:00",
                    metadata={"project_key": "alpha"},
                )
                linear_issue = LinearIssue(
                    linear_id="issue-712",
                    identifier="PRO-712",
                    title="Replay blocker safely",
                    description="## Goal\nReplay blocker safely.\n",
                    url="https://linear.example/PRO-712",
                    team_id="team-1",
                    team_name="Projects",
                    state_id="state-1",
                    state_name="Todo",
                    state_type="unstarted",
                    parent_id=None,
                    parent_identifier=None,
                    project_id="project-alpha",
                    project_name="Alpha",
                )
                client.issues_by_ref[linear_issue.identifier] = linear_issue
                client.issues_by_ref[linear_issue.linear_id] = linear_issue
                _request(
                    server,
                    "POST",
                    "/dispatch/run",
                    {"ingress_bot": "alpha_bot", "project_key": "alpha"},
                )
                runtime_storage = Storage(
                    resolve_project_runtime_paths("alpha", home=resolve_runtime_paths(temp_dir).home)
                )
                active = ContinuityService(runtime_storage).get_state("PRO-712", "main")
                self.assertIsNotNone(active)
                request_body = {
                    "project_key": "alpha",
                    "slice_id": active.active_slice_id,
                    "payload": {
                        "status": "blocked",
                        "summary": "Replayable blocker",
                        "verified": False,
                        "next_slice": None,
                        "artifacts": ["src/runtime.py"],
                        "metrics": {"source": "api"},
                        "owner_mismatch": "The required implementation owner lives in another repo.",
                    },
                }
                _request(server, "POST", "/slice-results", request_body)
                _request(server, "POST", "/slice-results", request_body)
            finally:
                _stop_server(server)

            create_calls = [call for call in client.calls if call == "create"]
            self.assertEqual(len(create_calls), 1)
            follow_ups = mirror.list_child_issues(mirror.get_issue(identifier="PRO-712"))
            self.assertEqual(len(follow_ups), 1)
            child_metadata = follow_ups[0].metadata
            self.assertEqual(child_metadata["follow_up_origin"], "PRO-712")
            self.assertEqual(child_metadata["follow_up_class"], "owner_reroute")

    def test_control_drift_endpoint_reports_blockers_for_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            server = _api_server_fixture(temp_dir)
            storage = Storage(resolve_runtime_paths(temp_dir))
            mirror = LinearMirrorRepository(storage)
            repo_root = Path(temp_dir) / "alpha-repo"
            repo_root.mkdir(parents=True, exist_ok=True)
            try:
                _request(
                    server,
                    "POST",
                    "/registry/projects",
                    {
                        "project_key": "alpha",
                        "display_name": "Alpha",
                        "repo_root": str(repo_root),
                        "owning_bot": "alpha_bot",
                        "owner_chat_id": 101,
                        "owner_thread_id": 202,
                    },
                )
                mirror.upsert_issue(
                    linear_id="issue-711",
                    identifier="PRO-711",
                    title="Detect drift",
                    description="Surface mismatches before recovery",
                    team_id="team-1",
                    team_name="Projects",
                    state_id="state-1",
                    state_name="Todo",
                    state_type="unstarted",
                    project_id="project-alpha",
                    project_name="Alpha",
                    source_updated_at="2026-04-16T13:10:00+00:00",
                    metadata={"project_key": "alpha"},
                )
                _request(
                    server,
                    "POST",
                    "/dispatch/run",
                    {
                        "ingress_bot": "alpha_bot",
                        "project_key": "alpha",
                    },
                )
                runtime_storage = Storage(
                    resolve_project_runtime_paths("alpha", home=resolve_runtime_paths(temp_dir).home)
                )
                continuity = ContinuityService(runtime_storage).get_state("PRO-711", "main")
                self.assertIsNotNone(continuity)
                with runtime_storage.session() as connection:
                    connection.execute(
                        "UPDATE continuity_state SET resume_context_json = ? WHERE issue_key = ? AND runner_id = ?",
                        ('{"project_key":"beta"}', "PRO-711", "main"),
                    )
                payload = _request(server, "GET", "/control/drift?project_key=alpha")
            finally:
                _stop_server(server)

            self.assertTrue(payload["ok"])
            self.assertFalse(payload["drift"]["ok"])
            self.assertTrue(
                any("resume_context project_key" in blocker for blocker in payload["drift"]["blockers"])
            )

    def test_intake_endpoints_cover_submit_fetch_approve_and_reject(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            client = FakeLinearCrudClient()
            server = _api_server_fixture(temp_dir, linear_client=client)

            _request(
                server,
                "POST",
                "/registry/projects",
                {
                    "project_key": "orx",
                    "display_name": "ORX",
                    "repo_root": "/tmp/orx",
                    "owning_bot": "orx_bot",
                    "metadata": {"linear_team_id": "team-orx", "linear_project_id": "project-orx"},
                },
            )
            _request(
                server,
                "POST",
                "/registry/projects",
                {
                    "project_key": "beta",
                    "display_name": "Beta",
                    "repo_root": "/tmp/beta",
                    "owning_bot": "beta_bot",
                    "metadata": {"linear_team_id": "team-beta", "linear_project_id": "project-beta"},
                },
            )
            submitted = _request(
                server,
                "POST",
                "/intake/submit",
                {
                    "ingress_bot": "orx_bot",
                    "request_text": "capture a better operator audit trail",
                },
            )
            fetched = _request(
                server,
                "GET",
                f"/intake?intake_key={submitted['intake']['intake_key']}",
            )
            approved = _request(
                server,
                "POST",
                "/intake/approve",
                {"intake_key": submitted["intake"]["intake_key"]},
            )
            rejected = _request(
                server,
                "POST",
                "/intake/submit",
                {
                    "ingress_bot": "orx_bot",
                    "request_text": "orx and beta both need work here",
                },
            )
            rejected_payload = _request(
                server,
                "POST",
                "/intake/reject",
                {"intake_key": rejected["intake"]["intake_key"], "note": "need clarification"},
            )

            self.assertTrue(submitted["ok"])
            self.assertEqual(submitted["intake"]["status"], "pending_approval")
            self.assertEqual(submitted["intake"]["planning_stage"], "planning")
            self.assertEqual(submitted["intake"]["planning_model"], "gpt-5.4")
            self.assertEqual(submitted["intake"]["planning_reasoning_effort"], "high")
            self.assertEqual(submitted["intake"]["decomposition_reasoning_effort"], "high")
            self.assertEqual(submitted["intake"]["execution_reasoning_effort"], "medium")
            self.assertEqual(submitted["intake"]["confidence"], "high")
            self.assertFalse(submitted["intake"]["requires_hil"])
            self.assertEqual(submitted["intake"]["plan"]["items"][0]["project_key"], "orx")
            self.assertEqual(
                submitted["intake"]["plan"]["stage_contract"]["stage_order"],
                ["planning", "decomposition", "execution"],
            )
            self.assertEqual(
                submitted["intake"]["plan"]["stage_contract"]["stages"][0]["selected_reasoning_effort"],
                "high",
            )
            self.assertEqual(fetched["intake"]["intake_key"], submitted["intake"]["intake_key"])
            self.assertEqual(approved["intake"]["status"], "materialized")
            self.assertEqual(len(approved["created_issues"]), 1)
            self.assertEqual(client.calls[-2:], ["create", "update"])
            self.assertEqual(rejected["intake"]["status"], "clarification_required")
            self.assertEqual(rejected_payload["intake"]["status"], "rejected")

            _stop_server(server)

    def test_intake_endpoints_materialize_grouped_ticket_sets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            client = FakeLinearCrudClient()
            server = _api_server_fixture(temp_dir, linear_client=client)

            _request(
                server,
                "POST",
                "/registry/projects",
                {
                    "project_key": "orx",
                    "display_name": "ORX",
                    "repo_root": "/tmp/orx",
                    "owning_bot": "orx_bot",
                    "metadata": {"linear_team_id": "team-orx", "linear_project_id": "project-orx"},
                },
            )
            _request(
                server,
                "POST",
                "/registry/projects",
                {
                    "project_key": "beta",
                    "display_name": "Beta",
                    "repo_root": "/tmp/beta",
                    "owning_bot": "beta_bot",
                    "metadata": {"linear_team_id": "team-beta", "linear_project_id": "project-beta"},
                },
            )
            submitted = _request(
                server,
                "POST",
                "/intake/submit",
                {
                    "ingress_bot": "orx_bot",
                    "request_text": "- orx: tighten intake approval preview copy\n- beta: verify grouped approval lane routing",
                },
            )
            approved = _request(
                server,
                "POST",
                "/intake/approve",
                {"intake_key": submitted["intake"]["intake_key"]},
            )

            self.assertEqual(submitted["intake"]["planning_reasoning_effort"], "xhigh")
            self.assertEqual(submitted["intake"]["plan"]["planning_result"]["recommendation"], "split_ticket_set")
            self.assertEqual(submitted["intake"]["plan"]["decomposition"]["materialization_mode"], "grouped_ticket_set")
            self.assertEqual(len(submitted["intake"]["plan"]["decomposition"]["dependency_edges"]), 2)
            self.assertEqual(approved["intake"]["status"], "materialized")
            self.assertEqual(len(approved["created_issues"]), 3)
            self.assertIsNone(approved["created_issues"][0]["parent_identifier"])
            self.assertEqual(
                approved["created_issues"][1]["parent_identifier"],
                approved["created_issues"][0]["identifier"],
            )
            self.assertEqual(
                approved["created_issues"][2]["parent_identifier"],
                approved["created_issues"][0]["identifier"],
            )
            self.assertEqual(client.calls[-6:], ["create", "update", "create", "update", "create", "update"])

            _stop_server(server)


def _api_server_fixture(
    temp_dir: str,
    *,
    linear_client: object | None = None,
    daemon_state: dict[str, object] | None = None,
) -> OrxApiServer:
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
        issue_key="PRO-24",
        runner_id="runner-a",
        objective="Expose ORX state over localhost",
        slice_goal="Return status and proposals",
        acceptance=["status payload available"],
        validation_plan=["read api response json"],
    )
    executor.submit_slice_result(
        request.slice_id,
        {
            "status": "success",
            "summary": "API-facing continuity state persisted",
            "verified": True,
            "next_slice": "Return status and proposals",
            "artifacts": ["orx/api.py"],
            "metrics": {"routes": 3},
        },
    )
    continuity = ContinuityService(storage)
    proposals = ProposalService(storage, continuity=continuity)
    mirror = LinearMirrorRepository(storage)
    phase = mirror.upsert_issue(
        linear_id="phase-11",
        identifier="PRO-11",
        title="Telegram phase",
        description="",
        team_id="team-1",
        team_name="Projects",
        state_name="In Progress",
        state_type="started",
        project_id="project-1",
        project_name="ORX",
        source_updated_at="2026-04-15T21:59:00+00:00",
    )
    mirror.upsert_issue(
        linear_id="leaf-24",
        identifier="PRO-24",
        title="API contract",
        description="",
        team_id=phase.team_id,
        team_name=phase.team_name,
        state_name="In Progress",
        state_type="started",
        project_id=phase.project_id,
        project_name=phase.project_name,
        parent_linear_id=phase.linear_id,
        parent_identifier=phase.identifier,
        source_updated_at="2026-04-15T22:00:00+00:00",
    )
    proposals.route(
        "PRO-24",
        "runner-a",
        improvement_title="Expose proposal updates to Telegram readers",
        context={"suggested_phase_issue_key": "PRO-11"},
    )
    materializer = None
    if linear_client is not None:
        materializer = ProposalMaterializationService(
            storage,
            proposals=proposals,
            mirror=mirror,
            client=linear_client,
        )
    DaemonStateService(storage).record_last_tick(
        daemon_state
        or {
            "home": temp_dir,
            "db_path": str(storage.paths.db_path),
            "schema_version": CURRENT_SCHEMA_VERSION,
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
    api = OrxApiService(
        storage=storage,
        repository=repository,
        continuity=continuity,
        proposals=proposals,
        materializer=materializer,
        linear_client=linear_client,  # type: ignore[arg-type]
        dispatch_transport_factory=lambda: transport,
    )
    server = OrxApiServer(("127.0.0.1", 0), api)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server._thread = thread  # type: ignore[attr-defined]
    return server


def _request(
    server: OrxApiServer,
    method: str,
    path: str,
    body: dict[str, object] | None = None,
) -> dict[str, object]:
    connection = http.client.HTTPConnection(server.server_address[0], server.server_address[1])
    headers = {"Content-Type": "application/json"}
    payload = json.dumps(body) if body is not None else None
    connection.request(method, path, body=payload, headers=headers)
    response = connection.getresponse()
    data = json.loads(response.read().decode("utf-8"))
    connection.close()
    return data


def _stop_server(server: OrxApiServer) -> None:
    server.shutdown()
    server.server_close()
    server._thread.join(timeout=2)  # type: ignore[attr-defined]


def _request_http_with_retry(
    host: str,
    port: int,
    method: str,
    path: str,
    *,
    attempts: int = 50,
    delay: float = 0.05,
) -> dict[str, object]:
    last_error: OSError | http.client.HTTPException | None = None
    for _ in range(attempts):
        try:
            connection = http.client.HTTPConnection(host, port, timeout=2)
            connection.request(method, path)
            response = connection.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
            connection.close()
            return payload
        except (OSError, http.client.HTTPException) as error:
            last_error = error
            time.sleep(delay)
    raise RuntimeError(f"Timed out waiting for API server {host}:{port}: {last_error}")


class FakeLinearCrudClient:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self._next_issue_number = 502
        self.issue = LinearIssue(
            linear_id="issue-501",
            identifier="PRO-501",
            title="API CRUD validation",
            description="created over http",
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
        self.issues_by_ref: dict[str, LinearIssue] = {
            self.issue.identifier: self.issue,
            self.issue.linear_id: self.issue,
        }
        self.deleted_refs: set[str] = set()

    def get_issue(self, *, issue_ref: str) -> LinearIssue | None:
        self.calls.append("get")
        if issue_ref in self.deleted_refs:
            return None
        return self.issues_by_ref.get(issue_ref)

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
        issue_number = self._next_issue_number
        self._next_issue_number += 1
        parent_issue = next(
            (issue for issue in self.issues_by_ref.values() if issue.linear_id == parent_id),
            None,
        )
        self.issue = LinearIssue(
            linear_id=f"issue-{issue_number}",
            identifier=f"PRO-{issue_number}",
            title=title,
            description=description,
            url=f"https://linear.example/PRO-{issue_number}",
            team_id=team_id,
            team_name="Projects",
            state_id="state-1",
            state_name="Backlog",
            state_type="unstarted",
            parent_id=parent_id,
            parent_identifier=None if parent_issue is None else parent_issue.identifier,
            project_id=project_id,
            project_name=None,
        )
        self.issues_by_ref[self.issue.identifier] = self.issue
        self.issues_by_ref[self.issue.linear_id] = self.issue
        self.deleted_refs.discard(self.issue.identifier)
        self.deleted_refs.discard(self.issue.linear_id)
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
        current = self.issues_by_ref[issue_ref]
        self.issue = LinearIssue(
            linear_id=current.linear_id,
            identifier=current.identifier,
            title=title or current.title,
            description=description or current.description,
            url=current.url,
            team_id=current.team_id,
            team_name=current.team_name,
            state_id=state_id or current.state_id,
            state_name=current.state_name,
            state_type=current.state_type,
            parent_id=current.parent_id,
            parent_identifier=current.parent_identifier,
            project_id=current.project_id,
            project_name=current.project_name,
        )
        self.issues_by_ref[self.issue.identifier] = self.issue
        self.issues_by_ref[self.issue.linear_id] = self.issue
        return self.issue

    def archive_issue(self, *, issue_ref: str, trash: bool = False) -> LinearIssue:
        self.calls.append("archive")
        return self.issue

    def complete_issue(self, *, issue_ref: str, team_id: str) -> LinearIssue:
        self.calls.append("complete")
        current = self.issues_by_ref[issue_ref]
        self.issue = LinearIssue(
            linear_id=current.linear_id,
            identifier=current.identifier,
            title=current.title,
            description=current.description,
            url=current.url,
            team_id=team_id,
            team_name=current.team_name,
            state_id="state-done",
            state_name="Done",
            state_type="completed",
            parent_id=current.parent_id,
            parent_identifier=current.parent_identifier,
            project_id=current.project_id,
            project_name=current.project_name,
        )
        self.issues_by_ref[self.issue.identifier] = self.issue
        self.issues_by_ref[self.issue.linear_id] = self.issue
        return self.issue

    def delete_issue(self, *, issue_ref: str) -> LinearIssue:
        self.calls.append("delete")
        issue = self.issues_by_ref[issue_ref]
        self.deleted_refs.add(issue.identifier)
        self.deleted_refs.add(issue.linear_id)
        return issue


if __name__ == "__main__":
    unittest.main()
