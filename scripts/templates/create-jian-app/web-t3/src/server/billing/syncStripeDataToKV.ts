import type Stripe from "stripe";

import { getStripe } from "~/lib/stripe";
import { getRedisClient } from "~/lib/upstash";
import { db } from "~/server/db";
import type { Entitlement } from "~/types/entitlement";

const PREMIUM_STATUSES = new Set<Stripe.Subscription.Status>(["trialing", "active", "past_due"]);

function paidCustomersKey(): string {
  return "index:paid_customers";
}

function toEntitlement(record: {
  stripeCustomerId: string;
  entitlementLevel: "free" | "premium" | "lifetime";
  premium: boolean;
  lifetime: boolean;
  updatedAt: Date;
}): Entitlement {
  return {
    customerId: record.stripeCustomerId,
    level: record.entitlementLevel,
    premium: record.premium,
    lifetime: record.lifetime,
    source: "db",
    updatedAt: record.updatedAt.toISOString(),
  };
}

async function mirrorEntitlementToKV(entitlement: Entitlement): Promise<void> {
  if (!process.env.UPSTASH_REDIS_REST_URL || !process.env.UPSTASH_REDIS_REST_TOKEN) {
    return;
  }

  try {
    const redis = getRedisClient();
    await redis.set(`entitlement:customer:${entitlement.customerId}`, entitlement);
    if (entitlement.premium || entitlement.lifetime) {
      await redis.sadd(paidCustomersKey(), entitlement.customerId);
    } else {
      await redis.srem(paidCustomersKey(), entitlement.customerId);
    }
  } catch {
    // prisma remains the source of truth if kv mirroring fails
  }
}

function hasPremiumSubscription(subscriptions: Stripe.Subscription[]): boolean {
  return subscriptions.some((subscription) => PREMIUM_STATUSES.has(subscription.status));
}

export async function readEntitlementByCustomerId(customerId: string): Promise<Entitlement | null> {
  const record = await db.billingAccount.findUnique({
    where: { stripeCustomerId: customerId },
    select: {
      stripeCustomerId: true,
      entitlementLevel: true,
      premium: true,
      lifetime: true,
      updatedAt: true,
    },
  });

  return record ? toEntitlement(record) : null;
}

export async function setLifetimeEntitlementFlag(customerId: string): Promise<void> {
  const updated = await db.billingAccount.update({
    where: { stripeCustomerId: customerId },
    data: {
      entitlementLevel: "lifetime",
      premium: true,
      lifetime: true,
    },
    select: {
      stripeCustomerId: true,
      entitlementLevel: true,
      premium: true,
      lifetime: true,
      updatedAt: true,
    },
  });

  await mirrorEntitlementToKV(toEntitlement(updated));
}

export async function syncStripeDataToKV(customerId: string): Promise<Entitlement> {
  const stripe = getStripe();

  const [subscriptions, existing] = await Promise.all([
    stripe.subscriptions.list({
      customer: customerId,
      status: "all",
      limit: 20,
    }),
    readEntitlementByCustomerId(customerId),
  ]);

  const lifetime = Boolean(existing?.lifetime);
  const premium = lifetime || hasPremiumSubscription(subscriptions.data);
  const updated = await db.billingAccount.update({
    where: { stripeCustomerId: customerId },
    data: {
      entitlementLevel: lifetime ? "lifetime" : premium ? "premium" : "free",
      premium,
      lifetime,
    },
    select: {
      stripeCustomerId: true,
      entitlementLevel: true,
      premium: true,
      lifetime: true,
      updatedAt: true,
    },
  });

  const entitlement = toEntitlement(updated);
  await mirrorEntitlementToKV(entitlement);

  return entitlement;
}
