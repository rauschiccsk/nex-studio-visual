// Poradca (ICCINT-167, docs/specs/poradca.md) — conversations with an agent that only reads and advises.
//
// On the left my conversations for the pinned project (admin sees all), on the right a conversation: what we talk
// about (version with phase and build status, or whole project), questions and answers with progress, question field.
// It runs alongside the build — nothing here changes its state. Three states like every project screen: no project —
// a prompt with a button to Projekty, no access — a sentence saying why, otherwise Poradca.
//
// The address carries the selected conversation (`?c=`) and the default version of a new one (`?verzia=`) — the
// „Opýtaj sa Poradcu" button on a finished version leads here with it. The review-with-Poradca button on
// a verification card adds `?overit=1`: that visit sends the card's question by itself (DEV-103,
// `@/lib/poradcaReview`).

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from "react";
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
  getPoradcaReviewConversationApi,
  getPoradcaStatusApi,
  listPoradcaConversationsApi,
  renamePoradcaConversationApi,
  setPoradcaScopeApi,
} from "@/services/api/poradca";
import { ApiError } from "@/services/api";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import { versionOptionLabel } from "@/lib/poradcaAnswer";
import { appendInstruction } from "@/lib/poradcaHandoff";
import { REVIEW_PARAM, reviewQuestion, takeReview } from "@/lib/poradcaReview";
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

/** DEV-103 — why a card's question waits in the box: its version is not one of this project's. */
const CARD_NOT_IN_PROJECT =
  "Otázka z karty nebola odoslaná — verzia karty v tomto projekte nie je. Skontroluj ju a pošli sám.";

