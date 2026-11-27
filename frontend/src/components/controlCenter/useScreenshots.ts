// DEV-534 — screenshots pasted (Ctrl+V) or dropped into the conversation box with the Staviteľ, as Nápady and Poradca
// take them (DEV-52): small previews above the field, removable, sent WITH the message — never stored in the project.
// orthodox-register 0.1.0, 27.11.2026 — the Director: „Chcel som aby to bolo vložené presne tak ako v nápadoch, kde
// vidno malý obrázok priamo v chate, okrem toho ja nechcem aby tieto snímky boli uložené do projektu ako súbory…“
// The limits are Poradca's, said by the backend; the backend decides (it reads the type from the content).
import { useEffect, useRef, useState, type ClipboardEvent, type DragEvent } from "react";

import {
  addAttachments,
  carriesFiles,
  carriesText,
  EMPTY_CLIPBOARD,
  filesIn,
  type AttachmentLimits,
  type PendingAttachment,
} from "@/lib/poradcaAttachments";
import { getScreenshotLimitsApi } from "@/services/api/screenshots";

/** The backend's sentence (conversation_images.NOT_AN_IMAGE): a file for the project goes by „Priložiť súbor“.
 * @internal — exported for its test. */
export const notAnImage = (name: string) =>
  `„${name || "súbor"}“ nie je obrázok (PNG, JPEG, WebP, GIF). Súbor, ktorý má projekt mať, prilož tlačidlom „Priložiť súbor“.`;

export function useScreenshots(versionId: string | null | undefined, disabled: boolean) {
  const [limits, setLimits] = useState<AttachmentLimits | null>(null);
  const [shots, setShots] = useState<PendingAttachment[]>([]);
  const [refused, setRefused] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const held = useRef<PendingAttachment[]>([]);

  useEffect(() => {
    held.current = shots;
  });
  // The previews' object URLs are let go when the box leaves the screen.
  useEffect(() => () => held.current.forEach((a) => a.url && URL.revokeObjectURL(a.url)), []);

  useEffect(() => {
    if (!versionId) return;
    let alive = true;
    getScreenshotLimitsApi(versionId)
      .then((found) => alive && setLimits(found))
      .catch(() => alive && setLimits(null));
    return () => {
      alive = false;
    };
  }, [versionId]);

  const off = disabled || !versionId || limits === null;

  function attach(files: File[]) {
    if (off || limits === null || files.length === 0) return;
    const pictures = files.filter((file) => (limits.attachment_types ?? []).includes(file.type));
    const other = files.find((file) => !pictures.includes(file));
    const { attachments, refused: why } = addAttachments(shots, pictures, limits);
    setShots(attachments);
    setRefused(why ?? (other ? notAnImage(other.name) : null));
  }

  function remove(key: string) {
    const gone = shots.find((a) => a.key === key);
    if (gone?.url) URL.revokeObjectURL(gone.url);
    setShots(shots.filter((a) => a.key !== key));
    setRefused(null);
  }

  /** After the message went out — its screenshots went with it. */
  function clear() {
    shots.forEach((a) => a.url && URL.revokeObjectURL(a.url));
    setShots([]);
    setRefused(null);
  }

  function onPaste(e: ClipboardEvent<HTMLElement>) {
    if (off) return;
    const files = filesIn(e.clipboardData);
    if (carriesText(e.clipboardData)) {
      // A copied web page brings its picture with its text: the picture is attached, the text pastes.
      const pictures = files.filter((file) => (limits?.attachment_types ?? []).includes(file.type));
      if (pictures.length > 0) attach(pictures);
      return;
    }
    e.preventDefault();
    if (files.length > 0) attach(files);
    else setRefused(EMPTY_CLIPBOARD);
  }

  const dropProps = {
    onDragOver: (e: DragEvent<HTMLElement>) => {
      if (off || !carriesFiles(e.dataTransfer)) return;
      e.preventDefault();
      setDragging(true);
    },
    onDragLeave: () => setDragging(false),
    onDrop: (e: DragEvent<HTMLElement>) => {
      setDragging(false);
      if (off) return;
      const files = filesIn(e.dataTransfer);
      if (files.length === 0) return;
      e.preventDefault();
      attach(files);
    },
  };

  return { shots, files: shots.map((a) => a.file), refused, dragging, remove, clear, onPaste, dropProps };
}
