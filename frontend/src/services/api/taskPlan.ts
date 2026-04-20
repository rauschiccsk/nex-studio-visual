/**
 * API client for the Task Plan pipeline.
 *
 * Covers:
 *   - GET  /versions/{id}/task-plan        → fetchTaskPlan
 *   - POST /versions/{id}/generate-task-plan (SSE) → generateTaskPlan
 *   - POST /versions/{id}/append-epic       (SSE) → appendEpic
 *   - POST /versions/{id}/reset-tasks       → resetTasks
 *   - POST /versions/{id}/reset-plan        → resetPlan
 *   - POST /tasks                           → createTask
 *   - PATCH /tasks/{id}                     → patchTask
 *   - DELETE /tasks/{id}                    → deleteTask
 *   - POST /feats                           → createFeat
 *   - DELETE /feats/{id}                    → deleteFeat
 *   - DELETE /epics/{id}                    → deleteEpic
 */

import { TOKEN_STORAGE_KEY } from "../api";
import type { TaskPlanEpic, TaskPlanEvent, TaskPriority, TaskStatus } from "../../types/taskPlan";

const API_PREFIX = "/api/v1";

type TaskPlanDoneEvent = Extract<TaskPlanEvent, { type: "done" }>;

function resolveBaseUrl(): string {
  const fromEnv = import.meta.env.VITE_API_BASE_URL;
  if (typeof fromEnv === "string" && fromEnv.length > 0) {
    return fromEnv.replace(/\/$/, "");
  }
  return "";
}

function authHeaders(): Record<string, string> {
  const token =
    typeof window !== "undefined" ? window.localStorage.getItem(TOKEN_STORAGE_KEY) : null;
  const h: Record<string, string> = { "Content-Type": "application/json" };
  if (token) h.Authorization = `Bearer ${token}`;
  return h;
}

const base = () => `${resolveBaseUrl()}${API_PREFIX}`;

// ---------------------------------------------------------------------------
// Fetch existing plan
// ---------------------------------------------------------------------------

/**
 * Fetch the existing Task Plan for a version from the database.
 * Returns null when no EPICs exist yet.
 */
