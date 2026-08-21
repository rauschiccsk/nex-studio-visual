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
 * Persisted under localStorage key ``nex-active-context:<username>`` — one
 * slot PER USER of this browser. Zustand persist re-reads whatever shape is
 * on disk; missing fields default to ``null`` so prior single-slot state
 * migrates without breakage.
 *
 * Why the key is scoped (v4.0.90). It used to be a single shared key, which
 * leaked: Nazar opened the cockpit and saw the Director's pinned project in
 * the sidebar, Metriky and UAT included. v4.0.33 fixed that by clearing the
 * pin on EVERY login — safe, but it also punished the far commoner case of
 * the same person coming back. A session expires, the 401 interceptor bounces
 * you to the login screen, and after signing in your project is gone and you
 * hunt for it again.
 *
 * Scoping the key fixes both: another user's pin cannot be read because it is
 * under a different key, and your own survives. The default key deliberately
 * ends in ``:anonymous`` so that anything read BEFORE the username is known
 * is empty rather than somebody else's.
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";

/** localStorage key for *username*'s pin. ``undefined`` → the anonymous slot,
 *  which is never written by a signed-in user and so is always empty. */
export function activeContextKey(username: string | undefined): string {
  return `nex-active-context:${username ?? "anonymous"}`;
}

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
      name: activeContextKey(undefined),
      partialize: (state) => ({
        selectedProject: state.selectedProject,
        selectedVersion: state.selectedVersion,
      }),
    },
  ),
);

/**
 * Point the store at *username*'s own slot and re-read it.
 *
 * Called from ``App`` whenever the signed-in user changes (including the
 * transition to ``undefined`` on logout). Without the ``rehydrate()`` the new
 * key would only take effect on the next write, so the previous user's
 * in-memory state would stay on screen — the very leak this scoping removes.
 */
export function scopeActiveContextTo(username: string | undefined): Promise<void> {
  const key = activeContextKey(username);
  if (useActiveContextStore.persist.getOptions().name === key) {
    return Promise.resolve(); // already this user — a rehydrate here would be churn
  }
  // Look BEFORE switching. The order here is not cosmetic and both halves were bugs:
  //
  //  - clearing after ``setOptions`` writes the empty state straight into the NEW key
  //    through the persist middleware, destroying the pin we were about to read back;
  //  - not clearing at all leaves the previous user's selection in memory, because
  //    ``rehydrate()`` merges what it finds and an empty slot finds nothing.
  //
  // So: read the slot first, then either restore from it or clear.
  const stored = typeof window === "undefined" ? null : window.localStorage.getItem(key);
  useActiveContextStore.persist.setOptions({ name: key });

  if (stored === null) {
    useActiveContextStore.setState({ selectedProject: null, selectedVersion: null });
    return Promise.resolve();
  }
  // Returned, not swallowed: rehydration is async, so a caller that needs the pin to be
  // back (a test, or any future code reading it right after a user switch) can await.
  // ``App`` does not — React re-renders when the store lands.
  return Promise.resolve(useActiveContextStore.persist.rehydrate());
}
