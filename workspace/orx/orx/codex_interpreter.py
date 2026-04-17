"""Optional Codex-backed interpretation helpers for ORX handoffs."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any
from urllib import error, request

DEFAULT_CODEX_MODEL = "gpt-5.4"
DEFAULT_CODEX_REASONING = "high"
DEFAULT_ENDPOINT = "https://api.openai.com/v1/responses"
ALLOWED_ACTIONS = {
    "continue",
    "blocked",
    "reroute",
    "replan",
    "needs_human_help",
    "complete",
}


@dataclass(frozen=True)
class CodexInterpretation:
    action: str | None
    next_slice: str | None
    next_step_hint: str | None
    follow_ups: tuple[dict[str, Any], ...]
    reasoning: str | None


class CodexInterpreterError(RuntimeError):
    pass


class CodexHandoffInterpreter:
    def __init__(
        self,
        *,
        api_key: str,
        endpoint: str = DEFAULT_ENDPOINT,
        model: str = DEFAULT_CODEX_MODEL,
        reasoning_effort: str = DEFAULT_CODEX_REASONING,
    ) -> None:
        if not api_key.strip():
            raise CodexInterpreterError("OpenAI API key is required.")
        self.api_key = api_key.strip()
        self.endpoint = endpoint.strip() or DEFAULT_ENDPOINT
        self.model = model.strip() or DEFAULT_CODEX_MODEL
        self.reasoning_effort = reasoning_effort.strip() or DEFAULT_CODEX_REASONING

    @classmethod
    def from_env(cls) -> "CodexHandoffInterpreter | None":
        api_key = os.environ.get("ORX_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if api_key is None or not api_key.strip():
            return None
        model = os.environ.get("ORX_HANDOFF_INTERPRETER_MODEL") or DEFAULT_CODEX_MODEL
        reasoning = os.environ.get("ORX_HANDOFF_INTERPRETER_REASONING") or DEFAULT_CODEX_REASONING
        endpoint = os.environ.get("ORX_OPENAI_RESPONSES_URL") or DEFAULT_ENDPOINT
        return cls(
            api_key=api_key,
            endpoint=endpoint,
            model=model,
            reasoning_effort=reasoning,
        )

    def interpret(self, *, context: dict[str, Any]) -> CodexInterpretation | None:
        payload = {
            "model": self.model,
            "reasoning": {"effort": self.reasoning_effort},
            "text": {"format": {"type": "json_object"}},
            "input": [
                {
                    "role": "developer",
                    "content": (
                        "You are ORX's handoff interpreter. Output JSON only. "
                        "Interpret the factual execution handoff and choose one action from: "
                        "continue, blocked, reroute, replan, needs_human_help, complete. "
                        "Do not invent workflow outside the given issue. "
                        "If follow-up tickets are needed, include compact follow_ups with title, why, goal, "
                        "scope_in, and acceptance."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Interpret this ORX slice handoff as JSON.\n"
                        + json.dumps(
                            {
                                "issue": context.get("issue"),
                                "latest_handoff": context.get("latest_handoff"),
                                "continuity": context.get("continuity"),
                                "slice_result": context.get("slice_result"),
                            },
                            sort_keys=True,
                        )
                    ),
                },
            ],
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        req = request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=30) as response:
                raw = response.read().decode("utf-8")
        except error.HTTPError as exc:
            raise CodexInterpreterError(f"Codex handoff interpreter HTTP {exc.code}") from exc
        except error.URLError as exc:
            raise CodexInterpreterError("Codex handoff interpreter request failed") from exc

        parsed = json.loads(raw)
        output_text = _extract_output_text(parsed)
        if not output_text:
            return None
        try:
            output = json.loads(output_text)
        except json.JSONDecodeError as exc:
            raise CodexInterpreterError("Codex handoff interpreter returned invalid JSON") from exc
        if not isinstance(output, dict):
            return None
        return CodexInterpretation(
            action=_normalize_action(output.get("action")),
            next_slice=_clean_line(output.get("next_slice")),
            next_step_hint=_clean_line(output.get("next_step_hint")),
            follow_ups=_normalize_follow_ups(output.get("follow_ups")),
            reasoning=_clean_line(output.get("reasoning")),
        )


def _extract_output_text(payload: dict[str, Any]) -> str | None:
    if isinstance(payload.get("output_text"), str) and payload["output_text"].strip():
        return payload["output_text"].strip()
    output = payload.get("output")
    if not isinstance(output, list):
        return None
    text_parts: list[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                text_parts.append(text.strip())
    return "\n".join(text_parts).strip() or None


def _normalize_action(value: Any) -> str | None:
    text = _clean_line(value)
    if text is None:
        return None
    normalized = text.lower().replace(" ", "_")
    if normalized not in ALLOWED_ACTIONS:
        return None
    return normalized


def _normalize_follow_ups(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list):
        return ()
    normalized: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict):
            continue
        title = _clean_line(raw.get("title"))
        if not title or title in seen_titles:
            continue
        seen_titles.add(title)
        entry = {key: raw[key] for key in raw if raw[key] not in (None, "", [], {})}
        entry["title"] = title
        normalized.append(entry)
    return tuple(normalized)


def _clean_line(value: Any) -> str | None:
    text = " ".join(str(value or "").split())
    return text or None
