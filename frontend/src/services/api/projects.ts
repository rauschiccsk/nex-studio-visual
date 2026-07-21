import api from "../api";
import type { PaginatedResponse, ProjectCreate, ProjectRead } from "../../types";

export interface ListProjectsParams {
  skip?: number;
  limit?: number;
  status?: string;
  type?: string;
  [key: string]: string | number | boolean | null | undefined;
}

export function listProjectsApi(
  params: ListProjectsParams = {},
): Promise<PaginatedResponse<ProjectRead>> {
  return api.get<PaginatedResponse<ProjectRead>>("/projects", { params });
}

export function createProjectApi(data: ProjectCreate): Promise<ProjectRead> {
  return api.post<ProjectRead>("/projects", data);
}

export function getProjectApi(projectId: string): Promise<ProjectRead> {
  return api.get<ProjectRead>(`/projects/${projectId}`);
}

/** One changelog section shown as "Čo prinesie" in the nex-shared upgrade prompt. */
export interface NexsharedChangelogEntry {
  version: string;
  body: string;
}

/** nex-shared upgrade status for the auto-notify prompt (#3). */
export interface NexsharedStatus {
  current: string | null;
  latest: string | null;
  behind: number;
  up_to_date: boolean;
  changelog: NexsharedChangelogEntry[];
}

/** The app's pinned nex-shared vs the latest published tag + the changelog delta. */
export function getNexsharedStatusApi(projectId: string): Promise<NexsharedStatus> {
  return api.get<NexsharedStatus>(`/projects/${projectId}/nexshared-status`);
}

/** Opt-in bump: rewrite the app's nex-shared pin to `targetVersion` + commit it. */
export function upgradeNexsharedApi(
  projectId: string,
  targetVersion: string,
): Promise<{ upgraded: boolean; target_version: string; committed: boolean }> {
  return api.post(`/projects/${projectId}/nexshared-upgrade`, {
    target_version: targetVersion,
  });
}

/**
 * Hard-delete a project (CR-V2-027). Admin-only (role `ri`) and rejected with 409 once the project
 * has had a PROD deploy — both enforced by the backend. When `deleteGithub` is true the backing
 * GitHub repository is removed too; otherwise it is left in place.
 */
export function deleteProjectApi(projectId: string, deleteGithub: boolean): Promise<void> {
  return api.delete<void>(`/projects/${projectId}?delete_github=${deleteGithub}`);
}

export function suggestPortApi(
  type: "backend" | "frontend" | "db",
): Promise<{ suggested_port: number }> {
  return api.get<{ suggested_port: number }>("/projects/ports/suggest", { params: { type } });
}

export interface PortBlockSuggestion {
  base: number;
  block_size: number;
}

/**
 * Ask the backend for the first free 10-port block in the ICC port
 * registry (DECISIONS.md D-020). The new-project form consumes this
 * to auto-fill the three port inputs from a single contiguous block.
 */
export function suggestPortBlockApi(): Promise<PortBlockSuggestion> {
  return api.get<PortBlockSuggestion>("/projects/ports/suggest-block");
}
