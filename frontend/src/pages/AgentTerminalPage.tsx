/**
 * AgentTerminalPage — full-page embedded claude CLI session in NEX
 * Studio for one of the three agent roles (Designer / Implementer /
 * Auditor).
 *
 * Project anchor is read from :file:`store/activeContextStore.ts`
 * (Director directive 2026-05-13: the Pin in ``/projects`` is the
 * single source of "which project am I working on"; every
 * context-needing page consumes it). The page does **not** show a
 * project picker — if nothing is pinned, it shows a CTA pointing the
 * user at ``/projects``.
 *
 * Three render states:
 *
 *   A. No ``selectedProject`` → CTA "Vyber projekt v Projects".
 *   B. ``selectedProject`` set, no active session for ``(user, role)``
 *      → "Spustiť <role> pre <project>" button → POST /spawn → attach.
 *   C. Active session running → terminal full-page (xterm.js via
 *      :file:`components/AgentTerminal.tsx`).
 *
 * A pinned-project change does **not** auto-end a running session.
 * The session is bound to its ``project_slug`` in the DB row and
 * represents a specific conversation continuity; user explicitly
 * ends it before spawning for a different project.
 *
 * Permissions: ``ri`` only (Director). Non-ri users see a Lock panel.
 */

import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Lock, Loader2, RefreshCw, X, FolderOpen, Play } from "lucide-react";

import { useAuthStore } from "@/store/authStore";
import { useActiveContextStore } from "@/store/activeContextStore";
import { ApiError, TOKEN_STORAGE_KEY } from "@/services/api";
import {
  listAgentTerminalSessionsApi,
  spawnAgentTerminalApi,
  endAgentTerminalSessionApi,
  type AgentRole,
  type AgentTerminalSession,
} from "@/services/api/agentTerminal";
import { AgentTerminal } from "@/components/AgentTerminal";

const ROLE_LABEL: Record<AgentRole, string> = {
  designer: "Designer",
  implementer: "Implementer",
  auditor: "Auditor",
};

export interface AgentTerminalPageProps {
  role: AgentRole;
}

