import importlib.util
import pathlib
import sys
import unittest
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


if __name__ == "__main__":
    unittest.main()
