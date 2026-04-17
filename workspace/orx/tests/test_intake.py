from __future__ import annotations

import tempfile
import unittest

from orx.config import resolve_runtime_paths
from orx.intake import IntakeService
from orx.mirror import LinearMirrorRepository
from orx.registry import ProjectRegistry
from orx.storage import Storage


class IntakeServiceTests(unittest.TestCase):
    def test_submit_defaults_to_receiving_bot_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service, _client = _intake_fixture(temp_dir)

            record = service.submit(
                request_text="capture a better operator audit trail",
                ingress_bot="alpha_bot",
            )

            self.assertEqual(record.status, "pending_approval")
            self.assertEqual(record.default_project_key, "alpha")
            self.assertFalse(record.plan["needs_clarification"])
            self.assertEqual(record.planning_stage, "planning")
            self.assertEqual(record.planning_model, "gpt-5.4")
            self.assertEqual(record.planning_reasoning_effort, "high")
            self.assertEqual(record.decomposition_reasoning_effort, "high")
            self.assertEqual(record.execution_reasoning_effort, "medium")
            self.assertEqual(record.confidence, "high")
            self.assertFalse(record.requires_hil)
            self.assertEqual(record.plan["items"][0]["project_key"], "alpha")
            self.assertEqual(
                record.plan["items"][0]["draft_ticket"]["title"],
                "Capture a better operator audit trail",
            )
            self.assertEqual(record.plan["planning_result"]["recommendation"], "single_ticket")
            self.assertEqual(record.plan["decomposition"]["materialization_mode"], "single_leaf")
            planning_stage = record.plan["stage_contract"]["stages"][0]
            self.assertEqual(planning_stage["stage"], "planning")
            self.assertEqual(planning_stage["selected_reasoning_effort"], "high")
            self.assertEqual(planning_stage["selection_mode"], "simple_intake_downgrade")
            self.assertIn("planning_model", record.plan["stage_contract"]["persistence_fields"])

    def test_submit_reroutes_when_request_mentions_other_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service, _client = _intake_fixture(temp_dir)

            record = service.submit(
                request_text="beta: tighten the beta deployment checklist",
                ingress_bot="alpha_bot",
            )

            self.assertEqual(record.status, "pending_approval")
            self.assertEqual(record.plan["items"][0]["project_key"], "beta")
            self.assertEqual(record.plan["items"][0]["routing_mode"], "rerouted-project")
            self.assertEqual(
                record.plan["items"][0]["title"],
                "Tighten the beta deployment checklist",
            )

    def test_submit_groups_bullets_by_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service, _client = _intake_fixture(temp_dir)

            record = service.submit(
                request_text="- alpha: improve operator logs\n- beta: verify deploy dashboard",
                ingress_bot="alpha_bot",
            )

            self.assertEqual(record.status, "pending_approval")
            groups = {group["project_key"]: group for group in record.plan["groups"]}
            self.assertEqual(len(groups["alpha"]["items"]), 1)
            self.assertEqual(len(groups["beta"]["items"]), 1)
            self.assertEqual(record.plan["planning_result"]["recommendation"], "split_ticket_set")
            self.assertEqual(record.plan["decomposition"]["materialization_mode"], "grouped_ticket_set")
            capsules = record.plan["decomposition"]["capsules"]
            self.assertEqual(capsules[0]["ticket_role"], "umbrella")
            self.assertEqual(capsules[1]["ticket_role"], "leaf")
            self.assertEqual(capsules[2]["ticket_role"], "leaf")
            dependency_edges = record.plan["decomposition"]["dependency_edges"]
            self.assertEqual(len(dependency_edges), 2)
            self.assertEqual(
                dependency_edges[0],
                {
                    "from_capsule_key": "umbrella-root",
                    "to_capsule_key": "leaf-item-1",
                    "relationship": "parent_child",
                },
            )
            planning_stage = record.plan["stage_contract"]["stages"][0]
            self.assertEqual(planning_stage["selected_reasoning_effort"], "xhigh")
            self.assertEqual(planning_stage["selection_mode"], "default")

    def test_submit_marks_oversized_single_project_requests_for_xhigh_planning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service, _client = _intake_fixture(temp_dir)

            request_text = (
                "alpha: refactor the intake approval rendering so it shows the real grouped work packet, "
                "make the codex execution brief more specific for the created Linear issues, and tighten "
                "the worktree and runner metadata so the runner can execute without reconstructing context."
            )
            record = service.submit(
                request_text=request_text,
                ingress_bot="alpha_bot",
            )

            self.assertEqual(record.status, "pending_approval")
            self.assertEqual(record.planning_reasoning_effort, "xhigh")
            self.assertEqual(record.confidence, "medium")
            self.assertEqual(record.plan["planning_result"]["recommendation"], "single_ticket")
            self.assertIn("oversized", record.plan["planning_result"]["complexity_signals"])
            planning_stage = record.plan["stage_contract"]["stages"][0]
            self.assertEqual(planning_stage["selection_mode"], "oversized_intake")

    def test_submit_splits_multiple_project_labels_in_one_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service, _client = _intake_fixture(temp_dir)

            record = service.submit(
                request_text="alpha: improve operator logs; beta: verify deploy dashboard",
                ingress_bot="alpha_bot",
            )

            self.assertEqual(record.status, "pending_approval")
            self.assertEqual([item["project_key"] for item in record.plan["items"]], ["alpha", "beta"])

    def test_submit_requires_clarification_for_multi_project_single_item(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service, _client = _intake_fixture(temp_dir)

            record = service.submit(
                request_text="alpha and beta both need follow-up on this same change",
                ingress_bot="alpha_bot",
            )

            self.assertEqual(record.status, "clarification_required")
            self.assertTrue(record.plan["needs_clarification"])
            self.assertIsNone(record.plan["items"][0]["project_key"])
            self.assertEqual(record.confidence, "low")
            self.assertTrue(record.requires_hil)
            self.assertEqual(record.plan["planning_result"]["recommendation"], "clarification_required")
            self.assertTrue(record.plan["stage_contract"]["requires_hil"])
            planning_stage = record.plan["stage_contract"]["stages"][0]
            self.assertEqual(planning_stage["selected_reasoning_effort"], "xhigh")
            self.assertEqual(planning_stage["selection_mode"], "clarification_required")

    def test_submit_prefers_leading_project_label_over_incidental_cross_project_mentions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service, _client = _intake_fixture(temp_dir)

            record = service.submit(
                request_text=(
                    "alpha: replace the runner picker wording and make the ORX dashboard summary "
                    "line match the same queue terminology"
                ),
                ingress_bot="alpha_bot",
            )

            self.assertEqual(record.status, "pending_approval")
            self.assertEqual(record.plan["items"][0]["project_key"], "alpha")
            self.assertEqual(record.plan["items"][0]["routing_mode"], "explicit-project")
            self.assertEqual(
                record.plan["items"][0]["rationale"],
                "Matched leading project label for `alpha`.",
            )

    def test_approve_materializes_linear_issues(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service, client = _intake_fixture(temp_dir)

            record = service.submit(
                request_text="- alpha: improve operator logs\n- beta: verify deploy dashboard",
                ingress_bot="alpha_bot",
            )
            result = service.approve(intake_key=record.intake_key)

            self.assertEqual(result.intake.status, "materialized")
            self.assertEqual(len(result.created_issues), 3)
            self.assertIsNone(client.calls[0]["parent_id"])
            self.assertIsNone(client.calls[0]["project_id"])
            self.assertEqual(client.calls[1]["parent_id"], "issue-1")
            self.assertEqual(client.calls[2]["parent_id"], "issue-1")
            self.assertEqual(client.calls[1]["team_id"], "team-alpha")
            self.assertEqual(client.calls[2]["team_id"], "team-beta")
            self.assertEqual(result.intake.plan["created_issues"][1]["project_key"], "alpha")
            mirrored = {issue.identifier: issue for issue in service.mirror.list_issues()}
            self.assertEqual(mirrored["PRO-71"].metadata["orx_ticket_role"], "umbrella")
            self.assertTrue(mirrored["PRO-71"].metadata["no_auto_select"])
            self.assertEqual(mirrored["PRO-72"].metadata["project_key"], "alpha")
            self.assertEqual(mirrored["PRO-73"].metadata["project_key"], "beta")
            self.assertEqual(mirrored["PRO-72"].parent_identifier, "PRO-71")
            self.assertIn("## Coordination Notes", mirrored["PRO-71"].description)
            self.assertIn("## Execution Context", mirrored["PRO-72"].description)
            self.assertIn("## Packet Context", mirrored["PRO-72"].description)
            self.assertIn("## Codex Execution Brief", mirrored["PRO-72"].description)
            self.assertIn("Execution recommendation", mirrored["PRO-72"].description)
            self.assertIn("Stateless execution", mirrored["PRO-72"].description)
            self.assertIn("Repo root", mirrored["PRO-72"].description)
            self.assertIn("Original request: alpha: improve operator logs", mirrored["PRO-72"].description)
            self.assertIn("Avoid scope", mirrored["PRO-72"].description)
            self.assertEqual(mirrored["PRO-72"].metadata["packet_scope"], "shared_packet")
            self.assertEqual(mirrored["PRO-72"].metadata["merge_into"], "main")
            self.assertEqual(mirrored["PRO-72"].metadata["execution_reasoning_effort"], "medium")
            self.assertEqual(
                mirrored["PRO-72"].metadata["codex_execution_brief"]["objective_title"],
                "Improve operator logs",
            )
            self.assertIn("## Objective", mirrored["PRO-72"].description)
            self.assertIn("## Success Criteria", mirrored["PRO-72"].description)
            self.assertIn("## Exact Output Required", mirrored["PRO-72"].description)
            self.assertIn("## Ordered Steps", mirrored["PRO-72"].description)
            self.assertIn("## Verification", mirrored["PRO-72"].description)
            self.assertIn("## Stopping Conditions", mirrored["PRO-72"].description)
            self.assertIn("## Blocked / Escalation", mirrored["PRO-72"].description)
            self.assertTrue(mirrored["PRO-72"].metadata["codex_execution_brief"]["ordered_steps"])
            self.assertIn("worktree_path", mirrored["PRO-72"].description)
            self.assertIn("<!-- orx:metadata:start -->", mirrored["PRO-72"].description)
            self.assertEqual(client.updated_calls[0]["issue_ref"], "PRO-71")


class FakeLinearCreateClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, str | None]] = []
        self.updated_calls: list[dict[str, str | None]] = []
        self.issues_by_ref: dict[str, dict[str, str | None]] = {}

    def create_issue(
        self,
        *,
        team_id: str,
        title: str,
        description: str,
        parent_id: str | None = None,
        project_id: str | None = None,
    ):
        issue_number = len(self.calls) + 1
        self.calls.append(
            {
                "team_id": team_id,
                "title": title,
                "description": description,
                "parent_id": parent_id,
                "project_id": project_id,
            }
        )
        from orx.linear_client import LinearIssue

        issue = LinearIssue(
            linear_id=f"issue-{issue_number}",
            identifier=f"PRO-7{issue_number}",
            title=title,
            description=description,
            url=f"https://linear.example/PRO-7{issue_number}",
            team_id=team_id,
            team_name="Projects",
            state_id="state-1",
            state_name="Todo",
            state_type="unstarted",
            parent_id=parent_id,
            parent_identifier=(
                None
                if parent_id is None
                else str(self.issues_by_ref[next(
                    ref for ref, payload in self.issues_by_ref.items() if payload["linear_id"] == parent_id
                )]["identifier"])
            ),
            project_id=project_id,
            project_name=None,
        )
        self.issues_by_ref[issue.identifier] = {
            "linear_id": issue.linear_id,
            "identifier": issue.identifier,
            "title": issue.title,
            "description": issue.description,
            "url": issue.url,
            "team_id": issue.team_id,
            "team_name": issue.team_name,
            "parent_id": issue.parent_id,
            "parent_identifier": issue.parent_identifier,
            "project_id": issue.project_id,
        }
        return issue

    def update_issue(
        self,
        *,
        issue_ref: str,
        title: str | None = None,
        description: str | None = None,
        state_id: str | None = None,
    ):
        self.updated_calls.append(
            {
                "issue_ref": issue_ref,
                "title": title,
                "description": description,
                "state_id": state_id,
            }
        )
        issue = self.issues_by_ref[issue_ref]
        from orx.linear_client import LinearIssue

        return LinearIssue(
            linear_id=str(issue["linear_id"]),
            identifier=issue_ref,
            title=title or str(issue["title"]),
            description=description or "",
            url=str(issue["url"]),
            team_id=str(issue["team_id"]),
            team_name=str(issue["team_name"]),
            state_id="state-1",
            state_name="Todo",
            state_type="unstarted",
            parent_id=str(issue["parent_id"]) if issue["parent_id"] is not None else None,
            parent_identifier=(
                str(issue["parent_identifier"])
                if issue["parent_identifier"] is not None
                else None
            ),
            project_id=str(issue["project_id"]) if issue["project_id"] is not None else None,
            project_name=None,
        )


