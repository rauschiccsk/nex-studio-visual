/**
 * API client for the delivered-token statement of a version (DEV-50) — the basis of invoicing development.
 *
 * Maps to ``backend.api.routes.delivery_statements``:
 *
 *   - ``GET  /versions/{id}/delivery-statement``   → getDeliveryStatement (preview + issued, newest first)
 *   - ``POST /versions/{id}/delivery-statement``   → issueDeliveryStatement (409 + why when it cannot be issued)
 *   - ``PUT  /versions/{id}/work-kind``            → setWorkKind
 *   - ``GET  /delivery-statements/{id}/csv``       → downloadStatementCsv
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
  tokenizer: string;
  code: KindTotal | null;
  tests: KindTotal | null;
  docs: KindTotal | null;
  files: DeliveredFile[];
  rate_code: string;
  rate_docs: string;
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
  amount_eur: string;
}

export interface DeliveryStatementView {
  preview: StatementPreview;
  issued: IssuedStatement[];
}

export function getDeliveryStatement(versionId: string): Promise<DeliveryStatementView> {
  return api.get<DeliveryStatementView>(`/versions/${versionId}/delivery-statement`);
}

export function issueDeliveryStatement(versionId: string): Promise<IssuedStatement> {
  return api.post<IssuedStatement>(`/versions/${versionId}/delivery-statement`, {});
}

export function setWorkKind(versionId: string, workKind: WorkKind): Promise<{ work_kind: WorkKind | null }> {
  return api.put<{ work_kind: WorkKind | null }>(`/versions/${versionId}/work-kind`, { work_kind: workKind });
}

export function downloadStatementCsv(statementId: string, fallbackName: string): Promise<void> {
  return downloadFile(`/delivery-statements/${statementId}/csv`, fallbackName);
}
