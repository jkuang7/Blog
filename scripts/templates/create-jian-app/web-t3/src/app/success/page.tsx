import Link from "next/link";
import { redirect } from "next/navigation";

import { Button } from "~/components/ui/button";
import { findStripeCustomerIdForUser } from "~/server/billing/billing-account-adapter";
import { syncStripeDataToKV } from "~/server/billing/syncStripeDataToKV";
import { getSessionUser } from "~/server/auth/session";

type SearchParams = Promise<{ plan?: string }>;

export default async function SuccessPage({ searchParams }: { searchParams: SearchParams }) {
  const user = await getSessionUser();
  if (!user) {
    redirect("/login?next=/success");
  }

  const params = await searchParams;
  const stripeCustomerId = await findStripeCustomerIdForUser(user.id);

  let syncState: "success" | "processing" = "processing";
  if (stripeCustomerId) {
    try {
      await syncStripeDataToKV(stripeCustomerId);
      syncState = "success";
    } catch {
      syncState = "processing";
    }
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-4xl flex-col gap-8 px-6 py-16">
      <div className="space-y-3">
        <p className="text-sm uppercase tracking-[0.3em] text-sky-300">Checkout Success</p>
        <h1 className="text-4xl font-semibold text-white">
          {syncState === "success" ? "Entitlement sync complete" : "Payment is processing"}
        </h1>
        <p className="text-zinc-300">
          {syncState === "success"
            ? `${params.plan ? `Plan: ${params.plan}. ` : ""}Premium access should be ready if the payment is valid.`
            : "Stripe sync is still pending. Access remains fail-closed until sync succeeds."}
        </p>
      </div>

      <div className="flex flex-wrap gap-4">
        <Button asChild>
          <Link href="/dashboard">Open dashboard</Link>
        </Button>
        <Button asChild variant="outline">
          <Link href="/premium">Try premium route</Link>
        </Button>
      </div>
    </main>
  );
}
