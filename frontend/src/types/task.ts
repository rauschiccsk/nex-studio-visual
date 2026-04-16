/**
 * TypeScript type definitions for the ``Task`` domain object.
 *
 * Mirrors ``backend.schemas.task`` — leaf level in the hierarchical
 * Epic → Feat → Task task tree.
 */

/**
 * Mirrors ``task_type IN ('backend', 'frontend', 'migration', 'test', 'docs')``
 * on the ``tasks`` table.
 */
export type TaskType = "backend" | "frontend" | "migration" | "test" | "docs";

/**
 * Mirrors ``status IN ('todo', 'in_progress', 'done', 'failed')`` on
 * the ``tasks`` table.
 */
export type TaskStatus = "todo" | "in_progress" | "done" | "failed";

/**
 * Payload for creating a new task.
 *
 * ``number`` is auto-assigned as ``max(number) + 1`` per feat by the
 * service layer.
 */
export interface TaskCreate {
  feat_id: string;
  title: string;
  /** Detailed description; server default ``""``. */
  description?: string;
  /** Task type discriminator. */
  task_type: TaskType;
  /** Lifecycle status; server default ``todo``. */
  status?: TaskStatus;
  estimated_minutes?: number | null;
  actual_minutes?: number | null;
  /** Checklist type injected into the CC delegation context. */
  checklist_type?: string | null;
}

/**
 * Partial update for an existing task.
 *
 * ``feat_id`` and ``number`` are immutable.
 */
export interface TaskUpdate {
  title?: string;
  description?: string;
  task_type?: TaskType;
  status?: TaskStatus;
  estimated_minutes?: number | null;
  actual_minutes?: number | null;
  checklist_type?: string | null;
}

/** Serialised representation of a task row. */
export interface TaskRead {
  id: string;
  feat_id: string;
  number: number;
  title: string;
  description: string;
  task_type: TaskType;
  status: TaskStatus;
  estimated_minutes: number | null;
  actual_minutes: number | null;
  checklist_type: string | null;
  /** ISO-8601 timestamp. */
  created_at: string;
  /** ISO-8601 timestamp. */
  updated_at: string;
}
