import type { SemanticActionInput } from "../domain/actions.js";
import type { PlannerContext } from "../domain/contracts.js";

export function planSwitchWorkspace(context: PlannerContext): SemanticActionInput[] {
  if (!context.guards.isHomeWorkspace) {
    return [
      {
        order: 0,
        type: "NOOP",
        target: "switch_ws",
        reason: "unsupported-workspace",
        guard_result: "skipped",
        details: { workspace: context.callback.workspace }
      }
    ];
  }

  return [
    {
      order: 10,
      type: "SET_CHURN",
      target: "churn_until",
      reason: "workspace-switch-guard",
      details: {}
    },
    {
      order: 20,
      type: "REBUILD",
      target: "workspace:w1",
      reason: "switch-ws-rebuild",
      details: {
        browser: context.workspaceState.browser,
        upnoteTiled: context.workspaceState.upnoteTiled
      }
    }
  ];
}

export function planResetWorkspace(_context: PlannerContext): SemanticActionInput[] {
  return [
    {
      order: 10,
      type: "STATE_MUTATION",
      target: "workspace_state",
      reason: "reset-defaults",
      details: {
        browser: "zen",
        upnoteTiled: true
      }
    },
    {
      order: 20,
      type: "WRITE_STATE",
      target: "w1.state",
      reason: "persist-reset-defaults",
      details: {
        browser: "zen",
        upnoteTiled: true
      }
    },
    {
      order: 30,
      type: "REBUILD",
      target: "workspace:w1",
      reason: "reset-rebuild",
      details: {
        force: true
      }
    }
  ];
}
