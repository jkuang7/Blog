"""CLI entrypoint for the ORX Phase 1 scaffold."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .api import OrxApiService, run_api_server
from .config import resolve_runtime_paths
from .continuity import ContinuityService
from .daemon import OrxDaemon
from .doctor import HostDoctorService
from .env import load_repo_env
from .operator import OperatorService
from .proposals import ProposalService
from .repository import OrxRepository
from .storage import Storage


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="orx")
    parser.add_argument("--home", help="Override ORX runtime directory.")
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable output.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Initialize runtime paths and storage.")
    init_parser.set_defaults(handler=_handle_init)

    doctor_parser = subparsers.add_parser("doctor", help="Report host readiness for live ORX operation.")
    doctor_parser.set_defaults(handler=_handle_doctor)

    status_parser = subparsers.add_parser("status", help="Show runtime and storage status.")
    status_parser.set_defaults(handler=_handle_status)

    daemon_parser = subparsers.add_parser("daemon", help="Daemon commands.")
    daemon_subparsers = daemon_parser.add_subparsers(dest="daemon_command", required=True)

    daemon_run_parser = daemon_subparsers.add_parser("run", help="Start the ORX daemon.")
    daemon_run_parser.add_argument(
        "--once",
        action="store_true",
        help="Bootstrap once and exit instead of staying resident.",
    )
    daemon_run_parser.add_argument(
        "--interval-seconds",
        type=float,
        default=5.0,
        help="Polling interval used for the no-op daemon loop.",
    )
    daemon_run_parser.set_defaults(handler=_handle_daemon_run)

    api_parser = subparsers.add_parser("api", help="Local API commands.")
    api_subparsers = api_parser.add_subparsers(dest="api_command", required=True)

    api_serve_parser = api_subparsers.add_parser("serve", help="Serve the local ORX API.")
    api_serve_parser.add_argument("--host", default="127.0.0.1")
    api_serve_parser.add_argument("--port", type=int, default=9467)
    api_serve_parser.add_argument(
        "--max-requests",
        type=int,
        help="Handle a fixed number of requests, then exit.",
    )
    api_serve_parser.set_defaults(handler=_handle_api_serve)

    dispatch_parser = subparsers.add_parser("dispatch", help="Global dispatch and project registry.")
    dispatch_subparsers = dispatch_parser.add_subparsers(dest="dispatch_command", required=True)

    dispatch_register_parser = dispatch_subparsers.add_parser(
        "register-project",
        help="Register or update a project in the global ORX registry.",
    )
    dispatch_register_parser.add_argument("--project-key", required=True)
    dispatch_register_parser.add_argument("--display-name", required=True)
    dispatch_register_parser.add_argument("--repo-root", required=True)
    dispatch_register_parser.add_argument("--owning-bot", required=True)
    dispatch_register_parser.add_argument("--owner-chat-id", type=int)
    dispatch_register_parser.add_argument("--owner-thread-id", type=int)
    dispatch_register_parser.add_argument("--metadata-json")
    dispatch_register_parser.set_defaults(handler=_handle_dispatch_register_project)

    dispatch_deregister_parser = dispatch_subparsers.add_parser(
        "deregister-project",
        help="Remove a project from the global ORX registry.",
    )
    dispatch_deregister_parser.add_argument("--project-key", required=True)
    dispatch_deregister_parser.set_defaults(handler=_handle_dispatch_deregister_project)

    dispatch_run_parser = dispatch_subparsers.add_parser(
        "run",
        help="Select the next runnable ticket and hand it to the owning project runtime.",
    )
    dispatch_run_parser.add_argument("--ingress-bot", required=True)
    dispatch_run_parser.add_argument("--ingress-chat-id", type=int)
    dispatch_run_parser.add_argument("--ingress-thread-id", type=int)
    dispatch_run_parser.add_argument("--issue-key")
    dispatch_run_parser.add_argument("--project-key")
    dispatch_run_parser.set_defaults(handler=_handle_dispatch_run)

    dispatch_release_parser = dispatch_subparsers.add_parser(
        "release-lane",
        help="Apply an explicit HIL release decision to a reserved feature lane.",
    )
    dispatch_release_parser.add_argument("--project-key", required=True)
    dispatch_release_parser.add_argument(
        "--action",
        required=True,
        choices=(
            "keep_reserved",
            "merge_to_main_and_release",
            "cherry_pick_and_release",
            "discard_and_release",
        ),
    )
    dispatch_release_parser.add_argument("--note")
    dispatch_release_parser.set_defaults(handler=_handle_dispatch_release_lane)

    dispatch_recover_failed_start_parser = dispatch_subparsers.add_parser(
        "recover-failed-start",
        help="Repair a stranded failed managed-start state and clear any partial control-plane residue.",
    )
    dispatch_recover_failed_start_parser.add_argument("--project-key", required=True)
    dispatch_recover_failed_start_parser.set_defaults(handler=_handle_dispatch_recover_failed_start)

    dispatch_resume_reviewed_parser = dispatch_subparsers.add_parser(
        "resume-reviewed",
        help="Resume a lane parked in awaiting_orx_review after ORX or HIL resolves the blocker.",
    )
    dispatch_resume_reviewed_parser.add_argument("--project-key", required=True)
    dispatch_resume_reviewed_parser.add_argument("--next-slice")
    dispatch_resume_reviewed_parser.set_defaults(handler=_handle_dispatch_resume_reviewed)

    dispatch_dashboard_parser = dispatch_subparsers.add_parser(
        "dashboard",
        help="Show cross-project ORX runtime and session status.",
    )
    dispatch_dashboard_parser.set_defaults(handler=_handle_dispatch_dashboard)

    dispatch_context_parser = dispatch_subparsers.add_parser(
        "context",
        help="Build a restart-safe project context pack from durable ORX state.",
    )
    dispatch_context_parser.add_argument("--project-key", required=True)
    dispatch_context_parser.set_defaults(handler=_handle_dispatch_context)

    dispatch_drift_parser = dispatch_subparsers.add_parser(
        "drift",
        help="Report project/runtime drift before recovery or handoff.",
    )
    dispatch_drift_parser.add_argument("--project-key", required=True)
    dispatch_drift_parser.set_defaults(handler=_handle_dispatch_drift)

    operator_parser = subparsers.add_parser("operator", help="SSH/local operator commands.")
    operator_subparsers = operator_parser.add_subparsers(dest="operator_command", required=True)

    operator_runners_parser = operator_subparsers.add_parser("runners", help="List runners.")
    operator_runners_parser.set_defaults(handler=_handle_operator_runners)

    operator_daemon_parser = operator_subparsers.add_parser(
        "daemon",
        help="Inspect the last known daemon tick state.",
    )
    operator_daemon_parser.set_defaults(handler=_handle_operator_daemon)

    operator_validations_parser = operator_subparsers.add_parser(
        "validations",
        help="Inspect durable validation evidence.",
    )
    operator_validations_parser.add_argument("--issue-key")
    operator_validations_parser.add_argument("--runner-id")
    operator_validations_parser.add_argument("--limit", type=int, default=20)
    operator_validations_parser.set_defaults(handler=_handle_operator_validations)

    operator_record_validation_parser = operator_subparsers.add_parser(
        "record-validation",
        help="Record durable validation evidence.",
    )
    operator_record_validation_parser.add_argument("--issue-key", required=True)
    operator_record_validation_parser.add_argument("--runner-id", required=True)
    operator_record_validation_parser.add_argument("--surface", required=True)
    operator_record_validation_parser.add_argument("--tool", required=True)
    operator_record_validation_parser.add_argument("--result", required=True)
    operator_record_validation_parser.add_argument("--confidence", required=True)
    operator_record_validation_parser.add_argument("--summary", required=True)
    operator_record_validation_parser.add_argument("--details-json")
    operator_record_validation_parser.add_argument(
        "--blocker",
        action="append",
        dest="blockers",
        default=[],
    )
    operator_record_validation_parser.set_defaults(handler=_handle_operator_record_validation)

    operator_queue_parser = operator_subparsers.add_parser("queue", help="Inspect pending queue.")
    operator_queue_parser.add_argument("--issue-key")
    operator_queue_parser.add_argument("--runner-id")
    operator_queue_parser.set_defaults(handler=_handle_operator_queue)

    operator_proposals_parser = operator_subparsers.add_parser(
        "proposals",
        help="Inspect durable proposals.",
    )
    operator_proposals_parser.add_argument("--issue-key")
    operator_proposals_parser.add_argument(
        "--status",
        choices=("open", "materialized"),
        default="open",
    )
    operator_proposals_parser.set_defaults(handler=_handle_operator_proposals)

    operator_status_parser = operator_subparsers.add_parser("status", help="Inspect issue/runner state.")
    operator_status_parser.add_argument("--issue-key", required=True)
    operator_status_parser.add_argument("--runner-id", required=True)
    operator_status_parser.set_defaults(handler=_handle_operator_status)

    operator_attach_parser = operator_subparsers.add_parser(
        "attach-target",
        help="Show tmux attach target for a runner.",
    )
    operator_attach_parser.add_argument("--runner-id", required=True)
    operator_attach_parser.set_defaults(handler=_handle_operator_attach_target)

    operator_pane_parser = operator_subparsers.add_parser(
        "pane",
        help="Capture recent pane output for a runner.",
    )
    operator_pane_parser.add_argument("--runner-id", required=True)
    operator_pane_parser.add_argument("--lines", type=int, default=50)
    operator_pane_parser.set_defaults(handler=_handle_operator_pane)

    operator_recovery_parser = operator_subparsers.add_parser(
        "recovery",
        help="List stale recovery candidates.",
    )
    operator_recovery_parser.add_argument("--stale-after-seconds", type=int, default=300)
    operator_recovery_parser.set_defaults(handler=_handle_operator_recovery)

    operator_takeovers_parser = operator_subparsers.add_parser(
        "takeovers",
        help="List active operator takeovers.",
    )
    operator_takeovers_parser.set_defaults(handler=_handle_operator_takeovers)

    operator_takeover_parser = operator_subparsers.add_parser(
        "takeover",
        help="Begin explicit local takeover for an issue/runner.",
    )
    operator_takeover_parser.add_argument("--issue-key", required=True)
    operator_takeover_parser.add_argument("--runner-id", required=True)
    operator_takeover_parser.add_argument("--operator-id", required=True)
    operator_takeover_parser.add_argument("--reason", required=True)
    operator_takeover_parser.set_defaults(handler=_handle_operator_takeover)

    operator_return_parser = operator_subparsers.add_parser(
        "return-control",
        help="Return control after a takeover.",
    )
    operator_return_parser.add_argument("--issue-key", required=True)
    operator_return_parser.add_argument("--runner-id", required=True)
    operator_return_parser.add_argument("--operator-id", required=True)
    operator_return_parser.add_argument("--note")
    operator_return_parser.set_defaults(handler=_handle_operator_return_control)

    operator_control_parser = operator_subparsers.add_parser(
        "control",
        help="Queue takeover-aware local control mutation.",
    )
    operator_control_parser.add_argument("--kind", required=True)
    operator_control_parser.add_argument("--issue-key", required=True)
    operator_control_parser.add_argument("--runner-id", required=True)
    operator_control_parser.add_argument("--operator-id", required=True)
    operator_control_parser.add_argument("--payload-json")
    operator_control_parser.set_defaults(handler=_handle_operator_control)

    operator_materialize_parser = operator_subparsers.add_parser(
        "materialize-proposal",
        help="Materialize a durable proposal into a Linear leaf ticket.",
    )
    operator_materialize_parser.add_argument("--proposal-id", type=int, required=True)
    operator_materialize_parser.set_defaults(handler=_handle_operator_materialize_proposal)

    operator_issue_parser = operator_subparsers.add_parser(
        "issue",
        help="Apply Linear ticket CRUD operations.",
    )
    operator_issue_subparsers = operator_issue_parser.add_subparsers(
        dest="issue_command",
        required=True,
    )

    operator_issue_get_parser = operator_issue_subparsers.add_parser(
        "get",
        help="Fetch a Linear issue by identifier or id.",
    )
    operator_issue_get_parser.add_argument("--issue", required=True)
    operator_issue_get_parser.set_defaults(handler=_handle_operator_issue_get)

    operator_issue_create_parser = operator_issue_subparsers.add_parser(
        "create",
        help="Create a Linear issue.",
    )
    operator_issue_create_parser.add_argument("--team-id", required=True)
    operator_issue_create_parser.add_argument("--title", required=True)
    operator_issue_create_parser.add_argument("--description", default="")
    operator_issue_create_parser.add_argument("--parent-id")
    operator_issue_create_parser.add_argument("--project-id")
    operator_issue_create_parser.set_defaults(handler=_handle_operator_issue_create)

    operator_issue_update_parser = operator_issue_subparsers.add_parser(
        "update",
        help="Update a Linear issue.",
    )
    operator_issue_update_parser.add_argument("--issue", required=True)
    operator_issue_update_parser.add_argument("--title")
    operator_issue_update_parser.add_argument("--description")
    operator_issue_update_parser.add_argument("--state-id")
    operator_issue_update_parser.set_defaults(handler=_handle_operator_issue_update)

    operator_issue_archive_parser = operator_issue_subparsers.add_parser(
        "archive",
        help="Archive a Linear issue.",
    )
    operator_issue_archive_parser.add_argument("--issue", required=True)
    operator_issue_archive_parser.add_argument(
        "--trash",
        action="store_true",
        help="Move the archived issue to trash when supported by Linear.",
    )
    operator_issue_archive_parser.set_defaults(handler=_handle_operator_issue_archive)

    operator_issue_delete_parser = operator_issue_subparsers.add_parser(
        "delete",
        help="Delete a Linear issue.",
    )
    operator_issue_delete_parser.add_argument("--issue", required=True)
    operator_issue_delete_parser.set_defaults(handler=_handle_operator_issue_delete)

    return parser


def main(argv: list[str] | None = None) -> int:
    load_repo_env()
    parser = build_parser()
    args = parser.parse_args(argv)
    payload = args.handler(args)
    _print_payload(payload, json_output=args.json_output)
    return 0


def _handle_init(args: argparse.Namespace) -> dict[str, Any]:
    paths = resolve_runtime_paths(args.home)
    storage = Storage(paths)
    result = storage.bootstrap()
    return {
        "command": "init",
        "home": str(paths.home),
        "db_path": str(result.db_path),
        "schema_version": result.schema_version,
        "created": result.created,
    }


def _handle_doctor(args: argparse.Namespace) -> dict[str, Any]:
    paths = resolve_runtime_paths(args.home)
    storage = Storage(paths)
    return {
        "command": "doctor",
        **HostDoctorService(storage).payload(),
    }


def _handle_status(args: argparse.Namespace) -> dict[str, Any]:
    paths = resolve_runtime_paths(args.home)
    storage = Storage(paths)
    return {
        "command": "status",
        "home": str(paths.home),
        "db_path": str(paths.db_path),
        "db_exists": paths.db_path.exists(),
        "schema_version": storage.current_version(),
    }


def _handle_daemon_run(args: argparse.Namespace) -> dict[str, Any]:
    paths = resolve_runtime_paths(args.home)
    storage = Storage(paths)
    daemon = OrxDaemon(paths=paths, storage=storage)
    try:
        snapshot = daemon.run(once=args.once, interval_seconds=args.interval_seconds)
    except KeyboardInterrupt:
        return {
            "command": "daemon run",
            "home": str(paths.home),
            "db_path": str(paths.db_path),
            "stopped": "interrupt",
        }

    return {
        "command": "daemon run",
        **snapshot.to_dict(),
        "stopped": "once" if args.once else "running",
    }


def _handle_api_serve(args: argparse.Namespace) -> dict[str, Any]:
    paths = resolve_runtime_paths(args.home)
    storage = Storage(paths)
    storage.bootstrap()
    api = _build_api_service(args)
    try:
        host, port = run_api_server(
            api,
            host=args.host,
            port=args.port,
            max_requests=args.max_requests,
        )
    except KeyboardInterrupt:
        return {
            "command": "api serve",
            "host": args.host,
            "port": args.port,
            "stopped": "interrupt",
        }

    return {
        "command": "api serve",
        "host": host,
        "port": port,
        "stopped": "max-requests" if args.max_requests is not None else "serve-forever",
    }


def _build_api_service(args: argparse.Namespace) -> OrxApiService:
    paths = resolve_runtime_paths(args.home)
    storage = Storage(paths)
    storage.bootstrap()
    repository = OrxRepository(storage)
    continuity = ContinuityService(storage)
    proposals = ProposalService(storage, continuity=continuity)
    return OrxApiService(
        storage=storage,
        repository=repository,
        continuity=continuity,
        proposals=proposals,
    )


def _handle_dispatch_register_project(args: argparse.Namespace) -> dict[str, Any]:
    metadata = json.loads(args.metadata_json) if args.metadata_json else {}
    if not isinstance(metadata, dict):
        raise ValueError("--metadata-json must decode to a JSON object.")
    return {
        "command": "dispatch register-project",
        **_build_api_service(args).register_project_payload(
            {
                "project_key": args.project_key,
                "display_name": args.display_name,
                "repo_root": args.repo_root,
                "owning_bot": args.owning_bot,
                "owner_chat_id": args.owner_chat_id,
                "owner_thread_id": args.owner_thread_id,
                "metadata": metadata,
            }
        ),
    }


def _handle_dispatch_deregister_project(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "command": "dispatch deregister-project",
        **_build_api_service(args).deregister_project_payload(project_key=args.project_key),
    }


def _handle_dispatch_run(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "command": "dispatch run",
        **_build_api_service(args).dispatch_run_payload(
            {
                "ingress_bot": args.ingress_bot,
                "ingress_chat_id": args.ingress_chat_id,
                "ingress_thread_id": args.ingress_thread_id,
                "issue_key": args.issue_key,
                "project_key": args.project_key,
            }
        ),
    }


def _handle_dispatch_release_lane(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "command": "dispatch release-lane",
        **_build_api_service(args).release_feature_lane_payload(
            {
                "project_key": args.project_key,
                "action": args.action,
                "note": args.note,
            }
        ),
    }


def _handle_dispatch_recover_failed_start(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "command": "dispatch recover-failed-start",
        **_build_api_service(args).recover_failed_start_payload(
            {
                "project_key": args.project_key,
            }
        ),
    }


def _handle_dispatch_resume_reviewed(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "command": "dispatch resume-reviewed",
        **_build_api_service(args).resume_reviewed_lane_payload(
            {
                "project_key": args.project_key,
                "next_slice": args.next_slice,
            }
        ),
    }


def _handle_dispatch_dashboard(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "command": "dispatch dashboard",
        **_build_api_service(args).dashboard_payload(),
    }


def _handle_dispatch_context(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "command": "dispatch context",
        **_build_api_service(args).control_context_payload(project_key=args.project_key),
    }


def _handle_dispatch_drift(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "command": "dispatch drift",
        **_build_api_service(args).control_drift_payload(project_key=args.project_key),
    }


def _build_operator_service(args: argparse.Namespace) -> OperatorService:
    paths = resolve_runtime_paths(args.home)
    storage = Storage(paths)
    storage.bootstrap()
    repository = OrxRepository(storage)
    continuity = ContinuityService(storage)
    proposals = ProposalService(storage, continuity=continuity)
    return OperatorService(
        storage=storage,
        repository=repository,
        continuity=continuity,
        proposals=proposals,
    )


def _handle_operator_runners(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "command": "operator runners",
        **_build_operator_service(args).runners_payload(),
    }


def _handle_operator_daemon(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "command": "operator daemon",
        **_build_operator_service(args).daemon_payload(),
    }


def _handle_operator_validations(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "command": "operator validations",
        **_build_operator_service(args).validations_payload(
            issue_key=args.issue_key,
            runner_id=args.runner_id,
            limit=args.limit,
        ),
    }


def _handle_operator_record_validation(args: argparse.Namespace) -> dict[str, Any]:
    details = json.loads(args.details_json) if args.details_json else {}
    if not isinstance(details, dict):
        raise ValueError("--details-json must decode to a JSON object.")
    return {
        "command": "operator record-validation",
        **_build_operator_service(args).record_validation_payload(
            issue_key=args.issue_key,
            runner_id=args.runner_id,
            surface=args.surface,
            tool=args.tool,
            result=args.result,
            confidence=args.confidence,
            summary=args.summary,
            details=details,
            blockers=args.blockers,
        ),
    }


def _handle_operator_queue(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "command": "operator queue",
        **_build_operator_service(args).queue_payload(
            issue_key=args.issue_key,
            runner_id=args.runner_id,
        ),
    }


def _handle_operator_proposals(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "command": "operator proposals",
        **_build_operator_service(args).proposals_payload(
            issue_key=args.issue_key,
            status=args.status,
        ),
    }


def _handle_operator_status(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "command": "operator status",
        **_build_operator_service(args).status_payload(
            issue_key=args.issue_key,
            runner_id=args.runner_id,
        ),
    }


def _handle_operator_attach_target(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "command": "operator attach-target",
        **_build_operator_service(args).attach_target_payload(runner_id=args.runner_id),
    }


def _handle_operator_pane(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "command": "operator pane",
        **_build_operator_service(args).pane_payload(
            runner_id=args.runner_id,
            lines=args.lines,
        ),
    }


def _handle_operator_recovery(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "command": "operator recovery",
        **_build_operator_service(args).recovery_payload(
            stale_after_seconds=args.stale_after_seconds,
        ),
    }


def _handle_operator_takeovers(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "command": "operator takeovers",
        **_build_operator_service(args).takeovers_payload(),
    }


def _handle_operator_takeover(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "command": "operator takeover",
        **_build_operator_service(args).takeover_payload(
            issue_key=args.issue_key,
            runner_id=args.runner_id,
            operator_id=args.operator_id,
            rationale=args.reason,
        ),
    }


def _handle_operator_return_control(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "command": "operator return-control",
        **_build_operator_service(args).return_control_payload(
            issue_key=args.issue_key,
            runner_id=args.runner_id,
            operator_id=args.operator_id,
            note=args.note,
        ),
    }


def _handle_operator_control(args: argparse.Namespace) -> dict[str, Any]:
    payload = json.loads(args.payload_json) if args.payload_json else None
    if payload is not None and not isinstance(payload, dict):
        raise ValueError("--payload-json must decode to a JSON object.")
    return {
        "command": "operator control",
        **_build_operator_service(args).control_payload(
            operator_id=args.operator_id,
            command_kind=args.kind,
            issue_key=args.issue_key,
            runner_id=args.runner_id,
            payload=payload,
        ),
    }


def _handle_operator_materialize_proposal(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "command": "operator materialize-proposal",
        **_build_operator_service(args).materialize_proposal_payload(
            proposal_id=args.proposal_id,
        ),
    }


def _handle_operator_issue_get(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "command": "operator issue get",
        **_build_operator_service(args).linear_issue_get_payload(issue_ref=args.issue),
    }


def _handle_operator_issue_create(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "command": "operator issue create",
        **_build_operator_service(args).linear_issue_create_payload(
            team_id=args.team_id,
            title=args.title,
            description=args.description,
            parent_id=args.parent_id,
            project_id=args.project_id,
        ),
    }


def _handle_operator_issue_update(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "command": "operator issue update",
        **_build_operator_service(args).linear_issue_update_payload(
            issue_ref=args.issue,
            title=args.title,
            description=args.description,
            state_id=args.state_id,
        ),
    }


def _handle_operator_issue_archive(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "command": "operator issue archive",
        **_build_operator_service(args).linear_issue_archive_payload(
            issue_ref=args.issue,
            trash=args.trash,
        ),
    }


def _handle_operator_issue_delete(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "command": "operator issue delete",
        **_build_operator_service(args).linear_issue_delete_payload(issue_ref=args.issue),
    }


def _print_payload(payload: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        json.dump(payload, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return

    for key, value in payload.items():
        print(f"{key}: {value}")
