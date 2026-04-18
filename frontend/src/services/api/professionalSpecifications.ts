/**
 * API client for Professional Specifications.
 *
 * Maps to ``backend.api.routes.professional_specifications``:
 *
 *   - ``GET    /professional-specifications``                → listProfessionalSpecs
 *   - ``GET    /professional-specifications/{id}``           → getProfessionalSpec
 *   - ``POST   /professional-specifications``                → createProfessionalSpec
 *   - ``PATCH  /professional-specifications/{id}``           → updateProfessionalSpec
 *   - ``DELETE /professional-specifications/{id}``           → deleteProfessionalSpec
 *   - ``POST   /professional-specifications/{id}/generate-design-doc`` → generateDesignDoc (SSE)
 */

import api, { TOKEN_STORAGE_KEY } from "../api";
import type { PaginatedResponse } from "../../types/common";
import type {
  ProfessionalSpecificationCreate,
  ProfessionalSpecificationRead,
  ProfessionalSpecificationUpdate,
} from "../../types/professionalSpecification";

const API_PREFIX = "/api/v1";

function resolveBaseUrl(): string {
  const fromEnv = import.meta.env.VITE_API_BASE_URL;
  if (typeof fromEnv === "string" && fromEnv.length > 0) {
    return fromEnv.replace(/\/$/, "");
  }
  return "";
}

export interface ListProfSpecsParams {
  project_id?: string;
  raw_spec_id?: string;
  skip?: number;
  limit?: number;
}

export function listProfessionalSpecs(
  params?: ListProfSpecsParams,
): Promise<PaginatedResponse<ProfessionalSpecificationRead>> {
  return api.get<PaginatedResponse<ProfessionalSpecificationRead>>(
    "/professional-specifications",
    { params: params as Record<string, string | number | undefined> },
  );
}

export function getProfessionalSpec(
  id: string,
): Promise<ProfessionalSpecificationRead> {
  return api.get<ProfessionalSpecificationRead>(`/professional-specifications/${id}`);
}

export function createProfessionalSpec(
  data: ProfessionalSpecificationCreate,
): Promise<ProfessionalSpecificationRead> {
  return api.post<ProfessionalSpecificationRead>("/professional-specifications", data);
}

export function updateProfessionalSpec(
  id: string,
  data: ProfessionalSpecificationUpdate,
): Promise<ProfessionalSpecificationRead> {
  return api.patch<ProfessionalSpecificationRead>(
    `/professional-specifications/${id}`,
    data,
  );
}

export function deleteProfessionalSpec(id: string): Promise<void> {
  return api.delete<void>(`/professional-specifications/${id}`);
}

/* ------------------------------------------------------------------ */
/*  Streaming generate-design-doc (SSE)                                */
/* ------------------------------------------------------------------ */

export interface GenerateDesignDocStreamEvent {
  type: "chunk" | "done" | "error";
  content: string;
  design_doc_id?: string | null;
}

/**
 * Stream-generate a DESIGN.md or BEHAVIOR.md from a professional spec.
 *
 * Returns an AbortController so the caller can cancel the stream.
 */
export function generateDesignDoc(
  profSpecId: string,
  docType: "design" | "behavior",
  onChunk: (content: string) => void,
  onDone: (event: GenerateDesignDocStreamEvent & { type: "done" }) => void,
  onError?: (error: Error) => void,
): AbortController {
  const controller = new AbortController();
  const baseUrl = resolveBaseUrl();
  const url =
    `${baseUrl}${API_PREFIX}/professional-specifications/${profSpecId}/generate-design-doc` +
    `?doc_type=${docType}`;

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

  _consumeDesignDocStream(url, headers, controller.signal, onChunk, onDone, onError);
  return controller;
}

async function _consumeDesignDocStream(
  url: string,
  headers: Record<string, string>,
  signal: AbortSignal,
  onChunk: (content: string) => void,
  onDone: (event: GenerateDesignDocStreamEvent & { type: "done" }) => void,
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
      throw new Error(`Design doc stream failed (${response.status}): ${text}`);
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
        let event: GenerateDesignDocStreamEvent;
        try {
          event = JSON.parse(jsonStr) as GenerateDesignDocStreamEvent;
        } catch {
          continue;
        }
        switch (event.type) {
          case "chunk":
            onChunk(event.content);
            break;
          case "done":
            onDone(event as GenerateDesignDocStreamEvent & { type: "done" });
            break;
          case "error":
            onError?.(new Error(event.content));
            break;
        }
      }
    }

    if (buffer.trim().startsWith("data: ")) {
      try {
        const event = JSON.parse(
          buffer.trim().slice(6),
        ) as GenerateDesignDocStreamEvent;
        switch (event.type) {
          case "chunk":
            onChunk(event.content);
            break;
          case "done":
            onDone(event as GenerateDesignDocStreamEvent & { type: "done" });
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
