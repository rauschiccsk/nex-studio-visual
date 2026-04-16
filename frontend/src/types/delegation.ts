/**
 * TypeScript type definitions for the ``Delegation`` domain object.
 *
 * Mirrors ``backend.schemas.delegation`` — one CC-agent invocation
 * attached to at most one of ``task_id`` / ``feat_id`` /
 * ``bug_fix_task_id`` / ``bug_id``.  All four parent FKs are
 * ``ON DELETE SET NULL`` at the DB level so the delegation record
 * survives deletion of the originating work item.
 */

/** Mirrors ``cc_agent IN ('ubuntu_cc')`` on the ``delegations`` table. */
export type DelegationCCAgent = "ubuntu_cc";

/**
 * Mirrors ``status IN ('pending', 'running', 'done', 'failed')`` on
 * the ``delegations`` table.
 */
export type DelegationStatus = "pending" | "running" | "done" | "failed";

/** Payload for creating a new CC delegation. */
export interface DelegationCreate {
  /** Optional parent task (``ON DELETE SET NULL``). */
  task_id?: string | null;
  /** Optional parent feat (``ON DELETE SET NULL``). */
  feat_id?: string | null;
  /** Optional parent bug fix task (``ON DELETE SET NULL``). */
  bug_fix_task_id?: string | null;
  /** Optional parent bug (``ON DELETE SET NULL``). */
  bug_id?: string | null;
  /** CC agent that will execute the delegation; server default ``ubuntu_cc``. */
  cc_agent?: DelegationCCAgent;
  /** Full CC delegation prompt injected into the agent. */
  prompt: string;
  /** Lifecycle status; server default ``pending``. */
  status?: DelegationStatus;
  /** Raw NDJSON / text stream captured from the CC agent. */
  raw_output?: string | null;
  /** Git commit hash produced by the delegation. Max 40 chars. */
  commit_hash?: string | null;
  /** ISO-8601 timestamp; server default ``NOW()``. */
  started_at?: string | null;
  /** ISO-8601 timestamp when the CC agent finished executing. */
  completed_at?: string | null;
}

/**
 * Partial update for an existing delegation.
 *
 * ``task_id``, ``feat_id``, ``bug_fix_task_id``, ``bug_id``,
 * ``cc_agent`` and ``prompt`` are immutable — the delegation's
 * execution contract is fixed at creation.
 */
export interface DelegationUpdate {
  status?: DelegationStatus;
  raw_output?: string | null;
  commit_hash?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
}

/** Serialised representation of a delegation row. */
export interface DelegationRead {
  id: string;
  task_id: string | null;
  feat_id: string | null;
  bug_fix_task_id: string | null;
  bug_id: string | null;
  cc_agent: DelegationCCAgent;
  prompt: string;
  status: DelegationStatus;
  raw_output: string | null;
  commit_hash: string | null;
  /** ISO-8601 timestamp. */
  started_at: string;
  /** ISO-8601 timestamp. */
  completed_at: string | null;
  /** ISO-8601 timestamp. */
  created_at: string;
  /** ISO-8601 timestamp. */
  updated_at: string;
}
