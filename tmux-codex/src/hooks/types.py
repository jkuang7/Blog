"""Shared hook event types for runner lifecycle dispatch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class HookEvent:
    """Lifecycle event payload delivered to hook handlers."""

    name: str
    ts: str
    project: str
    runner_id: str
    iteration: int
    payload: dict[str, Any]


class HookHandler(Protocol):
    """Protocol for local or external hook handlers."""

    def on_start(self, event: HookEvent) -> None: ...
    def on_step(self, event: HookEvent) -> None: ...
    def on_tool_call(self, event: HookEvent) -> None: ...
    def on_finish(self, event: HookEvent) -> None: ...
    def on_finalize(self, event: HookEvent) -> None: ...
    def on_error(self, event: HookEvent) -> None: ...
