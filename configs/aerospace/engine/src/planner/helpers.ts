import type { PlannerContext } from "../domain/contracts.js";

export function hasBundle(context: PlannerContext, bundleId: string): boolean {
  return context.windows.some((window) => window.bundleId === bundleId);
}

export function activeLayoutProfile(context: PlannerContext): "3-col" | "4-col" {
  return context.workspaceState.upnoteTiled ? "4-col" : "3-col";
}

export function isManagedBundle(bundleId: string | undefined): boolean {
  if (!bundleId) {
    return false;
  }
  return [
    "com.microsoft.VSCode",
    "com.openai.codex",
    "app.zen-browser.zen",
    "com.apple.Safari",
    "com.getupnote.desktop"
  ].includes(bundleId);
}
