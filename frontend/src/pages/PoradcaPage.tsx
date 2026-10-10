// Poradca (ICCINT-167, docs/specs/poradca.md) — rozhovory s agentom, ktorý len číta a radí.
//
// Vľavo moje rozhovory k pripnutému projektu (admin vidí všetky), vpravo rozhovor: o čom sa rozprávame
// (verzia s fázou a stavom stavby, alebo celý projekt), otázky a odpovede s priebehom, pole na otázku.
// Beží vedľa stavby — nič tu nemení jej stav. Tri stavy ako každá projektová obrazovka: bez projektu výzva
// s tlačidlom na Projekty, bez prístupu veta prečo, inak Poradca.
//
// Adresa nesie vybraný rozhovor (`?c=`) a predvolenú verziu nového rozhovoru (`?verzia=`) — tlačidlo
// „Opýtaj sa Poradcu" pri hotovej verzii sem vedie s ňou.

import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { ImagePlus, Loader2, MessageSquarePlus, Send, X } from "lucide-react";

import ErrorNote from "@/components/common/ErrorNote";
import PoradcaAnswer from "@/components/poradca/PoradcaAnswer";
import PoradcaConversationItem from "@/components/poradca/PoradcaConversationItem";
import QuestionImages from "@/components/poradca/QuestionImages";
import { useAutoGrowTextarea } from "@/hooks/useAutoGrowTextarea";
import { RESTORED_DRAFT_LABEL, draftKey, useDraft } from "@/hooks/useDraft";
import { useFollowBottom } from "@/hooks/useFollowBottom";
import { usePoradcaConversation } from "@/hooks/usePoradcaConversation";
import { useActiveContextStore } from "@/store/activeContextStore";
import { useAuthStore } from "@/store/authStore";
import {
  askPoradcaApi,
  createPoradcaConversationApi,
  deletePoradcaConversationApi,
  getPoradcaContextApi,
  getPoradcaStatusApi,
  listPoradcaConversationsApi,
  renamePoradcaConversationApi,
  setPoradcaScopeApi,
} from "@/services/api/poradca";
import { ApiError } from "@/services/api";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import { versionOptionLabel } from "@/lib/poradcaAnswer";
import {
  EMPTY_CLIPBOARD,
  addImages,
  carriesText,
  imagesIn,
  toUpload,
  type ImageLimits,
  type PendingImage,
} from "@/lib/poradcaImages";
import type { PoradcaConversation, PoradcaProjectContext, PoradcaStatus } from "@/types/poradca";

const WHOLE_PROJECT = "";

// DEV-24: the sidebar opens bare /poradca. Coming back from another screen must land where the Manažér
// left — the conversation he had open (or the new one he was starting) — not on an empty new conversation.
const OPEN_PREFIX = "nex.poradca.open.";

function rememberOpen(slug: string, conversationId: string | null): void {
  try {
    if (conversationId) window.localStorage.setItem(OPEN_PREFIX + slug, conversationId);
    else window.localStorage.removeItem(OPEN_PREFIX + slug);
  } catch {
    /* storage unavailable — he lands on a new conversation, as before */
  }
}

function recallOpen(slug: string): string | null {
  try {
    return window.localStorage.getItem(OPEN_PREFIX + slug);
  } catch {
    return null;
  }
}

