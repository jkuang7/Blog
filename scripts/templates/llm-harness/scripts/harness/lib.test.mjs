import assert from "node:assert/strict";
import test from "node:test";

import { applyNameStatusChanges } from "./lib.mjs";

test("applyNameStatusChanges overlays staged renames and deletions onto base files", () => {
  const changedFiles = applyNameStatusChanges(
    [
      "src/hooks/useAppLayoutRuntimeContext.wrapper.test.ts",
      "src/store/store.integration.test.ts",
      "src/store.ts",
    ],
    [
      "R100\tsrc/hooks/useAppLayoutRuntimeContext.wrapper.test.ts\tsrc/hooks/useAppLayoutRuntimeContext.spec.ts",
      "R100\tsrc/store/store.integration.test.ts\ttests/store.integration.test.ts",
      "M\tsrc/store.ts",
      "D\tsrc/store.project-events.test.ts",
      "A\tsrc/store.test.ts",
    ].join("\n"),
  );

  assert.deepEqual(changedFiles, [
    "src/store.ts",
    "src/hooks/useAppLayoutRuntimeContext.spec.ts",
    "tests/store.integration.test.ts",
    "src/store.test.ts",
  ]);
});
