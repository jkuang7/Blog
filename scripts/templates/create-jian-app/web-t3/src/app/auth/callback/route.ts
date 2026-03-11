import { NextResponse } from "next/server";

import { createSupabaseServerClient } from "~/lib/supabase/server";
import { safeNextPath } from "~/server/auth/auth-helpers";

export async function GET(request: Request) {
  const url = new URL(request.url);
  const code = url.searchParams.get("code");
  const nextPath = safeNextPath(url.searchParams.get("next"));

  if (code) {
    try {
      const supabase = await createSupabaseServerClient();
      await supabase.auth.exchangeCodeForSession(code);
    } catch {
      return NextResponse.redirect(new URL("/login", url.origin));
    }
  }

  return NextResponse.redirect(new URL(nextPath, url.origin));
}
