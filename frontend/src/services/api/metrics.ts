import api from "../api";
import type { ProjectCosts } from "../../types/metrics";

/** The project's cost per phase / version / project + the human-work conversion (CR-V2-063). Read-only. */
export function getProjectMetricsApi(slug: string): Promise<ProjectCosts> {
  return api.get<ProjectCosts>(`/projects/${slug}/metrics`);
}
