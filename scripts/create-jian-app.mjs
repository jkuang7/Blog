#!/usr/bin/env node
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const HARNESS_TEMPLATE_ROOT = path.join(__dirname, "templates", "llm-harness");
const WEB_TEMPLATE_ROOT = path.join(__dirname, "templates", "create-jian-app", "web-t3");
const DEFAULT_DB_URL = "postgresql://user:pass@localhost:5432/db";
const DEFAULT_SUPABASE_URL = "https://your-project.supabase.co";

const args = parseArgs(process.argv.slice(2));

if (args.help || !args.targetPath) {
  printUsage();
  process.exit(args.help ? 0 : 1);
}

const repoRoot = path.resolve(args.targetPath);

try {
  if (args.overlayOnly) {
    overlayExistingRepo(repoRoot, args);
  } else {
    createNewRepo(repoRoot, args);
  }
} catch (error) {
  const message = error instanceof Error ? error.message : String(error);
  console.error(message);
  process.exit(1);
}

function parseArgs(argv) {
  const result = {
    help: false,
    overlayOnly: false,
    skipInstall: false,
    skipVerify: false,
    skipGitInit: false,
    targetPath: null,
  };

  for (const arg of argv) {
    if (arg === "--help" || arg === "-h") {
      result.help = true;
      continue;
    }

    if (arg === "--overlay-only") {
      result.overlayOnly = true;
      continue;
    }

    if (arg === "--skip-install") {
      result.skipInstall = true;
      result.skipVerify = true;
      continue;
    }

    if (arg === "--skip-verify") {
      result.skipVerify = true;
      continue;
    }

    if (arg === "--skip-git-init") {
      result.skipGitInit = true;
      continue;
    }

    if (!result.targetPath) {
      result.targetPath = arg;
      continue;
    }

    throw new Error(`Unexpected argument: ${arg}`);
  }

  return result;
}

function printUsage() {
  console.log(
    [
      "Usage: node scripts/create-jian-app.mjs <target-path> [options]",
      "",
      "Options:",
      "  --overlay-only    Apply the Jian harness overlay to an existing repo.",
      "  --skip-install    Skip pnpm install and downstream lint/verify steps.",
      "  --skip-verify     Skip pnpm run verify after generation.",
      "  --skip-git-init   Skip git init and hooks-path configuration.",
      "  -h, --help        Show this message.",
    ].join("\n"),
  );
}

function createNewRepo(targetPath, options) {
  ensureTemplateRoots();

  if (fs.existsSync(targetPath)) {
    const entries = fs.readdirSync(targetPath);
    if (entries.length > 0) {
      throw new Error(`Target directory must be empty: ${targetPath}`);
    }
  } else {
    ensureDir(targetPath);
  }

  runCommand(
    "pnpm",
    [
      "dlx",
      "create-t3-app@latest",
      targetPath,
      "--CI",
      "--trpc",
      "--prisma",
      "--tailwind",
      "--appRouter",
      "--dbProvider",
      "postgres",
      "--noGit",
      "--noInstall",
    ],
    { cwd: path.dirname(targetPath) },
  );

  applyWebOverlay(targetPath);
  finalizeRepo(targetPath, options);
}

function overlayExistingRepo(targetPath, options) {
  ensureTemplateRoots();

  const packageJsonPath = path.join(targetPath, "package.json");
  if (!fs.existsSync(packageJsonPath)) {
    throw new Error(`Target repo missing package.json: ${targetPath}`);
  }

  copyRecursive(HARNESS_TEMPLATE_ROOT, targetPath, {
    ignoreRelativePaths: new Set(["DEPRECATED.md"]),
  });
  upsertHarnessScripts(packageJsonPath);
  const harnessConfig = buildHarnessConfig(targetPath);
  writeJson(path.join(targetPath, "harness.config.json"), harnessConfig);
  writeWorkflowFiles(targetPath);
  writeGitHooks(targetPath);

  if (!options.skipGitInit) {
    ensureGitRepo(targetPath);
    configureGitHooksPath(targetPath);
  }

  runContextPack(targetPath);

  if (options.skipInstall) {
    writeJson(path.join(targetPath, ".lint-debt.json"), { version: 1, entries: [] });
    console.log(`Applied deprecated harness overlay to ${targetPath}`);
    return;
  }

  runInRepo(targetPath, ["pnpm", "run", "lint:json"]);
  generateDebtFromReport(targetPath, harnessConfig);

  if (!options.skipVerify) {
    runInRepo(targetPath, ["pnpm", "run", "verify"]);
  }

  console.log(`Applied deprecated harness overlay to ${targetPath}`);
}

