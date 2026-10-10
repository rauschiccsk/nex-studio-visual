// DEV-52 — screenshots pasted (Ctrl+V, Shift+Insert), dropped or picked into a question for Poradca. The limits
// come from the backend (PoradcaStatus) — the screen refuses early, the backend decides (it reads the type from the
// content, not from what the browser says).

import type { PoradcaImageUpload } from "@/services/api/poradca";

export interface ImageLimits {
  attachment_max_bytes: number;
  attachments_max_count: number;
  attachments_max_total_bytes: number;
  attachment_types?: string[];
}

export interface PendingImage {
  key: string;
  file: File;
  /** Object URL for the preview — revoked when the image is removed or sent. */
  url: string;
}

/**
 * Ctrl+V brought neither an image nor text: the clipboard is empty — typically a screenshot taken on another
 * computer than the one the cockpit runs on (a remote desktop does not always carry images over). Said, not silent.
 */
export const EMPTY_CLIPBOARD =
  "Ctrl+V nedonieslo obrázok ani text — schránka je prázdna. Snímku urob na počítači, kde beží kokpit (vo " +
  "vzdialenej ploche priamo v nej, napríklad klávesom PrtScn), alebo ju vyber tlačidlom s obrázkom.";

/** Whether a paste carried text — a text paste is the field's own business, never a reason to say anything. */
export function carriesText(transfer: DataTransfer | null): boolean {
  const types = Array.from(transfer?.types ?? []);
  return types.includes("text/plain") || types.includes("text/html") || types.includes("text/uri-list");
}

const mb = (bytes: number) => `${Math.round(bytes / (1024 * 1024))} MB`;

/** The image files a paste or a drop carries — text and other files are left alone. */
export function imagesIn(transfer: DataTransfer | null): File[] {
  if (!transfer) return [];
  const files = Array.from(transfer.files ?? []);
  const fromItems = Array.from(transfer.items ?? [])
    .filter((item) => item.kind === "file")
    .map((item) => item.getAsFile())
    .filter((f): f is File => f !== null);
  return (files.length ? files : fromItems).filter((f) => f.type.startsWith("image/"));
}

/** Add what fits; say why the rest does not. A pasted screenshot has no name — it gets one. */
export function addImages(
  current: PendingImage[],
  incoming: File[],
  limits: ImageLimits,
): { images: PendingImage[]; refused: string | null } {
  const images = [...current];
  let refused: string | null = null;
  let total = images.reduce((sum, i) => sum + i.file.size, 0);
  for (const raw of incoming) {
    if (!(limits.attachment_types ?? []).includes(raw.type)) {
      refused = `„${raw.name || "obrázok"}“ nie je PNG, JPEG, WebP ani GIF.`;
      continue;
    }
    if (raw.size > limits.attachment_max_bytes) {
      refused = `Obrázok má ${mb(raw.size)} — jeden môže mať najviac ${mb(limits.attachment_max_bytes)}.`;
      continue;
    }
    if (images.length >= limits.attachments_max_count) {
      refused = `K jednej otázke sa dá priložiť najviac ${limits.attachments_max_count} obrázkov.`;
      break;
    }
    if (total + raw.size > limits.attachments_max_total_bytes) {
      refused = `Obrázky jednej otázky môžu mať spolu najviac ${mb(limits.attachments_max_total_bytes)}.`;
      break;
    }
    const file = raw.name && raw.name !== "image.png" ? raw : renamed(raw, images.length + 1);
    images.push({ key: `${Date.now()}-${images.length}-${file.name}`, file, url: URL.createObjectURL(file) });
    total += raw.size;
  }
  return { images, refused };
}

function renamed(file: File, n: number): File {
  const ext = file.type.split("/")[1] === "jpeg" ? "jpg" : file.type.split("/")[1];
  const stamp = new Date().toLocaleString("sk-SK").replace(/[/:]/g, "-");
  return new File([file], `Snímka ${n} ${stamp}.${ext}`, { type: file.type });
}

/** The image as the backend takes it — base64 without the `data:` prefix. */
export function toUpload(file: File): Promise<PoradcaImageUpload> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const url = String(reader.result ?? "");
      resolve({ name: file.name, data: url.slice(url.indexOf(",") + 1) });
    };
    reader.onerror = () => reject(reader.error ?? new Error("Obrázok sa nepodarilo prečítať."));
    reader.readAsDataURL(file);
  });
}
