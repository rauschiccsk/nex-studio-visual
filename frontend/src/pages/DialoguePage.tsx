/**
 * DialoguePage — Zákaznícky dialóg (Customer ↔ Designer, Director-mediated).
 *
 * Interný kód: Gate E. UI texty používajú slovenský preklad
 * "Zákaznícky dialóg" (Director directive 2026-05-16). Backend routes,
 * DB tabuľky, charter dokumenty zostávajú "Gate E" pre konzistenciu
 * s ICC waterfall (Gate A..D Designer + Gate E Customer + Gate F Implementer).
 *
 * Director directive 2026-05-15: 4. ICC agent (Customer) systematicky
 * kladie Designerovi otázky pred Implementer spawn-om. Director vidí
 * každú správu pred doručením a schvaľuje (plný-gate mode).
 *
 * Tri render stavy:
 *
 *   A. Žiadny ``selectedProject`` → CTA na /projects (pin a project).
 *   B. ``selectedProject`` set + no active session → "Spustiť zákaznícky
 *      dialóg" button → POST /dialogue/sessions.
 *   C. Active session → chronological message stream + Director controls
 *      (trigger Customer / inject / approve / reject / end).
 *
 * Loading feedback (Director directive 2026-05-16): každý slow async
 * action (claude CLI volá až 180s) musí mať vizuálnu spätnú väzbu —
 * per-button spinner + label change + per-panel progress bar + elapsed
 * timer. Bez toho aplikácia pôsobí zamrznute.
 *
 * Permissions: ``ri`` only. Non-ri users see Lock placeholder.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  Lock,
  Loader2,
  RefreshCw,
  X,
  FolderOpen,
  Play,
  Check,
  Send,
  MessagesSquare,
} from "lucide-react";

import { useAuthStore } from "@/store/authStore";
import { useActiveContextStore } from "@/store/activeContextStore";
import { ApiError } from "@/services/api";
import {
  approveDialogueMessageApi,
  createDialogueSessionApi,
  directorInjectMessageApi,
  endDialogueSessionApi,
  getDialogueSessionApi,
  listDialogueSessionsApi,
  rejectDialogueMessageApi,
  triggerCustomerNextQuestionApi,
  type DialogueMessage,
  type DialogueSessionWithMessages,
} from "@/services/api/dialogue";

// ─── Author styling helpers ───────────────────────────────────────────────

const AUTHOR_LABEL: Record<DialogueMessage["author"], string> = {
  customer: "Customer",
  designer: "Designer",
  director: "Director",
};

const AUTHOR_EMOJI: Record<DialogueMessage["author"], string> = {
  customer: "👤",
  designer: "✏️",
  director: "📢",
};

const AUTHOR_ACCENT: Record<DialogueMessage["author"], string> = {
  customer: "border-l-fuchsia-500 bg-fuchsia-500/5",
  designer: "border-l-fuchsia-400 bg-fuchsia-500/5",
  director: "border-l-slate-500 bg-slate-700/30",
};

AUTHOR_ACCENT.designer = "border-l-cyan-400 bg-cyan-500/5";

const STATUS_BADGE: Record<DialogueMessage["status"], string> = {
  pending: "bg-amber-500/15 text-amber-400 border border-amber-500/30",
  approved: "bg-emerald-500/15 text-emerald-400 border border-emerald-500/30",
  delivered: "bg-slate-700/40 text-slate-400 border border-slate-600",
  rejected: "bg-rose-500/15 text-rose-400 border border-rose-500/30",
};

// ─── Loading feedback helpers ─────────────────────────────────────────────

/** Slovak dative recipient label, used in progress strings ("Posielam …"). */
function recipientDative(recipient: "customer" | "designer"): string {
  return recipient === "designer" ? "Designerovi" : "Customer-ovi";
}

/** Author-aware "send to opposite agent" recipient (Approve label). */
function approveRecipientDative(author: DialogueMessage["author"]): string {
  return author === "customer" ? "Designerovi" : "Customer-ovi";
}

