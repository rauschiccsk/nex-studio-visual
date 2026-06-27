/**
 * TypeScript type definitions for the ``Project`` domain object.
 *
 * Mirrors ``backend.schemas.project`` — field names, max lengths and
 * nullability match the Pydantic schemas exactly.
 */

/**
 * Mirrors ``type IN ('standard', 'web')`` on the ``projects`` table
 * (CR-V2-005) — the project archetype, a preset surface composition
 * that replaces the retired v1 single/multi-module ``category``.
 */
export type ProjectType = "standard" | "web";

/**
 * Mirrors ``auth_mode IN ('password', 'token')`` on the ``projects``
 * table (CR-V2-005) — the login flavour wired onto every surface
 * (password-login like Studio / token-launch like Inbox). MANDATORY at
 * project creation; it shapes the BE login + FE login flow.
 */
export type ProjectAuthMode = "password" | "token";

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
  /** Project archetype (surface composition): ``standard`` | ``web``. */
  type: ProjectType;
  /** Login flavour wired onto every surface: ``password`` | ``token``. Required. */
  auth_mode: ProjectAuthMode;
  /** Project description. */
  description: string;
  /** Lifecycle status; server default ``active``. */
  status?: ProjectStatus;
  backend_port?: number | null;
  frontend_port?: number | null;
  db_port?: number | null;
  /** Git repository URL. Max 255 chars. */
  repo_url?: string | null;
  source_path?: string | null;
  kb_path?: string | null;
  /** Whether Guardian review is enabled for this project. */
  guardian_enabled?: boolean;
  /** User who created the project. */
  created_by: string;
  /** Notification owner (CR-NS-012). Their Telegram chat_id receives agent
   *  notifications. Optional — defaults to the creator server-side. */
  owner_id?: string | null;
  // F-004 setup flags
  /** F-004 K-005: copy github-actions-workflow.yml + commit + push. Default false. */
  enable_cicd?: boolean;
  /** F-004 K-004: full smoke (build + up + health) instead of minimal (build only). Default false. */
  full_smoke?: boolean;
  /** F-004 O-3: GitHub branch protection (require PR, no force push). Default false. */
  enable_branch_protection?: boolean;
}

/**
 * Partial update for an existing project.
 *
 * ``slug`` is auto-generated from ``name``; ``type`` and ``auth_mode``
 * are archetype/login presets fixed at creation — all three are excluded.
 * ``created_by`` is an audit column and must not be rewritten.
 */
export interface ProjectUpdate {
  name?: string;
  description?: string;
  status?: ProjectStatus;
  backend_port?: number | null;
  frontend_port?: number | null;
  db_port?: number | null;
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
  type: ProjectType;
  auth_mode: ProjectAuthMode;
  description: string;
  status: ProjectStatus;
  backend_port: number | null;
  frontend_port: number | null;
  db_port: number | null;
  repo_url: string | null;
  source_path: string | null;
  kb_path: string | null;
  guardian_enabled: boolean;
  created_by: string;
  /** Notification owner (CR-NS-012), nullable. */
  owner_id: string | null;
  /** ISO-8601 timestamp. */
  created_at: string;
  /** ISO-8601 timestamp. */
  updated_at: string;
}
