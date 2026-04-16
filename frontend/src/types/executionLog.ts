/**
 * TypeScript type definitions for the ``ExecutionLog`` domain object.
 *
 * Mirrors ``backend.schemas.execution_log`` — one terminal log entry
 * per delegation, carrying duration, token usage, cost and commit
 * verification state.
 *
 * ``total_cost_usd`` is ``DECIMAL(10, 6)`` on the backend; we
 * represent it as ``string`` on the wire to preserve full precision.
 */

/**
 * Mirrors ``status IN ('done', 'failed')`` on the ``execution_logs``
 * table.
 */
export type ExecutionLogStatus = "done" | "failed";

/** Payload for creating a new execution log entry. */
export interface ExecutionLogCreate {
  /** Parent delegation (``ON DELETE CASCADE``). */
  delegation_id: string;
  /** Optional task this execution targeted (``ON DELETE SET NULL``). */
  task_id?: string | null;
  /** Terminal status of the execution. */
  status: ExecutionLogStatus;
  /** Wall-clock duration of the CC delegation in seconds. */
  duration_seconds?: number | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
  /** Total USD cost — DECIMAL(10, 6) encoded as a string. */
  total_cost_usd?: string | null;
  /** Git commit hash produced by the delegation. Max 40 chars. */
  commit_hash?: string | null;
  /**
   * Whether the reported ``commit_hash`` has been confirmed via the
   * GitHub API. Server default ``false``.
   */
  commit_verified?: boolean;
}

/**
 * Partial update for an existing execution log.
 *
 * ``delegation_id`` and ``task_id`` are immutable parent references.
 * ``commit_verified`` is flipped from ``false`` to ``true`` by the
 * GitHub-verification job after the log is first written.
 */
export interface ExecutionLogUpdate {
  status?: ExecutionLogStatus;
  duration_seconds?: number | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
  total_cost_usd?: string | null;
  commit_hash?: string | null;
  commit_verified?: boolean;
}

/** Serialised representation of an execution log row. */
export interface ExecutionLogRead {
  id: string;
  delegation_id: string;
  task_id: string | null;
  status: ExecutionLogStatus;
  duration_seconds: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  /** DECIMAL(10, 6) encoded as a string on the wire. */
  total_cost_usd: string | null;
  commit_hash: string | null;
  commit_verified: boolean;
  /** ISO-8601 timestamp. */
  created_at: string;
  /** ISO-8601 timestamp. */
  updated_at: string;
}
