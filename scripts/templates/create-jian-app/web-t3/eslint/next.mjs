import tsParser from "@typescript-eslint/parser";
import tsPlugin from "@typescript-eslint/eslint-plugin";
import reactPlugin from "eslint-plugin-react";
import sonarjsPlugin from "eslint-plugin-sonarjs";
import importPlugin from "eslint-plugin-import";
import eslintCommentsPlugin from "@eslint-community/eslint-plugin-eslint-comments";
import {
  DEFAULT_IGNORES,
  buildFeatureBoundaryRules,
  buildSourceRules,
  buildUiLayerBoundaryRules,
  resolveRuleProfile,
} from "./base-rules.mjs";
import { createTestOverride, DEFAULT_TEST_GLOBS } from "./test-overrides.mjs";

const PLUGINS = {
  "@typescript-eslint": tsPlugin,
  react: reactPlugin,
  sonarjs: sonarjsPlugin,
  import: importPlugin,
  "eslint-comments": eslintCommentsPlugin,
};

export function createNextConfig(params = {}) {
  const srcRoot = params.srcRoot ?? "src";
  const tsFiles = params.tsFiles ?? [`${srcRoot}/**/*.ts`];
  const tsxFiles = params.tsxFiles ?? [`${srcRoot}/**/*.tsx`];
  const jsFiles = params.jsFiles ?? ["**/*.js", "**/*.mjs"];
  const testGlobs = params.testGlobs ?? DEFAULT_TEST_GLOBS;
  const maintainabilityThresholds = params.maintainabilityThresholds ?? {};
  const uiLayerFiles = params.uiLayerFiles ?? [
    `${srcRoot}/components/**/*.{ts,tsx}`,
    `${srcRoot}/features/**/components/**/*.{ts,tsx}`,
  ];
  const serverUiFiles = params.serverUiFiles ?? [];

  const profile = resolveRuleProfile(params.profile, params.profileOverrides);

  const sourceRuleParams = {
    ruleProfile: params.profile,
    profileOverrides: params.profileOverrides,
    maintainabilitySeverity:
      params.maintainabilitySeverity ?? profile.maintainabilitySeverity,
    maintainabilityThresholds,
  };

  const featureBoundaryRules = buildFeatureBoundaryRules({
    severity: params.featureBoundarySeverity ?? profile.featureBoundarySeverity,
    entrypointOnly:
      params.featureBoundaryEntrypointOnly ?? profile.featureBoundaryEntrypointOnly,
  });

  const uiLayerBoundaryRules = buildUiLayerBoundaryRules({
    severity: params.uiLayerBoundarySeverity ?? profile.uiLayerBoundarySeverity,
  });

  const serverUiOverride =
    serverUiFiles.length > 0
      ? [
          {
            files: serverUiFiles,
            plugins: PLUGINS,
            rules: {
              "no-restricted-imports": "off",
            },
          },
        ]
      : [];

  return [
    {
      files: jsFiles,
      languageOptions: {
        ecmaVersion: "latest",
        sourceType: "module",
      },
      rules: {
        "no-unused-vars": ["warn", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
      },
    },
    {
      files: [...tsFiles, ...tsxFiles],
      languageOptions: {
        parser: tsParser,
        parserOptions: {
          projectService: true,
        },
        ecmaVersion: "latest",
        sourceType: "module",
      },
      plugins: PLUGINS,
      settings: {
        react: {
          version: "detect",
        },
      },
      rules: {
        ...featureBoundaryRules,
      },
    },
    {
      files: tsFiles,
      plugins: PLUGINS,
      rules: buildSourceRules({
        ...sourceRuleParams,
        isTsx: false,
      }),
    },
    {
      files: tsxFiles,
      plugins: PLUGINS,
      rules: buildSourceRules({
        ...sourceRuleParams,
        isTsx: true,
      }),
    },
    {
      files: uiLayerFiles,
      plugins: PLUGINS,
      rules: uiLayerBoundaryRules,
    },
    ...serverUiOverride,
    createTestOverride(testGlobs),
    {
      ignores: [...DEFAULT_IGNORES, ...(params.extraIgnores ?? [])],
    },
    ...(params.extraConfigs ?? []),
  ];
}