export default function AgentTerminalPage({ role }: AgentTerminalPageProps) {
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);
  const isDirector = user?.role === "ri";

  const selectedProject = useActiveContextStore((s) => s.selectedProject);

  const [session, setSession] = useState<AgentTerminalSession | null>(null);
  const [loading, setLoading] = useState(true);
  const [spawning, setSpawning] = useState(false);
  const [ending, setEnding] = useState(false);
  const [error, setError] = useState("");

  // Token for WebSocket auth (browser WS API can't set headers, so it
  // travels in the query string). Read once at mount — the WS will
  // re-mount whenever ``session`` changes anyway.
  const token =
    typeof window !== "undefined"
      ? window.localStorage.getItem(TOKEN_STORAGE_KEY)
      : null;

  const refresh = useCallback(async () => {
    if (!isDirector) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError("");
    try {
      const rows = await listAgentTerminalSessionsApi();
      const active = rows.find((r) => r.role === role && r.ended_at === null);
      setSession(active ?? null);
    } catch (e) {
      const msg =
        e instanceof ApiError ? e.message : "Nepodarilo sa načítať sessions.";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [role, isDirector]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function handleSpawn() {
    if (!selectedProject) return;
    setSpawning(true);
    setError("");
    try {
      const row = await spawnAgentTerminalApi({
        role,
        project_slug: selectedProject.slug,
      });
      setSession(row);
    } catch (e) {
      const msg =
        e instanceof ApiError && e.message
          ? `Nepodarilo sa spustiť session: ${e.message}`
          : "Nepodarilo sa spustiť session.";
      setError(msg);
    } finally {
      setSpawning(false);
    }
  }

  async function handleEndSession() {
    if (!session) return;
    if (!window.confirm("Naozaj ukončiť session? Aktívna konverzácia zanikne.")) return;
    setEnding(true);
    try {
      await endAgentTerminalSessionApi(session.id);
      setSession(null);
    } catch (e) {
      const msg =
        e instanceof ApiError ? e.message : "Nepodarilo sa ukončiť session.";
      setError(msg);
    } finally {
      setEnding(false);
    }
  }

  // --- Render ---

  if (!isDirector) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 bg-slate-950 p-6 text-center">
        <Lock className="h-10 w-10 text-slate-700" />
        <h2 className="text-sm font-semibold text-slate-300">
          {ROLE_LABEL[role]} terminál
        </h2>
        <p className="max-w-md text-xs text-slate-500">
          Embedded agent terminál je v1 dostupný iba pre rolu{" "}
          <code className="rounded bg-slate-800 px-1 py-0.5">ri</code>{" "}
          (Director). Per-project membership pre <code>ha</code> a{" "}
          <code>shu</code> príde v ďalšej iterácii.
        </p>
      </div>
    );
  }

  // Display label for the session's project — falls back to the slug
  // when we don't have a name handy (i.e. an active session attached to
  // a project that was unpinned in the meantime).
  const sessionProjectLabel =
    session && selectedProject?.slug === session.project_slug
      ? selectedProject.name
      : session?.project_slug ?? "";

  return (
    <div className="flex h-full flex-col bg-slate-950">
      {/* Header chrome */}
      <div className="flex flex-shrink-0 items-center justify-between gap-3 border-b border-slate-800 bg-slate-900 px-4 py-2.5">
        <div className="flex min-w-0 items-center gap-3">
          <h1 className="text-sm font-semibold text-slate-100">
            {ROLE_LABEL[role]}
          </h1>
          {session ? (
            <>
              <span className="text-xs text-slate-600">·</span>
              <span className="truncate font-mono text-xs text-slate-400">
                {sessionProjectLabel}
              </span>
            </>
          ) : selectedProject ? (
            <>
              <span className="text-xs text-slate-600">·</span>
              <span className="truncate font-mono text-xs text-slate-500">
                {selectedProject.name}
              </span>
            </>
          ) : null}
        </div>

        <div className="flex items-center gap-2">
          {session && (
            <span className="flex items-center gap-1.5 rounded-full bg-green-500/10 px-2 py-0.5 text-[10px] text-green-400">
              <span className="h-1.5 w-1.5 rounded-full bg-green-400" />
              running · pid {session.pid}
            </span>
          )}
          <button
            onClick={() => void refresh()}
            className="text-slate-500 transition-colors hover:text-slate-200"
            title="Refresh"
          >
            <RefreshCw className="h-3.5 w-3.5" />
          </button>
          {session && (
            <button
              onClick={() => void handleEndSession()}
              disabled={ending}
              className="flex items-center gap-1 rounded border border-red-500/40 px-2 py-0.5 text-xs text-red-400 transition-colors hover:bg-red-500/10 disabled:opacity-40"
              title="Ukončí session (SIGTERM)"
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
        {loading || spawning ? (
          <div className="flex h-full items-center justify-center gap-2 text-xs text-slate-500">
            <Loader2 className="h-4 w-4 animate-spin" />
            {spawning ? "Spúšťam claude CLI…" : "Načítavam stav…"}
          </div>
        ) : session && token ? (
          // State C — active session: terminal full-page.
          <AgentTerminal
            key={session.id}
            sessionId={session.id}
            token={token}
            onEnded={() => void refresh()}
          />
        ) : !selectedProject ? (
          // State A — no project pinned: CTA to /projects.
          <div className="flex h-full flex-col items-center justify-center gap-4 p-6 text-center">
            <FolderOpen className="h-10 w-10 text-slate-700" />
            <h2 className="text-sm font-semibold text-slate-300">
              Nemáš vybraný projekt
            </h2>
            <p className="max-w-md text-xs text-slate-500">
              {ROLE_LABEL[role]} sa spúšťa nad konkrétnym projektom. Otvor{" "}
              <span className="font-mono">Projects</span> a klikni na pin
              ikonu pri projekte, ktorý chceš označiť ako{" "}
              <span className="text-primary-400">Selected</span>.
            </p>
            <button
              onClick={() => navigate("/projects")}
              className="rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-500"
            >
              → Otvor Projects
            </button>
          </div>
        ) : (
          // State B — project pinned, no active session: spawn CTA.
          <div className="flex h-full flex-col items-center justify-center gap-4 p-6 text-center">
            <p className="text-xs text-slate-500">
              Žiadna aktívna {ROLE_LABEL[role]} session.
            </p>
            <button
              onClick={() => void handleSpawn()}
              disabled={spawning}
              className="flex items-center gap-2 rounded-lg bg-primary-600 px-4 py-2 text-xs font-medium text-white hover:bg-primary-500 disabled:opacity-40"
            >
              <Play className="h-3.5 w-3.5 fill-current" />
              Spustiť {ROLE_LABEL[role]} pre {selectedProject.name}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
