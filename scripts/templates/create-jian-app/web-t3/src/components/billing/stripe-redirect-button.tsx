"use client";

import { startTransition, useState } from "react";

import { Button } from "~/components/ui/button";

export function StripeRedirectButton(props: {
  endpoint: "/api/stripe/checkout" | "/api/stripe/portal" | "/api/stripe/customer-portal";
  label: string;
  pendingLabel: string;
  payload?: Record<string, unknown>;
  variant?: "default" | "outline" | "ghost";
}) {
  const [error, setError] = useState<string | null>(null);
  const [isPending, setIsPending] = useState(false);

  function handleClick() {
    setError(null);
    setIsPending(true);

    startTransition(() => {
      void (async () => {
        try {
          const response = await fetch(props.endpoint, {
            method: "POST",
            headers: {
              "content-type": "application/json",
            },
            body: props.payload ? JSON.stringify(props.payload) : "{}",
          });

          const body = (await response.json()) as { error?: string; url?: string };
          if (!response.ok || !body.url) {
            throw new Error(body.error ?? "Unable to open Stripe flow.");
          }

          window.location.assign(body.url);
        } catch (caughtError) {
          setError(caughtError instanceof Error ? caughtError.message : "Unable to open Stripe.");
          setIsPending(false);
        }
      })();
    });
  }

  return (
    <div className="flex flex-col gap-2">
      <Button onClick={handleClick} disabled={isPending} variant={props.variant}>
        {isPending ? props.pendingLabel : props.label}
      </Button>
      {error ? <p className="text-sm text-amber-300">{error}</p> : null}
    </div>
  );
}
