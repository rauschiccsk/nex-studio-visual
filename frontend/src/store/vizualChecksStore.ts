/**
 * DEV-36 — which items of the Vizuál checklists the Manažér ticked, per version.
 *
 * Kept in this browser: it is his own walk through the Vizuál, not a record of the build. The count by
 * „Schváliť vizuál" reads the same store, so ticking in the thread moves it at once.
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";

/** One item's key: the message that carries the list, and the item's place in it. */
export function vizualCheckKey(seq: number, index: number): string {
  return `${seq}:${index}`;
}

interface VizualChecksState {
  /** versionId → keys of the ticked items. */
  checked: Record<string, string[]>;
  toggle: (versionId: string, key: string) => void;
}

export const useVizualChecksStore = create<VizualChecksState>()(
  persist(
    (set) => ({
      checked: {},
      toggle: (versionId, key) =>
        set((s) => {
          const current = s.checked[versionId] ?? [];
          const next = current.includes(key) ? current.filter((k) => k !== key) : [...current, key];
          return { checked: { ...s.checked, [versionId]: next } };
        }),
    }),
    { name: "nex.vizual.checks" },
  ),
);
