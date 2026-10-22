// DEV-154 — the orientačná cena of a version: after Návrh, from the tasks of its plan and the finished versions.
import api from "../api";
import type { components } from "./pipeline.generated";

export type PriceEstimate = components["schemas"]["PriceEstimateRead"];

/** The version's estimate — null for a fix of our own error, a version without a plan, or nothing measured yet. */
export function getPriceEstimateApi(
  versionId: string,
): Promise<PriceEstimate | null> {
  return api.get<PriceEstimate | null>(`/versions/${versionId}/price-estimate`);
}
