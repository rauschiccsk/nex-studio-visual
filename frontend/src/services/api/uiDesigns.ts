/**
 * API client for UIDesign — Step 2B of the pipeline.
 *
 *   GET    /ui-designs             → listUIDesigns
 *   GET    /ui-designs/{id}        → getUIDesign
 *   POST   /ui-designs             → createUIDesign
 *   PATCH  /ui-designs/{id}        → updateUIDesign
 *   DELETE /ui-designs/{id}        → deleteUIDesign
 *   POST   /ui-designs/{id}/chat   → chatUIDesign (SSE)
 *   POST   /ui-designs/{id}/generate → generateUIDesign (SSE)
 */

import api from "../api";
import { TOKEN_STORAGE_KEY } from "../api";
import type { PaginatedResponse } from "../../types/common";
import type { UIDesignCreate, UIDesignRead, UIDesignUpdate } from "../../types/uiDesign";

const API_PREFIX = "/api/v1";

function resolveBaseUrl(): string {
  const fromEnv = import.meta.env.VITE_API_BASE_URL;
  if (typeof fromEnv === "string" && fromEnv.length > 0) return fromEnv.replace(/\/$/, "");
  return "";
}

function authHeaders(): Record<string, string> {
  const token = typeof window !== "undefined" ? window.localStorage.getItem(TOKEN_STORAGE_KEY) : null;
  const h: Record<string, string> = { "Content-Type": "application/json", Accept: "text/event-stream" };
  if (token) h.Authorization = `Bearer ${token}`;
  return h;
}

export interface ListUIDesignsParams {
  project_id?: string;
  skip?: number;
  limit?: number;
}

export function listUIDesigns(params?: ListUIDesignsParams): Promise<PaginatedResponse<UIDesignRead>> {
  return api.get<PaginatedResponse<UIDesignRead>>("/ui-designs", {
    params: params as Record<string, string | number | undefined>,
  });
}

export function getUIDesign(id: string): Promise<UIDesignRead> {
  return api.get<UIDesignRead>(`/ui-designs/${id}`);
}

export function createUIDesign(data: UIDesignCreate): Promise<UIDesignRead> {
  return api.post<UIDesignRead>("/ui-designs", data);
}

export function updateUIDesign(id: string, data: UIDesignUpdate): Promise<UIDesignRead> {
  return api.patch<UIDesignRead>(`/ui-designs/${id}`, data);
}

export function deleteUIDesign(id: string): Promise<void> {
  return api.delete<void>(`/ui-designs/${id}`);
}

// ── SSE helpers ────────────────────────────────────────────────────────────

export type UIDesignSSEEvent =
  | { type: "chat_chunk"; content: string }
  | { type: "html_chunk"; content: string }
  | { type: "done" }
  | { type: "error"; content: string };

async function _consumeUIDesignStream(
  url: string,
  body: object,
  onEvent: (event: UIDesignSSEEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify(body),
      signal,
      credentials: "same-origin",
    });
    if (!response.ok) {
      const text = await response.text();
      onEvent({ type: "error", content: `Request failed (${response.status}): ${text}` });
      return;
    }
    const reader = response.body?.getReader();
    if (!reader) { onEvent({ type: "error", content: "Response body is not readable" }); return; }

    const decoder = new TextDecoder();
    let buffer = "";
    // eslint-disable-next-line no-constant-condition
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed || !trimmed.startsWith("data: ")) continue;
        try {
          const event = JSON.parse(trimmed.slice(6)) as UIDesignSSEEvent;
          onEvent(event);
        } catch { continue; }
      }
    }
    if (buffer.trim().startsWith("data: ")) {
      try { onEvent(JSON.parse(buffer.trim().slice(6)) as UIDesignSSEEvent); } catch { /* ignore */ }
    }
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") return;
    onEvent({ type: "error", content: err instanceof Error ? err.message : String(err) });
  }
}

export interface ChatHistoryItem {
  role: "user" | "assistant";
  content: string;
}

export function chatUIDesign(
  uiDesignId: string,
  message: string,
  currentContent: string,
  currentHtml: string,
  history: ChatHistoryItem[],
  onEvent: (event: UIDesignSSEEvent) => void,
): AbortController {
  const controller = new AbortController();
  const url = `${resolveBaseUrl()}${API_PREFIX}/ui-designs/${uiDesignId}/chat`;
  _consumeUIDesignStream(
    url,
    { message, current_content: currentContent, current_html: currentHtml, history },
    onEvent,
    controller.signal,
  );
  return controller;
}

export function generateUIDesign(
  uiDesignId: string,
  projectName: string,
  profspecContent: string,
  onEvent: (event: UIDesignSSEEvent) => void,
): AbortController {
  const controller = new AbortController();
  const url = `${resolveBaseUrl()}${API_PREFIX}/ui-designs/${uiDesignId}/generate`;
  _consumeUIDesignStream(
    url,
    { project_name: projectName, profspec_content: profspecContent },
    onEvent,
    controller.signal,
  );
  return controller;
}
