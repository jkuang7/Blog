"""Deterministic GitHub intake for board-visible /add requests."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .codex_engine import run_codex_iteration
from .runner_control import RunnerControlPlane
from .runner_state import RunnerStatePaths, ensure_memory_dir, utc_now

PROJECT_OWNER = "jkuang7"
PROJECT_NUMBER = 5


class IntakeValidationError(ValueError):
    """Raised when AI output does not satisfy the deterministic contract."""


def handle_add_intake(
    *,
    paths: RunnerStatePaths,
    project_root: Path,
    text: str,
    requested_by: str | None,
) -> tuple[int, str]:
    ensure_memory_dir(paths)
    control = RunnerControlPlane(paths)
    request_text = str(text or "").strip()
    if not request_text:
        return 1, "ERROR: --text is required for --intake add"

    known_repos = discover_known_github_repos(project_root)
    if not known_repos:
        return 1, f"ERROR: no GitHub repos were discoverable from {project_root}"

    current_repo = detect_github_repo(project_root)
    fingerprint = build_intake_fingerprint(
        request_text=request_text,
        current_repo=current_repo,
        known_repos=known_repos,
    )
    cached = control.get_intake_request(fingerprint)
    if cached and cached.get("result"):
        return 0, json.dumps(cached["result"], indent=2, sort_keys=True)

    try:
        proposal = propose_ticket_plan(
            project_root=project_root,
            request_text=request_text,
            current_repo=current_repo,
            known_repos=known_repos,
        )
        validated = validate_ticket_plan(
            proposal=proposal,
            current_repo=current_repo,
            known_repos=known_repos,
        )
        if validated["needs_refinement"]:
            result = {
                "status": "needs_refinement",
                "fingerprint": fingerprint,
                "requested_by": requested_by,
                "current_repo": current_repo,
                "known_repos": [repo["repo"] for repo in known_repos],
                "understood": validated.get("understood"),
                "reason": validated.get("reason"),
                "created_issues": [],
                "created_at": utc_now(),
            }
            control.record_intake_request(
                fingerprint=fingerprint,
                request_text=request_text,
                current_repo=current_repo,
                requested_by=requested_by,
                status="needs_refinement",
                result=result,
            )
            return 0, json.dumps(result, indent=2, sort_keys=True)

        created = create_issue_set(
            project_root=project_root,
            current_repo=current_repo,
            plan=validated,
        )
        result = {
            "status": "created",
            "fingerprint": fingerprint,
            "requested_by": requested_by,
            "current_repo": current_repo,
            "known_repos": [repo["repo"] for repo in known_repos],
            "parent_issue": created.get("parent_issue"),
            "created_issues": created["created_issues"],
            "first_runnable_issue": created.get("first_runnable_issue"),
            "created_at": utc_now(),
        }
        control.record_intake_request(
            fingerprint=fingerprint,
            request_text=request_text,
            current_repo=current_repo,
            requested_by=requested_by,
            status="created",
            result=result,
        )
        return 0, json.dumps(result, indent=2, sort_keys=True)
    except IntakeValidationError as exc:
        result = {
            "status": "needs_refinement",
            "fingerprint": fingerprint,
            "requested_by": requested_by,
            "current_repo": current_repo,
            "known_repos": [repo["repo"] for repo in known_repos],
            "reason": str(exc),
            "created_issues": [],
            "created_at": utc_now(),
        }
        control.record_intake_request(
            fingerprint=fingerprint,
            request_text=request_text,
            current_repo=current_repo,
            requested_by=requested_by,
            status="needs_refinement",
            result=result,
        )
        return 0, json.dumps(result, indent=2, sort_keys=True)


def discover_known_github_repos(project_root: Path) -> list[dict[str, str]]:
    roots: list[Path] = [project_root.resolve()]
    parent = project_root.resolve().parent
    for candidate in sorted(parent.iterdir()) if parent.exists() else []:
        if candidate.resolve() == project_root.resolve():
            continue
        if (candidate / ".git").exists():
            roots.append(candidate.resolve())

    seen: set[str] = set()
    repos: list[dict[str, str]] = []
    for root in roots:
        repo = detect_github_repo(root)
        if not repo or repo in seen:
            continue
        seen.add(repo)
        repos.append({"repo": repo, "path": str(root)})
    return repos


def detect_github_repo(project_root: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(project_root), "config", "--get", "remote.origin.url"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None

    origin = completed.stdout.strip()
    if not origin:
        return None

    match = re.search(r"github\.com[:/](?P<repo>[^/]+/[^/.]+)(?:\.git)?$", origin)
    if not match:
        return None
    return match.group("repo")


def build_intake_fingerprint(
    *,
    request_text: str,
    current_repo: str | None,
    known_repos: list[dict[str, str]],
) -> str:
    normalized_request = " ".join(request_text.lower().split())
    repo_scope = "|".join(sorted(repo["repo"] for repo in known_repos))
    payload = f"{current_repo or ''}\n{repo_scope}\n{normalized_request}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def propose_ticket_plan(
    *,
    project_root: Path,
    request_text: str,
    current_repo: str | None,
    known_repos: list[dict[str, str]],
) -> dict[str, Any]:
    repo_lines = "\n".join(
        f'- "{repo["repo"]}" at "{repo["path"]}"' for repo in known_repos
    )
    prompt = f"""
