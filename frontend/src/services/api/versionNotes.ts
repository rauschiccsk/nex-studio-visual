// DEV-151 — „Kde sme skončili“: notes at a version, why its development stands and what next.
import api from "../api";
import type { components } from "./pipeline.generated";

export type VersionNote = components["schemas"]["VersionNoteRead"];

/** The version's notes, newest first. */
export function listVersionNotesApi(versionId: string): Promise<VersionNote[]> {
  return api.get<VersionNote[]>(`/versions/${versionId}/notes`);
}

/** A new note — one to three sentences; the older ones stay. */
export function addVersionNoteApi(versionId: string, text: string): Promise<VersionNote> {
  return api.post<VersionNote>(`/versions/${versionId}/notes`, { text });
}
