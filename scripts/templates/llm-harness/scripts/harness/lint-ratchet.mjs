import path from "node:path";

import {
  getChangedFiles,
  parseHarnessConfig,
  readJson,
  summarizeRuleCounts,
} from "./lib.mjs";
import { collectEslintReports, flattenMessages, writeEslintReport } from "./eslint-report.mjs";

function parseNumericMetric(messageText) {
  const standardLimitMatch = messageText.match(/\((\d+)\).*Maximum allowed is (\d+)/i);
  if (standardLimitMatch) {
    return {
      current: Number.parseInt(standardLimitMatch[1], 10),
      limit: Number.parseInt(standardLimitMatch[2], 10),
    };
  }

  const complexityMatch = messageText.match(/complexity of (\d+)\. Maximum allowed is (\d+)/i);
  if (complexityMatch) {
    return {
      current: Number.parseInt(complexityMatch[1], 10),
      limit: Number.parseInt(complexityMatch[2], 10),
    };
  }

  const cognitiveMatch = messageText.match(/from (\d+) to the (\d+) allowed/i);
  if (cognitiveMatch) {
    return {
      current: Number.parseInt(cognitiveMatch[1], 10),
      limit: Number.parseInt(cognitiveMatch[2], 10),
    };
  }

  return null;
}

function debtKey(rule, file, line) {
  return `${rule}|${file}|${line}`;
}

const harness = parseHarnessConfig();
const report = collectEslintReports();
writeEslintReport(report);

const messages = flattenMessages(report).filter((message) => message.severity > 0);
const changedFiles = new Set(getChangedFiles());

const architectureRules = new Set(harness.architectureRules ?? []);
const maintainabilityRules = new Set(harness.maintainabilityRules ?? []);

const architectureViolations = messages.filter(
  (message) => architectureRules.has(message.ruleId),
);

if (architectureViolations.length > 0) {
  console.error("Architecture violations found:");
  for (const violation of architectureViolations.slice(0, 50)) {
    console.error(
      `- ${violation.file}:${violation.line} [${violation.ruleId}] ${violation.message}`,
    );
  }
  process.exit(1);
}

const debtPath = path.resolve(process.cwd(), ".lint-debt.json");
const debt = readJson(debtPath);
const debtEntries = debt.entries ?? [];

const debtByExactKey = new Map();
const debtByFileRule = new Map();
for (const entry of debtEntries) {
  const file = entry.file;
  const rule = entry.rule;
  const line = Number(entry.line ?? 0);

  debtByExactKey.set(debtKey(rule, file, line), entry);

  const fileRuleKey = `${rule}|${file}`;
  if (!debtByFileRule.has(fileRuleKey)) {
    debtByFileRule.set(fileRuleKey, []);
  }

  debtByFileRule.get(fileRuleKey).push(entry);
}

const maintainabilityViolations = messages.filter(
  (message) => maintainabilityRules.has(message.ruleId),
);

const today = new Date().toISOString().slice(0, 10);
const newUntrackedViolations = [];
const worsenedViolations = [];
const expiredViolations = [];
const paydownSuggestions = [];
const usedDebtKeys = new Set();

for (const violation of maintainabilityViolations) {
  const exactKey = debtKey(violation.ruleId, violation.file, violation.line);
  const looseKey = `${violation.ruleId}|${violation.file}`;

  const exactEntry = debtByExactKey.get(exactKey);
  const fallbackEntry = exactEntry ? null : (debtByFileRule.get(looseKey) ?? [])[0] ?? null;
  const matchedEntry = exactEntry ?? fallbackEntry;

  if (!matchedEntry) {
    if (changedFiles.has(violation.file)) {
      newUntrackedViolations.push(violation);
    }
    continue;
  }

  const key = debtKey(
    matchedEntry.rule,
    matchedEntry.file,
    Number(matchedEntry.line ?? 0),
  );
  usedDebtKeys.add(key);

  const expiresOn = matchedEntry.expiresOn;
  if (typeof expiresOn === "string" && expiresOn < today) {
    expiredViolations.push({
      violation,
      debt: matchedEntry,
    });
  }

  const metric = parseNumericMetric(violation.message);
  if (metric && typeof matchedEntry.current === "number") {
    if (metric.current > matchedEntry.current) {
      worsenedViolations.push({
        violation,
        debt: matchedEntry,
        metric,
      });
    }

    if (metric.current < matchedEntry.current) {
      paydownSuggestions.push({
        violation,
        debt: matchedEntry,
        metric,
      });
    }

    if (typeof matchedEntry.limit === "number" && metric.limit !== matchedEntry.limit) {
      worsenedViolations.push({
        violation,
        debt: matchedEntry,
        metric,
        reason: "limit changed",
      });
    }
  }
}

if (
  newUntrackedViolations.length > 0 ||
  worsenedViolations.length > 0 ||
  expiredViolations.length > 0
) {
  if (newUntrackedViolations.length > 0) {
    console.error("New maintainability violations in changed files:");
    for (const violation of newUntrackedViolations.slice(0, 80)) {
      console.error(
        `- ${violation.file}:${violation.line} [${violation.ruleId}] ${violation.message}`,
      );
    }
  }

  if (worsenedViolations.length > 0) {
    console.error("Worsened debt violations:");
    for (const entry of worsenedViolations.slice(0, 80)) {
      const reason = entry.reason ? ` (${entry.reason})` : "";
      console.error(
        `- ${entry.violation.file}:${entry.violation.line} [${entry.violation.ruleId}] current=${entry.metric.current} debt=${entry.debt.current}${reason}`,
      );
    }
  }

  if (expiredViolations.length > 0) {
    console.error("Expired debt entries still active:");
    for (const entry of expiredViolations.slice(0, 80)) {
      console.error(
        `- ${entry.violation.file}:${entry.violation.line} [${entry.violation.ruleId}] expired=${entry.debt.expiresOn}`,
      );
    }
  }

  process.exit(1);
}

const counts = summarizeRuleCounts(maintainabilityViolations);
console.log(
  `Maintainability ratchet check passed. violations=${maintainabilityViolations.length} trackedDebt=${usedDebtKeys.size}`,
);
for (const [rule, count] of counts.slice(0, 10)) {
  console.log(`  ${rule}: ${count}`);
}

if (paydownSuggestions.length > 0) {
  console.log("Debt paydown opportunities detected:");
  for (const suggestion of paydownSuggestions.slice(0, 30)) {
    console.log(
      `- ${suggestion.violation.file}:${suggestion.violation.line} [${suggestion.violation.ruleId}] ${suggestion.debt.current} -> ${suggestion.metric.current}`,
    );
  }
}
