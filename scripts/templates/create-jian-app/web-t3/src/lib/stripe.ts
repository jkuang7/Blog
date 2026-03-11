import Stripe from "stripe";

import { env } from "~/env";

let stripe: Stripe | null = null;

export function getStripe(): Stripe {
  if (!env.STRIPE_SECRET_KEY) {
    throw new Error("STRIPE_SECRET_KEY is not configured");
  }

  stripe ??= new Stripe(env.STRIPE_SECRET_KEY);
  return stripe;
}

export function getAppBaseUrl(requestUrl?: string): string {
  const explicit = env.NEXT_PUBLIC_APP_URL?.trim();
  if (explicit) {
    return explicit.replace(/\/+$/, "");
  }

  if (requestUrl) {
    try {
      return new URL(requestUrl).origin;
    } catch {
      // ignore bad request urls
    }
  }

  return "http://localhost:3000";
}
