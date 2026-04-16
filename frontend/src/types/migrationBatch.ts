/**
 * TypeScript type definitions for the ``MigrationBatch`` domain object.
 *
 * Mirrors ``backend.schemas.migration_batch`` — one row per migration
 * extract-or-load invocation.
 */

/** Mirrors ``direction IN ('extract', 'load')``. */
export type MigrationBatchDirection = "extract" | "load";

/**
 * Mirrors ``status IN ('pending', 'running', 'completed', 'failed')``
 * on the ``migration_batches`` table.
 */
export type MigrationBatchStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed";

/** Payload for creating a new migration batch. */
export interface MigrationBatchCreate {
  project_id: string;
  /** Migration category, e.g. ``PAB``, ``GSC``, ``STK``. Max 10 chars. */
  category: string;
  /** Batch direction; server default ``extract``. */
  direction?: MigrationBatchDirection;
  /** Lifecycle status; server default ``pending``. */
  status?: MigrationBatchStatus;
  source_count?: number | null;
  target_count?: number | null;
  /** Number of errors encountered during the batch; server default ``0``. */
  error_count?: number | null;
  error_log?: string | null;
  /** ISO-8601 timestamp when the batch started running. */
  started_at?: string | null;
  /** ISO-8601 timestamp when the batch finished. */
  completed_at?: string | null;
}

/**
 * Partial update for an existing migration batch.
 *
 * ``project_id``, ``category`` and ``direction`` are immutable — the
 * batch identity must not be rewritten after the fact.
 */
export interface MigrationBatchUpdate {
  status?: MigrationBatchStatus;
  source_count?: number | null;
  target_count?: number | null;
  error_count?: number | null;
  error_log?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
}

/** Serialised representation of a migration batch row. */
export interface MigrationBatchRead {
  id: string;
  project_id: string;
  category: string;
  direction: MigrationBatchDirection;
  status: MigrationBatchStatus;
  source_count: number | null;
  target_count: number | null;
  error_count: number | null;
  error_log: string | null;
  started_at: string | null;
  completed_at: string | null;
  /** ISO-8601 timestamp. */
  created_at: string;
}
