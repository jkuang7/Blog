import { describe, expect, it } from "vitest";

import { compareSemanticActions } from "../../../src/parity/semanticParity.js";

describe("semantic parity comparator", () => {
  it("passes when semantics match despite key order differences", () => {
    const expected = [
      {
        order: 10,
        type: "STATE_MUTATION",
        target: "workspace_state",
        reason: "managed-window-open",
        details: {
          browser: "safari",
          upnoteTiled: true
        }
      }
    ] as const;

    const actual = [
      {
        order: 10,
        type: "STATE_MUTATION",
        target: "workspace_state",
        reason: "managed-window-open",
        guard_result: "passed",
        details: {
          upnoteTiled: true,
          browser: "safari"
        }
      }
    ] as const;

    const result = compareSemanticActions(expected, actual);
    expect(result.pass).toBe(true);
    expect(result.mismatches).toEqual([]);
  });

  it("fails when sequence diverges", () => {
    const expected = [
      {
        order: 10,
        type: "STATE_MUTATION",
        target: "workspace_state",
        reason: "managed-window-open"
      }
    ] as const;

    const actual = [
      {
        order: 20,
        type: "REBUILD",
        target: "workspace:w1",
        reason: "delegate-balance-rebuild"
      }
    ] as const;

    const result = compareSemanticActions(expected, actual);
    expect(result.pass).toBe(false);
    expect(result.mismatches.some((mismatch) => mismatch.field === "type")).toBe(true);
  });
});
