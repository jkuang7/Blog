import type Stripe from "stripe";

import { getStripe } from "~/lib/stripe";

export function constructStripeEventFromRawBody(input: {
  body: string;
  signature: string;
  webhookSecret: string;
}): Stripe.Event {
  return getStripe().webhooks.constructEvent(input.body, input.signature, input.webhookSecret);
}
