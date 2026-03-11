import { describe, expect, it } from "vitest";

import { planActions } from "../../../src/planner/planner.js";
import { toCanonicalPlannerContext } from "../../../src/boundary/normalize.js";

describe("planner", () => {
  it("plans safari on_window transition as state mutation + delegate + rebuild", () => {
    const context = toCanonicalPlannerContext({
      callback: {
        kind: "on_window",
        workspace: "w1",
        bundleId: "com.apple.Safari",
        argv: ["com.apple.Safari"],
        timestampMs: 1730000000000
      },
      workspaceState: {
        workspace: "w1",
        browser: "zen",
        upnoteTiled: true,
        tiledOrder: [11410, 441, 80, 11670]
      },
      focusedWindow: {
        windowId: 11446,
        workspace: "w1",
        bundleId: "com.apple.Safari",
        layout: "floating",
        title: "Start Page"
      },
      windows: [
        {
          windowId: 441,
          workspace: "w1",
          bundleId: "com.microsoft.VSCode",
          layout: "h_tiles",
          title: "AGENTS.md"
        },
        {
          windowId: 80,
          workspace: "w1",
          bundleId: "com.openai.codex",
          layout: "h_tiles",
          title: "Codex"
        },
        {
          windowId: 11410,
          workspace: "w1",
          bundleId: "com.getupnote.desktop",
          layout: "h_tiles",
          title: "UpNote"
        },
        {
          windowId: 11446,
          workspace: "w1",
          bundleId: "com.apple.Safari",
          layout: "floating",
          title: "Start Page"
        }
      ]
    });

    const actions = planActions(context);

    expect(actions.map((action) => action.type)).toEqual([
      "STATE_MUTATION",
      "WRITE_STATE",
      "DELEGATE",
      "REBUILD"
    ]);
    expect(actions[0]?.details.browser).toBe("safari");
  });

  it("plans on_focus browser promotion when safari missing and zen present", () => {
    const context = toCanonicalPlannerContext({
      callback: {
        kind: "on_focus",
        workspace: "w1",
        argv: [],
        timestampMs: 1730000000000
      },
      workspaceState: {
        workspace: "w1",
        browser: "safari",
        upnoteTiled: true,
        tiledOrder: [11410, 441, 80, 11446]
      },
      focusedWindow: {
        windowId: 441,
        workspace: "w1",
        bundleId: "com.microsoft.VSCode",
        layout: "h_tiles",
        title: "AGENTS.md"
      },
      windows: [
        {
          windowId: 441,
          workspace: "w1",
          bundleId: "com.microsoft.VSCode",
          layout: "h_tiles",
          title: "AGENTS.md"
        },
        {
          windowId: 80,
          workspace: "w1",
          bundleId: "com.openai.codex",
          layout: "h_tiles",
          title: "Codex"
        },
        {
          windowId: 11410,
          workspace: "w1",
          bundleId: "com.getupnote.desktop",
          layout: "h_tiles",
          title: "UpNote"
        },
        {
          windowId: 9817,
          workspace: "w1",
          bundleId: "app.zen-browser.zen",
          layout: "h_tiles",
          title: "Zen"
        }
      ]
    });

    const actions = planActions(context);

    expect(actions.map((action) => action.type)).toEqual([
      "STATE_MUTATION",
      "WRITE_STATE",
      "REBUILD"
    ]);
    expect(actions[0]?.reason).toBe("active-browser-closed-promote-zen");
  });
});
