import path from "node:path";

import {
  ensureDir,
  normalizePath,
  parseHarnessConfig,
  summarizeRuleCounts,
  writeJson,
} from "./lib.mjs";
import { execFileSync } from "node:child_process";

function runEslintJsonForPackage(pkg) {
  const pkgRoot = pkg.root ?? ".";
  const args =
    pkgRoot === "."
      ? ["exec", "eslint", ".", "-f", "json"]
      : ["-C", pkgRoot, "exec", "eslint", ".", "-f", "json"];

  try {
    const stdout = execFileSync("pnpm", args, {
      cwd: process.cwd(),
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
      maxBuffer: 1024 * 1024 * 128,
    });

    return JSON.parse(stdout);
  } catch (error) {
    const stdout = error.stdout?.toString() ?? "";

    if (stdout.trim().length === 0) {
      throw error;
    }

    return JSON.parse(stdout);
  }
}

export function collectEslintReports() {
  const harness = parseHarnessConfig();
  const repoRoot = process.cwd();
  const combined = [];

  for (const pkg of harness.packages ?? []) {
    const report = runEslintJsonForPackage(pkg);

    for (const fileResult of report) {
      const relativeFilePath = normalizePath(path.relative(repoRoot, fileResult.filePath));
      combined.push({
        ...fileResult,
        filePath: relativeFilePath,
      });
    }
  }

  return combined;
}

export function writeEslintReport(report) {
  const codexDir = path.resolve(process.cwd(), ".codex");
  ensureDir(codexDir);

  const reportPath = path.join(codexDir, "eslint-report.json");
  writeJson(reportPath, { results: report });

  return reportPath;
}

export function flattenMessages(report) {
  const messages = [];

  for (const result of report) {
    for (const message of result.messages ?? []) {
      messages.push({
        file: normalizePath(result.filePath),
        ruleId: message.ruleId,
        severity: message.severity ?? 0,
        line: message.line ?? 0,
        message: message.message,
      });
    }
  }

  return messages;
}

export function printEslintSummary(messages) {
  const totals = {
    errors: messages.filter((message) => message.severity === 2).length,
    warnings: messages.filter((message) => message.severity === 1).length,
  };

  const topRules = summarizeRuleCounts(messages).slice(0, 10);

  console.log(`ESLint messages: ${messages.length} (errors: ${totals.errors}, warnings: ${totals.warnings})`);
  for (const [rule, count] of topRules) {
    console.log(`  ${rule}: ${count}`);
  }
}
