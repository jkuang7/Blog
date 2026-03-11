import { describe, expect, test } from "vitest";

import { createAppStore } from "~/lib/store/app-store";

describe("createAppStore", () => {
  test("increments and resets state", () => {
    const store = createAppStore(2);

    store.getState().increment();
    expect(store.getState().count).toBe(3);

    store.getState().reset();
    expect(store.getState().count).toBe(2);
  });
});
