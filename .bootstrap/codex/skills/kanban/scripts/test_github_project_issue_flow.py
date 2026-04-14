import importlib.util
import io
import pathlib
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock


MODULE_PATH = pathlib.Path(__file__).with_name("github_project_issue_flow.py")
SPEC = importlib.util.spec_from_file_location("github_project_issue_flow", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class UpdateSingleSelectFieldTests(unittest.TestCase):
    def test_project_field_skips_missing_option(self):
        fake_field = MODULE.ProjectField(id="field-1", name="Project", options={"Banksy": "opt-1"})

        with (
            mock.patch.object(MODULE, "load_fields", return_value={"Project": fake_field}),
            mock.patch.object(MODULE.subprocess, "run") as run_mock,
            mock.patch.object(MODULE, "project_node_id", return_value="project-1"),
        ):
            MODULE.update_single_select_field(
                owner="jkuang7",
                project_number=5,
                item_id="item-1",
                field_name="Project",
                value_name="Dev",
            )

        run_mock.assert_not_called()

    def test_non_project_field_still_errors_on_missing_option(self):
        fake_field = MODULE.ProjectField(id="field-2", name="Status", options={"Inbox": "opt-2"})

        with mock.patch.object(MODULE, "load_fields", return_value={"Status": fake_field}):
            with self.assertRaises(SystemExit):
                MODULE.update_single_select_field(
                    owner="jkuang7",
                    project_number=5,
                    item_id="item-1",
                    field_name="Status",
                    value_name="Missing",
                )


class TrackerSelectionTests(unittest.TestCase):
    def test_detects_deprecated_tracker_body(self):
        body = """
        ## Summary

        This issue is now an umbrella tracker.
        The original mixed-scope description is deprecated as an implementation spec.

        ## Source Of Truth

        Use these child issues as the current implementation source of truth:
        - https://github.com/jkuang7/Dev/issues/2
        """

        self.assertTrue(MODULE.issue_is_deprecated_tracker(body))
        self.assertEqual(
            MODULE.extract_issue_urls(body),
            ["https://github.com/jkuang7/Dev/issues/2"],
        )

    def test_prefers_linked_child_over_tracker_candidate(self):
        tracker = {
            "itemId": "parent",
            "number": 1,
            "title": "Umbrella",
            "url": "https://github.com/jkuang7/Dev/issues/1",
            "state": "OPEN",
            "repo": "jkuang7/Dev",
            "fields": {"Status": "Inbox", "Type": "Feature"},
        }
        child = {
            "itemId": "child",
            "number": 2,
            "title": "Child issue",
            "url": "https://github.com/jkuang7/Dev/issues/2",
            "state": "OPEN",
            "repo": "jkuang7/Dev",
            "fields": {"Status": "Inbox", "Type": "Feature"},
        }

        details = {
            tracker["url"]: {
                "body": """
                This issue is now an umbrella tracker.
                The original mixed-scope description is deprecated as an implementation spec.
                Use these child issues as the current implementation source of truth:
                - https://github.com/jkuang7/Dev/issues/2
                """,
            },
            child["url"]: {"body": "Normal actionable issue body."},
        }

        selected = MODULE.choose_actionable_candidate(
            [tracker, child],
            [tracker, child],
            lambda item: details[item["url"]],
        )

        self.assertEqual(selected["selection"], "tracker-child")
        self.assertEqual(selected["item"]["url"], child["url"])
        self.assertEqual(selected["tracker"]["url"], tracker["url"])

    def test_marks_tracker_when_no_linked_child_is_actionable(self):
        tracker = {
            "itemId": "parent",
            "number": 1,
            "title": "Umbrella",
            "url": "https://github.com/jkuang7/Dev/issues/1",
            "state": "OPEN",
            "repo": "jkuang7/Dev",
            "fields": {"Status": "Inbox", "Type": "Feature"},
        }

        selected = MODULE.choose_actionable_candidate(
            [tracker],
            [tracker],
            lambda item: {
                "body": """
                This issue is now an umbrella tracker.
                The original mixed-scope description is deprecated as an implementation spec.
                Use these child issues as the current implementation source of truth:
                - https://github.com/jkuang7/Dev/issues/99
                """,
            },
        )

        self.assertEqual(selected["selection"], "tracker-candidate")
        self.assertTrue(selected["tracker"])
        self.assertEqual(selected["item"]["url"], tracker["url"])


class CommandNextTests(unittest.TestCase):
    def test_falls_back_to_project_candidate_when_repo_and_workspace_do_not_match(self):
        project_item = {
            "itemId": "project-item",
            "number": 5,
            "title": "Project-wide task",
            "url": "https://github.com/jkuang7/other-repo/issues/5",
            "state": "OPEN",
            "repo": "jkuang7/other-repo",
            "fields": {"Status": "Ready", "Priority": "P1"},
        }

        args = MODULE.argparse.Namespace(
            owner="jkuang7",
            project_number=5,
            repo="jkuang7/Dev",
            repos_root="/tmp/Repos",
        )

        with (
            mock.patch.object(MODULE, "load_project_items", return_value=[project_item]),
            mock.patch.object(MODULE, "local_repos_from_root", return_value={"jkuang7/Banksy"}),
            mock.patch.object(
                MODULE,
                "load_issue_details",
                return_value={"body": "Normal actionable issue body."},
            ),
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = MODULE.command_next(args)

        payload = MODULE.json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["found"])
        self.assertEqual(payload["selection"], "project-fallback")
        self.assertEqual(payload["item"]["url"], project_item["url"])


class WorkflowCommentTests(unittest.TestCase):
    def test_latest_workflow_state_prefers_most_recent_matching_comment(self):
        comments = [
            {"body": "Picking this up now. Plan is one line.", "createdAt": "2026-04-12T10:00:00Z"},
            {"body": "Ready for review. I changed x.", "createdAt": "2026-04-12T11:00:00Z"},
            {"body": "Adjusting course based on feedback. I am moving this back to In Progress. Next I am going to y.", "createdAt": "2026-04-12T12:00:00Z"},
        ]

        self.assertEqual(MODULE.latest_workflow_state(comments), "in_progress")


class CommandActiveTests(unittest.TestCase):
    def test_returns_repo_started_issue_when_comment_marks_it_in_progress(self):
        args = MODULE.argparse.Namespace(
            owner="jkuang7",
            project_number=5,
            repo="jkuang7/time-track",
            repos_root="/tmp/Repos",
        )

        with mock.patch.object(
            MODULE,
            "find_active_started_issue",
            side_effect=[
                {
                    "repo": "jkuang7/time-track",
                    "number": 1,
                    "title": "Started",
                    "url": "https://github.com/jkuang7/time-track/issues/1",
                    "workflowState": "in_progress",
                    "updatedAt": "2026-04-12T22:57:54Z",
                }
            ],
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = MODULE.command_active(args)

        payload = MODULE.json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["found"])
        self.assertEqual(payload["selection"], "repo-started-issue")
        self.assertEqual(payload["item"]["url"], "https://github.com/jkuang7/time-track/issues/1")

    def test_workspace_active_skips_repo_failures_and_finds_later_match(self):
        args = MODULE.argparse.Namespace(
            owner="jkuang7",
            project_number=5,
            repo="jkuang7/Dev",
            repos_root="/tmp/Repos",
        )

        with (
            mock.patch.object(MODULE, "find_active_started_issue", side_effect=[
                None,
                None,
                {
                    "repo": "jkuang7/time-track",
                    "number": 1,
                    "title": "Started",
                    "url": "https://github.com/jkuang7/time-track/issues/1",
                    "workflowState": "in_progress",
                    "updatedAt": "2026-04-12T22:57:54Z",
                },
            ]),
            mock.patch.object(
                MODULE,
                "local_repos_from_root",
                return_value={"jkuang7/recert-studio", "jkuang7/time-track"},
            ),
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = MODULE.command_active(args)

        payload = MODULE.json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["found"])
        self.assertEqual(payload["selection"], "workspace-started-issue")
        self.assertEqual(payload["item"]["repo"], "jkuang7/time-track")


if __name__ == "__main__":
    unittest.main()
