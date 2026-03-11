import { redirect } from "next/navigation";

import { AuthGuardError, requirePremiumAccess } from "~/server/auth/guards";

export default async function PremiumPage() {
  try {
    const { auth, entitlement } = await requirePremiumAccess();

    return (
      <main className="mx-auto flex min-h-screen max-w-4xl flex-col gap-6 px-6 py-16">
        <p className="text-sm uppercase tracking-[0.3em] text-sky-300">Premium</p>
        <h1 className="text-4xl font-semibold text-white">Premium route unlocked</h1>
        <p className="text-zinc-300">
          Signed in as {auth.email}. Current entitlement: {entitlement.level}.
        </p>
      </main>
    );
  } catch (error) {
    if (error instanceof AuthGuardError) {
      redirect(error.status === 401 ? "/login?next=/premium" : "/pricing");
    }

    throw error;
  }
}
