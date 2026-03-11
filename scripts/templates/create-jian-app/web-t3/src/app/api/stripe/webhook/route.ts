import { NextResponse } from "next/server";
import type Stripe from "stripe";

import { env } from "~/env";
import { isStripeWebhookEventProcessed, recordStripeWebhookEvent } from "~/server/billing/webhook-event-adapter";
import { constructStripeEventFromRawBody } from "~/server/billing/webhook";
import { setLifetimeEntitlementFlag, syncStripeDataToKV } from "~/server/billing/syncStripeDataToKV";

export const runtime = "nodejs";

function asId(value: string | { id?: string } | null): string | null {
  if (!value) {
    return null;
  }
  if (typeof value === "string") {
    return value;
  }
  return value.id ?? null;
}

async function processEvent(event: Stripe.Event): Promise<void> {
  const customerId = (() => {
    switch (event.type) {
      case "customer.subscription.created":
      case "customer.subscription.updated":
      case "customer.subscription.deleted":
        return asId((event.data.object as Stripe.Subscription).customer);
      case "checkout.session.completed":
        return asId((event.data.object as Stripe.Checkout.Session).customer);
      default:
        return null;
    }
  })();

  switch (event.type) {
    case "checkout.session.completed": {
      const session = event.data.object as Stripe.Checkout.Session;
      if (session.mode === "payment" && customerId) {
        await setLifetimeEntitlementFlag(customerId);
      }
      break;
    }
    case "customer.subscription.created":
    case "customer.subscription.updated":
    case "customer.subscription.deleted":
      break;
    default:
      return;
  }

  if (customerId) {
    await syncStripeDataToKV(customerId);
  }
}

export async function POST(request: Request) {
  if (!env.STRIPE_WEBHOOK_SECRET) {
    return NextResponse.json(
      { error: "STRIPE_WEBHOOK_SECRET is not configured" },
      { status: 500 },
    );
  }

  const signature = request.headers.get("stripe-signature");
  if (!signature) {
    return NextResponse.json({ error: "Missing Stripe signature" }, { status: 400 });
  }

  const body = await request.text();

  let event: Stripe.Event;
  try {
    event = constructStripeEventFromRawBody({
      body,
      signature,
      webhookSecret: env.STRIPE_WEBHOOK_SECRET,
    });
  } catch (error) {
    return NextResponse.json(
      {
        error: `Webhook signature verification failed: ${
          error instanceof Error ? error.message : "unknown"
        }`,
      },
      { status: 400 },
    );
  }

  if (await isStripeWebhookEventProcessed(event.id)) {
    return NextResponse.json({ received: true, duplicate: true });
  }

  try {
    await recordStripeWebhookEvent({
      eventId: event.id,
      eventType: event.type,
      customerId:
        event.type === "checkout.session.completed"
          ? asId((event.data.object as Stripe.Checkout.Session).customer)
          : event.type.startsWith("customer.subscription.")
            ? asId((event.data.object as Stripe.Subscription).customer)
            : null,
    });

    await processEvent(event);
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Webhook processing failed" },
      { status: 500 },
    );
  }

  return NextResponse.json({ received: true });
}
