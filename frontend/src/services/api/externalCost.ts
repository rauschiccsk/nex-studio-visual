/**
 * API client for manually entered external cost (CR-V2-063, Part 4).
 *
 * Token spend the cockpit cannot meter — work done outside a build (Dedo in the terminal, a developer
 * working directly). Priced with the SAME per-model prices and the SAME human coefficient as measured
 * work, but always reported as a separate row and a separate `…_external` total.
 *
 * Maps to ``backend.api.routes.external_cost``:
 *
 *   - ``GET    /projects/{slug}/external-costs``       → listExternalCosts (newest occurred_on first)
 *   - ``POST   /projects/{slug}/external-costs``       → createExternalCost
 *   - ``PATCH  /projects/{slug}/external-costs/{id}``  → updateExternalCost
 *   - ``DELETE /projects/{slug}/external-costs/{id}``  → deleteExternalCost
 */

import api from "../api";

export interface ExternalCostRead {
  id: string;
  project_id: string;
  /** null = a project-level entry (counts in the project total, in no version). */
  version_id: string | null;
  occurred_on: string; // ISO date (YYYY-MM-DD)
  description: string;
  model: string; // full model id (e.g. "claude-opus-5")
  input_tokens: number;
  output_tokens: number;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface ExternalCostCreate {
  version_id?: string | null;
  occurred_on: string;
  description: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
}

export interface ExternalCostUpdate {
  version_id?: string | null;
  occurred_on?: string;
  description?: string;
  model?: string;
  input_tokens?: number;
  output_tokens?: number;
}

/** List the project's entries, newest `occurred_on` first. */
export function listExternalCosts(slug: string): Promise<ExternalCostRead[]> {
  return api.get<ExternalCostRead[]>(`/projects/${slug}/external-costs`);
}

export function createExternalCost(slug: string, data: ExternalCostCreate): Promise<ExternalCostRead> {
  return api.post<ExternalCostRead>(`/projects/${slug}/external-costs`, data);
}

export function updateExternalCost(
  slug: string,
  id: string,
  data: ExternalCostUpdate,
): Promise<ExternalCostRead> {
  return api.patch<ExternalCostRead>(`/projects/${slug}/external-costs/${id}`, data);
}

export function deleteExternalCost(slug: string, id: string): Promise<void> {
  return api.delete<void>(`/projects/${slug}/external-costs/${id}`);
}