You are drafting GitHub issues for a deterministic controller.

Rules:
- Output JSON only. No markdown fences. No commentary.
- Use only repo names from the provided known repo list.
- If the request is vague, contradictory, or gibberish, set needs_refinement=true and do not emit tickets.
- Prefer one bounded ticket when possible.
- Split into multiple tickets only for real repo boundaries, dependency boundaries, or distinct validation paths.
- Parent trackers are allowed only when there are multiple related child tickets.
- Child tickets are the executable units.
- Keep titles concise and specific.
- Each ticket must have non-empty acceptance and validation lists.

Known repos:
{repo_lines}

Current repo hint: {current_repo or "unknown"}

Return JSON matching this shape exactly:
{{
  "needs_refinement": true|false,
  "understood": "<short summary>",
  "reason": "<why refinement is needed or null>",
  "parent": {{
    "key": "PARENT",
    "repo": "<known repo>",
    "title": "<tracker title>",
    "summary": "<tracker summary>",
    "acceptance": ["<line>", "..."],
    "validation": ["<line>", "..."],
    "type": "Feature"
  }} | null,
  "tickets": [
    {{
      "key": "API",
      "repo": "<known repo>",
      "title": "<ticket title>",
      "summary": "<ticket summary>",
      "acceptance": ["<line>", "..."],
      "validation": ["<line>", "..."],
      "depends_on": ["<ticket key>", "..."],
      "priority": "P0|P1|P2|P3|null",
      "type": "Feature|Bug|Refactor"
    }}
  ]
}}

