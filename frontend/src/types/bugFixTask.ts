/**
 * TypeScript type definitions for the ``BugFixTask`` domain object.
 *
 * Mirrors ``backend.schemas.bug_fix_task`` — a discrete unit of fix
 * work attached to a parent :class:`~Bug`.
 */

/**
 * Mirrors ``task_type IN ('backend', 'frontend', 'migration', 'test', 'docs')``
 * on the ``bug_fix_tasks`` table.
 */
export type BugFixTaskType =
  | "backend"
  | "frontend"
  | "migration"
  | "test"
  | "docs";

/**
 * Mirrors ``status IN ('todo', 'in_progress', 'done', 'failed')`` on
 * the ``bug_fix_tasks`` table.
 */
export type BugFixTaskStatus = "todo" | "in_progress" | "done" | "failed";

/**
 * Payload for creating a new bug fix task.
 *
 * ``number`` is auto-assigned as ``max(number) + 1`` per bug by the
 * service layer.
 */
export interface BugFixTaskCreate {
  bug_id: string;
  title: string;
  /** Detailed description; server default ``""``. */
  description?: string;
  task_type: BugFixTaskType;
  /** Lifecycle status; server default ``todo``. */
  status?: BugFixTaskStatus;
  estimated_minutes?: number | null;
  actual_minutes?: number | null;
  /** Checklist type injected into the CC delegation context. */
  checklist_type?: string | null;
}

/**
 * Partial update for an existing bug fix task.
 *
 * ``bug_id`` and ``number`` are immutable.
 */
export interface BugFixTaskUpdate {
  title?: string;
  description?: string;
  task_type?: BugFixTaskType;
  status?: BugFixTaskStatus;
  estimated_minutes?: number | null;
  actual_minutes?: number | null;
  checklist_type?: string | null;
}

/** Serialised representation of a bug fix task row. */
export interface BugFixTaskRead {
  id: string;
  bug_id: string;
  number: number;
  title: string;
  description: string;
  task_type: BugFixTaskType;
  status: BugFixTaskStatus;
  estimated_minutes: number | null;
  actual_minutes: number | null;
  checklist_type: string | null;
  /** ISO-8601 timestamp. */
  created_at: string;
  /** ISO-8601 timestamp. */
  updated_at: string;
}
