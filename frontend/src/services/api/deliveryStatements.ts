/**
 * API client for the delivered-token statement of a version (DEV-50) — the basis of invoicing development.
 *
 * Maps to ``backend.api.routes.delivery_statements``:
 *
 *   - ``GET  /versions/{id}/delivery-statement``   → getDeliveryStatement (preview + issued, newest first)
 *   - ``POST /versions/{id}/delivery-statement``   → issueDeliveryStatement (409 + why when it cannot be issued;
 *                                                     a new one replaces the valid one only when ``replace`` names it)
 *   - ``PUT  /versions/{id}/work-kind``            → setWorkKind
 *   - ``GET  /delivery-statements/{id}/csv``       → downloadStatementCsv
 *   - ``GET  /projects/{slug}/billing``           → getProjectBilling (DEV-73, every version for invoicing)
 *   - ``PUT  /versions/{id}/billing-rates``       → setBillingRates (DEV-74, the version's own rates)
 */

import api, { downloadFile } from "../api";
import type { WorkKind } from "@/lib/workKind";

export interface DeliveredFile {
  path: string;
  /** ``kod`` · ``skusky`` · ``dokumentacia``; null for a file that is left out. */
  kind: string | null;
  /** Why the file is left out — a sentence; null for a counted file. */
  excluded: string | null;
  lines: number;
  tokens: number;
}

export interface KindTotal {
  tokens: number;
  lines: number;
}

export interface StatementCalibration {
  eur_code: number | null;
  eur_docs: number | null;
  per_1k_code: number | null;
  per_1k_docs: number | null;
  complete: boolean;
}

export interface StatementPreview {
  version_number: string;
  blocked: string | null;
  work_kind: WorkKind | null;
  base_sha: string | null;
  delivered_sha: string | null;
  delivered_source: string | null;
  /** The same in words („stav, na ktorom prešla Verifikácia“) — one wording for the screen and the CSV. */
  delivered_source_label: string | null;
  tokenizer: string;
  code: KindTotal | null;
  tests: KindTotal | null;
  docs: KindTotal | null;
  files: DeliveredFile[];
  rate_code: string;
  rate_docs: string;
  /** DEV-74 — the rates in Nastavenia, and whether this version counts with its own instead. */
  settings_rate_code: string;
  settings_rate_docs: string;
  rate_code_own: boolean;
  rate_docs_own: boolean;
  /** € per line, each rounded to cents; ``amount_eur`` is their sum. Null while the work kind is undecided. */
  amount_code_eur: string | null;
  amount_docs_eur: string | null;
  amount_eur: string | null;
  cannot_issue: string[];
  calibration: StatementCalibration | null;
}

export interface IssuedStatement {
  id: string;
  created_at: string;
  work_kind: WorkKind;
  base_sha: string;
  delivered_sha: string;
  delivered_source: string;
  tokenizer: string;
  tokens_code: number;
  tokens_tests: number;
  tokens_docs: number;
  rate_code: string;
  rate_docs: string;
  /** Frozen with the statement; null only on one issued before the lines were kept that would not add up. */
  amount_code_eur: string | null;
  amount_docs_eur: string | null;
  amount_eur: string;
  /** DEV-54 — the statement this one replaced, and when this one was replaced in turn (null = the valid one). */
  replaces_id: string | null;
  replaced_at: string | null;
}

export interface DeliveryStatementView {
  preview: StatementPreview;
  issued: IssuedStatement[];
  /** DEV-74 — every change of the version's own rates, the newest first. */
  rate_changes: RateChange[];
}

/** DEV-74 — one change of a version's own rates; null = the rate in Nastavenia. */
export interface RateChange {
  created_at: string;
  changed_by: string | null;
  previous_rate_code: string | null;
  previous_rate_docs: string | null;
  rate_code: string | null;
  rate_docs: string | null;
}

/** DEV-73 — one version on the billing tab: where it stands for invoicing. */
export interface BillingRow {
  version_id: string;
  version_number: string;
  flow_type: string | null;
  state: "issued" | "ready" | "needs_kind" | "cannot" | "blocked" | "not_done";
  /** Why it cannot be issued — a sentence; null when it can, or is issued. */
  reason: string | null;
  work_kind: WorkKind | null;
  base_sha: string | null;
  delivered_sha: string | null;
  delivered_source_label: string | null;
  tokens_code: number | null;
  tokens_docs: number | null;
  /** What a statement issued now would say. */
  preview_amount_eur: string | null;
  /** The valid statement, if any. */
  issued: IssuedStatement | null;
  /** Issued, but counted today the amount would differ (other rates or delivery). */
  differs: boolean;
  /** DEV-74 — the rates the version counts with now, and whether it has one of its own. */
  rate_code: string | null;
  rate_docs: string | null;
  own_rates: boolean;
}

export interface BillingOverview {
  rows: BillingRow[];
  issued_total_eur: string;
  /** Not issued yet and issuable now — preview amounts. */
  ready_total_eur: string;
  /** Fast fixes whose work kind waits for the manager. */
  needs_kind: number;
}

/** DEV-73 — every version of the project for invoicing, the newest first. */
export function getProjectBilling(slug: string): Promise<BillingOverview> {
  return api.get<BillingOverview>(`/projects/${slug}/billing`);
}

/** DEV-74 — the version's own token rates; null = back to the rate in Nastavenia. Returns every change. */
export function setBillingRates(
  versionId: string,
  rates: { rate_code: string | null; rate_docs: string | null },
): Promise<RateChange[]> {
  return api.put<RateChange[]>(`/versions/${versionId}/billing-rates`, rates);
}

export function getDeliveryStatement(versionId: string): Promise<DeliveryStatementView> {
  return api.get<DeliveryStatementView>(`/versions/${versionId}/delivery-statement`);
}

export function issueDeliveryStatement(versionId: string, replace?: string): Promise<IssuedStatement> {
  return api.post<IssuedStatement>(`/versions/${versionId}/delivery-statement`, replace ? { replace } : {});
}

export function setWorkKind(versionId: string, workKind: WorkKind): Promise<{ work_kind: WorkKind | null }> {
  return api.put<{ work_kind: WorkKind | null }>(`/versions/${versionId}/work-kind`, { work_kind: workKind });
}

export function downloadStatementCsv(statementId: string, fallbackName: string): Promise<void> {
  return downloadFile(`/delivery-statements/${statementId}/csv`, fallbackName);
}
