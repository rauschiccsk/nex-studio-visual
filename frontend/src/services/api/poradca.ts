// Poradca (ICCINT-167) — conversations with a read-and-advise-only agent. Backend: `backend/api/routes/poradca.py`.

import api, { fetchBlob } from "../api";
import type {
  PoradcaAgentTicketsAttached,
  PoradcaConversation,
  PoradcaConversationDetail,
  PoradcaBacklogSaved,
  PoradcaProjectContext,
  PoradcaReviewConversation,
  PoradcaStatus,
  PoradcaTicketCreate,
  PoradcaTicketDraft,
  PoradcaTicketFounded,
} from "../../types/poradca";

export function getPoradcaStatusApi(): Promise<PoradcaStatus> {
  return api.get<PoradcaStatus>("/poradca/status");
}

export function getPoradcaContextApi(slug: string): Promise<PoradcaProjectContext> {
  return api.get<PoradcaProjectContext>(`/poradca/projects/${slug}/context`);
}

export function listPoradcaConversationsApi(slug: string): Promise<PoradcaConversation[]> {
  return api.get<PoradcaConversation[]>(`/poradca/projects/${slug}/conversations`);
}

/** DEV-52 — a screenshot pasted into the question: its name and its content, base64 (as the Claude API takes it). */
export interface PoradcaImageUpload {
  name: string;
  data: string;
}

/** `review` (DEV-138): a card from „Overiť s Poradcom“ founds the conversation for the cards of its version and phase. */
export function createPoradcaConversationApi(
  slug: string,
  question: string,
  versionId: string | null,
  attachments: PoradcaImageUpload[] = [],
  review = false,
): Promise<PoradcaConversationDetail> {
  return api.post<PoradcaConversationDetail>(`/poradca/projects/${slug}/conversations`, {
    question,
    version_id: versionId,
    attachments,
    review,
  });
}

/** DEV-138 — where a card from „Overiť s Poradcom“ goes: his conversation for the cards of this version and phase. */
export function getPoradcaReviewConversationApi(slug: string, versionId: string): Promise<PoradcaReviewConversation> {
  return api.get<PoradcaReviewConversation>(
    `/poradca/projects/${slug}/review-conversation?${new URLSearchParams({ version_id: versionId }).toString()}`,
  );
}

export function getPoradcaConversationApi(id: string): Promise<PoradcaConversationDetail> {
  return api.get<PoradcaConversationDetail>(`/poradca/conversations/${id}`);
}

export function askPoradcaApi(
  id: string,
  question: string,
  attachments: PoradcaImageUpload[] = [],
): Promise<PoradcaConversationDetail> {
  return api.post<PoradcaConversationDetail>(`/poradca/conversations/${id}/messages`, { question, attachments });
}

/** What we are talking about — `null` = the whole project. */
export function setPoradcaScopeApi(id: string, versionId: string | null): Promise<PoradcaConversation> {
  return api.patch<PoradcaConversation>(`/poradca/conversations/${id}`, { version_id: versionId });
}

/** Rename a conversation — the order in the list does not change. */
export function renamePoradcaConversationApi(id: string, title: string): Promise<PoradcaConversation> {
  return api.put<PoradcaConversation>(`/poradca/conversations/${id}/title`, { title });
}

/** Delete a conversation permanently — the text is gone, the cost stays in Náklady. While Poradca is answering, 409. */
export function deletePoradcaConversationApi(id: string): Promise<void> {
  return api.delete<void>(`/poradca/conversations/${id}`);
}

export function stopPoradcaApi(messageId: string): Promise<{ stopping: boolean }> {
  return api.post<{ stopping: boolean }>(`/poradca/messages/${messageId}/stop`);
}

/** DEV-29: the request in Poradca's answer goes to the project's Zásobník — no version comes of it. */
export function saveRequestToBacklogApi(messageId: string): Promise<PoradcaBacklogSaved> {
  return api.post<PoradcaBacklogSaved>(`/poradca/messages/${messageId}/backlog`);
}

/** DEV-119 — the preview of the ticket Poradca proposes: what the cockpit adds and similar open tickets. */
export function getPoradcaTicketDraftApi(messageId: string): Promise<PoradcaTicketDraft> {
  return api.get<PoradcaTicketDraft>(`/poradca/messages/${messageId}/plane-ticket`);
}

/** DEV-119 — the edited proposal becomes a ticket in Plane, once; a second click returns the same ticket. */
export function foundPoradcaTicketApi(messageId: string, body: PoradcaTicketCreate): Promise<PoradcaTicketFounded> {
  return api.post<PoradcaTicketFounded>(`/poradca/messages/${messageId}/plane-ticket`, body);
}

/** DEV-218 — the tickets the answer names, read from Plane by the cockpit, become files for the agent. */
export function attachAgentTicketsApi(messageId: string): Promise<PoradcaAgentTicketsAttached> {
  return api.post<PoradcaAgentTicketsAttached>(`/poradca/messages/${messageId}/agent-tickets`);
}

export function buildPoradcaWsUrl(conversationId: string, token: string): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const base = (import.meta.env.VITE_API_BASE_URL as string | undefined) || `${protocol}//${window.location.host}`;
  return `${base.replace(/^http/, "ws")}/api/v1/poradca/conversations/${conversationId}/ws?token=${encodeURIComponent(token)}`;
}

/** DEV-52 — one screenshot of a question, fetched with the reader's sign-in (the backend checks the conversation). */
export function getPoradcaAttachmentApi(conversationId: string, attachmentId: string): Promise<Blob> {
  return fetchBlob(`/poradca/conversations/${conversationId}/attachments/${attachmentId}`);
}
