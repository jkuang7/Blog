#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const argv = process.argv.slice(2);
const targetArg = argv[0];

if (!targetArg || targetArg === "--help" || targetArg === "-h") {
  console.error("Deprecated: use node scripts/create-jian-app.mjs <target-path> instead.");
  console.error("");
  console.error("Compatibility usage:");
  console.error("  node scripts/bootstrap-llm-harness.mjs <repo-path> [--skip-install] [--skip-verify]");
  process.exit(targetArg ? 0 : 1);
}

const targetPath = path.resolve(targetArg);
const passthroughArgs = argv.slice(1);
const createJianAppPath = path.join(__dirname, "create-jian-app.mjs");
const targetHasPackageJson = fs.existsSync(path.join(targetPath, "package.json"));

console.warn("bootstrap-llm-harness.mjs is deprecated.");
console.warn("Use node scripts/create-jian-app.mjs <target-path> for new projects.");

const forwardedArgs = targetHasPackageJson
  ? [createJianAppPath, targetPath, "--overlay-only", ...passthroughArgs]
  : [createJianAppPath, targetPath, ...passthroughArgs];

const result = spawnSync(process.execPath, forwardedArgs, {
  stdio: "inherit",
});

process.exit(result.status ?? 1);
