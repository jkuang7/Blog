import { redirect } from "next/navigation";

import { GoogleSignInButton } from "~/components/auth/google-sign-in-button";
import { getSessionUser } from "~/server/auth/session";

export default async function LoginPage() {
  const user = await getSessionUser();
  if (user) {
    redirect("/dashboard");
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-xl flex-col justify-center gap-8 px-6 py-16">
      <div className="space-y-3">
        <p className="text-sm uppercase tracking-[0.3em] text-sky-300">Supabase Auth</p>
        <h1 className="text-4xl font-semibold text-white">Sign in to __JIAN_APP_TITLE__</h1>
        <p className="text-base text-zinc-300">
          This baseline ships with a Google OAuth entrypoint wired through Supabase SSR auth.
        </p>
      </div>
      <div className="rounded-3xl border border-white/10 bg-white/5 p-6 backdrop-blur">
        <GoogleSignInButton />
      </div>
    </main>
  );
}
