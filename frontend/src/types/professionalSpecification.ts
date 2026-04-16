/**
 * TypeScript type definitions for the ``ProfessionalSpecification``
 * domain object.
 *
 * Mirrors ``backend.schemas.professional_specification`` — the
 * AI-generated, structured markdown derived from a raw specification.
 * Once approved (``approved_by`` non-null) it unlocks DESIGN.md
 * generation.
 */

/** Payload for creating a new professional specification. */
export interface ProfessionalSpecificationCreate {
  /** Raw specification this professional spec was derived from. */
  raw_spec_id: string;
  /** Denormalised from the raw specification for query convenience. */
  project_id: string;
  /** Structured markdown content. */
  content: string;
  /** Monotonic version; server default ``1``. */
  version?: number;
  /** ``ri``-role approver. ``null`` = not yet approved. */
  approved_by?: string | null;
  /** ISO-8601 timestamp of approval. */
  approved_at?: string | null;
}

/**
 * Partial update for an existing professional specification.
 *
 * ``project_id`` and ``raw_spec_id`` are immutable foreign keys.
 */
export interface ProfessionalSpecificationUpdate {
  content?: string;
  version?: number;
  approved_by?: string | null;
  approved_at?: string | null;
}

/** Serialised representation of a professional specification row. */
export interface ProfessionalSpecificationRead {
  id: string;
  raw_spec_id: string;
  project_id: string;
  content: string;
  version: number;
  approved_by: string | null;
  approved_at: string | null;
  /** ISO-8601 timestamp. */
  created_at: string;
  /** ISO-8601 timestamp. */
  updated_at: string;
}
