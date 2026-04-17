"""ORX/Linear-native runner context helpers."""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, parse, request

ORX_API_URL_DEFAULT = "http://127.0.0.1:9467"
METADATA_START = "<!-- orx:metadata:start -->"
METADATA_END = "<!-- orx:metadata:end -->"
_METADATA_BLOCK_RE = re.compile(
    re.escape(METADATA_START) + r"\s*(.*?)\s*" + re.escape(METADATA_END),
    re.DOTALL,
)


class OrxControlError(RuntimeError):
    """Raised when ORX/Linear control-plane requests fail."""


@dataclass(frozen=True)
class PreparedIssueContext:
    """Normalized issue/worktree context for the tmux-codex runner."""

    issue: dict[str, Any]
    snapshot: dict[str, Any]
    phase: str
    project_root: Path
    worktree_path: Path
    branch: str
    linear_url: str | None


def orx_api_url() -> str:
    return os.environ.get("ORX_API_URL", ORX_API_URL_DEFAULT).rstrip("/")


def fetch_project_context(*, project_key: str) -> dict[str, Any]:
    payload = _request_json("GET", f"/control/context?project_key={parse.quote(project_key)}")
    context = payload.get("context")
    if not isinstance(context, dict):
        raise OrxControlError(f"ORX returned invalid project context for {project_key}.")
    return context


def fetch_dashboard() -> dict[str, Any]:
    payload = _request_json("GET", "/dashboard")
    projects = payload.get("projects")
    if not isinstance(projects, list):
        raise OrxControlError("ORX returned invalid dashboard payload.")
    return payload


def fetch_linear_issue(*, issue_ref: str) -> dict[str, Any] | None:
    payload = _request_json("GET", f"/linear/issues?issue={parse.quote(issue_ref)}")
    issue = payload.get("issue")
    if issue is None:
        return None
    if not isinstance(issue, dict):
        raise OrxControlError(f"ORX returned invalid Linear issue payload for {issue_ref}.")
    return issue


def update_linear_issue(
    *,
    issue_ref: str,
    title: str | None = None,
    description: str | None = None,
    state_id: str | None = None,
) -> dict[str, Any] | None:
    body: dict[str, Any] = {"issue": issue_ref}
    if title is not None:
        body["title"] = title
    if description is not None:
        body["description"] = description
    if state_id is not None:
        body["state_id"] = state_id
    payload = _request_json("PATCH", "/linear/issues", body)
    issue = payload.get("issue")
    if issue is None:
        return None
    if not isinstance(issue, dict):
        raise OrxControlError(f"ORX returned invalid updated issue payload for {issue_ref}.")
    return issue


