import { describe, expect, it } from "vitest";

import { ENGINE_VERSION } from "../../src/index.js";

describe("engine scaffold", () => {
  it("exposes a version", () => {
    expect(ENGINE_VERSION).toBe("0.1.0");
  });
});
