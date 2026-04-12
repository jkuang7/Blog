#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_OWNER = "jkuang7"
DEFAULT_PROJECT_NUMBER = 5
PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2}


@dataclass
class ProjectField:
    id: str
    name: str
    options: dict[str, str]


def run_gh(args: list[str]) -> Any:
    proc = subprocess.run(
        ["gh", *args],
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(proc.stdout)


def graphql(query: str, variables: dict[str, Any]) -> Any:
    args = ["api", "graphql", "-f", f"query={query}"]
    for key, value in variables.items():
        if value is None:
            continue
        args.extend(["-F", f"{key}={value}"])
    return run_gh(args)


def load_project_items(owner: str, project_number: int) -> list[dict[str, Any]]:
    query = """
query($owner: String!, $number: Int!, $cursor: String) {
  user(login: $owner) {
    projectV2(number: $number) {
      items(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          content {
            ... on Issue {
              number
              title
              url
              state
              repository { nameWithOwner }
            }
          }
          fieldValues(first: 20) {
            nodes {
              ... on ProjectV2ItemFieldSingleSelectValue {
                name
                optionId
                field {
                  ... on ProjectV2FieldCommon {
                    name
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
"""
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        data = graphql(query, {"owner": owner, "number": project_number, "cursor": cursor})
        project = data["data"]["user"]["projectV2"]
        page = project["items"]
        for node in page["nodes"]:
            content = node.get("content")
            if not content:
                continue
            fields: dict[str, str] = {}
            for field_value in node["fieldValues"]["nodes"]:
                field = field_value.get("field")
                if field and field.get("name") and field_value.get("name"):
                    fields[field["name"]] = field_value["name"]
            items.append(
                {
                    "itemId": node["id"],
                    "number": content["number"],
                    "title": content["title"],
                    "url": content["url"],
                    "state": content["state"],
                    "repo": content["repository"]["nameWithOwner"],
                    "fields": fields,
                }
            )
        if not page["pageInfo"]["hasNextPage"]:
            return items
        cursor = page["pageInfo"]["endCursor"]


def load_fields(owner: str, project_number: int) -> dict[str, ProjectField]:
    data = run_gh(
        ["project", "field-list", str(project_number), "--owner", owner, "--format", "json"]
    )
    fields: dict[str, ProjectField] = {}
    for field in data["fields"]:
        options = {option["name"]: option["id"] for option in field.get("options", [])}
        fields[field["name"]] = ProjectField(field["id"], field["name"], options)
    return fields


def status_value(item: dict[str, Any]) -> str:
    return item["fields"].get("Status", "")


def priority_value(item: dict[str, Any]) -> int:
    return PRIORITY_ORDER.get(item["fields"].get("Priority", "P2"), 9)


def project_value(item: dict[str, Any]) -> str:
    return item["fields"].get("Project", "")


def repo_name_to_project_value(repo: str) -> str:
    return repo.split("/", 1)[1]


def workspace_status_rank(item: dict[str, Any]) -> int:
    status = status_value(item)
    if status == "Ready":
        return 0
    if status == "Inbox":
        return 1
    if status == "":
        return 2
    return 9


def local_repos_from_root(repos_root: str) -> set[str]:
    root = Path(repos_root).expanduser()
    if not root.is_dir():
        return set()

    repos: set[str] = set()
    for child in sorted(root.iterdir()):
        if not child.is_dir() or not (child / ".git").exists():
            continue
        try:
            proc = subprocess.run(
                ["git", "-C", os.fspath(child), "remote", "get-url", "origin"],
                check=True,
                text=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            continue
        remote = proc.stdout.strip()
        if remote.startswith("git@github.com:"):
            repo = remote.removeprefix("git@github.com:").removesuffix(".git")
            repos.add(repo)
        elif remote.startswith("https://github.com/"):
            repo = remote.removeprefix("https://github.com/").removesuffix(".git")
            repos.add(repo)
    return repos


def project_preference_rank(item: dict[str, Any]) -> int:
    project = project_value(item)
    if not project:
        return 0
    return 0 if project == repo_name_to_project_value(item["repo"]) else 1


def next_candidate_sort_key(item: dict[str, Any]) -> tuple[int, int, int, int]:
    return (
        workspace_status_rank(item),
        project_preference_rank(item),
        priority_value(item),
        item["number"],
    )


def command_next(args: argparse.Namespace) -> int:
    items = load_project_items(args.owner, args.project_number)
    repo_candidates = [
        item
        for item in items
        if item["repo"] == args.repo and item["state"] == "OPEN" and status_value(item) in {"Ready", "Inbox", ""}
    ]
    repo_candidates.sort(key=next_candidate_sort_key)
    if repo_candidates:
        print(
            json.dumps(
                {"found": True, "selection": "repo-candidate", "item": repo_candidates[0]},
                indent=2,
            )
        )
        return 0

    if args.repos_root:
        local_repos = local_repos_from_root(args.repos_root)
        candidates = [
            item
            for item in items
            if item["repo"] in local_repos
            and item["state"] == "OPEN"
            and status_value(item) in {"Ready", "Inbox", ""}
        ]
        candidates.sort(key=next_candidate_sort_key)
        if candidates:
            selected = candidates[0]
            print(
                json.dumps(
                    {
                        "found": True,
                        "selection": "workspace-fallback",
                        "requestedRepo": args.repo,
                        "reposRoot": args.repos_root,
                        "item": selected,
                    },
                    indent=2,
                )
            )
            return 0

    print(
        json.dumps(
            {
                "found": False,
                "repo": args.repo,
                "reposRoot": args.repos_root,
                "message": "No matching project issue found for this repo or local workspace.",
            },
            indent=2,
        )
    )
    return 1


def command_issue_item(args: argparse.Namespace) -> int:
    items = load_project_items(args.owner, args.project_number)
    for item in items:
        if item["url"] == args.issue_url:
            print(json.dumps(item, indent=2))
            return 0
    print(json.dumps({"found": False, "issueUrl": args.issue_url}, indent=2))
    return 1


def command_list(args: argparse.Namespace) -> int:
    items = load_project_items(args.owner, args.project_number)
    filtered = items
    if args.status:
        filtered = [item for item in filtered if status_value(item) == args.status]
    if args.repo:
        filtered = [item for item in filtered if item["repo"] == args.repo]
    filtered.sort(key=lambda item: (item["repo"], priority_value(item), item["number"]))
    print(json.dumps(filtered, indent=2))
    return 0


def command_set_status(args: argparse.Namespace) -> int:
    fields = load_fields(args.owner, args.project_number)
    status_field = fields["Status"]
    option_id = status_field.options.get(args.status)
    if not option_id:
        raise SystemExit(f"Unknown status: {args.status}")

    items = load_project_items(args.owner, args.project_number)
    target = next((item for item in items if item["url"] == args.issue_url), None)
    if not target:
        raise SystemExit(f"Issue not found in project: {args.issue_url}")

    subprocess.run(
        [
            "gh",
            "project",
            "item-edit",
            "--id",
            target["itemId"],
            "--project-id",
            project_node_id(args.owner, args.project_number),
            "--field-id",
            status_field.id,
            "--single-select-option-id",
            option_id,
        ],
        check=True,
        text=True,
    )
    print(
        json.dumps(
            {
                "ok": True,
                "issueUrl": args.issue_url,
                "itemId": target["itemId"],
                "status": args.status,
            },
            indent=2,
        )
    )
    return 0


def update_single_select_field(
    *,
    owner: str,
    project_number: int,
    item_id: str,
    field_name: str,
    value_name: str | None,
) -> None:
    if not value_name:
        return

    fields = load_fields(owner, project_number)
    field = fields.get(field_name)
    if not field:
        return

    option_id = field.options.get(value_name)
    if not option_id:
        raise SystemExit(f"Unknown {field_name} value: {value_name}")

    subprocess.run(
        [
            "gh",
            "project",
            "item-edit",
            "--id",
            item_id,
            "--project-id",
            project_node_id(owner, project_number),
            "--field-id",
            field.id,
            "--single-select-option-id",
            option_id,
        ],
        check=True,
        text=True,
    )


def command_create_issue(args: argparse.Namespace) -> int:
    create_args = [
        "gh",
        "issue",
        "create",
        "--repo",
        args.repo,
        "--title",
        args.title,
        "--body-file",
        args.body_file,
    ]

    if args.label:
        for label in args.label:
            create_args.extend(["--label", label])

    proc = subprocess.run(
        create_args,
        check=True,
        text=True,
        capture_output=True,
    )
    issue_url = proc.stdout.strip()
    if not issue_url:
        raise SystemExit("Issue create did not return an issue URL")

    subprocess.run(
        [
            "gh",
            "project",
            "item-add",
            str(args.project_number),
            "--owner",
            args.owner,
            "--url",
            issue_url,
        ],
        check=True,
        text=True,
    )

    items = load_project_items(args.owner, args.project_number)
    target = next((item for item in items if item["url"] == issue_url), None)
    if not target:
        raise SystemExit(f"Created issue was not added to project: {issue_url}")

    project_field_value = args.project_field or repo_name_to_project_value(args.repo)
    update_single_select_field(
        owner=args.owner,
        project_number=args.project_number,
        item_id=target["itemId"],
        field_name="Status",
        value_name=args.status,
    )
    update_single_select_field(
        owner=args.owner,
        project_number=args.project_number,
        item_id=target["itemId"],
        field_name="Project",
        value_name=project_field_value,
    )
    update_single_select_field(
        owner=args.owner,
        project_number=args.project_number,
        item_id=target["itemId"],
        field_name="Priority",
        value_name=args.priority,
    )
    update_single_select_field(
        owner=args.owner,
        project_number=args.project_number,
        item_id=target["itemId"],
        field_name="Type",
        value_name=args.type,
    )

    refreshed = load_project_items(args.owner, args.project_number)
    item = next((candidate for candidate in refreshed if candidate["url"] == issue_url), None)
    print(
        json.dumps(
            {
                "ok": True,
                "issueUrl": issue_url,
                "repo": args.repo,
                "item": item,
            },
            indent=2,
        )
    )
    return 0


def project_node_id(owner: str, project_number: int) -> str:
    data = run_gh(
        ["project", "view", str(project_number), "--owner", owner, "--format", "json"]
    )
    return data["id"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.set_defaults(owner=DEFAULT_OWNER, project_number=DEFAULT_PROJECT_NUMBER)
    subparsers = parser.add_subparsers(dest="command", required=True)

    next_parser = subparsers.add_parser("next")
    next_parser.add_argument("--owner", default=DEFAULT_OWNER)
    next_parser.add_argument("--project-number", type=int, default=DEFAULT_PROJECT_NUMBER)
    next_parser.add_argument("--repo", required=True)
    next_parser.add_argument("--repos-root")
    next_parser.set_defaults(func=command_next)

    issue_item_parser = subparsers.add_parser("issue-item")
    issue_item_parser.add_argument("--owner", default=DEFAULT_OWNER)
    issue_item_parser.add_argument("--project-number", type=int, default=DEFAULT_PROJECT_NUMBER)
    issue_item_parser.add_argument("--issue-url", required=True)
    issue_item_parser.set_defaults(func=command_issue_item)

    list_parser = subparsers.add_parser("list")
    list_parser.add_argument("--owner", default=DEFAULT_OWNER)
    list_parser.add_argument("--project-number", type=int, default=DEFAULT_PROJECT_NUMBER)
    list_parser.add_argument("--status")
    list_parser.add_argument("--repo")
    list_parser.set_defaults(func=command_list)

    set_status_parser = subparsers.add_parser("set-status")
    set_status_parser.add_argument("--owner", default=DEFAULT_OWNER)
    set_status_parser.add_argument("--project-number", type=int, default=DEFAULT_PROJECT_NUMBER)
    set_status_parser.add_argument("--issue-url", required=True)
    set_status_parser.add_argument("--status", required=True)
    set_status_parser.set_defaults(func=command_set_status)

    create_issue_parser = subparsers.add_parser("create-issue")
    create_issue_parser.add_argument("--owner", default=DEFAULT_OWNER)
    create_issue_parser.add_argument("--project-number", type=int, default=DEFAULT_PROJECT_NUMBER)
    create_issue_parser.add_argument("--repo", required=True)
    create_issue_parser.add_argument("--title", required=True)
    create_issue_parser.add_argument("--body-file", required=True)
    create_issue_parser.add_argument("--label", action="append")
    create_issue_parser.add_argument("--status", default="Inbox")
    create_issue_parser.add_argument("--project-field")
    create_issue_parser.add_argument("--priority")
    create_issue_parser.add_argument("--type")
    create_issue_parser.set_defaults(func=command_create_issue)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
