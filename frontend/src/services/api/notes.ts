/**
 * DEV-89 — the signed-in person's quick notes. Maps to ``backend.api.routes.user_notes`` (``/notes``); every call
 * touches only the caller's own notes.
 */

import api, { ApiError } from "@/services/api";
import type { components } from "@/services/api/pipeline.generated";

export type Note = components["schemas"]["UserNoteRead"];

export function listNotes(): Promise<Note[]> {
  return api.get<Note[]>("/notes");
}

export function createNote(body = ""): Promise<Note> {
  return api.post<Note>("/notes", { body });
}

/** Store the text written over `revision`. A note that moved on meanwhile throws {@link NoteConflict}. */
export async function saveNote(id: string, body: string, revision: number): Promise<Note> {
  try {
    return await api.put<Note>(`/notes/${id}`, { body, revision });
  } catch (e: unknown) {
    const current = e instanceof ApiError && e.status === 409 ? (e.data as { note?: Note } | null)?.note : undefined;
    if (current) throw new NoteConflict(current);
    throw e;
  }
}

/** The note changed on another device — `current` is how it is now; nothing was overwritten. */
export class NoteConflict extends Error {
  constructor(readonly current: Note) {
    super("Poznámka sa medzitým zmenila na inom zariadení.");
  }
}

export function resolveNote(id: string, resolved: boolean): Promise<Note> {
  return api.post<Note>(`/notes/${id}/resolve`, { resolved });
}

export function deleteNote(id: string): Promise<void> {
  return api.delete<void>(`/notes/${id}`);
}

/** A request REQ-N in the backlog of the project, once; the note comes back resolved and pointing to it. */
export function noteToBacklog(id: string, projectSlug: string): Promise<Note> {
  return api.post<Note>(`/notes/${id}/to-backlog`, { project_slug: projectSlug });
}
