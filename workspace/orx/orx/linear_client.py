"""Minimal runtime Linear GraphQL client for ORX issue CRUD."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from typing import Any
from urllib import request

from .env import load_repo_env

LINEAR_GRAPHQL_URL = "https://api.linear.app/graphql"


class LinearClientError(RuntimeError):
    """Raised when a Linear API request cannot be completed."""


@dataclass(frozen=True)
class LinearCreatedIssue:
    linear_id: str
    title: str
    identifier: str | None = None
    url: str | None = None


@dataclass(frozen=True)
class LinearIssue:
    linear_id: str
    identifier: str | None
    title: str
    description: str
    url: str | None
    team_id: str | None
    team_name: str | None
    state_id: str | None
    state_name: str | None
    state_type: str | None
    parent_id: str | None
    parent_identifier: str | None
    project_id: str | None
    project_name: str | None


@dataclass(frozen=True)
class LinearWorkflowState:
    state_id: str
    name: str
    type: str | None


class LinearGraphQLClient:
    def __init__(self, *, api_key: str, endpoint: str = LINEAR_GRAPHQL_URL) -> None:
        if not api_key.strip():
            raise ValueError("Linear API key cannot be empty.")
        self.api_key = api_key.strip()
        self.endpoint = endpoint

    @classmethod
    def from_env(cls) -> "LinearGraphQLClient":
        load_repo_env()
        api_key = os.environ.get("ORX_LINEAR_API_KEY") or os.environ.get("LINEAR_API_KEY")
        if api_key is None or not api_key.strip():
            raise LinearClientError(
                "Set ORX_LINEAR_API_KEY or LINEAR_API_KEY to materialize proposals into Linear."
            )
        endpoint = os.environ.get("ORX_LINEAR_API_URL", LINEAR_GRAPHQL_URL)
        return cls(api_key=api_key, endpoint=endpoint)

    def get_issue(self, *, issue_ref: str) -> LinearIssue | None:
        try:
            payload = self._graphql(
                """
                query Issue($id: String!) {
                  issue(id: $id) {
                    id
                    identifier
                    title
                    description
                    url
                    team {
                      id
                      name
                    }
                    state {
                      id
                      name
                      type
                    }
                    parent {
                      id
                      identifier
                    }
                    project {
                      id
                      name
                    }
                  }
                }
                """,
                {"id": issue_ref},
            ).get("issue")
        except LinearClientError as error:
            if "Entity not found: Issue" in str(error):
                return None
            raise
        if payload is None:
            return None
        if not isinstance(payload, dict):
            raise LinearClientError("Linear issue query returned an unexpected issue payload.")
        return _parse_issue(payload, error_prefix="Linear issue query")

    def create_issue(
        self,
        *,
        team_id: str,
        title: str,
        description: str,
        parent_id: str | None = None,
        project_id: str | None = None,
    ) -> LinearIssue:
        payload = self._graphql(
            """
            mutation IssueCreate($input: IssueCreateInput!) {
              issueCreate(input: $input) {
                success
                issue {
                  id
                  identifier
                  title
                  description
                  url
                  team {
                    id
                    name
                  }
                  state {
                    id
                    name
                    type
                  }
                  parent {
                    id
                    identifier
                  }
                  project {
                    id
                    name
                  }
                }
              }
            }
            """,
            {
                "input": {
                    "teamId": team_id,
                    "title": title,
                    "description": description,
                    **({"parentId": parent_id} if parent_id is not None else {}),
                    **({"projectId": project_id} if project_id is not None else {}),
                }
            },
        ).get("issueCreate")
        if not isinstance(payload, dict) or not payload.get("success"):
            raise LinearClientError("Linear issueCreate mutation did not succeed.")
        issue = payload.get("issue")
        if not isinstance(issue, dict):
            raise LinearClientError("Linear issueCreate mutation returned no issue payload.")
        return _parse_issue(issue, error_prefix="Linear issueCreate mutation")

    def update_issue(
        self,
        *,
        issue_ref: str,
        title: str | None = None,
        description: str | None = None,
        state_id: str | None = None,
    ) -> LinearIssue:
        input_payload: dict[str, Any] = {}
        if title is not None:
            input_payload["title"] = title
        if description is not None:
            input_payload["description"] = description
        if state_id is not None:
            input_payload["stateId"] = state_id
        if not input_payload:
            raise ValueError("update_issue requires at least one updated field.")

        payload = self._graphql(
            """
            mutation IssueUpdate($id: String!, $input: IssueUpdateInput!) {
              issueUpdate(id: $id, input: $input) {
                success
                issue {
                  id
                  identifier
                  title
                  description
                  url
                  team {
                    id
                    name
                  }
                  state {
                    id
                    name
                    type
                  }
                  parent {
                    id
                    identifier
                  }
                  project {
                    id
                    name
                  }
                }
              }
            }
            """,
            {
                "id": self._resolve_issue_id(issue_ref),
                "input": input_payload,
            },
        ).get("issueUpdate")
        if not isinstance(payload, dict) or not payload.get("success"):
            raise LinearClientError("Linear issueUpdate mutation did not succeed.")
        issue = payload.get("issue")
        if not isinstance(issue, dict):
            raise LinearClientError("Linear issueUpdate mutation returned no issue payload.")
        return _parse_issue(issue, error_prefix="Linear issueUpdate mutation")

    def archive_issue(self, *, issue_ref: str, trash: bool = False) -> LinearIssue:
        return self._archive_like_mutation(
            mutation_name="issueArchive",
            issue_ref=issue_ref,
            trash=trash,
        )

    def complete_issue(self, *, issue_ref: str, team_id: str) -> LinearIssue:
        state_id = self._resolve_completed_state_id(team_id=team_id)
        return self.update_issue(issue_ref=issue_ref, state_id=state_id)

    def delete_issue(self, *, issue_ref: str) -> LinearIssue:
        payload = self._graphql(
            """
            mutation IssueDelete($id: String!, $permanentlyDelete: Boolean) {
              issueDelete(id: $id, permanentlyDelete: $permanentlyDelete) {
                success
                entity {
                  id
                  identifier
                  title
                  description
                  url
                }
              }
            }
            """,
            {
                "id": self._resolve_issue_id(issue_ref),
                "permanentlyDelete": True,
            },
        ).get("issueDelete")
        if not isinstance(payload, dict) or not payload.get("success"):
            raise LinearClientError("Linear issueDelete mutation did not succeed.")
        issue = payload.get("entity")
        if not isinstance(issue, dict):
            raise LinearClientError("Linear issueDelete mutation returned no issue entity.")
        return _parse_issue(issue, error_prefix="Linear issueDelete mutation")

    def _archive_like_mutation(
        self,
        *,
        mutation_name: str,
        issue_ref: str,
        trash: bool,
    ) -> LinearIssue:
        payload = self._graphql(
            f"""
            mutation IssueArchiveLike($id: String!, $trash: Boolean) {{
              {mutation_name}(id: $id, trash: $trash) {{
                success
                entity {{
                  id
                  identifier
                  title
                  description
                  url
                  team {{
                    id
                    name
                  }}
                  state {{
                    id
                    name
                    type
                  }}
                  parent {{
                    id
                    identifier
                  }}
                  project {{
                    id
                    name
                  }}
                }}
              }}
            }}
            """,
            {
                "id": self._resolve_issue_id(issue_ref),
                "trash": trash,
            },
        ).get(mutation_name)
        if not isinstance(payload, dict) or not payload.get("success"):
            raise LinearClientError(f"Linear {mutation_name} mutation did not succeed.")
        issue = payload.get("entity")
        if not isinstance(issue, dict):
            raise LinearClientError(f"Linear {mutation_name} mutation returned no issue entity.")
        return _parse_issue(issue, error_prefix=f"Linear {mutation_name} mutation")

    def _resolve_completed_state_id(self, *, team_id: str) -> str:
        payload = self._graphql(
            """
            query TeamStates($id: String!) {
              team(id: $id) {
                states {
                  nodes {
                    id
                    name
                    type
                  }
                }
              }
            }
            """,
            {"id": team_id},
        ).get("team")
        if not isinstance(payload, dict):
            raise LinearClientError("Linear team states query returned no team payload.")
        states_payload = payload.get("states")
        nodes = states_payload.get("nodes") if isinstance(states_payload, dict) else None
        if not isinstance(nodes, list):
            raise LinearClientError("Linear team states query returned no workflow states.")

        states = [_parse_workflow_state(node) for node in nodes if isinstance(node, dict)]
        for state in states:
            if (state.type or "").strip().lower() == "completed":
                return state.state_id
        for state in states:
            if state.name.strip().lower() == "done":
                return state.state_id
        raise LinearClientError(f"No completed workflow state found for team {team_id}.")

    def _resolve_issue_id(self, issue_ref: str) -> str:
        try:
            parsed = uuid.UUID(issue_ref)
        except ValueError:
            parsed = None
        if parsed is not None:
            return str(parsed)
        issue = self.get_issue(issue_ref=issue_ref)
        if issue is None:
            raise LinearClientError(f"Linear issue {issue_ref!r} was not found.")
        return issue.linear_id

    def _graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
        http_request = request.Request(
            self.endpoint,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": self.api_key,
            },
            method="POST",
        )
        try:
            with request.urlopen(http_request) as response:  # noqa: S310
                raw = response.read().decode("utf-8")
        except Exception as error:  # pragma: no cover - network boundary
            raise LinearClientError(f"Linear API request failed: {error}") from error

        try:
            document = json.loads(raw)
        except json.JSONDecodeError as error:
            raise LinearClientError("Linear API returned invalid JSON.") from error

        if not isinstance(document, dict):
            raise LinearClientError("Linear API returned an unexpected response shape.")
        errors = document.get("errors")
        if isinstance(errors, list) and errors:
            messages = [
                item.get("message", "unknown error")
                for item in errors
                if isinstance(item, dict)
            ]
            raise LinearClientError(f"Linear API returned errors: {', '.join(messages)}")

        data = document.get("data")
        if not isinstance(data, dict):
            raise LinearClientError("Linear API returned no data payload.")
        return data


def _parse_issue(payload: dict[str, Any], *, error_prefix: str) -> LinearIssue:
    linear_id = payload.get("id")
    title = payload.get("title")
    if not isinstance(linear_id, str) or not linear_id.strip():
        raise LinearClientError(f"{error_prefix} returned no issue id.")
    if not isinstance(title, str) or not title.strip():
        raise LinearClientError(f"{error_prefix} returned no issue title.")

    description = payload.get("description")
    identifier = payload.get("identifier")
    url = payload.get("url")
    team = payload.get("team") if isinstance(payload.get("team"), dict) else {}
    state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
    parent = payload.get("parent") if isinstance(payload.get("parent"), dict) else {}
    project = payload.get("project") if isinstance(payload.get("project"), dict) else {}
    return LinearIssue(
        linear_id=linear_id.strip(),
        identifier=identifier.strip() if isinstance(identifier, str) and identifier.strip() else None,
        title=title.strip(),
        description=description if isinstance(description, str) else "",
        url=url.strip() if isinstance(url, str) and url.strip() else None,
        team_id=team.get("id") if isinstance(team.get("id"), str) and team.get("id").strip() else None,
        team_name=team.get("name") if isinstance(team.get("name"), str) and team.get("name").strip() else None,
        state_id=state.get("id") if isinstance(state.get("id"), str) and state.get("id").strip() else None,
        state_name=state.get("name") if isinstance(state.get("name"), str) and state.get("name").strip() else None,
        state_type=state.get("type") if isinstance(state.get("type"), str) and state.get("type").strip() else None,
        parent_id=parent.get("id") if isinstance(parent.get("id"), str) and parent.get("id").strip() else None,
        parent_identifier=(
            parent.get("identifier")
            if isinstance(parent.get("identifier"), str) and parent.get("identifier").strip()
            else None
        ),
        project_id=project.get("id") if isinstance(project.get("id"), str) and project.get("id").strip() else None,
        project_name=(
            project.get("name")
            if isinstance(project.get("name"), str) and project.get("name").strip()
            else None
        ),
    )


def _parse_workflow_state(payload: dict[str, Any]) -> LinearWorkflowState:
    state_id = payload.get("id")
    name = payload.get("name")
    if not isinstance(state_id, str) or not state_id.strip():
        raise LinearClientError("Linear workflow state returned no id.")
    if not isinstance(name, str) or not name.strip():
        raise LinearClientError("Linear workflow state returned no name.")
    state_type = payload.get("type")
    return LinearWorkflowState(
        state_id=state_id.strip(),
        name=name.strip(),
        type=state_type.strip() if isinstance(state_type, str) and state_type.strip() else None,
    )
