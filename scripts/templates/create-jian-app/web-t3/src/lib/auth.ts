import { createClient } from "@supabase/supabase-js";

import { env } from "~/env";
import { createSupabaseServerClient } from "~/lib/supabase/server";
import type { SessionIdentity } from "~/types/auth";

function normalizeIdentity(user: { id?: string; email?: string | null } | null | undefined) {
  if (!user?.id) {
    return null;
  }

  return {
    id: user.id,
    email: user.email ?? "",
  } satisfies SessionIdentity;
}

function createSupabaseTokenVerifier() {
  const url = env.NEXT_PUBLIC_SUPABASE_URL?.trim();
  const anonKey = env.NEXT_PUBLIC_SUPABASE_ANON_KEY?.trim();

  if (!url || !anonKey) {
    return null;
  }

  return createClient(url, anonKey, {
    auth: {
      persistSession: false,
      autoRefreshToken: false,
      detectSessionInUrl: false,
    },
  });
}

export async function getIdentityFromServerSession() {
  try {
    const supabase = await createSupabaseServerClient();
    const { data, error } = await supabase.auth.getUser();
    if (error || !data.user) {
      return null;
    }

    return normalizeIdentity(data.user);
  } catch {
    return null;
  }
}

export async function getIdentityFromBearerToken(token: string) {
  const verifier = createSupabaseTokenVerifier();
  if (!verifier) {
    return null;
  }

  const { data, error } = await verifier.auth.getUser(token);
  if (error || !data.user) {
    return null;
  }

  return normalizeIdentity(data.user);
}
