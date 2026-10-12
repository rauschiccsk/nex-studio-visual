/**
 * DEV-84 — the CI runs of the selected project's GitHub repository. Maps to ``backend.api.routes.ci_runs``; the
 * server asks GitHub at most once per `refresh_seconds` for everybody, and keeps the token.
 */

import api from "@/services/api";
import type { components } from "@/services/api/pipeline.generated";

export type CiRuns = components["schemas"]["CiRunsRead"];
export type CiRun = components["schemas"]["CiRunRead"];

export function getCiRuns(slug: string): Promise<CiRuns> {
  return api.get<CiRuns>(`/projects/${encodeURIComponent(slug)}/ci-runs`);
}
