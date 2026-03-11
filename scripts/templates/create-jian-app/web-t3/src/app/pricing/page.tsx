import Link from "next/link";

import { Button } from "~/components/ui/button";
import { StripeRedirectButton } from "~/components/billing/stripe-redirect-button";
import { getSessionUser } from "~/server/auth/session";

const PLANS = [
  {
    name: "Starter",
    price: "Free",
    description: "Auth, dashboard, tRPC, Prisma, and the hardened harness contract.",
    cta: null,
    bullets: ["Supabase auth", "Generated context pack", "Upstash rate limiting"],
  },
  {
    name: "Pro Monthly",
    price: "$29/mo",
    description: "Subscription checkout wired through Stripe Checkout and webhook sync.",
    cta: "monthly",
    bullets: ["Stripe checkout", "Customer portal", "Webhook + /success sync"],
  },
  {
    name: "Lifetime",
    price: "$399 once",
    description: "One-time payment path with lifetime entitlement stored in KV.",
    cta: "lifetime",
    bullets: ["One-time checkout", "Lifetime entitlement flag", "Same protected premium route"],
  },
] as const;

export default async function PricingPage() {
  const user = await getSessionUser();

  return (
    <main className="mx-auto flex min-h-screen w-full max-w-6xl flex-col gap-12 px-6 py-16">
      <div className="space-y-4">
        <p className="text-sm uppercase tracking-[0.3em] text-sky-300">Pricing</p>
        <h1 className="text-5xl font-semibold tracking-tight text-white">Starter SaaS billing shell</h1>
        <p className="max-w-2xl text-lg leading-8 text-zinc-300">
          This scaffold includes Stripe Checkout, Customer Portal, webhook processing, eager
          entitlement sync on <code>/success</code>, and Upstash-backed billing state.
        </p>
      </div>

      <section className="grid gap-5 md:grid-cols-3">
        {PLANS.map((plan) => (
          <div
            key={plan.name}
            className="flex flex-col gap-5 rounded-3xl border border-white/10 bg-white/5 p-6 backdrop-blur"
          >
            <div className="space-y-2">
              <h2 className="text-2xl font-semibold text-white">{plan.name}</h2>
              <p className="text-3xl text-sky-300">{plan.price}</p>
              <p className="text-sm leading-6 text-zinc-300">{plan.description}</p>
            </div>

            <ul className="space-y-2 text-sm text-zinc-200">
              {plan.bullets.map((bullet) => (
                <li key={bullet}>• {bullet}</li>
              ))}
            </ul>

            {plan.cta ? (
              user ? (
                <StripeRedirectButton
                  endpoint="/api/stripe/checkout"
                  label={`Choose ${plan.name}`}
                  pendingLabel="Opening checkout..."
                  payload={{ plan: plan.cta }}
                />
              ) : (
                <Button asChild>
                  <Link href={`/login?next=/pricing`}>Sign in to continue</Link>
                </Button>
              )
            ) : (
              <Button asChild variant="outline">
                <Link href={user ? "/dashboard" : "/login"}>{user ? "Open dashboard" : "Get started"}</Link>
              </Button>
            )}
          </div>
        ))}
      </section>
    </main>
  );
}