function finalizeRepo(targetPath, options) {
  const harnessConfig = buildHarnessConfig(targetPath);
  writeJson(path.join(targetPath, "harness.config.json"), harnessConfig);
  writeWorkflowFiles(targetPath);
  writeGitHooks(targetPath);

  if (!options.skipGitInit) {
    ensureGitRepo(targetPath);
    configureGitHooksPath(targetPath);
  }

  runContextPack(targetPath);

  if (options.skipInstall) {
    writeJson(path.join(targetPath, ".lint-debt.json"), { version: 1, entries: [] });
    console.log(`Created ${targetPath}`);
    return;
  }

  runInRepo(targetPath, ["pnpm", "install"]);
  runContextPack(targetPath);
  runInRepo(targetPath, ["pnpm", "run", "lint:json"]);
  generateDebtFromReport(targetPath, harnessConfig);

  if (!options.skipVerify) {
    runInRepo(targetPath, ["pnpm", "run", "verify"]);
  }

  console.log(`Created ${targetPath}`);
}

function ensureTemplateRoots() {
  if (!fs.existsSync(HARNESS_TEMPLATE_ROOT)) {
    throw new Error(`Missing template directory: ${HARNESS_TEMPLATE_ROOT}`);
  }

  if (!fs.existsSync(WEB_TEMPLATE_ROOT)) {
    throw new Error(`Missing template directory: ${WEB_TEMPLATE_ROOT}`);
  }
}

function applyWebOverlay(targetPath) {
  const repoId = path.basename(targetPath);
  const tokens = {
    "__JIAN_APP_NAME__": repoId,
    "__JIAN_APP_TITLE__": toTitle(repoId),
  };

  copyRecursive(HARNESS_TEMPLATE_ROOT, targetPath, {
    ignoreRelativePaths: new Set(["DEPRECATED.md"]),
  });
  copyRecursive(WEB_TEMPLATE_ROOT, targetPath, { tokens });

  removePath(path.join(targetPath, "src", "app", "_components"));
  removePath(path.join(targetPath, "src", "server", "api", "routers", "post.ts"));

  upsertPackageJson(path.join(targetPath, "package.json"));
  appendEnvExamples(targetPath);
}

