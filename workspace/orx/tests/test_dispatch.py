from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from orx.dispatch import GlobalDispatchService
from orx.linear_client import LinearIssue
from orx.mirror import LinearMirrorRepository
from orx.registry import ProjectRegistry
from orx.storage import Storage
from orx.config import resolve_runtime_paths

from tests.test_executor import FakeTmuxTransport


class GlobalDispatchTests(unittest.TestCase):
    def test_get_project_for_bot_prefers_bot_registry_assignment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(resolve_runtime_paths(temp_dir))
            storage.bootstrap()
            registry = ProjectRegistry(storage)

            registry.upsert_project(
                project_key="alpha",
                display_name="Alpha",
                repo_root="/tmp/alpha",
                runtime_home="/tmp/runtime-alpha",
                owning_bot="shared_bot",
            )
            registry.upsert_project(
                project_key="beta",
                display_name="Beta",
                repo_root="/tmp/beta",
                runtime_home="/tmp/runtime-beta",
                owning_bot="shared_bot",
            )
            registry.upsert_bot(
                bot_identity="shared_bot",
                default_display_name="Shared",
            )
            with storage.session() as connection:
                connection.execute(
                    """
                    UPDATE bot_registry
                    SET assigned_project_key = ?, assignment_id = ?, availability_state = 'assigned'
                    WHERE bot_identity = ?
                    """,
                    ("beta", "assignment-beta", "shared_bot"),
                )

            project = registry.get_project_for_bot("shared_bot")

            self.assertIsNotNone(project)
            self.assertEqual(project.project_key, "beta")

    def test_assign_project_bot_clears_stale_project_rows_for_reused_bot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(resolve_runtime_paths(temp_dir))
            storage.bootstrap()
            registry = ProjectRegistry(storage)

            registry.upsert_project(
                project_key="alpha",
                display_name="Alpha",
                repo_root="/tmp/alpha",
                runtime_home="/tmp/runtime-alpha",
                owning_bot="shared_bot",
            )
            registry.upsert_project(
                project_key="beta",
                display_name="Beta",
                repo_root="/tmp/beta",
                runtime_home="/tmp/runtime-beta",
                owning_bot="shared_bot",
            )
            registry.upsert_bot(
                bot_identity="shared_bot",
                default_display_name="Shared",
                telegram_chat_id=101,
            )

            assignment = registry.assign_project_bot(project_key="beta", preferred_bot="shared_bot")

            self.assertIsNotNone(assignment)
            assert assignment is not None
            self.assertEqual(assignment.bot.assigned_project_key, "beta")
            self.assertEqual(registry.get_project("beta").assigned_bot, "shared_bot")
            self.assertIsNone(registry.get_project("alpha").assigned_bot)

    def test_deregister_project_removes_registry_entry_and_notifications(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(resolve_runtime_paths(temp_dir))
            storage.bootstrap()
            registry = ProjectRegistry(storage)
            service = GlobalDispatchService(
                storage=storage,
                registry=registry,
                transport_factory=FakeTmuxTransport,
            )
            service.register_project(
                project_key="alpha",
                display_name="Alpha",
                repo_root="/tmp/alpha",
                owning_bot="alpha_bot",
                owner_chat_id=101,
            )
            registry.create_notification(
                project_key="alpha",
                owning_bot="alpha_bot",
                kind="dispatch-handoff",
                payload={"message": "started"},
            )

            deleted = service.deregister_project(project_key="alpha")

            self.assertIsNotNone(deleted)
            self.assertEqual(deleted.project_key, "alpha")
            self.assertIsNone(registry.get_project("alpha"))
            self.assertEqual(
                registry.list_pending_notifications(project_key="alpha", owning_bot="alpha_bot"),
                [],
            )

    def test_stale_dispatch_lease_is_reclaimed_for_new_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(resolve_runtime_paths(temp_dir))
            storage.bootstrap()
            registry = ProjectRegistry(storage)

            with storage.session() as connection:
                connection.execute(
                    """
                    INSERT INTO dispatch_leases(lease_key, owner_id, acquired_at)
                    VALUES (?, ?, ?)
                    """,
                    (
                        registry.GLOBAL_DISPATCH_LEASE,
                        "drain:stale-owner",
                        "2026-04-16T10:00:00+00:00",
                    ),
                )

            lease = registry.acquire_dispatch_lease("dispatch:fresh-owner")

            self.assertEqual(lease.owner_id, "dispatch:fresh-owner")

    def test_dispatch_selects_registered_issue_and_creates_handoff_notification(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(resolve_runtime_paths(temp_dir))
            storage.bootstrap()
            registry = ProjectRegistry(storage)
            transport = FakeTmuxTransport()
            service = GlobalDispatchService(
                storage=storage,
                registry=registry,
                transport_factory=lambda: transport,
            )
            mirror = LinearMirrorRepository(storage)

            alpha = service.register_project(
                project_key="alpha",
                display_name="Alpha",
                repo_root="/tmp/alpha",
                owning_bot="alpha_bot",
                owner_chat_id=101,
                metadata={"tmux_namespace": "alpha"},
            )
            service.register_project(
                project_key="beta",
                display_name="Beta",
                repo_root="/tmp/beta",
                owning_bot="beta_bot",
                owner_chat_id=102,
                owner_thread_id=202,
                metadata={"tmux_namespace": "beta"},
            )

            mirror.upsert_issue(
                linear_id="lin-alpha-1",
                identifier="PRO-600",
                title="Alpha first",
                description="Ship alpha work",
                team_id="team-1",
                team_name="Projects",
                state_name="Todo",
                state_type="unstarted",
                priority=3,
                project_id="project-alpha",
                project_name="Alpha",
                source_updated_at="2026-04-15T10:00:00+00:00",
                metadata={"project_key": "alpha"},
            )
            mirror.upsert_issue(
                linear_id="lin-beta-1",
                identifier="PRO-601",
                title="Beta first",
                description="Ship beta work",
                team_id="team-1",
                team_name="Projects",
                state_name="Todo",
                state_type="unstarted",
                priority=1,
                project_id="project-beta",
                project_name="Beta",
                source_updated_at="2026-04-15T11:00:00+00:00",
                metadata={"project_key": "beta"},
            )

            result = service.dispatch_run(
                ingress_bot="alpha_bot",
                ingress_chat_id=1,
                ingress_thread_id=2,
            )

            self.assertEqual(result.decision, "dispatched")
            self.assertEqual(result.project_key, "beta")
            self.assertEqual(result.issue_key, "PRO-601")
            self.assertTrue(result.handoff_required)
            self.assertEqual(result.assigned_bot, "beta_bot")
            self.assertEqual(result.assignment_action, "reused")
            self.assertEqual(result.owning_bot, "beta_bot")
            self.assertIn("beta_bot", result.ingress_message)
            self.assertEqual(result.runtime.action, "started")
            self.assertEqual(result.runtime.session_name, "runner-beta")

            notifications = registry.list_pending_notifications(
                project_key="beta",
                owning_bot="beta_bot",
            )
            self.assertEqual(len(notifications), 1)
            self.assertEqual(notifications[0].issue_key, "PRO-601")
            self.assertEqual(notifications[0].target_bot, "beta_bot")
            self.assertEqual(notifications[0].payload["action"], "started")
            self.assertEqual(notifications[0].payload["assigned_bot"], "beta_bot")
            self.assertEqual(notifications[0].payload["target_thread_id"], 202)
            self.assertEqual(notifications[0].payload["execution_thread_id"], 202)
            self.assertEqual(
                notifications[0].payload["desired_bot_display_name"],
                "beta - Beta first",
            )

            dashboard = service.dashboard_payload()
            beta_entry = next(
                project for project in dashboard["projects"] if project["project"]["project_key"] == "beta"
            )
            self.assertEqual(beta_entry["active_issue_key"], "PRO-601")
            self.assertTrue(beta_entry["busy"])
            self.assertEqual(beta_entry["health_state"], "busy")
            self.assertTrue(beta_entry["drift"]["ok"])
            self.assertEqual(beta_entry["project"]["assigned_bot"], "beta_bot")

            bot_status = service.bot_status(bot_identity="beta_bot")
            self.assertEqual(bot_status["bot"]["assigned_project_key"], "beta")
            self.assertEqual(
                bot_status["bot"]["desired_display_name"],
                "beta - Beta first",
            )

            runtime = GlobalDispatchService(
                storage=storage,
                registry=registry,
                transport_factory=lambda: transport,
            )
            self.assertTrue(runtime.control_status(project_key="beta")["ok"])
            queued = runtime.control_queue_command(
                project_key="beta",
                command_kind="pause",
                payload={"source": "test"},
            )
            self.assertEqual(queued["command"]["command_kind"], "pause")

    def test_dispatch_prefers_persisted_project_execution_thread_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(resolve_runtime_paths(temp_dir))
            storage.bootstrap()
            registry = ProjectRegistry(storage)
            service = GlobalDispatchService(
                storage=storage,
                registry=registry,
                transport_factory=FakeTmuxTransport,
            )
            mirror = LinearMirrorRepository(storage)

            service.register_project(
                project_key="beta",
                display_name="Beta",
                repo_root="/tmp/beta",
                owning_bot="beta_bot",
                owner_chat_id=101,
                owner_thread_id=202,
                metadata={"execution_thread_id": 909},
            )
            mirror.upsert_issue(
                linear_id="lin-beta-thread",
                identifier="PRO-602",
                title="Beta execution thread",
                description="Route updates through the execution thread",
                team_id="team-1",
                team_name="Projects",
                state_name="Todo",
                state_type="unstarted",
                project_id="project-beta",
                project_name="Beta",
                source_updated_at="2026-04-16T13:10:00+00:00",
                metadata={"project_key": "beta"},
            )

            result = service.dispatch_run(ingress_bot="beta_bot", explicit_project_key="beta")

            self.assertEqual(result.decision, "dispatched")
            notifications = registry.list_pending_notifications(project_key="beta", owning_bot="beta_bot")
            self.assertEqual(len(notifications), 1)
            self.assertEqual(notifications[0].payload["target_thread_id"], 909)
            self.assertEqual(notifications[0].payload["execution_thread_id"], 909)
            dashboard = service.dashboard_payload()
            beta_entry = next(
                project for project in dashboard["projects"] if project["project"]["project_key"] == "beta"
            )
            self.assertEqual(beta_entry["project"]["execution_thread_id"], 909)

    def test_second_dispatch_skips_busy_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(resolve_runtime_paths(temp_dir))
            storage.bootstrap()
            registry = ProjectRegistry(storage)
            service = GlobalDispatchService(
                storage=storage,
                registry=registry,
                transport_factory=FakeTmuxTransport,
            )
            mirror = LinearMirrorRepository(storage)

            service.register_project(
                project_key="alpha",
                display_name="Alpha",
                repo_root="/tmp/alpha",
                owning_bot="alpha_bot",
            )
            service.register_project(
                project_key="beta",
                display_name="Beta",
                repo_root="/tmp/beta",
                owning_bot="beta_bot",
            )
            for index, project_key in enumerate(("alpha", "beta"), start=1):
                mirror.upsert_issue(
                    linear_id=f"lin-{project_key}",
                    identifier=f"PRO-61{index}",
                    title=f"{project_key.title()} task",
                    description="Run it",
                    team_id="team-1",
                    team_name="Projects",
                    state_name="Todo",
                    state_type="unstarted",
                    priority=index,
                    project_id=f"project-{project_key}",
                    project_name=project_key.title(),
                    source_updated_at=f"2026-04-15T1{index}:00:00+00:00",
                    metadata={"project_key": project_key},
                )

            first = service.dispatch_run(ingress_bot="alpha_bot")
            second = service.dispatch_run(ingress_bot="alpha_bot")

            self.assertEqual(first.project_key, "alpha")
            self.assertEqual(second.project_key, "beta")

    def test_dispatch_reports_existing_active_run_when_no_new_work_is_available(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(resolve_runtime_paths(temp_dir))
            storage.bootstrap()
            registry = ProjectRegistry(storage)
            service = GlobalDispatchService(
                storage=storage,
                registry=registry,
                transport_factory=FakeTmuxTransport,
            )
            mirror = LinearMirrorRepository(storage)

            service.register_project(
                project_key="alpha",
                display_name="Alpha",
                repo_root="/tmp/alpha",
                owning_bot="alpha_bot",
            )
            mirror.upsert_issue(
                linear_id="lin-alpha-active",
                identifier="PRO-620",
                title="Alpha task",
                description="Already in progress",
                team_id="team-1",
                team_name="Projects",
                state_name="Todo",
                state_type="unstarted",
                priority=1,
                project_id="project-alpha",
                project_name="Alpha",
                source_updated_at="2026-04-16T12:00:00+00:00",
                metadata={"project_key": "alpha"},
            )

            first = service.dispatch_run(ingress_bot="alpha_bot")
            second = service.dispatch_run(ingress_bot="observer_bot")

            self.assertEqual(first.decision, "dispatched")
            self.assertEqual(second.decision, "already-running")
            self.assertEqual(second.project_key, "alpha")
            self.assertEqual(second.issue_key, "PRO-620")
            self.assertTrue(second.handoff_required)
            self.assertEqual(second.assigned_bot, "alpha_bot")
            self.assertEqual(second.assignment_action, "active")
            self.assertIn("Work is already running", second.ingress_message)
            self.assertIn("alpha_bot", second.ingress_message)
            self.assertIn("PRO-620", second.ingress_message)
            self.assertIsNone(second.runtime)

    def test_submit_slice_result_can_continue_same_issue_then_drain_next_ticket(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(resolve_runtime_paths(temp_dir))
            storage.bootstrap()
            registry = ProjectRegistry(storage)
            linear = FakeLinearCompleteClient()
            transport = FakeTmuxTransport()
            service = GlobalDispatchService(
                storage=storage,
                registry=registry,
                linear_client=linear,  # type: ignore[arg-type]
                transport_factory=lambda: transport,
            )
            mirror = LinearMirrorRepository(storage)

            service.register_project(
                project_key="alpha",
                display_name="Alpha",
                repo_root="/tmp/alpha",
                owning_bot="alpha_bot",
            )
            mirror.upsert_issue(
                linear_id="lin-alpha-1",
                identifier="PRO-700",
                title="Alpha first",
                description="Ship alpha work",
                team_id="team-1",
                team_name="Projects",
                state_name="Todo",
                state_type="unstarted",
                priority=1,
                project_id="project-alpha",
                project_name="Alpha",
                source_updated_at="2026-04-16T12:00:00+00:00",
                metadata={"project_key": "alpha"},
            )
            mirror.upsert_issue(
                linear_id="lin-alpha-2",
                identifier="PRO-701",
                title="Alpha second",
                description="Ship next alpha work",
                team_id="team-1",
                team_name="Projects",
                state_name="Todo",
                state_type="unstarted",
                priority=2,
                project_id="project-alpha",
                project_name="Alpha",
                source_updated_at="2026-04-16T12:01:00+00:00",
                metadata={"project_key": "alpha"},
            )

            first = service.dispatch_run(ingress_bot="alpha_bot")
            registration = registry.get_project("alpha")
            self.assertIsNotNone(registration)
            runtime = service._runtime_service(registration)  # type: ignore[arg-type]
            continuity = runtime.continuity.get_state("PRO-700", "main")
            self.assertIsNotNone(continuity)

            continued = service.submit_slice_result(
                project_key="alpha",
                slice_id=continuity.active_slice_id,  # type: ignore[arg-type]
                payload={
                    "status": "success",
                    "summary": "First slice passed",
                    "verified": True,
                    "next_slice": "Finish the remaining alpha work",
                    "artifacts": ["proof-1"],
                    "metrics": {"step": 1},
                },
            )
            self.assertFalse(continued.finalized)
            self.assertEqual(continued.next_slice, "Finish the remaining alpha work")

            drained = service.drain_projects()
            self.assertEqual(len(drained), 1)
            self.assertEqual(drained[0].project_key, "alpha")
            self.assertEqual(drained[0].issue_key, "PRO-700")
            self.assertEqual(drained[0].action, "continued")

            continued_state = runtime.continuity.get_state("PRO-700", "main")
            self.assertIsNotNone(continued_state)
            self.assertIsNotNone(continued_state.active_slice_id)
            self.assertEqual(continued_state.resume_context["project_key"], "alpha")

            finalized = service.submit_slice_result(
                project_key="alpha",
                slice_id=continued_state.active_slice_id,  # type: ignore[arg-type]
                payload={
                    "status": "success",
                    "summary": "Alpha issue complete",
                    "verified": True,
                    "next_slice": None,
                    "artifacts": ["proof-2"],
                    "metrics": {"step": 2},
                },
            )
            self.assertTrue(finalized.finalized)
            self.assertTrue(finalized.linear_completed)
            self.assertIn("complete", linear.calls)

            next_batch = service.drain_projects()
            self.assertEqual(len(next_batch), 1)
            self.assertEqual(next_batch[0].project_key, "alpha")
            self.assertEqual(next_batch[0].issue_key, "PRO-701")
            self.assertEqual(next_batch[0].action, "started")

            completed_issue = mirror.get_issue(identifier="PRO-700")
            self.assertIsNotNone(completed_issue)
            self.assertEqual(completed_issue.state_type, "completed")
            self.assertIsNotNone(completed_issue.completed_at)

    def test_restart_context_pack_exposes_durable_project_issue_and_slice_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(resolve_runtime_paths(temp_dir))
            storage.bootstrap()
            registry = ProjectRegistry(storage)
            transport = FakeTmuxTransport()
            service = GlobalDispatchService(
                storage=storage,
                registry=registry,
                transport_factory=lambda: transport,
            )
            mirror = LinearMirrorRepository(storage)

            service.register_project(
                project_key="alpha",
                display_name="Alpha",
                repo_root="/tmp/alpha",
                owning_bot="alpha_bot",
                owner_chat_id=101,
                owner_thread_id=202,
                metadata={"linear_project_id": "project-alpha"},
            )
            mirror.upsert_issue(
                linear_id="lin-alpha-1",
                identifier="PRO-710",
                title="Crash continuity",
                description="Persist enough to resume after a crash",
                team_id="team-1",
                team_name="Projects",
                state_name="Todo",
                state_type="unstarted",
                priority=1,
                project_id="project-alpha",
                project_name="Alpha",
                source_updated_at="2026-04-16T13:00:00+00:00",
                metadata={"project_key": "alpha"},
            )

            dispatched = service.dispatch_run(ingress_bot="alpha_bot")
            context = service.build_restart_context(project_key="alpha")

            self.assertEqual(dispatched.project_key, "alpha")
            self.assertEqual(context["project"]["project_key"], "alpha")
            self.assertEqual(context["runtime"]["active_issue_key"], "PRO-710")
            self.assertEqual(context["issue"]["identifier"], "PRO-710")
            self.assertEqual(context["continuity"]["issue_key"], "PRO-710")
            self.assertEqual(context["continuity"]["resume_context"]["project_key"], "alpha")
            self.assertEqual(context["execution_packet"]["issue_key"], "PRO-710")
            self.assertEqual(context["execution_packet"]["active_slice_id"], context["continuity"]["active_slice_id"])
            self.assertEqual(context["execution_packet"]["continuity_revision"], context["continuity"]["updated_at"])
            self.assertEqual(context["execution_packet"]["decision_epoch"], context["continuity"]["active_slice_id"])
            self.assertIsNotNone(context["execution_packet"]["packet_revision"])
            self.assertEqual(
                context["active_slice_request"]["request"]["context"]["project_key"],
                "alpha",
            )
            self.assertEqual(context["recovery"]["action"], "resume")
            self.assertTrue(context["drift"]["ok"])
            self.assertEqual(context["drift"]["blockers"], [])
            self.assertIn("project_runtime", context["durable_sources"])

    def test_project_drift_reports_context_and_session_mismatches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(resolve_runtime_paths(temp_dir))
            storage.bootstrap()
            registry = ProjectRegistry(storage)
            service = GlobalDispatchService(
                storage=storage,
                registry=registry,
                transport_factory=FakeTmuxTransport,
            )
            mirror = LinearMirrorRepository(storage)

            repo_root = f"{temp_dir}/alpha-repo"
            Path(repo_root).mkdir(parents=True, exist_ok=True)
            service.register_project(
                project_key="alpha",
                display_name="Alpha",
                repo_root=repo_root,
                owning_bot="alpha_bot",
                owner_chat_id=101,
                owner_thread_id=202,
            )
            mirror.upsert_issue(
                linear_id="lin-alpha-2",
                identifier="PRO-711",
                title="Detect drift",
                description="Surface mismatches before recovery",
                team_id="team-1",
                team_name="Projects",
                state_name="Todo",
                state_type="unstarted",
                priority=1,
                project_id="project-alpha",
                project_name="Alpha",
                source_updated_at="2026-04-16T13:10:00+00:00",
                metadata={"project_key": "alpha"},
            )

            service.dispatch_run(ingress_bot="alpha_bot")
            registration = registry.get_project("alpha")
            self.assertIsNotNone(registration)
            runtime = service._runtime_service(registration)  # type: ignore[arg-type]
            continuity = runtime.continuity.get_state("PRO-711", "main")
            self.assertIsNotNone(continuity)
            request = runtime.store.get_slice_request(continuity.active_slice_id)  # type: ignore[arg-type]
            self.assertIsNotNone(request)

            with runtime.storage.session() as connection:
                connection.execute(
                    "UPDATE continuity_state SET resume_context_json = ? WHERE issue_key = ? AND runner_id = ?",
                    ('{"project_key":"beta"}', "PRO-711", "main"),
                )
                connection.execute(
                    "UPDATE slice_requests SET request_json = ? WHERE slice_id = ?",
                    (
                        '{"issue_key":"PRO-711","runner_id":"main","session_name":"runner-alpha","context":{"project_key":"beta"}}',
                        request.slice_id,
                    ),
                )
                connection.execute(
                    "UPDATE executor_sessions SET session_name = ? WHERE runner_id = ?",
                    ("runner-beta", "main"),
                )

            drift = service.build_project_drift(project_key="alpha")

            self.assertFalse(drift["ok"])
            self.assertTrue(
                any("resume_context project_key" in blocker for blocker in drift["blockers"])
            )
            self.assertTrue(
                any("slice request project_key" in blocker for blocker in drift["blockers"])
            )
            self.assertTrue(
                any("does not match the expected runner session `runner-alpha`" in blocker for blocker in drift["blockers"])
            )
            dashboard = service.dashboard_payload()
            alpha_entry = next(
                project for project in dashboard["projects"] if project["project"]["project_key"] == "alpha"
            )
            self.assertEqual(alpha_entry["health_state"], "drift-blocked")
            self.assertFalse(alpha_entry["drift"]["ok"])

    def test_project_drift_reports_missing_tmux_session_as_recoverable_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(resolve_runtime_paths(temp_dir))
            storage.bootstrap()
            registry = ProjectRegistry(storage)
            transport = FakeTmuxTransport()
            service = GlobalDispatchService(
                storage=storage,
                registry=registry,
                transport_factory=lambda: transport,
            )
            mirror = LinearMirrorRepository(storage)

            repo_root = f"{temp_dir}/alpha-repo"
            Path(repo_root).mkdir(parents=True, exist_ok=True)
            service.register_project(
                project_key="alpha",
                display_name="Alpha",
                repo_root=repo_root,
                owning_bot="alpha_bot",
                owner_chat_id=101,
            )
            mirror.upsert_issue(
                linear_id="lin-alpha-missing-session",
                identifier="PRO-711A",
                title="Detect missing tmux session",
                description="Block recovery when the claimed session is gone",
                team_id="team-1",
                team_name="Projects",
                state_name="Todo",
                state_type="unstarted",
                priority=1,
                project_id="project-alpha",
                project_name="Alpha",
                source_updated_at="2026-04-16T13:10:00+00:00",
                metadata={"project_key": "alpha"},
            )

            service.dispatch_run(ingress_bot="alpha_bot")
            transport.sessions.clear()

            drift = service.build_project_drift(project_key="alpha")

            self.assertTrue(drift["ok"])
            self.assertFalse(drift["checks"]["session_exists"])
            self.assertTrue(drift["checks"]["session_recoverable"])
            self.assertEqual(drift["blockers"], [])
            self.assertTrue(
                any("ORX can recreate it from continuity" in warning for warning in drift["warnings"])
            )

    def test_status_and_context_clear_stale_idle_session_without_live_tmux(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(resolve_runtime_paths(temp_dir))
            storage.bootstrap()
            registry = ProjectRegistry(storage)
            transport = FakeTmuxTransport()
            service = GlobalDispatchService(
                storage=storage,
                registry=registry,
                transport_factory=lambda: transport,
            )

            repo_root = f"{temp_dir}/alpha-repo"
            Path(repo_root).mkdir(parents=True, exist_ok=True)
            service.register_project(
                project_key="alpha",
                display_name="Alpha",
                repo_root=repo_root,
                owning_bot="alpha_bot",
                owner_chat_id=101,
            )
            registration = registry.get_project("alpha")
            self.assertIsNotNone(registration)
            runtime = service._runtime_service(registration)  # type: ignore[arg-type]
            runtime.store.upsert_session(
                runner_id="main",
                issue_key="PRO-799",
                session_name="runner-alpha",
                pane_target="%1",
                transport="tmux-codex",
                state="idle",
            )

            status = service.control_status(project_key="alpha")
            context = service.build_restart_context(project_key="alpha")
            dashboard = service.dashboard_payload()
            drift = service.build_project_drift(project_key="alpha")

            self.assertIsNone(status["session"])
            self.assertIsNone(context["runtime"]["session"])
            alpha_entry = next(
                project for project in dashboard["projects"] if project["project"]["project_key"] == "alpha"
            )
            self.assertIsNone(alpha_entry["session"])
            self.assertFalse(drift["checks"]["session_exists"])
            self.assertFalse(drift["checks"]["session_recoverable"])
            self.assertIsNone(runtime.store.get_session("main"))

    def test_drift_report_clears_stale_idle_session_without_prior_status_call(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(resolve_runtime_paths(temp_dir))
            storage.bootstrap()
            registry = ProjectRegistry(storage)
            transport = FakeTmuxTransport()
            service = GlobalDispatchService(
                storage=storage,
                registry=registry,
                transport_factory=lambda: transport,
            )

            repo_root = f"{temp_dir}/alpha-repo"
            Path(repo_root).mkdir(parents=True, exist_ok=True)
            service.register_project(
                project_key="alpha",
                display_name="Alpha",
                repo_root=repo_root,
                owning_bot="alpha_bot",
                owner_chat_id=101,
            )
            registration = registry.get_project("alpha")
            self.assertIsNotNone(registration)
            runtime = service._runtime_service(registration)  # type: ignore[arg-type]
            runtime.store.upsert_session(
                runner_id="main",
                issue_key="PRO-799",
                session_name="runner-alpha",
                pane_target="%1",
                transport="tmux-codex",
                state="idle",
            )

            drift = service.build_project_drift(project_key="alpha")

            self.assertFalse(drift["checks"]["session_exists"])
            self.assertFalse(drift["checks"]["session_recoverable"])
            self.assertIsNone(runtime.store.get_session("main"))

    def test_restart_context_hides_missing_recoverable_session_from_runtime_view(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(resolve_runtime_paths(temp_dir))
            storage.bootstrap()
            registry = ProjectRegistry(storage)
            transport = FakeTmuxTransport()
            service = GlobalDispatchService(
                storage=storage,
                registry=registry,
                transport_factory=lambda: transport,
            )
            mirror = LinearMirrorRepository(storage)

            repo_root = f"{temp_dir}/alpha-repo"
            Path(repo_root).mkdir(parents=True, exist_ok=True)
            service.register_project(
                project_key="alpha",
                display_name="Alpha",
                repo_root=repo_root,
                owning_bot="alpha_bot",
                owner_chat_id=101,
            )
            mirror.upsert_issue(
                linear_id="lin-alpha-recover-context",
                identifier="PRO-711C",
                title="Hide stale runtime session",
                description="Missing tmux should be recoverable, not reported as live",
                team_id="team-1",
                team_name="Projects",
                state_name="Todo",
                state_type="unstarted",
                priority=1,
                project_id="project-alpha",
                project_name="Alpha",
                source_updated_at="2026-04-16T13:14:00+00:00",
                metadata={"project_key": "alpha"},
            )

            service.dispatch_run(ingress_bot="alpha_bot")
            transport.sessions.clear()

            context = service.build_restart_context(project_key="alpha")

            self.assertEqual(context["runtime"]["active_issue_key"], "PRO-711C")
            self.assertIsNone(context["runtime"]["session"])
            self.assertFalse(context["drift"]["checks"]["session_exists"])
            self.assertTrue(context["drift"]["checks"]["session_recoverable"])

    def test_bot_name_sync_records_rate_limit_retry_at(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(resolve_runtime_paths(temp_dir))
            storage.bootstrap()
            registry = ProjectRegistry(storage)
            service = GlobalDispatchService(storage=storage, registry=registry)

            service.register_bot(
                bot_identity="alpha_bot",
                default_display_name="Alpha",
                telegram_chat_id=101,
            )

            synced = service.sync_bot_name(
                bot_identity="alpha_bot",
                current_display_name=None,
                sync_state="rate_limited",
                retry_at="2026-04-16T16:00:00+00:00",
            )

            self.assertTrue(synced["ok"])
            self.assertEqual(synced["bot"]["name_sync_state"], "rate_limited")
            self.assertEqual(
                synced["bot"]["name_sync_retry_at"],
                "2026-04-16T16:00:00+00:00",
            )

    def test_bot_name_sync_can_degrade_desired_display_name_to_project_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(resolve_runtime_paths(temp_dir))
            storage.bootstrap()
            registry = ProjectRegistry(storage)
            service = GlobalDispatchService(storage=storage, registry=registry)

            service.register_bot(
                bot_identity="alpha_bot",
                default_display_name="Alpha",
                telegram_chat_id=101,
            )
            registry.set_bot_display_target(
                bot_identity="alpha_bot",
                desired_display_name="alpha - fix lock drift",
            )

            synced = service.sync_bot_name(
                bot_identity="alpha_bot",
                current_display_name="alpha",
                desired_display_name="alpha",
                sync_state="synced",
                retry_at=None,
            )

            self.assertTrue(synced["ok"])
            self.assertEqual(synced["bot"]["current_display_name"], "alpha")
            self.assertEqual(synced["bot"]["desired_display_name"], "alpha")
            self.assertEqual(synced["bot"]["name_sync_state"], "synced")
            self.assertIsNone(synced["bot"]["name_sync_retry_at"])

    def test_drain_projects_recovers_missing_tmux_session_for_active_issue(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(resolve_runtime_paths(temp_dir))
            storage.bootstrap()
            registry = ProjectRegistry(storage)
            transport = FakeTmuxTransport()
            service = GlobalDispatchService(
                storage=storage,
                registry=registry,
                transport_factory=lambda: transport,
            )
            mirror = LinearMirrorRepository(storage)

            repo_root = f"{temp_dir}/alpha-repo"
            Path(repo_root).mkdir(parents=True, exist_ok=True)
            service.register_project(
                project_key="alpha",
                display_name="Alpha",
                repo_root=repo_root,
                owning_bot="alpha_bot",
                owner_chat_id=101,
            )
            mirror.upsert_issue(
                linear_id="lin-alpha-recover",
                identifier="PRO-711B",
                title="Recover missing session",
                description="Replay the active slice after tmux disappears",
                team_id="team-1",
                team_name="Projects",
                state_name="Todo",
                state_type="unstarted",
                priority=1,
                project_id="project-alpha",
                project_name="Alpha",
                source_updated_at="2026-04-16T13:12:00+00:00",
                metadata={"project_key": "alpha"},
            )

            service.dispatch_run(ingress_bot="alpha_bot")
            transport.sessions.clear()

            drained = service.drain_projects()

            self.assertEqual(len(drained), 1)
            self.assertEqual(drained[0].project_key, "alpha")
            self.assertEqual(drained[0].issue_key, "PRO-711B")
            self.assertEqual(drained[0].action, "recovered")
            self.assertIn("runner-alpha", transport.sessions)

    def test_dispatch_run_refuses_to_start_when_project_has_drift_blockers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(resolve_runtime_paths(temp_dir))
            storage.bootstrap()
            registry = ProjectRegistry(storage)
            service = GlobalDispatchService(
                storage=storage,
                registry=registry,
                transport_factory=FakeTmuxTransport,
            )
            mirror = LinearMirrorRepository(storage)

            repo_root = f"{temp_dir}/alpha-repo"
            Path(repo_root).mkdir(parents=True, exist_ok=True)
            service.register_project(
                project_key="alpha",
                display_name="Alpha",
                repo_root=repo_root,
                owning_bot="alpha_bot",
                owner_chat_id=101,
                owner_thread_id=202,
            )
            mirror.upsert_issue(
                linear_id="lin-alpha-3",
                identifier="PRO-712",
                title="Blocked by drift",
                description="Do not dispatch when project bindings are stale",
                team_id="team-1",
                team_name="Projects",
                state_name="Todo",
                state_type="unstarted",
                priority=1,
                project_id="project-alpha",
                project_name="Alpha",
                source_updated_at="2026-04-16T13:20:00+00:00",
                metadata={"project_key": "alpha"},
            )
            service.dispatch_run(ingress_bot="alpha_bot", explicit_project_key="alpha")
            registration = registry.get_project("alpha")
            self.assertIsNotNone(registration)
            runtime = service._runtime_service(registration)  # type: ignore[arg-type]
            with runtime.storage.session() as connection:
                connection.execute(
                    "UPDATE continuity_state SET resume_context_json = ? WHERE issue_key = ? AND runner_id = ?",
                    ('{"project_key":"beta"}', "PRO-712", "main"),
                )
            runtime.repository.release_issue_lease("PRO-712", "main")

            result = service.dispatch_run(ingress_bot="alpha_bot", explicit_project_key="alpha")

            self.assertEqual(result.decision, "drift-blocked")
            self.assertIsNone(result.runtime)
            self.assertIn("drift blockers", result.ingress_message)

    def test_restart_context_recovery_is_overridden_to_drift_when_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(resolve_runtime_paths(temp_dir))
            storage.bootstrap()
            registry = ProjectRegistry(storage)
            service = GlobalDispatchService(
                storage=storage,
                registry=registry,
                transport_factory=FakeTmuxTransport,
            )
            mirror = LinearMirrorRepository(storage)

            repo_root = f"{temp_dir}/alpha-repo"
            Path(repo_root).mkdir(parents=True, exist_ok=True)
            service.register_project(
                project_key="alpha",
                display_name="Alpha",
                repo_root=repo_root,
                owning_bot="alpha_bot",
                owner_chat_id=101,
                owner_thread_id=202,
            )
            mirror.upsert_issue(
                linear_id="lin-alpha-4",
                identifier="PRO-713",
                title="Recovery should degrade on drift",
                description="Do not auto-resume stale bindings",
                team_id="team-1",
                team_name="Projects",
                state_name="Todo",
                state_type="unstarted",
                priority=1,
                project_id="project-alpha",
                project_name="Alpha",
                source_updated_at="2026-04-16T13:30:00+00:00",
                metadata={"project_key": "alpha"},
            )

            service.dispatch_run(ingress_bot="alpha_bot")
            registration = registry.get_project("alpha")
            self.assertIsNotNone(registration)
            runtime = service._runtime_service(registration)  # type: ignore[arg-type]
            with runtime.storage.session() as connection:
                connection.execute(
                    "UPDATE continuity_state SET resume_context_json = ? WHERE issue_key = ? AND runner_id = ?",
                    ('{"project_key":"beta"}', "PRO-713", "main"),
                )

            context = service.build_restart_context(project_key="alpha")

            self.assertEqual(context["recovery"]["action"], "drift")
            self.assertIn("drift blockers", context["recovery"]["reason"])

    def test_drain_projects_skips_project_with_drift_blockers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(resolve_runtime_paths(temp_dir))
            storage.bootstrap()
            registry = ProjectRegistry(storage)
            service = GlobalDispatchService(
                storage=storage,
                registry=registry,
                transport_factory=FakeTmuxTransport,
            )
            mirror = LinearMirrorRepository(storage)

            repo_root = f"{temp_dir}/alpha-repo"
            Path(repo_root).mkdir(parents=True, exist_ok=True)
            service.register_project(
                project_key="alpha",
                display_name="Alpha",
                repo_root=repo_root,
                owning_bot="alpha_bot",
                owner_chat_id=101,
                owner_thread_id=202,
            )
            mirror.upsert_issue(
                linear_id="lin-alpha-5",
                identifier="PRO-714",
                title="Do not drain drifted project",
                description="daemon should skip stale topology",
                team_id="team-1",
                team_name="Projects",
                state_name="Todo",
                state_type="unstarted",
                priority=1,
                project_id="project-alpha",
                project_name="Alpha",
                source_updated_at="2026-04-16T13:40:00+00:00",
                metadata={"project_key": "alpha"},
            )
            service.dispatch_run(ingress_bot="alpha_bot")
            registration = registry.get_project("alpha")
            self.assertIsNotNone(registration)
            runtime = service._runtime_service(registration)  # type: ignore[arg-type]
            with runtime.storage.session() as connection:
                connection.execute(
                    "UPDATE continuity_state SET resume_context_json = ? WHERE issue_key = ? AND runner_id = ?",
                    ('{"project_key":"beta"}', "PRO-714", "main"),
                )

            drained = service.drain_projects()

            self.assertEqual(drained, [])

    def test_submit_slice_result_marks_stale_revision_payload_as_audit_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(resolve_runtime_paths(temp_dir))
            storage.bootstrap()
            registry = ProjectRegistry(storage)
            service = GlobalDispatchService(
                storage=storage,
                registry=registry,
                transport_factory=FakeTmuxTransport,
            )
            mirror = LinearMirrorRepository(storage)

            repo_root = f"{temp_dir}/alpha-repo"
            Path(repo_root).mkdir(parents=True, exist_ok=True)
            service.register_project(
                project_key="alpha",
                display_name="Alpha",
                repo_root=repo_root,
                owning_bot="alpha_bot",
            )
            mirror.upsert_issue(
                linear_id="lin-alpha-6",
                identifier="PRO-715",
                title="Reject stale handoff",
                description="Ticket body\n\n## Latest Handoff\n- Status: running\n",
                team_id="team-1",
                team_name="Projects",
                state_name="Todo",
                state_type="unstarted",
                priority=1,
                project_id="project-alpha",
                project_name="Alpha",
                source_updated_at="2026-04-16T13:50:00+00:00",
                metadata={"project_key": "alpha"},
            )

            dispatched = service.dispatch_run(ingress_bot="alpha_bot")
            self.assertEqual(dispatched.decision, "dispatched")
            context = service.build_restart_context(project_key="alpha")
            packet = context["execution_packet"]
            continuity = context["continuity"]

            stale = service.submit_slice_result(
                project_key="alpha",
                slice_id=continuity["active_slice_id"],
                payload={
                    "status": "success",
                    "summary": "Late stale result",
                    "verified": True,
                    "next_slice": None,
                    "artifacts": ["tests/test_dispatch.py"],
                    "metrics": {"source": "stale"},
                    "packet_key": packet["packet_key"],
                    "packet_revision": "stale-packet",
                    "latest_handoff_revision": packet["latest_handoff_revision"],
                    "continuity_revision": packet["continuity_revision"],
                    "decision_epoch": packet["decision_epoch"],
                },
            )

            self.assertEqual(stale.apply_status, "stale_audit_only")
            self.assertEqual(stale.status, "stale_audit_only")
            self.assertEqual(stale.stale_reason, "packet_revision_mismatch")
            post = service.build_restart_context(project_key="alpha")
            self.assertEqual(post["continuity"]["active_slice_id"], continuity["active_slice_id"])
            self.assertEqual(post["execution_packet"]["packet_revision"], packet["packet_revision"])


class FakeLinearCompleteClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def complete_issue(self, *, issue_ref: str, team_id: str) -> LinearIssue:
        self.calls.append("complete")
        return LinearIssue(
            linear_id=f"lin-{issue_ref}",
            identifier=issue_ref,
            title=f"{issue_ref} complete",
            description="completed",
            url=f"https://linear.example/{issue_ref}",
            team_id=team_id,
            team_name="Projects",
            state_id="state-done",
            state_name="Done",
            state_type="completed",
            parent_id=None,
            parent_identifier=None,
            project_id="project-alpha",
            project_name="Alpha",
        )


if __name__ == "__main__":
    unittest.main()
