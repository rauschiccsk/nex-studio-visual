/**
 * TypeScript type definitions for the ``ArchitectSession`` domain
 * object.
 *
 * Mirrors ``backend.schemas.architect_session`` — a chat session
 * scoped to either a project (``module_id`` is ``null``) or a specific
 * project module (``module_id`` set).
 */

/**
 * Mirrors ``status IN ('active', 'closed')`` on the
 * ``architect_sessions`` table.
 */
export type ArchitectSessionStatus = "active" | "closed";

/** Payload for creating a new Architect chat session. */
export interface ArchitectSessionCreate {
  /** Project the session is scoped to. */
  project_id: string;
  /** ``null`` denotes a project-level Architect session. */
  module_id?: string | null;
  /** Lifecycle status; server default ``active``. */
  status?: ArchitectSessionStatus;
  /** User who opened the Architect session. */
  created_by: string;
  /** ISO-8601 timestamp when the session was closed, if applicable. */
  closed_at?: string | null;
}

/**
 * Partial update for an existing Architect chat session.
 *
 * ``project_id`` and ``created_by`` are immutable foreign keys.
 * ``module_id`` remains mutable (``null`` denotes project-level).
 */
export interface ArchitectSessionUpdate {
  module_id?: string | null;
  status?: ArchitectSessionStatus;
  closed_at?: string | null;
}

/** Serialised representation of an Architect session row. */
export interface ArchitectSessionRead {
  id: string;
  project_id: string;
  module_id: string | null;
  status: ArchitectSessionStatus;
  created_by: string;
  /** ISO-8601 timestamp. */
  closed_at: string | null;
  /** ISO-8601 timestamp. */
  created_at: string;
  /** ISO-8601 timestamp. */
  updated_at: string;
}
