import fs from "node:fs";
import path from "node:path";

import {
  fileMatchesAnyGlob,
  getChangedFiles,
  integrationRootForFile,
  isLikelyIntegrationTest,
  isTestFile,
  isTypeScriptSourceFile,
  normalizePath,
  packageSourceGlobs,
  parseHarnessConfig,
  resolvePackageForFile,
} from "./lib.mjs";

function expectedSourceCandidates(testFile) {
  const normalized = normalizePath(testFile);
  const base = normalized.replace(/\.(test|spec)\.(ts|tsx)$/, "");

  return [`${base}.ts`, `${base}.tsx`];
}

function isCoLocatedUnitTest(testFile, pkg) {
  const sourceGlobs = packageSourceGlobs(pkg);
  if (!fileMatchesAnyGlob(testFile, sourceGlobs)) {
    return false;
  }

  const candidates = expectedSourceCandidates(testFile);
  return candidates.some((candidate) => fs.existsSync(path.resolve(process.cwd(), candidate)));
}

const harness = parseHarnessConfig();
const changedFiles = getChangedFiles();
const changedTestFiles = changedFiles.filter((file) => isTestFile(file));

const violations = [];

for (const file of changedTestFiles) {
  const pkg = resolvePackageForFile(file, harness.packages ?? []);
  if (!pkg) {
    continue;
  }

  const integrationRoot = integrationRootForFile(file, pkg);

  if (integrationRoot) {
    if (!isLikelyIntegrationTest(file)) {
      violations.push(
        `${file}: tests in integration roots must be integration/e2e tests (name/path must include e2e or integration)`,
      );
    }

    continue;
  }

  if (isLikelyIntegrationTest(file)) {
    violations.push(
      `${file}: integration/e2e tests must be placed in dedicated integration roots (${(pkg.integrationTestRoots ?? []).join(", ")})`,
    );
    continue;
  }

  if (!isCoLocatedUnitTest(file, pkg)) {
    violations.push(
      `${file}: unit tests must be co-located with source (same folder and basename) under source globs`,
    );
  }
}

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
}

if (violations.length > 0) {
  console.error("Structure policy violations:");
  for (const violation of violations) {
    console.error(`- ${violation}`);
  }
  process.exit(1);
}

console.log(
  `Structure policy check passed. changedFiles=${changedFiles.length} changedTests=${changedTestFiles.length}`,
);
