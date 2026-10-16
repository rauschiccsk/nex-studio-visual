/**
 * TypeScript type definitions for the ``Version`` domain object.
 *
 * Mirrors ``backend.schemas.version`` — field names, nullability and
 * aggregate counts match the Pydantic schemas exactly.
 *
 * Status values come from the backend schema (``VersionRead.status``, the ``ck_versions_status`` CHECK constraint).
 */

import type { WorkKind } from "@/lib/workKind";
import type { components } from "@/services/api/pipeline.generated";

/**
 * The status of a version as the backend sends it — taken from the generated schema, never copied. DEV-134: the
 * copy said ``planned | active | released`` while the backend also sends ``done`` (26 versions on 16.10.2026), so
 * the Zásobník offered finished versions and called each one „Neznámy stav“.
 */
export type VersionStatus = components["schemas"]["VersionRead"]["status"];

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
  /** DEV-50 — a fix of our own error (never billed), a change (billed); DEV-118 — or a gift. */
  work_kind?: WorkKind;
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