export default function PoradcaPage() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const selectedProject = useActiveContextStore((s) => s.selectedProject);
  const selectedVersion = useActiveContextStore((s) => s.selectedVersion);
  const me = useAuthStore((s) => s.user);
  const slug = selectedProject?.slug ?? null;

  const [context, setContext] = useState<PoradcaProjectContext | null>(null);
  const [accessDenied, setAccessDenied] = useState(false);
  const [conversations, setConversations] = useState<PoradcaConversation[]>([]);
  const [status, setStatus] = useState<PoradcaStatus | null>(null);
  const [loadError, setLoadError] = useState<HumanError | null>(null);
  // The project whose conversation list has arrived — until then a remembered conversation cannot be checked.
  const [listedSlug, setListedSlug] = useState<string | null>(null);

  const conversationId = params.get("c");
  const { detail, error: convError, setDetail, reload } = usePoradcaConversation(conversationId);

  const refreshList = useCallback(async () => {
    if (!slug) return;
    try {
      setConversations(await listPoradcaConversationsApi(slug));
      setListedSlug(slug);
    } catch (e: unknown) {
      setLoadError(humanizeApiError(e, "Rozhovory sa nepodarilo načítať"));
    }
  }, [slug]);

  useEffect(() => {
    if (!slug) return;
    setAccessDenied(false);
    setLoadError(null);
    getPoradcaContextApi(slug)
      .then(setContext)
      .catch((e: unknown) => {
        if (e instanceof ApiError && e.status === 403) setAccessDenied(true);
        else setLoadError(humanizeApiError(e, "Projekt sa nepodarilo načítať"));
      });
    void refreshList();
    getPoradcaStatusApi()
      .then(setStatus)
      .catch(() => setStatus(null));
  }, [slug, refreshList]);

  // Keď odpoveď dobehne, zoznam si obnoví „beží" a poradie.
  const running = !!detail?.messages?.some((m) => m.status === "running");
  // DEV-25: what grows while Poradca writes — a new message, a new step of the last one, its answer (the text
  // arrives with the status change, not piece by piece).
  const lastMessage = detail?.messages?.[detail.messages.length - 1];
  const thread = useFollowBottom<HTMLDivElement>(
    `${detail?.messages?.length ?? 0}:${lastMessage?.steps?.length ?? 0}:${lastMessage?.status ?? ""}`,
    conversationId,
  );
  const wasRunning = useRef(false);
  useEffect(() => {
    if (wasRunning.current && !running) void refreshList();
    wasRunning.current = running;
  }, [running, refreshList]);

  // Otvorený rozhovor pozná svoj stav presnejšie než zoznam: odpoveď mohla dobehnúť, kým bol človek
  // v inom rozhovore (zoznam by ho držal ako bežiaci a kôš zašednutý bez dôvodu), alebo práve začala.
  // Zoznam je potom jediný zdroj „beží" pre bodku aj kôš.
  useEffect(() => {
    if (!detail) return;
    setConversations((list) =>
      list.some((c) => c.id === detail.id && c.running !== running)
        ? list.map((c) => (c.id === detail.id ? { ...c, running } : c))
        : list,
    );
  }, [detail, running]);

  useEffect(() => {
    if (slug && conversationId) rememberOpen(slug, conversationId);
  }, [slug, conversationId]);

  const asksForNew = params.has("verzia");
  useEffect(() => {
    if (!slug || conversationId || asksForNew || listedSlug !== slug) return;
    const last = recallOpen(slug);
    if (!last) return;
    if (conversations.some((c) => c.id === last)) {
      const next = new URLSearchParams(params);
      next.set("c", last);
      setParams(next, { replace: true });
    } else {
      rememberOpen(slug, null); // deleted meanwhile — nothing to come back to
    }
  }, [slug, conversationId, asksForNew, listedSlug, conversations, params, setParams]);

  // Predvolená verzia nového rozhovoru: z adresy, inak pripnutá verzia, inak celý projekt.
  const defaultScope = useMemo(() => {
    const wanted = params.get("verzia") ?? selectedVersion?.versionId ?? null;
    return context?.versions?.some((v) => v.id === wanted) ? (wanted as string) : WHOLE_PROJECT;
  }, [params, selectedVersion, context]);
  const [newScope, setNewScope] = useState<string>(WHOLE_PROJECT);
  useEffect(() => setNewScope(defaultScope), [defaultScope]);

  const scopeId = conversationId ? (detail?.version_id ?? WHOLE_PROJECT) : newScope;
  const scopeVersion = context?.versions?.find((v) => v.id === scopeId) ?? null;

  if (!selectedProject) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 bg-[var(--color-canvas)] p-6 text-center">
        <h2 className="text-sm font-semibold text-[var(--color-text-secondary)]">Nemáš vybraný projekt</h2>
        <p className="max-w-md text-xs text-[var(--color-text-muted)]">
          Poradca sa rozpráva o konkrétnom projekte. Otvor <span className="font-mono">Projekty</span> a pripni
          projekt.
        </p>
        <button
          onClick={() => navigate("/projects")}
          className="rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-500"
        >
          → Otvor Projekty
        </button>
      </div>
    );
  }

  if (accessDenied) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 bg-[var(--color-canvas)] p-6 text-center">
        <h2 className="text-sm font-semibold text-[var(--color-text-secondary)]">Nemáš prístup k tomuto projektu</h2>
        <p className="max-w-md text-xs text-[var(--color-text-muted)]">
          Projekt <span className="font-medium">{selectedProject.name}</span> patrí niekomu inému. Poradcu k nemu môže
          otvoriť jeho vlastník alebo Manažér; v Projektoch si pripni vlastný projekt.
        </p>
      </div>
    );
  }

  function select(id: string | null) {
    const next = new URLSearchParams(params);
    if (id) next.set("c", id);
    else next.delete("c");
    if (slug) rememberOpen(slug, id);
    setParams(next);
  }

  async function changeScope(value: string) {
    if (!conversationId) {
      setNewScope(value);
      return;
    }
    try {
      await setPoradcaScopeApi(conversationId, value || null);
      await reload();
      void refreshList();
    } catch (e: unknown) {
      setLoadError(humanizeApiError(e, "Zmena verzie zlyhala"));
    }
  }

  async function renameConversation(id: string, title: string): Promise<boolean> {
    try {
      const updated = await renamePoradcaConversationApi(id, title);
      setConversations((list) => list.map((c) => (c.id === id ? updated : c)));
      setLoadError(null);
      return true;
    } catch (e: unknown) {
      setLoadError(humanizeApiError(e, "Premenovanie zlyhalo"));
      return false;
    }
  }

  async function deleteConversation(id: string): Promise<boolean> {
    try {
      await deletePoradcaConversationApi(id);
      setConversations((list) => list.filter((c) => c.id !== id));
      setLoadError(null);
      if (id === conversationId) select(null);
      void refreshList();
      return true;
    } catch (e: unknown) {
      setLoadError(humanizeApiError(e, "Vymazanie zlyhalo"));
      return false;
    }
  }

  const notReady = status && !status.ready ? (status.problems ?? []).join("; ") : null;

  return (
    <div className="grid h-full grid-cols-[260px_minmax(0,1fr)] bg-[var(--color-canvas)]">
      {/* Vľavo — rozhovory */}
      <aside className="flex min-h-0 flex-col border-r border-[var(--color-border-default)]">
        <div className="flex items-center justify-between px-3 py-3">
          <h1 className="text-sm font-bold text-[var(--color-text-primary)]">Poradca</h1>
          <button
            type="button"
            onClick={() => select(null)}
            className="flex items-center gap-1 rounded-lg bg-primary-600 px-2 py-1 text-[11px] font-medium text-white hover:bg-primary-500"
          >
            <MessageSquarePlus className="h-3.5 w-3.5" /> Nový rozhovor
          </button>
        </div>
        <ul className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
          {conversations.length === 0 && (
            <li className="px-1 text-[11px] text-[var(--color-text-muted)]">
              Zatiaľ žiadny rozhovor k projektu {selectedProject.name}.
            </li>
          )}
          {conversations.map((c) => (
            <PoradcaConversationItem
              key={c.id}
              conversation={c}
              active={c.id === conversationId}
              busy={c.running}
              authorName={me && c.author_id !== me.id ? c.author_name : null}
              onSelect={() => select(c.id)}
              onRename={(title) => renameConversation(c.id, title)}
              onDelete={() => deleteConversation(c.id)}
            />
          ))}
        </ul>
      </aside>

      {/* Vpravo — rozhovor */}
      <section className="flex min-h-0 flex-col">
        <div className="flex flex-wrap items-center gap-2 border-b border-[var(--color-border-default)] px-4 py-2">
          <span className="text-xs text-[var(--color-text-secondary)]">
            {selectedProject.name} — o čom sa rozprávame:
          </span>
          <select
            aria-label="O čom sa rozprávame"
            value={scopeId}
            onChange={(e) => void changeScope(e.target.value)}
            className="rounded border border-[var(--color-border-default)] bg-[var(--color-surface)] px-2 py-1 text-xs text-[var(--color-text-primary)]"
          >
            <option value={WHOLE_PROJECT}>Celý projekt</option>
            {(context?.versions ?? []).map((v) => (
              <option key={v.id} value={v.id}>
                {versionOptionLabel(v)}
              </option>
            ))}
          </select>
        </div>

        <div
          ref={thread.ref}
          onScroll={thread.onScroll}
          role="log"
          aria-label="Rozhovor s Poradcom"
          className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-3"
        >
          <ErrorNote error={loadError} />
          {convError && <p className="text-xs text-[var(--color-state-error-fg)]">{convError}</p>}
          {!conversationId && (
            <div className="max-w-xl space-y-1 text-xs text-[var(--color-text-muted)]">
              <p>
                Opýtaj sa na čokoľvek k projektu — čomu v stavbe nerozumieš, prečo agent stojí, čo je v logoch, čo je
                v databáze UAT, prečo zlyhalo zostavenie.
              </p>
              <p>
                Poradca len číta: nič nezmení, nevidí ostrú prevádzku zákazníkov ani heslá a na internet nemá
                prístup. Keď treba zmenu, poradí tlačidlo a pokyn ti pripraví na odoslanie.
              </p>
            </div>
          )}
          {(detail?.messages ?? []).map((m) =>
            m.author === "human" ? (
              <div
                key={m.id}
                className="ml-auto max-w-[80%] whitespace-pre-wrap rounded-lg border border-[var(--color-accent-primary)]/30 bg-[var(--color-accent-primary)]/10 px-3 py-2 text-sm text-[var(--color-text-primary)]"
              >
                {m.content}
                {conversationId && (m.attachments ?? []).length > 0 && (
                  <QuestionImages conversationId={conversationId} attachments={m.attachments ?? []} />
                )}
              </div>
            ) : (
              <PoradcaAnswer key={m.id} message={m} scopeVersion={scopeVersion} />
            ),
          )}
        </div>

        <PoradcaComposer
          draftKey={draftKey(`poradca.${conversationId ?? "new"}`, selectedProject.slug)}
          disabledReason={
            notReady
              ? `Poradca teraz nevie bežať: ${notReady}`
              : running
                ? "Poradca odpovedá — ďalšiu otázku pošleš, keď dokončí (alebo ho zastav)."
                : null
          }
          limits={status}
          onAsk={async (question, images) => {
            thread.follow(); // his own question: show what comes back, wherever he had scrolled to
            const uploads = await Promise.all(images.map(toUpload));
            if (conversationId) {
              setDetail(await askPoradcaApi(conversationId, question, uploads));
            } else {
              const created = await createPoradcaConversationApi(
                selectedProject.slug,
                question,
                newScope || null,
                uploads,
              );
              setDetail(created);
              select(created.id);
            }
            void refreshList();
          }}
        />
      </section>
    </div>
  );
}

