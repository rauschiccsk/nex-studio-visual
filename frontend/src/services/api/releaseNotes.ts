/**
 * API client for ``/api/v1/release-notes`` — the *Aktualizácie* changelog.
 *
 * Backend: :file:`backend/api/routes/release_notes.py` (public, no auth). Each
 * entry is one shipped version's user-facing release notes, newest first.
 */

import { api } from "@/services/api";

export interface ReleaseNote {
  /** Version directory name, e.g. ``"v0.9.0"`` — drives the card heading. */
  version: string;
  /** ISO date string (``YYYY-MM-DD``) of the release, or null when unknown. */
  released_at: string | null;
  /** Raw Markdown body of the version's ``RELEASE_NOTES.md``. */
  markdown: string;
}

/** List every shipped version's release notes, newest version first. */
export async function listReleaseNotes(): Promise<ReleaseNote[]> {
  return await api.get<ReleaseNote[]>("/release-notes");
}
