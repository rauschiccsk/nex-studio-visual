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
 *   - ``PUT  /versions/{id}/billing-discounts``   → setBillingDiscounts (DEV-75, the version's own discounts)
 *   - ``PUT  /versions/{id}/task-categories``     → setTaskCategories (DEV-75, what tasks are billed as)
 */

import api, { downloadFile } from "../api";
import type { BillingCategory } from "@/lib/billingCategory";
import type { WorkKind } from "@/lib/workKind";

interface DeliveredFile {
  path: string;
  /** ``kod`` · ``skusky`` · ``dokumentacia``; null for a file that is left out. */
  kind: string | null;
  /** Why the file is left out — a sentence; null for a counted file. */
  excluded: string | null;
  lines: number;
  tokens: number;
}

interface KindTotal {
  tokens: number;
  lines: number;
}

interface StatementCalibration {
  eur_code: number | null;
  eur_docs: number | null;
  per_1k_code: number | null;
  per_1k_docs: number | null;
  complete: boolean;
}

/** DEV-75 — a task whose work is on a line of the statement. */
interface StatementLineTask {
  /** epic.feat.task — the number in the plan and in the agent's commits. */
  label: string;
  title: string;
  lines: number;
  tokens: number;
}

/** DEV-75 — one line of the statement: a category × kind, its full value, the discount and the amount. */
export interface StatementLine {
  category: BillingCategory;
  kind: "code" | "docs";
  /** False = work without a task (design documents, the visual phase, fixes after verification). */
  tasked: boolean;
  label: string;
  lines: number;
  tokens: number;
  rate: string;
  full_eur: string;
  discount_pct: string;
  /** „záručná oprava“, „pozornosť ICC“, „zľava“ — null without a discount. */
  discount_label: string | null;
  discount_eur: string;
  amount_eur: string;
  tasks: StatementLineTask[];
}

/** DEV-75 — a task of the version's plan with its category and what it delivered. */
export interface StatementTask {
  id: string;
  label: string;
  title: string;
  /** The epic it is under — a whole epic's tasks are set at once. */
  epic_title: string;
  /** Its own category; null = the version's. */
  own_category: BillingCategory | null;
  /** As it counts; null while the version's work kind is undecided. */
  category: BillingCategory | null;
  lines: number;
  tokens: number;
}

export type CategoryValues<T> = Record<BillingCategory, T>;

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
  /** DEV-75 — the category of a task nobody decided; null while the work kind is undecided. */
  version_category: BillingCategory | null;
  /** In %, as the version counts — its own where it has one, else Nastavenia. */
  discounts: CategoryValues<string>;
  settings_discounts: CategoryValues<string>;
  discounts_own: CategoryValues<boolean>;
  lines: StatementLine[];
  tasks: StatementTask[];
  /** What the delivery was worth before the discounts. */
  full_eur: string | null;
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
  /** DEV-75 — the lines it was issued with; null on one issued before the categories. */
  lines: StatementLine[] | null;
}

export interface DeliveryStatementView {
  preview: StatementPreview;
  issued: IssuedStatement[];
  /** DEV-74 — every change of the version's own rates, the newest first. */
  rate_changes: RateChange[];
  /** DEV-75 — every change of the version's own discounts, the newest first. */
  discount_changes: DiscountChange[];
}

/** DEV-75 — one change of a version's own discounts in %; null = the discount in Nastavenia. */
export interface DiscountChange {
  created_at: string;
  changed_by: string | null;
  previous: CategoryValues<string | null>;
  current: CategoryValues<string | null>;
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

/** DEV-154 — the orientačná cena of a version: the tasks of its plan × what a task of the finished versions
 * delivered × the version's rates. An estimate, not an invoice — the statement after the delivery says what is billed. */
export interface PriceEstimate {
  tasks: number;
  /** The first version of a new project — measured apart, it carries the skeleton of the application. */
  first_version: boolean;
  code_tokens: number;
  docs_tokens: number;
  rate_code: string;
  rate_docs: string;
  code_eur: string;
  docs_eur: string;
  amount_eur: string;
  spread_pct: number | null;
  /** How many finished versions it rests on, and whether that is enough to be firm. */
  samples: number;
  enough: boolean;
  created_at: string;
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
  /** DEV-154, DEV-225 — the orientačná cena from the version's plan, beside what the statement says; null for a fix
   * of our own error, a version without a plan, or nothing measured yet. */
  estimate?: PriceEstimate | null;
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

/** DEV-75 — the version's own discount per category in %; null = back to Nastavenia. Returns every change. */
export function setBillingDiscounts(
  versionId: string,
  discounts: CategoryValues<string | null>,
): Promise<DiscountChange[]> {
  return api.put<DiscountChange[]>(`/versions/${versionId}/billing-discounts`, discounts);
}

/** DEV-75 — what these tasks are billed as (all of an epic or feat at once); null = the version's category. */
export function setTaskCategories(
  versionId: string,
  taskIds: string[],
  category: BillingCategory | null,
): Promise<{ changed: number }> {
  return api.put<{ changed: number }>(`/versions/${versionId}/task-categories`, { task_ids: taskIds, category });
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
