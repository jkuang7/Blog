import { canonicalizeActions, serializeActions, type SemanticAction, type SemanticActionInput } from "../domain/actions.js";

export interface SemanticMismatch {
  index: number;
  field: "length" | "order" | "type" | "target" | "reason" | "guard_result" | "details";
  expected: unknown;
  actual: unknown;
}

export interface SemanticParityResult {
  pass: boolean;
  expected: SemanticAction[];
  actual: SemanticAction[];
  mismatches: SemanticMismatch[];
}

function stableString(value: unknown): string {
  if (value === null || value === undefined) {
    return String(value);
  }
  if (typeof value !== "object") {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => stableString(item)).join(",")}]`;
  }
  const entries = Object.entries(value as Record<string, unknown>).sort(([a], [b]) => a.localeCompare(b));
  const body = entries.map(([key, itemValue]) => `${JSON.stringify(key)}:${stableString(itemValue)}`).join(",");
  return `{${body}}`;
}

function compareActionField(
  mismatches: SemanticMismatch[],
  index: number,
  field: SemanticMismatch["field"],
  expected: unknown,
  actual: unknown
): void {
  if (stableString(expected) !== stableString(actual)) {
    mismatches.push({ index, field, expected, actual });
  }
}

export function compareSemanticActions(
  expectedInput: readonly SemanticActionInput[],
  actualInput: readonly SemanticActionInput[]
): SemanticParityResult {
  const expected = canonicalizeActions(expectedInput);
  const actual = canonicalizeActions(actualInput);
  const mismatches: SemanticMismatch[] = [];

  if (expected.length !== actual.length) {
    mismatches.push({
      index: -1,
      field: "length",
      expected: expected.length,
      actual: actual.length
    });
  }

  const maxLen = Math.max(expected.length, actual.length);
  for (let index = 0; index < maxLen; index += 1) {
    const expectedAction = expected[index];
    const actualAction = actual[index];

    if (!expectedAction || !actualAction) {
      continue;
    }

    compareActionField(mismatches, index, "order", expectedAction.order, actualAction.order);
    compareActionField(mismatches, index, "type", expectedAction.type, actualAction.type);
    compareActionField(mismatches, index, "target", expectedAction.target, actualAction.target);
    compareActionField(mismatches, index, "reason", expectedAction.reason, actualAction.reason);
    compareActionField(mismatches, index, "guard_result", expectedAction.guard_result, actualAction.guard_result);
    compareActionField(mismatches, index, "details", expectedAction.details, actualAction.details);
  }

  return {
    pass: mismatches.length === 0,
    expected,
    actual,
    mismatches
  };
}

export function formatSemanticParityDiff(result: SemanticParityResult): string {
  if (result.pass) {
    return "semantic parity passed";
  }

  const mismatchLines = result.mismatches.map((mismatch) => {
    const expected = typeof mismatch.expected === "string" ? mismatch.expected : JSON.stringify(mismatch.expected);
    const actual = typeof mismatch.actual === "string" ? mismatch.actual : JSON.stringify(mismatch.actual);
    return `index=${mismatch.index} field=${mismatch.field} expected=${expected} actual=${actual}`;
  });

  return [
    "semantic parity mismatches:",
    ...mismatchLines,
    "expected canonical actions:",
    serializeActions(result.expected),
    "actual canonical actions:",
    serializeActions(result.actual)
  ].join("\n");
}
