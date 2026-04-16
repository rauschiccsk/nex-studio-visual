/**
 * TypeScript type definitions for the ``AutoFixAttempt`` domain object.
 *
 * Mirrors ``backend.schemas.auto_fix_attempt`` — one row per auto-fix
 * retry of a failed feat delegation.  ``attempt_number`` is auto-
 * assigned as ``max(attempt_number) + 1`` per feat by the service
 * layer; ``(feat_id, attempt_number)`` is uniquely constrained.
 */

/** Payload for creating a new auto-fix attempt. */
export interface AutoFixAttemptCreate {
  /** Feat whose failed delegation triggered this auto-fix attempt. */
  feat_id: string;
  /** Accumulated error context from the failed delegation. */
  error_description: string;
  /** Remediation summary; typically populated after the fix completes. */
  fix_description?: string | null;
  /** Optional reference to the auto-fix delegation spawned. */
  delegation_id?: string | null;
}

/**
 * Partial update for an existing auto-fix attempt.
 *
 * ``feat_id`` and ``attempt_number`` are immutable — the attempt
 * identity and its position within the feat's retry sequence must not
 * be rewritten after the fact.
 */
export interface AutoFixAttemptUpdate {
  error_description?: string;
  fix_description?: string | null;
  delegation_id?: string | null;
}

/** Serialised representation of an auto-fix attempt row. */
export interface AutoFixAttemptRead {
  id: string;
  feat_id: string;
  attempt_number: number;
  error_description: string;
  fix_description: string | null;
  delegation_id: string | null;
  /** ISO-8601 timestamp. */
  created_at: string;
  /** ISO-8601 timestamp. */
  updated_at: string;
}
