/**
 * DEV-158 — the selected project's own CI runner on ANDROS, and the jobs of its workflows that run elsewhere. Maps to
 * ``backend.api.routes.ci_runner``; the server decides what the tab says and offers, and the start runs after its answer.
 */

import api from "@/services/api";
import type { components } from "@/services/api/pipeline.generated";

export type CiRunner = components["schemas"]["CiRunnerRead"];

export function getCiRunner(slug: string): Promise<CiRunner> {
  return api.get<CiRunner>(`/projects/${encodeURIComponent(slug)}/ci-runner`);
}

export function startCiRunner(slug: string): Promise<CiRunner> {
  return api.post<CiRunner>(`/projects/${encodeURIComponent(slug)}/ci-runner/start`);
}
