/**
 * TypeScript type definitions for the project creation form.
 *
 * These types model the single-screen "New Project" form (Feat 24).
 * They are separate from the API-level ``ProjectCreate`` schema so that
 * the form layer can carry UI-specific validation error shapes while the
 * API layer keeps its own contract.
 */

import type { ProjectCategory } from "./project";

// ---------------------------------------------------------------------------
// Form data
// ---------------------------------------------------------------------------

/**
 * Shape of the "New Project" form state.
 *
 * Every field the user fills in on the single-screen creation form is
 * represented here.  Port fields are ``number | null`` because they are
 * optional — `null` means "not assigned yet".
 */
export interface ProjectCreationFormData {
  /** Human-readable project name. */
  name: string;
  /** URL-safe slug (auto-generated from name, editable). */
  slug: string;
  /** Single-module or multi-module project. */
  category: ProjectCategory;
  /** Free-text project description. */
  description: string;
  /** GitHub repository URL (e.g. ``https://github.com/org/repo``). */
  github_repo: string;
  /** Backend service port. */
  backend_port: number | null;
  /** Frontend dev-server / production port. */
  frontend_port: number | null;
  /** Database port. */
  db_port: number | null;
  /** UI Design mockup preview port (Step 2B output). */
  ui_design_port: number | null;
}

// ---------------------------------------------------------------------------
// Validation errors
// ---------------------------------------------------------------------------

/** Returned when a requested port is already in use by another project. */
export interface PortValidationError {
  /** The port number that failed validation. */
  port: number;
  /** Which port field this error belongs to. */
  field: "backend_port" | "frontend_port" | "db_port" | "ui_design_port";
  /** Human-readable error message. */
  message: string;
  /** Slug of the project that currently occupies this port (if known). */
  conflicting_project?: string;
}

/** Returned when the chosen slug is already taken or invalid. */
export interface SlugValidationError {
  /** The slug value that failed validation. */
  slug: string;
  /** Human-readable error message. */
  message: string;
}

/** Returned when the GitHub repo URL is unreachable or malformed. */
export interface GitHubRepoValidationError {
  /** The repo URL that failed validation. */
  repo_url: string;
  /** Human-readable error message. */
  message: string;
}
