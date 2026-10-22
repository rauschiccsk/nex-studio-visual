// DEV-175 — reading the engine's refusal to found a version over unsaved work off its 409.

import { ApiError } from "@/services/api";
import type { GitStatus } from "@/services/api/projects";

/** What the engine sends when it founds no version because the project has unsaved work. `status` is the same
 *  answer the New-Version page reads (`/git-status`); `null` when the engine could not tell — then only the sentence. */
export interface UnsavedWorkRefusal {
  message: string;
  projectId: string;
  status: GitStatus | null;
}

export function unsavedWorkOf(err: unknown): UnsavedWorkRefusal | null {
  if (!(err instanceof ApiError) || err.status !== 409) return null;
  const detail = (err.data as { detail?: { message?: unknown; project_id?: unknown; unsaved_work?: unknown } } | null)
    ?.detail;
  if (typeof detail?.project_id !== "string" || typeof detail.message !== "string") return null;
  return {
    message: detail.message,
    projectId: detail.project_id,
    status: (detail.unsaved_work as GitStatus | undefined) ?? null,
  };
}
