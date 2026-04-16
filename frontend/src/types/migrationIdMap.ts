/**
 * TypeScript type definitions for the ``MigrationIdMap`` domain
 * object.
 *
 * Mirrors ``backend.schemas.migration_id_map`` — maps a legacy Btrieve
 * source key to a new PostgreSQL UUID per (project, category).
 * ``target_id`` holds a stringified UUID (stored as ``VARCHAR(36)``
 * on the backend — not an FK to any specific table).
 */

/** Payload for creating a new migration ID-map entry. */
export interface MigrationIdMapCreate {
  project_id: string;
  /** Migration category, e.g. ``PAB``, ``GSC``. Max 10 chars. */
  category: string;
  /** Legacy Btrieve source key. Max 255 chars. */
  source_key: string;
  /** New PostgreSQL UUID mapped from the source key. 36 chars. */
  target_id: string;
  /** Optional migration batch that produced this mapping. */
  batch_id?: string | null;
}

/**
 * Partial update for an existing migration ID-map entry.
 *
 * The natural key ``(project_id, category, source_key)`` is
 * immutable — only ``target_id`` and ``batch_id`` may be amended.
 */
export interface MigrationIdMapUpdate {
  target_id?: string;
  batch_id?: string | null;
}

/** Serialised representation of a migration ID-map row. */
export interface MigrationIdMapRead {
  id: string;
  project_id: string;
  category: string;
  source_key: string;
  target_id: string;
  batch_id: string | null;
  /** ISO-8601 timestamp. */
  created_at: string;
  /** ISO-8601 timestamp. */
  updated_at: string;
}