User request:
{request_text}
""".strip()

    result = run_codex_iteration(
        cwd=project_root,
        model="gpt-5.4",
        prompt=prompt,
        session_id=None,
        reasoning_effort="high",
        sandbox_mode="workspace-write",
        enable_search=False,
        json_stream=True,
    )
    if result.exit_code != 0:
        raise IntakeValidationError("intake generation failed before producing a valid ticket plan")
    return extract_json_payload(result.final_message, result.raw_lines)


def extract_json_payload(final_message: str, raw_lines: list[str]) -> dict[str, Any]:
    candidates: list[str] = []
    if final_message.strip():
        candidates.append(final_message.strip())
    candidates.extend(reversed([line.strip() for line in raw_lines if line.strip()]))

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise IntakeValidationError("intake output was not valid JSON")


def validate_ticket_plan(
    *,
    proposal: dict[str, Any],
    current_repo: str | None,
    known_repos: list[dict[str, str]],
) -> dict[str, Any]:
    if not isinstance(proposal, dict):
        raise IntakeValidationError("intake output must be a JSON object")
    known_repo_names = {repo["repo"] for repo in known_repos}
    needs_refinement = bool(proposal.get("needs_refinement"))
    understood = _clean_text(proposal.get("understood"))
    reason = _clean_text(proposal.get("reason"))

    if needs_refinement:
        return {
            "needs_refinement": True,
            "understood": understood or current_repo or "Unable to identify a bounded work unit.",
            "reason": reason or "The request needs clarification before creating board work.",
            "parent": None,
            "tickets": [],
        }

    raw_tickets = proposal.get("tickets")
    if not isinstance(raw_tickets, list) or not raw_tickets:
        raise IntakeValidationError("intake output did not include any executable tickets")

    parent = _validate_parent(proposal.get("parent"), known_repo_names)
    tickets: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for index, raw_ticket in enumerate(raw_tickets, start=1):
        if not isinstance(raw_ticket, dict):
            raise IntakeValidationError("every ticket must be an object")
        key = _slug_key(raw_ticket.get("key") or f"T{index}")
        if key in seen_keys:
            raise IntakeValidationError(f"duplicate ticket key: {key}")
        seen_keys.add(key)
        repo = _clean_text(raw_ticket.get("repo"))
        if repo not in known_repo_names:
            raise IntakeValidationError(f"unknown repo in intake plan: {repo or 'missing'}")
        title = _clean_text(raw_ticket.get("title"))
        summary = _clean_text(raw_ticket.get("summary"))
        acceptance = _clean_list(raw_ticket.get("acceptance"))
        validation = _clean_list(raw_ticket.get("validation"))
        if not title or not summary:
            raise IntakeValidationError(f"ticket {key} is missing title or summary")
        if not acceptance:
            raise IntakeValidationError(f"ticket {key} is missing acceptance criteria")
        if not validation:
            raise IntakeValidationError(f"ticket {key} is missing validation steps")
        depends_on = [_slug_key(value) for value in _clean_list(raw_ticket.get("depends_on"))]
        priority = _normalize_priority(raw_ticket.get("priority"))
        issue_type = _normalize_type(raw_ticket.get("type"))
        tickets.append(
            {
                "key": key,
                "repo": repo,
                "title": title,
                "summary": summary,
                "acceptance": acceptance,
                "validation": validation,
                "depends_on": depends_on,
                "priority": priority,
                "type": issue_type,
            }
        )

    known_ticket_keys = {ticket["key"] for ticket in tickets}
    for ticket in tickets:
        unknown_dependencies = [value for value in ticket["depends_on"] if value not in known_ticket_keys]
        if unknown_dependencies:
            raise IntakeValidationError(
                f"ticket {ticket['key']} depends on unknown ticket keys: {', '.join(unknown_dependencies)}"
            )

    if parent and len(tickets) < 2:
        raise IntakeValidationError("a parent tracker is only valid when more than one child ticket exists")

    return {
        "needs_refinement": False,
        "understood": understood or summary_for_tickets(tickets),
        "reason": None,
        "parent": parent,
        "tickets": tickets,
    }


def _validate_parent(value: Any, known_repo_names: set[str]) -> dict[str, Any] | None:
    if value in (None, False):
        return None
    if not isinstance(value, dict):
        raise IntakeValidationError("parent tracker must be an object when present")
    repo = _clean_text(value.get("repo"))
    title = _clean_text(value.get("title"))
    summary = _clean_text(value.get("summary"))
    acceptance = _clean_list(value.get("acceptance"))
    validation = _clean_list(value.get("validation"))
    if repo not in known_repo_names:
        raise IntakeValidationError("parent tracker repo is missing or unknown")
    if not title or not summary:
        raise IntakeValidationError("parent tracker is missing title or summary")
    return {
        "key": _slug_key(value.get("key") or "PARENT"),
        "repo": repo,
        "title": title,
        "summary": summary,
        "acceptance": acceptance or ["Parent tracker reflects the scoped child tickets."],
        "validation": validation or ["Confirm child issue links and dependencies are accurate."],
        "type": _normalize_type(value.get("type")) or "Feature",
        "issue_class": "coordination",
    }


def create_issue_set(
    *,
    project_root: Path,
    current_repo: str | None,
    plan: dict[str, Any],
) -> dict[str, Any]:
    parent = plan.get("parent")
    tickets = [dict(ticket) for ticket in plan["tickets"]]
    reverse_dependencies: dict[str, list[str]] = {ticket["key"]: [] for ticket in tickets}
    for ticket in tickets:
        for dependency in ticket["depends_on"]:
            reverse_dependencies.setdefault(dependency, []).append(ticket["key"])

    created_urls: dict[str, str] = {}
    created_records: list[dict[str, Any]] = []

    if parent:
        body = render_issue_body(
            draft=parent,
            parent_url=None,
            child_urls={},
            dependency_urls={},
            reverse_dependency_urls={},
            child_titles=[ticket["title"] for ticket in tickets],
        )
        issue = create_github_issue(
            project_root=project_root,
            repo=parent["repo"],
            title=parent["title"],
            body=body,
            priority=None,
            issue_type=parent["type"],
        )
        parent["url"] = issue["issueUrl"]
        created_urls[parent["key"]] = issue["issueUrl"]
        created_records.append(
            {
                "key": parent["key"],
                "repo": parent["repo"],
                "title": parent["title"],
                "url": issue["issueUrl"],
                "type": parent["type"],
                "role": "parent",
            }
        )

    for ticket in tickets:
        dependency_urls = {key: created_urls[key] for key in ticket["depends_on"] if key in created_urls}
        body = render_issue_body(
            draft=ticket,
            parent_url=parent["url"] if parent else None,
            child_urls={},
            dependency_urls=dependency_urls,
            reverse_dependency_urls={},
            child_titles=[],
        )
        issue = create_github_issue(
            project_root=project_root,
            repo=ticket["repo"],
            title=ticket["title"],
            body=body,
            priority=ticket["priority"],
            issue_type=ticket["type"],
        )
        ticket["url"] = issue["issueUrl"]
        created_urls[ticket["key"]] = issue["issueUrl"]
        created_records.append(
            {
                "key": ticket["key"],
                "repo": ticket["repo"],
                "title": ticket["title"],
                "url": issue["issueUrl"],
                "type": ticket["type"],
                "role": "child",
            }
        )

    if parent:
        parent_body = render_issue_body(
            draft=parent,
            parent_url=None,
            child_urls={ticket["key"]: ticket["url"] for ticket in tickets},
            dependency_urls={},
            reverse_dependency_urls={},
            child_titles=[],
        )
        edit_github_issue(
            project_root=project_root,
            repo=parent["repo"],
            issue_url=parent["url"],
            body=parent_body,
        )

    for ticket in tickets:
        dependency_urls = {key: created_urls[key] for key in ticket["depends_on"] if key in created_urls}
        reverse_dependency_urls = {
            key: created_urls[key]
            for key in reverse_dependencies.get(ticket["key"], [])
            if key in created_urls
        }
        body = render_issue_body(
            draft=ticket,
            parent_url=parent["url"] if parent else None,
            child_urls={},
            dependency_urls=dependency_urls,
            reverse_dependency_urls=reverse_dependency_urls,
            child_titles=[],
        )
        edit_github_issue(
            project_root=project_root,
            repo=ticket["repo"],
            issue_url=ticket["url"],
            body=body,
        )

    first_runnable = None
    for ticket in tickets:
        if not ticket["depends_on"]:
            first_runnable = next(record for record in created_records if record["key"] == ticket["key"])
            break

    return {
        "parent_issue": next((record for record in created_records if record["role"] == "parent"), None),
        "created_issues": created_records,
        "first_runnable_issue": first_runnable,
        "current_repo": current_repo,
    }


def create_github_issue(
    *,
    project_root: Path,
    repo: str,
    title: str,
    body: str,
    priority: str | None,
    issue_type: str | None,
) -> dict[str, Any]:
    helper = Path.home() / ".codex" / "skills" / "kanban" / "scripts" / "github_project_issue_flow.py"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as handle:
        handle.write(body)
        body_file = handle.name

    try:
        command = [
            "python3",
            str(helper),
            "create-issue",
            "--owner",
            PROJECT_OWNER,
            "--project-number",
            str(PROJECT_NUMBER),
            "--repo",
            repo,
            "--title",
            title,
            "--body-file",
            body_file,
            "--status",
            "Inbox",
        ]
        if priority:
            command.extend(["--priority", priority])
        if issue_type:
            command.extend(["--type", issue_type])
        try:
            completed = subprocess.run(
                command,
                cwd=str(project_root),
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            existing = find_existing_issue_by_title(
                project_root=project_root,
                repo=repo,
                title=title,
            )
            if existing is None:
                stderr = (exc.stderr or "").strip()
                stdout = (exc.stdout or "").strip()
                detail = stderr or stdout or str(exc)
                raise IntakeValidationError(
                    f"issue creation failed for {repo}: {detail}"
                ) from exc
            return existing
    finally:
        Path(body_file).unlink(missing_ok=True)

    try:
        payload = json.loads(completed.stdout.strip() or "{}")
    except json.JSONDecodeError as exc:
        raise IntakeValidationError(f"issue creation returned invalid JSON for {repo}: {exc}") from exc
    if not isinstance(payload, dict) or not str(payload.get("issueUrl") or "").strip():
        raise IntakeValidationError(f"issue creation did not return an issue URL for {repo}")
    return payload


def find_existing_issue_by_title(
    *,
    project_root: Path,
    repo: str,
    title: str,
) -> dict[str, Any] | None:
    completed = subprocess.run(
        [
            "gh",
            "issue",
            "list",
            "--repo",
            repo,
            "--state",
            "all",
            "--search",
            f'"{title}" in:title',
            "--limit",
            "20",
            "--json",
            "number,title,url",
        ],
        cwd=str(project_root),
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        payload = json.loads(completed.stdout.strip() or "[]")
    except json.JSONDecodeError as exc:
        raise IntakeValidationError(
            f"issue lookup returned invalid JSON for {repo}: {exc}"
        ) from exc
    if not isinstance(payload, list):
        raise IntakeValidationError(f"issue lookup returned invalid payload for {repo}")
    for item in payload:
        if not isinstance(item, dict):
            continue
        if str(item.get("title") or "").strip() != title:
            continue
        issue_url = str(item.get("url") or "").strip()
        if not issue_url:
            continue
        return {
            "ok": True,
            "issueUrl": issue_url,
            "repo": repo,
            "item": {
                "title": title,
            },
        }
    return None


def edit_github_issue(*, project_root: Path, repo: str, issue_url: str, body: str) -> None:
    number = issue_url.rstrip("/").split("/")[-1]
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as handle:
        handle.write(body)
        body_file = handle.name

    try:
        subprocess.run(
            [
                "gh",
                "issue",
                "edit",
                number,
                "--repo",
                repo,
                "--body-file",
                body_file,
            ],
            cwd=str(project_root),
            check=True,
            capture_output=True,
            text=True,
        )
    finally:
        Path(body_file).unlink(missing_ok=True)


def render_issue_body(
    *,
    draft: dict[str, Any],
    parent_url: str | None,
    child_urls: dict[str, str],
    dependency_urls: dict[str, str],
    reverse_dependency_urls: dict[str, str],
    child_titles: list[str],
) -> str:
    lines = [
        "## Problem",
        "",
        draft["summary"],
        "",
        "## Desired Outcome",
        "",
        draft["acceptance"][0] if draft["acceptance"] else draft["summary"],
        "",
        "## Acceptance Criteria",
        "",
    ]
    lines.extend(f"- [ ] {line}" for line in draft["acceptance"])
    lines.extend(["", "## Validation", ""])
    lines.extend(f"- {line}" for line in draft["validation"])
    lines.extend(["", "## Ticket Relations", ""])
    lines.append(f"- Parent: {parent_url or 'none'}")
    if child_urls:
        children_value = ", ".join(child_urls[key] for key in sorted(child_urls))
    elif child_titles:
        children_value = ", ".join(child_titles)
    else:
        children_value = "none"
    lines.append(f"- Children: {children_value}")
    blocked_by = ", ".join(dependency_urls[key] for key in sorted(dependency_urls)) or "none"
    lines.append(f"- Blocked by: {blocked_by}")
    unblocks = ", ".join(reverse_dependency_urls[key] for key in sorted(reverse_dependency_urls)) or "none"
    lines.append(f"- Unblocks: {unblocks}")
    lines.extend(["", "## Execution Routing", ""])
    repo_slug = draft["repo"].split("/", 1)[-1]
    branch_slug = _branch_slug(draft["key"], draft["title"])
    lines.append(f"- Worktree: {repo_slug}@{branch_slug}")
    lines.append(f"- Branch: {branch_slug}")
    depends_on = ", ".join(dependency_urls[key] for key in sorted(dependency_urls)) or "none"
    lines.append(f"- Depends on: {depends_on}")
    lines.append("- Merge into: main")
    if _clean_text(draft.get("issue_class")):
        lines.append(f"- Type: {draft['issue_class']}")
    return "\n".join(lines).strip() + "\n"


def summary_for_tickets(tickets: list[dict[str, Any]]) -> str:
    repos = sorted({ticket["repo"] for ticket in tickets})
    if len(tickets) == 1:
        return f"One bounded ticket in {repos[0]}."
    return f"{len(tickets)} related tickets across {', '.join(repos)}."


def _clean_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _clean_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    cleaned: list[str] = []
    for entry in value:
        text = _clean_text(entry)
        if text:
            cleaned.append(text)
    return cleaned


def _normalize_priority(value: Any) -> str | None:
    text = _clean_text(value)
    if not text:
        return None
    candidate = text.upper()
    if candidate in {"P0", "P1", "P2", "P3"}:
        return candidate
    return None


def _normalize_type(value: Any) -> str | None:
    text = _clean_text(value)
    if not text:
        return None
    lowered = text.lower()
    if lowered == "feature":
        return "Feature"
    if lowered == "bug":
        return "Bug"
    if lowered == "refactor":
        return "Refactor"
    return None


def _slug_key(value: Any) -> str:
    text = _clean_text(value) or "TICKET"
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").upper()
    return normalized or "TICKET"


def _branch_slug(key: str, title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", f"{key.lower()}-{title.lower()}").strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return f"issue/{slug[:48] or key.lower()}"
