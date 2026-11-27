// DEV-527 — a screenshot for the Staviteľ by Ctrl+V or by dropping it on the box, as Poradca and Nápady take one (DEV-52).
// The file goes the way of „Priložiť súbor“ (DEV-44): into the project's private/, and the line saying where it lies
// lands in the message. orthodox-register 0.1.0, Vizuál, 27.11.2026 — the Director: „…potrebujem to ukázať Staviteľovi,
// preto potrebujem doplniť do chatu staviteľa možnosť pridania snímku obrazovky podobne ako sme to už spravili v prípade
// Nápadov.“ One upload for the button, the paste and the drop, so the three never store a file differently.
import { useEffect, useRef, useState, type ClipboardEvent, type DragEvent } from "react";

import { carriesFiles, carriesText, EMPTY_CLIPBOARD, filesIn } from "@/lib/poradcaAttachments";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import { uploadPrivateFileApi } from "@/services/api/projectFiles";

const two = (n: number) => String(n).padStart(2, "0");

/** A screenshot from the clipboard arrives as „image.png“ (or nameless) — it is stored under the minute it was taken,
 *  so several of them read apart in Prílohy. A file with a name of its own keeps it.
 * @internal — exported for its test. */
export function namedForUpload(file: File, now: Date = new Date()): File {
  if (file.name && !/^image\.[a-z0-9]+$/i.test(file.name)) return file;
  const ext = (file.type.split("/")[1] ?? "png").replace("jpeg", "jpg").replace(/[^a-z0-9]/gi, "") || "png";
  const stamp = `${now.getFullYear()}-${two(now.getMonth() + 1)}-${two(now.getDate())}-${two(now.getHours())}${two(now.getMinutes())}`;
  return new File([file], `snimka-${stamp}.${ext}`, { type: file.type });
}

export interface PrivateUpload {
  /** Store the files in private/, one by one; the lines of those stored go into the message together. */
  upload: (files: File[]) => Promise<void>;
  uploading: boolean;
  error: HumanError | null;
  /** A sentence that is not an error — an empty clipboard. */
  note: string | null;
}

/** The upload of „Priložiť súbor“. ``onAttached`` gets the lines for the message; the latest one is called, so text
 *  written while a file was uploading is kept. */
export function usePrivateUpload(versionId: string, onAttached: (lines: string) => void): PrivateUpload & {
  say: (note: string | null) => void;
} {
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<HumanError | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const latest = useRef(onAttached);
  useEffect(() => {
    latest.current = onAttached;
  });

  async function upload(files: File[]) {
    if (files.length === 0) return;
    setError(null);
    setNote(null);
    setUploading(true);
    const lines: string[] = [];
    try {
      for (const file of files) lines.push((await uploadPrivateFileApi(versionId, file)).answer_line);
    } catch (err: unknown) {
      setError(humanizeApiError(err, "Súbor sa nepodarilo priložiť"));
    } finally {
      if (lines.length > 0) latest.current(lines.join("\n"));
      setUploading(false);
    }
  }

  return { upload, uploading, error, note, say: setNote };
}

/** Ctrl+V in the box and a drop on its row. A paste of text stays the field's own; a picture pasted with text (a copied
 *  web page) is attached and the text pastes. Off while the box is locked. */
export function usePasteAndDropUpload({
  versionId,
  disabled,
  onAttached,
}: {
  versionId: string | null | undefined;
  disabled: boolean;
  onAttached: (lines: string) => void;
}) {
  const uploader = usePrivateUpload(versionId ?? "", onAttached);
  const [dragging, setDragging] = useState(false);
  const off = disabled || !versionId || uploader.uploading;
  const send = (files: File[]) => void uploader.upload(files.map((file) => namedForUpload(file)));

  function onPaste(e: ClipboardEvent<HTMLElement>) {
    if (off) return;
    const files = filesIn(e.clipboardData);
    if (carriesText(e.clipboardData)) {
      const pictures = files.filter((file) => file.type.startsWith("image/"));
      if (pictures.length > 0) send(pictures);
      return;
    }
    e.preventDefault();
    if (files.length > 0) send(files);
    else uploader.say(EMPTY_CLIPBOARD);
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
      send(files);
    },
  };

  return { uploader, onPaste, dropProps, dragging };
}
