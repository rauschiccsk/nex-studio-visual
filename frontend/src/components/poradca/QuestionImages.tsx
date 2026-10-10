// QuestionImages — the screenshots the Manažér attached to a question (DEV-52): small previews under the question,
// the whole image on click. Fetched with his sign-in (the backend gives an image only to whoever may read the
// conversation), shown from object URLs that are released when the question leaves the screen.

import { useEffect, useState } from "react";
import { X } from "lucide-react";

import { getPoradcaAttachmentApi } from "@/services/api/poradca";
import type { PoradcaMessage } from "@/types/poradca";

type Attachment = NonNullable<PoradcaMessage["attachments"]>[number];

export default function QuestionImages({
  conversationId,
  attachments,
}: {
  conversationId: string;
  attachments: Attachment[];
}) {
  const [urls, setUrls] = useState<Record<string, string>>({});
  const [failed, setFailed] = useState<Record<string, boolean>>({});
  const [open, setOpen] = useState<Attachment | null>(null);
  const ids = attachments.map((a) => a.id).join(",");

  useEffect(() => {
    let alive = true;
    const made: string[] = [];
    for (const a of attachments) {
      getPoradcaAttachmentApi(conversationId, a.id)
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
    // `ids` stands for the attachments — the array itself is a new object on every render of the conversation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId, ids]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  if (attachments.length === 0) return null;
  return (
    <>
      <div className="mt-2 flex flex-wrap justify-end gap-2" data-testid="question-images">
        {attachments.map((a) =>
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
