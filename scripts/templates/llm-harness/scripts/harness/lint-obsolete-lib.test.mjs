import assert from "node:assert/strict";
import test from "node:test";

import { parseAddedLineViolations } from "./lint-obsolete-lib.mjs";

test("flags obsolete markers in added comments", () => {
  const diff = [
    "diff --git a/src/example.ts b/src/example.ts",
    "+++ b/src/example.ts",
    "@@ +10,1 @@",
    "+// temporary workaround until adapter migration lands",
  ].join("\n");

  const violations = parseAddedLineViolations(diff);

  assert.equal(violations.length, 1);
  assert.equal(violations[0].file, "src/example.ts");
  assert.equal(violations[0].line, 10);
});

test("ignores legitimate strings that mention obsolete data", () => {
  const diff = [
    "diff --git a/src/example.test.ts b/src/example.test.ts",
    "+++ b/src/example.test.ts",
    "@@ +40,2 @@",
    '+  it("cleans obsolete settings keys during schema ensure", async () => {',
    '+    code: "obsolete-marker",',
  ].join("\n");

  const violations = parseAddedLineViolations(diff);

  assert.equal(violations.length, 0);
});
