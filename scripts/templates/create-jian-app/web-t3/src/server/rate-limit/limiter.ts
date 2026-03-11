import { headers } from "next/headers";

import { createSlidingWindowRatelimit } from "~/lib/upstash";
import { resolveIp } from "~/server/rate-limit/resolve-ip";

type LimitResult = {
  success: boolean;
  remaining: number;
  reset: number;
};

const FALLBACK: LimitResult = {
  success: true,
  remaining: 999,
  reset: 0,
};

export async function limitRequest(prefix: string): Promise<LimitResult> {
  try {
    const hdrs = await headers();
    const forwardedFor = hdrs.get("x-forwarded-for");
    const ip = resolveIp(forwardedFor);
    const ratelimit = createSlidingWindowRatelimit(prefix);
    const result = await ratelimit.limit(ip);

    return {
      success: result.success,
      remaining: result.remaining,
      reset: result.reset,
    };
  } catch {
    return FALLBACK;
  }
}
