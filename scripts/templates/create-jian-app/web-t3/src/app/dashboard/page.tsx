import Link from "next/link";

import { StripeRedirectButton } from "~/components/billing/stripe-redirect-button";
import { Button } from "~/components/ui/button";
import { getCurrentUserEntitlement } from "~/server/billing/entitlements";
import { getSessionUser } from "~/server/auth/session";

export default async function DashboardPage() {
  const user = await getSessionUser();
  const entitlement = user ? await getCurrentUserEntitlement(user.id) : null;

  return (
    <main className="mx-auto flex min-h-screen max-w-4xl flex-col gap-8 px-6 py-16">
      <div className="space-y-3">
        <p className="text-sm uppercase tracking-[0.3em] text-sky-300">Dashboard</p>
        <h1 className="text-4xl font-semibold text-white">Starter baseline ready</h1>
        <p className="text-zinc-300">
          {user
            ? `Signed in as ${user.email} (${user.role}).`
            : "No Supabase session detected yet. Configure env vars and sign in to validate auth."}
        </p>
        {entitlement ? (
          <p className="text-sm text-zinc-400">
            Current entitlement: <span className="text-zinc-200">{entitlement.level}</span>
          </p>
        ) : null}
      </div>

      <div className="grid gap-4 rounded-3xl border border-white/10 bg-white/5 p-6 backdrop-blur">
        <p className="text-sm text-zinc-200">
          Generated context files live under <code>docs/llm</code> and <code>.codex</code>.
        </p>
        <p className="text-sm text-zinc-200">
          The shared quality gate is <code>pnpm run verify</code>.
        </p>
        <p className="text-sm text-zinc-200">
          Upstash rate-limit helpers are scaffolded under <code>src/server/rate-limit</code>.
        </p>
        <div className="flex flex-wrap gap-3">
          <Button asChild variant="outline">
            <Link href="/">Back home</Link>
          </Button>
          <Button asChild variant="outline">
            <Link href="/pricing">Open pricing</Link>
          </Button>
          {user ? (
            <StripeRedirectButton
              endpoint="/api/stripe/portal"
              label="Open billing portal"
              pendingLabel="Opening portal..."
              variant="ghost"
            />
          ) : null}
        </div>
      </div>
    </main>
  );
}
