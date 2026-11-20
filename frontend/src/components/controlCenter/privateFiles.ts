// DEV-44: one wording for the attach button (answer box + conversation) and the list of files in `private/`.
import type { PipelineBoard } from "@/services/api/pipeline";
import type { PrivateFile } from "@/services/api/projectFiles";
import { formatShortDateTime } from "@/utils/format";

export const ATTACH_LABEL = "Priložiť súbor";
export const ATTACHING_LABEL = "Nahrávam…";
export const ATTACH_HINT =
  "Súbor sa uloží do projektu, do priečinka private/ — git ho nevidí. Do správy sa doplní, kde ho Staviteľ nájde.";
export const FILES_TITLE = "Súbory pre Staviteľa";
export const FILES_HINT = "Ležia v projekte v priečinku private/, mimo gitu. Agent ich nájde podľa cesty v správe.";
export const DELETE_LABEL = "Zmazať";
export const CONFIRM_DELETE_LABEL = "Naozaj zmazať";
const BY_AGENT_LABEL = "vytvoril Staviteľ";
export const CONFIRM_DELETE_FOLDER_LABEL = "Naozaj zmazať priečinok";
export const SHOW_FILES_LABEL = "Rozbaliť zoznam súborov pre Staviteľa";
export const HIDE_FILES_LABEL = "Zbaliť zoznam súborov pre Staviteľa";
/** DEV-441 — whether the list is folded, kept in the browser across screens and versions. */
export const FILES_FOLDED_KEY = "nsv.private-files.folded";

/** DEV-443 — one line of the list: a file, or a folder of files the agent made itself (its working copy). */
export type PrivateFilesRow =
  | { kind: "file"; file: PrivateFile }
  | { kind: "folder"; path: string; files: PrivateFile[] };

/** DEV-443 — what a person attached stays a line of its own; what the agent put into a folder of `private/` is one
 *  line per folder (20.11.2026 its unpacked copy of a library made 190 lines). The agent's file lying right in
 *  `private/` stays a line of its own. */
export function groupPrivateFiles(files: PrivateFile[]): PrivateFilesRow[] {
  const rows: PrivateFilesRow[] = [];
  const folders = new Map<string, Extract<PrivateFilesRow, { kind: "folder" }>>();
  for (const file of files) {
    const inside = file.path.split("/");
    if (file.uploaded_by || inside.length < 3) {
      rows.push({ kind: "file", file });
      continue;
    }
    const path = `${inside[0]}/${inside[1]}/`;
    let folder = folders.get(path);
    if (!folder) {
      folder = { kind: "folder", path, files: [] };
      folders.set(path, folder);
      rows.push(folder);
    }
    folder.files.push(file);
  }
  return rows;
}

/** „počet súborov: 190 · vytvoril Staviteľ“ — without a plural rule. */
export function folderLabel(row: Extract<PrivateFilesRow, { kind: "folder" }>): string {
  return `počet súborov: ${row.files.length} · ${BY_AGENT_LABEL}`;
}

/** The text with the line for the attached file — on its own line after what is already written. */
export function withAttachedLine(text: string, line: string): string {
  const trimmed = text.replace(/\s+$/, "");
  return trimmed ? `${trimmed}\n${line}` : line;
}

/** How many attach/delete records the board carries — the list reloads when this moves (any tab, any user). */
export function privateFileEvents(board: PipelineBoard | null): number {
  return (board?.recent_messages ?? []).filter((m) => !!(m.payload as Record<string, unknown> | null)?.private_file)
    .length;
}

/** „nahral alex 09.10. 18:52“ · „vytvoril agent“ */
export function uploadedLabel(by: string | null | undefined, at: string | null | undefined): string {
  if (!by) return BY_AGENT_LABEL;
  if (!at) return `nahral ${by}`;
  const when = formatShortDateTime(at);
  return `nahral ${by} ${when}`;
}
