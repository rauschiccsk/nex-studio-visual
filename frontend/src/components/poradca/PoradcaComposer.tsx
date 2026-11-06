// The question field of a Poradca conversation (ICCINT-167) — text, pasted or dropped screenshots (DEV-52), a draft that
// survives leaving the screen (DEV-24) and a question handed over from a decision card (DEV-103). Shared by Poradca of a
// project and by Nápady (DEV-318), so a new idea is asked exactly as a project question is.

import { useEffect, useLayoutEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { ImagePlus, Loader2, Send, X } from "lucide-react";

import ErrorNote from "@/components/common/ErrorNote";
import { useAutoGrowTextarea } from "@/hooks/useAutoGrowTextarea";
import { RESTORED_DRAFT_LABEL, useDraft } from "@/hooks/useDraft";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import { appendInstruction } from "@/lib/poradcaHandoff";
import {
  EMPTY_CLIPBOARD,
  addImages,
  carriesText,
  imagesIn,
  type ImageLimits,
  type PendingImage,
} from "@/lib/poradcaImages";

/** DEV-103 — a question from a decision card: sent once by itself, or left in the box with the reason (`held`). */
export interface IncomingQuestion {
  question: string;
  held: string | null;
}

const DEFAULT_PLACEHOLDER = "Opýtaj sa Poradcu…";

export default function PoradcaComposer({
  draftKey: key,
  onAsk,
  disabledReason,
  limits,
  incoming,
  placeholder = DEFAULT_PLACEHOLDER,
}: {
  /** Where the half-written question lives — one per conversation, one for the new conversation (DEV-24). */
  draftKey: string | null;
  onAsk: (question: string, images: File[]) => Promise<void>;
  disabledReason: string | null;
  /** DEV-52 — the backend's limits for pasted screenshots; null until Poradca's status is known. */
  limits: ImageLimits | null;
  /** DEV-103 — the question from a decision card, sent once by itself (or left in the box when it cannot go). */
  incoming: IncomingQuestion | null;
  /** DEV-318 — what the empty box says; Nápady asks for the idea, not a question about a project. */
  placeholder?: string;
}) {
  const { text, setText, clear, restored } = useDraft(key);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<HumanError | null>(null);
  const [images, setImages] = useState<PendingImage[]>([]);
  const [refused, setRefused] = useState<string | null>(null);
  const picker = useRef<HTMLInputElement>(null);
  const growRef = useAutoGrowTextarea(text);
  const locked = !!disabledReason || sending;
  const canAttach = !!limits && limits.attachments_max_count > 0;

  function attach(files: File[]) {
    if (!limits || files.length === 0) return;
    const { images: next, refused: why } = addImages(images, files, limits);
    setImages(next);
    setRefused(why);
  }

  // DEV-52: Ctrl+V / Shift+Insert with a screenshot works ANYWHERE on Poradca's page, not only with the cursor in
  // the field (the Director 10.10.2026: „nechápem kde to mám stlačiť v editore chatu?“). Text still pastes where the
  // cursor is; a paste that brings nothing at all says why instead of doing nothing.
  // DEV-223: a layout effect, registered in the same commit that shows the attach button — a passive effect ran
  // after the paint, and a screenshot pasted outside the field in between was lost (the CI test, 25.10.2026).
  useLayoutEffect(() => {
    if (!canAttach) return;
    function onPaste(e: ClipboardEvent) {
      const pasted = imagesIn(e.clipboardData);
      if (pasted.length > 0) {
        e.preventDefault();
        attach(pasted);
      } else if (!carriesText(e.clipboardData)) {
        setRefused(EMPTY_CLIPBOARD);
      }
    }
    document.addEventListener("paste", onPaste);
    return () => document.removeEventListener("paste", onPaste);
  });

  function detach(key: string) {
    const gone = images.find((i) => i.key === key);
    if (gone) URL.revokeObjectURL(gone.url);
    setImages(images.filter((i) => i.key !== key));
    setRefused(null);
  }

  async function submit() {
    const question = text.trim();
    if (!question || locked) return;
    await send(question, true);
  }

  // `own` — the text of the box, with its images. A question from a card is not his draft: it leaves the draft
  // alone when it goes out, and lands under it when it does not.
  async function send(question: string, own: boolean) {
    setSending(true);
    setError(null);
    try {
      await onAsk(question, own ? images.map((i) => i.file) : []);
      if (own) {
        clear(); // only once it went out — a failed question stays in the box, with its images
        images.forEach((i) => URL.revokeObjectURL(i.url));
        setImages([]);
        setRefused(null);
      }
    } catch (e: unknown) {
      if (!own) setText(appendInstruction(text, question));
      setError(humanizeApiError(e, "Otázku sa nepodarilo poslať"));
    } finally {
      setSending(false);
    }
  }

  // DEV-103: the card's question goes out once, through the same path as a typed one. When it cannot go (Poradca
  // cannot run, the version is not this project's) it waits in the box under his draft, and the box says why.
  useEffect(() => {
    if (!incoming) return;
    if (incoming.held || disabledReason) {
      setText(appendInstruction(text, incoming.question));
      if (incoming.held) setRefused(incoming.held);
      return;
    }
    void send(incoming.question, false);
    // Once per card: what else it reads (the box, the reason it is locked) is current when the card arrives — the
    // page hands it over only after Poradca's status answered.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [incoming]);

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void submit();
    }
  }

  return (
    <form
      onSubmit={(e: FormEvent) => {
        e.preventDefault();
        void submit();
      }}
      onDragOver={(e) => {
        if (canAttach && !locked && imagesIn(e.dataTransfer).length) e.preventDefault();
      }}
      onDrop={(e) => {
        const dropped = imagesIn(e.dataTransfer);
        if (!canAttach || locked || dropped.length === 0) return;
        e.preventDefault();
        attach(dropped);
      }}
      className="flex-shrink-0 border-t border-[var(--color-border-default)] bg-[var(--color-surface)] p-3"
    >
      {disabledReason && <p className="mb-2 text-[11px] text-[var(--color-text-muted)]">{disabledReason}</p>}
      {restored && <p className="mb-2 text-[11px] text-[var(--color-text-muted)]">{RESTORED_DRAFT_LABEL}</p>}
      <ErrorNote error={error} />
      {refused && (
        <p role="status" className="mb-2 text-[11px] text-[var(--color-state-warning-fg)]">
          {refused}
        </p>
      )}
      {images.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-2" data-testid="pending-images">
          {images.map((i) => (
            <div key={i.key} className="relative overflow-hidden rounded border border-[var(--color-border-default)]">
              <img src={i.url} alt={i.file.name} className="h-16 max-w-[8rem] object-cover" />
              <button
                type="button"
                aria-label={`Odobrať ${i.file.name}`}
                onClick={() => detach(i.key)}
                disabled={sending}
                className="absolute right-0.5 top-0.5 rounded-full bg-black/60 p-0.5 text-white hover:bg-black/80"
              >
                <X className="h-3 w-3" />
              </button>
            </div>
          ))}
        </div>
      )}
      <div className="flex items-end gap-2">
        {canAttach && (
          <>
            <input
              ref={picker}
              type="file"
              accept={(limits.attachment_types ?? []).join(",")}
              multiple
              hidden
              data-testid="poradca-image-picker"
              onChange={(e) => {
                attach(Array.from(e.target.files ?? []));
                e.target.value = "";
              }}
            />
            <button
              type="button"
              onClick={() => picker.current?.click()}
              disabled={locked}
              title="Priložiť snímku obrazovky (alebo ju vlož do otázky cez Ctrl+V)"
              aria-label="Priložiť snímku obrazovky"
              className="flex h-10 items-center rounded-lg border border-[var(--color-border-default)] px-2.5 text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] disabled:cursor-not-allowed disabled:opacity-40"
            >
              <ImagePlus className="h-4 w-4" />
            </button>
          </>
        )}
        <textarea
          data-draft="poradca"
          lang="sk"
          spellCheck={true}
          ref={growRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          disabled={locked}
          rows={1}
          placeholder={
            canAttach
              ? `${placeholder} (Enter odošle, Shift+Enter nový riadok, Ctrl+V vloží snímku obrazovky)`
              : `${placeholder} (Enter odošle, Shift+Enter nový riadok)`
          }
          className="min-h-[2.5rem] min-w-0 flex-1 resize-none rounded-lg border border-[var(--color-border-default)] bg-[var(--color-canvas)] px-3 py-2 text-sm text-[var(--color-text-primary)] placeholder:text-[var(--color-text-muted)] focus:border-[var(--color-accent-primary)] focus:outline-none disabled:opacity-50"
        />
        <button
          type="submit"
          disabled={locked || !text.trim()}
          className="flex h-10 items-center gap-1.5 rounded-lg bg-primary-600 px-3 text-xs font-medium text-white hover:bg-primary-500 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {sending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />} Opýtať sa
        </button>
      </div>
    </form>
  );
}
