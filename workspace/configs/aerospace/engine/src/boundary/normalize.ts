import {
  callbackInputSchema,
  plannerContextSchema,
  type PlannerContextOutput,
  windowSnapshotRowSchema,
  workspaceStateSchema,
  workspaceStateV2Schema
} from "./schemas.js";

const POPUP_TITLE_REGEX =
  /oauth|auth|sign\s*in|log\s*in|login|permission|extension|download|save|open\s*file|alert|dialog|sheet|confirm|prompt|settings|preferences|prefs|rename\s+(tab|workspace|surface|pane)/i;

export function normalizeWorkspaceId(raw: string): "w1" {
  const normalized = raw.trim();
  if (normalized === "w1" || normalized === "1") {
    return "w1";
  }
  throw new Error(`unsupported workspace id: ${raw}`);
}

export function parseWorkspaceStateFile(
  workspace: string,
  content: string
): ReturnType<typeof workspaceStateSchema.parse> {
  const ws = normalizeWorkspaceId(workspace);
  const lines = content
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0);

  const kv = new Map<string, string>();
  for (const line of lines) {
    const idx = line.indexOf("=");
    if (idx <= 0) {
      continue;
    }
    const key = line.slice(0, idx).trim();
    const value = line.slice(idx + 1).trim();
    kv.set(key, value);
  }

  const browser = (kv.get("BROWSER") ?? "") as "zen" | "safari" | "";
  const upnoteTiled = (kv.get("UPNOTE_TILED") ?? "false") === "true";
  const tiledOrder = (kv.get("TILED_ORDER") ?? "")
    .split(",")
    .map((value) => value.trim())
    .filter((value) => value.length > 0)
    .map((value) => Number(value))
    .filter((value) => Number.isInteger(value) && value >= 0);
  const activeUtilityBundle = (kv.get("ACTIVE_UTILITY_BUNDLE") ?? "") as
    | "com.openai.codex"
    | "com.cmuxterm.app"
    | "com.tdesktop.Telegram"
    | "";
  const activeUtilityWindowIdRaw = kv.get("ACTIVE_UTILITY_WID") ?? "";
  const activeUtilityWindowId = activeUtilityWindowIdRaw.length > 0 ? Number(activeUtilityWindowIdRaw) : null;

  return workspaceStateSchema.parse({
    workspace: ws,
    browser,
    upnoteTiled,
    tiledOrder,
    activeUtilityBundle,
    activeUtilityWindowId:
      Number.isInteger(activeUtilityWindowId) && (activeUtilityWindowId ?? -1) >= 0 ? activeUtilityWindowId : null
  });
}

export function parseWorkspaceStateV2File(content: string): ReturnType<typeof workspaceStateV2Schema.parse> {
  const parsed = JSON.parse(content) as unknown;
  const stateV2 = workspaceStateV2Schema.parse(parsed);
  return stateV2;
}

export function parseWorkspaceStateCompat(input: {
  workspace: string;
  legacyContent?: string;
  v2Content?: string;
}): ReturnType<typeof workspaceStateSchema.parse> {
  if (input.v2Content) {
    const v2 = parseWorkspaceStateV2File(input.v2Content);
    return workspaceStateSchema.parse({
      workspace: v2.workspace,
      browser: v2.browser,
      upnoteTiled: v2.upnoteTiled,
      tiledOrder: v2.tiledOrder,
      activeUtilityBundle: v2.activeUtilityBundle,
      activeUtilityWindowId: v2.activeUtilityWindowId
    });
  }

  if (input.legacyContent) {
    return parseWorkspaceStateFile(input.workspace, input.legacyContent);
  }

  return workspaceStateSchema.parse({
    workspace: normalizeWorkspaceId(input.workspace),
    browser: "zen",
    upnoteTiled: true,
    tiledOrder: [],
    activeUtilityBundle: "",
    activeUtilityWindowId: null
  });
}

export function parseWindowSnapshotLine(line: string): ReturnType<typeof windowSnapshotRowSchema.parse> {
  const [windowIdRaw, workspaceRaw, bundleIdRaw, layoutRaw, ...titleParts] = line.split("|");
  const windowId = Number(windowIdRaw);
  const workspace = normalizeWorkspaceId(workspaceRaw ?? "");
  const bundleId = (bundleIdRaw ?? "").trim();
  const layout = (layoutRaw ?? "").trim();
  const title = titleParts.join("|");

  return windowSnapshotRowSchema.parse({
    windowId,
    workspace,
    bundleId,
    layout,
    title
  });
}

export function parseWindowSnapshot(lines: string): ReturnType<typeof windowSnapshotRowSchema.parse>[] {
  return lines
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
    .map(parseWindowSnapshotLine)
    .sort((a, b) => {
      if (a.windowId !== b.windowId) {
        return a.windowId - b.windowId;
      }
      return a.bundleId.localeCompare(b.bundleId);
    });
}

export function normalizeCallbackInput(input: {
  kind: string;
  workspace: string;
  bundleId?: string;
  argv?: string[];
  timestampMs?: number;
}) {
  return callbackInputSchema.parse({
    kind: input.kind,
    workspace: normalizeWorkspaceId(input.workspace),
    bundleId: input.bundleId?.trim() || undefined,
    argv: input.argv ?? [],
    timestampMs: input.timestampMs ?? Date.now()
  });
}

export function isPopupTitle(title: string): boolean {
  return POPUP_TITLE_REGEX.test(title);
}

export function toCanonicalPlannerContext(
  input: Omit<PlannerContextOutput, "guards"> & {
    guards?: Partial<PlannerContextOutput["guards"]>;
  }
): PlannerContextOutput {
  const windows = [...input.windows].sort((a, b) => {
    if (a.windowId !== b.windowId) {
      return a.windowId - b.windowId;
    }
    return a.bundleId.localeCompare(b.bundleId);
  });

  const callback = normalizeCallbackInput(input.callback);

  return plannerContextSchema.parse({
    callback,
    workspaceState: input.workspaceState,
    focusedWindow: input.focusedWindow,
    windows,
    guards: {
      isHomeWorkspace: input.guards?.isHomeWorkspace ?? callback.workspace === "w1",
      isManagedBundle: input.guards?.isManagedBundle ?? Boolean(callback.bundleId),
      isPopupIntent:
        input.guards?.isPopupIntent ?? (input.focusedWindow ? isPopupTitle(input.focusedWindow.title) : false)
    }
  });
}
