import fs from "node:fs";
import path from "node:path";

import {
  colocatedTestCandidates,
  escapeHatchChanged,
  fileMatchesAnyGlob,
  getChangedFiles,
  isTypeScriptSourceFile,
  packageSourceGlobs,
  parseHarnessConfig,
  resolvePackageForFile,
} from "./lib.mjs";

const harness = parseHarnessConfig();
const changedFiles = getChangedFiles();

if (escapeHatchChanged(changedFiles)) {
  console.log("tests:changed bypassed via .ci/TESTS_EXEMPTION.md");
  process.exit(0);
}

const violations = [];

for (const file of changedFiles) {
  if (!isTypeScriptSourceFile(file)) {
    continue;
  }

  const pkg = resolvePackageForFile(file, harness.packages ?? []);
  if (!pkg) {
    continue;
  }

  const sourceGlobs = packageSourceGlobs(pkg);
  if (!fileMatchesAnyGlob(file, sourceGlobs)) {
    continue;
  }

  const candidates = colocatedTestCandidates(file);
  const hasColocatedTest = candidates.some((candidate) =>
    fs.existsSync(path.resolve(process.cwd(), candidate)),
  );

  if (!hasColocatedTest) {
    violations.push({
      file,
      candidates,
    });
  }
}

if (violations.length > 0) {
  console.error("Missing co-located unit tests for changed source files:");
  for (const violation of violations) {
    console.error(`- ${violation.file}`);
    console.error(`  expected one of: ${violation.candidates.join(", ")}`);
  }
  console.error("Add tests or include .ci/TESTS_EXEMPTION.md in the PR diff with justification.");
  process.exit(1);
}

console.log(`tests:changed passed for ${changedFiles.length} changed files.`);
