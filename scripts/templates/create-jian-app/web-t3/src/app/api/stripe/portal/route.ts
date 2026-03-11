import { NextResponse } from "next/server";

import { getAppBaseUrl, getStripe } from "~/lib/stripe";
import { AuthGuardError, requireAuth } from "~/server/auth/guards";
import { findStripeCustomerIdForUser } from "~/server/billing/billing-account-adapter";
import { limitRequest } from "~/server/rate-limit/limiter";

export const runtime = "nodejs";

export async function POST(request: Request) {
  const limit = await limitRequest("ratelimit:portal");
  if (!limit.success) {
    return NextResponse.json({ error: "Rate limit exceeded" }, { status: 429 });
  }

  try {
    const auth = await requireAuth();
    const stripeCustomerId = await findStripeCustomerIdForUser(auth.id);

    if (!stripeCustomerId) {
      return NextResponse.json(
        { error: "No Stripe customer is mapped to this account" },
        { status: 400 },
      );
    }

    const stripe = getStripe();
    const portal = await stripe.billingPortal.sessions.create({
      customer: stripeCustomerId,
      return_url: `${getAppBaseUrl(request.url)}/dashboard`,
    });

    return NextResponse.json({ url: portal.url });
  } catch (error) {
    if (error instanceof AuthGuardError) {
      return NextResponse.json({ error: error.message }, { status: error.status });
    }

    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Unable to open billing portal" },
      { status: 500 },
    );
  }
}
