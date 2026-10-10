/**
 * TypeScript type definitions for the ``Version`` domain object.
 *
 * Mirrors ``backend.schemas.version`` — field names, nullability and
 * aggregate counts match the Pydantic schemas exactly.
 *
 * Status values correspond to the ``ck_versions_status`` CHECK constraint
 * on the ``versions`` table (``planned | active | released``).
 */

/**
 * Mirrors ``status IN ('planned', 'active', 'released')`` on the
 * ``versions`` table.
 */
export type VersionStatus = "planned" | "active" | "released";

/** Serialised representation of a version row. */
export interface Version {
  id: string;
  project_id: string;
  version_number: string;
  name: string | null;
  status: VersionStatus;
  description: string | null;
  /** ISO date string (YYYY-MM-DD) or null. */
  target_date: string | null;
  /** ISO date string (YYYY-MM-DD) or null — set on release. */
  release_date: string | null;
  /** ISO-8601 datetime. */
  created_at: string;
  /** ISO-8601 datetime. */
  updated_at: string;
  /** Number of Epics assigned to this version. */
  epic_count: number;
  /** Number of Epics with status 'done'. */
  epics_done: number;
  /** Number of Bugs assigned to this version. */
  bug_count: number;
}

/** Payload for creating a new version (``POST /projects/{id}/versions``). */
export interface VersionCreate {
  version_number: string;
  name?: string;
  description?: string;
  target_date?: string;
  /** DEV-50 — a fix of our own error (never billed) or a change (billed). */
  work_kind?: "fix" | "change";
}

/** Partial update for an existing version (``PATCH /versions/{id}``). */
export interface VersionUpdate {
  version_number?: string;
  name?: string;
  status?: VersionStatus;
  description?: string;
  target_date?: string;
  release_date?: string;
}
