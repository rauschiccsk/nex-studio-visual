/**
 * TypeScript type definitions for the ``Project`` domain object.
 *
 * Mirrors ``backend.schemas.project`` — field names, max lengths and
 * nullability match the Pydantic schemas exactly.
 */

/**
 * Mirrors ``category IN ('singlemodule', 'multimodule')`` on the
 * ``projects`` table.
 */
export type ProjectCategory = "singlemodule" | "multimodule";

/**
 * Mirrors ``status IN ('active', 'archived', 'paused')`` on the
 * ``projects`` table.
 */
export type ProjectStatus = "active" | "archived" | "paused";

/** Payload for creating a new project (``POST /api/v1/projects``). */
export interface ProjectCreate {
  /** Human-readable project name — unique across the system. */
  name: string;
  /** URL-safe identifier — unique across the system. */
  slug: string;
  /** ``singlemodule`` | ``multimodule``. */
  category: ProjectCategory;
  /** Project description. */
  description: string;
  /** Lifecycle status; server default ``active``. */
  status?: ProjectStatus;
  backend_port?: number | null;
  frontend_port?: number | null;
  db_port?: number | null;
  /** UI Design mockup preview port (Step 2B output). */
  ui_design_port?: number | null;
  /** Git repository URL. Max 255 chars. */
  repo_url?: string | null;
  source_path?: string | null;
  kb_path?: string | null;
  /** Whether Guardian review is enabled for this project. */
  guardian_enabled?: boolean;
  /** User who created the project. */
  created_by: string;
}

/**
 * Partial update for an existing project.
 *
 * ``slug`` is auto-generated from ``name``, and ``category`` cannot be
 * changed once the project is created — both are excluded.
 * ``created_by`` is an audit column and must not be rewritten.
 */
export interface ProjectUpdate {
  name?: string;
  description?: string;
  status?: ProjectStatus;
  backend_port?: number | null;
  frontend_port?: number | null;
  db_port?: number | null;
  ui_design_port?: number | null;
  repo_url?: string | null;
  source_path?: string | null;
  kb_path?: string | null;
  guardian_enabled?: boolean;
}

/** Serialised representation of a project row. */
export interface ProjectRead {
  id: string;
  name: string;
  slug: string;
  category: ProjectCategory;
  description: string;
  status: ProjectStatus;
  backend_port: number | null;
  frontend_port: number | null;
  db_port: number | null;
  ui_design_port: number | null;
  repo_url: string | null;
  source_path: string | null;
  kb_path: string | null;
  guardian_enabled: boolean;
  created_by: string;
  /** ISO-8601 timestamp. */
  created_at: string;
  /** ISO-8601 timestamp. */
  updated_at: string;
}
