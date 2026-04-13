#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const DEFAULT_DEV_ROOT = path.resolve(__dirname, "../..");
const AUTO_COMMIT_PATHS = ["workspace/repos.txt", ".bootstrap/codex"];
const DEFAULT_COMMIT_MESSAGE = "chore: sync bootstrap snapshot";

main();

function main() {
  try {
    const args = parseArgs(process.argv.slice(2));
    const preGenerateStatus = captureWorktreeStatus(args.devRoot);
    const branchName = currentBranch(args.devRoot);
    runGenerate(args.generateScript, args.generateArgs);
    maybeCommitSnapshot(args.devRoot, branchName, preGenerateStatus, args.commitMessage);
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  }
}

function parseArgs(argv) {
  const args = {
    devRoot: DEFAULT_DEV_ROOT,
    generateScript: null,
    generateArgs: ["generate"],
    commitMessage: DEFAULT_COMMIT_MESSAGE,
  };

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--dev-root") {
      const value = argv[++index];
      if (!value) {
        throw new Error("Missing value for --dev-root");
      }
      args.devRoot = path.resolve(value);
      continue;
    }

    if (arg === "--generate-script") {
      const value = argv[++index];
      if (!value) {
        throw new Error("Missing value for --generate-script");
      }
      args.generateScript = path.resolve(value);
      continue;
    }

    if (arg === "--commit-message") {
      const value = argv[++index];
      if (!value) {
        throw new Error("Missing value for --commit-message");
      }
      args.commitMessage = value;
      continue;
    }

    if (arg === "--") {
      args.generateArgs = argv.slice(index + 1);
      break;
    }

    throw new Error(`Unexpected argument: ${arg}`);
  }

  args.generateScript = args.generateScript ?? path.join(args.devRoot, "workspace", "scripts", "repos-bootstrap.mjs");
  return args;
}

function runGenerate(generateScript, generateArgs) {
  const result = spawnSync(process.execPath, [generateScript, ...generateArgs], {
    stdio: "inherit",
    env: process.env,
  });

  if (result.error) {
    throw result.error;
  }

  if (result.status !== 0) {
    throw new Error(`bootstrap push failed with exit code ${result.status}`);
  }
}

function maybeCommitSnapshot(devRoot, branchName, preGenerateStatus, commitMessage) {
  const postGenerateStatus = captureWorktreeStatus(devRoot);
  if (!postGenerateStatus.entries.length) {
    console.log("[Dev] bootstrap push ok (no tracked snapshot changes)");
    return;
  }

  if (preGenerateStatus.entries.length) {
    console.warn("[Dev] warning: auto-commit skipped (dirty worktree before push)");
    return;
  }

  if (!branchName) {
    console.warn("[Dev] warning: auto-commit skipped (detached HEAD)");
    return;
  }

  if (branchName !== "main") {
    console.warn(`[Dev] warning: auto-commit skipped (branch is ${branchName}, expected main)`);
    return;
  }

  const unrelatedEntries = postGenerateStatus.entries.filter((entry) => !isAllowedPath(entry.path));
  if (unrelatedEntries.length > 0) {
    console.warn("[Dev] warning: auto-commit skipped (unexpected changes outside bootstrap snapshot)");
    return;
  }

  runCommand("git", ["-C", devRoot, "add", "--all", "--", ...AUTO_COMMIT_PATHS], {
    label: "[Dev] stage snapshot",
  });

  const stagedNames = runCommandCapture("git", ["-C", devRoot, "diff", "--cached", "--name-only"], {
    label: "[Dev] staged names",
  }).stdout
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);

  if (stagedNames.length === 0) {
    console.log("[Dev] bootstrap push ok (no staged snapshot changes)");
    return;
  }

  runCommand("git", ["-C", devRoot, "commit", "-m", commitMessage], {
    label: "[Dev] commit snapshot",
  });
  console.log(`[Dev] bootstrap push committed ${stagedNames.length} path(s) on main`);
}

function isAllowedPath(relativePath) {
  return AUTO_COMMIT_PATHS.some((prefix) => relativePath === prefix || relativePath.startsWith(`${prefix}/`));
}

function captureWorktreeStatus(devRoot) {
  const output = runCommandCapture("git", ["-C", devRoot, "status", "--short"], {
    label: "[Dev] status",
  }).stdout;
  const entries = output
    .split(/\r?\n/)
    .map((line) => line.trimEnd())
    .filter(Boolean)
    .map((line) => ({
      raw: line,
      path: line.slice(3),
    }));
  return { entries };
}

function currentBranch(devRoot) {
  const result = runCommandCapture("git", ["-C", devRoot, "symbolic-ref", "--quiet", "--short", "HEAD"], {
    label: "[Dev] branch",
    allowFailure: true,
  });
  return result.status === 0 ? result.stdout.trim() : "";
}

function runCommand(commandName, args, options) {
  const result = spawnSync(commandName, args, {
    encoding: "utf8",
    stdio: "inherit",
    env: process.env,
  });

  if (result.error) {
    if (result.error.code === "ENOENT") {
      throw new Error(`${options.label} failed: executable not found (${commandName})`);
    }

    throw result.error;
  }

  if (result.status !== 0) {
    throw new Error(`${options.label} failed with exit code ${result.status}`);
  }
}

function runCommandCapture(commandName, args, options) {
  const result = spawnSync(commandName, args, {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
    env: process.env,
  });

  if (result.error) {
    if (result.error.code === "ENOENT") {
      throw new Error(`${options.label} failed: executable not found (${commandName})`);
    }

    throw result.error;
  }

  if (!options.allowFailure && result.status !== 0) {
    const stderr = result.stderr?.trim();
    throw new Error(stderr ? `${options.label} failed: ${stderr}` : `${options.label} failed with exit code ${result.status}`);
  }

  return {
    status: result.status ?? 0,
    stdout: result.stdout ?? "",
    stderr: result.stderr ?? "",
  };
}
