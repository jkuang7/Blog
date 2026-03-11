"""Optional OpenAI Agents SDK hook bridge.

This module stays inert unless the `openai-agents` package is installed.
"""

from __future__ import annotations

from .types import HookEvent


class AgentsSdkBridge:
    """Thin compatibility layer for future SDK wiring."""

    def on_start(self, event: HookEvent) -> None:
        _ = event

    def on_step(self, event: HookEvent) -> None:
        _ = event

    def on_tool_call(self, event: HookEvent) -> None:
        _ = event

    def on_finish(self, event: HookEvent) -> None:
        _ = event

    def on_finalize(self, event: HookEvent) -> None:
        _ = event

    def on_error(self, event: HookEvent) -> None:
        _ = event


def load_agents_bridge() -> AgentsSdkBridge | None:
    """Return SDK bridge when dependency is available, else None."""
    try:
        __import__("openai_agents")
    except Exception:
        return None
    return AgentsSdkBridge()
