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

import type { components } from "@/services/api/pipeline.generated";

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
  /** True when the customer's NEWEST UAT deploy attempt failed — the cell shows the last-good version but the
   *  upgrade didn't land; surfaced so a failed deploy never reads as a green cell (audit #5). */
  uat_last_attempt_failed: boolean;
  /** True when the customer's NEWEST PROD deploy attempt failed — same honest flag as UAT (audit #5). */
  prod_last_attempt_failed: boolean;
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

/**
 * WHY the Nasadiť button is closed (v4.0.54) — mirrors backend `DeployBlock`.
 *
 * `ok`               — a version is deployable, nothing to explain.
 * `drift`            — the version WAS finished, but the project's code moved past the point it was
 *                      checked at, so it auto-un-verified. Recoverable right here via "Over znova".
 * `reverify_running` — that re-verification is running now (the version leaves the finished state for the
 *                      whole run, so without this the screen would look identical to before the click).
 * `stale_signoff`    — later work outranked the sign-off; "Over znova" is NOT offered for this shape
 *                      anywhere (its handler rejects it) — route the manager to Riadiace centrum instead.
 * `none_finished`    — no version of this project was ever finished.
 */
/**
 * Aliased from the GENERATED contract, never restated by hand: the backend declares the vocabulary as a
 * `Literal`, FastAPI emits it as an OpenAPI enum and `npm run codegen` carries it here. Adding a cause on
 * the backend therefore makes `DeployBlockNotice`'s switch fail type-check until it is handled — which is
 * the point (an unhandled cause used to render a confidently wrong explanation).
 */
export type DeployBlockCause = components["schemas"]["DeployBlock"]["cause"];

export interface DeployBlock {
  cause: DeployBlockCause;
  /** The implicated version (the one a manager would deploy); null when no version is implicated. */
  version_number: string | null;
  /** The implicated version's id — what "Over znova" is posted against. */
  version_id: string | null;
  /**
   * True iff THIS user may post `overit_znovu` for this version right now. Backend-computed: the matrix is
   * readable more widely than the pipeline action is writable, and only `drift` has a handler that accepts
   * the action — never re-derive this in the frontend.
   */
  can_reverify: boolean;
}

/** The full version × customer matrix payload for a project's UAT/PROD tabs. */
export interface DeployMatrix {
  project_slug: string;
  /** Launch mode ('token' | 'password') — drives the UAT 'Spustiť' vs 'Otvoriť aplikáciu' (v4.0.30). */
  auth_mode: string;
  /** Deployable (verified / Hotovo) version_numbers — the Nasadiť options. */
  verified_versions: string[];
  /** Why Nasadiť is closed when `verified_versions` is empty (v4.0.54). */
  deployability: DeployBlock;
  /**
   * Whether this user may record a UAT acceptance at all (v4.0.55). Acceptance OPENS PROD, so it is
   * ri-only (D3) — deliberately narrower than the owner-or-ri UAT deploy. Backend-computed; used to
   * disable "Akceptovať" WITH a reason instead of letting it look live and then 403.
   */
  can_accept: boolean;
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
