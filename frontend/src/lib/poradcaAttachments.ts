// DEV-52 — screenshots pasted (Ctrl+V, Shift+Insert), dropped or picked into a question for Poradca; DEV-389 — and
// documents (Markdown, text, PDF), which the agent reads whole. The limits and types come from the backend
// (PoradcaStatus) — the screen refuses early, the backend decides (it reads the type from the content, not from what
// the browser says; a browser often gives a text file no type at all, so a document is picked by its name here).

import type { PoradcaImageUpload } from "@/services/api/poradca";
import { formatDateTime } from "@/utils/format";

export interface AttachmentLimits {
  attachment_max_bytes: number;
  attachments_max_count: number;
  attachments_max_total_bytes: number;
  attachment_types?: string[];
  /** DEV-389 — the names a document is picked by (`.md`, `.txt`, `.pdf` …). */
  attachment_extensions?: string[];
}

export interface PendingAttachment {
  key: string;
  file: File;
  /** Object URL for an image's preview — revoked when it is removed or sent; `null` for a document. */
  url: string | null;
}

/** What a refusal names as accepted — the backend's sentence (attachments.ACCEPTED_WORDS). */
const ACCEPTED_WORDS = "obrázok (PNG, JPEG, WebP, GIF) ani dokument (Markdown, text v UTF-8, PDF)";

/**
 * Ctrl+V brought neither an image nor text: the clipboard is empty — typically a screenshot taken on another
 * computer than the one the cockpit runs on (a remote desktop does not always carry images over). Said, not silent.
 */
export const EMPTY_CLIPBOARD =
  "Ctrl+V nedonieslo obrázok ani text — schránka je prázdna. Snímku urob na počítači, kde beží kokpit (vo " +
  "vzdialenej ploche priamo v nej, napríklad klávesom PrtScn), alebo ju vyber tlačidlom s kancelárskou sponkou.";

/** Whether a paste carried text — a text paste is the field's own business, never a reason to say anything. */
export function carriesText(transfer: DataTransfer | null): boolean {
  const types = Array.from(transfer?.types ?? []);
  return types.includes("text/plain") || types.includes("text/html") || types.includes("text/uri-list");
}

const mb = (bytes: number) => `${Math.round(bytes / (1024 * 1024))} MB`;

/** Every file a paste or a drop carries. */
export function filesIn(transfer: DataTransfer | null): File[] {
  if (!transfer) return [];
  const files = Array.from(transfer.files ?? []);
  const fromItems = Array.from(transfer.items ?? [])
    .filter((item) => item.kind === "file")
    .map((item) => item.getAsFile())
    .filter((f): f is File => f !== null);
  return files.length ? files : fromItems;
}

/** Whether a drag carries files — what they are is known only on the drop, so the drop itself decides. */
export function carriesFiles(transfer: DataTransfer | null): boolean {
  return Array.from(transfer?.items ?? []).some((item) => item.kind === "file");
}

/** DEV-389: a document — by its name, since a browser often gives a text file no type. */
function isDocument(file: File, limits: AttachmentLimits): boolean {
  const name = file.name.toLowerCase();
  return (limits.attachment_extensions ?? []).some((extension) => name.endsWith(extension));
}

/** An image or a document the backend takes. */
export function fits(file: File, limits: AttachmentLimits): boolean {
  return (
    (file.type.startsWith("image/") && (limits.attachment_types ?? []).includes(file.type)) || isDocument(file, limits)
  );
}

/** Add what fits; say why the rest does not. A pasted screenshot has no name — it gets one. */
export function addAttachments(
  current: PendingAttachment[],
  incoming: File[],
  limits: AttachmentLimits,
): { attachments: PendingAttachment[]; refused: string | null } {
  const attachments = [...current];
  let refused: string | null = null;
  let total = attachments.reduce((sum, a) => sum + a.file.size, 0);
  for (const raw of incoming) {
    if (!fits(raw, limits)) {
      refused = `„${raw.name || "súbor"}“ nie je ${ACCEPTED_WORDS}.`;
      continue;
    }
    if (raw.size > limits.attachment_max_bytes) {
      refused = `„${raw.name || "Obrázok"}“ má ${mb(raw.size)} — jeden súbor môže mať najviac ${mb(limits.attachment_max_bytes)}.`;
      continue;
    }
    if (attachments.length >= limits.attachments_max_count) {
      refused = `K jednej otázke sa dá priložiť najviac ${limits.attachments_max_count} súborov.`;
      break;
    }
    if (total + raw.size > limits.attachments_max_total_bytes) {
      refused = `Prílohy jednej otázky môžu mať spolu najviac ${mb(limits.attachments_max_total_bytes)}.`;
      break;
    }
    const document = isDocument(raw, limits);
    const file = document || (raw.name && raw.name !== "image.png") ? raw : renamed(raw, attachments.length + 1);
    attachments.push({
      key: `${Date.now()}-${attachments.length}-${file.name}`,
      file,
      url: document ? null : URL.createObjectURL(file),
    });
    total += raw.size;
  }
  return { attachments, refused };
}

function renamed(file: File, n: number): File {
  const ext = file.type.split("/")[1] === "jpeg" ? "jpg" : file.type.split("/")[1];
  const stamp = formatDateTime(new Date()).replace(/:/g, "-");
  return new File([file], `Snímka ${n} ${stamp}.${ext}`, { type: file.type });
}

/** The file as the backend takes it — base64 without the `data:` prefix. */
export function toUpload(file: File): Promise<PoradcaImageUpload> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const url = String(reader.result ?? "");
      resolve({ name: file.name, data: url.slice(url.indexOf(",") + 1) });
    };
    reader.onerror = () => reject(reader.error ?? new Error("Súbor sa nepodarilo prečítať."));
    reader.readAsDataURL(file);
  });
}
