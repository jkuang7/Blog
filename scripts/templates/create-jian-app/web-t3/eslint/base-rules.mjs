const RULE_WITH_TRIMMED_LINES = {
  skipBlankLines: true,
  skipComments: true,
};

export const DEFAULT_MAINTAINABILITY_THRESHOLDS = {
  complexity: 12,
  cognitiveComplexity: 15,
  maxLines: 300,
  maxLinesPerFunctionTs: 90,
  maxLinesPerFunctionTsx: 120,
  jsxMaxDepth: 5,
};

export const MAINTAINABILITY_RULE_NAMES = [
  "complexity",
  "max-lines-per-function",
  "max-lines",
  "react/jsx-max-depth",
  "sonarjs/cognitive-complexity",
];

export const DEFAULT_IGNORES = [
  ".next/**",
  "coverage/**",
  "dist/**",
  "generated/**",
  "node_modules/**",
  "test-results/**",
  "tsconfig.tsbuildinfo",
];

const RULE_PROFILES = {
  strict: {
    maintainabilitySeverity: "error",
    architectureSeverity: "error",
    featureBoundarySeverity: "error",
    uiLayerBoundarySeverity: "error",
    featureBoundaryEntrypointOnly: false,
    eslintCommentRequireDescription: true,
    unusedVarsSeverity: "error",
    noExplicitAnySeverity: "off",
  },
  hardened: {
    maintainabilitySeverity: "error",
    architectureSeverity: "error",
    featureBoundarySeverity: "error",
    uiLayerBoundarySeverity: "error",
    featureBoundaryEntrypointOnly: true,
    eslintCommentRequireDescription: true,
    unusedVarsSeverity: "error",
    noExplicitAnySeverity: "off",
  },
};

function normalizeSeverity(value, fallback = "error") {
  if (value === "off" || value === "warn" || value === "error") {
    return value;
  }

  return fallback;
}

function withSeverity(severity, payload) {
  if (severity === "off") {
    return "off";
  }

  return [severity, payload];
}

function withSeverityNumber(severity, payload) {
  if (severity === "off") {
    return "off";
  }

  return [severity, payload];
}

export function resolveRuleProfile(profileName = "strict", profileOverrides = {}) {
  const baseProfile = RULE_PROFILES[profileName] ?? RULE_PROFILES.strict;

  return {
    ...baseProfile,
    ...profileOverrides,
  };
}

function buildCoreArchitectureRules(severity = "error") {
  const normalizedSeverity = normalizeSeverity(severity);
  const focusedTestSelectors = [
    { object: "describe", property: "only", message: "Focused tests are not allowed." },
    { object: "it", property: "only", message: "Focused tests are not allowed." },
    { object: "test", property: "only", message: "Focused tests are not allowed." },
  ];

  return {
    "@typescript-eslint/no-floating-promises": normalizedSeverity,
    "@typescript-eslint/no-misused-promises": withSeverity(normalizedSeverity, {
      checksVoidReturn: { attributes: false },
    }),
    "import/no-cycle": normalizedSeverity,
    "no-restricted-properties":
      normalizedSeverity === "off"
        ? "off"
        : [normalizedSeverity, ...focusedTestSelectors],
  };
}

function buildCoreSharedRules(params = {}) {
  const unusedVarsSeverity = normalizeSeverity(params.unusedVarsSeverity, "error");
  const noExplicitAnySeverity = normalizeSeverity(params.noExplicitAnySeverity, "off");

  return {
    "@typescript-eslint/no-explicit-any": noExplicitAnySeverity,
    "no-unused-vars": "off",
    "@typescript-eslint/no-unused-vars": withSeverity(unusedVarsSeverity, {
      argsIgnorePattern: "^_",
      varsIgnorePattern: "^_",
    }),
    "eslint-comments/require-description": params.eslintCommentRequireDescription
      ? "error"
      : "off",
  };
}

