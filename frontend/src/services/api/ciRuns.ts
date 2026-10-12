/**
 * DEV-84 — the CI runs of the selected project's GitHub repository. Maps to ``backend.api.routes.ci_runs``; the
 * server asks GitHub at most once per `refresh_seconds` for everybody, and keeps the token.
 */

import api from "@/services/api";
import type { components } from "@/services/api/pipeline.generated";

export type CiRuns = components["schemas"]["CiRunsRead"];
export type CiRun = components["schemas"]["CiRunRead"];
/** DEV-85 — is the project's main branch red, from what the cockpit's watch last saw (GitHub is not asked). */
export type CiMain = components["schemas"]["CiMainRead"];

export function getCiRuns(slug: string): Promise<CiRuns> {
  return api.get<CiRuns>(`/projects/${encodeURIComponent(slug)}/ci-runs`);
}

export function getCiMain(slug: string): Promise<CiMain> {
  return api.get<CiMain>(`/projects/${encodeURIComponent(slug)}/ci-main`);
}
