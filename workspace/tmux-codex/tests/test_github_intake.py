import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.codex_engine import CodexRunResult
from src.github_intake import handle_add_intake
from src.runner_control import RunnerControlPlane
from src.runner_state import build_runner_state_paths_for_root


class GitHubIntakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dev = Path(self.tmp.name)
        self.blog = self.dev / "workspace" / "blog"
        self.api = self.dev / "workspace" / "api"
        self.blog.mkdir(parents=True)
        self.api.mkdir(parents=True)
        (self.blog / ".git").mkdir()
        (self.api / ".git").mkdir()
        self.paths = build_runner_state_paths_for_root(
            project_root=self.blog,
            dev=str(self.dev),
            project="blog",
            runner_id="main",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _fake_subprocess_run(self, args, cwd=None, check=True, capture_output=True, text=True):  # noqa: ARG002
        command = " ".join(args)
        if "git -C" in command and "config --get remote.origin.url" in command:
            repo_root = Path(args[2]).resolve()
            if repo_root.name == "blog":
                return SimpleNamespace(stdout="git@github.com:jkuang7/blog.git\n")
            if repo_root.name == "api":
                return SimpleNamespace(stdout="git@github.com:jkuang7/api.git\n")
        if command.startswith("gh issue list"):
            return SimpleNamespace(stdout="[]")
        if "create-issue" in command:
            repo = args[args.index("--repo") + 1]
            title = args[args.index("--title") + 1]
            slug = title.lower().replace(" ", "-")
            return SimpleNamespace(
                stdout=json.dumps(
                    {
                        "ok": True,
                        "issueUrl": f"https://github.com/{repo}/issues/{11 if 'blog' in repo else 22 if 'api' in repo else 33}",
                        "repo": repo,
                        "item": {"title": title, "slug": slug},
                    }
                )
            )
        if command.startswith("gh issue edit"):
            return SimpleNamespace(stdout="")
        raise AssertionError(f"unexpected subprocess call: {command}")

    def test_add_intake_creates_and_dedupes_cross_repo_issue_set(self):
        proposal = {
            "needs_refinement": False,
            "understood": "Split feature into blog UI and api worker tickets.",
            "reason": None,
            "parent": {
                "key": "TRACKER",
                "repo": "jkuang7/blog",
                "title": "Build shared feature tracker",
                "summary": "Coordinate the cross-repo delivery.",
                "acceptance": ["Tracker links both child tickets."],
                "validation": ["Confirm links on the board."],
                "type": "Feature",
            },
            "tickets": [
                {
                    "key": "BLOG",
                    "repo": "jkuang7/blog",
                    "title": "Build blog workflow UI",
                    "summary": "Add the blog-facing controls and status view.",
                    "acceptance": ["Telegram /add creates blog UI ticket correctly."],
                    "validation": ["Review the issue body and routing metadata."],
                    "depends_on": [],
                    "priority": "P1",
                    "type": "Feature",
                },
                {
                    "key": "API",
                    "repo": "jkuang7/api",
                    "title": "Build API workflow worker",
                    "summary": "Handle background orchestration in the API repo.",
                    "acceptance": ["API worker ticket is split from UI work."],
                    "validation": ["Review dependency metadata."],
                    "depends_on": ["BLOG"],
                    "priority": "P2",
                    "type": "Feature",
                },
            ],
        }

        with patch("src.github_intake.run_codex_iteration") as run_codex, patch(
            "src.github_intake.subprocess.run",
            side_effect=self._fake_subprocess_run,
        ) as subprocess_run:
            run_codex.return_value = CodexRunResult(
                exit_code=0,
                session_id=None,
                final_message=json.dumps(proposal),
                events=[],
                raw_lines=[],
            )
            code, output = handle_add_intake(
                paths=self.paths,
                project_root=self.blog,
                text="build the new shared feature across blog and api",
                requested_by="telegram:1:2",
            )
            self.assertEqual(code, 0)
            payload = json.loads(output)
            self.assertEqual(payload["status"], "created")
            self.assertEqual(len(payload["created_issues"]), 3)
            self.assertEqual(payload["first_runnable_issue"]["key"], "BLOG")

            repeat_code, repeat_output = handle_add_intake(
                paths=self.paths,
                project_root=self.blog,
                text="build the new shared feature across blog and api",
                requested_by="telegram:1:2",
            )
            self.assertEqual(repeat_code, 0)
            repeated = json.loads(repeat_output)
            self.assertEqual(repeated["fingerprint"], payload["fingerprint"])
            self.assertEqual(run_codex.call_count, 1)
            create_calls = [
                call for call in subprocess_run.call_args_list if "create-issue" in " ".join(call.args[0])
            ]
            self.assertEqual(len(create_calls), 3)

        control = RunnerControlPlane(self.paths)
        cached = control.get_intake_request(payload["fingerprint"])
        self.assertIsNotNone(cached)
        assert cached is not None
        self.assertEqual(cached["status"], "created")

    def test_add_intake_returns_refinement_without_creating_issues(self):
        proposal = {
            "needs_refinement": True,
            "understood": "Request mentions unrelated ideas without a clear repo split.",
            "reason": "Need a clearer goal before creating board work.",
            "parent": None,
            "tickets": [],
        }

        with patch("src.github_intake.run_codex_iteration") as run_codex, patch(
            "src.github_intake.subprocess.run",
            side_effect=self._fake_subprocess_run,
        ) as subprocess_run:
            run_codex.return_value = CodexRunResult(
                exit_code=0,
                session_id=None,
                final_message=json.dumps(proposal),
                events=[],
                raw_lines=[],
            )
            code, output = handle_add_intake(
                paths=self.paths,
                project_root=self.blog,
                text="uh maybe do something everywhere somehow",
                requested_by="telegram:1:2",
            )
            self.assertEqual(code, 0)
            payload = json.loads(output)
            self.assertEqual(payload["status"], "needs_refinement")
            self.assertEqual(payload["created_issues"], [])
            create_calls = [
                call for call in subprocess_run.call_args_list if "create-issue" in " ".join(call.args[0])
            ]
            self.assertEqual(create_calls, [])

    def test_add_intake_recovers_when_issue_create_returns_error_after_issue_exists(self):
        proposal = {
            "needs_refinement": False,
            "understood": "Split feature into tracker plus one child.",
            "reason": None,
            "parent": None,
            "tickets": [
                {
                    "key": "BLOG",
                    "repo": "jkuang7/blog",
                    "title": "Build blog workflow UI",
                    "summary": "Add the blog-facing controls and status view.",
                    "acceptance": ["Telegram /add creates blog UI ticket correctly."],
                    "validation": ["Review the issue body and routing metadata."],
                    "depends_on": [],
                    "priority": "P1",
                    "type": "Feature",
                }
            ],
        }

        def flaky_subprocess(args, cwd=None, check=True, capture_output=True, text=True):  # noqa: ARG001
            command = " ".join(args)
            if "git -C" in command and "config --get remote.origin.url" in command:
                return SimpleNamespace(stdout="git@github.com:jkuang7/blog.git\n")
            if command.startswith("gh issue list"):
                return SimpleNamespace(
                    stdout=json.dumps(
                        [
                            {
                                "number": 42,
                                "title": "Build blog workflow UI",
                                "url": "https://github.com/jkuang7/blog/issues/42",
                            }
                        ]
                    )
                )
            if "create-issue" in command and "Build blog workflow UI" in command:
                raise subprocess.CalledProcessError(
                    returncode=1,
                    cmd=args,
                    output="",
                    stderr="Unknown Priority value: P1",
                )
            if "create-issue" in command:
                return SimpleNamespace(
                    stdout=json.dumps(
                        {
                            "ok": True,
                            "issueUrl": "https://github.com/jkuang7/blog/issues/41",
                            "repo": "jkuang7/blog",
                            "item": {"title": "Build shared feature tracker"},
                        }
                    )
                )
            if command.startswith("gh issue edit"):
                return SimpleNamespace(stdout="")
            raise AssertionError(f"unexpected subprocess call: {command}")

        with patch("src.github_intake.run_codex_iteration") as run_codex, patch(
            "src.github_intake.subprocess.run",
            side_effect=flaky_subprocess,
        ):
            run_codex.return_value = CodexRunResult(
                exit_code=0,
                session_id=None,
                final_message=json.dumps(proposal),
                events=[],
                raw_lines=[],
            )
            code, output = handle_add_intake(
                paths=self.paths,
                project_root=self.blog,
                text="build the new shared feature in blog",
                requested_by="telegram:1:2",
            )
            self.assertEqual(code, 0)
            payload = json.loads(output)
            self.assertEqual(payload["status"], "created")
            self.assertEqual(payload["first_runnable_issue"]["url"], "https://github.com/jkuang7/blog/issues/42")


if __name__ == "__main__":
    unittest.main()