function PoradcaComposer({
  draftKey: key,
  onAsk,
  disabledReason,
  limits,
}: {
  /** Where the half-written question lives — one per conversation, one for the new conversation (DEV-24). */
  draftKey: string | null;
  onAsk: (question: string, images: File[]) => Promise<void>;
  disabledReason: string | null;
  /** DEV-52 — the backend's limits for pasted screenshots; null until Poradca's status is known. */
  limits: ImageLimits | null;
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
  useEffect(() => {
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
    setSending(true);
    setError(null);
    try {
      await onAsk(
        question,
        images.map((i) => i.file),
      );
      clear(); // only once it went out — a failed question stays in the box, with its images
      images.forEach((i) => URL.revokeObjectURL(i.url));
      setImages([]);
      setRefused(null);
    } catch (e: unknown) {
      setError(humanizeApiError(e, "Otázku sa nepodarilo poslať"));
    } finally {
      setSending(false);
    }
  }

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
              ? "Opýtaj sa Poradcu… (Enter odošle, Shift+Enter nový riadok, Ctrl+V vloží snímku obrazovky)"
              : "Opýtaj sa Poradcu… (Enter odošle, Shift+Enter nový riadok)"
          }
          className="min-h-[2.5rem] flex-1 resize-none rounded-lg border border-[var(--color-border-default)] bg-[var(--color-canvas)] px-3 py-2 text-sm text-[var(--color-text-primary)] placeholder:text-[var(--color-text-muted)] focus:border-[var(--color-accent-primary)] focus:outline-none disabled:opacity-50"
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
