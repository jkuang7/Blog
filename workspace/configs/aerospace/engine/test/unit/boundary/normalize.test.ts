import { describe, expect, it } from "vitest";

import {
  isPopupTitle,
  normalizeCallbackInput,
  normalizeWorkspaceId,
  parseWindowSnapshot,
  parseWorkspaceStateFile,
  parseWorkspaceStateCompat,
  parseWorkspaceStateV2File,
  toCanonicalPlannerContext
} from "../../../src/boundary/normalize.js";

describe("boundary normalization", () => {
  it("normalizes workspace ids", () => {
    expect(normalizeWorkspaceId("w1")).toBe("w1");
    expect(normalizeWorkspaceId("1")).toBe("w1");
  });

  it("parses workspace state file content", () => {
    const state = parseWorkspaceStateFile(
      "w1",
      [
        "BROWSER=zen",
        "UPNOTE_TILED=true",
        "TILED_ORDER=11410,441,80,11670",
        "ACTIVE_UTILITY_BUNDLE=com.openai.codex",
        "ACTIVE_UTILITY_WID=80",
        ""
      ].join("\n")
    );

    expect(state.browser).toBe("zen");
    expect(state.upnoteTiled).toBe(true);
    expect(state.tiledOrder).toEqual([11410, 441, 80, 11670]);
    expect(state.activeUtilityBundle).toBe("com.openai.codex");
    expect(state.activeUtilityWindowId).toBe(80);
  });

  it("parses typed v2 workspace state content", () => {
    const stateV2 = parseWorkspaceStateV2File(
      JSON.stringify({
        version: 2,
        workspace: "w1",
        browser: "safari",
        upnoteTiled: false,
        tiledOrder: [441, 80, 11446],
        activeUtilityBundle: "com.cmuxterm.app",
        activeUtilityWindowId: 80,
        updatedAtMs: 1740744000000
      })
    );

    expect(stateV2.version).toBe(2);
    expect(stateV2.browser).toBe("safari");
    expect(stateV2.upnoteTiled).toBe(false);
    expect(stateV2.activeUtilityBundle).toBe("com.cmuxterm.app");
    expect(stateV2.activeUtilityWindowId).toBe(80);
  });

  it("prefers v2 state during compat parse", () => {
    const state = parseWorkspaceStateCompat({
      workspace: "w1",
      legacyContent: "BROWSER=zen\nUPNOTE_TILED=true\nTILED_ORDER=1,2,3\n",
      v2Content: JSON.stringify({
        version: 2,
        workspace: "w1",
        browser: "safari",
        upnoteTiled: false,
        tiledOrder: [9, 8, 7],
        activeUtilityBundle: "com.tdesktop.Telegram",
        activeUtilityWindowId: 9
      })
    });

    expect(state.browser).toBe("safari");
    expect(state.upnoteTiled).toBe(false);
    expect(state.tiledOrder).toEqual([9, 8, 7]);
    expect(state.activeUtilityBundle).toBe("com.tdesktop.Telegram");
    expect(state.activeUtilityWindowId).toBe(9);
  });

  it("parses and sorts window snapshots", () => {
    const rows = parseWindowSnapshot([
      "11670|w1|app.zen-browser.zen|h_tiles|AeroSpace Refactor Plan Review",
      "441|w1|com.microsoft.VSCode|h_tiles|AGENTS.md — Dev",
      "80|w1|com.openai.codex|h_tiles|Codex"
    ].join("\n"));

    expect(rows.map((row) => row.windowId)).toEqual([80, 441, 11670]);
  });

  it("classifies popup titles", () => {
    expect(isPopupTitle("Sign in required")).toBe(true);
    expect(isPopupTitle("Rename Tab")).toBe(true);
    expect(isPopupTitle("Start Page")).toBe(false);
  });

  it("produces canonical planner context", () => {
    const callback = normalizeCallbackInput({
      kind: "on_window",
      workspace: "1",
      bundleId: "com.apple.Safari",
      argv: ["com.apple.Safari"],
      timestampMs: 1730000000000
    });

    const context = toCanonicalPlannerContext({
      callback,
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
      windows: parseWindowSnapshot(
        [
          "441|w1|com.microsoft.VSCode|h_tiles|AGENTS.md — Dev",
          "80|w1|com.openai.codex|h_tiles|Codex",
          "11446|w1|com.apple.Safari|floating|Start Page"
        ].join("\n")
      )
    });

    expect(context.callback.workspace).toBe("w1");
    expect(context.guards.isHomeWorkspace).toBe(true);
    expect(context.guards.isPopupIntent).toBe(false);
    expect(context.windows.map((w) => w.windowId)).toEqual([80, 441, 11446]);
  });
});
