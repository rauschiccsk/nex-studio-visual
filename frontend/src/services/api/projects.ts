import api from "../api";
import type { PaginatedResponse, ProjectCreate, ProjectRead, ProjectUpdate } from "../../types";

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

/**
 * Patch a project's mutable fields (ICCINT-7).
 *
 * The backend has always accepted this; the cockpit never called it, so a value typed
 * at creation could not be corrected afterwards without going into the database — which
 * is what had to be done on 21.08.2026 when a project was recorded on another system's
 * reserved port block.
 *
 * Ports are validated exactly as on create (range, reserved blocks, other projects, what
 * Docker holds) and a change moves the block in the KB port registry. If that registry
 * write fails, the response carries ``setup_warnings`` — show them; the project is saved
 * either way, but an unrecorded block is how the next project gets handed this one.
 */
export function updateProjectApi(projectId: string, data: ProjectUpdate): Promise<ProjectRead> {
  return api.patch<ProjectRead>(`/projects/${projectId}`, data);
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

/** One uncommitted entry in the project's working tree (git status --porcelain). */
export interface GitStatusFile {
  code: string;
  path: string;
}

/** Working-tree preflight (v4.0.25): is the project clean enough to found a version? */
export interface GitStatus {
  clean: boolean;
  dirty_count: number;
  files: GitStatusFile[];
  truncated: boolean;
}

/** Read the project's working-tree cleanliness — the dirty-tree guard gate. */
export function getGitStatusApi(projectId: string): Promise<GitStatus> {
  return api.get<GitStatus>(`/projects/${projectId}/git-status`);
}

/** "Uložiť ich" — commit ALL pending changes so the tree is clean (preserves work). */
export function commitGitApi(
  projectId: string,
  message?: string,
): Promise<{ ok: boolean; note?: string; error?: string }> {
  return api.post(`/projects/${projectId}/git-commit`, { message: message ?? null });
}

/** "Zahodiť" — discard ALL pending changes (destructive; FE gates behind a confirm). */
export function discardGitApi(projectId: string): Promise<{ ok: boolean }> {
  return api.post(`/projects/${projectId}/git-discard`, {});
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
): Promise<{ suggested_port: number; warnings?: string[] }> {
  return api.get<{ suggested_port: number; warnings?: string[] }>("/projects/ports/suggest", {
    params: { type },
  });
}

export interface PortBlockSuggestion {
  base: number;
  block_size: number;
  /**
   * Slovak sentences the backend wrote FOR THE MANAGER — e.g. that no reserved port ranges are
   * configured, so the suggestion could not be checked against them. The route has always sent them;
   * this type omitted the field, so they were dropped at the seam and the form offered a port with a
   * caveat nobody ever saw.
   */
  warnings?: string[];
}

/**
 * Ask the backend for the first free 10-port block in the ICC port
 * registry (DECISIONS.md D-020). The new-project form consumes this
 * to auto-fill the three port inputs from a single contiguous block.
 */
export function suggestPortBlockApi(): Promise<PortBlockSuggestion> {
  return api.get<PortBlockSuggestion>("/projects/ports/suggest-block");
}
