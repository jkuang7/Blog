import path from "node:path";

import { parseHarnessConfig, readFileIfExists, writeText } from "../harness/lib.mjs";

function renderGoldenPathMarkdown(harness) {
  const lines = [];
  const goldenPath = harness.goldenPath ?? { title: "Golden Path", sections: [] };

  lines.push(`# ${goldenPath.title ?? "Golden Path"}`);
  lines.push("");

  for (const section of goldenPath.sections ?? []) {
    lines.push(`## ${section.title}`);
    lines.push("");
    for (const bullet of section.bullets ?? []) {
      lines.push(`- ${bullet}`);
    }
    lines.push("");
  }

  return `${lines.join("\n").trim()}\n`;
}

function buildMarkdown(harness, goldenPathMarkdown) {
  const lines = [];

  lines.push(`# LLM Context Pack: ${harness.repoId}`);
  lines.push("");
  lines.push("## Hard Boundaries");
  for (const rule of harness.architectureRules ?? []) {
    lines.push(`- ${rule}`);
  }
  lines.push("");
  lines.push("## Maintainability Ratchet");
  for (const rule of harness.maintainabilityRules ?? []) {
    lines.push(`- ${rule}`);
  }
  lines.push("");
  lines.push("## Package Contracts");
  for (const pkg of harness.packages ?? []) {
    lines.push(`- ${pkg.name}`);
    lines.push(`  - root: ${pkg.root}`);
    lines.push(`  - source: ${(pkg.sourceGlobs ?? []).join(", ")}`);
    lines.push(
      `  - unit tests: co-located (${(pkg.unitTestPolicy?.patterns ?? []).join(", ")})`,
    );
    lines.push(`  - integration roots: ${(pkg.integrationTestRoots ?? []).join(", ")}`);
  }
  lines.push("");
  lines.push("## Done Criteria");
  lines.push("- During iteration, use the smallest verification that proves the changed contract");
  lines.push("- For commit-ready or high-risk work, run `pnpm run verify` (full quality gate)");
  lines.push("- `pnpm run lint` is ESLint-only");
  lines.push("");
  lines.push("## Golden Path");
  lines.push(goldenPathMarkdown.trim());
  lines.push("");

  return `${lines.join("\n")}\n`;
}

function buildJson(harness, goldenPathMarkdown) {
  return {
    repoId: harness.repoId,
    scaffoldProfile: harness.scaffoldProfile ?? null,
    architectureRules: harness.architectureRules ?? [],
    maintainabilityRules: harness.maintainabilityRules ?? [],
    packages: harness.packages ?? [],
    testPolicy: {
      unit: "co-located",
      integration: "dedicated roots",
      migrationMode: harness.migrationMode ?? "phased-ratchet",
    },
    doneCriteria: ["pnpm run verify", "pnpm run lint (eslint-only)"],
    goldenPath: harness.goldenPath ?? null,
    goldenPathMarkdown,
  };
}

function serializeJson(value) {
  return `${JSON.stringify(value, null, 2)}\n`;
}

function main() {
  const checkMode = process.argv.includes("--check");
  const harness = parseHarnessConfig();

  const goldenPathMarkdown = renderGoldenPathMarkdown(harness);
  const markdown = buildMarkdown(harness, goldenPathMarkdown);
  const json = serializeJson(buildJson(harness, goldenPathMarkdown));

  const goldenPathPath = path.resolve(process.cwd(), "docs/llm/golden-path.md");
  const markdownPath = path.resolve(process.cwd(), ".codex/context-pack.md");
  const jsonPath = path.resolve(process.cwd(), ".codex/context-pack.json");

  if (checkMode) {
    const existingGoldenPath = readFileIfExists(goldenPathPath);
    const existingMarkdown = readFileIfExists(markdownPath);
    const existingJson = readFileIfExists(jsonPath);

    if (
      existingGoldenPath !== goldenPathMarkdown ||
      existingMarkdown !== markdown ||
      existingJson !== json
    ) {
      console.error("Context pack is out of date. Run: pnpm run context:pack");
      process.exit(1);
    }

    console.log("Context pack is up to date.");
    return;
  }

  writeText(goldenPathPath, goldenPathMarkdown);
  writeText(markdownPath, markdown);
  writeText(jsonPath, json);

  console.log(`Wrote ${goldenPathPath}`);
  console.log(`Wrote ${markdownPath}`);
  console.log(`Wrote ${jsonPath}`);
}

main();
