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
import { Loader2, MessageSquarePlus, Send } from "lucide-react";

import ErrorNote from "@/components/common/ErrorNote";
import PoradcaAnswer from "@/components/poradca/PoradcaAnswer";
import { useAutoGrowTextarea } from "@/hooks/useAutoGrowTextarea";
import { usePoradcaConversation } from "@/hooks/usePoradcaConversation";
import { useActiveContextStore } from "@/store/activeContextStore";
import { useAuthStore } from "@/store/authStore";
import {
  askPoradcaApi,
  createPoradcaConversationApi,
  getPoradcaContextApi,
  getPoradcaStatusApi,
  listPoradcaConversationsApi,
  setPoradcaScopeApi,
} from "@/services/api/poradca";
import { ApiError } from "@/services/api";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import { formatDate } from "@/utils/format";
import { versionOptionLabel } from "@/lib/poradcaAnswer";
import type { PoradcaConversation, PoradcaProjectContext, PoradcaStatus } from "@/types/poradca";

const WHOLE_PROJECT = "";

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

  const conversationId = params.get("c");
  const { detail, error: convError, setDetail, reload } = usePoradcaConversation(conversationId);

  const refreshList = useCallback(async () => {
    if (!slug) return;
    try {
      setConversations(await listPoradcaConversationsApi(slug));
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
  const wasRunning = useRef(false);
  useEffect(() => {
    if (wasRunning.current && !running) void refreshList();
    wasRunning.current = running;
  }, [running, refreshList]);

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
            <li key={c.id}>
              <button
                type="button"
                onClick={() => select(c.id)}
                className={`w-full rounded-md px-2 py-1.5 text-left ${
                  c.id === conversationId
                    ? "bg-[var(--color-accent-primary)]/10"
                    : "hover:bg-[var(--color-surface-hover)]"
                }`}
              >
                <div className="flex items-center gap-1.5">
                  {c.running && (
                    <span className="h-1.5 w-1.5 flex-shrink-0 rounded-full bg-[var(--color-accent-primary)]" title="Poradca odpovedá" />
                  )}
                  <span className="truncate text-xs text-[var(--color-text-primary)]">{c.title}</span>
                </div>
                <div className="truncate text-[10px] text-[var(--color-text-muted)]">
                  {c.version_number ? `verzia ${c.version_number}` : "celý projekt"} · {formatDate(c.updated_at)}
                  {me && c.author_id !== me.id ? ` · ${c.author_name}` : ""}
                </div>
              </button>
            </li>
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

        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-3">
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
              </div>
            ) : (
              <PoradcaAnswer key={m.id} message={m} scopeVersion={scopeVersion} />
            ),
          )}
        </div>

        <PoradcaComposer
          disabledReason={
            notReady
              ? `Poradca teraz nevie bežať: ${notReady}`
              : running
                ? "Poradca odpovedá — ďalšiu otázku pošleš, keď dokončí (alebo ho zastav)."
                : null
          }
          onAsk={async (question) => {
            if (conversationId) {
              setDetail(await askPoradcaApi(conversationId, question));
            } else {
              const created = await createPoradcaConversationApi(selectedProject.slug, question, newScope || null);
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
  onAsk,
  disabledReason,
}: {
  onAsk: (question: string) => Promise<void>;
  disabledReason: string | null;
}) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<HumanError | null>(null);
  const growRef = useAutoGrowTextarea(text);
  const locked = !!disabledReason || sending;

  async function submit() {
    const question = text.trim();
    if (!question || locked) return;
    setSending(true);
    setError(null);
    try {
      await onAsk(question);
      setText("");
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
      className="flex-shrink-0 border-t border-[var(--color-border-default)] bg-[var(--color-surface)] p-3"
    >
      {disabledReason && <p className="mb-2 text-[11px] text-[var(--color-text-muted)]">{disabledReason}</p>}
      <ErrorNote error={error} />
      <div className="flex items-end gap-2">
        <textarea
          lang="sk"
          spellCheck={true}
          ref={growRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          disabled={locked}
          rows={1}
          placeholder="Opýtaj sa Poradcu… (Enter odošle, Shift+Enter nový riadok)"
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
