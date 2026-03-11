"use client";

import { createBrowserClient } from "@supabase/ssr";

import { env } from "~/env";

let browserClient: ReturnType<typeof createBrowserClient> | null = null;

export function createSupabaseBrowserClient() {
  if (browserClient) {
    return browserClient;
  }

  const url = env.NEXT_PUBLIC_SUPABASE_URL?.trim();
  const anonKey = env.NEXT_PUBLIC_SUPABASE_ANON_KEY?.trim();
  if (!url || !anonKey) {
    throw new Error("Supabase auth is not configured");
  }

  browserClient = createBrowserClient(url, anonKey);
  return browserClient;
}
