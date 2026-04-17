"""Bootstrap plus background daemon tasks for ORX."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass

from .dispatch import GlobalDispatchService
from .proposal_materialization import ProposalMaterializationBatch, ProposalMaterializationService
from .config import RuntimePaths
from .runtime_state import DaemonStateService
from .storage import Storage


@dataclass(frozen=True)
class DaemonSnapshot:
    home: str
    db_path: str
    schema_version: int
    tick: str
    proposal_materialization: dict[str, object]
    drained_projects: list[dict[str, object]]
    drifted_projects: list[dict[str, object]]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class OrxDaemon:
    def __init__(
        self,
        *,
        paths: RuntimePaths,
        storage: Storage,
        materializer: ProposalMaterializationService | None = None,
        dispatch: GlobalDispatchService | None = None,
        runtime_state: DaemonStateService | None = None,
    ) -> None:
        self.paths = paths
        self.storage = storage
        self.materializer = materializer or ProposalMaterializationService(storage)
        self.dispatch = dispatch or GlobalDispatchService(storage=storage)
        self.runtime_state = runtime_state or DaemonStateService(storage)

    def run(self, *, once: bool, interval_seconds: float) -> DaemonSnapshot:
        snapshot = self.run_once()
        if once:
            return snapshot

        while True:
            time.sleep(interval_seconds)
            snapshot = self.run_once()

    def run_once(self) -> DaemonSnapshot:
        result = self.storage.bootstrap()
        materialization = self.materializer.materialize_open_proposals()
        drained = self.dispatch.drain_projects()
        drifted = self.dispatch.list_drifted_projects()
        snapshot = DaemonSnapshot(
            home=str(self.paths.home),
            db_path=str(result.db_path),
            schema_version=result.schema_version,
            tick=_tick_label(materialization, drained, drifted),
            proposal_materialization=materialization.to_dict(),
            drained_projects=[
                {
                    "project_key": item.project_key,
                    "issue_key": item.issue_key,
                    "action": item.action,
                    "session_name": item.session_name,
                }
                for item in drained
            ],
            drifted_projects=[
                {
                    "project_key": item.project_key,
                    "blockers": list(item.blockers),
                    "warnings": list(item.warnings),
                }
                for item in drifted
            ],
        )
        self.runtime_state.record_last_tick(snapshot.to_dict())
        return snapshot


def _tick_label(
    materialization: ProposalMaterializationBatch,
    drained: list[object],
    drifted: list[object],
) -> str:
    if materialization.status == "disabled":
        return "degraded"
    if drifted:
        return "degraded"
    if drained:
        return "drained"
    if materialization.materialized > 0:
        return "materialized"
    if materialization.failed > 0:
        return "warning"
    return "idle"
