import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

const DEV_ROOT = "/Users/jian/Dev";
const CREATE_JIAN_APP = path.join(DEV_ROOT, "scripts", "create-jian-app.mjs");
const BOOTSTRAP_HARNESS = path.join(DEV_ROOT, "scripts", "bootstrap-llm-harness.mjs");

function makeTempDir(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

function runNodeScript(scriptPath, args, options = {}) {
  const result = spawnSync(process.execPath, [scriptPath, ...args], {
    cwd: options.cwd ?? DEV_ROOT,
    encoding: "utf8",
    maxBuffer: 1024 * 1024 * 128,
  });

  if (result.status !== 0) {
    throw new Error(
      `Command failed: node ${scriptPath} ${args.join(" ")}\nSTDOUT:\n${result.stdout}\nSTDERR:\n${result.stderr}`,
    );
  }

  return result;
}

function runInRepo(repoRoot, args) {
  execFileSync(args[0], args.slice(1), {
    cwd: repoRoot,
    stdio: ["ignore", "pipe", "pipe"],
    encoding: "utf8",
    maxBuffer: 1024 * 1024 * 128,
  });
}

test(
  "create-jian-app scaffolds a verified web baseline from create-t3-app",
  { timeout: 600_000 },
  () => {
    const tempRoot = makeTempDir("create-jian-app-live-");
    const repoRoot = path.join(tempRoot, "sample-app");

    try {
      runNodeScript(CREATE_JIAN_APP, [repoRoot]);

      const requiredPaths = [
        "harness.config.json",
        "docs/llm/golden-path.md",
        ".codex/context-pack.md",
        ".codex/context-pack.json",
        ".lint-debt.json",
        "scripts/harness/lint-obsolete.mjs",
        "scripts/harness/lint-obsolete-lib.mjs",
        "scripts/harness/lib.test.mjs",
        "scripts/harness/lint-obsolete-lib.test.mjs",
        "src/app/login/page.tsx",
        "src/app/pricing/page.tsx",
        "src/app/success/page.tsx",
        "src/app/api/stripe/checkout/route.ts",
        "src/app/api/stripe/webhook/route.ts",
        "src/lib/store/app-store.ts",
        "src/lib/stripe.ts",
        "src/server/auth/auth-helpers.test.ts",
        "src/server/rate-limit/limiter.ts",
        "src/server/rate-limit/resolve-ip.test.ts",
        "src/server/billing/checkout.ts",
      ];

      for (const relativePath of requiredPaths) {
        assert.equal(fs.existsSync(path.join(repoRoot, relativePath)), true, relativePath);
      }

      assert.equal(fs.existsSync(path.join(repoRoot, "desktop")), false);
      assert.equal(fs.existsSync(path.join(repoRoot, "src", "server", "analytics")), false);

      const packageJson = JSON.parse(
        fs.readFileSync(path.join(repoRoot, "package.json"), "utf8"),
      );
      assert.equal(packageJson.scripts["lint:harness"], "node scripts/harness/lint-ratchet.mjs");
      assert.equal(packageJson.scripts["lint:obsolete"], "node scripts/harness/lint-obsolete.mjs");
      assert.equal(packageJson.scripts["context:pack"], "node scripts/llm/build-context-pack.mjs");
      assert.match(packageJson.scripts.verify, /pnpm run lint:harness/);
      assert.match(packageJson.scripts.verify, /pnpm run lint:obsolete/);
      assert.equal(packageJson.scripts["test:unit:harness"], "node --test scripts/harness/*.test.mjs");
      assert.equal(packageJson.dependencies.zustand, "^5.0.11");
      assert.equal(packageJson.dependencies["@supabase/ssr"], "^0.9.0");
      assert.equal(packageJson.dependencies["@upstash/ratelimit"], "^2.0.8");
      assert.equal(packageJson.dependencies["@upstash/redis"], "^1.36.2");
      assert.equal(packageJson.dependencies.stripe, "^20.4.1");

      const contextPack = JSON.parse(
        fs.readFileSync(path.join(repoRoot, ".codex", "context-pack.json"), "utf8"),
      );
      assert.equal(contextPack.scaffoldProfile, "web-t3");
      assert.match(contextPack.goldenPathMarkdown, /Supabase owns authentication state/);

      runInRepo(repoRoot, ["pnpm", "run", "lint"]);
      runInRepo(repoRoot, ["pnpm", "run", "context:check"]);
      runInRepo(repoRoot, ["pnpm", "run", "typecheck"]);
      runInRepo(repoRoot, ["pnpm", "run", "test:unit"]);
      runInRepo(repoRoot, ["pnpm", "run", "verify"]);
    } finally {
      fs.rmSync(tempRoot, { recursive: true, force: true });
    }
  },
);

test("deprecated bootstrap forwards to overlay-only mode for existing repos", () => {
  const tempRoot = makeTempDir("bootstrap-harness-");
  const repoRoot = path.join(tempRoot, "existing-repo");

  try {
    fs.mkdirSync(repoRoot, { recursive: true });
    fs.writeFileSync(
      path.join(repoRoot, "package.json"),
      JSON.stringify(
        {
          name: "existing-repo",
          private: true,
          scripts: {
            typecheck: "echo typecheck",
            "test:unit": "echo tests",
          },
        },
        null,
        2,
      ),
    );

    const result = runNodeScript(BOOTSTRAP_HARNESS, [repoRoot, "--skip-install"]);

    assert.match(result.stderr, /deprecated/i);
    assert.equal(fs.existsSync(path.join(repoRoot, "harness.config.json")), true);
    assert.equal(fs.existsSync(path.join(repoRoot, "docs", "llm", "golden-path.md")), true);
    assert.equal(fs.existsSync(path.join(repoRoot, ".codex", "context-pack.json")), true);

    const packageJson = JSON.parse(
      fs.readFileSync(path.join(repoRoot, "package.json"), "utf8"),
    );
    assert.equal(packageJson.scripts["lint:harness"], "node scripts/harness/lint-ratchet.mjs");
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
});
