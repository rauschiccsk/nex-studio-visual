// DEV-151 — one note of „Kde sme skončili“ as a person reads it: „„Čakáme na Tibora.“ — Zoltán Rausch, 22.10.2026 19:15“.
import type { VersionNote } from "@/services/api/versionNotes";
import { formatDateTime } from "@/utils/format";

export function noteLine(note: Pick<VersionNote, "text" | "author_name" | "created_at">): string {
  return `„${note.text}“ — ${note.author_name ?? "bývalý používateľ"}, ${formatDateTime(note.created_at)}`;
}