export function buildMaintainabilityRules(params = {}) {
  const isTsx = params.isTsx === true;
  const severity = normalizeSeverity(params.severity);
  const thresholds = {
    ...DEFAULT_MAINTAINABILITY_THRESHOLDS,
    ...(params.thresholds ?? {}),
  };

  const rules = {
    complexity: withSeverityNumber(severity, thresholds.complexity),
    "max-lines": withSeverity(severity, {
      max: thresholds.maxLines,
      ...RULE_WITH_TRIMMED_LINES,
    }),
    "sonarjs/cognitive-complexity": withSeverityNumber(
      severity,
      thresholds.cognitiveComplexity,
    ),
    "max-lines-per-function": withSeverity(severity, {
      max: isTsx ? thresholds.maxLinesPerFunctionTsx : thresholds.maxLinesPerFunctionTs,
      ...RULE_WITH_TRIMMED_LINES,
    }),
  };

  if (isTsx) {
    rules["react/jsx-max-depth"] = withSeverity(severity, {
      max: thresholds.jsxMaxDepth,
    });
  }

  return rules;
}

export function buildSourceRules(params = {}) {
  const isTsx = params.isTsx === true;
  const profile = resolveRuleProfile(params.ruleProfile, params.profileOverrides);

  return {
    ...buildCoreSharedRules({
      eslintCommentRequireDescription: profile.eslintCommentRequireDescription,
      unusedVarsSeverity: profile.unusedVarsSeverity,
      noExplicitAnySeverity: profile.noExplicitAnySeverity,
    }),
    ...buildCoreArchitectureRules(profile.architectureSeverity),
    ...buildMaintainabilityRules({
      isTsx,
      severity: params.maintainabilitySeverity ?? profile.maintainabilitySeverity,
      thresholds: params.maintainabilityThresholds,
    }),
  };
}

export function buildTestRules() {
  const disabledMaintainabilityRules = Object.fromEntries(
    MAINTAINABILITY_RULE_NAMES.map((ruleName) => [ruleName, "off"]),
  );

  return {
    ...disabledMaintainabilityRules,
    "import/no-cycle": "off",
    "@typescript-eslint/no-floating-promises": "off",
    "@typescript-eslint/no-misused-promises": "off",
    "@typescript-eslint/no-unused-vars": "off",
    "no-unused-vars": "off",
    "@typescript-eslint/no-explicit-any": "off",
    "@typescript-eslint/no-unused-expressions": "off",
    "eslint-comments/require-description": "off",
  };
}

function buildFeatureBoundaryPatterns(params = {}) {
  const patterns = [
    {
      group: [
        "**/features/*/internal/**",
        "**/features/*/*/internal/**",
        "@/features/*/internal/**",
        "~/features/*/internal/**",
      ],
      message:
        "Import feature internals through the feature public entrypoint (index.ts or public.ts).",
    },
    {
      group: [
        "@/features/*/*/*",
        "~/features/*/*/*",
        "src/features/*/*/*",
        "../features/*/*/*",
        "../../features/*/*/*",
        "../../../features/*/*/*",
      ],
      message: "Avoid deep cross-feature imports. Use the feature public entrypoint instead.",
    },
  ];

  if (params.entrypointOnly === true) {
    patterns.push({
      group: ["@/features/*/*", "~/features/*/*", "src/features/*/*"],
      message:
        "Hardened mode: import features through their root entrypoint only (for example '~/features/<feature>').",
    });
  }

  return patterns;
}

export function buildFeatureBoundaryRules(params = {}) {
  const severity = normalizeSeverity(params.severity ?? "error");

  return {
    "no-restricted-imports": withSeverity(severity, {
      patterns: buildFeatureBoundaryPatterns({
        entrypointOnly: params.entrypointOnly,
      }),
    }),
  };
}

export function buildUiLayerBoundaryRules(params = {}) {
  const severity = normalizeSeverity(params.severity ?? "error");

  return {
    "no-restricted-imports": withSeverity(severity, {
      patterns: [
        {
          group: [
            "@/server/**",
            "~/server/**",
            "../server/**",
            "../../server/**",
            "../../../server/**",
            "@/infra/**",
            "~/infra/**",
            "../infra/**",
            "../../infra/**",
            "../../../infra/**",
          ],
          message: "UI/presentational code cannot import server/infra modules directly.",
        },
      ],
    }),
  };
}
