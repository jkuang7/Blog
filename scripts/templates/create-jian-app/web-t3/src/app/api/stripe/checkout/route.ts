import { NextResponse } from "next/server";
import { z } from "zod";

import { createCheckoutSession } from "~/server/billing/checkout";
import { AuthGuardError, requireAuth } from "~/server/auth/guards";
import { limitRequest } from "~/server/rate-limit/limiter";

export const runtime = "nodejs";

const schema = z.object({
  plan: z.enum(["monthly", "lifetime"]),
});

export async function POST(request: Request) {
  const limit = await limitRequest("ratelimit:checkout");
  if (!limit.success) {
    return NextResponse.json({ error: "Rate limit exceeded" }, { status: 429 });
  }

  try {
    const auth = await requireAuth();
    const payload = schema.parse(await request.json());

    const url = await createCheckoutSession({
      userId: auth.id,
      email: auth.email,
      plan: payload.plan,
      requestUrl: request.url,
    });

    return NextResponse.json({ url });
  } catch (error) {
    if (error instanceof AuthGuardError) {
      return NextResponse.json({ error: error.message }, { status: error.status });
    }

    if (error instanceof z.ZodError) {
      return NextResponse.json({ error: "Invalid checkout payload" }, { status: 400 });
    }

    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Unable to create checkout session" },
      { status: 500 },
    );
  }
}
