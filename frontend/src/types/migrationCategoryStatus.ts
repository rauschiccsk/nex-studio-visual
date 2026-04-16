/**
 * TypeScript type definitions for the ``MigrationCategoryStatus``
 * domain object.
 *
 * Mirrors ``backend.schemas.migration_category_status`` — exactly one
 * status row per category per project (``(project_id, category)`` is
 * uniquely constrained).
 */

/**
 * Mirrors ``status IN ('pending', 'in_progress', 'completed', 'failed')``
 * on the ``migration_category_status`` table.
 */
export type MigrationCategoryStatusStatus =
  | "pending"
  | "in_progress"
  | "completed"
  | "failed";

/** Payload for creating a new migration category status row. */
export interface MigrationCategoryStatusCreate {
  project_id: string;
  /** Migration category, e.g. ``PAB``, ``GSC``. Max 20 chars. */
  category: string;
  /** Lifecycle status; server default ``pending``. */
  status?: MigrationCategoryStatusStatus;
  /** ISO-8601 timestamp of the last batch run for this category. */
  last_run_at?: string | null;
  /** Manual notes, e.g. encoding issues found. */
  notes?: string | null;
}

/**
 * Partial update for an existing migration category status row.
 *
 * ``project_id`` and ``category`` are immutable — the row identity
 * must not be rewritten.
 */
export interface MigrationCategoryStatusUpdate {
  status?: MigrationCategoryStatusStatus;
  last_run_at?: string | null;
  notes?: string | null;
}

/** Serialised representation of a migration category status row. */
export interface MigrationCategoryStatusRead {
  id: string;
  project_id: string;
  category: string;
  status: MigrationCategoryStatusStatus;
  last_run_at: string | null;
  notes: string | null;
  /** ISO-8601 timestamp. */
  created_at: string;
  /** ISO-8601 timestamp. */
  updated_at: string;
}
