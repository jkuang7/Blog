#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const DEFAULT_DEV_ROOT = path.resolve(__dirname, "..");

const CONFIG_HEADER = [
  "# Managed repositories - one per line",
  "# Format: <directory>\\t<remote-origin-url>",
  "# Remove line to stop bootstrap management (won't auto-delete local folders)",
  "# Generated from standalone git repos under Repos/*",
  "",
];

const SUPPORTED_PACKAGE_MANAGERS = new Map([
  ["pnpm", { install: ["install"] }],
  ["npm", { install: ["install"] }],
  ["yarn", { install: ["install"] }],
  ["bun", { install: ["install"] }],
]);

main();

function main() {
  try {
    const args = parseArgs(process.argv.slice(2));

    if (args.command === "help") {
      printUsage();
      return;
    }

    if (!args.command) {
      printUsage();
      process.exitCode = 1;
      return;
    }

    if (args.command === "generate") {
      runGenerate(args);
      return;
    }

    if (args.command === "bootstrap") {
      runBootstrap(args);
      return;
    }

    throw new Error(`Unknown command: ${args.command}`);
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  }
}

function parseArgs(argv) {
  const args = {
    command: null,
    devRoot: DEFAULT_DEV_ROOT,
    reposRoot: null,
    configPath: null,
    withBuild: false,
    withCheck: false,
  };

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];

    if (!args.command && !arg.startsWith("--")) {
      args.command = arg === "--help" || arg === "-h" ? "help" : arg;
      continue;
    }

    if (arg === "--help" || arg === "-h") {
      args.command = "help";
      continue;
    }

    if (arg === "--with-build") {
      args.withBuild = true;
      continue;
    }

    if (arg === "--with-check") {
      args.withCheck = true;
      continue;
    }

    if (arg === "--dev-root") {
      args.devRoot = resolveRequiredPath(argv, ++index, "--dev-root");
      continue;
    }

    if (arg === "--repos-root") {
      args.reposRoot = resolveRequiredPath(argv, ++index, "--repos-root");
      continue;
    }

    if (arg === "--config") {
      args.configPath = resolveRequiredPath(argv, ++index, "--config");
      continue;
    }

    throw new Error(`Unexpected argument: ${arg}`);
  }

  args.devRoot = path.resolve(args.devRoot);
  args.reposRoot = path.resolve(args.reposRoot ?? path.join(args.devRoot, "Repos"));
  args.configPath = path.resolve(args.configPath ?? path.join(args.devRoot, "repos.txt"));

  return args;
}

function resolveRequiredPath(argv, index, flagName) {
  const value = argv[index];
  if (!value) {
    throw new Error(`Missing value for ${flagName}`);
  }
  return value;
}

function printUsage() {
  console.log(
    [
      "Usage:",
      "  node scripts/repos-bootstrap.mjs generate [--dev-root <path>] [--repos-root <path>] [--config <path>]",
      "  node scripts/repos-bootstrap.mjs bootstrap [--dev-root <path>] [--repos-root <path>] [--config <path>] [--with-build] [--with-check]",
      "  node scripts/repos-bootstrap.mjs help",
    ].join("\n"),
  );
}

function runGenerate(args) {
  ensureDirectoryExists(args.reposRoot, "Repos root");

  const summary = {
    written: 0,
    skippedMissingOrigin: [],
    ignored: [],
  };
  const entries = [];
  const children = fs.readdirSync(args.reposRoot, { withFileTypes: true });

  for (const child of children) {
    if (!child.isDirectory()) {
      continue;
    }

    if (child.name.startsWith(".")) {
      summary.ignored.push(child.name);
      continue;
    }

    const repoPath = path.join(args.reposRoot, child.name);
    if (!isStandaloneGitRepo(repoPath)) {
      summary.ignored.push(child.name);
      continue;
    }

    const origin = getGitOrigin(repoPath);
    if (!origin) {
      summary.skippedMissingOrigin.push(child.name);
      console.warn(`[${child.name}] warning: missing remote.origin.url, skipped`);
      continue;
    }

    entries.push({ directory: child.name, url: origin });
  }

  entries.sort((left, right) => left.directory.localeCompare(right.directory, "en", { sensitivity: "case" }));

  if (entries.length === 0) {
    throw new Error("No valid managed repos found; leaving existing config untouched");
  }

  const contents = buildConfigFile(entries);
  writeFileAtomically(args.configPath, contents);

  summary.written = entries.length;

  console.log(`Wrote ${args.configPath}`);
  console.log(`written: ${summary.written}`);
  console.log(`skipped missing origin: ${summary.skippedMissingOrigin.length}`);
  console.log(`ignored non-standalone: ${summary.ignored.length}`);
}