def prepare_linear_issue_context(
    *,
    dev: str,
    project_key: str,
    project_context: dict[str, Any],
    issue: dict[str, Any],
    runner_id: str = "main",
    update_issue_metadata: bool = True,
) -> PreparedIssueContext:
    project_record = project_context.get("project") if isinstance(project_context.get("project"), dict) else {}
    project_root = Path(
        _required_text(
            project_context.get("repo_root") or project_record.get("repo_root"),
            field_name="project.repo_root",
        )
    ).expanduser().resolve()
    if not project_root.exists():
        raise OrxControlError(f"Project root does not exist for {project_key}: {project_root}")

    description = _as_text(issue.get("description"))
    metadata = parse_issue_metadata(description)
    identifier = _required_text(issue.get("identifier"), field_name="issue.identifier")
    branch = _as_text(metadata.get("branch")) or _issue_branch_name(identifier)
    worktree_path = Path(
        _as_text(metadata.get("worktree_path"))
        or _as_text(metadata.get("worktree"))
        or str(_default_worktree_path(dev=dev, project_key=project_key, identifier=identifier))
    ).expanduser().resolve()
    linear_url = issue_url(issue)

    ensure_issue_worktree(
        repo_root=project_root,
        worktree_path=worktree_path,
        branch=branch,
    )

    metadata_updates: dict[str, Any] = {
        "project_key": project_key,
        "project_name": (
            _as_text(issue.get("project_name"))
            or _as_text(project_context.get("display_name"))
            or _as_text(project_record.get("display_name"))
            or project_key
        ),
        "repo_root": str(project_root),
        "worktree_path": str(worktree_path),
        "branch": branch,
        "runner_id": runner_id,
        "selection_lane": "orx_linear",
        "linear_identifier": identifier,
    }
    if linear_url:
        metadata_updates["linear_url"] = linear_url

    merged_metadata = dict(metadata)
    merged_metadata.update(metadata_updates)
    updated_description = merge_issue_metadata(description, merged_metadata)
    if update_issue_metadata and updated_description != description:
        refreshed = update_linear_issue(issue_ref=identifier, description=updated_description)
        if isinstance(refreshed, dict):
            issue = refreshed
    issue = dict(issue)
    issue["description"] = updated_description
    issue["metadata"] = merged_metadata
    execution_brief = build_issue_execution_brief(issue)

    snapshot = {
        "url": linear_url or f"linear://issue/{identifier}",
        "external_url": linear_url,
        "repo": project_key,
        "number": None,
        "identifier": identifier,
        "linear_id": _as_text(issue.get("linear_id")) or None,
        "title": _as_text(issue.get("title")) or identifier,
        "description": updated_description,
        "issue_class": _as_text(merged_metadata.get("issue_class")) or _as_text(merged_metadata.get("type")),
        "complexity": _as_text(merged_metadata.get("complexity")),
        "routing": _as_text(merged_metadata.get("routing")),
        "parent": _as_text(merged_metadata.get("parent")) or _as_text(issue.get("parent_identifier")),
        "blocked_by": _join_metadata_list(merged_metadata.get("blocked_by")),
        "depends_on": _join_metadata_list(merged_metadata.get("depends_on")),
        "unblocks": _join_metadata_list(merged_metadata.get("unblocks")),
        "worktree": str(worktree_path),
        "branch": branch,
        "merge_into": _as_text(merged_metadata.get("merge_into")),
        "resume_from": _as_text(merged_metadata.get("resume_from")),
        "project_key": project_key,
        "project_name": (
            _as_text(issue.get("project_name"))
            or _as_text(project_context.get("display_name"))
            or _as_text(project_record.get("display_name"))
            or project_key
        ),
        "repo_root": str(project_root),
        "state_name": _as_text(issue.get("state_name")),
        "state_type": _as_text(issue.get("state_type")),
        "team_name": _as_text(issue.get("team_name")),
        "priority": issue.get("priority"),
        "metadata": merged_metadata,
        "execution_brief": execution_brief,
    }
    return PreparedIssueContext(
        issue=issue,
        snapshot=snapshot,
        phase=_phase_from_state(issue),
        project_root=project_root,
        worktree_path=worktree_path,
        branch=branch,
        linear_url=linear_url,
    )


def parse_issue_metadata(description: str) -> dict[str, Any]:
    text = str(description or "")
    match = _METADATA_BLOCK_RE.search(text)
    if match is None:
        return {}
    raw = match.group(1).strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def merge_issue_metadata(description: str, metadata: dict[str, Any]) -> str:
    clean_description = str(description or "").strip()
    block = f"{METADATA_START}\n{json.dumps(metadata, indent=2, sort_keys=True)}\n{METADATA_END}"
    if _METADATA_BLOCK_RE.search(clean_description):
        return _METADATA_BLOCK_RE.sub(block, clean_description).strip() + "\n"
    if clean_description:
        return clean_description.rstrip() + "\n\n" + block + "\n"
    return block + "\n"


def parse_ticket_sections(description: str) -> dict[str, dict[str, Any]]:
    sections: dict[str, dict[str, Any]] = {}
    current_section: str | None = None
    current_subsection: str | None = None
    in_metadata_block = False
    for raw_line in str(description or "").splitlines():
        stripped = raw_line.strip()
        if stripped == METADATA_START:
            in_metadata_block = True
            continue
        if stripped == METADATA_END:
            in_metadata_block = False
            continue
        if in_metadata_block:
            continue
        if stripped.startswith("## "):
            current_section = _normalize_heading(stripped[3:])
            current_subsection = None
            sections.setdefault(current_section, {"lines": [], "subsections": {}})
            continue
        if stripped.startswith("### "):
            if current_section is None:
                continue
            current_subsection = _normalize_heading(stripped[4:])
            sections[current_section]["subsections"].setdefault(current_subsection, [])
            continue
        if current_section is None:
            continue
        target = (
            sections[current_section]["subsections"].setdefault(current_subsection, [])
            if current_subsection is not None
            else sections[current_section]["lines"]
        )
        target.append(raw_line.rstrip())
    return sections


