"use client";

import { startTransition, useState } from "react";

import { createSupabaseBrowserClient } from "~/lib/supabase/client";
import { Button } from "~/components/ui/button";

export function GoogleSignInButton({ nextPath = "/dashboard" }: { nextPath?: string }) {
  const [error, setError] = useState<string | null>(null);
  const [isPending, setIsPending] = useState(false);

  function handleClick() {
    setError(null);
    setIsPending(true);

    startTransition(() => {
      void (async () => {
        try {
          const supabase = createSupabaseBrowserClient();
          const redirectTo = `${window.location.origin}/auth/callback?next=${encodeURIComponent(nextPath)}`;
          const { error: signInError } = await supabase.auth.signInWithOAuth({
            provider: "google",
            options: {
              redirectTo,
            },
          });

          if (signInError) {
            setError(signInError.message);
          }
        } catch (caughtError) {
          setError(caughtError instanceof Error ? caughtError.message : "Unable to start sign-in.");
        } finally {
          setIsPending(false);
        }
      })();
    });
  }

  return (
    <div className="flex flex-col gap-3">
      <Button onClick={handleClick} disabled={isPending} size="lg">
        {isPending ? "Opening Google..." : "Continue with Google"}
      </Button>
      {error ? <p className="text-sm text-amber-300">{error}</p> : null}
    </div>
  );
}