function upsertPackageJson(packageJsonPath) {
  const pkg = readJson(packageJsonPath);
  pkg.scripts = {
    ...pkg.scripts,
    build: "next build",
    check: "pnpm run verify",
    dev: "next dev --turbo",
    lint: "eslint . --max-warnings 0",
    "lint:json": "node scripts/harness/lint-json.mjs",
    "lint:harness": "node scripts/harness/lint-ratchet.mjs",
    "lint:obsolete": "node scripts/harness/lint-obsolete.mjs",
    "lint:structure": "node scripts/harness/lint-structure.mjs",
    "tests:changed": "node scripts/harness/tests-changed.mjs",
    "context:pack": "node scripts/llm/build-context-pack.mjs",
    "context:check": "node scripts/llm/build-context-pack.mjs --check",
    "test:unit:harness": "node --test scripts/harness/*.test.mjs",
    "test:unit": "vitest run && pnpm run test:unit:harness",
    test: "pnpm run test:unit",
    typecheck: "tsc --noEmit",
    "prisma:validate": `sh -c 'DATABASE_URL=\${DATABASE_URL:-${DEFAULT_DB_URL}} prisma validate'`,
    verify: [
      "pnpm run lint",
      "pnpm run lint:harness",
      "pnpm run lint:obsolete",
      "pnpm run lint:structure",
      "pnpm run tests:changed",
      "pnpm run context:check",
      "pnpm run typecheck",
      "pnpm run test:unit",
    ].join(" && "),
  };

  pkg.dependencies = {
    ...pkg.dependencies,
    "@radix-ui/react-slot": "^1.2.4",
    "@supabase/ssr": "^0.9.0",
    "@supabase/supabase-js": "^2.99.0",
    "@upstash/ratelimit": "^2.0.8",
    "@upstash/redis": "^1.36.2",
    "class-variance-authority": "^0.7.1",
    clsx: "^2.1.1",
    "tailwind-merge": "^3.5.0",
    stripe: "^20.4.1",
    zustand: "^5.0.11",
  };

  const nextVersion = pkg.dependencies?.next ?? "^15.2.3";
  pkg.devDependencies = {
    ...pkg.devDependencies,
    "@eslint-community/eslint-plugin-eslint-comments": "^4.7.1",
    "@typescript-eslint/eslint-plugin": "^8.56.1",
    "@typescript-eslint/parser": "^8.56.1",
    eslint: "^9.39.1",
    "eslint-config-next": nextVersion,
    "eslint-plugin-import": "^2.32.0",
    "eslint-plugin-react": "^7.37.5",
    "eslint-plugin-sonarjs": "^4.0.0",
    vitest: "^4.0.18",
  };

  writeJson(packageJsonPath, pkg);
}

function upsertHarnessScripts(packageJsonPath) {
  const pkg = readJson(packageJsonPath);
  pkg.scripts = pkg.scripts ?? {};
  pkg.scripts.lint = pkg.scripts.lint ?? "eslint .";
  pkg.scripts.check = "pnpm run verify";
  pkg.scripts["lint:json"] = "node scripts/harness/lint-json.mjs";
  pkg.scripts["lint:harness"] = "node scripts/harness/lint-ratchet.mjs";
  pkg.scripts["lint:obsolete"] = "node scripts/harness/lint-obsolete.mjs";
  pkg.scripts["lint:structure"] = "node scripts/harness/lint-structure.mjs";
  pkg.scripts["tests:changed"] = "node scripts/harness/tests-changed.mjs";
  pkg.scripts["context:pack"] = "node scripts/llm/build-context-pack.mjs";
  pkg.scripts["context:check"] = "node scripts/llm/build-context-pack.mjs --check";
  pkg.scripts["test:unit:harness"] = pkg.scripts["test:unit:harness"] ??
    "node --test scripts/harness/*.test.mjs";
  pkg.scripts.verify = pkg.scripts.verify ?? [
    "pnpm run lint",
    "pnpm run lint:harness",
    "pnpm run lint:obsolete",
    "pnpm run lint:structure",
    "pnpm run tests:changed",
    "pnpm run context:check",
    "pnpm run typecheck",
    "pnpm run test:unit",
  ].join(" && ");

  writeJson(packageJsonPath, pkg);
}

