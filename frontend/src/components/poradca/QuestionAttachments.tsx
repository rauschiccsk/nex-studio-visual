// QuestionAttachments — what the Manažér attached to a question: screenshots (DEV-52) as small previews under the
// question, the whole image on click; documents (DEV-389) by their name, opened in a new tab on click. Fetched with his
// sign-in (the backend gives an attachment only to whoever may read the conversation), shown from object URLs that are
// released when the question leaves the screen. A document is fetched only when opened — it may be a large PDF.

import { useEffect, useState } from "react";
import { FileText, X } from "lucide-react";

import { getPoradcaAttachmentApi } from "@/services/api/poradca";
import type { PoradcaMessage } from "@/types/poradca";

type Attachment = NonNullable<PoradcaMessage["attachments"]>[number];

/** DEV-389: the document types (attachments.DOCUMENT_TYPES) — a screenshot is everything else. */
const isDocument = (a: Attachment) => a.mime.startsWith("text/") || a.mime === "application/pdf";
/** How long an opened document's object URL lives — the new tab has read it by then. */
const OPENED_URL_MS = 60_000;

export default function QuestionAttachments({
  conversationId,
  attachments,
  load,
}: {
  conversationId: string;
  attachments: Attachment[];
  /** DEV-534: where a screenshot is fetched from when it is not a Poradca conversation's (the Staviteľ's thread). */
  load?: (attachmentId: string) => Promise<Blob>;
}) {
  const [urls, setUrls] = useState<Record<string, string>>({});
  const [failed, setFailed] = useState<Record<string, boolean>>({});
  const [open, setOpen] = useState<Attachment | null>(null);
  const [opening, setOpening] = useState<Record<string, string>>({});
  const images = attachments.filter((a) => !isDocument(a));
  const documents = attachments.filter(isDocument);
  const ids = images.map((a) => a.id).join(",");

  useEffect(() => {
    let alive = true;
    const made: string[] = [];
    for (const a of images) {
      (load ?? ((id: string) => getPoradcaAttachmentApi(conversationId, id)))(a.id)
        .then((blob) => {
          if (!alive) return;
          const url = URL.createObjectURL(blob);
          made.push(url);
          setUrls((prev) => ({ ...prev, [a.id]: url }));
        })
        .catch(() => alive && setFailed((prev) => ({ ...prev, [a.id]: true })));
    }
    return () => {
      alive = false;
      made.forEach((url) => URL.revokeObjectURL(url));
    };
    // `ids` stands for the images — the array itself is a new object on every render of the conversation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId, ids]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  /** The tab opens on the click itself (a tab opened after waiting would be blocked), the document comes after. */
  async function openDocument(a: Attachment) {
    const tab = window.open("", "_blank");
    setOpening((prev) => ({ ...prev, [a.id]: "" }));
    try {
      const url = URL.createObjectURL(await getPoradcaAttachmentApi(conversationId, a.id));
      if (tab) tab.location.href = url;
      else window.open(url, "_blank");
      window.setTimeout(() => URL.revokeObjectURL(url), OPENED_URL_MS);
      setOpening((prev) => Object.fromEntries(Object.entries(prev).filter(([id]) => id !== a.id)));
    } catch {
      tab?.close();
      setOpening((prev) => ({ ...prev, [a.id]: `Dokument „${a.name}“ sa nepodarilo otvoriť.` }));
    }
  }

  if (attachments.length === 0) return null;
  return (
    <>
      <div className="mt-2 flex flex-wrap justify-end gap-2" data-testid="question-attachments">
        {documents.map((a) => (
          <span key={a.id} className="flex flex-col items-end gap-0.5">
            <button
              type="button"
              onClick={() => void openDocument(a)}
              title={`Otvoriť dokument ${a.name}`}
              className="flex max-w-[16rem] items-center gap-1.5 rounded border border-[var(--color-border-default)] px-2 py-1.5 text-xs text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
            >
              <FileText className="h-4 w-4 flex-shrink-0" aria-hidden />
              <span className="break-all text-left">{a.name}</span>
            </button>
            {opening[a.id] && (
              <span role="status" className="text-[10px] text-[var(--color-state-warning-fg)]">
                {opening[a.id]}
              </span>
            )}
          </span>
        ))}
        {images.map((a) =>
          urls[a.id] ? (
            <button
              key={a.id}
              type="button"
              onClick={() => setOpen(a)}
              title={a.name}
              className="overflow-hidden rounded border border-[var(--color-border-default)]"
            >
              <img src={urls[a.id]} alt={a.name} className="h-20 max-w-[10rem] object-cover" />
            </button>
          ) : (
            <span
              key={a.id}
              className="flex h-20 w-28 items-center justify-center rounded border border-dashed border-[var(--color-border-default)] px-1 text-center text-[10px] text-[var(--color-text-muted)]"
            >
              {failed[a.id] ? `Obrázok „${a.name}“ sa nepodarilo načítať` : a.name}
            </span>
          ),
        )}
      </div>
      {open && urls[open.id] && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label={open.name}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-6"
          onClick={() => setOpen(null)}
        >
          <img src={urls[open.id]} alt={open.name} className="max-h-full max-w-full rounded shadow-lg" />
          <button
            type="button"
            aria-label="Zavrieť"
            onClick={() => setOpen(null)}
            className="absolute right-4 top-4 rounded-full bg-black/60 p-1.5 text-white hover:bg-black/80"
          >
            <X className="h-5 w-5" />
          </button>
        </div>
      )}
    </>
  );
}
