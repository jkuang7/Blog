import { describe, expect, test } from "vitest";

import { resolveIp } from "~/server/rate-limit/resolve-ip";

describe("resolveIp", () => {
  test("returns unknown when no header is present", () => {
    expect(resolveIp(null)).toBe("unknown");
  });

  test("uses the first forwarded IP", () => {
    expect(resolveIp("203.0.113.8, 10.0.0.1")).toBe("203.0.113.8");
  });

  test("trims whitespace around the IP", () => {
    expect(resolveIp(" 198.51.100.4 ")).toBe("198.51.100.4");
  });
});
