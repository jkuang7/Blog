import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

const DEV_ROOT = "/Users/jian/Dev";
const REPOS_BOOTSTRAP = path.join(DEV_ROOT, "workspace", "scripts", "repos-bootstrap.mjs");

function makeTempDir(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

function writeFile(filePath, contents) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, contents);
}

function runScript(args, options = {}) {
  return spawnSync(process.execPath, [REPOS_BOOTSTRAP, ...args], {
    cwd: options.cwd ?? DEV_ROOT,
    env: options.env ?? process.env,
    encoding: "utf8",
    maxBuffer: 1024 * 1024 * 128,
  });
}

function assertSuccess(result) {
  assert.equal(result.status, 0, `STDOUT:\n${result.stdout}\nSTDERR:\n${result.stderr}`);
}

function assertFailure(result, pattern) {
  assert.notEqual(result.status, 0, `Expected failure but succeeded:\n${result.stdout}\n${result.stderr}`);
  if (pattern) {
    assert.match(`${result.stdout}\n${result.stderr}`, pattern);
  }
}

function git(args, cwd) {
  execFileSync("git", args, {
    cwd,
    stdio: ["ignore", "pipe", "pipe"],
    encoding: "utf8",
    maxBuffer: 1024 * 1024 * 64,
  });
}

function createBareOrigin(tempRoot, name, files) {
  const seedRoot = path.join(tempRoot, "seed", name);
  const originPath = path.join(tempRoot, "origins", `${name}.git`);
  fs.mkdirSync(seedRoot, { recursive: true });

  for (const [relativePath, contents] of Object.entries(files)) {
    writeFile(path.join(seedRoot, relativePath), contents);
  }

  git(["init"], seedRoot);
  git(["config", "user.name", "Test User"], seedRoot);
  git(["config", "user.email", "test@example.com"], seedRoot);
  git(["add", "."], seedRoot);
  git(["commit", "-m", "initial"], seedRoot);
  git(["branch", "-M", "main"], seedRoot);
  git(["init", "--bare", originPath], tempRoot);
  git(["remote", "add", "origin", originPath], seedRoot);
  git(["push", "--set-upstream", "origin", "main"], seedRoot);

  return originPath;
}

function createDevLayout(tempRoot) {
  const devRoot = path.join(tempRoot, "Dev");
  const reposRoot = path.join(devRoot, "Repos");
  const configPath = path.join(devRoot, "repos.txt");
  fs.mkdirSync(reposRoot, { recursive: true });
  return { devRoot, reposRoot, configPath };
}

function createFreshCloneLayout(tempRoot) {
  const devRoot = path.join(tempRoot, "Dev");
  const reposRoot = path.join(devRoot, "Repos");
  const configPath = path.join(devRoot, "repos.txt");
  fs.mkdirSync(devRoot, { recursive: true });
  return { devRoot, reposRoot, configPath };
}

function codexSnapshotRoot(devRoot) {
  return path.join(devRoot, ".codex");
}

function createHomeEnv(tempRoot, extraEnv = {}) {
  const homeDir = path.join(tempRoot, "home");
  fs.mkdirSync(homeDir, { recursive: true });
  return {
    homeDir,
    env: {
      ...process.env,
      ...extraEnv,
      HOME: homeDir,
    },
  };
}

function createFakeTooling(tempRoot) {
  const binDir = path.join(tempRoot, "fake-bin");
  const logPath = path.join(tempRoot, "tool.log");
  fs.mkdirSync(binDir, { recursive: true });

  const shellLogger = `#!/bin/sh
set -eu
LOG="\${TEST_BOOTSTRAP_LOG:?}"
printf '%s|%s|%s\\n' "$(basename "$0")" "$(pwd)" "$*" >> "$LOG"
exit 0
`;

  for (const tool of ["npm", "pnpm", "yarn", "bun"]) {
    writeFile(path.join(binDir, tool), shellLogger);
    fs.chmodSync(path.join(binDir, tool), 0o755);
  }

  writeFile(
    path.join(binDir, "python3"),
    `#!/bin/sh
set -eu
LOG="\${TEST_BOOTSTRAP_LOG:?}"
printf 'python3|%s|%s\\n' "$(pwd)" "$*" >> "$LOG"
if [ "$#" -ge 3 ] && [ "$1" = "-m" ] && [ "$2" = "venv" ]; then
  target="$3"
  mkdir -p "$target/bin"
  cat > "$target/bin/python" <<'EOF'
#!/bin/sh
set -eu
LOG="\${TEST_BOOTSTRAP_LOG:?}"
printf 'venv-python|%s|%s\\n' "$(pwd)" "$*" >> "$LOG"
exit 0
EOF
  cat > "$target/bin/pip" <<'EOF'
#!/bin/sh
set -eu
LOG="\${TEST_BOOTSTRAP_LOG:?}"
printf 'venv-pip|%s|%s\\n' "$(pwd)" "$*" >> "$LOG"
exit 0
EOF
  chmod +x "$target/bin/python" "$target/bin/pip"
fi
exit 0
`,
  );
  fs.chmodSync(path.join(binDir, "python3"), 0o755);

  return {
    logPath,
    env: {
      ...process.env,
      PATH: `${binDir}:${process.env.PATH}`,
      TEST_BOOTSTRAP_LOG: logPath,
    },
  };
}