export default function DialoguePage() {
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);
  const isDirector = user?.role === "ri";
  const selectedProject = useActiveContextStore((s) => s.selectedProject);
  const selectedVersion = useActiveContextStore((s) => s.selectedVersion);

  const [session, setSession] = useState<DialogueSessionWithMessages | null>(null);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [ending, setEnding] = useState(false);
  const [actionInFlight, setActionInFlight] = useState<string | null>(null);
  const [injectRecipient, setInjectRecipient] = useState<"customer" | "designer">(
    "designer",
  );
  const [injectContent, setInjectContent] = useState("");
  const [error, setError] = useState("");

  // Elapsed-time ticker for in-flight slow ops (claude calls up to 180s).
  // Set when ANY long-running action starts; cleared when it finishes.
  const [actionStartedAt, setActionStartedAt] = useState<number | null>(null);
  const [elapsedSec, setElapsedSec] = useState(0);

  const messagesEndRef = useRef<HTMLDivElement | null>(null);

  const inFlight = actionInFlight !== null || creating || ending;

  useEffect(() => {
    if (!inFlight) {
      setActionStartedAt(null);
      setElapsedSec(0);
      return;
    }
    if (actionStartedAt === null) {
      setActionStartedAt(Date.now());
      setElapsedSec(0);
    }
    const interval = window.setInterval(() => {
      if (actionStartedAt !== null) {
        setElapsedSec(Math.floor((Date.now() - actionStartedAt) / 1000));
      }
    }, 1000);
    return () => window.clearInterval(interval);
  }, [inFlight, actionStartedAt]);

  // --- Loaders ---

  const fetchSessionDetail = useCallback(async (sessionId: string) => {
    try {
      const detail = await getDialogueSessionApi(sessionId);
      setSession(detail);
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : "Nepodarilo sa načítať session.";
      setError(msg);
    }
  }, []);

  const refresh = useCallback(async () => {
    if (!isDirector) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError("");
    try {
      const sessions = await listDialogueSessionsApi();
      const active = sessions.find(
        (s) =>
          s.status === "active" &&
          (!selectedProject || s.project_slug === selectedProject.slug),
      );
      if (active) {
        await fetchSessionDetail(active.id);
      } else {
        setSession(null);
      }
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : "Nepodarilo sa načítať dialógy.";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [isDirector, selectedProject, fetchSessionDetail]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Auto-scroll to newest message when list grows.
  useEffect(() => {
    if (messagesEndRef.current) {
      messagesEndRef.current.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [session?.messages.length]);

  // --- Actions ---

  async function handleCreateSession() {
    if (!selectedProject) return;
    setCreating(true);
    setError("");
    try {
      const newSess = await createDialogueSessionApi({
        project_slug: selectedProject.slug,
        version_id: selectedVersion?.versionId ?? null,
      });
      await fetchSessionDetail(newSess.id);
    } catch (e) {
      const msg =
        e instanceof ApiError && e.message
          ? `Nepodarilo sa spustiť zákaznícky dialóg: ${e.message}`
          : "Nepodarilo sa spustiť zákaznícky dialóg.";
      setError(msg);
    } finally {
      setCreating(false);
    }
  }

  async function handleEndSession() {
    if (!session) return;
    if (
      !window.confirm(
        "Naozaj ukončiť zákaznícky dialóg? Oba agenti sa ukončia.",
      )
    )
      return;
    setEnding(true);
    try {
      await endDialogueSessionApi(session.id);
      setSession(null);
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : "Nepodarilo sa ukončiť dialóg.";
      setError(msg);
    } finally {
      setEnding(false);
    }
  }

  async function handleTriggerCustomer() {
    if (!session) return;
    setActionInFlight("trigger");
    try {
      await triggerCustomerNextQuestionApi(session.id);
      await fetchSessionDetail(session.id);
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : "Žiadosť o ďalšiu otázku zlyhala.";
      setError(msg);
    } finally {
      setActionInFlight(null);
    }
  }

  async function handleInject() {
    if (!session || !injectContent.trim()) return;
    setActionInFlight("inject");
    try {
      await directorInjectMessageApi(session.id, {
        recipient: injectRecipient,
        content: injectContent.trim(),
      });
      setInjectContent("");
      await fetchSessionDetail(session.id);
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : "Odoslanie správy zlyhalo.";
      setError(msg);
    } finally {
      setActionInFlight(null);
    }
  }

  async function handleApprove(messageId: string) {
    setActionInFlight(`approve-${messageId}`);
    try {
      await approveDialogueMessageApi(messageId);
      await fetchSessionDetail(session!.id);
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : "Schválenie zlyhalo.";
      setError(msg);
    } finally {
      setActionInFlight(null);
    }
  }

  async function handleReject(messageId: string) {
    setActionInFlight(`reject-${messageId}`);
    try {
      await rejectDialogueMessageApi(messageId);
      await fetchSessionDetail(session!.id);
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : "Zamietnutie zlyhalo.";
      setError(msg);
    } finally {
      setActionInFlight(null);
    }
  }

  // --- Render ---

  if (!isDirector) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 bg-slate-950 p-6 text-center">
        <Lock className="h-10 w-10 text-slate-700" />
        <h2 className="text-sm font-semibold text-slate-300">Zákaznícky dialóg</h2>
        <p className="max-w-md text-xs text-slate-500">
          Zákaznícky dialóg je v1 dostupný iba pre rolu{" "}
          <code className="rounded bg-slate-800 px-1 py-0.5">ri</code>{" "}
          (Director — mediator role).
        </p>
      </div>
    );
  }

  const triggerInFlight = actionInFlight === "trigger";
  const injectInFlight = actionInFlight === "inject";

  return (
    <div className="flex h-full flex-col bg-slate-950">
      {/* Header */}
      <div className="flex flex-shrink-0 items-center justify-between gap-3 border-b border-slate-800 bg-slate-900 px-4 py-2.5">
        <div className="flex min-w-0 items-center gap-3">
          <h1 className="text-sm font-semibold text-slate-100">Zákaznícky dialóg</h1>
          {session && (
            <>
              <span className="text-xs text-slate-600">·</span>
              <span className="truncate font-mono text-xs text-slate-400">
                {session.project_slug}
              </span>
            </>
          )}
        </div>

        <div className="flex items-center gap-2">
          {session && session.status === "active" && (
            <span className="flex items-center gap-1.5 rounded-full bg-green-500/10 px-2 py-0.5 text-[10px] text-green-400">
              <span className="h-1.5 w-1.5 rounded-full bg-green-400" />
              beží · {session.message_count} správ
            </span>
          )}
          <button
            onClick={() => void refresh()}
            className="text-slate-500 transition-colors hover:text-slate-200"
            title="Obnoviť"
          >
            <RefreshCw className="h-3.5 w-3.5" />
          </button>
          {session && session.status === "active" && (
            <button
              onClick={() => void handleEndSession()}
              disabled={ending}
              className="flex items-center gap-1 rounded border border-red-500/40 px-2 py-0.5 text-xs text-red-400 transition-colors hover:bg-red-500/10 disabled:cursor-wait disabled:opacity-60"
            >
              {ending ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <X className="h-3 w-3" />
              )}
              {ending ? "Ukončujem…" : "Ukončiť zákaznícky dialóg"}
            </button>
          )}
        </div>
      </div>

      {/* Error banner */}
      {error && (
        <div className="flex-shrink-0 border-b border-red-500/30 bg-red-500/10 px-4 py-2 text-xs text-red-400">
          {error}
        </div>
      )}

      {/* Body */}
      <div className="flex-1 overflow-hidden">
        {loading || creating ? (
          <div className="flex h-full flex-col items-center justify-center gap-3 text-xs text-slate-500">
            <div className="flex items-center gap-2">
              <Loader2 className="h-4 w-4 animate-spin" />
              {creating
                ? "Spúšťam zákaznícky dialóg (2 agenti)…"
                : "Načítavam stav…"}
            </div>
            {creating && (
              <div className="text-[10px] text-slate-600">
                Inicializujem Customer + Designer agentov — môže trvať 30-90 sekúnd
                {elapsedSec > 0 && ` · ${elapsedSec}s`}
              </div>
            )}
          </div>
        ) : session ? (
          // State C — active or ended session
          <div className="flex h-full flex-col">
            {/* Messages */}
            <div className="flex-1 overflow-y-auto p-4 space-y-3">
              {session.messages.length === 0 ? (
                <div className="text-center text-xs text-slate-500 py-8">
                  <MessagesSquare className="h-8 w-8 mx-auto mb-2 text-slate-700" />
                  Zatiaľ žiadne správy. Klikni{" "}
                  <span className="text-primary-400">
                    Vyžiadať ďalšiu otázku od Customer
                  </span>{" "}
                  alebo pošli vlastnú správu nižšie.
                </div>
              ) : (
                session.messages.map((msg) => (
                  <DialogueMessageBubble
                    key={msg.id}
                    message={msg}
                    actionInFlight={actionInFlight}
                    onApprove={handleApprove}
                    onReject={handleReject}
                  />
                ))
              )}
              <div ref={messagesEndRef} />
            </div>

            {/* Director controls */}
            {session.status === "active" && (
              <div className="flex-shrink-0 border-t border-slate-800 bg-slate-900 p-3 space-y-2">
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => void handleTriggerCustomer()}
                    disabled={triggerInFlight}
                    className="flex items-center gap-1.5 rounded-lg bg-fuchsia-600 hover:bg-fuchsia-500 px-3 py-1.5 text-xs font-medium text-white disabled:cursor-wait disabled:opacity-60"
                  >
                    {triggerInFlight ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : (
                      <Play className="h-3 w-3 fill-current" />
                    )}
                    {triggerInFlight
                      ? "Generujem otázku…"
                      : "Vyžiadať ďalšiu otázku od Customer"}
                  </button>
                  <span className="text-[10px] text-slate-600">
                    Customer vygeneruje ďalšiu otázku zo svojho coverage plánu.
                  </span>
                </div>

                <div className="flex items-start gap-2 pt-2 border-t border-slate-800">
                  <select
                    value={injectRecipient}
                    onChange={(e) =>
                      setInjectRecipient(e.target.value as "customer" | "designer")
                    }
                    disabled={injectInFlight}
                    className="bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs text-slate-200 disabled:opacity-60"
                  >
                    <option value="designer">Designerovi</option>
                    <option value="customer">Customer-ovi</option>
                  </select>
                  <textarea
                    value={injectContent}
                    onChange={(e) => setInjectContent(e.target.value)}
                    disabled={injectInFlight}
                    placeholder="Tvoja správa (po odoslaní ide rovno na adresáta)…"
                    rows={2}
                    className="flex-1 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs text-slate-100 resize-none focus:outline-none focus:border-primary-500 disabled:opacity-60"
                  />
                  <button
                    onClick={() => void handleInject()}
                    disabled={!injectContent.trim() || injectInFlight}
                    className="flex items-center gap-1 self-start rounded-lg bg-primary-600 hover:bg-primary-500 px-3 py-1.5 text-xs font-medium text-white disabled:cursor-wait disabled:opacity-60"
                  >
                    {injectInFlight ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : (
                      <Send className="h-3 w-3" />
                    )}
                    {injectInFlight
                      ? `Posielam ${recipientDative(injectRecipient)}…`
                      : `Poslať ${recipientDative(injectRecipient)}`}
                  </button>
                </div>

                {/* Per-panel progress indicator — shown whenever any
                    slow action is in flight. claude calls can take up
                    to 3 min; without this the UI appears frozen. */}
                {actionInFlight !== null && (
                  <div className="pt-1 space-y-1">
                    <div className="h-0.5 w-full overflow-hidden rounded bg-slate-800">
                      <div className="h-full w-1/3 animate-pulse rounded bg-fuchsia-500" />
                    </div>
                    <div className="text-[10px] text-slate-500">
                      Čakám na claude — môže trvať až 3 minúty
                      {elapsedSec > 0 && ` · uplynulo ${elapsedSec}s`}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        ) : !selectedProject ? (
          // State A — no pinned project
          <div className="flex h-full flex-col items-center justify-center gap-4 p-6 text-center">
            <FolderOpen className="h-10 w-10 text-slate-700" />
            <h2 className="text-sm font-semibold text-slate-300">
              Nemáš vybraný projekt
            </h2>
            <p className="max-w-md text-xs text-slate-500">
              Zákaznícky dialóg beží nad konkrétnym projektom. Otvor{" "}
              <span className="font-mono">Projekty</span> a pripni projekt.
            </p>
            <button
              onClick={() => navigate("/projects")}
              className="rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-500"
            >
              Otvoriť Projects
            </button>
          </div>
        ) : (
          // State B — pinned project + no active session
          <div className="flex h-full flex-col items-center justify-center gap-4 p-6 text-center">
            <p className="text-xs text-slate-500">
              Žiadny aktívny zákaznícky dialóg pre{" "}
              <span className="font-mono text-slate-300">{selectedProject.name}</span>.
            </p>
            <button
              onClick={() => void handleCreateSession()}
              disabled={creating}
              className="flex items-center gap-2 rounded-lg bg-primary-600 px-4 py-2 text-xs font-medium text-white hover:bg-primary-500 disabled:cursor-wait disabled:opacity-60"
            >
              {creating ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Play className="h-3.5 w-3.5 fill-current" />
              )}
              {creating
                ? "Spúšťam (2 agenti)…"
                : `Spustiť zákaznícky dialóg pre ${selectedProject.name}`}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Message bubble ───────────────────────────────────────────────────────

interface MessageBubbleProps {
  message: DialogueMessage;
  actionInFlight: string | null;
  onApprove: (id: string) => void;
  onReject: (id: string) => void;
}

function DialogueMessageBubble({
  message,
  actionInFlight,
  onApprove,
  onReject,
}: MessageBubbleProps) {
  const showActions = message.status === "pending" && message.author !== "director";
  const ts = new Date(message.created_at).toLocaleTimeString("sk-SK", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });

  const approveInFlight = actionInFlight === `approve-${message.id}`;
  const rejectInFlight = actionInFlight === `reject-${message.id}`;
  const recipient = approveRecipientDative(message.author);

  return (
    <div
      className={`rounded-r-lg border-l-4 ${AUTHOR_ACCENT[message.author]} px-3 py-2 text-sm`}
    >
      <div className="flex items-center justify-between gap-2 mb-1">
        <div className="flex items-center gap-2 text-xs text-slate-300">
          <span>{AUTHOR_EMOJI[message.author]}</span>
          <span className="font-semibold">{AUTHOR_LABEL[message.author]}</span>
          <span className="text-slate-600">·</span>
          <span className="text-slate-500 font-mono text-[10px]">{ts}</span>
        </div>
        <span
          className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${STATUS_BADGE[message.status]}`}
        >
          {message.status}
        </span>
      </div>
      <div
        className="prose prose-sm prose-invert max-w-none text-slate-200 leading-relaxed
                   prose-headings:text-slate-100 prose-headings:font-semibold prose-headings:mt-3 prose-headings:mb-1.5
                   prose-h1:text-base prose-h2:text-sm prose-h3:text-xs
                   prose-p:my-1.5 prose-p:text-sm
                   prose-strong:text-slate-100
                   prose-code:text-fuchsia-300 prose-code:bg-slate-800/60 prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:before:content-none prose-code:after:content-none prose-code:text-[0.8em]
                   prose-pre:bg-slate-950 prose-pre:border prose-pre:border-slate-800 prose-pre:rounded prose-pre:text-[0.75em] prose-pre:my-2
                   prose-ul:my-1.5 prose-ol:my-1.5 prose-li:my-0
                   prose-blockquote:border-l-fuchsia-500/50 prose-blockquote:text-slate-300 prose-blockquote:italic
                   prose-hr:border-slate-800 prose-hr:my-3
                   prose-a:text-cyan-400 prose-a:no-underline hover:prose-a:underline"
      >
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            table: ({ ...props }) => (
              <div className="my-2 overflow-x-auto">
                <table
                  className="w-full border-collapse border border-slate-700 text-xs"
                  {...props}
                />
              </div>
            ),
            thead: ({ ...props }) => (
              <thead className="bg-slate-800/60" {...props} />
            ),
            th: ({ ...props }) => (
              <th
                className="border border-slate-700 px-2 py-1 text-left font-semibold text-slate-100"
                {...props}
              />
            ),
            td: ({ ...props }) => (
              <td className="border border-slate-800 px-2 py-1 align-top" {...props} />
            ),
          }}
        >
          {message.content}
        </ReactMarkdown>
      </div>
      {showActions && (
        <div className="flex items-center gap-2 mt-2 pt-2 border-t border-slate-800">
          <button
            onClick={() => onApprove(message.id)}
            disabled={approveInFlight || rejectInFlight}
            className="flex items-center gap-1 rounded bg-emerald-600 hover:bg-emerald-500 px-2 py-0.5 text-[10px] font-medium text-white disabled:cursor-wait disabled:opacity-60"
          >
            {approveInFlight ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <Check className="h-3 w-3" />
            )}
            {approveInFlight
              ? `Posielam ${recipient}…`
              : `Schváliť — poslať ${recipient}`}
          </button>
          <button
            onClick={() => onReject(message.id)}
            disabled={approveInFlight || rejectInFlight}
            className="flex items-center gap-1 rounded border border-red-500/40 px-2 py-0.5 text-[10px] text-red-400 hover:bg-red-500/10 disabled:cursor-wait disabled:opacity-60"
          >
            {rejectInFlight ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <X className="h-3 w-3" />
            )}
            {rejectInFlight ? "Zamietam…" : "Zamietnuť otázku"}
          </button>
        </div>
      )}
    </div>
  );
}
