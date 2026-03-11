import { canonicalizeActions, type SemanticAction, type SemanticActionInput } from "../domain/actions.js";
import type { PlannerContext } from "../domain/contracts.js";

import { planBalance } from "./balance.js";
import { planOnFocus } from "./onFocus.js";
import { planOnWindow } from "./onWindow.js";
import { planResetWorkspace, planSwitchWorkspace } from "./switchReset.js";

export function planActions(context: PlannerContext): SemanticAction[] {
  let actions: SemanticActionInput[];

  switch (context.callback.kind) {
    case "on_window":
      actions = planOnWindow(context);
      break;
    case "on_focus":
      actions = planOnFocus(context);
      break;
    case "balance":
      actions = planBalance(context);
      break;
    case "switch_ws":
      actions = planSwitchWorkspace(context);
      break;
    case "reset_ws":
      actions = planResetWorkspace(context);
      break;
    default:
      actions = [
        {
          order: 0,
          type: "NOOP",
          target: context.callback.kind,
          reason: "unimplemented-callback",
          guard_result: "skipped",
          details: {}
        }
      ];
      break;
  }

  return canonicalizeActions(actions);
}
