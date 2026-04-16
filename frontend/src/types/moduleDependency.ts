/**
 * TypeScript type definitions for the ``ModuleDependency`` edge row.
 *
 * Mirrors ``backend.schemas.module_dependency`` — the natural key of
 * the table is ``(module_id, depends_on_module_id)``.  Both FKs are
 * immutable so the ``Update`` schema is intentionally empty.
 */

/** Payload for creating a new module dependency edge. */
export interface ModuleDependencyCreate {
  /** The dependent module — requires the other to be done first. */
  module_id: string;
  /** The prerequisite module — must reach ``done`` first. */
  depends_on_module_id: string;
}

/**
 * Partial update for an existing module dependency edge.
 *
 * The natural key ``(module_id, depends_on_module_id)`` is immutable —
 * an edge is either created or deleted, never rewritten in place.
 */
export type ModuleDependencyUpdate = Record<string, never>;

/** Serialised representation of a module dependency row. */
export interface ModuleDependencyRead {
  id: string;
  module_id: string;
  depends_on_module_id: string;
  /** ISO-8601 timestamp. */
  created_at: string;
  /** ISO-8601 timestamp. */
  updated_at: string;
}
