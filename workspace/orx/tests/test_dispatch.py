from __future__ import annotations

import tempfile
import subprocess
import unittest
import os
from pathlib import Path
from unittest.mock import patch

from orx.dispatch import GlobalDispatchService
from orx.linear_client import LinearIssue
from orx.mirror import LinearMirrorRepository
from orx.registry import ProjectRegistry
from orx.storage import Storage
from orx.config import resolve_runtime_paths

from tests.test_executor import FakeTmuxTransport


class FlakyTmuxTransport(FakeTmuxTransport):
    def __init__(self) -> None:
        super().__init__()
        self.visible_limit_by_session: dict[str, int] = {}
        self.visible_checks_by_session: dict[str, int] = {}

    def expire_session_after(self, name: str, *, visible_checks: int) -> None:
        self.visible_limit_by_session[name] = visible_checks
        self.visible_checks_by_session.pop(name, None)

    def has_session(self, name: str) -> bool:
        if name not in self.sessions:
            return False
        limit = self.visible_limit_by_session.get(name)
        if limit is None:
            return True
        checks = self.visible_checks_by_session.get(name, 0)
        if checks >= limit:
            self.sessions.pop(name, None)
            return False
        self.visible_checks_by_session[name] = checks + 1
        return True

    def kill_session(self, name: str) -> bool:
        self.visible_checks_by_session.pop(name, None)
        self.visible_limit_by_session.pop(name, None)
        return super().kill_session(name)


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

    def test_assign_project_bot_prefers_project_affinity_bot_before_ingress_bot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(resolve_runtime_paths(temp_dir))
            storage.bootstrap()
            registry = ProjectRegistry(storage)

            registry.upsert_project(
                project_key="validation-os",
                display_name="validation-os",
                repo_root="/tmp/validation-os",
                runtime_home="/tmp/runtime-validation-os",
            )
            registry.upsert_bot(
                bot_identity="BentoBoxThreeBot",
                default_display_name="validation-os",
                telegram_chat_id=101,
            )
            registry.upsert_bot(
                bot_identity="BerryRamenBot",
                default_display_name="create-t3-jian",
                telegram_chat_id=102,
            )

            assignment = registry.assign_project_bot(
                project_key="validation-os",
                preferred_bot="BerryRamenBot",
            )

            self.assertIsNotNone(assignment)
            assert assignment is not None
            self.assertEqual(assignment.bot.bot_identity, "BentoBoxThreeBot")
            self.assertEqual(assignment.project.assigned_bot, "BentoBoxThreeBot")

    def test_assign_project_bot_skips_stale_affinity_bot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(resolve_runtime_paths(temp_dir))
            storage.bootstrap()
            registry = ProjectRegistry(storage)

            registry.upsert_project(
                project_key="create-t3-jian",
                display_name="create-t3-jian",
                repo_root="/tmp/create-t3-jian",
                runtime_home="/tmp/runtime-create-t3-jian",
            )
            registry.upsert_bot(
                bot_identity="BerryRamenBot",
                default_display_name="create-t3-jian",
                telegram_chat_id=101,
            )
            registry.upsert_bot(
                bot_identity="BentoBoxThreeBot",
                default_display_name="validation-os",
                telegram_chat_id=102,
            )
            with storage.session() as connection:
                connection.execute(
                    "UPDATE bot_registry SET last_heartbeat_at = ? WHERE bot_identity = ?",
                    ("2026-04-16T00:00:00+00:00", "BerryRamenBot"),
                )

            assignment = registry.assign_project_bot(project_key="create-t3-jian")

            self.assertIsNotNone(assignment)
            assert assignment is not None
            self.assertEqual(assignment.bot.bot_identity, "BentoBoxThreeBot")
            self.assertEqual(assignment.project.assigned_bot, "BentoBoxThreeBot")

    def test_assign_project_bot_does_not_reuse_stale_current_bot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(resolve_runtime_paths(temp_dir))
            storage.bootstrap()
            registry = ProjectRegistry(storage)

            registry.upsert_project(
                project_key="validation-os",
                display_name="validation-os",
                repo_root="/tmp/validation-os",
                runtime_home="/tmp/runtime-validation-os",
                owning_bot="BentoBoxThreeBot",
            )
            registry.upsert_bot(
                bot_identity="BentoBoxThreeBot",
                default_display_name="validation-os",
                telegram_chat_id=101,
            )
            registry.upsert_bot(
                bot_identity="BlastRadiusBot",
                default_display_name="tmux-codex",
                telegram_chat_id=102,
            )
            with storage.session() as connection:
                connection.execute(
                    "UPDATE bot_registry SET last_heartbeat_at = ?, assigned_project_key = NULL, assignment_id = NULL, availability_state = 'available' WHERE bot_identity = ?",
                    ("2026-04-16T00:00:00+00:00", "BentoBoxThreeBot"),
                )

            assignment = registry.assign_project_bot(project_key="validation-os")

            self.assertIsNotNone(assignment)
            assert assignment is not None
            self.assertEqual(assignment.bot.bot_identity, "BlastRadiusBot")
            self.assertEqual(assignment.project.assigned_bot, "BlastRadiusBot")

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

    def test_dispatch_run_prefers_project_affinity_bot_over_wrong_ingress_bot(self) -> None:
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

            repo_root = Path(temp_dir) / "validation-os"
            repo_root.mkdir(parents=True, exist_ok=True)
            service.register_project(
                project_key="validation-os",
                display_name="validation-os",
                repo_root=str(repo_root),
            )
            service.register_bot(
                bot_identity="BentoBoxThreeBot",
                default_display_name="validation-os",
                telegram_chat_id=101,
            )
            service.register_bot(
                bot_identity="BerryRamenBot",
                default_display_name="create-t3-jian",
                telegram_chat_id=102,
            )
            mirror.upsert_issue(
                linear_id="lin-validation-1",
                identifier="PRO-645",
                title="Stay on validation lane",
                description="Disposable wrong-bot ingress proof",
                team_id="team-1",
                team_name="Projects",
                state_name="Todo",
                state_type="unstarted",
                priority=1,
                project_id="project-validation",
                project_name="validation-os",
                source_updated_at="2026-04-17T19:18:00+00:00",
                metadata={"project_key": "validation-os", "worktree_path": str(repo_root)},
            )

            result = service.dispatch_run(
                ingress_bot="BerryRamenBot",
                explicit_project_key="validation-os",
                explicit_issue_key="PRO-645",
            )

            self.assertEqual(result.decision, "dispatched")
            self.assertEqual(result.assigned_bot, "BentoBoxThreeBot")
            self.assertEqual(result.owning_bot, "BentoBoxThreeBot")
            self.assertTrue(result.handoff_required)
            notifications = registry.list_pending_notifications(
                project_key="validation-os",
                owning_bot="BentoBoxThreeBot",
            )
            self.assertEqual(len(notifications), 1)
            self.assertEqual(notifications[0].target_bot, "BentoBoxThreeBot")
            self.assertEqual(notifications[0].ingress_bot, "BerryRamenBot")

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

    def test_dispatch_with_explicit_project_returns_already_running_for_active_lane(self) -> None:
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

            service.register_bot(
                bot_identity="BentoBoxThreeBot",
                default_display_name="validation-os",
                telegram_chat_id=101,
            )
            service.register_bot(
                bot_identity="BerryRamenBot",
                default_display_name="create-t3-jian",
                telegram_chat_id=102,
            )
            service.register_project(
                project_key="validation-os",
                display_name="validation-os",
                repo_root="/tmp/validation-os",
                metadata={"linear_team_id": "team-validation"},
            )
            mirror.upsert_issue(
                linear_id="lin-validation-active",
                identifier="PRO-621",
                title="Validation task",
                description="Already in progress",
                team_id="team-1",
                team_name="Projects",
                state_name="Todo",
                state_type="unstarted",
                priority=1,
                project_id="project-validation",
                project_name="validation-os",
                source_updated_at="2026-04-16T12:00:00+00:00",
                metadata={"project_key": "validation-os"},
            )

            first = service.dispatch_run(
                ingress_bot="BerryRamenBot",
                explicit_project_key="validation-os",
                explicit_issue_key="PRO-621",
            )
            notifications_before = registry.list_pending_notifications(
                project_key="validation-os",
                owning_bot="BentoBoxThreeBot",
            )
            second = service.dispatch_run(
                ingress_bot="observer_bot",
                explicit_project_key="validation-os",
            )
            notifications_after = registry.list_pending_notifications(
                project_key="validation-os",
                owning_bot="BentoBoxThreeBot",
            )

            self.assertEqual(first.decision, "dispatched")
            self.assertEqual(second.decision, "already-running")
            self.assertEqual(second.project_key, "validation-os")
            self.assertEqual(second.issue_key, "PRO-621")
            self.assertTrue(second.handoff_required)
            self.assertEqual(second.assigned_bot, "BentoBoxThreeBot")
            self.assertEqual(second.assignment_action, "active")
            self.assertIsNone(second.notification_id)
            self.assertIsNone(second.runtime)
            self.assertEqual(len(notifications_before), 1)
            self.assertEqual(len(notifications_after), 1)

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
            self.assertIsNone(runtime.store.get_session("main"))
            self.assertFalse(transport.has_session("runner-alpha"))
            restart_context = service.build_restart_context(project_key="alpha")
            self.assertEqual(restart_context["start_state"], "runnable")
            self.assertIsNone(restart_context["runtime"]["session"])

            drained = service.drain_projects()
            self.assertEqual(len(drained), 1)
            self.assertEqual(drained[0].project_key, "alpha")
            self.assertEqual(drained[0].issue_key, "PRO-700")
            self.assertEqual(drained[0].action, "continued")

            continued_state = runtime.continuity.get_state("PRO-700", "main")
            self.assertIsNotNone(continued_state)
            self.assertIsNotNone(continued_state.active_slice_id)
            self.assertEqual(continued_state.resume_context["project_key"], "alpha")
            continued_request = runtime.store.get_slice_request(continued_state.active_slice_id)  # type: ignore[arg-type]
            self.assertIsNotNone(continued_request)
            self.assertEqual(
                continued_request.request["slice_goal"],
                "Finish the remaining alpha work",
            )

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
            self.assertEqual(next_batch, [])
            registration = registry.get_project("alpha")
            self.assertIsNotNone(registration)
            assert registration is not None
            self.assertEqual(
                registration.metadata["feature_lane"]["lane_state"],
                "awaiting_hil_release",
            )
            self.assertEqual(
                registration.metadata["feature_lane"]["feature_key"],
                "PRO-700",
            )
            self.assertTrue(registration.metadata["feature_lane"]["release_required"])

            completed_issue = mirror.get_issue(identifier="PRO-700")
            self.assertIsNotNone(completed_issue)
            self.assertEqual(completed_issue.state_type, "completed")
            self.assertIsNotNone(completed_issue.completed_at)

    def test_drain_projects_continues_same_feature_without_releasing_lane(self) -> None:
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
            )
            for identifier, title, priority in (
                ("PRO-720", "Alpha packet first", 1),
                ("PRO-721", "Alpha packet second", 2),
            ):
                mirror.upsert_issue(
                    linear_id=f"lin-{identifier.lower()}",
                    identifier=identifier,
                    title=title,
                    description=title,
                    team_id="team-1",
                    team_name="Projects",
                    state_name="Todo",
                    state_type="unstarted",
                    priority=priority,
                    project_id="project-alpha",
                    project_name="Alpha",
                    source_updated_at=f"2026-04-16T12:0{priority}:00+00:00",
                    metadata={"project_key": "alpha", "packet_key": "FEATURE-ALPHA"},
                )

            first = service.dispatch_run(ingress_bot="alpha_bot")
            self.assertEqual(first.issue_key, "PRO-720")
            registration = registry.get_project("alpha")
            self.assertIsNotNone(registration)
            runtime = service._runtime_service(registration)  # type: ignore[arg-type]
            continuity = runtime.continuity.get_state("PRO-720", "main")
            self.assertIsNotNone(continuity)
            finalized = service.submit_slice_result(
                project_key="alpha",
                slice_id=continuity.active_slice_id,  # type: ignore[arg-type]
                payload={
                    "status": "success",
                    "summary": "First feature ticket complete",
                    "verified": True,
                    "next_slice": None,
                    "artifacts": ["proof"],
                    "metrics": {"step": 1},
                },
            )

            self.assertTrue(finalized.finalized)
            next_batch = service.drain_projects()
            self.assertEqual(len(next_batch), 1)
            self.assertEqual(next_batch[0].issue_key, "PRO-721")
            registration = registry.get_project("alpha")
            self.assertIsNotNone(registration)
            assert registration is not None
            self.assertEqual(
                registration.metadata["feature_lane"]["feature_key"],
                "FEATURE-ALPHA",
            )
            self.assertEqual(
                registration.metadata["feature_lane"]["lane_state"],
                "executing",
            )

    def test_release_feature_lane_clears_reservation_and_releases_bot(self) -> None:
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
                owner_chat_id=101,
            )
            mirror.upsert_issue(
                linear_id="lin-alpha-release",
                identifier="PRO-730",
                title="Feature release",
                description="Finish and wait for HIL",
                team_id="team-1",
                team_name="Projects",
                state_name="Done",
                state_type="completed",
                priority=1,
                project_id="project-alpha",
                project_name="Alpha",
                source_updated_at="2026-04-16T12:10:00+00:00",
                completed_at="2026-04-16T12:12:00+00:00",
                metadata={"project_key": "alpha", "packet_key": "FEATURE-RELEASE"},
            )
            registry.assign_project_bot(project_key="alpha", preferred_bot="alpha_bot")
            registry.set_project_feature_lane(
                project_key="alpha",
                lane={
                    "feature_key": "FEATURE-RELEASE",
                    "packet_key": "FEATURE-RELEASE",
                    "lane_state": "awaiting_hil_release",
                    "release_required": True,
                    "last_issue_key": "PRO-730",
                    "last_issue_title": "Feature release",
                    "merge_target": "main",
                    "merge_strategy": "hil_merge_to_main",
                },
            )

            released = service.release_feature_lane(
                project_key="alpha",
                action="merge_to_main_and_release",
                note="Merged by HIL.",
            )

            self.assertTrue(released["released"])
            self.assertIsNone(registry.get_project("alpha").assigned_bot)
            self.assertIsNone(registry.get_project_feature_lane("alpha"))

    def test_recover_failed_start_clears_lane_and_releases_bot(self) -> None:
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

            service.register_project(
                project_key="alpha",
                display_name="Alpha",
                repo_root="/tmp/alpha",
                owning_bot="alpha_bot",
                owner_chat_id=101,
            )
            registry.assign_project_bot(project_key="alpha", preferred_bot="alpha_bot")
            registry.set_project_feature_lane(
                project_key="alpha",
                lane={
                    "feature_key": "FEATURE-FAILED",
                    "packet_key": "FEATURE-FAILED",
                    "lane_state": "launch_failed",
                    "release_required": False,
                    "last_issue_key": "PRO-731",
                    "last_issue_title": "Failed start",
                    "merge_target": "main",
                    "merge_strategy": "hil_merge_to_main",
                },
            )

            registration = registry.get_project("alpha")
            self.assertIsNotNone(registration)
            runtime = service._runtime_service(registration)  # type: ignore[arg-type]
            runtime.repository.upsert_runner(
                "main",
                transport="tmux-codex",
                display_name="Alpha main",
                state="ready",
            )
            runtime.repository.acquire_issue_lease("PRO-731", "main")
            runtime.store.upsert_session(
                runner_id="main",
                issue_key="PRO-731",
                session_name="runner-alpha",
                pane_target="%1",
                transport="tmux-codex",
                state="claimed",
            )

            recovered = service.recover_failed_start(project_key="alpha")

            self.assertTrue(recovered["recovered"])
            self.assertEqual(runtime.repository.list_active_leases(runner_id="main"), [])
            self.assertIsNone(runtime.store.get_session("main"))
            self.assertIsNone(registry.get_project("alpha").assigned_bot)
            self.assertIsNone(registry.get_project_feature_lane("alpha"))

    def test_runner_event_parks_lane_for_orx_reconciliation(self) -> None:
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

            repo_root = Path(temp_dir) / "alpha-repo"
            repo_root.mkdir(parents=True, exist_ok=True)
            service.register_project(
                project_key="alpha",
                display_name="Alpha",
                repo_root=str(repo_root),
                owning_bot="alpha_bot",
            )
            mirror.upsert_issue(
                linear_id="lin-alpha-review",
                identifier="PRO-740",
                title="Needs review",
                description="Runner stalled before handoff",
                team_id="team-1",
                team_name="Projects",
                state_name="Todo",
                state_type="unstarted",
                priority=1,
                project_id="project-alpha",
                project_name="Alpha",
                source_updated_at="2026-04-16T12:13:00+00:00",
                metadata={"project_key": "alpha", "worktree_path": str(repo_root)},
            )

            dispatched = service.dispatch_run(ingress_bot="alpha_bot")
            self.assertEqual(dispatched.decision, "dispatched")
            runtime = service._runtime_service(registry.get_project("alpha"))  # type: ignore[arg-type]

            parked = service.submit_runner_event(
                project_key="alpha",
                event_kind="result_missing",
                final_summary="Codex stopped after hitting a blocker.",
                transcript_excerpt="blocked on missing migration context",
                reason="missing RUNNER_RESULT block",
            )

            self.assertTrue(parked["ok"])
            self.assertEqual(parked["feature_lane"]["lane_state"], "awaiting_orx_review")
            self.assertEqual(parked["reconciliation"]["status"], "awaiting_orx_review")
            self.assertEqual(parked["reconciliation"]["action"], "blocked")
            self.assertFalse(parked["has_active_slice"])
            self.assertIsNone(parked["active_slice_id"])
            restart = service.build_restart_context(project_key="alpha")
            self.assertEqual(restart["start_state"], "awaiting_orx_review")
            refreshed = runtime.continuity.get_state("PRO-740", "main")
            self.assertIsNotNone(refreshed)
            assert refreshed is not None
            self.assertIsNone(refreshed.active_slice_id)
            self.assertEqual(refreshed.last_result_status, "failed")
            self.assertEqual(refreshed.resume_context["interpreted_action"], "blocked")
            mirrored = mirror.get_issue(identifier="PRO-740")
            self.assertIsNotNone(mirrored)
            assert mirrored is not None
            self.assertIn("## Latest Handoff", mirrored.description)
            self.assertIn("Codex stopped after hitting a blocker.", mirrored.description)
            self.assertEqual(runtime.repository.list_active_leases(runner_id="main"), [])
            self.assertIsNone(runtime.store.get_session("main"))
            self.assertEqual(service.drain_projects(), [])

    def test_submit_slice_result_routes_visual_work_to_design_review(self) -> None:
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

            repo_root = Path(temp_dir) / "alpha-repo"
            repo_root.mkdir(parents=True, exist_ok=True)
            service.register_project(
                project_key="alpha",
                display_name="Alpha",
                repo_root=str(repo_root),
                owning_bot="alpha_bot",
            )
            mirror.upsert_issue(
                linear_id="lin-alpha-visual-review",
                identifier="PRO-741",
                title="Redesign the dashboard layout",
                description="Create a cleaner visual hierarchy for the dashboard.",
                team_id="team-1",
                team_name="Projects",
                state_name="Todo",
                state_type="unstarted",
                priority=1,
                project_id="project-alpha",
                project_name="Alpha",
                source_updated_at="2026-04-16T12:14:00+00:00",
                metadata={"project_key": "alpha", "worktree_path": str(repo_root)},
            )

            dispatched = service.dispatch_run(ingress_bot="alpha_bot")
            self.assertEqual(dispatched.decision, "dispatched")
            runtime = service._runtime_service(registry.get_project("alpha"))  # type: ignore[arg-type]
            continuity = runtime.continuity.get_state("PRO-741", "main")
            self.assertIsNotNone(continuity)

            reviewed = service.submit_slice_result(
                project_key="alpha",
                slice_id=continuity.active_slice_id,  # type: ignore[arg-type]
                payload={
                    "status": "success",
                    "summary": "Prepared the design direction and captured Stitch artifacts.",
                    "verified": False,
                    "next_slice": None,
                    "artifacts": [".codex/stitch/run-1/STYLE.md"],
                    "design_artifacts": [".codex/stitch/run-1/DESIGN.md"],
                    "design_reference": ".codex/stitch/run-1/DESIGN.md",
                    "design_review_requested": True,
                    "verification_surface": "none",
                    "metrics": {"step": 1},
                },
            )

            self.assertEqual(reviewed.status, "awaiting_orx_review")
            self.assertFalse(reviewed.finalized)
            registration = registry.get_project("alpha")
            self.assertIsNotNone(registration)
            assert registration is not None
            self.assertEqual(registration.metadata["feature_lane"]["lane_state"], "awaiting_orx_review")
            self.assertEqual(registration.metadata["feature_lane"]["release_action"], "design_review_required")
            self.assertEqual(registration.metadata["reconciliation"]["review_kind"], "design_review_required")
            self.assertEqual(registration.metadata["reconciliation"]["design_state"], "pending")

            resumed = service.resume_reviewed_lane(
                project_key="alpha",
                next_slice="Implement the approved dashboard design and verify it with Playwright.",
            )

            self.assertTrue(resumed["resumed"])
            refreshed = runtime.continuity.get_state("PRO-741", "main")
            self.assertIsNotNone(refreshed)
            assert refreshed is not None
            self.assertEqual(refreshed.resume_context["design_state"], "approved")
            self.assertTrue(refreshed.resume_context["ui_evidence_required"])
            self.assertEqual(refreshed.resume_context["design_reference"], ".codex/stitch/run-1/DESIGN.md")

    def test_submit_slice_result_blocks_ui_logic_closeout_without_playwright(self) -> None:
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

            repo_root = Path(temp_dir) / "alpha-repo"
            repo_root.mkdir(parents=True, exist_ok=True)
            (repo_root / "ui.txt").write_text("before\n", encoding="utf-8")
            service.register_project(
                project_key="alpha",
                display_name="Alpha",
                repo_root=str(repo_root),
                owning_bot="alpha_bot",
            )
            mirror.upsert_issue(
                linear_id="lin-alpha-ui-logic",
                identifier="PRO-743",
                title="Fix modal submit validation bug",
                description="The submit button state is wrong after validation fails.",
                team_id="team-1",
                team_name="Projects",
                state_name="Todo",
                state_type="unstarted",
                priority=1,
                project_id="project-alpha",
                project_name="Alpha",
                source_updated_at="2026-04-16T12:16:00+00:00",
                metadata={"project_key": "alpha", "worktree_path": str(repo_root)},
            )

            dispatched = service.dispatch_run(ingress_bot="alpha_bot")
            self.assertEqual(dispatched.decision, "dispatched")
            runtime = service._runtime_service(registry.get_project("alpha"))  # type: ignore[arg-type]
            continuity = runtime.continuity.get_state("PRO-743", "main")
            self.assertIsNotNone(continuity)
            (repo_root / "ui.txt").write_text("after\n", encoding="utf-8")

            blocked = service.submit_slice_result(
                project_key="alpha",
                slice_id=continuity.active_slice_id,  # type: ignore[arg-type]
                payload={
                    "status": "success",
                    "summary": "Fixed the modal submit behavior.",
                    "verified": True,
                    "next_slice": None,
                    "artifacts": ["ui.txt"],
                    "verification_ran": ["pnpm test"],
                    "verification_surface": "cli",
                    "metrics": {"step": 1},
                },
            )

            self.assertEqual(blocked.status, "awaiting_orx_review")
            self.assertFalse(blocked.finalized)
            self.assertFalse(blocked.linear_completed)
            registration = registry.get_project("alpha")
            self.assertIsNotNone(registration)
            assert registration is not None
            self.assertEqual(registration.metadata["reconciliation"]["review_kind"], "ui_evidence_missing")
            self.assertEqual(registration.metadata["reconciliation"]["verification_surface"], "cli")
            mirrored = mirror.get_issue(identifier="PRO-743")
            self.assertIsNotNone(mirrored)
            assert mirrored is not None
            self.assertEqual(mirrored.state_type, "unstarted")

    def test_runner_event_ignores_stale_terminal_signal_after_feature_completion(self) -> None:
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

            repo_root = Path(temp_dir) / "alpha-repo"
            repo_root.mkdir(parents=True, exist_ok=True)
            service.register_project(
                project_key="alpha",
                display_name="Alpha",
                repo_root=str(repo_root),
                owning_bot="alpha_bot",
            )
            issue = mirror.upsert_issue(
                linear_id="lin-alpha-complete",
                identifier="PRO-744",
                title="Feature already completed",
                description="Late runner events should be ignored once the lane is waiting for release.",
                team_id="team-1",
                team_name="Projects",
                state_name="Done",
                state_type="completed",
                priority=1,
                project_id="project-alpha",
                project_name="Alpha",
                source_updated_at="2026-04-16T12:17:00+00:00",
                metadata={"project_key": "alpha", "worktree_path": str(repo_root)},
                completed_at="2026-04-16T12:18:00+00:00",
            )
            registry.set_project_feature_lane(
                project_key="alpha",
                lane={
                    "feature_key": issue.identifier,
                    "packet_key": issue.identifier,
                    "lane_state": "awaiting_hil_release",
                    "release_required": True,
                    "last_issue_key": issue.identifier,
                    "last_issue_title": issue.title,
                    "merge_target": "main",
                    "merge_strategy": "hil_merge_to_main",
                    "release_action": None,
                    "release_note": None,
                    "updated_at": "2026-04-16T12:18:00+00:00",
                },
            )

            event = service.submit_runner_event(
                project_key="alpha",
                event_kind="interrupted",
                issue_key="PRO-744",
                final_summary="Late cleanup event should not reopen review.",
                reason="stale late runner event",
            )

            self.assertTrue(event["ok"])
            self.assertTrue(event["ignored"])
            self.assertEqual(event["reason"], "feature_already_completed")
            self.assertEqual(event["feature_lane"]["lane_state"], "awaiting_hil_release")
            self.assertIsNone(event["reconciliation"])
            mirrored = mirror.get_issue(identifier="PRO-744")
            self.assertIsNotNone(mirrored)
            assert mirrored is not None
            self.assertEqual(mirrored.completed_at, "2026-04-16T12:18:00+00:00")
            self.assertNotIn("Latest Handoff", mirrored.description)

    def test_resume_reviewed_lane_continues_same_issue_with_new_slice_goal(self) -> None:
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

            repo_root = Path(temp_dir) / "alpha-repo"
            repo_root.mkdir(parents=True, exist_ok=True)
            service.register_project(
                project_key="alpha",
                display_name="Alpha",
                repo_root=str(repo_root),
                owning_bot="alpha_bot",
            )
            mirror.upsert_issue(
                linear_id="lin-alpha-review-resume",
                identifier="PRO-742",
                title="Resume after ORX review",
                description="Runner stalled before a structured result",
                team_id="team-1",
                team_name="Projects",
                state_name="Todo",
                state_type="unstarted",
                priority=1,
                project_id="project-alpha",
                project_name="Alpha",
                source_updated_at="2026-04-16T12:15:00+00:00",
                metadata={"project_key": "alpha", "worktree_path": str(repo_root)},
            )

            dispatched = service.dispatch_run(ingress_bot="alpha_bot")
            self.assertEqual(dispatched.decision, "dispatched")
            runtime = service._runtime_service(registry.get_project("alpha"))  # type: ignore[arg-type]
            parked = service.submit_runner_event(
                project_key="alpha",
                event_kind="result_missing",
                issue_key="PRO-742",
                final_summary="Stopped before the final structured result.",
                transcript_excerpt="The missing context is now available.",
                reason="missing RUNNER_RESULT block",
            )
            self.assertTrue(parked["ok"])

            resumed = service.resume_reviewed_lane(
                project_key="alpha",
                next_slice="Retry with the missing context filled in.",
            )

            self.assertTrue(resumed["ok"])
            self.assertTrue(resumed["resumed"])
            self.assertEqual(resumed["feature_lane"]["lane_state"], "executing")
            self.assertIsNone(resumed["reconciliation"])
            status = service.control_status(project_key="alpha")
            execution_packet = status["restart_context"]["execution_packet"]
            self.assertIsInstance(execution_packet, dict)
            assert isinstance(execution_packet, dict)
            execution_brief = execution_packet.get("execution_brief")
            self.assertIsInstance(execution_brief, dict)
            assert isinstance(execution_brief, dict)
            self.assertEqual(execution_packet.get("owning_bot"), "alpha_bot")
            self.assertEqual(execution_packet.get("assigned_bot"), "alpha_bot")
            self.assertEqual(execution_packet.get("feature_lane", {}).get("lane_state"), "executing")
            self.assertEqual(
                execution_brief.get("goal"),
                "Retry with the missing context filled in.",
            )
            state = runtime.continuity.get_state("PRO-742", "main")
            self.assertIsNotNone(state)
            assert state is not None
            self.assertIsNotNone(state.active_slice_id)
            self.assertEqual(state.next_slice, "Retry with the missing context filled in.")
            self.assertEqual(runtime.active_issue_key(), "PRO-742")

    def test_dispatch_run_rolls_back_when_runner_never_becomes_durable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(resolve_runtime_paths(temp_dir))
            storage.bootstrap()
            registry = ProjectRegistry(storage)
            transport = FlakyTmuxTransport()
            transport.expire_session_after("runner-alpha", visible_checks=1)
            service = GlobalDispatchService(
                storage=storage,
                registry=registry,
                transport_factory=lambda: transport,
            )
            mirror = LinearMirrorRepository(storage)

            repo_root = Path(temp_dir) / "alpha-repo"
            repo_root.mkdir(parents=True, exist_ok=True)
            service.register_project(
                project_key="alpha",
                display_name="Alpha",
                repo_root=str(repo_root),
                owning_bot="alpha_bot",
            )
            mirror.upsert_issue(
                linear_id="lin-alpha-flaky",
                identifier="PRO-743",
                title="Start should fail closed",
                description="runner vanishes immediately",
                team_id="team-1",
                team_name="Projects",
                state_name="Todo",
                state_type="unstarted",
                priority=1,
                project_id="project-alpha",
                project_name="Alpha",
                source_updated_at="2026-04-16T12:16:00+00:00",
                metadata={"project_key": "alpha", "worktree_path": str(repo_root)},
            )

            result = service.dispatch_run(ingress_bot="alpha_bot")
            runtime = service._runtime_service(registry.get_project("alpha"))  # type: ignore[arg-type]

            self.assertEqual(result.decision, "launch-failed")
            self.assertEqual(result.lane_state, "launch_failed")
            self.assertEqual(runtime.repository.list_active_leases(runner_id="main"), [])
            self.assertIsNone(runtime.store.get_session("main"))
            self.assertIsNone(runtime.continuity.get_state("PRO-743", "main"))
            self.assertEqual(registry.get_project_feature_lane("alpha")["lane_state"], "launch_failed")

    def test_resume_reviewed_lane_restores_parked_state_when_runner_never_becomes_durable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(resolve_runtime_paths(temp_dir))
            storage.bootstrap()
            registry = ProjectRegistry(storage)
            transport = FlakyTmuxTransport()
            service = GlobalDispatchService(
                storage=storage,
                registry=registry,
                transport_factory=lambda: transport,
            )
            mirror = LinearMirrorRepository(storage)

            repo_root = Path(temp_dir) / "alpha-repo"
            repo_root.mkdir(parents=True, exist_ok=True)
            service.register_project(
                project_key="alpha",
                display_name="Alpha",
                repo_root=str(repo_root),
                owning_bot="alpha_bot",
            )
            mirror.upsert_issue(
                linear_id="lin-alpha-review-flaky",
                identifier="PRO-744",
                title="Resume should fail closed",
                description="runner vanishes during resume",
                team_id="team-1",
                team_name="Projects",
                state_name="Todo",
                state_type="unstarted",
                priority=1,
                project_id="project-alpha",
                project_name="Alpha",
                source_updated_at="2026-04-16T12:17:00+00:00",
                metadata={"project_key": "alpha", "worktree_path": str(repo_root)},
            )

            dispatched = service.dispatch_run(ingress_bot="alpha_bot")
            self.assertEqual(dispatched.decision, "dispatched")
            parked = service.submit_runner_event(
                project_key="alpha",
                event_kind="result_missing",
                issue_key="PRO-744",
                final_summary="Runner stopped before the final structured result.",
                reason="missing RUNNER_RESULT block",
            )
            self.assertTrue(parked["ok"])
            parked_state = service._runtime_service(registry.get_project("alpha")).continuity.get_state(  # type: ignore[arg-type]
                "PRO-744",
                "main",
            )
            self.assertIsNotNone(parked_state)
            assert parked_state is not None
            transport.expire_session_after("runner-alpha", visible_checks=1)

            resumed = service.resume_reviewed_lane(
                project_key="alpha",
                next_slice="Retry after ORX fills the missing context.",
            )
            runtime = service._runtime_service(registry.get_project("alpha"))  # type: ignore[arg-type]
            state = runtime.continuity.get_state("PRO-744", "main")

            self.assertTrue(resumed["ok"])
            self.assertFalse(resumed["resumed"])
            self.assertEqual(resumed["reason"], "runner_not_durable")
            self.assertEqual(resumed["feature_lane"]["lane_state"], "awaiting_orx_review")
            self.assertEqual(resumed["reconciliation"]["status"], "awaiting_orx_review")
            self.assertIsNotNone(state)
            assert state is not None
            self.assertIsNone(state.active_slice_id)
            self.assertEqual(state.next_slice, parked_state.next_slice)
            self.assertEqual(runtime.repository.list_active_leases(runner_id="main"), [])
            self.assertIsNone(runtime.store.get_session("main"))

    def test_resume_reviewed_lane_reclassifies_stale_ui_routing_from_current_issue(self) -> None:
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

            repo_root = Path(temp_dir) / "validation-os-repo"
            repo_root.mkdir(parents=True, exist_ok=True)
            service.register_project(
                project_key="validation-os",
                display_name="validation-os",
                repo_root=str(repo_root),
                owning_bot="BentoBoxThreeBot",
            )
            mirror.upsert_issue(
                linear_id="lin-validation-review-stale-ui",
                identifier="PRO-745",
                title="Disposable wrong-bot ingress proof on validation-os",
                description="Confirm the control-plane lane stayed on the assigned bot.",
                team_id="team-1",
                team_name="Projects",
                state_name="Todo",
                state_type="unstarted",
                priority=1,
                project_id="project-validation-os",
                project_name="validation-os",
                source_updated_at="2026-04-16T12:18:00+00:00",
                metadata={"project_key": "validation-os", "worktree_path": str(repo_root)},
            )

            dispatched = service.dispatch_run(ingress_bot="BentoBoxThreeBot")
            self.assertEqual(dispatched.decision, "dispatched")
            runtime = service._runtime_service(registry.get_project("validation-os"))  # type: ignore[arg-type]
            parked = service.submit_runner_event(
                project_key="validation-os",
                event_kind="result_missing",
                issue_key="PRO-745",
                final_summary="Stopped before the final structured result.",
                reason="missing RUNNER_RESULT block",
            )
            self.assertTrue(parked["ok"])
            runtime.continuity.apply_handoff_interpretation(
                issue_key="PRO-745",
                runner_id="main",
                next_slice="Retry with the fresh lane proof.",
                resume_context_updates={
                    "ui_mode": "logic",
                    "design_state": "none",
                    "ui_evidence_required": True,
                },
            )

            resumed = service.resume_reviewed_lane(
                project_key="validation-os",
                next_slice="Retry with the fresh lane proof.",
            )

            self.assertTrue(resumed["ok"])
            self.assertTrue(resumed["resumed"])
            status = service.control_status(project_key="validation-os")
            execution_packet = status["restart_context"]["execution_packet"]
            self.assertEqual(execution_packet["ui_mode"], "none")
            self.assertFalse(execution_packet["ui_evidence_required"])

    def test_finalized_slice_records_checkpoint_commit_when_packet_worktree_is_git_repo(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir) / "alpha-repo"
            repo_root.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "-C", str(repo_root), "init"], check=True, capture_output=True, text=True)
            (repo_root / "feature.txt").write_text("initial\n", encoding="utf-8")
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo_root),
                    "-c",
                    "user.name=Tests",
                    "-c",
                    "user.email=tests@example.com",
                    "add",
                    "-A",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo_root),
                    "-c",
                    "user.name=Tests",
                    "-c",
                    "user.email=tests@example.com",
                    "commit",
                    "-m",
                    "initial",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            baseline_head = subprocess.run(
                ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

            storage = Storage(resolve_runtime_paths(temp_dir))
            storage.bootstrap()
            registry = ProjectRegistry(storage)
            linear = FakeLinearCompleteClient()
            service = GlobalDispatchService(
                storage=storage,
                registry=registry,
                linear_client=linear,  # type: ignore[arg-type]
                transport_factory=FakeTmuxTransport,
            )
            mirror = LinearMirrorRepository(storage)

            service.register_project(
                project_key="alpha",
                display_name="Alpha",
                repo_root=str(repo_root),
                owning_bot="alpha_bot",
            )
            mirror.upsert_issue(
                linear_id="lin-alpha-checkpoint",
                identifier="PRO-741",
                title="Checkpoint ticket",
                description="Create a checkpoint commit when complete",
                team_id="team-1",
                team_name="Projects",
                state_name="Todo",
                state_type="unstarted",
                priority=1,
                project_id="project-alpha",
                project_name="Alpha",
                source_updated_at="2026-04-16T12:14:00+00:00",
                metadata={"project_key": "alpha", "worktree_path": str(repo_root)},
            )

            dispatched = service.dispatch_run(ingress_bot="alpha_bot")
            self.assertEqual(dispatched.decision, "dispatched")
            runtime = service._runtime_service(registry.get_project("alpha"))  # type: ignore[arg-type]
            active = runtime.continuity.get_state("PRO-741", "main")
            self.assertIsNotNone(active)
            (repo_root / "feature.txt").write_text("initial\ncheckpointed\n", encoding="utf-8")

            finalized = service.submit_slice_result(
                project_key="alpha",
                slice_id=active.active_slice_id,  # type: ignore[arg-type]
                payload={
                    "status": "success",
                    "summary": "Ticket complete with checkpoint",
                    "verified": True,
                    "next_slice": None,
                    "artifacts": ["feature.txt"],
                    "metrics": {"step": 1},
                },
            )

            self.assertTrue(finalized.finalized)
            registration = registry.get_project("alpha")
            self.assertIsNotNone(registration)
            assert registration is not None
            reconciliation = registration.metadata.get("reconciliation")
            self.assertIsInstance(reconciliation, dict)
            checkpoint_commit = reconciliation.get("checkpoint_commit")
            self.assertIsInstance(checkpoint_commit, str)
            self.assertNotEqual(checkpoint_commit, baseline_head)

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

            with patch.dict(os.environ, {"DEV": temp_dir}, clear=False):
                dispatched = service.dispatch_run(ingress_bot="alpha_bot")
                context = service.build_restart_context(project_key="alpha")

            self.assertEqual(dispatched.project_key, "alpha")
            self.assertEqual(context["project"]["project_key"], "alpha")
            self.assertEqual(context["runtime"]["active_issue_key"], "PRO-710")
            self.assertEqual(context["start_state"], "already_running")
            self.assertIn("Attach to the existing managed runner", context["remediation"])
            self.assertEqual(context["issue"]["identifier"], "PRO-710")
            self.assertEqual(context["continuity"]["issue_key"], "PRO-710")
            self.assertEqual(context["continuity"]["resume_context"]["project_key"], "alpha")
            self.assertEqual(context["execution_packet"]["issue_key"], "PRO-710")
            self.assertEqual(context["execution_packet"]["active_slice_id"], context["continuity"]["active_slice_id"])
            self.assertEqual(context["execution_packet"]["continuity_revision"], context["continuity"]["updated_at"])
            self.assertEqual(context["execution_packet"]["decision_epoch"], context["continuity"]["active_slice_id"])
            self.assertEqual(context["execution_packet"]["owning_bot"], "alpha_bot")
            self.assertEqual(context["execution_packet"]["assigned_bot"], "alpha_bot")
            self.assertEqual(context["execution_packet"]["feature_lane"]["lane_state"], "executing")
            self.assertIsNotNone(context["execution_packet"]["packet_revision"])
            expected_worktree = str(Path(temp_dir).resolve() / "worktrees" / "alpha" / "pro-710")
            self.assertEqual(context["execution_packet"]["worktree_path"], expected_worktree)
            self.assertEqual(context["execution_packet"]["branch"], "linear/pro-710")
            self.assertIsInstance(context["execution_packet"]["execution_brief"], dict)
            execution_brief = context["execution_packet"]["execution_brief"]
            assert isinstance(execution_brief, dict)
            self.assertEqual(
                execution_brief["success_criteria"],
                ["Persist enough to resume after a crash"],
            )
            self.assertEqual(
                execution_brief["verification"],
                ["Confirm PRO-710 remains on the intended ORX project runtime."],
            )
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
            self.assertEqual(context["start_state"], "runnable")
            self.assertIn("valid ORX execution packet", context["remediation"])
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

            self.assertEqual(context["start_state"], "drift_blocked")
            self.assertIn("Repair ORX drift", context["remediation"])
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
