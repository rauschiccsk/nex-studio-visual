/**
 * TypeScript type definitions for the ``Epic`` domain object.
 *
 * Mirrors ``backend.schemas.epic`` — top-level entry in the hierarchical
 * Epic → Feat → Task task tree.
 */

/**
 * Mirrors ``status IN ('planned', 'in_progress', 'done')`` on the
 * ``epics`` table.
 */
export type EpicStatus = "planned" | "in_progress" | "done";

/**
 * Payload for creating a new epic.
 *
 * ``number`` is auto-assigned as ``max(number) + 1`` per project by
 * the service layer.
 */
export interface EpicCreate {
  project_id: string;
  /** ``null`` denotes a project-level epic (single-module projects). */
  module_id?: string | null;
  title: string;
  /** Lifecycle status; server default ``planned``. */
  status?: EpicStatus;
}

/**
 * Partial update for an existing epic.
 *
 * ``project_id`` and ``number`` are immutable; ``module_id`` remains
 * mutable.
 */
export interface EpicUpdate {
  module_id?: string | null;
  title?: string;
  status?: EpicStatus;
}

/** Serialised representation of an epic row. */
export interface EpicRead {
  id: string;
  project_id: string;
  module_id: string | null;
  number: number;
  title: string;
  status: EpicStatus;
  /** ISO-8601 timestamp. */
  created_at: string;
  /** ISO-8601 timestamp. */
  updated_at: string;
}
