/**
 * TypeScript type definitions for the ``Bug`` domain object.
 *
 * Mirrors ``backend.schemas.bug`` — top-level bug tracking record.
 */

/** Mirrors ``severity IN ('critical', 'major', 'minor')``. */
export type BugSeverity = "critical" | "major" | "minor";

/**
 * Mirrors ``status IN ('new', 'accepted', 'in_progress', 'resolved',
 * 'wont_fix')`` on the ``bugs`` table.
 */
export type BugStatus =
  | "new"
  | "accepted"
  | "in_progress"
  | "resolved"
  | "wont_fix";

/** Mirrors ``source IN ('internal', 'customer')``. */
export type BugSource = "internal" | "customer";

/**
 * Payload for creating a new bug.
 *
 * ``bug_number`` is auto-assigned as ``max(bug_number) + 1`` per
 * project by the service layer.
 */
export interface BugCreate {
  project_id: string;
  title: string;
  description: string;
  severity: BugSeverity;
  /** Lifecycle status; server default ``new``. */
  status?: BugStatus;
  /** Source; server default ``internal``. */
  source?: BugSource;
  reported_by?: string | null;
  environment?: string | null;
  /** ISO-8601 timestamp; typically set when ``status`` → ``resolved``. */
  resolved_at?: string | null;
  /** Commit that resolved the bug. Max 40 chars. */
  commit_hash?: string | null;
  /** User who registered the bug. */
  created_by: string;
}

/**
 * Partial update for an existing bug.
 *
 * ``project_id``, ``bug_number`` and ``created_by`` are immutable
 * audit columns.  ``resolved_at`` is typically set automatically when
 * ``status`` transitions to ``resolved`` but is exposed for backfill.
 */
export interface BugUpdate {
  title?: string;
  description?: string;
  severity?: BugSeverity;
  status?: BugStatus;
  source?: BugSource;
  reported_by?: string | null;
  environment?: string | null;
  resolved_at?: string | null;
  commit_hash?: string | null;
}

/** Serialised representation of a bug row. */
export interface BugRead {
  id: string;
  project_id: string;
  bug_number: number;
  title: string;
  description: string;
  severity: BugSeverity;
  status: BugStatus;
  source: BugSource;
  reported_by: string | null;
  environment: string | null;
  resolved_at: string | null;
  commit_hash: string | null;
  created_by: string;
  /** ISO-8601 timestamp. */
  created_at: string;
  /** ISO-8601 timestamp. */
  updated_at: string;
}
