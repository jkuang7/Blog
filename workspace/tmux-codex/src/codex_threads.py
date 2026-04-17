"""Helpers for listing and archiving Codex threads via app-server."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


RUNNER_THREAD_PREVIEW_MARKERS = (
    "Use this command to execute exactly one medium bounded infinite-runner work slice.",
    "Use this command to govern infinite-runner state after one execute slice finishes.",
    "RUNNER_DISPATCH mode=run_execute",
    "RUNNER_DISPATCH mode=run_govern",
    "/run_execute",
    "/run_govern",
    "/prompts:run_execute",
    "/prompts:run_govern",
)


def _call_app_server(method: str, params: dict[str, Any]) -> dict[str, Any]:
    """Make one app-server request over stdio."""
    process = subprocess.Popen(
        ["codex", "app-server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    try:
        assert process.stdin is not None
        assert process.stdout is not None
        request_id = 2
        handshake = (
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "clientInfo": {"name": "tmux-codex", "version": "0.0.0"},
                    "capabilities": {},
                },
            },
            {
                "jsonrpc": "2.0",
                "method": "initialized",
                "params": {},
            },
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            },
        )
        for message in handshake:
            process.stdin.write(json.dumps(message) + "\n")
            process.stdin.flush()

        for raw_line in process.stdout:
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if payload.get("id") != request_id:
                continue
            if "error" in payload:
                raise RuntimeError(f"app-server {method} failed: {payload['error']}")
            result = payload.get("result")
            if not isinstance(result, dict):
                return {}
            return result

        stderr = ""
        if process.stderr is not None:
            stderr = process.stderr.read().strip()
        raise RuntimeError(f"app-server {method} returned no response{f': {stderr}' if stderr else ''}")
    finally:
        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()


def list_threads_for_cwd(*, cwd: Path, archived: bool = False, limit: int = 200) -> list[dict[str, Any]]:
    """List Codex threads for one cwd, following pagination."""
    cursor: str | None = None
    threads: list[dict[str, Any]] = []
    while True:
        params: dict[str, Any] = {
            "cwd": str(cwd),
            "archived": archived,
            "limit": limit,
        }
        if cursor:
            params["cursor"] = cursor
        result = _call_app_server("thread/list", params)
        page = result.get("data")
        if isinstance(page, list):
            threads.extend(item for item in page if isinstance(item, dict))
        cursor = result.get("nextCursor") if isinstance(result.get("nextCursor"), str) else None
        if not cursor:
            break
    return threads


def is_runner_thread(thread: dict[str, Any]) -> bool:
    """Heuristic for runner-generated prompt threads."""
    preview = str(thread.get("preview") or "")
    name = str(thread.get("name") or "")
    haystack = f"{preview}\n{name}"
    return any(marker in haystack for marker in RUNNER_THREAD_PREVIEW_MARKERS)


def archive_thread(thread_id: str) -> None:
    """Archive one thread by id."""
    _call_app_server("thread/archive", {"threadId": thread_id})


def archive_runner_threads_for_cwd(*, cwd: Path, keep: int = 0) -> dict[str, Any]:
    """Archive runner-generated threads for a cwd, keeping the newest N."""
    threads = [thread for thread in list_threads_for_cwd(cwd=cwd, archived=False) if is_runner_thread(thread)]
    threads.sort(
        key=lambda thread: (
            int(thread.get("updatedAt") or 0),
            int(thread.get("createdAt") or 0),
        ),
        reverse=True,
    )
    kept = threads[: max(0, keep)]
    archived_threads = threads[max(0, keep) :]
    archived_ids: list[str] = []
    failed_ids: list[str] = []
    for thread in archived_threads:
        thread_id = str(thread.get("id") or "").strip()
        if not thread_id:
            continue
        try:
            archive_thread(thread_id)
            archived_ids.append(thread_id)
        except Exception:
            failed_ids.append(thread_id)
    return {
        "cwd": str(cwd),
        "matched": len(threads),
        "kept": len(kept),
        "archived": len(archived_ids),
        "archived_ids": archived_ids,
        "failed": len(failed_ids),
        "failed_ids": failed_ids,
    }
