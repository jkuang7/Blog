import type { SemanticActionInput } from "../domain/actions.js";
import type { PlannerContext } from "../domain/contracts.js";

export function planBalance(context: PlannerContext): SemanticActionInput[] {
  if (!context.guards.isHomeWorkspace) {
    return [
      {
        order: 0,
        type: "NOOP",
        target: "balance",
        reason: "non-home-workspace",
        guard_result: "skipped",
        details: {}
      }
    ];
  }

  return [
    {
      order: 10,
      type: "REBUILD",
      target: "workspace:w1",
      reason: "manual-rebalance",
      details: {
        force: true,
        browser: context.workspaceState.browser,
        upnoteTiled: context.workspaceState.upnoteTiled
      }
    }
  ];
}
