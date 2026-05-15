/**
 * API client for ``/api/v1/dialogue/*``.
 *
 * Backend: :file:`backend/api/routes/dialogue.py`. Customer ↔ Designer
 * Gate E orchestration with Director-mediated approval flow.
 */

import api from "../api";

export type MessageAuthor = "customer" | "designer" | "director";
export type MessageStatus = "pending" | "approved" | "delivered" | "rejected";
export type SessionStatus = "active" | "paused" | "ended";
export type TerminatedBy =
  | "user"
  | "timeout"
  | "server_restart"
  | "coverage_complete";

export interface DialogueMessage {
  id: string;
  session_id: string;
  author: MessageAuthor;
  content: string;
  status: MessageStatus;
  created_at: string;
  updated_at: string;
}

export interface DialogueSession {
  id: string;
  user_id: string;
  project_slug: string;
  version_id: string | null;
  status: SessionStatus;
  message_count: number;
  created_at: string;
  updated_at: string;
  ended_at: string | null;
  terminated_by: TerminatedBy | null;
}

export interface DialogueSessionWithMessages extends DialogueSession {
  messages: DialogueMessage[];
}

export interface CreateSessionRequest {
  project_slug: string;
  version_id?: string | null;
}

export interface DirectorInjectRequest {
  recipient: "customer" | "designer";
  content: string;
}

/** Director starts a fresh Gate E session — spawns both agents. */
export function createDialogueSessionApi(
  payload: CreateSessionRequest,
): Promise<DialogueSession> {
  return api.post<DialogueSession>("/dialogue/sessions", payload);
}

/** List sessions owned by the current user, newest first. */
export function listDialogueSessionsApi(): Promise<DialogueSession[]> {
  return api.get<DialogueSession[]>("/dialogue/sessions");
}

/** Session detail + all messages (chronological). */
export function getDialogueSessionApi(
  sessionId: string,
): Promise<DialogueSessionWithMessages> {
  return api.get<DialogueSessionWithMessages>(
    `/dialogue/sessions/${sessionId}`,
  );
}

/** Explicit End — SIGTERM both agents + grace + SIGKILL. */
export function endDialogueSessionApi(sessionId: string): Promise<void> {
  return api.delete<void>(`/dialogue/sessions/${sessionId}`);
}

/** Tell Customer agent to produce its next question. */
export function triggerCustomerNextQuestionApi(
  sessionId: string,
): Promise<DialogueMessage> {
  return api.post<DialogueMessage>(
    `/dialogue/sessions/${sessionId}/customer-next-question`,
    {},
  );
}

/** Director injects own message — auto-delivered to recipient. */
export function directorInjectMessageApi(
  sessionId: string,
  payload: DirectorInjectRequest,
): Promise<DialogueMessage> {
  return api.post<DialogueMessage>(
    `/dialogue/sessions/${sessionId}/director-inject`,
    payload,
  );
}

/** Director approves a pending message — forwards to recipient agent. */
export function approveDialogueMessageApi(
  messageId: string,
): Promise<DialogueMessage> {
  return api.post<DialogueMessage>(
    `/dialogue/messages/${messageId}/approve`,
    {},
  );
}

/** Director rejects a pending message — audit trail, no delivery. */
export function rejectDialogueMessageApi(
  messageId: string,
): Promise<DialogueMessage> {
  return api.post<DialogueMessage>(
    `/dialogue/messages/${messageId}/reject`,
    {},
  );
}

/**
 * Build the WebSocket URL for the real-time event stream of a session.
 * Same pattern as agent-terminal (token via query-string because the
 * browser WebSocket API can't set headers).
 */
export function buildDialogueWsUrl(sessionId: string, token: string): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const base =
    (import.meta.env.VITE_API_BASE_URL as string | undefined) ||
    `${protocol}//${window.location.host}`;
  const wsBase = base.replace(/^http/, "ws");
  return `${wsBase}/api/v1/dialogue/sessions/${sessionId}/stream?token=${encodeURIComponent(token)}`;
}
