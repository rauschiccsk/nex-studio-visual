/**
 * Session-scoped UI state — Knowledge Base browse continuity.
 *
 * Ported (subset) from NEX Command `frontend/src/store/sessionStore.ts`
 * per Director mandate 2026-05-07 (M1.D milestone).
 *
 * Persists ``knowledgeCategory`` + ``knowledgeDocPath`` so the user
 * returns to the same KB document after a page reload or tab switch.
 * NEX Command's full sessionStore also tracks ``projectId`` / ``chatId``
 * / ``activeTab`` — these are not ported because NEX Studio uses
 * route params + ``activeContextStore`` for the same purpose.
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";

interface SessionState {
  knowledgeCategory: string | null;
  knowledgeDocPath: string | null;

  setKnowledgeCategory: (cat: string | null) => void;
  setKnowledgeDocPath: (path: string | null) => void;
  clear: () => void;
}

export const useSessionStore = create<SessionState>()(
  persist(
    (set) => ({
      knowledgeCategory: null,
      knowledgeDocPath: null,

      setKnowledgeCategory: (cat) => set({ knowledgeCategory: cat }),
      setKnowledgeDocPath: (path) => set({ knowledgeDocPath: path }),
      clear: () =>
        set({
          knowledgeCategory: null,
          knowledgeDocPath: null,
        }),
    }),
    { name: "nex-studio-session" },
  ),
);
