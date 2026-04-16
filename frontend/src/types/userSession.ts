/**
 * TypeScript type definitions for the ``UserSession`` domain object.
 *
 * Mirrors ``backend.schemas.user_session`` — a session row represents a
 * per-user JWT lifecycle anchor.  ``token_version`` is incremented on
 * logout to invalidate all outstanding JWTs issued against the session.
 */

/** Payload for creating a new user session (server-side use). */
export interface UserSessionCreate {
  /** User the session belongs to. */
  user_id: string;
  /**
   * Monotonically increasing counter, bumped on logout to rotate
   * outstanding JWTs.  Defaults to ``0`` on the server.
   */
  token_version?: number;
  /**
   * Timestamp of the most recent authenticated request.  ``null``
   * defers to the DB-level ``NOW()`` default.
   */
  last_seen_at?: string | null;
}

/**
 * Partial update for an existing user session.
 *
 * ``user_id`` is immutable — sessions are created or deleted, never
 * reassigned to another user.
 */
export interface UserSessionUpdate {
  token_version?: number;
  last_seen_at?: string | null;
}

/** Serialised representation of a user session row. */
export interface UserSessionRead {
  id: string;
  user_id: string;
  token_version: number;
  /** ISO-8601 timestamp. */
  last_seen_at: string;
  /** ISO-8601 timestamp. */
  created_at: string;
  /** ISO-8601 timestamp. */
  updated_at: string;
}
