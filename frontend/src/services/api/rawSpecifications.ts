/**
 * API client for Raw Specifications.
 *
 * Maps to ``backend.api.routes.raw_specifications``:
 *
 *   - ``GET    /raw-specifications``           → listRawSpecifications
 *   - ``GET    /raw-specifications/{id}``      → getRawSpecification
 *   - ``POST   /raw-specifications``           → createRawSpecification
 *   - ``PATCH  /raw-specifications/{id}``      → updateRawSpecification
 *   - ``DELETE /raw-specifications/{id}``      → deleteRawSpecification
 *   - ``POST   /raw-specifications/{id}/generate`` → generateProfessionalSpec (SSE)
 */

import api, { TOKEN_STORAGE_KEY } from "../api";
import type { PaginatedResponse } from "../../types/common";
import type {
  RawSpecificationCreate,
  RawSpecificationRead,
  RawSpecificationUpdate,
} from "../../types/rawSpecification";

const API_PREFIX = "/api/v1";

function resolveBaseUrl(): string {
  const fromEnv = import.meta.env.VITE_API_BASE_URL;
  if (typeof fromEnv === "string" && fromEnv.length > 0) {
    return fromEnv.replace(/\/$/, "");
  }
  return "";
}

export interface ListRawSpecsParams {
  project_id?: string;
  status?: string;
  skip?: number;
  limit?: number;
}

export function listRawSpecifications(
  params?: ListRawSpecsParams,
): Promise<PaginatedResponse<RawSpecificationRead>> {
  return api.get<PaginatedResponse<RawSpecificationRead>>(
    "/raw-specifications",
    { params: params as Record<string, string | number | undefined> },
  );
}

export function getRawSpecification(id: string): Promise<RawSpecificationRead> {
  return api.get<RawSpecificationRead>(`/raw-specifications/${id}`);
}

export function createRawSpecification(
  data: RawSpecificationCreate,
): Promise<RawSpecificationRead> {
  return api.post<RawSpecificationRead>("/raw-specifications", data);
}

export function updateRawSpecification(
  id: string,
  data: RawSpecificationUpdate,
): Promise<RawSpecificationRead> {
  return api.patch<RawSpecificationRead>(`/raw-specifications/${id}`, data);
}

export function deleteRawSpecification(id: string): Promise<void> {
  return api.delete<void>(`/raw-specifications/${id}`);
}

/* ------------------------------------------------------------------ */
/*  Streaming generate (SSE)                                           */
/* ------------------------------------------------------------------ */

export interface GenerateSpecStreamEvent {
  type: "chunk" | "done" | "error";
  content: string;
  professional_spec_id?: string | null;
}

/**
 * Stream-generate a professional specification from a raw spec.
 *
 * Returns an AbortController so the caller can cancel the stream.
 */
export function generateProfessionalSpec(
  rawSpecId: string,
  onChunk: (content: string) => void,
  onDone: (event: GenerateSpecStreamEvent & { type: "done" }) => void,
  onError?: (error: Error) => void,
): AbortController {
  const controller = new AbortController();
  const baseUrl = resolveBaseUrl();
  const url = `${baseUrl}${API_PREFIX}/raw-specifications/${rawSpecId}/generate`;

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

  _consumeGenerateStream(url, headers, controller.signal, onChunk, onDone, onError);
  return controller;
}

async function _consumeGenerateStream(
  url: string,
  headers: Record<string, string>,
  signal: AbortSignal,
  onChunk: (content: string) => void,
  onDone: (event: GenerateSpecStreamEvent & { type: "done" }) => void,
  onError?: (error: Error) => void,
): Promise<void> {
  try {
    const response = await fetch(url, {
      method: "POST",
      headers,
      signal,
      credentials: "same-origin",
    });

    if (!response.ok) {
      const text = await response.text();
      throw new Error(`Generate stream failed (${response.status}): ${text}`);
    }

    const reader = response.body?.getReader();
    if (!reader) throw new Error("Response body is not readable");

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
        const jsonStr = trimmed.slice(6);
        let event: GenerateSpecStreamEvent;
        try {
          event = JSON.parse(jsonStr) as GenerateSpecStreamEvent;
        } catch {
          continue;
        }
        switch (event.type) {
          case "chunk":
            onChunk(event.content);
            break;
          case "done":
            onDone(event as GenerateSpecStreamEvent & { type: "done" });
            break;
          case "error":
            onError?.(new Error(event.content));
            break;
        }
      }
    }

    if (buffer.trim().startsWith("data: ")) {
      try {
        const event = JSON.parse(buffer.trim().slice(6)) as GenerateSpecStreamEvent;
        switch (event.type) {
          case "chunk":
            onChunk(event.content);
            break;
          case "done":
            onDone(event as GenerateSpecStreamEvent & { type: "done" });
            break;
          case "error":
            onError?.(new Error(event.content));
            break;
        }
      } catch {
        // ignore malformed trailing data
      }
    }
  } catch (err: unknown) {
    if (err instanceof DOMException && err.name === "AbortError") return;
    onError?.(err instanceof Error ? err : new Error(String(err)));
  }
}
