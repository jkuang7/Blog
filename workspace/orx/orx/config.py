"""Runtime path conventions for ORX."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "orx"
ENV_HOME = "ORX_HOME"
PROJECTS_DIR_NAME = "projects"


@dataclass(frozen=True)
class RuntimePaths:
    home: Path
    db_path: Path
    log_dir: Path
    run_dir: Path

    def ensure(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.run_dir.mkdir(parents=True, exist_ok=True)


def resolve_runtime_paths(home: str | os.PathLike[str] | None = None) -> RuntimePaths:
    if home is None:
        env_home = os.environ.get(ENV_HOME)
        root = Path(env_home).expanduser() if env_home else Path.home() / ".orx"
    else:
        root = Path(home).expanduser()

    root = root.resolve()
    return RuntimePaths(
        home=root,
        db_path=root / "orx.sqlite3",
        log_dir=root / "logs",
        run_dir=root / "run",
    )


def resolve_project_runtime_paths(
    project_key: str,
    *,
    home: str | os.PathLike[str] | None = None,
) -> RuntimePaths:
    normalized = normalize_project_key(project_key)
    root = resolve_runtime_paths(home).home / PROJECTS_DIR_NAME / normalized
    return RuntimePaths(
        home=root,
        db_path=root / "orx.sqlite3",
        log_dir=root / "logs",
        run_dir=root / "run",
    )


def normalize_project_key(value: str) -> str:
    normalized = value.strip().lower().replace(" ", "-").replace("_", "-")
    allowed = "".join(char if char.isalnum() or char == "-" else "-" for char in normalized)
    collapsed = "-".join(segment for segment in allowed.split("-") if segment)
    if not collapsed:
        raise ValueError("project_key cannot be empty.")
    return collapsed
