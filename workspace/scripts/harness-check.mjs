#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const DEFAULT_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const REQUIRED_CARD_PHRASE = "Context and Reuse Card";
const FORBIDDEN_TRACKED_PREFIXES = [
  ".bootstrap/",
  ".playwright-mcp/",
  ".codex/browser-profiles/",
  ".codex/tmp/",
  ".codex/stitch-runs/",
  ".codex/proofs/",
  ".codex/sanitized-mirrors/",
  ".codex/transfer/",
  ".restore/",
  ".restore-backups/",
  ".repo-archives/",
  "Repos/",
];

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main();
}

function main() {
  const root = parseRoot(process.argv.slice(2));
  const failures = checkHarness(root);
  if (failures.length > 0) {
    for (const failure of failures) {
      console.error(`harness-check: ${failure}`);
    }
    process.exitCode = 1;
    return;
  }
  console.log("harness-check: ok");
}

export function checkHarness(root) {
  const failures = [];
  const requiredFiles = [
    "AGENTS.md",
    ".gitignore",
    "package.json",
    ".codex/docs/agent-harness.md",
    ".codex/commands/_reference_telecodex_linear.md",
    ".codex/commands/add.md",
    ".codex/commands/run.md",
    ".codex/commands/review.md",
  ];

  for (const file of requiredFiles) {
    if (!fs.existsSync(path.join(root, file))) {
      failures.push(`missing required file: ${file}`);
    }
  }

  if (failures.length > 0) {
    return failures;
  }

  const agentsLineCount = readText(root, "AGENTS.md").trimEnd().split(/\r?\n/).length;
  if (agentsLineCount > 140) {
    failures.push(`AGENTS.md is too large (${agentsLineCount} lines); keep it lean and move detail into .codex/docs`);
  }

  for (const file of [
    ".codex/docs/agent-harness.md",
    ".codex/commands/_reference_telecodex_linear.md",
    ".codex/commands/add.md",
    ".codex/commands/run.md",
    ".codex/commands/review.md",
  ]) {
    if (!readText(root, file).includes(REQUIRED_CARD_PHRASE)) {
      failures.push(`${file} must reference ${REQUIRED_CARD_PHRASE}`);
    }
  }

  const scannedTextFiles = [
    "AGENTS.md",
    "workspace/AGENTS.md",
    ".gitignore",
    "repos.txt",
    ".codex/docs/agent-harness.md",
    ".codex/commands/_reference_telecodex_linear.md",
    ".codex/commands/add.md",
    ".codex/commands/run.md",
    ".codex/commands/review.md",
    ".codex/commands/commit.md",
  ];
  for (const file of scannedTextFiles) {
    const filePath = path.join(root, file);
    if (fs.existsSync(filePath) && readText(root, file).includes(".bootstrap")) {
      failures.push(`${file} contains stale .bootstrap reference`);
    }
  }

  if (fs.existsSync(path.join(root, ".codex/commands/helix.md"))) {
    failures.push(".codex/commands/helix.md should not be active in the clean Dev layout");
  }

  const reposConfig = readText(root, "repos.txt");
  if (/^helix-work\t/m.test(reposConfig)) {
    failures.push("repos.txt still manages retired helix-work");
  }
  for (const requiredRepo of ["telecodex", "tmux-codex"]) {
    if (!new RegExp(`^${requiredRepo}\\t`, "m").test(reposConfig)) {
      failures.push(`repos.txt must preserve ${requiredRepo}`);
    }
  }
  failures.push(...checkManagedRepos(root, reposConfig));

  const packageJson = JSON.parse(readText(root, "package.json"));
  for (const script of ["bootstrap", "pull", "push", "harness:check", "verify"]) {
    if (!packageJson.scripts?.[script]) {
      failures.push(`package.json missing script: ${script}`);
    }
  }

  for (const file of listTrackedFiles(root)) {
    for (const prefix of FORBIDDEN_TRACKED_PREFIXES) {
      if (file === prefix.slice(0, -1) || file.startsWith(prefix)) {
        failures.push(`forbidden tracked runtime/repo path: ${file}`);
      }
    }
  }

  return failures;
}

function checkManagedRepos(root, reposConfig) {
  const failures = [];

  for (const repo of parseManagedRepoNames(reposConfig)) {
    const repoRoot = path.join(root, "Repos", repo);
    if (!fs.existsSync(repoRoot)) {
      continue;
    }

    for (const localDir of [".codex", ".memory", ".restore", ".repo-archives", ".archive"]) {
      if (fs.existsSync(path.join(repoRoot, localDir))) {
        failures.push(`managed repo ${repo} contains forbidden local state: ${localDir}`);
      }
    }

    const packageJsonPath = path.join(repoRoot, "package.json");
    if (fs.existsSync(packageJsonPath)) {
      const packageJson = JSON.parse(fs.readFileSync(packageJsonPath, "utf8"));
      const scripts = packageJson.scripts ?? {};
      for (const scriptName of ["context:pack", "context:check"]) {
        if (scripts[scriptName]) {
          failures.push(`managed repo ${repo} still defines ${scriptName}`);
        }
      }
      for (const [scriptName, scriptValue] of Object.entries(scripts)) {
        if (typeof scriptValue === "string" && scriptValue.includes("build-context-pack")) {
          failures.push(`managed repo ${repo} script ${scriptName} still calls build-context-pack`);
        }
      }
    }

    if (fs.existsSync(path.join(repoRoot, "scripts/llm/build-context-pack.mjs"))) {
      failures.push(`managed repo ${repo} still has scripts/llm/build-context-pack.mjs`);
    }
  }

  return failures;
}

function parseManagedRepoNames(reposConfig) {
  return reposConfig
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith("#"))
    .map((line) => line.split(/\s+/)[0])
    .filter(Boolean);
}

function parseRoot(argv) {
  const rootFlagIndex = argv.indexOf("--root");
  if (rootFlagIndex === -1) {
    return DEFAULT_ROOT;
  }
  const value = argv[rootFlagIndex + 1];
  if (!value) {
    throw new Error("Missing value for --root");
  }
  return path.resolve(value);
}

function readText(root, relativePath) {
  return fs.readFileSync(path.join(root, relativePath), "utf8");
}

function listTrackedFiles(root) {
  if (!fs.existsSync(path.join(root, ".git"))) {
    return listFiles(root);
  }
  const result = spawnSync("git", ["-C", root, "ls-files"], {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
  });
  if (result.status !== 0) {
    throw new Error(result.stderr.trim() || "git ls-files failed");
  }
  return result.stdout.split(/\r?\n/).filter(Boolean);
}

function listFiles(root) {
  const out = [];
  walk(root, "");
  return out;

  function walk(base, relative) {
    for (const entry of fs.readdirSync(base, { withFileTypes: true })) {
      if (entry.name === ".git") {
        continue;
      }
      const nextRelative = relative ? `${relative}/${entry.name}` : entry.name;
      const nextPath = path.join(base, entry.name);
      if (entry.isDirectory()) {
        walk(nextPath, nextRelative);
      } else {
        out.push(nextRelative);
      }
    }
  }
}
