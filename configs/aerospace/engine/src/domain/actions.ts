import { z } from "zod";

export const actionTypeSchema = z.enum([
  "NOOP",
  "STATE_MUTATION",
  "DELEGATE",
  "REBUILD",
  "SET_CHURN",
  "CAPTURE_SNAPSHOT",
  "WRITE_STATE",
  "MOVE_WINDOW"
]);

export type ActionType = z.infer<typeof actionTypeSchema>;

export const semanticActionSchema = z.object({
  order: z.number().int().nonnegative(),
  type: actionTypeSchema,
  target: z.string().min(1),
  reason: z.string().min(1),
  guard_result: z.string().min(1).default("passed"),
  details: z.record(z.string(), z.unknown()).default({})
});

export type SemanticAction = z.output<typeof semanticActionSchema>;
export type SemanticActionInput = z.input<typeof semanticActionSchema>;

function stableSortValue(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map((item) => stableSortValue(item));
  }
  if (value && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>).sort(([a], [b]) => a.localeCompare(b));
    const sorted: Record<string, unknown> = {};
    for (const [key, entryValue] of entries) {
      sorted[key] = stableSortValue(entryValue);
    }
    return sorted;
  }
  return value;
}

export function canonicalizeAction(action: SemanticActionInput): SemanticAction {
  const parsed = semanticActionSchema.parse(action);
  return {
    ...parsed,
    details: stableSortValue(parsed.details) as Record<string, unknown>
  };
}

export function canonicalizeActions(actions: readonly SemanticActionInput[]): SemanticAction[] {
  return [...actions]
    .map((action) => canonicalizeAction(action))
    .sort((a, b) => {
      if (a.order !== b.order) {
        return a.order - b.order;
      }
      if (a.type !== b.type) {
        return a.type.localeCompare(b.type);
      }
      if (a.target !== b.target) {
        return a.target.localeCompare(b.target);
      }
      if (a.reason !== b.reason) {
        return a.reason.localeCompare(b.reason);
      }
      return a.guard_result.localeCompare(b.guard_result);
    });
}

export function serializeActions(actions: readonly SemanticActionInput[]): string {
  const canonical = canonicalizeActions(actions);
  return JSON.stringify(canonical, null, 2);
}
