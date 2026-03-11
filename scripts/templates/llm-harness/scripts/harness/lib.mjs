import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const TEST_FILE_REGEX = /\.(test|spec)\.(ts|tsx)$/;
const DEFAULT_COMMAND_MAX_BUFFER = 10 * 1024 * 1024;

export function normalizePath(filePath) {
  return filePath.replace(/\\/g, "/").replace(/^\.\//, "");
}

export function ensureDir(dirPath) {
  fs.mkdirSync(dirPath, { recursive: true });
}

export function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

export function writeJson(filePath, value) {
  ensureDir(path.dirname(filePath));
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`);
}

export function runCommand(command, args, options = {}) {
  const output = execFileSync(command, args, {
    cwd: options.cwd ?? process.cwd(),
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
    env: { ...process.env, ...(options.env ?? {}) },
    maxBuffer: options.maxBuffer ?? DEFAULT_COMMAND_MAX_BUFFER,
  });

  return output.trim();
}

export function tryRunCommand(command, args, options = {}) {
  try {
    return {
      ok: true,
      output: runCommand(command, args, options),
    };
  } catch (error) {
    return {
      ok: false,
      output: error.stdout?.toString()?.trim() ?? "",
      error,
    };
  }
}

function resolveBaseRef() {
  const envRef = process.env.HARNESS_BASE_REF;
  const candidates = [envRef, "origin/main", "main"].filter(Boolean);

  for (const ref of candidates) {
    const verify = tryRunCommand("git", ["rev-parse", "--verify", ref]);
    if (!verify.ok) {
      continue;
    }

    const mergeBase = tryRunCommand("git", ["merge-base", ref, "HEAD"]);
    if (mergeBase.ok && mergeBase.output.length > 0) {
      return mergeBase.output;
    }
  }

  return null;
}

export function resolveDiffRange() {
  const mergeBase = resolveBaseRef();
  return mergeBase ? `${mergeBase}...HEAD` : "HEAD";
}

export function readGitDiffForFiles(files, options = {}) {
  const normalizedFiles = files.map((file) => normalizePath(file)).filter(Boolean);
  const mergeBase = resolveBaseRef();
  const range = mergeBase ? `${mergeBase}...HEAD` : "HEAD";

  if (normalizedFiles.length === 0) {
    return {
      ok: true,
      output: "",
      range,
    };
  }

  const baseArgs = [
    "diff",
    `--unified=${options.unified ?? 0}`,
    "--no-color",
  ];

  const diff = mergeBase
    ? tryRunCommand(
        "git",
        [...baseArgs, range, "--", ...normalizedFiles],
        options.commandOptions ?? {},
      )
    : tryRunCommand(
        "git",
        [...baseArgs, "--", ...normalizedFiles],
        options.commandOptions ?? {},
      );

  return {
    ...diff,
    range,
  };
}

export function applyNameStatusChanges(baseFiles, nameStatusOutput) {
  const files = new Set(baseFiles.map((file) => normalizePath(file)).filter(Boolean));

  if (!nameStatusOutput || nameStatusOutput.trim().length === 0) {
    return [...files];
  }

  for (const rawLine of nameStatusOutput.split("\n")) {
    const line = rawLine.trim();
    if (line.length === 0) {
      continue;
    }

    const [statusToken, firstPath, secondPath] = line.split("\t");
    const status = statusToken?.trim() ?? "";

    if (status.startsWith("R")) {
      if (firstPath) {
        files.delete(normalizePath(firstPath));
      }
      if (secondPath) {
        files.add(normalizePath(secondPath));
      }
      continue;
    }

    if (status.startsWith("C")) {
      if (firstPath) {
        files.add(normalizePath(firstPath));
      }
      if (secondPath) {
        files.add(normalizePath(secondPath));
      }
      continue;
    }

    if (status === "D") {
      if (firstPath) {
        files.delete(normalizePath(firstPath));
      }
      continue;
    }

    if (firstPath) {
      files.add(normalizePath(firstPath));
    }
  }

  return [...files];
}

export function getChangedFiles() {
  const stagedDiff = tryRunCommand("git", ["diff", "--name-status", "--find-renames", "--cached"]);
  const worktreeDiff = tryRunCommand("git", ["diff", "--name-status", "--find-renames"]);
  const untrackedFiles = tryRunCommand("git", ["ls-files", "--others", "--exclude-standard"]);

  const hasLocalChanges = (stagedDiff.ok && stagedDiff.output.length > 0) ||
    (worktreeDiff.ok && worktreeDiff.output.length > 0) ||
    (untrackedFiles.ok && untrackedFiles.output.length > 0);

  if (hasLocalChanges) {
    let changedFiles = [];
    if (stagedDiff.ok) {
      changedFiles = applyNameStatusChanges(changedFiles, stagedDiff.output);
    }
    if (worktreeDiff.ok) {
      changedFiles = applyNameStatusChanges(changedFiles, worktreeDiff.output);
    }
    if (untrackedFiles.ok && untrackedFiles.output.length > 0) {
      changedFiles = changedFiles.concat(
        untrackedFiles.output
          .split("\n")
          .map((line) => normalizePath(line.trim()))
          .filter(Boolean),
      );
    }
    return [...new Set(changedFiles)].sort();
  }

  const mergeBase = resolveBaseRef();

  if (mergeBase) {
    const diff = tryRunCommand("git", ["diff", "--name-only", `${mergeBase}...HEAD`]);
    if (diff.ok) {
      return diff.output.length === 0
        ? []
        : diff.output
            .split("\n")
            .map((line) => normalizePath(line.trim()))
            .filter(Boolean);
    }
  }

  const fallback = tryRunCommand("git", ["ls-files"]);
  if (!fallback.ok || fallback.output.length === 0) {
    return [];
  }

  return fallback.output
    .split("\n")
    .map((line) => normalizePath(line.trim()))
    .filter(Boolean);
}

function extractPrefixFromGlob(globPattern) {
  const pattern = normalizePath(globPattern);
  const wildcardIndex = pattern.indexOf("/**");

  if (wildcardIndex >= 0) {
    return pattern.slice(0, wildcardIndex + 1);
  }

  const slashIndex = pattern.lastIndexOf("/");
  return slashIndex >= 0 ? pattern.slice(0, slashIndex + 1) : "";
}

function extractExtensionsFromGlob(globPattern) {
  const pattern = normalizePath(globPattern);
  const braceMatch = pattern.match(/\{([^}]+)\}/);

  if (braceMatch) {
    return braceMatch[1].split(",").map((entry) => entry.trim().replace(/^\./, ""));
  }

  const extMatch = pattern.match(/\.([a-z0-9]+)$/i);
  if (!extMatch) {
    return [];
  }

  return [extMatch[1]];
}

export function matchesSimpleGlob(filePath, globPattern) {
  const file = normalizePath(filePath);
  const pattern = normalizePath(globPattern);
  const prefix = extractPrefixFromGlob(pattern);

  if (prefix && !file.startsWith(prefix)) {
    return false;
  }

  const extensions = extractExtensionsFromGlob(pattern);
  if (extensions.length > 0) {
    const ext = path.extname(file).replace(/^\./, "");
    if (!extensions.includes(ext)) {
      return false;
    }
  }

  if (pattern.includes(".test.") && !file.includes(".test.")) {
    return false;
  }

  if (pattern.includes(".spec.") && !file.includes(".spec.")) {
    return false;
  }

  return true;
}

export function isInDir(filePath, dirPath) {
  const file = normalizePath(filePath);
  const dir = normalizePath(dirPath).replace(/\/+$/, "");

  if (dir.length === 0) {
    return true;
  }

  return file === dir || file.startsWith(`${dir}/`);
}

export function isTestFile(filePath) {
  return TEST_FILE_REGEX.test(normalizePath(filePath));
}

export function isTypeScriptSourceFile(filePath) {
  const file = normalizePath(filePath);
  if (!/\.(ts|tsx)$/.test(file)) {
    return false;
  }

  if (file.endsWith(".d.ts")) {
    return false;
  }

  return !isTestFile(file);
}

export function isLikelyIntegrationTest(filePath) {
  const file = normalizePath(filePath);
  return (
    file.includes("/e2e/") ||
    file.includes("/integration/") ||
    file.includes(".e2e.") ||
    file.includes(".integration.")
  );
}

export function parseHarnessConfig() {
  const configPath = path.resolve(process.cwd(), "harness.config.json");
  if (!fs.existsSync(configPath)) {
    throw new Error(`Missing harness config: ${configPath}`);
  }

  return readJson(configPath);
}

export function resolvePackageForFile(filePath, packages) {
  const file = normalizePath(filePath);

  const sorted = [...packages].sort((left, right) => {
    const leftRoot = normalizePath(left.root ?? ".");
    const rightRoot = normalizePath(right.root ?? ".");
    return rightRoot.length - leftRoot.length;
  });

  for (const pkg of sorted) {
    const root = normalizePath(pkg.root ?? ".");
    if (root === ".") {
      return pkg;
    }

    if (isInDir(file, root)) {
      return pkg;
    }
  }

  return null;
}

export function packageSourceGlobs(pkg) {
  return (pkg.sourceGlobs ?? []).map((glob) => normalizePath(glob));
}

export function fileMatchesAnyGlob(filePath, globs) {
  return globs.some((glob) => matchesSimpleGlob(filePath, glob));
}

export function integrationRootsForPackage(pkg) {
  return (pkg.integrationTestRoots ?? []).map((root) => normalizePath(root));
}

export function colocatedTestCandidates(sourceFilePath) {
  const file = normalizePath(sourceFilePath);
  const ext = path.extname(file);
  const base = file.slice(0, -ext.length);

  return [
    `${base}.test.ts`,
    `${base}.test.tsx`,
    `${base}.spec.ts`,
    `${base}.spec.tsx`,
  ];
}

export function integrationRootForFile(filePath, pkg) {
  const roots = integrationRootsForPackage(pkg);
  return roots.find((root) => isInDir(filePath, root)) ?? null;
}

export function escapeHatchChanged(changedFiles) {
  return changedFiles.includes(".ci/TESTS_EXEMPTION.md");
}

export function summarizeRuleCounts(messages) {
  const counts = new Map();

  for (const message of messages) {
    const rule = message.ruleId ?? "<unknown>";
    counts.set(rule, (counts.get(rule) ?? 0) + 1);
  }

  return [...counts.entries()].sort((left, right) => right[1] - left[1]);
}

export function readFileIfExists(filePath) {
  if (!fs.existsSync(filePath)) {
    return null;
  }

  return fs.readFileSync(filePath, "utf8");
}

export function writeText(filePath, value) {
  ensureDir(path.dirname(filePath));
  fs.writeFileSync(filePath, value);
}
