import Link from "next/link";

import { Button } from "~/components/ui/button";
import { getSessionUser } from "~/server/auth/session";

export default async function HomePage() {
  const user = await getSessionUser();

  return (
    <main className="mx-auto flex min-h-screen w-full max-w-6xl flex-col justify-center gap-16 px-6 py-16">
      <div className="grid gap-12 lg:grid-cols-[1.2fr_0.8fr] lg:items-center">
        <div className="space-y-6">
          <p className="text-sm uppercase tracking-[0.3em] text-sky-300">Web T3 Baseline</p>
          <h1 className="max-w-3xl text-5xl font-semibold tracking-tight text-white sm:text-6xl">
            __JIAN_APP_TITLE__
          </h1>
          <p className="max-w-2xl text-lg leading-8 text-zinc-300">
            Scaffolded from <code>create-t3-app@latest</code>, then hardened with Jian&apos;s
            linting, Supabase auth, Stripe billing starter, Upstash rate limiting, generated
            context artifacts, and a minimal Zustand starter.
          </p>

          <div className="flex flex-wrap items-center gap-4">
            <Button asChild size="lg">
              <Link href={user ? "/dashboard" : "/login"}>
                {user ? "Open dashboard" : "Sign in"}
              </Link>
            </Button>
            <Button asChild size="lg" variant="outline">
              <Link href="/pricing">View pricing</Link>
            </Button>
          </div>
        </div>

        <section className="rounded-[2rem] border border-white/10 bg-white/5 p-6 backdrop-blur">
          <div className="space-y-3">
            <p className="text-sm uppercase tracking-[0.3em] text-sky-300">Starter Landing</p>
            <h2 className="text-2xl font-semibold text-white">Pricing-ready from day one</h2>
            <p className="text-sm leading-6 text-zinc-300">
              The baseline includes a public pricing page, Stripe Checkout, Customer Portal, webhook
              handling, and an eager <code>/success</code> sync flow.
            </p>
          </div>
          <div className="mt-6 grid gap-3">
            {[
              "Public landing + pricing routes",
              "Monthly + lifetime Stripe checkout",
              "Premium route guard backed by KV entitlements",
            ].map((item) => (
              <div
                key={item}
                className="rounded-2xl border border-white/10 bg-black/20 px-4 py-3 text-sm text-zinc-200"
              >
                {item}
              </div>
            ))}
          </div>
        </section>
      </div>

      <section className="grid gap-4 md:grid-cols-4">
        {[
          "Next.js App Router + TypeScript",
          "tRPC + Prisma + Zod",
          "Supabase SSR auth + Zustand starter",
          "Stripe + Upstash billing shell",
        ].map((item) => (
          <div
            key={item}
            className="rounded-3xl border border-white/10 bg-white/5 p-5 text-sm text-zinc-200 backdrop-blur"
          >
            {item}
          </div>
        ))}
      </section>
    </main>
  );
}
