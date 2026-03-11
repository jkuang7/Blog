import { promises as fs } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";
import { z } from "zod";

import { toCanonicalPlannerContext } from "../../src/boundary/normalize.js";
import { compareSemanticActions, formatSemanticParityDiff } from "../../src/parity/semanticParity.js";
import { planActions } from "../../src/planner/planner.js";

const traceContextSchema = z.object({
  trace_id: z.string().min(1),
  planner_context: z.object({
    callback: z.object({
      kind: z.enum([
        "on_window",
        "on_focus",
        "balance",
        "switch_ws",
        "reset_ws",
        "move_to_focused",
        "video_mode"
      ]),
      workspace: z.literal("w1"),
      bundleId: z.string().min(1).optional(),
      argv: z.array(z.string()),
      timestampMs: z.number().int().nonnegative()
    }),
    workspaceState: z.object({
      workspace: z.literal("w1"),
      browser: z.enum(["zen", "safari", ""]),
      upnoteTiled: z.boolean(),
      tiledOrder: z.array(z.number().int().nonnegative())
    }),
    focusedWindow: z
      .object({
        windowId: z.number().int().nonnegative(),
        workspace: z.literal("w1"),
        bundleId: z.string().min(1),
        layout: z.enum(["floating", "h_tiles", "v_tiles", "stacked", "accordion"]),
        title: z.string()
      })
      .nullable(),
    windows: z.array(
      z.object({
        windowId: z.number().int().nonnegative(),
        workspace: z.literal("w1"),
        bundleId: z.string().min(1),
        layout: z.enum(["floating", "h_tiles", "v_tiles", "stacked", "accordion"]),
        title: z.string()
      })
    ),
    guards: z
      .object({
        isHomeWorkspace: z.boolean(),
        isManagedBundle: z.boolean(),
        isPopupIntent: z.boolean()
      })
      .optional()
  })
});

const expectedActionsSchema = z.object({
  trace_id: z.string().min(1),
  expected_semantic_actions: z.array(
    z.object({
      order: z.number().int().nonnegative(),
      type: z.enum([
        "NOOP",
        "STATE_MUTATION",
        "DELEGATE",
        "REBUILD",
        "SET_CHURN",
        "CAPTURE_SNAPSHOT",
        "WRITE_STATE",
        "MOVE_WINDOW"
      ]),
      target: z.string().min(1),
      reason: z.string().min(1),
      guard_result: z.string().min(1).optional(),
      details: z.record(z.string(), z.unknown()).optional()
    })
  )
});

interface TraceCase {
  traceId: string;
  plannerContext: z.infer<typeof traceContextSchema>["planner_context"];
  expectedActions: z.infer<typeof expectedActionsSchema>["expected_semantic_actions"];
}

async function loadTraceCases(): Promise<TraceCase[]> {
  const tracesDir = path.resolve(process.cwd(), "test", "fixtures", "trace_samples");
  const fileNames = await fs.readdir(tracesDir);
  const expectedActionFiles = fileNames
    .filter((fileName) => fileName.endsWith(".expected-actions.json"))
    .sort((a, b) => a.localeCompare(b));

  const traces: TraceCase[] = [];

  for (const expectedFile of expectedActionFiles) {
    const contextFile = expectedFile.replace(".expected-actions.json", ".context.json");
    const contextRaw = await fs.readFile(path.join(tracesDir, contextFile), "utf8");
    const expectedRaw = await fs.readFile(path.join(tracesDir, expectedFile), "utf8");
    const context = traceContextSchema.parse(JSON.parse(contextRaw));
    const expected = expectedActionsSchema.parse(JSON.parse(expectedRaw));

    if (context.trace_id !== expected.trace_id) {
      throw new Error(`trace id mismatch for ${expectedFile}: ${context.trace_id} vs ${expected.trace_id}`);
    }

    traces.push({
      traceId: expected.trace_id,
      plannerContext: context.planner_context,
      expectedActions: expected.expected_semantic_actions
    });
  }

  return traces;
}

const traceCases = await loadTraceCases();

describe("golden trace replay parity", () => {
  it("loads at least one parity trace", () => {
    expect(traceCases.length).toBeGreaterThan(0);
  });

  for (const traceCase of traceCases) {
    it(`matches semantic parity for ${traceCase.traceId}`, () => {
      const context = toCanonicalPlannerContext(traceCase.plannerContext);
      const actualActions = planActions(context);
      const result = compareSemanticActions(traceCase.expectedActions, actualActions);

      expect(result.pass, formatSemanticParityDiff(result)).toBe(true);
    });
  }
});
