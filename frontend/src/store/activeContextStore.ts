/**
 * Active project + version context — Zustand store with localStorage
 * persistence.
 *
 * Two independent slots:
 *
 * * ``selectedProject`` — Director's explicit "Selected" pin from
 *   :file:`pages/ProjectsPage.tsx` (Pin icon per row). Once pinned,
 *   every feature in NEX Studio that needs a project anchor reads
 *   this — agent terminals (Designer / Implementer / Auditor), and
 *   any future "needs a project" page. Persisted; survives F5.
 *
 * * ``selectedVersion`` — sub-selection auto-set by
 *   :func:`useActiveContextSync` when the user opens a
 *   ``VersionDetailPage`` / pipeline-step page. Independent of
 *   ``selectedProject`` so a feature that only needs a project (e.g.
 *   Designer terminal) is not blocked by "you haven't picked a
 *   verzia yet".
 *
 * Helper :func:`hasFullContext` returns ``true`` only when both slots
 * are populated — used by Sidebar to gate pipeline-step shortcuts
 * (Spec, Audit, TaskPlan, …) which always need a verzia anchor.
 *
 * Persisted under localStorage key ``nex-active-context``. Zustand
 * persist re-reads whatever shape is on disk; missing fields default
 * to ``null`` so prior single-slot state migrates without breakage.
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";

export interface SelectedProject {
  slug: string;
  name: string;
}

export interface SelectedVersion {
  versionId: string;
  versionNumber: string;
}

export interface ActiveContextState {
  selectedProject: SelectedProject | null;
  selectedVersion: SelectedVersion | null;

  /** Pin a project (or clear with ``null``). Clearing the project also
   *  clears any active version sub-selection — a version without its
   *  parent project is not a coherent state. */
  setSelectedProject: (p: SelectedProject | null) => void;
  /** Set the active version. Caller must ensure the version belongs to
   *  the currently selected project; this store does not enforce it. */
  setSelectedVersion: (v: SelectedVersion | null) => void;

  /** ``true`` iff both ``selectedProject`` and ``selectedVersion``
   *  are populated — required for pipeline-step navigation. */
  hasFullContext: () => boolean;
}

export const useActiveContextStore = create<ActiveContextState>()(
  persist(
    (set, get) => ({
      selectedProject: null,
      selectedVersion: null,
      setSelectedProject: (p) =>
        set({
          selectedProject: p,
          // Clearing the project implies clearing the version. Switching
          // to a different project also clears version — the previous
          // version was scoped to the previous project.
          selectedVersion: null,
        }),
      setSelectedVersion: (v) => set({ selectedVersion: v }),
      hasFullContext: () => {
        const s = get();
        return s.selectedProject !== null && s.selectedVersion !== null;
      },
    }),
    {
      name: "nex-active-context",
      partialize: (state) => ({
        selectedProject: state.selectedProject,
        selectedVersion: state.selectedVersion,
      }),
    },
  ),
);
