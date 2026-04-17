"""Minimal repo-local .env loading for ORX."""

from __future__ import annotations

import os
from pathlib import Path

ENV_DISABLE = "ORX_ENV_DISABLE"
ENV_FILE = "ORX_ENV_FILE"
DEFAULT_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def load_repo_env() -> dict[str, str]:
    if os.environ.get(ENV_DISABLE, "").strip():
        return {}

    env_path = _resolve_env_path()
    if not env_path.exists():
        return {}

    loaded: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        normalized = _normalize_value(value.strip())
        if key not in os.environ:
            os.environ[key] = normalized
        loaded[key] = os.environ[key]
    return loaded


def _resolve_env_path() -> Path:
    override = os.environ.get(ENV_FILE)
    if override and override.strip():
        return Path(override).expanduser().resolve()
    return DEFAULT_ENV_PATH


def _normalize_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