function readLog(logPath) {
  if (!fs.existsSync(logPath)) {
    return [];
  }
  return fs.readFileSync(logPath, "utf8").trim().split("\n").filter(Boolean);
}

test("generate writes sorted dir-tab-url entries and skips missing origin/non-standalone repos", () => {
  const tempRoot = makeTempDir("repos-bootstrap-generate-");
  const { devRoot, reposRoot, configPath } = createDevLayout(tempRoot);

  try {
    const blogOrigin = createBareOrigin(tempRoot, "Blog", {
      "package.json": JSON.stringify({ name: "blog" }, null, 2),
    });
    const banksyOrigin = createBareOrigin(tempRoot, "Banksy", {
      "requirements.txt": "requests\n",
    });
    const stonksOrigin = createBareOrigin(tempRoot, "stonks-web", {
      "package.json": JSON.stringify({ name: "stonks-web" }, null, 2),
    });

    git(["clone", blogOrigin, path.join(reposRoot, "Blog")], tempRoot);
    git(["clone", banksyOrigin, path.join(reposRoot, "Banksy")], tempRoot);
    git(["clone", stonksOrigin, path.join(reposRoot, "stonks-web-t3")], tempRoot);

    fs.mkdirSync(path.join(reposRoot, ".memory"), { recursive: true });
    fs.mkdirSync(path.join(reposRoot, "Repos"), { recursive: true });
    git(["init"], devRoot);
    git(["config", "user.name", "Test User"], devRoot);
    git(["config", "user.email", "test@example.com"], devRoot);
    writeFile(path.join(devRoot, "README.md"), "root");
    git(["add", "."], devRoot);
    git(["commit", "-m", "root"], devRoot);

    fs.mkdirSync(path.join(reposRoot, "paycheck"), { recursive: true });
    git(["init"], path.join(reposRoot, "paycheck"));
    git(["config", "user.name", "Test User"], path.join(reposRoot, "paycheck"));
    git(["config", "user.email", "test@example.com"], path.join(reposRoot, "paycheck"));
    writeFile(path.join(reposRoot, "paycheck", "README.md"), "paycheck");
    git(["add", "."], path.join(reposRoot, "paycheck"));
    git(["commit", "-m", "init"], path.join(reposRoot, "paycheck"));

    writeFile(configPath, "old-config\n");

    const result = runScript(["generate", "--dev-root", devRoot]);
    assertSuccess(result);

    const config = fs.readFileSync(configPath, "utf8");
    assert.match(config, /Banksy\t.*origins\/Banksy\.git/);
    assert.match(config, /Blog\t.*origins\/Blog\.git/);
    assert.match(config, /stonks-web-t3\t.*origins\/stonks-web\.git/);
    assert.doesNotMatch(config, /paycheck/);
    assert.match(result.stderr, /missing remote\.origin\.url/);

    const lines = config
      .split(/\r?\n/)
      .filter((line) => line !== "" && !line.startsWith("#"));
    assert.deepEqual(lines.map((line) => line.split("\t")[0]), ["Banksy", "Blog", "stonks-web-t3"]);
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
});

test("generate leaves existing config untouched when zero valid repos are found", () => {
  const tempRoot = makeTempDir("repos-bootstrap-generate-empty-");
  const { devRoot, reposRoot, configPath } = createDevLayout(tempRoot);

  try {
    writeFile(configPath, "keep-me\n");
    fs.mkdirSync(path.join(reposRoot, ".memory"), { recursive: true });

    const result = runScript(["generate", "--dev-root", devRoot]);
    assertFailure(result, /No valid managed repos found/);
    assert.equal(fs.readFileSync(configPath, "utf8"), "keep-me\n");
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
});

test("generate mirrors managed .codex entries into the tracked codex snapshot", () => {
  const tempRoot = makeTempDir("repos-bootstrap-codex-generate-");
  const { devRoot, reposRoot, configPath } = createDevLayout(tempRoot);
  const home = createHomeEnv(tempRoot);

  try {
    const blogOrigin = createBareOrigin(tempRoot, "Blog", {
      "README.md": "blog\n",
    });
    git(["clone", blogOrigin, path.join(reposRoot, "Blog")], tempRoot);

    const codexRoot = path.join(home.homeDir, ".codex");
    writeFile(path.join(codexRoot, "bin", "launch-playwright-chrome.sh"), "#!/bin/sh\n");
    writeFile(path.join(codexRoot, "config.toml"), "model = 'gpt-5.4'\n");
    writeFile(path.join(codexRoot, "commands", "commit.md"), "# commit\n");
    writeFile(path.join(codexRoot, "docs", "agent-harness.md"), "# harness\n");
    writeFile(path.join(codexRoot, "skills", "kanban", "SKILL.md"), "# kanban\n");
    writeFile(path.join(codexRoot, "plugins", "local", "plugin.json"), "{}\n");
    writeFile(path.join(codexRoot, "rules", "context7.md"), "# rules\n");
    writeFile(path.join(codexRoot, "config", "settings.local.json"), "{}\n");
    writeFile(path.join(codexRoot, "prompts", "run.md"), "prompt\n");
    writeFile(path.join(codexRoot, "session-tags.json"), "{\"tags\":[]}\n");
    writeFile(path.join(codexRoot, "AGENTS.md"), "# local\n");
    writeFile(path.join(codexRoot, "logs", "runner.log"), "skip me\n");

    const snapshotRoot = codexSnapshotRoot(devRoot);
    writeFile(path.join(snapshotRoot, "stale.txt"), "remove me\n");

    const result = runScript(["generate", "--dev-root", devRoot], { env: home.env });
    assertSuccess(result);

    assert.equal(fs.readFileSync(path.join(snapshotRoot, "bin", "launch-playwright-chrome.sh"), "utf8"), "#!/bin/sh\n");
    assert.equal(fs.readFileSync(path.join(snapshotRoot, "config.toml"), "utf8"), "model = 'gpt-5.4'\n");
    assert.equal(fs.readFileSync(path.join(snapshotRoot, "commands", "commit.md"), "utf8"), "# commit\n");
    assert.equal(fs.readFileSync(path.join(snapshotRoot, "docs", "agent-harness.md"), "utf8"), "# harness\n");
    assert.equal(fs.readFileSync(path.join(snapshotRoot, "skills", "kanban", "SKILL.md"), "utf8"), "# kanban\n");
    assert.equal(fs.readFileSync(path.join(snapshotRoot, "rules", "context7.md"), "utf8"), "# rules\n");
    assert.equal(fs.existsSync(path.join(snapshotRoot, "config")), false);
    assert.equal(fs.existsSync(path.join(snapshotRoot, "logs")), false);
    assert.equal(fs.existsSync(path.join(snapshotRoot, "plugins")), false);
    assert.equal(fs.existsSync(path.join(snapshotRoot, "prompts")), false);
    assert.equal(fs.existsSync(path.join(snapshotRoot, "session-tags.json")), false);
    assert.equal(fs.existsSync(path.join(snapshotRoot, "AGENTS.md")), false);
    assert.equal(fs.existsSync(path.join(snapshotRoot, "stale.txt")), false);

    const manifest = JSON.parse(fs.readFileSync(path.join(snapshotRoot, "manifest.json"), "utf8"));
    assert.deepEqual(manifest.entries, ["bin", "commands", "config.toml", "docs", "rules", "skills"]);
    assert.match(result.stdout, /codex snapshot entries: 6/);
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
});

test("bootstrap clones missing repos and runs npm and pnpm installs", () => {
  const tempRoot = makeTempDir("repos-bootstrap-node-");
  const { devRoot, reposRoot, configPath } = createDevLayout(tempRoot);
  const tooling = createFakeTooling(tempRoot);
  const home = createHomeEnv(tempRoot, tooling.env);

  try {
    const blogOrigin = createBareOrigin(tempRoot, "Blog", {
      "package.json": JSON.stringify({ name: "blog", scripts: { build: "echo build", check: "echo check" } }, null, 2),
      "package-lock.json": "{}\n",
    });
    const scaffoldOrigin = createBareOrigin(tempRoot, "stonks-web-scaffold", {
      "package.json": JSON.stringify(
        { name: "scaffold", packageManager: "pnpm@10.14.0", scripts: { verify: "echo verify", build: "echo build" } },
        null,
        2,
      ),
      "pnpm-lock.yaml": "lockfileVersion: '9.0'\n",
    });

    writeFile(
      configPath,
      [
        ...[
          "# Managed repositories - one per line",
          "# Format: <directory>\\t<remote-origin-url>",
          "",
        ],
        `Blog\t${blogOrigin}`,
        `stonks-web-scaffold\t${scaffoldOrigin}`,
        "",
      ].join("\n"),
    );

    const result = runScript(["bootstrap", "--dev-root", devRoot], { env: home.env });
    assertSuccess(result);

    assert.equal(fs.existsSync(path.join(reposRoot, "Blog", "package.json")), true);
    assert.equal(fs.existsSync(path.join(reposRoot, "stonks-web-scaffold", "package.json")), true);

    const pointerPath = path.join(home.homeDir, ".config", "dev-bootstrap", "root");
    assert.equal(fs.readFileSync(pointerPath, "utf8"), `${devRoot}\n`);
    const zshrc = fs.readFileSync(path.join(home.homeDir, ".zshrc"), "utf8");
    assert.match(zshrc, /# >>> dev-bootstrap >>>/);
    assert.match(zshrc, /\.config\/dev-bootstrap\/root/);

    const logLines = readLog(tooling.logPath);
    assert(logLines.some((line) => line.includes("npm|") && line.endsWith("|install")), logLines.join("\n"));
    assert(logLines.some((line) => line.includes("pnpm|") && line.endsWith("|install")), logLines.join("\n"));
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
});

test("bootstrap creates the Repos root for a fresh clone layout", () => {
  const tempRoot = makeTempDir("repos-bootstrap-fresh-clone-");
  const { devRoot, reposRoot, configPath } = createFreshCloneLayout(tempRoot);
  const tooling = createFakeTooling(tempRoot);
  const home = createHomeEnv(tempRoot, tooling.env);

  try {
    const blogOrigin = createBareOrigin(tempRoot, "Blog", {
      "package.json": JSON.stringify({ name: "blog" }, null, 2),
      "package-lock.json": "{}\n",
    });

    writeFile(configPath, `Blog\t${blogOrigin}\n`);

    const result = runScript(["bootstrap", "--dev-root", devRoot], { env: home.env });
    assertSuccess(result);

    assert.equal(fs.existsSync(reposRoot), true);
    assert.equal(fs.existsSync(path.join(reposRoot, "Blog", ".git")), true);
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
});

test("bootstrap mirrors the tracked .codex snapshot back into HOME", () => {
  const tempRoot = makeTempDir("repos-bootstrap-codex-bootstrap-");
  const { devRoot, configPath } = createDevLayout(tempRoot);
  const home = createHomeEnv(tempRoot);

  try {
    const blogOrigin = createBareOrigin(tempRoot, "Blog", {
      "README.md": "blog\n",
    });
    writeFile(configPath, `Blog\t${blogOrigin}\n`);

    const snapshotRoot = codexSnapshotRoot(devRoot);
    writeFile(path.join(snapshotRoot, "config.toml"), "model = 'gpt-5.4-mini'\n");
    writeFile(path.join(snapshotRoot, "skills", "kanban", "SKILL.md"), "# pulled\n");
    writeFile(
      path.join(snapshotRoot, "manifest.json"),
      JSON.stringify(
        {
          version: 1,
          entries: ["config.toml", "skills"],
        },
        null,
        2,
      ) + "\n",
    );

    const codexRoot = path.join(home.homeDir, ".codex");
    writeFile(path.join(codexRoot, "config.toml"), "model = 'stale'\n");
    writeFile(path.join(codexRoot, "commands", "old.md"), "# stale managed entry\n");
    writeFile(path.join(codexRoot, "logs", "runner.log"), "keep unmanaged runtime\n");

    const result = runScript(["bootstrap", "--dev-root", devRoot], { env: home.env });
    assertSuccess(result);

    assert.equal(fs.readFileSync(path.join(codexRoot, "config.toml"), "utf8"), "model = 'gpt-5.4-mini'\n");
    assert.equal(fs.readFileSync(path.join(codexRoot, "skills", "kanban", "SKILL.md"), "utf8"), "# pulled\n");
    assert.equal(fs.existsSync(path.join(codexRoot, "commands")), false);
    assert.equal(fs.readFileSync(path.join(codexRoot, "logs", "runner.log"), "utf8"), "keep unmanaged runtime\n");
    assert.match(result.stdout, /Codex snapshot restored: 2 entries/);
    assert.match(result.stdout, /codex restored: 2/);
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
});

test("bootstrap tolerates manifest entries whose snapshot paths are missing", () => {
  const tempRoot = makeTempDir("repos-bootstrap-codex-missing-entry-");
  const { devRoot, configPath } = createDevLayout(tempRoot);
  const home = createHomeEnv(tempRoot);

  try {
    const blogOrigin = createBareOrigin(tempRoot, "Blog", {
      "README.md": "blog\n",
    });
    writeFile(configPath, `Blog\t${blogOrigin}\n`);

    const snapshotRoot = codexSnapshotRoot(devRoot);
    writeFile(path.join(snapshotRoot, "config.toml"), "model = 'gpt-5.4-mini'\n");
    writeFile(
      path.join(snapshotRoot, "manifest.json"),
      JSON.stringify(
        {
          version: 1,
          entries: ["config.toml", "skills"],
        },
        null,
        2,
      ) + "\n",
    );

    const codexRoot = path.join(home.homeDir, ".codex");
    writeFile(path.join(codexRoot, "config.toml"), "model = 'stale'\n");
    writeFile(path.join(codexRoot, "skills", "kanban", "SKILL.md"), "# stale skill\n");

    const result = runScript(["bootstrap", "--dev-root", devRoot], { env: home.env });
    assertSuccess(result);

    assert.equal(fs.readFileSync(path.join(codexRoot, "config.toml"), "utf8"), "model = 'gpt-5.4-mini'\n");
    assert.equal(fs.existsSync(path.join(codexRoot, "skills")), false);
    assert.match(result.stderr, /Codex snapshot skipped 1 missing managed entries from manifest/);
    assert.match(result.stdout, /Codex snapshot restored: 1 entries/);
    assert.match(result.stdout, /codex restored: 1/);
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
});

test("bootstrap prunes unmanaged repos to match the pushed config", () => {
  const tempRoot = makeTempDir("repos-bootstrap-prune-");
  const { devRoot, reposRoot, configPath } = createDevLayout(tempRoot);
  const tooling = createFakeTooling(tempRoot);
  const home = createHomeEnv(tempRoot, tooling.env);

  try {
    const trackedOrigin = createBareOrigin(tempRoot, "Banksy", {
      "package.json": JSON.stringify({ name: "banksy" }, null, 2),
      "package-lock.json": "{}\n",
    });
    const staleOrigin = createBareOrigin(tempRoot, "stale-repo", {
      "package.json": JSON.stringify({ name: "stale" }, null, 2),
      "package-lock.json": "{}\n",
    });

    git(["clone", trackedOrigin, path.join(reposRoot, "Banksy")], tempRoot);
    git(["clone", staleOrigin, path.join(reposRoot, "stale-repo")], tempRoot);
    fs.mkdirSync(path.join(reposRoot, ".memory"), { recursive: true });

    writeFile(
      configPath,
      [
        "# Managed repositories - one per line",
        "# Format: <directory>\\t<remote-origin-url>",
        "",
        `Banksy\t${trackedOrigin}`,
        "",
      ].join("\n"),
    );

    const result = runScript(["bootstrap", "--dev-root", devRoot], { env: home.env });
    assertSuccess(result);

    assert.equal(fs.existsSync(path.join(reposRoot, "Banksy", ".git")), true);
    assert.equal(fs.existsSync(path.join(reposRoot, "stale-repo")), false);
    assert.equal(fs.existsSync(path.join(reposRoot, ".memory")), true);
    assert.match(result.stdout, /pruned: 1/);
    assert.match(result.stdout, /\[stale-repo\] pruned \(not tracked in config\)/);
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
});

test("bootstrap creates venvs and installs requirements.txt and pyproject repos", () => {
  const tempRoot = makeTempDir("repos-bootstrap-python-");
  const { devRoot, reposRoot, configPath } = createDevLayout(tempRoot);
  const tooling = createFakeTooling(tempRoot);
  const home = createHomeEnv(tempRoot, tooling.env);

  try {
    const banksyOrigin = createBareOrigin(tempRoot, "Banksy", {
      "requirements.txt": "requests\n",
    });
    const deckOrigin = createBareOrigin(tempRoot, "DeckFoundry", {
      "pyproject.toml": [
        "[project]",
        "name = 'deckfoundry'",
        "version = '0.1.0'",
        "",
        "[project.optional-dependencies]",
        "dev = ['pytest']",
        "",
      ].join("\n"),
    });

    writeFile(
      configPath,
      [
        "# Managed repositories - one per line",
        "# Format: <directory>\\t<remote-origin-url>",
        "",
        `Banksy\t${banksyOrigin}`,
        `DeckFoundry\t${deckOrigin}`,
        "",
      ].join("\n"),
    );

    const result = runScript(["bootstrap", "--dev-root", devRoot], { env: home.env });
    assertSuccess(result);

    const logLines = readLog(tooling.logPath);
    assert(logLines.some((line) => line.includes("python3|") && line.includes("-m venv .venv")), logLines.join("\n"));
    assert(logLines.some((line) => line.includes("venv-pip|") && line.endsWith("|install -r requirements.txt")), logLines.join("\n"));
    assert(logLines.some((line) => line.includes("venv-pip|") && line.endsWith("|install -e .[dev]")), logLines.join("\n"));
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
});

test("bootstrap sets up existing matching repos and skips origin mismatch and non-repo collisions", () => {
  const tempRoot = makeTempDir("repos-bootstrap-existing-");
  const { devRoot, reposRoot, configPath } = createDevLayout(tempRoot);
  const tooling = createFakeTooling(tempRoot);
  const home = createHomeEnv(tempRoot, tooling.env);

  try {
    const goodOrigin = createBareOrigin(tempRoot, "Blog", {
      "package.json": JSON.stringify({ name: "blog", scripts: { check: "echo check" } }, null, 2),
      "package-lock.json": "{}\n",
    });
    const wrongOrigin = createBareOrigin(tempRoot, "DeckFoundry", {
      "package.json": JSON.stringify({ name: "deck" }, null, 2),
      "package-lock.json": "{}\n",
    });
    const expectedOrigin = createBareOrigin(tempRoot, "ExpectedDeck", {
      "package.json": JSON.stringify({ name: "expected-deck" }, null, 2),
      "package-lock.json": "{}\n",
    });

    git(["clone", goodOrigin, path.join(reposRoot, "Blog")], tempRoot);
    git(["clone", wrongOrigin, path.join(reposRoot, "DeckFoundry")], tempRoot);
    fs.mkdirSync(path.join(reposRoot, "Collision"), { recursive: true });
    writeFile(path.join(reposRoot, "Collision", "README.md"), "not a repo");

    writeFile(
      configPath,
      [
        "# Managed repositories - one per line",
        "# Format: <directory>\\t<remote-origin-url>",
        "",
        `Blog\t${goodOrigin}`,
        `DeckFoundry\t${expectedOrigin}`,
        `Collision\t${goodOrigin}`,
        "",
      ].join("\n"),
    );

    const result = runScript(["bootstrap", "--dev-root", devRoot], { env: home.env });
    assertSuccess(result);

    const logLines = readLog(tooling.logPath);
    assert(logLines.some((line) => line.includes(`${path.join(reposRoot, "Blog")}`) && line.endsWith("|install")), logLines.join("\n"));
    assert.match(`${result.stdout}\n${result.stderr}`, /origin mismatch/);
    assert.match(`${result.stdout}\n${result.stderr}`, /not a standalone git repo/);
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
});

test("bootstrap --with-build and --with-check choose the expected scripts", () => {
  const tempRoot = makeTempDir("repos-bootstrap-build-check-");
  const { devRoot, configPath } = createDevLayout(tempRoot);
  const tooling = createFakeTooling(tempRoot);
  const home = createHomeEnv(tempRoot, tooling.env);

  try {
    const verifyOrigin = createBareOrigin(tempRoot, "verify-repo", {
      "package.json": JSON.stringify(
        { name: "verify-repo", scripts: { verify: "echo verify", build: "echo build" } },
        null,
        2,
      ),
      "package-lock.json": "{}\n",
    });
    const checkOrigin = createBareOrigin(tempRoot, "check-repo", {
      "package.json": JSON.stringify({ name: "check-repo", scripts: { check: "echo check" } }, null, 2),
      "package-lock.json": "{}\n",
    });
    const typecheckOrigin = createBareOrigin(tempRoot, "type-repo", {
      "package.json": JSON.stringify({ name: "type-repo", scripts: { typecheck: "echo type" } }, null, 2),
      "package-lock.json": "{}\n",
    });
    const testOrigin = createBareOrigin(tempRoot, "test-repo", {
      "package.json": JSON.stringify({ name: "test-repo", scripts: { test: "echo test" } }, null, 2),
      "package-lock.json": "{}\n",
    });
    const noBuildOrigin = createBareOrigin(tempRoot, "nobuild-repo", {
      "package.json": JSON.stringify({ name: "nobuild-repo", scripts: { check: "echo check" } }, null, 2),
      "package-lock.json": "{}\n",
    });

    writeFile(
      configPath,
      [
        "# Managed repositories - one per line",
        "# Format: <directory>\\t<remote-origin-url>",
        "",
        `verify-repo\t${verifyOrigin}`,
        `check-repo\t${checkOrigin}`,
        `type-repo\t${typecheckOrigin}`,
        `test-repo\t${testOrigin}`,
        `nobuild-repo\t${noBuildOrigin}`,
        "",
      ].join("\n"),
    );

    const result = runScript(["bootstrap", "--dev-root", devRoot, "--with-build", "--with-check"], {
      env: home.env,
    });
    assertSuccess(result);

    const logLines = readLog(tooling.logPath);
    assert(logLines.some((line) => line.endsWith("|run build")), logLines.join("\n"));
    assert(logLines.some((line) => line.includes("verify-repo") && line.endsWith("|run verify")), logLines.join("\n"));
    assert(logLines.some((line) => line.includes("check-repo") && line.endsWith("|run check")), logLines.join("\n"));
    assert(logLines.some((line) => line.includes("type-repo") && line.endsWith("|run typecheck")), logLines.join("\n"));
    assert(logLines.some((line) => line.includes("test-repo") && line.endsWith("|run test")), logLines.join("\n"));
    assert.match(result.stdout, /build skipped \(no build step\)/);
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
});

test("bootstrap parser fails on malformed and duplicate config lines", () => {
  const tempRoot = makeTempDir("repos-bootstrap-parse-");
  const { devRoot, configPath } = createDevLayout(tempRoot);
  const home = createHomeEnv(tempRoot);

  try {
    writeFile(
      configPath,
      [
        "# Managed repositories - one per line",
        "bad line",
        "Blog\tone",
        "Blog\ttwo",
        "",
      ].join("\n"),
    );

    const malformed = runScript(["bootstrap", "--dev-root", devRoot], { env: home.env });
    assertFailure(malformed, /expected "<directory>\\t<remote-origin-url>\[\\t<branch>\]"/);

    writeFile(
      configPath,
      [
        "# Managed repositories - one per line",
        "Blog\tone",
        "Blog\ttwo",
        "",
      ].join("\n"),
    );

    const duplicate = runScript(["bootstrap", "--dev-root", devRoot], { env: home.env });
    assertFailure(duplicate, /duplicate directory "Blog"/);
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
});

test("bootstrap replaces legacy zshrc hook with managed pointer block", () => {
  const tempRoot = makeTempDir("repos-bootstrap-zshrc-");
  const { devRoot, configPath } = createDevLayout(tempRoot);
  const tooling = createFakeTooling(tempRoot);
  const home = createHomeEnv(tempRoot, tooling.env);

  try {
    writeFile(
      path.join(home.homeDir, ".zshrc"),
      [
        "# Bootstrap - source shared shell config from Dev root",
        "# .custom contains: aliases, functions, automations, and reusable config",
        "# Edit with: ce (opens .custom)  |  Reload with: cs (sources .custom)",
        '_p="$HOME/Dev/.custom"',
        '[[ -r "$_p" ]] && source "$_p" || echo "⚠ .custom not found at $_p"',
        "unset _p",
        "",
        "export PATH=\"$PATH:/tmp/example\"",
        "",
      ].join("\n"),
    );

    const origin = createBareOrigin(tempRoot, "Blog", {
      "package.json": JSON.stringify({ name: "blog" }, null, 2),
      "package-lock.json": "{}\n",
    });
    writeFile(configPath, `Blog\t${origin}\n`);

    const result = runScript(["bootstrap", "--dev-root", devRoot], { env: home.env });
    assertSuccess(result);

    const zshrc = fs.readFileSync(path.join(home.homeDir, ".zshrc"), "utf8");
    assert.doesNotMatch(zshrc, /\$HOME\/Dev\/\.custom/);
    assert.match(zshrc, /# >>> dev-bootstrap >>>/);
    assert.match(zshrc, /Managed by Dev bootstrap/);
    assert.match(zshrc, /export PATH="\$PATH:\/tmp\/example"/);
    assert.equal((zshrc.match(/# >>> dev-bootstrap >>>/g) ?? []).length, 1);
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
});

test("bootstrap fast-forwards clean repos on main and skips dirty repos", () => {
  const tempRoot = makeTempDir("repos-bootstrap-sync-");
  const { devRoot, reposRoot, configPath } = createDevLayout(tempRoot);
  const tooling = createFakeTooling(tempRoot);
  const home = createHomeEnv(tempRoot, tooling.env);

  try {
    const cleanOrigin = createBareOrigin(tempRoot, "Blog", {
      "package.json": JSON.stringify({ name: "blog" }, null, 2),
      "package-lock.json": "{}\n",
      "README.md": "v1\n",
    });
    const dirtyOrigin = createBareOrigin(tempRoot, "DeckFoundry", {
      "package.json": JSON.stringify({ name: "deck" }, null, 2),
      "package-lock.json": "{}\n",
      "README.md": "deck-v1\n",
    });

    git(["clone", cleanOrigin, path.join(reposRoot, "Blog")], tempRoot);
    git(["clone", dirtyOrigin, path.join(reposRoot, "DeckFoundry")], tempRoot);

    const cleanSeed = path.join(tempRoot, "clean-seed");
    const dirtySeed = path.join(tempRoot, "dirty-seed");
    git(["clone", cleanOrigin, cleanSeed], tempRoot);
    git(["clone", dirtyOrigin, dirtySeed], tempRoot);
    git(["config", "user.name", "Test User"], cleanSeed);
    git(["config", "user.email", "test@example.com"], cleanSeed);
    git(["config", "user.name", "Test User"], dirtySeed);
    git(["config", "user.email", "test@example.com"], dirtySeed);

    writeFile(path.join(cleanSeed, "README.md"), "v2\n");
    git(["add", "README.md"], cleanSeed);
    git(["commit", "-m", "update clean"], cleanSeed);
    git(["push", "origin", "main"], cleanSeed);

    writeFile(path.join(dirtySeed, "README.md"), "deck-v2\n");
    git(["add", "README.md"], dirtySeed);
    git(["commit", "-m", "update dirty"], dirtySeed);
    git(["push", "origin", "main"], dirtySeed);

    writeFile(path.join(reposRoot, "DeckFoundry", "LOCAL_ONLY.txt"), "keep me\n");

    writeFile(
      configPath,
      [
        `Blog\t${cleanOrigin}`,
        `DeckFoundry\t${dirtyOrigin}`,
        "",
      ].join("\n"),
    );

    const result = runScript(["bootstrap", "--dev-root", devRoot], { env: home.env });
    assertSuccess(result);

    assert.equal(fs.readFileSync(path.join(reposRoot, "Blog", "README.md"), "utf8"), "v2\n");
    assert.equal(fs.readFileSync(path.join(reposRoot, "DeckFoundry", "README.md"), "utf8"), "deck-v1\n");
    assert.match(`${result.stdout}\n${result.stderr}`, /sync ok \(fast-forwarded main\)/);
    assert.match(`${result.stdout}\n${result.stderr}`, /sync skipped \(dirty worktree\)/);
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
});
