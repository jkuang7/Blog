"""Local file-backed lifecycle hooks for deterministic auditing."""

from __future__ import annotations

from pathlib import Path

from ..runner_state import append_ndjson
from .types import HookEvent


class LocalHooks:
    """Persist hook events for deterministic auditing."""

    def __init__(self, memory_dir: Path, hooks_log: Path):
        self.memory_dir = memory_dir
        self.hooks_log = hooks_log

    def _record(self, event: HookEvent) -> None:
        append_ndjson(
            self.hooks_log,
            {
                "ts": event.ts,
                "event": event.name,
                "project": event.project,
                "runner_id": event.runner_id,
                "iteration": event.iteration,
                "payload": event.payload,
            },
        )

    def on_start(self, event: HookEvent) -> None:
        self._record(event)

    def on_step(self, event: HookEvent) -> None:
        self._record(event)

    def on_tool_call(self, event: HookEvent) -> None:
        self._record(event)

    def on_finish(self, event: HookEvent) -> None:
        self._record(event)

    def on_finalize(self, event: HookEvent) -> None:
        self._record(event)

    def on_error(self, event: HookEvent) -> None:
        self._record(event)
