export const KNOWN_BUNDLE_IDS = [
  "com.microsoft.VSCode",
  "com.openai.codex",
  "com.cmuxterm.app",
  "com.tdesktop.Telegram",
  "app.zen-browser.zen",
  "com.apple.Safari",
  "com.getupnote.desktop",
  "com.google.Chrome",
  "company.thebrowser.Browser",
  "com.brave.Browser",
  "org.mozilla.firefox",
  "NULL-APP-BUNDLE-ID"
] as const;

export type KnownBundleId = (typeof KNOWN_BUNDLE_IDS)[number];

export type CanonicalWorkspaceId = "w1";

export type BrowserState = "zen" | "safari" | "";
export type ActiveUtilityBundle = "com.openai.codex" | "com.cmuxterm.app" | "com.tdesktop.Telegram" | "";

export interface WorkspaceState {
  workspace: CanonicalWorkspaceId;
  browser: BrowserState;
  upnoteTiled: boolean;
  tiledOrder: number[];
  activeUtilityBundle: ActiveUtilityBundle;
  activeUtilityWindowId: number | null;
}

export type CallbackKind =
  | "on_window"
  | "on_focus"
  | "balance"
  | "switch_ws"
  | "reset_ws"
  | "move_to_focused"
  | "video_mode";

export interface CallbackInput {
  kind: CallbackKind;
  workspace: CanonicalWorkspaceId;
  bundleId?: string;
  argv: string[];
  timestampMs: number;
}

export interface WindowSnapshotRow {
  windowId: number;
  workspace: CanonicalWorkspaceId;
  bundleId: string;
  layout: "floating" | "h_tiles" | "v_tiles" | "stacked" | "accordion";
  title: string;
}

export interface FocusedWindow {
  windowId: number;
  workspace: CanonicalWorkspaceId;
  bundleId: string;
  layout: "floating" | "h_tiles" | "v_tiles" | "stacked" | "accordion";
  title: string;
}

export interface PlannerContext {
  callback: CallbackInput;
  workspaceState: WorkspaceState;
  focusedWindow: FocusedWindow | null;
  windows: WindowSnapshotRow[];
  guards: {
    isHomeWorkspace: boolean;
    isManagedBundle: boolean;
    isPopupIntent: boolean;
  };
}
