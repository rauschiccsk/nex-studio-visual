/**
 * API client for ``/api/v1/release-notes`` — the *Aktualizácie* changelog.
 *
 * Backend: :file:`backend/api/routes/release_notes.py` (public, no auth). Each
 * entry is one shipped version's user-facing release notes, newest first.
 *
 * The ``ReleaseNote`` shape is owned by ``nex-shared`` — it is the prop type of
 * the unified ``<ReleaseNotes>`` renderer (E1 unification) — and re-exported
 * here so this API client and the shared component can never drift apart.
 */

import { api } from "@/services/api";
import type { ReleaseNote } from "nex-shared";

export type { ReleaseNote };

/** List every shipped version's release notes, newest version first. */
export async function listReleaseNotes(): Promise<ReleaseNote[]> {
  return await api.get<ReleaseNote[]>("/release-notes");
}
