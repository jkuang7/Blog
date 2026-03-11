import { create } from "zustand";
import { createStore } from "zustand/vanilla";

export type AppStoreState = {
  count: number;
  increment: () => void;
  reset: () => void;
};

export function createAppStore(initialCount = 0) {
  return createStore<AppStoreState>((set) => ({
    count: initialCount,
    increment: () => set((state) => ({ count: state.count + 1 })),
    reset: () => set({ count: initialCount }),
  }));
}

export const useAppStore = create<AppStoreState>((set) => ({
  count: 0,
  increment: () => set((state) => ({ count: state.count + 1 })),
  reset: () => set({ count: 0 }),
}));
