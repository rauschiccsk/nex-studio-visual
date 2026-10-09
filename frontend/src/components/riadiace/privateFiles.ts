// DEV-44: one wording for the attach button (answer box + conversation) and the list of files in `private/`.
import type { PipelineBoard } from "@/services/api/pipeline";

export const ATTACH_LABEL = "Priložiť súbor";
export const ATTACHING_LABEL = "Nahrávam…";
export const ATTACH_HINT =
  "Súbor sa uloží do projektu, do priečinka private/ — git ho nevidí. Do správy sa doplní, kde ho agent nájde.";
export const FILES_TITLE = "Súbory pre agenta";
export const FILES_HINT = "Ležia v projekte v priečinku private/, mimo gitu. Agent ich nájde podľa cesty v správe.";
export const DELETE_LABEL = "Zmazať";
export const CONFIRM_DELETE_LABEL = "Naozaj zmazať";
export const BY_AGENT_LABEL = "vytvoril agent";

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

/** „nahral alex 9. 10. 18:52“ · „vytvoril agent“ */
export function uploadedLabel(by: string | null | undefined, at: string | null | undefined): string {
  if (!by) return BY_AGENT_LABEL;
  if (!at) return `nahral ${by}`;
  const when = new Date(at).toLocaleString("sk-SK", {
    day: "numeric",
    month: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
  return `nahral ${by} ${when}`;
}
