import { findStripeCustomerIdForUser } from "~/server/billing/billing-account-adapter";
import { readEntitlementByCustomerId } from "~/server/billing/syncStripeDataToKV";
import type { Entitlement } from "~/types/entitlement";

function defaultFreeEntitlement(customerId: string): Entitlement {
  return {
    customerId,
    level: "free",
    premium: false,
    lifetime: false,
    source: "db",
    updatedAt: new Date(0).toISOString(),
  };
}

export function isPremiumEntitlement(entitlement: Entitlement | null): boolean {
  return Boolean(entitlement?.premium || entitlement?.lifetime);
}

export async function getCurrentUserEntitlement(userId: string): Promise<Entitlement> {
  const stripeCustomerId = await findStripeCustomerIdForUser(userId);
  if (!stripeCustomerId) {
    return defaultFreeEntitlement("unmapped");
  }

  const entitlement = await readEntitlementByCustomerId(stripeCustomerId);
  if (!entitlement) {
    return defaultFreeEntitlement(stripeCustomerId);
  }

  return entitlement;
}
