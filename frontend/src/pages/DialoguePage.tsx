/**
 * DialoguePage — Gate E Customer ↔ Designer review (Director-mediated).
 *
 * Director directive 2026-05-15: 4. ICC agent (Customer) systematicky
 * kladie Designerovi otázky pred Implementer spawn-om. Director vidí
 * každú správu pred doručením a schvaľuje (plný-gate mode).
 *
 * Tri render stavy:
 *
 *   A. Žiadny ``selectedProject`` → CTA na /projects (pin a project).
 *   B. ``selectedProject`` set + no active session → "Spustiť Gate E"
 *      button → POST /dialogue/sessions.
 *   C. Active session → chronological message stream + Director controls
 *      (trigger Customer / inject / approve / reject / end).
 *
 * Permissions: ``ri`` only. Non-ri users see Lock placeholder.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
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
import { ApiError, TOKEN_STORAGE_KEY } from "@/services/api";
import {
  approveDialogueMessageApi,
  buildDialogueWsUrl,
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
  designer: "border-l-fuchsia-400 bg-fuchsia-500/5", // designer = pen icon, same family
  director: "border-l-slate-500 bg-slate-700/30",
};

// Note: Designer accent overridden below to use cyan for visual distinction
// from Customer (both have pen-y emojis); keeping the const ordered for clarity.
AUTHOR_ACCENT.designer = "border-l-cyan-400 bg-cyan-500/5";

const STATUS_BADGE: Record<DialogueMessage["status"], string> = {
  pending: "bg-amber-500/15 text-amber-400 border border-amber-500/30",
  approved: "bg-emerald-500/15 text-emerald-400 border border-emerald-500/30",
  delivered: "bg-slate-700/40 text-slate-400 border border-slate-600",
  rejected: "bg-rose-500/15 text-rose-400 border border-rose-500/30",
};

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

  const wsRef = useRef<WebSocket | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);

  const token =
    typeof window !== "undefined"
      ? window.localStorage.getItem(TOKEN_STORAGE_KEY)
      : null;

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
      const msg = e instanceof ApiError ? e.message : "Nepodarilo sa načítať sessions.";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [isDirector, selectedProject, fetchSessionDetail]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // --- WebSocket lifecycle ---

  useEffect(() => {
    if (!session || !token || session.status !== "active") return;
    const ws = new WebSocket(buildDialogueWsUrl(session.id, token));
    wsRef.current = ws;

    ws.onmessage = (ev) => {
      try {
        const payload = JSON.parse(ev.data);
        if (payload.type === "message" || payload.type === "message_updated") {
          // Refetch session detail to get the new message + updated message list.
          void fetchSessionDetail(session.id);
        } else if (payload.type === "session_ended") {
          void refresh();
        }
      } catch {
        // ignore malformed
      }
    };

    return () => {
      try {
        ws.close();
      } catch {
        // already closed
      }
      wsRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.id, session?.status, token]);

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
          ? `Nepodarilo sa spustiť Gate E: ${e.message}`
          : "Nepodarilo sa spustiť Gate E.";
      setError(msg);
    } finally {
      setCreating(false);
    }
  }

  async function handleEndSession() {
    if (!session) return;
    if (!window.confirm("Naozaj ukončiť Gate E session? Oba agenti sa ukončia.")) return;
    setEnding(true);
    try {
      await endDialogueSessionApi(session.id);
      setSession(null);
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : "Nepodarilo sa ukončiť session.";
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
      // WS will deliver the actual pending message once Customer settles.
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : "Trigger zlyhal.";
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
      const msg = e instanceof ApiError ? e.message : "Inject zlyhal.";
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
      const msg = e instanceof ApiError ? e.message : "Approve zlyhal.";
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
      const msg = e instanceof ApiError ? e.message : "Reject zlyhal.";
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
        <h2 className="text-sm font-semibold text-slate-300">Gate E Dialogue</h2>
        <p className="max-w-md text-xs text-slate-500">
          Dialogue je v1 dostupný iba pre rolu{" "}
          <code className="rounded bg-slate-800 px-1 py-0.5">ri</code>{" "}
          (Director — mediator role).
        </p>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col bg-slate-950">
      {/* Header */}
      <div className="flex flex-shrink-0 items-center justify-between gap-3 border-b border-slate-800 bg-slate-900 px-4 py-2.5">
        <div className="flex min-w-0 items-center gap-3">
          <h1 className="text-sm font-semibold text-slate-100">Gate E Dialogue</h1>
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
              running · {session.message_count} msgs
            </span>
          )}
          <button
            onClick={() => void refresh()}
            className="text-slate-500 transition-colors hover:text-slate-200"
            title="Refresh"
          >
            <RefreshCw className="h-3.5 w-3.5" />
          </button>
          {session && session.status === "active" && (
            <button
              onClick={() => void handleEndSession()}
              disabled={ending}
              className="flex items-center gap-1 rounded border border-red-500/40 px-2 py-0.5 text-xs text-red-400 transition-colors hover:bg-red-500/10 disabled:opacity-40"
            >
              <X className="h-3 w-3" />
              End session
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
          <div className="flex h-full items-center justify-center gap-2 text-xs text-slate-500">
            <Loader2 className="h-4 w-4 animate-spin" />
            {creating ? "Spúšťam Gate E (2 agenti)…" : "Načítavam stav…"}
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
                  <span className="text-primary-400">Trigger Customer next question</span>{" "}
                  alebo inject vlastnú správu nižšie.
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
                    disabled={actionInFlight === "trigger"}
                    className="flex items-center gap-1.5 rounded-lg bg-fuchsia-600 hover:bg-fuchsia-500 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-40"
                  >
                    <Play className="h-3 w-3 fill-current" />
                    Trigger Customer next question
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
                    className="bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs text-slate-200"
                  >
                    <option value="designer">→ Designer</option>
                    <option value="customer">→ Customer</option>
                  </select>
                  <textarea
                    value={injectContent}
                    onChange={(e) => setInjectContent(e.target.value)}
                    placeholder="Director-injected message (auto-delivered)…"
                    rows={2}
                    className="flex-1 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs text-slate-100 resize-none focus:outline-none focus:border-primary-500"
                  />
                  <button
                    onClick={() => void handleInject()}
                    disabled={!injectContent.trim() || actionInFlight === "inject"}
                    className="flex items-center gap-1 self-start rounded-lg bg-primary-600 hover:bg-primary-500 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-40"
                  >
                    <Send className="h-3 w-3" />
                    Send
                  </button>
                </div>
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
              Gate E Dialogue beží nad konkrétnym projektom. Otvor{" "}
              <span className="font-mono">Projects</span> a pripni projekt.
            </p>
            <button
              onClick={() => navigate("/projects")}
              className="rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-500"
            >
              → Otvor Projects
            </button>
          </div>
        ) : (
          // State B — pinned project + no active session
          <div className="flex h-full flex-col items-center justify-center gap-4 p-6 text-center">
            <p className="text-xs text-slate-500">
              Žiadna aktívna Gate E session pre{" "}
              <span className="font-mono text-slate-300">{selectedProject.name}</span>.
            </p>
            <button
              onClick={() => void handleCreateSession()}
              disabled={creating}
              className="flex items-center gap-2 rounded-lg bg-primary-600 px-4 py-2 text-xs font-medium text-white hover:bg-primary-500 disabled:opacity-40"
            >
              <Play className="h-3.5 w-3.5 fill-current" />
              Spustiť Gate E pre {selectedProject.name}
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
        <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${STATUS_BADGE[message.status]}`}>
          {message.status}
        </span>
      </div>
      <div className="whitespace-pre-wrap text-slate-200 leading-relaxed">
        {message.content}
      </div>
      {showActions && (
        <div className="flex items-center gap-2 mt-2 pt-2 border-t border-slate-800">
          <button
            onClick={() => onApprove(message.id)}
            disabled={actionInFlight === `approve-${message.id}`}
            className="flex items-center gap-1 rounded bg-emerald-600 hover:bg-emerald-500 px-2 py-0.5 text-[10px] font-medium text-white disabled:opacity-40"
          >
            <Check className="h-3 w-3" />
            Approve → deliver
          </button>
          <button
            onClick={() => onReject(message.id)}
            disabled={actionInFlight === `reject-${message.id}`}
            className="flex items-center gap-1 rounded border border-red-500/40 px-2 py-0.5 text-[10px] text-red-400 hover:bg-red-500/10 disabled:opacity-40"
          >
            <X className="h-3 w-3" />
            Reject
          </button>
        </div>
      )}
    </div>
  );
}