def _intake_fixture(temp_dir: str) -> tuple[IntakeService, FakeLinearCreateClient]:
    storage = Storage(resolve_runtime_paths(temp_dir))
    storage.bootstrap()
    registry = ProjectRegistry(storage)
    registry.upsert_project(
        project_key="alpha",
        display_name="Alpha",
        repo_root="/tmp/alpha",
        runtime_home=f"{temp_dir}/alpha",
        owning_bot="alpha_bot",
    )
    registry.upsert_project(
        project_key="beta",
        display_name="Beta",
        repo_root="/tmp/beta",
        runtime_home=f"{temp_dir}/beta",
        owning_bot="beta_bot",
    )
    mirror = LinearMirrorRepository(storage)
    mirror.upsert_issue(
        linear_id="alpha-issue-1",
        identifier="PRO-101",
        title="Alpha issue",
        description="",
        team_id="team-alpha",
        team_name="Projects",
        state_name="Todo",
        state_type="unstarted",
        project_id="linear-alpha",
        project_name="Alpha",
        source_updated_at="2026-04-16T00:00:00+00:00",
        metadata={"project_key": "alpha"},
    )
    mirror.upsert_issue(
        linear_id="beta-issue-1",
        identifier="PRO-102",
        title="Beta issue",
        description="",
        team_id="team-beta",
        team_name="Projects",
        state_name="Todo",
        state_type="unstarted",
        project_id="linear-beta",
        project_name="Beta",
        source_updated_at="2026-04-16T00:00:01+00:00",
        metadata={"project_key": "beta"},
    )
    client = FakeLinearCreateClient()
    return IntakeService(storage, registry=registry, mirror=mirror, linear_client=client), client


if __name__ == "__main__":
    unittest.main()