function appendEnvExamples(repoRoot) {
  const extraLines = [
    "",
    "# Supabase auth",
    `NEXT_PUBLIC_SUPABASE_URL="${DEFAULT_SUPABASE_URL}"`,
    'NEXT_PUBLIC_SUPABASE_ANON_KEY=""',
    'SUPABASE_SERVICE_ROLE_KEY=""',
    'NEXT_PUBLIC_APP_URL="http://localhost:3000"',
    'ADMIN_EMAILS=""',
    "",
    "# Upstash rate limiting",
    'UPSTASH_REDIS_REST_URL=""',
    'UPSTASH_REDIS_REST_TOKEN=""',
    "",
    "# Stripe billing",
    'STRIPE_SECRET_KEY=""',
    'STRIPE_WEBHOOK_SECRET=""',
    'STRIPE_PRICE_MONTHLY_ID=""',
    'STRIPE_PRICE_LIFETIME_ID=""',
    'STRIPE_TRIAL_DAYS="14"',
  ].join("\n");

  for (const fileName of [".env.example", ".env"]) {
    const filePath = path.join(repoRoot, fileName);
    if (!fs.existsSync(filePath)) {
      continue;
    }

    const current = fs.readFileSync(filePath, "utf8");
    if (current.includes("NEXT_PUBLIC_SUPABASE_URL")) {
      continue;
    }

    fs.writeFileSync(filePath, `${current.trimEnd()}\n${extraLines}\n`);
  }
}

function buildHarnessConfig(repoRoot) {
  return {
    repoId: path.basename(repoRoot),
    scaffoldProfile: "web-t3",
    packages: [
      {
        name: "web",
        root: ".",
        sourceGlobs: ["src/**/*.{ts,tsx}"],
        unitTestPolicy: {
          mode: "colocated",
          patterns: ["src/**/*.test.{ts,tsx}", "src/**/*.spec.{ts,tsx}"],
        },
        integrationTestRoots: ["tests"],
      },
    ],
    maintainabilityRules: [
      "max-lines",
      "max-lines-per-function",
      "complexity",
      "sonarjs/cognitive-complexity",
      "react/jsx-max-depth",
    ],
    architectureRules: [
      "no-restricted-imports",
      "import/no-cycle",
      "@typescript-eslint/no-floating-promises",
      "@typescript-eslint/no-misused-promises",
      "no-restricted-properties",
    ],
    migrationMode: "phased-ratchet",
    goldenPath: {
      title: "Golden Path",
      sections: [
        {
          title: "Layering and Import Direction",
          bullets: [
            "`src/components/ui/**` is presentational-only and cannot import `~/server/**`.",
            "Client auth, store, and route components must not import server modules directly; use tRPC or shared types.",
            "Cross-feature imports should go through feature entrypoints rather than deep relative imports.",
          ],
        },
        {
          title: "Auth and Data",
          bullets: [
            "Supabase owns authentication state; server auth context derives role from `ADMIN_EMAILS` and bearer/session identity.",
            "tRPC procedures should use `publicProcedure` or `protectedProcedure` instead of re-implementing auth checks in each router.",
            "Prisma is the source of truth for billing state and webhook idempotency; Upstash is optional as a cache and rate-limit layer.",
            "Upstash-backed rate limiting should live behind shared helpers in `src/server/rate-limit/**` rather than inline route logic.",
            "Stripe billing state should sync through shared server helpers and Prisma, with `/success` doing eager refresh instead of trusting webhook timing alone.",
          ],
        },
        {
          title: "Unit Test Policy",
          bullets: [
            "Unit tests are co-located with the source file they validate.",
            "A changed source module must have a co-located unit test file: `foo.ts` -> `foo.test.ts` or `foo.spec.ts`.",
            "Use `tests/**` for integration/e2e coverage rather than centralizing unit tests there.",
          ],
        },
        {
          title: "Maintainability Ratchet",
          bullets: [
            "Maintainability rules run at warning severity.",
            "Existing legacy violations are tracked in `.lint-debt.json`.",
            "New or worsened violations in changed files fail `pnpm run lint:harness`.",
          ],
        },
        {
          title: "Definition of Done",
          bullets: [
            "`pnpm run verify` passes.",
            "`pnpm run lint` stays ESLint-only; harness checks live under `verify`.",
            "If context artifacts drift, regenerate them with `pnpm run context:pack`.",
          ],
        },
      ],
    },
  };
}

