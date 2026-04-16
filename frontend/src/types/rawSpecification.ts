/**
 * TypeScript type definitions for the ``RawSpecification`` domain object.
 *
 * Mirrors ``backend.schemas.raw_specification`` — the free-form customer
 * specification uploaded at the start of the spec pipeline.
 */

/** Mirrors ``input_format IN ('text', 'pdf', 'docx')``. */
export type RawSpecificationInputFormat = "text" | "pdf" | "docx";

/**
 * Mirrors ``status IN ('pending', 'processing', 'done', 'failed')`` on
 * the ``raw_specifications`` table.
 */
export type RawSpecificationStatus =
  | "pending"
  | "processing"
  | "done"
  | "failed";

/** Payload for creating a new raw customer specification. */
export interface RawSpecificationCreate {
  project_id: string;
  /** Free-form customer specification text. */
  input_text: string;
  /** Original input format; server default ``text``. */
  input_format?: RawSpecificationInputFormat;
  /** ISO-style language code; server default ``sk``. */
  language?: string;
  /** Processing status; server default ``pending``. */
  status?: RawSpecificationStatus;
  /** User who uploaded the raw specification. */
  created_by: string;
}

/**
 * Partial update for an existing raw customer specification.
 *
 * ``project_id`` and ``created_by`` are immutable foreign keys.
 */
export interface RawSpecificationUpdate {
  input_text?: string;
  input_format?: RawSpecificationInputFormat;
  language?: string;
  status?: RawSpecificationStatus;
}

/** Serialised representation of a raw specification row. */
export interface RawSpecificationRead {
  id: string;
  project_id: string;
  input_text: string;
  input_format: RawSpecificationInputFormat;
  language: string;
  status: RawSpecificationStatus;
  created_by: string;
  /** ISO-8601 timestamp. */
  created_at: string;
  /** ISO-8601 timestamp. */
  updated_at: string;
}
