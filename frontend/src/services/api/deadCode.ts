// DEV-156 — the code nobody uses that the project's earlier versions left behind, and the fast fix that cleans it up.
import api from "../api";
import type { components } from "./pipeline.generated";

export type DeadCode = components["schemas"]["DeadCodeRead"];

/** What the check finds in the project's current commit — every finding is old: Verifikácia stops the new ones. */
export function getDeadCodeApi(slug: string): Promise<DeadCode> {
  return api.get<DeadCode>(`/projects/${slug}/dead-code`);
}
