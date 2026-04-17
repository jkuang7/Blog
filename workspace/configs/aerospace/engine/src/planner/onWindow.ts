import type { SemanticActionInput } from "../domain/actions.js";
import type { PlannerContext } from "../domain/contracts.js";

import { activeLayoutProfile, isManagedBundle } from "./helpers.js";

export function planOnWindow(context: PlannerContext): SemanticActionInput[] {
  const bundle = context.callback.bundleId;
  const focusedWindowId = context.focusedWindow?.windowId ?? null;

  if (!context.guards.isHomeWorkspace) {
    return [
      {
        order: 0,
        type: "NOOP",
        target: "on_window",
        reason: "non-home-workspace",
        guard_result: "skipped",
        details: { workspace: context.callback.workspace }
      }
    ];
  }

  if (!isManagedBundle(bundle) || !context.guards.isManagedBundle) {
    return [
      {
        order: 0,
        type: "NOOP",
        target: "on_window",
        reason: "non-managed-bundle",
        guard_result: "skipped",
        details: { bundleId: bundle ?? "" }
      }
    ];
  }

  if (context.guards.isPopupIntent) {
    return [
      {
        order: 0,
        type: "NOOP",
        target: "on_window",
        reason: "popup-intent",
        guard_result: "skipped",
        details: { bundleId: bundle ?? "" }
      }
    ];
  }

  const nextBrowser =
    bundle === "com.apple.Safari"
      ? "safari"
      : bundle === "app.zen-browser.zen"
      ? "zen"
      : context.workspaceState.browser;

  const upnoteTiled =
    bundle === "com.getupnote.desktop" ? true : context.workspaceState.upnoteTiled;
  const nextUtilityBundle =
    bundle === "com.openai.codex" || bundle === "com.cmuxterm.app" || bundle === "com.tdesktop.Telegram"
      ? bundle
      : context.workspaceState.activeUtilityBundle;
  const nextUtilityWindowId =
    bundle === "com.openai.codex" || bundle === "com.cmuxterm.app" || bundle === "com.tdesktop.Telegram"
      ? focusedWindowId
      : context.workspaceState.activeUtilityWindowId;
  const stateDetails: Record<string, string | boolean | number | null> = {
    browser: nextBrowser,
    upnoteTiled
  };

  if (bundle === "com.openai.codex" || bundle === "com.cmuxterm.app" || bundle === "com.tdesktop.Telegram") {
    stateDetails.activeUtilityBundle = nextUtilityBundle;
    stateDetails.activeUtilityWindowId = nextUtilityWindowId;
  }

  const actions: SemanticActionInput[] = [
    {
      order: 10,
      type: "STATE_MUTATION",
      target: "workspace_state",
      reason: "managed-window-open",
      details: stateDetails
    },
    {
      order: 20,
      type: "WRITE_STATE",
      target: "w1.state",
      reason: "persist-state-before-balance",
      details: stateDetails
    },
    {
      order: 30,
      type: "DELEGATE",
      target: "balance",
      reason: "standardized-retile-path",
      details: {
        mode: "ctrl-e-path"
      }
    },
    {
      order: 40,
      type: "REBUILD",
      target: "workspace:w1",
      reason: "delegate-balance-rebuild",
      details: {
        activeBrowser: nextBrowser,
        layoutProfile: upnoteTiled ? "4-col" : "3-col",
        inactiveBrowserHidden:
          nextBrowser === "safari" ? "app.zen-browser.zen" : "com.apple.Safari"
      }
    }
  ];

  if (bundle === "com.openai.codex" || bundle === "com.microsoft.VSCode") {
    actions[0] = {
      order: 10,
      type: "STATE_MUTATION",
      target: "workspace_state",
      reason: "core-app-open-no-browser-switch",
      details: {
        browser: context.workspaceState.browser,
        upnoteTiled: context.workspaceState.upnoteTiled
      }
    };
    actions[3].details = {
      ...actions[3].details,
      activeBrowser: context.workspaceState.browser,
      layoutProfile: activeLayoutProfile(context)
    };
  }

  return actions;
}
