import { env } from "~/env";
import { getAppBaseUrl, getStripe } from "~/lib/stripe";
import { ensureStripeCustomerForUser } from "~/server/billing/customer";

type Plan = "monthly" | "lifetime";

export async function createCheckoutSession(input: {
  userId: string;
  email: string;
  plan: Plan;
  requestUrl?: string;
}): Promise<string> {
  if (input.plan === "monthly" && !env.STRIPE_PRICE_MONTHLY_ID) {
    throw new Error("Monthly Stripe price is not configured");
  }

  if (input.plan === "lifetime" && !env.STRIPE_PRICE_LIFETIME_ID) {
    throw new Error("Lifetime Stripe price is not configured");
  }

  const customerId = await ensureStripeCustomerForUser({
    userId: input.userId,
    email: input.email,
  });

  const stripe = getStripe();
  const baseUrl = getAppBaseUrl(input.requestUrl);
  const successUrl = `${baseUrl}/success?plan=${input.plan}`;
  const cancelUrl = `${baseUrl}/pricing?checkout=cancelled&plan=${input.plan}`;

  const session =
    input.plan === "monthly"
      ? await stripe.checkout.sessions.create({
          mode: "subscription",
          customer: customerId,
          line_items: [{ price: env.STRIPE_PRICE_MONTHLY_ID!, quantity: 1 }],
          payment_method_collection: "always",
          subscription_data: {
            trial_period_days: Number(env.STRIPE_TRIAL_DAYS ?? 14),
            metadata: {
              userId: input.userId,
            },
          },
          metadata: {
            userId: input.userId,
            plan: input.plan,
          },
          success_url: successUrl,
          cancel_url: cancelUrl,
          allow_promotion_codes: true,
        })
      : await stripe.checkout.sessions.create({
          mode: "payment",
          customer: customerId,
          line_items: [{ price: env.STRIPE_PRICE_LIFETIME_ID!, quantity: 1 }],
          metadata: {
            userId: input.userId,
            plan: input.plan,
          },
          success_url: successUrl,
          cancel_url: cancelUrl,
          allow_promotion_codes: true,
        });

  if (!session.url) {
    throw new Error("Stripe did not return a redirect URL");
  }

  return session.url;
}
