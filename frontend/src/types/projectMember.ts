/**
 * TypeScript type definitions for the ``ProjectMember`` join row.
 *
 * Mirrors ``backend.schemas.project_member`` — the natural key of the
 * table is ``(project_id, user_id)``.  Both FKs are immutable so the
 * ``Update`` schema is intentionally empty and exists only for symmetry
 * with the rest of the codebase.
 */

/** Payload for creating a new project membership. */
export interface ProjectMemberCreate {
  /** Project the user is being added to. */
  project_id: string;
  /** User being granted membership in the project. */
  user_id: string;
}

/**
 * Partial update for an existing project membership.
 *
 * The natural key ``(project_id, user_id)`` is immutable — a
 * membership is either created or deleted, never rewritten.  No
 * mutable fields are therefore exposed.
 */
export type ProjectMemberUpdate = Record<string, never>;

/** Serialised representation of a project membership row. */
export interface ProjectMemberRead {
  id: string;
  project_id: string;
  user_id: string;
  /** ISO-8601 timestamp. */
  created_at: string;
  /** ISO-8601 timestamp. */
  updated_at: string;
}
