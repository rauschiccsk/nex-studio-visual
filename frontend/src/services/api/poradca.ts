// Poradca (ICCINT-167) — rozhovory s agentom, ktorý len číta a radí. Backend: `backend/api/routes/poradca.py`.

import api from "../api";
import type {
  PoradcaConversation,
  PoradcaConversationDetail,
  PoradcaNewVersion,
  PoradcaProjectContext,
  PoradcaStatus,
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

export function createPoradcaConversationApi(
  slug: string,
  question: string,
  versionId: string | null,
): Promise<PoradcaConversationDetail> {
  return api.post<PoradcaConversationDetail>(`/poradca/projects/${slug}/conversations`, {
    question,
    version_id: versionId,
  });
}

export function getPoradcaConversationApi(id: string): Promise<PoradcaConversationDetail> {
  return api.get<PoradcaConversationDetail>(`/poradca/conversations/${id}`);
}

export function askPoradcaApi(id: string, question: string): Promise<PoradcaConversationDetail> {
  return api.post<PoradcaConversationDetail>(`/poradca/conversations/${id}/messages`, { question });
}

/** O čom sa rozprávame — `null` = celý projekt. */
export function setPoradcaScopeApi(id: string, versionId: string | null): Promise<PoradcaConversation> {
  return api.patch<PoradcaConversation>(`/poradca/conversations/${id}`, { version_id: versionId });
}

export function stopPoradcaApi(messageId: string): Promise<{ stopping: boolean }> {
  return api.post<{ stopping: boolean }>(`/poradca/messages/${messageId}/stop`);
}

export function newVersionFromPoradcaApi(messageId: string): Promise<PoradcaNewVersion> {
  return api.post<PoradcaNewVersion>(`/poradca/messages/${messageId}/new-version`);
}

export function buildPoradcaWsUrl(conversationId: string, token: string): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const base = (import.meta.env.VITE_API_BASE_URL as string | undefined) || `${protocol}//${window.location.host}`;
  return `${base.replace(/^http/, "ws")}/api/v1/poradca/conversations/${conversationId}/ws?token=${encodeURIComponent(token)}`;
}