function writeGitHooks(repoRoot) {
  const hooksDir = path.join(repoRoot, ".githooks");
  ensureDir(hooksDir);

  const preCommitPath = path.join(hooksDir, "pre-commit");
  const prePushPath = path.join(hooksDir, "pre-push");

  fs.writeFileSync(
    preCommitPath,
    [
      "#!/usr/bin/env bash",
      "set -euo pipefail",
      "pnpm run lint",
      "pnpm run lint:obsolete",
      "pnpm run lint:structure",
      "pnpm run tests:changed",
      "pnpm run context:check",
      "",
    ].join("\n"),
  );
  fs.writeFileSync(
    prePushPath,
    ["#!/usr/bin/env bash", "set -euo pipefail", "pnpm run verify", ""].join("\n"),
  );

  fs.chmodSync(preCommitPath, 0o755);
  fs.chmodSync(prePushPath, 0o755);
}

function writeWorkflowFiles(repoRoot) {
  const workflowsDir = path.join(repoRoot, ".github", "workflows");
  ensureDir(workflowsDir);

  const qualityGates = [
    "name: Quality Gates",
    "",
    "on:",
    "  pull_request:",
    "  workflow_dispatch:",
    "",
    "jobs:",
    "  verify:",
    "    runs-on: ubuntu-latest",
    "    timeout-minutes: 25",
    "    steps:",
    "      - uses: actions/checkout@v4",
    "      - uses: pnpm/action-setup@v4",
    "        with:",
    "          version: 10.14.0",
    "      - uses: actions/setup-node@v4",
    "        with:",
    "          node-version: 20",
    "          cache: pnpm",
    "      - name: Install",
    "        run: pnpm install --frozen-lockfile",
    "      - name: Verify (1/3)",
    "        run: pnpm run lint",
    "      - name: Verify (2/3)",
    "        run: pnpm run typecheck",
    "      - name: Verify (3/3)",
    "        run: pnpm run test:unit",
    "",
  ].join("\n");

  const ci = [
    "name: CI",
    "",
    "on:",
    "  push:",
    "    branches: [\"main\"]",
    "  pull_request:",
    "",
    "permissions:",
    "  contents: read",
    "",
    "concurrency:",
    "  group: ci-${{ github.ref }}",
    "  cancel-in-progress: true",
    "",
    "jobs:",
    "  verify:",
    "    runs-on: ubuntu-latest",
    "    steps:",
    "      - uses: actions/checkout@v4",
    "      - uses: pnpm/action-setup@v4",
    "        with:",
    "          version: 10.14.0",
    "      - uses: actions/setup-node@v4",
    "        with:",
    "          node-version: 20",
    "          cache: pnpm",
    "      - name: Install",
    "        run: pnpm install --frozen-lockfile",
    "      - name: Prisma validate",
    "        run: pnpm run prisma:validate",
    "      - name: Verify harness contract",
    "        run: pnpm run verify",
    "",
  ].join("\n");

  fs.writeFileSync(path.join(workflowsDir, "quality-gates.yml"), qualityGates);
  fs.writeFileSync(path.join(workflowsDir, "ci.yml"), ci);
}

function ensureGitRepo(repoRoot) {
  if (fs.existsSync(path.join(repoRoot, ".git"))) {
    return;
  }

  runCommand("git", ["init"], { cwd: repoRoot });
}

function configureGitHooksPath(repoRoot) {
  try {
    runCommand("git", ["config", "core.hooksPath", ".githooks"], { cwd: repoRoot });
  } catch (error) {
    console.warn("Unable to set git hooks path automatically.");
  }
}

function runContextPack(repoRoot) {
  runInRepo(repoRoot, ["pnpm", "run", "context:pack"]);
}

