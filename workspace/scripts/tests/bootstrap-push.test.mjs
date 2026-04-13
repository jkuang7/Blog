import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

const DEV_ROOT = "/Users/jian/Dev";
const BOOTSTRAP_PUSH = path.join(DEV_ROOT, "workspace", "scripts", "bootstrap-push.mjs");

function makeTempDir(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

function writeFile(filePath, contents) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, contents);
}

function git(args, cwd) {
  return execFileSync("git", args, {
    cwd,
    stdio: ["ignore", "pipe", "pipe"],
    encoding: "utf8",
    maxBuffer: 1024 * 1024 * 64,
  });
}

function initDevRepo(tempRoot) {
  const devRoot = path.join(tempRoot, "Dev");
  fs.mkdirSync(path.join(devRoot, "workspace"), { recursive: true });
  fs.mkdirSync(path.join(devRoot, ".bootstrap", "codex"), { recursive: true });
  writeFile(path.join(devRoot, "workspace", "repos.txt"), "old\n");
  writeFile(path.join(devRoot, ".bootstrap", "codex", "manifest.json"), '{"version":1}\n');
  git(["init"], devRoot);
  git(["config", "user.name", "Test User"], devRoot);
  git(["config", "user.email", "test@example.com"], devRoot);
  git(["add", "."], devRoot);
  git(["commit", "-m", "initial"], devRoot);
  git(["branch", "-M", "main"], devRoot);
  return devRoot;
}

function createFakeGenerateScript(tempRoot, body) {
  const scriptPath = path.join(tempRoot, "fake-generate.mjs");
  writeFile(scriptPath, body);
  return scriptPath;
}

function runBootstrapPush(args, options = {}) {
  return spawnSync(process.execPath, [BOOTSTRAP_PUSH, ...args], {
    cwd: options.cwd ?? DEV_ROOT,
    env: options.env ?? process.env,
    encoding: "utf8",
    maxBuffer: 1024 * 1024 * 64,
  });
}

function assertSuccess(result) {
  assert.equal(result.status, 0, `STDOUT:\n${result.stdout}\nSTDERR:\n${result.stderr}`);
}

test("bootstrap push commits snapshot changes on clean main", () => {
  const tempRoot = makeTempDir("bootstrap-push-commit-");

  try {
    const devRoot = initDevRepo(tempRoot);
    const generateScript = createFakeGenerateScript(
      tempRoot,
      `import fs from "node:fs";
import path from "node:path";
const devRoot = ${JSON.stringify(devRoot)};
fs.writeFileSync(path.join(devRoot, "workspace", "repos.txt"), "new\\n");
fs.mkdirSync(path.join(devRoot, ".bootstrap", "codex", "skills", "enhance"), { recursive: true });
fs.writeFileSync(path.join(devRoot, ".bootstrap", "codex", "skills", "enhance", "SKILL.md"), "# updated\\n");
fs.writeFileSync(path.join(devRoot, ".bootstrap", "codex", "manifest.json"), '{"version":2}\\n');
`,
    );

    const result = runBootstrapPush(["--dev-root", devRoot, "--generate-script", generateScript, "--", "generate"]);
    assertSuccess(result);
    assert.match(result.stdout, /\[Dev\] bootstrap push committed 3 path\(s\) on main/);
    assert.equal(git(["rev-list", "--count", "HEAD"], devRoot).trim(), "2");
    assert.equal(git(["status", "--short"], devRoot).trim(), "");
    assert.match(git(["log", "-1", "--pretty=%s"], devRoot), /chore: sync bootstrap snapshot/);
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
});

test("bootstrap push skips auto-commit when worktree is dirty before generate", () => {
  const tempRoot = makeTempDir("bootstrap-push-dirty-");

  try {
    const devRoot = initDevRepo(tempRoot);
    writeFile(path.join(devRoot, "README.md"), "dirty\n");
    const generateScript = createFakeGenerateScript(
      tempRoot,
      `import fs from "node:fs";
fs.writeFileSync(${JSON.stringify(path.join(devRoot, "workspace", "repos.txt"))}, "new\\n");
`,
    );

    const result = runBootstrapPush(["--dev-root", devRoot, "--generate-script", generateScript, "--", "generate"]);
    assertSuccess(result);
    assert.match(result.stderr, /\[Dev\] warning: auto-commit skipped \(dirty worktree before push\)/);
    assert.equal(git(["rev-list", "--count", "HEAD"], devRoot).trim(), "1");
    assert.match(git(["status", "--short"], devRoot), /README\.md/);
    assert.match(git(["status", "--short"], devRoot), /workspace\/repos\.txt/);
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
});

test("bootstrap push skips auto-commit when branch is not main", () => {
  const tempRoot = makeTempDir("bootstrap-push-branch-");

  try {
    const devRoot = initDevRepo(tempRoot);
    git(["checkout", "-b", "feature/bootstrap"], devRoot);
    const generateScript = createFakeGenerateScript(
      tempRoot,
      `import fs from "node:fs";
fs.writeFileSync(${JSON.stringify(path.join(devRoot, "workspace", "repos.txt"))}, "new\\n");
`,
    );

    const result = runBootstrapPush(["--dev-root", devRoot, "--generate-script", generateScript, "--", "generate"]);
    assertSuccess(result);
    assert.match(result.stderr, /\[Dev\] warning: auto-commit skipped \(branch is feature\/bootstrap, expected main\)/);
    assert.equal(git(["rev-list", "--count", "HEAD"], devRoot).trim(), "1");
    assert.match(git(["status", "--short"], devRoot), /workspace\/repos\.txt/);
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
});