export async function fetchTaskPlan(versionId: string): Promise<TaskPlanDoneEvent | null> {
  try {
    const resp = await fetch(`${base()}/versions/${versionId}/task-plan`, {
      headers: authHeaders(),
      credentials: "same-origin",
    });
    if (!resp.ok) return null;
    const data = (await resp.json()) as {
      plan: TaskPlanEpic[];
      epic_count: number;
      feat_count: number;
      task_count: number;
    };
    if (!data.plan || data.plan.length === 0) return null;
    return { type: "done", plan: data.plan, epic_count: data.epic_count, feat_count: data.feat_count, task_count: data.task_count };
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// SSE streaming helpers
// ---------------------------------------------------------------------------

async function _consumeStream(
  url: string,
  headers: Record<string, string>,
  body: object,
  signal: AbortSignal,
  onProgress: (message: string, percent: number) => void,
  onDone: (event: TaskPlanDoneEvent) => void,
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
      throw new Error(`Request failed (${response.status}): ${text}`);
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

    if (buffer.trim().startsWith("data: ")) {
      try {
        const event = JSON.parse(buffer.trim().slice(6)) as TaskPlanEvent;
        if (event.type === "done") onDone(event);
        else if (event.type === "error") onError?.(new Error(event.content));
        else if (event.type === "validation_error") onValidationError?.(event.content);
      } catch {
        /* ignore malformed trailing data */
      }
    }
  } catch (err: unknown) {
    if (err instanceof DOMException && err.name === "AbortError") return;
    onError?.(err instanceof Error ? err : new Error(String(err)));
  }
}

/**
 * Stream-generate a Task Plan for the given version from the project's DESIGN.md.
 * Returns an AbortController so the caller can cancel the stream.
 */
export function generateTaskPlan(
  versionId: string,
  replaceExisting: boolean,
  onProgress: (message: string, percent: number) => void,
  onDone: (event: TaskPlanDoneEvent) => void,
  onError?: (error: Error) => void,
  onValidationError?: (reason: string) => void,
): AbortController {
  const controller = new AbortController();
  const headers = { ...authHeaders(), Accept: "text/event-stream" };
  _consumeStream(
    `${base()}/versions/${versionId}/generate-task-plan`,
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

/**
 * Stream-append a new EPIC to the existing task plan (non-destructive).
 * Returns an AbortController so the caller can cancel the stream.
 */
export function appendEpic(
  versionId: string,
  onProgress: (message: string, percent: number) => void,
  onDone: (event: TaskPlanDoneEvent) => void,
  onError?: (error: Error) => void,
  onValidationError?: (reason: string) => void,
): AbortController {
  const controller = new AbortController();
  const headers = { ...authHeaders(), Accept: "text/event-stream" };
  _consumeStream(
    `${base()}/versions/${versionId}/append-epic`,
    headers,
    {},
    controller.signal,
    onProgress,
    onDone,
    onError,
    onValidationError,
  );
  return controller;
}

// ---------------------------------------------------------------------------
// Plan-level actions
// ---------------------------------------------------------------------------

export async function resetTasks(versionId: string): Promise<void> {
  const resp = await fetch(`${base()}/versions/${versionId}/reset-tasks`, {
    method: "POST",
    headers: authHeaders(),
    credentials: "same-origin",
  });
  if (!resp.ok) throw new Error(`Reset tasks failed (${resp.status})`);
}

export async function resetPlan(versionId: string): Promise<void> {
  const resp = await fetch(`${base()}/versions/${versionId}/reset-plan`, {
    method: "POST",
    headers: authHeaders(),
    credentials: "same-origin",
  });
  if (!resp.ok) throw new Error(`Reset plan failed (${resp.status})`);
}

// ---------------------------------------------------------------------------
// Task CRUD
// ---------------------------------------------------------------------------

export interface TaskCreatePayload {
  feat_id: string;
  title: string;
  task_type: "backend" | "frontend" | "migration" | "test" | "docs";
  priority?: TaskPriority;
}

export async function createTask(payload: TaskCreatePayload): Promise<{ id: string; number: number }> {
  const resp = await fetch(`${base()}/tasks`, {
    method: "POST",
    headers: authHeaders(),
    credentials: "same-origin",
    body: JSON.stringify(payload),
  });
  if (!resp.ok) throw new Error(`Create task failed (${resp.status})`);
  return (await resp.json()) as { id: string; number: number };
}

export async function patchTask(
  taskId: string,
  updates: { status?: TaskStatus; priority?: TaskPriority; title?: string },
): Promise<void> {
  const resp = await fetch(`${base()}/tasks/${taskId}`, {
    method: "PATCH",
    headers: authHeaders(),
    credentials: "same-origin",
    body: JSON.stringify(updates),
  });
  if (!resp.ok) throw new Error(`Patch task failed (${resp.status})`);
}

export async function deleteTask(taskId: string): Promise<void> {
  const resp = await fetch(`${base()}/tasks/${taskId}`, {
    method: "DELETE",
    headers: authHeaders(),
    credentials: "same-origin",
  });
  if (!resp.ok) throw new Error(`Delete task failed (${resp.status})`);
}

// ---------------------------------------------------------------------------
// Feat CRUD
// ---------------------------------------------------------------------------

export async function createFeat(epicId: string, title: string): Promise<{ id: string; number: number }> {
  const resp = await fetch(`${base()}/feats`, {
    method: "POST",
    headers: authHeaders(),
    credentials: "same-origin",
    body: JSON.stringify({ epic_id: epicId, title }),
  });
  if (!resp.ok) throw new Error(`Create feat failed (${resp.status})`);
  return (await resp.json()) as { id: string; number: number };
}

export async function deleteFeat(featId: string): Promise<void> {
  const resp = await fetch(`${base()}/feats/${featId}`, {
    method: "DELETE",
    headers: authHeaders(),
    credentials: "same-origin",
  });
  if (!resp.ok) throw new Error(`Delete feat failed (${resp.status})`);
}

// ---------------------------------------------------------------------------
// Epic CRUD
// ---------------------------------------------------------------------------

export async function deleteEpic(epicId: string): Promise<void> {
  const resp = await fetch(`${base()}/epics/${epicId}`, {
    method: "DELETE",
    headers: authHeaders(),
    credentials: "same-origin",
  });
  if (!resp.ok) throw new Error(`Delete epic failed (${resp.status})`);
}

// ---------------------------------------------------------------------------
// Feat execution (SSE)
// ---------------------------------------------------------------------------

export type FeatExecuteEvent =
  | { type: "task_start"; task_id: string; task_number: number; task_title: string }
  | { type: "chunk"; text: string; task_id: string }
  | { type: "task_done"; task_id: string; status: string }
  | { type: "feat_done"; feat_id: string; feat_status: string }
  | { type: "error"; content: string };

/**
 * Stream-execute all todo/failed tasks in a feat via CC.
 * Returns an AbortController so the caller can cancel the stream.
 */
export function executeFeat(
  featId: string,
  onEvent: (event: FeatExecuteEvent) => void,
  onError?: (error: Error) => void,
): AbortController {
  const controller = new AbortController();
  const headers = { ...authHeaders(), Accept: "text/event-stream" };

  (async () => {
    try {
      const response = await fetch(`${base()}/feats/${featId}/execute`, {
        method: "POST",
        headers,
        signal: controller.signal,
        credentials: "same-origin",
      });

      if (!response.ok) {
        const text = await response.text();
        throw new Error(`Execute feat failed (${response.status}): ${text}`);
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
          try {
            const event = JSON.parse(trimmed.slice(6)) as FeatExecuteEvent;
            onEvent(event);
          } catch {
            continue;
          }
        }
      }

      if (buffer.trim().startsWith("data: ")) {
        try {
          const event = JSON.parse(buffer.trim().slice(6)) as FeatExecuteEvent;
          onEvent(event);
        } catch {
          /* ignore */
        }
      }
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      onError?.(err instanceof Error ? err : new Error(String(err)));
    }
  })();

  return controller;
}
