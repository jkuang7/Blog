import { describe, expect, test } from "vitest";

import {
  extractBearerToken,
  roleFromEmail,
  safeNextPath,
} from "~/server/auth/auth-helpers";

describe("auth helpers", () => {
  test("extractBearerToken returns the token when present", () => {
    expect(extractBearerToken("Bearer abc123")).toBe("abc123");
    expect(extractBearerToken(" bearer xyz ")).toBe("xyz");
  });

  test("roleFromEmail detects configured admins", () => {
    expect(roleFromEmail("admin@example.com", "admin@example.com")).toBe("ADMIN");
    expect(roleFromEmail("user@example.com", "admin@example.com")).toBe("USER");
  });

  test("safeNextPath rejects invalid redirects", () => {
    expect(safeNextPath("/billing")).toBe("/billing");
    expect(safeNextPath("https://evil.example")).toBe("/dashboard");
    expect(safeNextPath("//evil.example")).toBe("/dashboard");
  });
});
