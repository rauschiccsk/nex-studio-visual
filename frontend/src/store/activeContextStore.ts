/**
 * Active project + version context — Zustand store with localStorage
 * persistence.
 *
 * Remembers the last project/version the user was working on so the
 * sidebar can offer one-click navigation to pipeline steps
 * (``Zákaznícka špecifikácia``, ``Vývojová dokumentácia``…) from any
 * screen — including Dashboard, where no slug/versionId is in the URL.
 *
 * Context is set on mount of ``VersionDetailPage`` and every
 * ``pages/step/*`` page via ``useParams``. It is cleared with
 * :func:`clearActiveContext` when the target verzia disappears
 * (e.g. backend returns 404 on navigation).
 *
 * Persisted under localStorage key ``nex-active-context`` so the
 * context survives F5 / browser restart. Values are non-sensitive
 * (slug + UUID + display names) — no tokens, no secrets.
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";

export interface ActiveContext {
  slug: string;
  versionId: string;
  projectName: string;
  versionNumber: string;
}

export interface ActiveContextState {
  /** ``null`` when the user has never opened a version in this browser. */
  context: ActiveContext | null;
  setActiveContext: (ctx: ActiveContext) => void;
  clearActiveContext: () => void;
}

export const useActiveContextStore = create<ActiveContextState>()(
  persist(
    (set) => ({
      context: null,
      setActiveContext: (ctx) => set({ context: ctx }),
      clearActiveContext: () => set({ context: null }),
    }),
    {
      name: "nex-active-context",
      partialize: (state) => ({ context: state.context }),
    },
  ),
);
