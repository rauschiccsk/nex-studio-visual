/**
 * TypeScript type definitions for the ``ArchitectMessage`` domain
 * object.
 *
 * Mirrors ``backend.schemas.architect_message`` — one row per chat
 * turn in an Architect session.  Token counts and cost are typically
 * recorded only after the SSE stream completes (see DESIGN.md §1.5).
 *
 * ``cost_usd`` is ``DECIMAL(10, 6)`` on the backend; we represent it
 * as ``string`` on the wire to preserve full precision — JavaScript
 * ``number`` cannot faithfully round-trip arbitrary decimals.
 */

/** Mirrors ``role IN ('user', 'assistant')``. */
export type ArchitectMessageRole = "user" | "assistant";

/** Payload for creating a new Architect chat message. */
export interface ArchitectMessageCreate {
  /** Architect session the message belongs to. */
  session_id: string;
  /** ``user`` | ``assistant``. */
  role: ArchitectMessageRole;
  /** Full message content. */
  content: string;
  /** Anthropic API input tokens consumed by the message. */
  input_tokens?: number | null;
  /** Anthropic API output tokens produced by the message. */
  output_tokens?: number | null;
  /** USD cost — DECIMAL(10, 6) encoded as a string on the wire. */
  cost_usd?: string | null;
}

/**
 * Partial update for an existing Architect chat message.
 *
 * ``session_id``, ``role`` and ``content`` are immutable — chat
 * history is append-only.  Only usage / cost columns remain mutable
 * for backfill after the SSE stream completes.
 */
export interface ArchitectMessageUpdate {
  input_tokens?: number | null;
  output_tokens?: number | null;
  cost_usd?: string | null;
}

/** Serialised representation of an Architect message row. */
export interface ArchitectMessageRead {
  id: string;
  session_id: string;
  role: ArchitectMessageRole;
  content: string;
  input_tokens: number | null;
  output_tokens: number | null;
  /** DECIMAL(10, 6) encoded as a string on the wire. */
  cost_usd: string | null;
  /** ISO-8601 timestamp. */
  created_at: string;
  /** ISO-8601 timestamp. */
  updated_at: string;
}
