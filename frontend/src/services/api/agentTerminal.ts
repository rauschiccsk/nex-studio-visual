/**
 * API client for ``/api/v1/agent-terminal/*``.
 *
 * Backend: :file:`backend/api/routes/agent_terminal.py`. Surfaces the
 * embedded PTY-backed claude CLI sessions used by Designer / Implementer
 * / Auditor pages — REST for lifecycle, WebSocket for IO streaming.
 *
 * All endpoints require the ``ri`` role; the auth wrapper in ``api.ts``
 * surfaces 403 as a thrown ``ApiError`` the page can render as a Lock
 * placeholder.
 */

import api from "../api";

export type AgentRole = "designer" | "implementer" | "auditor" | "coordinator";

export type TerminatedBy = "idle" | "user" | "crash" | "server_restart";

export interface AgentTerminalSession {
  id: string;
  user_id: string;
  role: AgentRole;
  project_slug: string;
  pid: number;
  created_at: string;
  ended_at: string | null;
  exit_code: number | null;
  terminated_by: TerminatedBy | null;
  last_activity_at: string;
}

export interface SpawnRequest {
  role: AgentRole;
  project_slug: string;
}

/** Spawn a fresh claude CLI process for ``(role, project_slug)``. */
export function spawnAgentTerminalApi(
  payload: SpawnRequest,
): Promise<AgentTerminalSession> {
  return api.post<AgentTerminalSession>("/agent-terminal/spawn", payload);
}

/** List active (``ended_at IS NULL``) sessions for the current user. */
export function listAgentTerminalSessionsApi(): Promise<AgentTerminalSession[]> {
  return api.get<AgentTerminalSession[]>("/agent-terminal/sessions");
}

/** Explicit End session — SIGTERM, grace, SIGKILL. Idempotent. */
export function endAgentTerminalSessionApi(
  sessionId: string,
): Promise<void> {
  return api.delete<void>(`/agent-terminal/sessions/${sessionId}`);
}

/**
 * Build the WebSocket URL for a given session. The browser ``WebSocket``
 * constructor cannot set headers, so the JWT travels as a query-string
 * ``token`` parameter — the backend route ``WS /ws/{session_id}``
 * decodes it the same way as the REST ``Authorization: Bearer`` flow.
 *
 * Scheme is derived from the current page (``wss://`` if the page is
 * served over HTTPS, otherwise ``ws://``). API base path is fixed to
 * ``/api/v1`` mirroring ``api.ts``.
 */
export function buildAgentTerminalWsUrl(
  sessionId: string,
  token: string,
): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const base =
    (import.meta.env.VITE_API_BASE_URL as string | undefined) ||
    `${protocol}//${window.location.host}`;
  // Strip http(s) → ws(s) if VITE_API_BASE_URL was an http URL.
  const wsBase = base.replace(/^http/, "ws");
  return `${wsBase}/api/v1/agent-terminal/ws/${sessionId}?token=${encodeURIComponent(token)}`;
}