function runBootstrap(args) {
  ensureDirectoryExists(args.reposRoot, "Repos root");
  const entries = parseConfigFile(args.configPath);
  const summary = {
    cloned: 0,
    exists: 0,
    installOk: 0,
    buildOk: 0,
    buildSkipped: 0,
    checkOk: 0,
    checkSkipped: 0,
    warnings: 0,
    errors: 0,
  };

  for (const entry of entries) {
    const repoPrefix = `[${entry.directory}]`;
    try {
      const resolution = reconcileRepo(entry, args.reposRoot, repoPrefix);
      if (resolution.state === "cloned") {
        summary.cloned += 1;
        console.log(`${repoPrefix} cloned`);
      } else if (resolution.state === "exists") {
        summary.exists += 1;
        console.log(`${repoPrefix} exists`);
      } else if (resolution.state === "skipped") {
        summary.warnings += 1;
        console.warn(`${repoPrefix} warning: ${resolution.reason}`);
        continue;
      }

      const installOutcome = runInstallSetup(resolution.repoPath, repoPrefix);
      summary.installOk += installOutcome.ranAnyInstall ? 1 : 0;
      summary.warnings += installOutcome.warningCount;

      if (args.withBuild) {
        const buildOutcome = runBuild(resolution.repoPath, repoPrefix);
        if (buildOutcome.ran) {
          summary.buildOk += 1;
        } else {
          summary.buildSkipped += 1;
        }
      }

      if (args.withCheck) {
        const checkOutcome = runCheck(resolution.repoPath, repoPrefix);
        if (checkOutcome.ran) {
          summary.checkOk += 1;
        } else {
          summary.checkSkipped += 1;
        }
      }
    } catch (error) {
      summary.errors += 1;
      console.error(`${repoPrefix} error: ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  console.log("Summary:");
  console.log(`cloned: ${summary.cloned}`);
  console.log(`exists: ${summary.exists}`);
  console.log(`install ok: ${summary.installOk}`);
  console.log(`build ok: ${summary.buildOk}`);
  console.log(`build skipped: ${summary.buildSkipped}`);
  console.log(`check ok: ${summary.checkOk}`);
  console.log(`check skipped: ${summary.checkSkipped}`);
  console.log(`warnings: ${summary.warnings}`);
  console.log(`errors: ${summary.errors}`);

  if (summary.errors > 0) {
    process.exitCode = 1;
  }
}

function ensureDirectoryExists(directoryPath, label) {
  if (!fs.existsSync(directoryPath)) {
    throw new Error(`${label} does not exist: ${directoryPath}`);
  }

  if (!fs.statSync(directoryPath).isDirectory()) {
    throw new Error(`${label} is not a directory: ${directoryPath}`);
  }
}

function buildConfigFile(entries) {
  const lines = [...CONFIG_HEADER, ...entries.map((entry) => `${entry.directory}\t${entry.url}`)];
  return `${lines.join("\n")}\n`;
}

function writeFileAtomically(targetPath, contents) {
  fs.mkdirSync(path.dirname(targetPath), { recursive: true });
  const tempPath = path.join(
    path.dirname(targetPath),
    `.${path.basename(targetPath)}.tmp-${process.pid}-${Date.now()}-${Math.random().toString(16).slice(2)}`,
  );
  fs.writeFileSync(tempPath, contents);
  fs.renameSync(tempPath, targetPath);
}

function parseConfigFile(configPath) {
  if (!fs.existsSync(configPath)) {
    throw new Error(`Config file not found: ${configPath}`);
  }

  const contents = fs.readFileSync(configPath, "utf8");
  const lines = contents.split(/\r?\n/);
  const entries = [];
  const seenDirectories = new Set();

  for (const [index, rawLine] of lines.entries()) {
    const lineNumber = index + 1;
    const trimmed = rawLine.trim();

    if (trimmed === "" || trimmed.startsWith("#")) {
      continue;
    }

    const parts = rawLine.split("\t");
    if (parts.length !== 2) {
      throw new Error(`Invalid config line ${lineNumber}: expected "<directory>\\t<remote-origin-url>"`);
    }

    const [directory, url] = parts;
    validateConfigDirectory(directory, lineNumber);
    if (!url) {
      throw new Error(`Invalid config line ${lineNumber}: missing remote-origin-url`);
    }

    if (seenDirectories.has(directory)) {
      throw new Error(`Invalid config line ${lineNumber}: duplicate directory "${directory}"`);
    }
    seenDirectories.add(directory);
    entries.push({ directory, url });
  }

  return entries;
}

function validateConfigDirectory(directory, lineNumber) {
  if (!directory) {
    throw new Error(`Invalid config line ${lineNumber}: missing directory`);
  }

  if (path.isAbsolute(directory)) {
    throw new Error(`Invalid config line ${lineNumber}: directory must be relative`);
  }

  if (directory.includes("/") || directory.includes("\\") || directory.includes("..")) {
    throw new Error(`Invalid config line ${lineNumber}: invalid directory "${directory}"`);
  }
}

function reconcileRepo(entry, reposRoot, repoPrefix) {
  const repoPath = path.join(reposRoot, entry.directory);

  if (!fs.existsSync(repoPath)) {
    cloneRepo(entry.url, repoPath, repoPrefix);
    return { state: "cloned", repoPath };
  }

  const stat = fs.statSync(repoPath);
  if (!stat.isDirectory()) {
    return { state: "skipped", reason: `target exists and is not a directory: ${repoPath}` };
  }

  if (isDirectoryEmpty(repoPath)) {
    cloneRepo(entry.url, repoPath, repoPrefix);
    return { state: "cloned", repoPath };
  }

  if (!isStandaloneGitRepo(repoPath)) {
    return { state: "skipped", reason: "target exists but is not a standalone git repo" };
  }

  const origin = getGitOrigin(repoPath);
  if (!origin) {
    return { state: "skipped", reason: "target repo has no remote.origin.url" };
  }

  if (origin !== entry.url) {
    return { state: "skipped", reason: `origin mismatch (${origin})` };
  }

  return { state: "exists", repoPath };
}

function cloneRepo(url, targetPath, repoPrefix) {
  fs.mkdirSync(path.dirname(targetPath), { recursive: true });
  runCommand("git", ["clone", url, targetPath], { cwd: path.dirname(targetPath), label: `${repoPrefix} clone` });
}

function isDirectoryEmpty(directoryPath) {
  return fs.readdirSync(directoryPath).length === 0;
}

function isStandaloneGitRepo(repoPath) {
  const result = spawnSync("git", ["-C", repoPath, "rev-parse", "--show-toplevel"], {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
  });

  if (result.status !== 0) {
    return false;
  }

  try {
    const actualTopLevel = fs.realpathSync(result.stdout.trim());
    const expectedTopLevel = fs.realpathSync(repoPath);
    return actualTopLevel === expectedTopLevel;
  } catch {
    return false;
  }
}

function getGitOrigin(repoPath) {
  const result = spawnSync("git", ["-C", repoPath, "config", "--get", "remote.origin.url"], {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
  });

  if (result.status !== 0) {
    return "";
  }

  return result.stdout.trim();
}

function runInstallSetup(repoPath, repoPrefix) {
  let ranAnyInstall = false;
  let warningCount = 0;
  const nodePlan = detectNodeInstallPlan(repoPath);
  const pythonPlan = detectPythonInstallPlan(repoPath);

  if (nodePlan.warnings.length > 0) {
    for (const warning of nodePlan.warnings) {
      warningCount += 1;
      console.warn(`${repoPrefix} warning: ${warning}`);
    }
  }

  if (nodePlan.manager) {
    if (!commandExists(nodePlan.manager)) {
      throw new Error(`missing package manager executable: ${nodePlan.manager}`);
    }

    runCommand(nodePlan.manager, [...SUPPORTED_PACKAGE_MANAGERS.get(nodePlan.manager).install], {
      cwd: repoPath,
      label: `${repoPrefix} install (${nodePlan.manager})`,
    });
    ranAnyInstall = true;
  }

  if (pythonPlan.type === "pyproject" || pythonPlan.type === "requirements") {
    setupPythonRepo(repoPath, pythonPlan, repoPrefix);
    ranAnyInstall = true;
  }

  if (ranAnyInstall) {
    console.log(`${repoPrefix} install ok`);
  } else {
    console.log(`${repoPrefix} install skipped (no supported setup files)`);
  }

  return { ranAnyInstall, warningCount };
}

function detectNodeInstallPlan(repoPath) {
  const packageJsonPath = path.join(repoPath, "package.json");
  const warnings = [];

  if (!fs.existsSync(packageJsonPath)) {
    return { manager: null, warnings, packageJson: null };
  }

  const packageJson = JSON.parse(fs.readFileSync(packageJsonPath, "utf8"));
  const packageManagerField = typeof packageJson.packageManager === "string" ? packageJson.packageManager : "";
  const declaredManager = packageManagerField.split("@")[0] || "";
  const lockfileManagers = [];

  if (fs.existsSync(path.join(repoPath, "pnpm-lock.yaml"))) {
    lockfileManagers.push("pnpm");
  }
  if (fs.existsSync(path.join(repoPath, "package-lock.json"))) {
    lockfileManagers.push("npm");
  }
  if (fs.existsSync(path.join(repoPath, "yarn.lock"))) {
    lockfileManagers.push("yarn");
  }
  if (fs.existsSync(path.join(repoPath, "bun.lock")) || fs.existsSync(path.join(repoPath, "bun.lockb"))) {
    lockfileManagers.push("bun");
  }

  let manager = null;
  if (SUPPORTED_PACKAGE_MANAGERS.has(declaredManager)) {
    manager = declaredManager;
    if (lockfileManagers.length > 0 && !lockfileManagers.includes(declaredManager)) {
      warnings.push(
        `packageManager declares ${declaredManager} but lockfiles suggest ${lockfileManagers.join(", ")}`,
      );
    }
  } else if (lockfileManagers.length > 0) {
    manager = lockfileManagers[0];
    if (lockfileManagers.length > 1) {
      warnings.push(`multiple lockfiles detected (${lockfileManagers.join(", ")}), using ${manager}`);
    }
  } else {
    manager = "npm";
  }

  return { manager, warnings, packageJson };
}

function detectPythonInstallPlan(repoPath) {
  const pyprojectPath = path.join(repoPath, "pyproject.toml");
  const requirementsPath = path.join(repoPath, "requirements.txt");

  if (fs.existsSync(pyprojectPath)) {
    const pyprojectContents = fs.readFileSync(pyprojectPath, "utf8");
    return {
      type: "pyproject",
      hasDevDependencies: pyprojectHasDevOptionalDependencies(pyprojectContents),
    };
  }

  if (fs.existsSync(requirementsPath)) {
    return { type: "requirements" };
  }

  return { type: null };
}

function pyprojectHasDevOptionalDependencies(contents) {
  const lines = contents.split(/\r?\n/);
  let insideOptionalDependencies = false;

  for (const line of lines) {
    const trimmed = line.trim();
    if (trimmed.startsWith("[") && trimmed.endsWith("]")) {
      insideOptionalDependencies = trimmed === "[project.optional-dependencies]";
      continue;
    }

    if (insideOptionalDependencies && /^\s*dev\s*=\s*\[/.test(line)) {
      return true;
    }
  }

  return false;
}

function setupPythonRepo(repoPath, pythonPlan, repoPrefix) {
  const venvPath = path.join(repoPath, ".venv");
  const venvPython = path.join(venvPath, "bin", "python");
  const venvPip = path.join(venvPath, "bin", "pip");

  if (!fs.existsSync(venvPython) || !fs.existsSync(venvPip)) {
    runCommand("python3", ["-m", "venv", ".venv"], {
      cwd: repoPath,
      label: `${repoPrefix} create venv`,
    });
  }

  runCommand(venvPython, ["-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"], {
    cwd: repoPath,
    label: `${repoPrefix} python upgrade tooling`,
  });

  if (pythonPlan.type === "pyproject") {
    const installTarget = pythonPlan.hasDevDependencies ? ".[dev]" : ".";
    runCommand(venvPip, ["install", "-e", installTarget], {
      cwd: repoPath,
      label: `${repoPrefix} python install`,
    });
    return;
  }

  runCommand(venvPip, ["install", "-r", "requirements.txt"], {
    cwd: repoPath,
    label: `${repoPrefix} python install`,
  });
}

function runBuild(repoPath, repoPrefix) {
  const nodePlan = detectNodeInstallPlan(repoPath);
  if (!nodePlan.packageJson || !nodePlan.packageJson.scripts || !nodePlan.packageJson.scripts.build) {
    console.log(`${repoPrefix} build skipped (no build step)`);
    return { ran: false };
  }

  if (!commandExists(nodePlan.manager)) {
    throw new Error(`missing package manager executable: ${nodePlan.manager}`);
  }

  runCommand(nodePlan.manager, ["run", "build"], {
    cwd: repoPath,
    label: `${repoPrefix} build`,
  });
  console.log(`${repoPrefix} build ok`);
  return { ran: true };
}

function runCheck(repoPath, repoPrefix) {
  const nodePlan = detectNodeInstallPlan(repoPath);
  const packageJson = nodePlan.packageJson;
  const scripts = packageJson?.scripts ?? {};
  const scriptName = ["verify", "check", "typecheck", "test"].find((candidate) => Boolean(scripts[candidate]));

  if (!scriptName) {
    console.log(`${repoPrefix} check skipped (no verify/check/typecheck/test step)`);
    return { ran: false };
  }

  if (!commandExists(nodePlan.manager)) {
    throw new Error(`missing package manager executable: ${nodePlan.manager}`);
  }

  runCommand(nodePlan.manager, ["run", scriptName], {
    cwd: repoPath,
    label: `${repoPrefix} check (${scriptName})`,
  });
  console.log(`${repoPrefix} check ok (${scriptName})`);
  return { ran: true };
}

function commandExists(commandName) {
  const result = spawnSync(commandName, ["--version"], {
    stdio: ["ignore", "ignore", "ignore"],
  });
  return !(result.error && result.error.code === "ENOENT");
}

function runCommand(commandName, args, options) {
  const result = spawnSync(commandName, args, {
    cwd: options.cwd,
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
