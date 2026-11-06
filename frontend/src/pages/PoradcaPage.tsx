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

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { MessageSquarePlus } from "lucide-react";

import ErrorNote from "@/components/common/ErrorNote";
import PoradcaAnswer from "@/components/poradca/PoradcaAnswer";
import PoradcaComposer, { type IncomingQuestion } from "@/components/poradca/PoradcaComposer";
import PoradcaConversationItem from "@/components/poradca/PoradcaConversationItem";
import QuestionImages from "@/components/poradca/QuestionImages";
import { draftKey } from "@/hooks/useDraft";
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
import { REVIEW_PARAM, reviewQuestion, takeReview } from "@/lib/poradcaReview";
import { toUpload } from "@/lib/poradcaImages";
import type { PoradcaConversation, PoradcaProjectContext, PoradcaStatus } from "@/types/poradca";

const WHOLE_PROJECT = "";

/** DEV-103 — why a card's question waits in the box: its version is not one of this project's. */
const CARD_NOT_IN_PROJECT =
  "Otázka z karty nebola odoslaná — verzia karty v tomto projekte nie je. Skontroluj ju a pošli sám.";

/** DEV-103 — a question composed elsewhere for the composer to send; `held` says why it waits in the box instead. */
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
              <PoradcaAnswer key={m.id} message={m} scopeVersion={scopeVersion} projectVersions={context?.versions ?? []} />
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
