import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { checkHarness } from "../harness-check.mjs";

function makeRoot() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "dev-harness-check-"));
  write(root, "AGENTS.md", "# Agent\n");
  write(root, "workspace/AGENTS.md", "# Workspace\n");
  write(root, ".gitignore", "*\n");
  write(root, "repos.txt", [
    "# Managed repositories",
    "telecodex\tgit@github.com:jkuang7/telecodex.git\tmaster",
    "tmux-codex\tgit@github.com:jkuang7/tmux-codex.git",
    "",
  ].join("\n"));
  write(root, "package.json", JSON.stringify({
    private: true,
    scripts: {
      bootstrap: "node workspace/scripts/repos-bootstrap.mjs bootstrap",
      pull: "node workspace/scripts/bootstrap-pull.mjs",
      push: "node workspace/scripts/bootstrap-push.mjs",
      "harness:check": "node workspace/scripts/harness-check.mjs",
      verify: "npm run harness:check",
    },
  }, null, 2));
  for (const file of [
    ".codex/docs/agent-harness.md",
    ".codex/commands/_reference_telecodex_linear.md",
    ".codex/commands/add.md",
    ".codex/commands/run.md",
    ".codex/commands/review.md",
  ]) {
    write(root, file, `# ${path.basename(file)}\n\nContext and Reuse Card\n`);
  }
  write(root, ".codex/commands/commit.md", "# commit\n");
  return root;
}

function write(root, relativePath, contents) {
  const filePath = path.join(root, relativePath);
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, contents);
}

test("accepts clean harness shape", () => {
  const root = makeRoot();
  try {
    assert.deepEqual(checkHarness(root), []);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("rejects stale bootstrap and retired helix surfaces", () => {
  const root = makeRoot();
  try {
    write(root, ".codex/commands/helix.md", "# old\n");
    fs.appendFileSync(path.join(root, "repos.txt"), "helix-work\tgit@example.com:old.git\n");
    fs.appendFileSync(path.join(root, "AGENTS.md"), "old .bootstrap path\n");

    const failures = checkHarness(root);
    assert(failures.some((failure) => failure.includes("helix.md")));
    assert(failures.some((failure) => failure.includes("helix-work")));
    assert(failures.some((failure) => failure.includes(".bootstrap")));
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("rejects missing Context and Reuse Card and forbidden runtime paths", () => {
  const root = makeRoot();
  try {
    write(root, ".codex/commands/run.md", "# run\n");
    write(root, ".playwright-mcp/log.txt", "runtime\n");

    const failures = checkHarness(root);
    assert(failures.some((failure) => failure.includes("Context and Reuse Card")));
    assert(failures.some((failure) => failure.includes("forbidden tracked runtime/repo path")));
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("rejects managed repo cleanup regressions", () => {
  const root = makeRoot();
  try {
    fs.mkdirSync(path.join(root, "Repos/telecodex"), { recursive: true });
    fs.mkdirSync(path.join(root, "Repos/tmux-codex/.codex"), { recursive: true });
    write(root, "Repos/tmux-codex/package.json", JSON.stringify({
      private: true,
      scripts: {
        "context:pack": "node scripts/llm/build-context-pack.mjs",
      },
    }, null, 2));
    write(root, "Repos/tmux-codex/scripts/llm/build-context-pack.mjs", "console.log('stale');\n");

    const failures = checkHarness(root);
    assert(failures.some((failure) => failure.includes("tmux-codex") && failure.includes(".codex")));
    assert(failures.some((failure) => failure.includes("context:pack")));
    assert(failures.some((failure) => failure.includes("build-context-pack.mjs")));
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