function generateDebtFromReport(repoRoot, config) {
  const reportPath = path.join(repoRoot, ".codex", "eslint-report.json");
  if (!fs.existsSync(reportPath)) {
    writeJson(path.join(repoRoot, ".lint-debt.json"), { version: 1, entries: [] });
    return;
  }

  const report = readJson(reportPath).results ?? [];
  const maintainabilityRules = new Set(config.maintainabilityRules ?? []);
  const entries = [];

  for (const fileResult of report) {
    for (const message of fileResult.messages ?? []) {
      if (!maintainabilityRules.has(message.ruleId)) {
        continue;
      }

      const metric = parseMetric(message.message ?? "");
      entries.push({
        rule: message.ruleId,
        file: fileResult.filePath,
        line: message.line ?? 0,
        current: metric.current,
        limit: metric.limit,
        owner: "web",
        reason: "seeded scaffold debt baseline",
        expiresOn: "2026-12-31",
      });
    }
  }

  entries.sort((left, right) =>
    `${left.file}:${left.rule}:${left.line}`.localeCompare(
      `${right.file}:${right.rule}:${right.line}`,
    ),
  );
  writeJson(path.join(repoRoot, ".lint-debt.json"), { version: 1, entries });
}

function parseMetric(messageText) {
  const standard = messageText.match(/\((\d+)\).*Maximum allowed is (\d+)/i);
  if (standard) {
    return { current: Number(standard[1]), limit: Number(standard[2]) };
  }

  const complexity = messageText.match(/complexity of (\d+)\. Maximum allowed is (\d+)/i);
  if (complexity) {
    return { current: Number(complexity[1]), limit: Number(complexity[2]) };
  }

  const cognitive = messageText.match(/from (\d+) to the (\d+) allowed/i);
  if (cognitive) {
    return { current: Number(cognitive[1]), limit: Number(cognitive[2]) };
  }

  const jsx = messageText.match(/<=\s*(\d+),\s*but found\s*(\d+)/i);
  if (jsx) {
    return { current: Number(jsx[2]), limit: Number(jsx[1]) };
  }

  return { current: null, limit: null };
}

function copyRecursive(sourceDir, targetDir, options = {}, relativeDir = "") {
  ensureDir(targetDir);
  for (const entry of fs.readdirSync(sourceDir, { withFileTypes: true })) {
    const sourcePath = path.join(sourceDir, entry.name);
    const targetPath = path.join(targetDir, entry.name);
    const relativePath = path.posix.join(relativeDir, entry.name).replace(/\\/g, "/");

    if (options.ignoreRelativePaths?.has(relativePath)) {
      continue;
    }

    if (entry.isDirectory()) {
      copyRecursive(sourcePath, targetPath, options, relativePath);
      continue;
    }

    ensureDir(path.dirname(targetPath));
    if (isTextTemplate(sourcePath)) {
      let text = fs.readFileSync(sourcePath, "utf8");
      for (const [token, value] of Object.entries(options.tokens ?? {})) {
        text = text.replaceAll(token, value);
      }
      fs.writeFileSync(targetPath, text);
      continue;
    }

    fs.copyFileSync(sourcePath, targetPath);
  }
}

function isTextTemplate(filePath) {
  return /\.(?:[cm]?js|json|md|ts|tsx|css|yml|yaml|sh|gitignore)$/i.test(filePath);
}

function ensureDir(dirPath) {
  fs.mkdirSync(dirPath, { recursive: true });
}

function removePath(targetPath) {
  fs.rmSync(targetPath, { recursive: true, force: true });
}

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

function writeJson(filePath, value) {
  ensureDir(path.dirname(filePath));
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`);
}

function runInRepo(repoRoot, args) {
  runCommand(args[0], args.slice(1), { cwd: repoRoot });
}

function runCommand(command, args, options = {}) {
  execFileSync(command, args, {
    cwd: options.cwd ?? process.cwd(),
    env: { ...process.env, ...(options.env ?? {}) },
    stdio: ["ignore", "inherit", "inherit"],
    maxBuffer: 1024 * 1024 * 128,
  });
}

function toTitle(repoId) {
  return repoId
    .split(/[-_]/g)
    .filter(Boolean)
    .map((segment) => segment[0].toUpperCase() + segment.slice(1))
    .join(" ");
}
