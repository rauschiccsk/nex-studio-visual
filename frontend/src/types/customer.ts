/**
 * TypeScript type definitions for the Customers domain (v2.0.0, CR-V2-025).
 *
 * The per-project customer registry (design §3.2 "Zákazníci"). Each customer
 * runs the app on its own UAT + PROD instance / DB / data.
 *
 * Secret handling (CLAUDE.md §4/§5, OQ-5): the `secret` field is WRITE-ONLY —
 * it appears only on the create / update payloads and is routed to the backend
 * credentials store. The read shape (`CustomerRead`) carries NO secret, only a
 * `has_secret` boolean, so the value is never echoed back to the browser.
 */

export interface CustomerRead {
  id: string;
  project_id: string;
  name: string;
  slug: string;
  subdomain: string | null;
  integrations: Record<string, unknown> | null;
  notes: string | null;
  has_secret: boolean;
  /** DEV-72: values of the settings the app marks `@customer`. */
  settings: Record<string, string>;
  /** DEV-72: which `@customer-secret` settings have a stored value — names only, never the value. */
  secret_settings_set: string[];
  created_at: string;
  updated_at: string;
}

/** DEV-72: one setting an app marks in its `.env.example`. */
export interface CustomerSetting {
  key: string;
  /** `public_url_admin` — a Web project's administration address (DEV-164). */
  kind: "customer" | "customer_secret" | "public_url" | "public_url_admin";
  required: boolean;
  /** What the manager is told about it — the app's own words. */
  label: string;
}

/** DEV-72: what a project asks of each customer. */
export interface CustomerSettingsContract {
  settings: CustomerSetting[];
  /** Markers that mean nothing (misspelt, misplaced) — the deploy refuses until they are fixed. */
  problems: string[];
  /** The app takes its `admin` password from the customer's „Tajný kľúč“ (ADMIN_INITIAL_PASSWORD). */
  legacy_admin: boolean;
}

export interface CustomerCreate {
  name: string;
  slug: string;
  subdomain?: string | null;
  integrations?: Record<string, unknown> | null;
  notes?: string | null;
  /** Write-only — handed to the credentials store; never returned. */
  secret?: string | null;
  settings?: Record<string, string> | null;
  /** Write-only — kept in the credentials store; never returned. */
  secret_settings?: Record<string, string> | null;
}

export interface CustomerUpdate {
  name?: string;
  slug?: string;
  subdomain?: string | null;
  integrations?: Record<string, unknown> | null;
  notes?: string | null;
  /** Write-only — rotates the stored secret; never returned. */
  secret?: string | null;
  /** Replaces the stored settings; an empty value removes one. */
  settings?: Record<string, string> | null;
  /** Write-only — only the given non-empty values change; the others stay. */
  secret_settings?: Record<string, string> | null;
}
