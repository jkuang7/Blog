import { getRequestAuthContext } from "~/server/auth/context";
import { getCurrentUserEntitlement, isPremiumEntitlement } from "~/server/billing/entitlements";

export class AuthGuardError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

export async function requireAuth() {
  const auth = await getRequestAuthContext();
  if (!auth) {
    throw new AuthGuardError("Authentication required", 401);
  }

  return auth;
}

export async function requirePremiumAccess() {
  const auth = await requireAuth();
  const entitlement = await getCurrentUserEntitlement(auth.id);
  if (!isPremiumEntitlement(entitlement)) {
    throw new AuthGuardError("Premium access required", 403);
  }

  return {
    auth,
    entitlement,
  };
}
