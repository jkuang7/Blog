import { getStripe } from "~/lib/stripe";
import {
  findStripeCustomerIdForUser,
  upsertStripeCustomerIdForUser,
} from "~/server/billing/billing-account-adapter";

export async function ensureStripeCustomerForUser(input: {
  userId: string;
  email: string;
}): Promise<string> {
  const existingStripeCustomerId = await findStripeCustomerIdForUser(input.userId);
  if (existingStripeCustomerId) {
    return existingStripeCustomerId;
  }

  const stripe = getStripe();
  const email = input.email.trim();
  const customer = await stripe.customers.create({
    metadata: {
      userId: input.userId,
    },
    ...(email ? { email } : {}),
  });

  await upsertStripeCustomerIdForUser({
    userId: input.userId,
    stripeCustomerId: customer.id,
  });

  return customer.id;
}
