from __future__ import annotations

import tempfile
import unittest

from orx.config import resolve_runtime_paths
from orx.executor import ExecutorService, SliceApplyGate, SliceResultValidationError
from orx.ownership import OwnershipService
from orx.repository import OrxRepository
from orx.storage import Storage


class FakeTmuxTransport:
    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, object]] = {}
        self.sent: list[tuple[str, str]] = []

    def has_session(self, name: str) -> bool:
        return name in self.sessions

    def create_session(self, name: str, cmd: str) -> str:
        self.sessions[name] = {"cmd": cmd, "pane": f"%{len(self.sessions) + 1}"}
        return self.sessions[name]["pane"]  # type: ignore[return-value]

    def send_keys(self, session: str, text: str, *, enter: bool = True) -> bool:
        self.sent.append((session, text))
        return True

    def capture_pane(self, session: str, *, lines: int = 50) -> str:
        return f"capture:{session}:{lines}"

    def list_panes(self, session: str) -> list[str]:
        if session not in self.sessions:
            return []
        return [self.sessions[session]["pane"]]  # type: ignore[list-item]


class FakeRunnerLauncher:
    def __init__(self, transport: FakeTmuxTransport) -> None:
        self.transport = transport
        self.launches: list[tuple[str, str | None, str]] = []

    def ensure_session(
        self,
        *,
        project_key: str,
        repo_root: str | None,
        runner_id: str,
    ) -> tuple[str, str]:
        session_name = f"runner-{project_key}"
        self.launches.append((project_key, repo_root, runner_id))
        if not self.transport.has_session(session_name):
            pane_target = self.transport.create_session(
                session_name,
                f"runner-loop {project_key} {repo_root or ''}".strip(),
            )
            return session_name, pane_target
        panes = self.transport.list_panes(session_name)
        return session_name, panes[0] if panes else f"{session_name}:0.0"


