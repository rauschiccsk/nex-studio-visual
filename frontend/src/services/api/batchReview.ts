// DEV-483 — the fast path with Poradca: the summary of the cards Poradca assessed at once, and the switch per account.
import api from "../api";
import type { components } from "./pipeline.generated";

export type BatchReview = components["schemas"]["BatchReviewRead"];
export type BatchReviewRow = components["schemas"]["BatchReviewRow"];
export type BatchReviewAccount = components["schemas"]["BatchReviewAccount"];

/** The summary of the open cards — ``null`` when Poradca was not asked about them (the switch is off). */
export function getBatchReviewApi(versionId: string): Promise<BatchReview | null> {
  return api.get<BatchReview | null>(`/pipeline/${versionId}/batch-review`);
}

export function listBatchReviewAccountsApi(): Promise<BatchReviewAccount[]> {
  return api.get<BatchReviewAccount[]>("/users/batch-review");
}

export function setBatchReviewApi(userId: string, on: boolean): Promise<BatchReviewAccount> {
  return api.put<BatchReviewAccount>(`/users/${userId}/batch-review`, { on });
}
