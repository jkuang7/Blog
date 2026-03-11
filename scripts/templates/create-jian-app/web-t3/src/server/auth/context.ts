import { headers as nextHeaders } from "next/headers";

import { env } from "~/env";
import { getIdentityFromBearerToken } from "~/lib/auth";
import type { RequestAuthContext, SessionIdentity } from "~/types/auth";
import {
  extractBearerToken,
  roleFromEmail,
} from "~/server/auth/auth-helpers";
import { getSessionUser } from "~/server/auth/session";

export function buildRequestAuthContextFromIdentity(
  identity: SessionIdentity | null,
): RequestAuthContext | null {
  if (!identity) {
    return null;
  }

  return {
    id: identity.id,
    email: identity.email,
    role: roleFromEmail(identity.email, env.ADMIN_EMAILS),
  } satisfies RequestAuthContext;
}

export async function getRequestAuthContext(
  requestHeaders?: Headers,
): Promise<RequestAuthContext | null> {
  const resolvedHeaders = requestHeaders ?? new Headers(await nextHeaders());
  const bearer = extractBearerToken(resolvedHeaders.get("authorization"));

  let identity = null;
  if (bearer) {
    identity = await getIdentityFromBearerToken(bearer);
  }

  if (!identity) {
    const sessionUser = await getSessionUser();
    identity = sessionUser
      ? ({
          id: sessionUser.id,
          email: sessionUser.email,
        } satisfies SessionIdentity)
      : null;
  }

  return buildRequestAuthContextFromIdentity(identity);
}
