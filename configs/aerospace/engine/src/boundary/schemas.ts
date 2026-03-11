import { z } from "zod";

import { KNOWN_BUNDLE_IDS } from "../domain/contracts.js";

export const canonicalWorkspaceIdSchema = z.literal("w1");

export const callbackKindSchema = z.enum([
  "on_window",
  "on_focus",
  "balance",
  "switch_ws",
  "reset_ws",
  "move_to_focused",
  "video_mode"
]);

export const bundleIdSchema = z.string().trim().min(1);

export const knownBundleIdSchema = z.enum(KNOWN_BUNDLE_IDS);

export const browserStateSchema = z.enum(["zen", "safari", ""]);

export const workspaceStateSchema = z.object({
  workspace: canonicalWorkspaceIdSchema,
  browser: browserStateSchema,
  upnoteTiled: z.boolean(),
  tiledOrder: z.array(z.number().int().nonnegative())
});

export const workspaceStateV2Schema = z.object({
  version: z.literal(2),
  workspace: canonicalWorkspaceIdSchema,
  browser: browserStateSchema,
  upnoteTiled: z.boolean(),
  tiledOrder: z.array(z.number().int().nonnegative()),
  updatedAtMs: z.number().int().nonnegative().optional()
});

export const callbackInputSchema = z.object({
  kind: callbackKindSchema,
  workspace: canonicalWorkspaceIdSchema,
  bundleId: bundleIdSchema.optional(),
  argv: z.array(z.string()),
  timestampMs: z.number().int().nonnegative()
});

export const windowLayoutSchema = z.enum([
  "floating",
  "h_tiles",
  "v_tiles",
  "stacked",
  "accordion"
]);

export const windowSnapshotRowSchema = z.object({
  windowId: z.number().int().nonnegative(),
  workspace: canonicalWorkspaceIdSchema,
  bundleId: bundleIdSchema,
  layout: windowLayoutSchema,
  title: z.string()
});

export const focusedWindowSchema = windowSnapshotRowSchema;

export const plannerContextSchema = z.object({
  callback: callbackInputSchema,
  workspaceState: workspaceStateSchema,
  focusedWindow: focusedWindowSchema.nullable(),
  windows: z.array(windowSnapshotRowSchema),
  guards: z.object({
    isHomeWorkspace: z.boolean(),
    isManagedBundle: z.boolean(),
    isPopupIntent: z.boolean()
  })
});

export type PlannerContextInput = z.input<typeof plannerContextSchema>;
export type PlannerContextOutput = z.output<typeof plannerContextSchema>;
export type WorkspaceStateV2Input = z.input<typeof workspaceStateV2Schema>;
export type WorkspaceStateV2Output = z.output<typeof workspaceStateV2Schema>;
