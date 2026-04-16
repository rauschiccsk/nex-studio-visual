/**
 * TypeScript type definitions for the ``Feat`` domain object.
 *
 * Mirrors ``backend.schemas.feat`` — middle level in the hierarchical
 * Epic → Feat → Task task tree.
 */

/**
 * Mirrors ``status IN ('todo', 'in_progress', 'done', 'failed')`` on
 * the ``feats`` table.
 */
export type FeatStatus = "todo" | "in_progress" | "done" | "failed";

/**
 * Payload for creating a new feat.
 *
 * ``number`` is auto-assigned as ``max(number) + 1`` per epic by the
 * service layer.  ``task_count`` and ``auto_fix_count`` are
 * server-managed counters and therefore excluded.
 */
export interface FeatCreate {
  epic_id: string;
  title: string;
  /** Detailed description; server default ``""``. */
  description?: string;
  /** Lifecycle status; server default ``todo``. */
  status?: FeatStatus;
  /** Architect's estimated duration in minutes. */
  estimated_minutes?: number | null;
}

/**
 * Partial update for an existing feat.
 *
 * ``epic_id`` and ``number`` are immutable.  ``task_count`` and
 * ``auto_fix_count`` are server-managed counters and excluded.
 * ``actual_minutes`` is exposed for backfill / correction flows.
 */
export interface FeatUpdate {
  title?: string;
  description?: string;
  status?: FeatStatus;
  estimated_minutes?: number | null;
  actual_minutes?: number | null;
}

/** Serialised representation of a feat row. */
export interface FeatRead {
  id: string;
  epic_id: string;
  number: number;
  title: string;
  description: string;
  status: FeatStatus;
  estimated_minutes: number | null;
  actual_minutes: number | null;
  task_count: number;
  auto_fix_count: number;
  /** ISO-8601 timestamp. */
  created_at: string;
  /** ISO-8601 timestamp. */
  updated_at: string;
}
