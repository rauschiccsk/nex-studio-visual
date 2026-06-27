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
  created_at: string;
  updated_at: string;
}

export interface CustomerCreate {
  name: string;
  slug: string;
  subdomain?: string | null;
  integrations?: Record<string, unknown> | null;
  notes?: string | null;
  /** Write-only — handed to the credentials store; never returned. */
  secret?: string | null;
}

export interface CustomerUpdate {
  name?: string;
  slug?: string;
  subdomain?: string | null;
  integrations?: Record<string, unknown> | null;
  notes?: string | null;
  /** Write-only — rotates the stored secret; never returned. */
  secret?: string | null;
}
