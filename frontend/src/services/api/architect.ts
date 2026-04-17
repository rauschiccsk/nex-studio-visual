/**
 * API client for the Architect feature.
 *
 * Maps to the backend routes defined in ``backend.api.routes.architect``:
 *
 *   - ``POST   /projects/{projectId}/architect``                → createSessionApi
 *   - ``GET    /projects/{projectId}/architect``                → listSessionsApi
 *   - ``GET    /architect/sessions/{sessionId}``                → getSessionApi
 *   - ``POST   /architect/sessions/{sessionId}/close``          → closeSessionApi
 *   - ``GET    /architect/sessions/{sessionId}/messages``       → listMessagesApi
 *   - ``POST   /architect/sessions/{sessionId}/message``        → sendMessageStream (SSE)
 *
 * All CRUD helpers delegate to the shared ``api`` client from
 * ``../api.ts``.  ``sendMessageStream`` uses raw ``fetch`` because it
 * needs to consume a ``ReadableStream`` of NDJSON / SSE events — the
 * generic ``api.post`` helper parses the full response body as JSON
 * which is incompatible with streaming.
 */

import api, { TOKEN_STORAGE_KEY } from "../api";
import type { PaginatedResponse } from "../../types/common";
import type {
  ArchitectSessionCreate,
  ArchitectSessionRead,
} from "../../types/architectSession";
import type { ArchitectMessageRead } from "../../types/architectMessage";
import type { ArchitectStreamEvent } from "../../types/architect";

/* ------------------------------------------------------------------ */
/*  Session CRUD                                                       */
/* ------------------------------------------------------------------ */

/** Optional filters for ``listSessionsApi``. */
export interface ListSessionsParams {
  module_id?: string;
  status?: "active" | "closed";
  skip?: number;
  limit?: number;
}

/** Create a new Architect session scoped to a project. */
export function createSessionApi(
  projectId: string,
  data: ArchitectSessionCreate,
): Promise<ArchitectSessionRead> {
  return api.post<ArchitectSessionRead>(
    `/projects/${projectId}/architect`,
    data,
  );
}

/** List Architect sessions for a project with optional filters. */
export function listSessionsApi(
  projectId: string,
  params?: ListSessionsParams,
): Promise<PaginatedResponse<ArchitectSessionRead>> {
  return api.get<PaginatedResponse<ArchitectSessionRead>>(
    `/projects/${projectId}/architect`,
    { params: params as Record<string, string | number | undefined> },
  );
}

/** Fetch a single Architect session by its UUID. */
export function getSessionApi(
  sessionId: string,
): Promise<ArchitectSessionRead> {
  return api.get<ArchitectSessionRead>(
    `/architect/sessions/${sessionId}`,
  );
}

/** Close an active Architect session. */
export function closeSessionApi(
  sessionId: string,
): Promise<ArchitectSessionRead> {
  return api.post<ArchitectSessionRead>(
    `/architect/sessions/${sessionId}/close`,
  );
}

/* ------------------------------------------------------------------ */
/*  Messages                                                           */
/* ------------------------------------------------------------------ */

/** Optional pagination for ``listMessagesApi``. */
export interface ListMessagesParams {
  skip?: number;
  limit?: number;
}

/** List messages for an Architect session (conversation order). */
export function listMessagesApi(
  sessionId: string,
  params?: ListMessagesParams,
): Promise<PaginatedResponse<ArchitectMessageRead>> {
  return api.get<PaginatedResponse<ArchitectMessageRead>>(
    `/architect/sessions/${sessionId}/messages`,
    { params: params as Record<string, string | number | undefined> },
  );
}

/* ------------------------------------------------------------------ */
/*  Streaming message (SSE via ReadableStream)                         */
/* ------------------------------------------------------------------ */

/** REST version prefix shared by every backend route. */
const API_PREFIX = "/api/v1";

/**
 * Resolve the backend base URL — mirrors the logic in ``../api.ts``.
 *
 * We duplicate this instead of importing a private helper because the
 * base ``api`` module does not export ``buildUrl``.
 */
function resolveBaseUrl(): string {
  const fromEnv = import.meta.env.VITE_API_BASE_URL;
  if (typeof fromEnv === "string" && fromEnv.length > 0) {
    return fromEnv.replace(/\/$/, "");
  }
  return "";
}

