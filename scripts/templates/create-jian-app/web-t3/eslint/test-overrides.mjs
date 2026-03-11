import { buildTestRules } from "./base-rules.mjs";

export const DEFAULT_TEST_GLOBS = [
  "**/*.test.ts",
  "**/*.test.tsx",
  "**/*.spec.ts",
  "**/*.spec.tsx",
  "tests/**/*.ts",
  "tests/**/*.tsx",
];

export function createTestOverride(files = DEFAULT_TEST_GLOBS) {
  return {
    files,
    rules: buildTestRules(),
  };
}
