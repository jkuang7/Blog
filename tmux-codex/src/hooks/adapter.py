"""Lifecycle hook adapter for runner events."""

from __future__ import annotations

from typing import Any

from .types import HookEvent, HookHandler


class HookAdapter:
    """Dispatches runner lifecycle events to one or more handlers."""

    def __init__(self, handlers: list[HookHandler] | None = None):
        self.handlers = handlers or []

    def emit(
        self,
        name: str,
        ts: str,
        project: str,
        runner_id: str,
        iteration: int,
        payload: dict[str, Any] | None = None,
    ) -> HookEvent:
        event = HookEvent(
            name=name,
            ts=ts,
            project=project,
            runner_id=runner_id,
            iteration=iteration,
            payload=payload or {},
        )

        method_name = {
            "on_start": "on_start",
            "on_step": "on_step",
            "on_tool_call": "on_tool_call",
            "on_finish": "on_finish",
            "on_finalize": "on_finalize",
            "on_error": "on_error",
        }.get(name)

        if method_name is None:
            return event

        for handler in self.handlers:
            callback = getattr(handler, method_name, None)
            if callback is None:
                continue
            try:
                callback(event)
            except Exception:
                # Hook failures are non-fatal by design.
                continue
        return event
