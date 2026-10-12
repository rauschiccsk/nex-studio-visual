/**
 * DEV-82 — how the signed-in person arranged the screen: read from the server once per session, changed here and
 * kept there, so it is the same on every computer and every person has their own.
 *
 * A change shows at once and is sent to the server; when the server refuses it, the previous layout comes back — the
 * screen never shows a layout that was not kept. Until the layout arrives the screen keeps its defaults.
 */

import { create } from "zustand";

import { getUserLayout, saveUserLayout, type UserLayout, type UserLayoutChanges } from "@/services/api/userLayout";

export interface UserLayoutState {
  /** ``null`` until read from the server. */
  layout: UserLayout | null;
  load: () => Promise<void>;
  save: (changes: UserLayoutChanges) => Promise<void>;
  /** Another person signs in — nothing of the previous one's layout stays. */
  reset: () => void;
}

export const useUserLayoutStore = create<UserLayoutState>()((set, get) => ({
  layout: null,
  load: async () => {
    try {
      set({ layout: await getUserLayout() });
    } catch {
      /* without it the screen keeps its defaults — the layout is a convenience, never a reason to break the screen */
    }
  },
  save: async (changes) => {
    const before = get().layout;
    if (before) set({ layout: { ...before, ...changes } });
    try {
      set({ layout: await saveUserLayout(changes) });
    } catch {
      set({ layout: before });
    }
  },
  reset: () => set({ layout: null }),
}));