/**
 * Send a user message and stream the AI response via SSE.
 *
 * Uses raw ``fetch`` + ``ReadableStream`` to consume the NDJSON event
 * stream produced by ``POST /architect/sessions/{sessionId}/message``.
 *
 * Each SSE line has the format ``data: {"type":"chunk"|"done"|"error", ...}\n\n``.
 * The function parses each line and invokes the appropriate callback:
 *
 *   - ``onChunk`` — called for every ``type: "chunk"`` event with the
 *     partial content string.
 *   - ``onDone`` — called once when ``type: "done"`` arrives with the
 *     full assistant response and optional token counts.
 *   - ``onError`` (optional) — called when ``type: "error"`` arrives or
 *     when the stream encounters a network / parse failure.
 *
 * Returns an ``AbortController`` so the caller can cancel the stream.
 */
export function sendMessageStream(
  sessionId: string,
  content: string,
  onChunk: (content: string) => void,
  onDone: (event: ArchitectStreamEvent & { type: "done" }) => void,
  onError?: (error: Error) => void,
): AbortController {
  const controller = new AbortController();
  const baseUrl = resolveBaseUrl();
  const url = `${baseUrl}${API_PREFIX}/architect/sessions/${sessionId}/message`;

  // Read JWT token from localStorage (same key as ../api.ts).
  const token =
    typeof window !== "undefined"
      ? window.localStorage.getItem(TOKEN_STORAGE_KEY)
      : null;

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "text/event-stream",
  };
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  // Fire the fetch and consume the stream in the background.
  _consumeStream(url, headers, content, controller.signal, onChunk, onDone, onError);

  return controller;
}

/**
 * Internal — performs the fetch and processes the ReadableStream.
 *
 * Separated from ``sendMessageStream`` so the public function can
 * return the ``AbortController`` synchronously.
 */
async function _consumeStream(
  url: string,
  headers: Record<string, string>,
  content: string,
  signal: AbortSignal,
  onChunk: (content: string) => void,
  onDone: (event: ArchitectStreamEvent & { type: "done" }) => void,
  onError?: (error: Error) => void,
): Promise<void> {
  try {
    const response = await fetch(url, {
      method: "POST",
      headers,
      body: JSON.stringify({ content }),
      signal,
      credentials: "same-origin",
    });

    if (!response.ok) {
      const text = await response.text();
      throw new Error(`Architect stream failed (${response.status}): ${text}`);
    }

    const reader = response.body?.getReader();
    if (!reader) {
      throw new Error("Response body is not readable");
    }

    const decoder = new TextDecoder();
    let buffer = "";

    // eslint-disable-next-line no-constant-condition
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      // SSE lines are delimited by double newlines.
      const lines = buffer.split("\n");
      // Keep the last (potentially incomplete) line in the buffer.
      buffer = lines.pop() ?? "";

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed || !trimmed.startsWith("data: ")) continue;

        const jsonStr = trimmed.slice(6); // strip "data: " prefix
        let event: ArchitectStreamEvent;
        try {
          event = JSON.parse(jsonStr) as ArchitectStreamEvent;
        } catch {
          // Malformed JSON line — skip silently.
          continue;
        }

        switch (event.type) {
          case "chunk":
            onChunk(event.content);
            break;
          case "done":
            onDone(event);
            break;
          case "error":
            onError?.(new Error(event.content));
            break;
        }
      }
    }

    // Process any remaining data in the buffer.
    if (buffer.trim().startsWith("data: ")) {
      const jsonStr = buffer.trim().slice(6);
      try {
        const event = JSON.parse(jsonStr) as ArchitectStreamEvent;
        switch (event.type) {
          case "chunk":
            onChunk(event.content);
            break;
          case "done":
            onDone(event);
            break;
          case "error":
            onError?.(new Error(event.content));
            break;
        }
      } catch {
        // Ignore malformed trailing data.
      }
    }
  } catch (err: unknown) {
    // AbortError is expected when the caller cancels — don't surface it.
    if (err instanceof DOMException && err.name === "AbortError") return;
    onError?.(err instanceof Error ? err : new Error(String(err)));
  }
}