class ExecutorServiceTests(unittest.TestCase):
    def test_claim_dispatch_heartbeat_and_attach_use_same_tmux_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(resolve_runtime_paths(temp_dir))
            storage.bootstrap()
            repository = OrxRepository(storage)
            repository.upsert_runner(
                "runner-a",
                transport="tmux-codex",
                display_name="Runner A",
                state="idle",
                metadata={"protected_scopes": ["tmux:workspace:/Users/jian/Dev/workspace/orx"]},
            )
            transport = FakeTmuxTransport()
            launcher = FakeRunnerLauncher(transport)
            service = ExecutorService(
                storage=storage,
                repository=repository,
                ownership=OwnershipService(repository),
                transport=transport,
                runner_launcher=launcher,
            )

            first = service.claim_session("PRO-9", "runner-a")
            second = service.claim_session("PRO-9", "runner-a")
            self.assertEqual(first.session_name, second.session_name)
            self.assertEqual(first.session_name, "runner-runner-a")

            heartbeat = service.heartbeat("runner-a")
            self.assertEqual(heartbeat.state, "active")
            self.assertEqual(service.attach_target("runner-a"), first.session_name)
            self.assertIn("capture:", service.view_pane("runner-a"))

            request = service.dispatch_slice(
                issue_key="PRO-9",
                runner_id="runner-a",
                objective="Implement executor contract",
                acceptance=["tmux session", "slice result"],
                context={"repo_root": "/tmp/project"},
            )
            self.assertEqual(request.issue_key, "PRO-9")
            self.assertEqual(request.status, "dispatched")
            self.assertEqual(transport.sent, [])
            session_cmd = transport.sessions[first.session_name]["cmd"]
            self.assertIn("runner-loop runner-a", str(session_cmd))
            self.assertEqual(launcher.launches, [("runner-a", None, "runner-a")])

    def test_dispatch_slice_creates_codex_session_in_repo_root_when_claim_happens_inline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(resolve_runtime_paths(temp_dir))
            storage.bootstrap()
            repository = OrxRepository(storage)
            repository.upsert_runner(
                "runner-b",
                transport="tmux-codex",
                display_name="Runner B",
                state="idle",
            )
            transport = FakeTmuxTransport()
            launcher = FakeRunnerLauncher(transport)
            service = ExecutorService(
                storage=storage,
                repository=repository,
                ownership=OwnershipService(repository),
                transport=transport,
                runner_launcher=launcher,
            )

            request = service.dispatch_slice(
                issue_key="PRO-10",
                runner_id="runner-b",
                objective="Bootstrap codex in repo root",
                acceptance=["codex session is ready"],
                context={"repo_root": "/tmp/project"},
            )

            session_cmd = transport.sessions[request.session_name]["cmd"]
            self.assertEqual(request.session_name, "runner-runner-b")
            self.assertIn("/tmp/project", str(session_cmd))
            self.assertEqual(transport.sent, [])

    def test_submit_slice_result_requires_structured_payload(self) -> None:
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
            transport = FakeTmuxTransport()
            service = ExecutorService(
                storage=storage,
                repository=repository,
                ownership=OwnershipService(repository),
                transport=transport,
                runner_launcher=FakeRunnerLauncher(transport),
            )
            request = service.dispatch_slice(
                issue_key="PRO-9",
                runner_id="runner-a",
                objective="Implement executor contract",
                acceptance=["tmux session", "slice result"],
            )

            with self.assertRaises(SliceResultValidationError):
                service.submit_slice_result(
                    request.slice_id,
                    {"status": "success", "summary": "done", "verified": True},
                )

            result = service.submit_slice_result(
                request.slice_id,
                {
                    "status": "success",
                    "summary": "Executor contract landed",
                    "verified": True,
                    "next_slice": "Add continuity persistence",
                    "artifacts": ["tests/test_executor.py"],
                    "metrics": {"files_changed": 2},
                },
            )
            self.assertEqual(result.status, "success")
            self.assertEqual(result.artifacts, ("tests/test_executor.py",))

    def test_submit_slice_result_marks_duplicate_payload_as_duplicate_ignored(self) -> None:
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
            transport = FakeTmuxTransport()
            service = ExecutorService(
                storage=storage,
                repository=repository,
                ownership=OwnershipService(repository),
                transport=transport,
                runner_launcher=FakeRunnerLauncher(transport),
            )
            request = service.dispatch_slice(
                issue_key="PRO-10",
                runner_id="runner-a",
                objective="Implement duplicate safety",
                acceptance=["one authoritative result"],
            )
            payload = {
                "status": "success",
                "summary": "Authoritative result",
                "verified": True,
                "next_slice": None,
                "artifacts": ["tests/test_executor.py"],
                "metrics": {"source": "duplicate"},
            }

            first = service.submit_slice_result(request.slice_id, payload)
            second = service.submit_slice_result(request.slice_id, payload)

            self.assertEqual(first.apply_status, "applied")
            self.assertEqual(second.apply_status, "duplicate_ignored")
            self.assertEqual(second.stale_reason, "duplicate_payload")

    def test_submit_slice_result_marks_mismatched_gate_as_stale_audit_only(self) -> None:
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
            transport = FakeTmuxTransport()
            service = ExecutorService(
                storage=storage,
                repository=repository,
                ownership=OwnershipService(repository),
                transport=transport,
                runner_launcher=FakeRunnerLauncher(transport),
            )
            request = service.dispatch_slice(
                issue_key="PRO-11",
                runner_id="runner-a",
                objective="Implement stale audit",
                acceptance=["stale result does not clear continuity"],
            )
            gate = SliceApplyGate(
                expected_issue_key="PRO-11",
                expected_active_slice_id=request.slice_id,
                expected_packet_revision="packet-a",
            )

            result = service.submit_slice_result(
                request.slice_id,
                {
                    "status": "success",
                    "summary": "Late result",
                    "verified": True,
                    "next_slice": None,
                    "artifacts": ["tests/test_executor.py"],
                    "metrics": {"source": "stale"},
                    "packet_revision": "packet-b",
                },
                gate=gate,
            )

            self.assertEqual(result.apply_status, "stale_audit_only")
            self.assertEqual(result.stale_reason, "packet_revision_mismatch")
            continuity = service.continuity.get_state("PRO-11", "runner-a")
            self.assertIsNotNone(continuity)
            self.assertEqual(continuity.active_slice_id, request.slice_id)
