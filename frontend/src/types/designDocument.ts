/**
 * TypeScript type definitions for the ``DesignDocument`` domain object.
 *
 * Mirrors ``backend.schemas.design_document`` — DESIGN.md / BEHAVIOR.md
 * documents with per-module splitting (see DESIGN.md D-04).
 */

/**
 * Mirrors ``doc_type IN ('design', 'behavior')`` on the
 * ``design_documents`` table.
 */
export type DesignDocumentType = "design" | "behavior";

/** Payload for creating a new design or behavior document. */
export interface DesignDocumentCreate {
  project_id: string;
  /** ``null`` denotes a Foundation / project-level document. */
  module_id?: string | null;
  /** ``design`` | ``behavior``. */
  doc_type: DesignDocumentType;
  /** Full markdown content of the document. */
  content: string;
  /** Monotonic version; server default ``1``. */
  version?: number;
  /** ``ri``-role approver of the document. */
  approved_by?: string | null;
  /** ISO-8601 timestamp of approval. */
  approved_at?: string | null;
}

/**
 * Partial update for an existing design or behavior document.
 *
 * ``project_id`` and ``doc_type`` are immutable; ``module_id`` remains
 * mutable because project-level documents are expressed through the
 * same column (``null`` = Foundation / project-level).
 */
export interface DesignDocumentUpdate {
  module_id?: string | null;
  content?: string;
  version?: number;
  approved_by?: string | null;
  approved_at?: string | null;
}

/** Serialised representation of a design document row. */
export interface DesignDocumentRead {
  id: string;
  project_id: string;
  module_id: string | null;
  doc_type: DesignDocumentType;
  content: string;
  version: number;
  approved_by: string | null;
  approved_at: string | null;
  /** ISO-8601 timestamp. */
  created_at: string;
  /** ISO-8601 timestamp. */
  updated_at: string;
}