/** DEV-103 — a question composed elsewhere for the composer to send; `held` says why it waits in the box instead. */
interface IncomingQuestion {
  question: string;
  held: string | null;
}

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
  const [pane, setPane] = useState<"rozhovory" | "rozhovor">("rozhovor");
  const [conversations, setConversations] = useState<PoradcaConversation[]>([]);
  const [status, setStatus] = useState<PoradcaStatus | null>(null);
  // Until the status has answered (or failed), the length a question may have is not known.
  const [statusSettled, setStatusSettled] = useState(false);
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
      .catch(() => setStatus(null))
      .finally(() => setStatusSettled(true));
  }, [slug, refreshList]);

  // When the answer finishes, the list refreshes its "running" flag and the order.
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

  // An open conversation knows its state better than the list: the answer may have finished while the person was
  // in another conversation (the list would hold it as running and the bin greyed out for no reason), or just started.
  // The list is then the single source of "running" for both the dot and the bin.
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

  // Default version of a new conversation: from the address, otherwise the pinned version, otherwise the whole project.
  const defaultScope = useMemo(() => {
    const wanted = params.get("verzia") ?? selectedVersion?.versionId ?? null;
    return context?.versions?.some((v) => v.id === wanted) ? (wanted as string) : WHOLE_PROJECT;
  }, [params, selectedVersion, context]);
  const [newScope, setNewScope] = useState<string>(WHOLE_PROJECT);
  useEffect(() => setNewScope(defaultScope), [defaultScope]);

  // DEV-103: the visit from the review-with-Poradca button — the card is taken once, the mark leaves the address
  // (a reload or the way back sends nothing again) and the composer sends the question like one he typed. A card of
  // a version this project does not have is not sent under another scope: it waits in the box, with why.
  const [incoming, setIncoming] = useState<IncomingQuestion | null>(null);
  // DEV-138: the cards of one version and phase share one conversation. `reviewCard` — the card on its way: `into` the
  // phase's conversation (its id) or into a new one (`null`); `reviewNew` — the card founds the phase's conversation.
  const [reviewCard, setReviewCard] = useState<{ question: string; into: string | null } | null>(null);
  const [reviewNew, setReviewNew] = useState(false);
  const mounted = useRef(true);
  useEffect(
    () => () => {
      mounted.current = false;
    },
    [],
  );
  const reviewRequested = params.has(REVIEW_PARAM);
  useEffect(() => {
    const versionId = params.get("verzia");
    if (!reviewRequested || conversationId || !context || !statusSettled) return;
    const next = new URLSearchParams(params);
    next.delete(REVIEW_PARAM);
    setParams(next, { replace: true });
    const card = versionId ? takeReview(versionId) : null;
    if (!card) return;
    const known = context.versions?.some((v) => v.id === versionId) ?? false;
    const question = reviewQuestion(card, status?.question_max_chars ?? Number.POSITIVE_INFINITY);
    if (!known || !slug || !versionId) {
      setIncoming({ question, held: known ? null : CARD_NOT_IN_PROJECT });
      return;
    }
    void getPoradcaReviewConversationApi(slug, versionId)
      .then((found) => found.conversation_id ?? null)
      // The server could not say where the card goes — it founds the phase's conversation rather than wait.
      .catch(() => null)
      .then((id) => {
        if (!mounted.current) return;
        if (id) {
          next.set("c", id);
          rememberOpen(slug, id);
          setParams(next);
        } else {
          setReviewNew(true);
        }
        setReviewCard({ question, into: id });
      });
  }, [reviewRequested, conversationId, context, statusSettled, status, params, setParams, slug]);

  // DEV-138: the card reaches the composer only once the page stands where the card goes — the composer sends into the
  // conversation the address names. Into the phase's conversation once it has loaded (whether Poradca still answers
  // the previous card decides between sending and leaving it in the box); into a new one once no conversation is
  // open. A phase's conversation that cannot be opened (deleted meanwhile) does not swallow the card: it founds one.
  useEffect(() => {
    if (!reviewCard) return;
    if (reviewCard.into === null) {
      if (conversationId) return;
      setIncoming({ question: reviewCard.question, held: null });
      setReviewCard(null);
    } else if (conversationId === reviewCard.into && detail?.id === reviewCard.into) {
      setIncoming({ question: reviewCard.question, held: null });
      setReviewCard(null);
    } else if (conversationId === reviewCard.into && convError) {
      const next = new URLSearchParams(params);
      next.delete("c");
      if (slug) rememberOpen(slug, null);
      setParams(next, { replace: true });
      setReviewNew(true);
      setReviewCard({ question: reviewCard.question, into: null });
    }
  }, [reviewCard, conversationId, detail, convError, params, setParams, slug]);

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
    // DEV-103: the card's visit is over once a conversation opens — `params` here may still be the address the
    // visit came with, and a mark left in it would send a later card again on the way back.
    next.delete(REVIEW_PARAM);
    if (slug) rememberOpen(slug, id);
    setReviewNew(false); // DEV-138: another conversation — what he writes next is not the card's
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

  // DEV-62: a phone has room for one column — the list of conversations or the open one, switched by the tabs on
  // top; choosing a conversation (or starting a new one) shows it. From the tablet up both stand side by side.
  const open = (id: string | null) => {
    select(id);
    setPane("rozhovor");
  };

  return (
    <div className="grid h-full grid-cols-1 grid-rows-[auto_minmax(0,1fr)] bg-[var(--color-canvas)] md:grid-cols-[260px_minmax(0,1fr)] md:grid-rows-1">
      <div
        role="tablist"
        aria-label="Čo ukázať"
        className="row-start-1 flex border-b border-[var(--color-border-default)] bg-[var(--color-surface)] md:hidden"
      >
        {(
          [
            ["rozhovory", "Rozhovory"],
            ["rozhovor", "Rozhovor"],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={pane === id}
            onClick={() => setPane(id)}
            className={`flex-1 py-2 text-xs font-medium ${
              pane === id
                ? "border-b-2 border-[var(--color-accent-primary)] text-[var(--color-text-primary)]"
                : "text-[var(--color-text-muted)]"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Left — conversations */}
      <aside
        className={`row-start-2 flex min-h-0 flex-col border-r border-[var(--color-border-default)] md:col-start-1 md:row-start-1 ${
          pane === "rozhovory" ? "" : "max-md:hidden"
        }`}
      >
        <div className="flex items-center justify-between px-3 py-3">
          <h1 className="text-sm font-bold text-[var(--color-text-primary)]">Poradca</h1>
          <button
            type="button"
            onClick={() => open(null)}
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
              onSelect={() => open(c.id)}
              onRename={(title) => renameConversation(c.id, title)}
              onDelete={() => deleteConversation(c.id)}
            />
          ))}
        </ul>
      </aside>

      {/* Right — the conversation */}
      <section
        className={`row-start-2 flex min-h-0 min-w-0 flex-col md:col-start-2 md:row-start-1 ${
          pane === "rozhovor" ? "" : "max-md:hidden"
        }`}
      >
        <div className="flex flex-wrap items-center gap-2 border-b border-[var(--color-border-default)] px-4 py-2">
          <span className="text-xs text-[var(--color-text-secondary)]">
            {selectedProject.name} — o čom sa rozprávame:
          </span>
          <select
            aria-label="O čom sa rozprávame"
            value={scopeId}
            onChange={(e) => void changeScope(e.target.value)}
            className="min-w-0 max-w-full rounded border border-[var(--color-border-default)] bg-[var(--color-surface)] px-2 py-1 text-xs text-[var(--color-text-primary)]"
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
          incoming={incoming}
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
                reviewNew,
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
  incoming,
}: {
  /** Where the half-written question lives — one per conversation, one for the new conversation (DEV-24). */
  draftKey: string | null;
  onAsk: (question: string, images: File[]) => Promise<void>;
  disabledReason: string | null;
  /** DEV-52 — the backend's limits for pasted screenshots; null until Poradca's status is known. */
  limits: ImageLimits | null;
  /** DEV-103 — the question from a decision card, sent once by itself (or left in the box when it cannot go). */
  incoming: IncomingQuestion | null;
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
              ? "Opýtaj sa Poradcu… (Enter odošle, Shift+Enter nový riadok, Ctrl+V vloží snímku obrazovky)"
              : "Opýtaj sa Poradcu… (Enter odošle, Shift+Enter nový riadok)"
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