def build_issue_execution_brief(issue: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(issue, dict):
        return None
    description = _as_text(issue.get("description"))
    metadata = issue.get("metadata") if isinstance(issue.get("metadata"), dict) else parse_issue_metadata(description)
    compact_brief = _normalize_compact_execution_brief(metadata.get("codex_execution_brief"))
    if compact_brief is not None:
        return compact_brief
    sections = parse_ticket_sections(description)
    title = _section_text(sections.get("title")) or _as_text(issue.get("title")) or _as_text(issue.get("identifier"))
    if not title:
        return None

    scope_section = sections.get("scope") or {}
    scope_in = _coalesce_text_list(
        _section_bullets(scope_section.get("subsections", {}).get("in scope")),
        metadata.get("codex_context_scope_in"),
    )
    if not scope_in:
        scope_in = _compact_lines(description, limit=3, line_chars=180)
    scope_out = _coalesce_text_list(
        _section_bullets(scope_section.get("subsections", {}).get("out of scope")),
        metadata.get("codex_context_scope_out"),
    )
    requirements = _coalesce_text_list(
        _section_bullets((sections.get("requirements") or {}).get("lines")),
        metadata.get("codex_context_requirements"),
    )
    acceptance = _coalesce_text_list(
        _section_bullets((sections.get("acceptance criteria") or {}).get("lines")),
        metadata.get("codex_context_acceptance_criteria"),
    )
    definition_of_done = _coalesce_text_list(
        _section_bullets((sections.get("definition of done") or {}).get("lines")),
        metadata.get("codex_context_definition_of_done"),
    )
    technical_notes = _coalesce_text_list(
        _section_bullets((sections.get("technical notes") or {}).get("lines")),
        metadata.get("codex_context_technical_notes"),
    )
    execution_context = _section_bullets((sections.get("execution context") or {}).get("lines"))
    problem = _section_text(sections.get("why")) or _as_text(metadata.get("codex_context_why"))
    goal = _section_text(sections.get("goal")) or _as_text(metadata.get("codex_context_goal")) or title

    constraints = list(requirements)
    constraints.extend(execution_context)
    constraints.extend(technical_notes)
    if scope_out:
        constraints.append(f"Out of scope: {'; '.join(scope_out[:3])}")
    constraints = _dedupe_lines(constraints)

    success_criteria = list(acceptance)
    success_criteria.extend(f"Definition of done: {item}" for item in definition_of_done)
    success_criteria = _dedupe_lines(success_criteria)

    brief: dict[str, Any] = {
        "objective_title": title,
        "problem": problem,
        "goal": goal,
        "scope_in": scope_in or [f"Execute {title}."],
        "scope_out": scope_out,
        "success_criteria": success_criteria or [f"Complete {title} with recorded verification evidence."],
        "constraints": constraints,
    }
    return brief


def _normalize_compact_execution_brief(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    objective_title = _as_text(raw.get("objective_title"))
    if not objective_title:
        return None
    return {
        "objective_title": objective_title,
        "problem": _as_text(raw.get("problem")) or None,
        "goal": _as_text(raw.get("goal")) or objective_title,
        "scope_in": _coalesce_text_list(raw.get("scope_in"), None) or [f"Execute {objective_title}."],
        "scope_out": _coalesce_text_list(raw.get("scope_out"), None),
        "success_criteria": (
            _coalesce_text_list(raw.get("success_criteria"), None)
            or [f"Complete {objective_title} with recorded verification evidence."]
        ),
        "constraints": _coalesce_text_list(raw.get("constraints"), None),
    }


def issue_url(issue: dict[str, Any]) -> str | None:
    metadata = issue.get("metadata") if isinstance(issue.get("metadata"), dict) else {}
    for key in ("url", "linear_url", "issue_url"):
        candidate = _as_text(issue.get(key)) or _as_text(metadata.get(key))
        if candidate:
            return candidate
    identifier = _as_text(issue.get("identifier"))
    if not identifier:
        return None


def _normalize_heading(value: str) -> str:
    return " ".join(str(value).strip().lower().split())


def _section_text(section: dict[str, Any] | None) -> str | None:
    if not isinstance(section, dict):
        return None
    lines = [str(line).strip() for line in section.get("lines", []) if str(line).strip()]
    if not lines:
        return None
    return " ".join(lines)


def _section_bullets(lines: Any) -> list[str]:
    if not isinstance(lines, list):
        return []
    bullets: list[str] = []
    for raw in lines:
        text = str(raw).strip()
        if not text:
            continue
        if text.startswith("- "):
            text = text[2:].strip()
        bullets.append(text)
    return bullets


def _coalesce_text_list(*candidates: Any) -> list[str]:
    for candidate in candidates:
        if isinstance(candidate, list):
            values = [str(item).strip() for item in candidate if str(item).strip()]
            if values:
                return values
    return []


def _compact_lines(text: str, *, limit: int, line_chars: int) -> list[str]:
    values: list[str] = []
    for line in str(text or "").splitlines():
        compact = " ".join(line.strip().split())
        if not compact or compact.startswith("#") or compact.startswith("<!--"):
            continue
        values.append(compact[:line_chars])
        if len(values) >= limit:
            break
    return values


def _dedupe_lines(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        normalized = str(value).strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(normalized)
    return deduped
    slug = (
        os.environ.get("LINEAR_WORKSPACE_SLUG")
        or os.environ.get("ORX_LINEAR_WORKSPACE_SLUG")
        or "jkprojects"
    ).strip()
    return f"https://linear.app/{slug}/issue/{identifier}"


def ensure_issue_worktree(*, repo_root: Path, worktree_path: Path, branch: str) -> None:
    repo_root = repo_root.expanduser().resolve()
    worktree_path = worktree_path.expanduser().resolve()
    if worktree_path == repo_root:
        return
    if _looks_like_git_worktree(worktree_path):
        return
    if worktree_path.exists() and any(worktree_path.iterdir()):
        raise OrxControlError(f"Refusing to reuse non-empty non-git worktree path: {worktree_path}")
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    branch_exists = _git_branch_exists(repo_root=repo_root, branch=branch)
    command = ["git", "worktree", "add"]
    if not branch_exists:
        command.extend(["-b", branch])
    command.append(str(worktree_path))
    if branch_exists:
        command.append(branch)
    else:
        command.append("HEAD")
    try:
        subprocess.run(
            command,
            cwd=str(repo_root),
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise OrxControlError(
            f"Failed to provision worktree {worktree_path} for branch {branch}: {detail or exc.returncode}"
        ) from exc


def _request_json(method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    url = f"{orx_api_url()}{path}"
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = request.Request(url, method=method, data=data, headers=headers)
    try:
        with request.urlopen(req, timeout=10) as response:
            raw = response.read().decode("utf-8")
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise OrxControlError(f"ORX {method} {path} failed: {exc.code} {body.strip()}") from exc
    except error.URLError as exc:
        raise OrxControlError(f"ORX {method} {path} failed: {exc.reason}") from exc
    try:
        payload_json = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise OrxControlError(f"ORX returned invalid JSON for {method} {path}: {exc}") from exc
    if not isinstance(payload_json, dict):
        raise OrxControlError(f"ORX returned non-object JSON for {method} {path}.")
    return payload_json


def _phase_from_state(issue: dict[str, Any]) -> str:
    state_type = _as_text(issue.get("state_type")).lower()
    if state_type in {"started", "in_progress"}:
        return "executing"
    if state_type in {"completed", "canceled"}:
        return "done"
    if state_type in {"triage", "backlog", "unstarted"}:
        return "selecting"
    return "selecting"


def _default_worktree_path(*, dev: str, project_key: str, identifier: str) -> Path:
    return Path(dev).expanduser().resolve() / "worktrees" / project_key / identifier.lower()


def _issue_branch_name(identifier: str) -> str:
    slug = re.sub(r"[^a-z0-9._/-]+", "-", identifier.strip().lower()).strip("-")
    return f"linear/{slug or 'issue'}"


def _git_branch_exists(*, repo_root: Path, branch: str) -> bool:
    result = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=str(repo_root),
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _looks_like_git_worktree(path: Path) -> bool:
    return path.exists() and ((path / ".git").exists() or (path / ".git").is_file())


def _join_metadata_list(raw: Any) -> str | None:
    if isinstance(raw, list):
        values = [str(item).strip() for item in raw if str(item).strip()]
        if values:
            return ", ".join(values)
        return None
    return _as_text(raw) or None


def _required_text(value: Any, *, field_name: str) -> str:
    text = _as_text(value)
    if not text:
        raise OrxControlError(f"Missing required field: {field_name}")
    return text


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()
