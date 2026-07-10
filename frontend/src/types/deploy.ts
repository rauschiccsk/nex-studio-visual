/**
 * TypeScript type definitions for the per-customer Deploy subsystem (v2.0.0).
 *
 * Mirrors `backend/schemas/deploy.py` — the deploy/accept audit-log plus the
 * version × customer matrix that drives the UAT / PROD tabs (CR-V2-027, design
 * §3.3/§3.4/§3.5).
 *
 * Secret handling (CLAUDE.md §4/§5, OQ-5): NO type here carries secret material.
 * Per-customer secrets live only in the backend credentials store; the deploy
 * surface never echoes a secret into a request or a response.
 */

/** UAT or PROD — the two per-customer deploy environments (design §3.3). */
export type DeployEnvironment = "uat" | "prod";

/** A deploy or an acceptance event in the audit-log. */
export type DeployEventType = "deploy" | "accept";

/** Per-deploy outcome (an `accept` event is always `ok`). */
export type DeployStatus = "ok" | "failed";

/** Serialised deploy/accept audit-log row (who / when / version / customer). */
export interface DeployEventRead {
  id: string;
  seq: number;
  customer_id: string;
  project_id: string;
  version_number: string;
  environment: DeployEnvironment;
  event_type: DeployEventType;
  status: DeployStatus;
  actor_id: string | null;
  /** Non-secret human-readable summary only. */
  detail: string | null;
  created_at: string;
  updated_at: string;
}

/** Outcome of a Nasadiť (deploy) action. */
export interface DeployResult {
  ok: boolean;
  event: DeployEventRead;
  /** The deployed instance URL (null when no FE route / on failure). */
  url: string | null;
  /** Set when a first-PROD deploy bumped the project version (e.g. "v1.0.0"). */
  bumped_to: string | null;
  warnings: string[];
}

/** One customer's row in the version × customer matrix (design §3.3). */
export interface DeployMatrixRow {
  customer_id: string;
  customer_name: string;
  customer_slug: string;
  subdomain: string | null;
  /** Currently deployed UAT version (null = never deployed there). */
  uat_version: string | null;
  /** Currently deployed PROD version (null = never deployed there). */
  prod_version: string | null;
  /**
   * Versions accepted-for-PROD for this customer — the ONLY versions whose PROD
   * Nasadiť is open (the never-bypassed acceptance gate, §3.5).
   */
  accepted_versions: string[];
  /** Link to the customer's live UAT instance (the UAT tab link, §3.5); null until a UAT deploy. */
  uat_url: string | null;
  /** Link to the customer's live PROD instance (the PROD tab link); null until a PROD deploy. */
  prod_url: string | null;
}

/** The full version × customer matrix payload for a project's UAT/PROD tabs. */
export interface DeployMatrix {
  project_slug: string;
  /** Deployable (verified / Hotovo) version_numbers — the Nasadiť options. */
  verified_versions: string[];
  rows: DeployMatrixRow[];
}

/** Payload for the Nasadiť action (deploy a verified version to a customer). */
export interface DeployRequest {
  version_number: string;
  environment: DeployEnvironment;
  /**
   * Opt-in: re-provision from scratch (rotate secrets, fresh data). Default
   * false — a redeploy PRESERVES data + secrets + extra_hosts (§3.7).
   */
  force_fresh?: boolean;
}

/** Payload for the Akceptovať action (record a Manažér's UAT acceptance). */
export interface AcceptRequest {
  version_number: string;
}
