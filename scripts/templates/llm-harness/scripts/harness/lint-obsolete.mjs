import path from "node:path";

import { getChangedFiles, readGitDiffForFiles } from "./lib.mjs";
import { parseAddedLineViolations } from "./lint-obsolete-lib.mjs";

// Intentionally future-feature safe: this gate targets explicit lifecycle markers
// and backup artifacts, while allowing incomplete scaffolding work.
const SOURCE_EXTENSIONS = new Set([".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"]);

const changedSourceFiles = getChangedFiles().filter((file) =>
  SOURCE_EXTENSIONS.has(path.extname(file)),
);

const backupArtifactViolations = changedSourceFiles.filter((file) => {
  const basename = path.basename(file).toLowerCase();
  return (
    /\.(bak|orig|rej)$/.test(basename) ||
    /(?:^|[-_.])(old|backup|copy)(?:[-_.]|$)/.test(basename)
  );
});

if (backupArtifactViolations.length > 0) {
  console.error("Obsolete file artifact violations:");
  for (const file of backupArtifactViolations) {
    console.error(`- ${file}`);
  }
  console.error("Remove backup/copy artifacts before committing.");
  process.exit(1);
}

if (changedSourceFiles.length === 0) {
  console.log("Obsolete code check passed. changedSourceFiles=0");
  process.exit(0);
}

const diff = readGitDiffForFiles(changedSourceFiles, { unified: 0 });

if (!diff.ok) {
  console.error("Failed to read git diff for obsolete code check.");
  process.exit(1);
}

const violations = parseAddedLineViolations(diff.output);

if (violations.length > 0) {
  console.error("Obsolete code policy violations in added lines:");
  for (const violation of violations.slice(0, 100)) {
    console.error(
      `- ${violation.file}:${violation.line} [${violation.code}] ${violation.message}`,
    );
    console.error(`  + ${violation.text}`);
  }
  console.error("Remove or rewrite flagged lines before committing.");
  process.exit(1);
}

console.log(
  `Obsolete code check passed. changedSourceFiles=${changedSourceFiles.length} scannedRange=${diff.range}`,
);
