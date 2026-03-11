import type { SemanticActionInput } from "../domain/actions.js";
import type { PlannerContext } from "../domain/contracts.js";

import { hasBundle } from "./helpers.js";

export function planOnFocus(context: PlannerContext): SemanticActionInput[] {
  if (!context.guards.isHomeWorkspace) {
    return [
      {
        order: 0,
        type: "NOOP",
        target: "on_focus",
        reason: "non-home-workspace",
        guard_result: "skipped",
        details: { workspace: context.callback.workspace }
      }
    ];
  }

  if (context.guards.isPopupIntent) {
    return [
      {
        order: 0,
        type: "NOOP",
        target: "on_focus",
        reason: "transient-focus",
        guard_result: "skipped",
        details: {}
      }
    ];
  }

  const hasZen = hasBundle(context, "app.zen-browser.zen");
  const hasSafari = hasBundle(context, "com.apple.Safari");
  const hasUpNote = hasBundle(context, "com.getupnote.desktop");

  const actions: SemanticActionInput[] = [];

  if (context.workspaceState.browser === "safari" && !hasSafari && hasZen) {
    actions.push({
      order: 10,
      type: "STATE_MUTATION",
      target: "workspace_state",
      reason: "active-browser-closed-promote-zen",
      details: { browser: "zen" }
    });
  } else if (context.workspaceState.browser === "zen" && !hasZen && hasSafari) {
    actions.push({
      order: 10,
      type: "STATE_MUTATION",
      target: "workspace_state",
      reason: "active-browser-closed-promote-safari",
      details: { browser: "safari" }
    });
  }

  if (context.workspaceState.upnoteTiled && !hasUpNote) {
    actions.push({
      order: 15,
      type: "STATE_MUTATION",
      target: "workspace_state",
      reason: "upnote-closed-demote",
      details: { upnoteTiled: false }
    });
  }

  if (actions.length === 0) {
    return [
      {
        order: 0,
        type: "NOOP",
        target: "on_focus",
        reason: "no-state-change-required",
        guard_result: "skipped",
        details: {}
      }
    ];
  }

  actions.push({
    order: 20,
    type: "WRITE_STATE",
    target: "w1.state",
    reason: "persist-state-after-focus-transition",
    details: {}
  });

  actions.push({
    order: 30,
    type: "REBUILD",
    target: "workspace:w1",
    reason: "focus-transition-rebuild",
    details: {}
  });

  return actions;
}
