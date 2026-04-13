import pathlib
import unittest


SKILLS_ROOT = pathlib.Path(__file__).resolve().parent
ENHANCE_SKILL = SKILLS_ROOT / "enhance" / "SKILL.md"
KANBAN_SKILL = SKILLS_ROOT / "kanban" / "SKILL.md"


class SkillContractTests(unittest.TestCase):
    def test_enhance_requires_child_repo_routing(self):
        contents = ENHANCE_SKILL.read_text()

        self.assertIn(
            "Assign repo and project metadata per child ticket based on that child ticket's actual scope",
            contents,
        )
        self.assertIn(
            "Do not keep all child tickets in the parent repo just because the original umbrella issue lived there.",
            contents,
        )
        self.assertIn(
            "Explicitly deprecate the original implementation detail so future agents do not execute from stale mixed-scope text.",
            contents,
        )

    def test_kanban_rejects_tracker_issues_as_execution_specs(self):
        contents = KANBAN_SKILL.read_text()

        self.assertIn(
            "If the selected issue explicitly says it is an umbrella, tracker, deprecated spec, or points to child tickets as the source of truth, do not implement from it directly.",
            contents,
        )
        self.assertIn(
            "Prefer the linked child ticket that actually carries the actionable scope.",
            contents,
        )
        self.assertIn(
            "If the current repo or workspace exposes a pre-work sync command such as `bs:pull` or `bootstrap:pull`, prefer running it before picking new work.",
            contents,
        )


if __name__ == "__main__":
    unittest.main()
