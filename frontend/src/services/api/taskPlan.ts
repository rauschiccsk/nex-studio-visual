/**
 * API client for Task Plan generation.
 *
 * Maps to the backend route:
 *   - ``POST /versions/{version_id}/generate-task-plan`` → generateTaskPlan (SSE)
 */

import { TOKEN_STORAGE_KEY } from "../api";
import type { TaskPlanEvent } from "../../types/taskPlan";

const API_PREFIX = "/api/v1";

function resolveBaseUrl(): string {
  const fromEnv = import.meta.env.VITE_API_BASE_URL;
  if (typeof fromEnv === "string" && fromEnv.length > 0) {
    return fromEnv.replace(/\/$/, "");
  }
  return "";
}

/**
 * Stream-generate a Task Plan for the given version from the project's DESIGN.md.
 *
 * Returns an {@link AbortController} so the caller can cancel the stream.
 *
 * @param versionId  UUID of the target version.
 * @param replaceExisting  When true, existing EPICs under this version are
 *   deleted before generating the new plan.
 * @param onProgress  Called for each ``progress`` event.
 * @param onDone  Called once with the final plan summary on ``done``.
 * @param onError  Called on ``error`` or network failure.
 * @param onValidationError  Called on ``validation_error`` (e.g. missing DESIGN.md).
 */
export function generateTaskPlan(
  versionId: string,
  replaceExisting: boolean,
  onProgress: (message: string, percent: number) => void,
  onDone: (event: Extract<TaskPlanEvent, { type: "done" }>) => void,
  onError?: (error: Error) => void,
  onValidationError?: (reason: string) => void,
): AbortController {
  const controller = new AbortController();
  const baseUrl = resolveBaseUrl();
  const url = `${baseUrl}${API_PREFIX}/versions/${versionId}/generate-task-plan`;

  const token =
    typeof window !== "undefined"
      ? window.localStorage.getItem(TOKEN_STORAGE_KEY)
      : null;

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "text/event-stream",
  };
  if (token) headers.Authorization = `Bearer ${token}`;

  _consumeStream(
    url,
    headers,
    { replace_existing: replaceExisting },
    controller.signal,
    onProgress,
    onDone,
    onError,
    onValidationError,
  );

  return controller;
}

async function _consumeStream(
  url: string,
  headers: Record<string, string>,
  body: object,
  signal: AbortSignal,
  onProgress: (message: string, percent: number) => void,
  onDone: (event: Extract<TaskPlanEvent, { type: "done" }>) => void,
  onError?: (error: Error) => void,
  onValidationError?: (reason: string) => void,
): Promise<void> {
  try {
    const response = await fetch(url, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
      signal,
      credentials: "same-origin",
    });

    if (!response.ok) {
      const text = await response.text();
      throw new Error(`Task plan generation failed (${response.status}): ${text}`);
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
        let event: TaskPlanEvent;
        try {
          event = JSON.parse(trimmed.slice(6)) as TaskPlanEvent;
        } catch {
          continue;
        }
        switch (event.type) {
          case "progress":
            onProgress(event.message, event.percent);
            break;
          case "done":
            onDone(event);
            break;
          case "error":
            onError?.(new Error(event.content));
            break;
          case "validation_error":
            onValidationError?.(event.content);
            break;
        }
      }
    }

    // Flush remaining buffer
    if (buffer.trim().startsWith("data: ")) {
      try {
        const event = JSON.parse(buffer.trim().slice(6)) as TaskPlanEvent;
        if (event.type === "done") onDone(event);
        else if (event.type === "error") onError?.(new Error(event.content));
        else if (event.type === "validation_error") onValidationError?.(event.content);
      } catch {
        // ignore malformed trailing data
      }
    }
  } catch (err: unknown) {
    if (err instanceof DOMException && err.name === "AbortError") return;
    onError?.(err instanceof Error ? err : new Error(String(err)));
  }
}
