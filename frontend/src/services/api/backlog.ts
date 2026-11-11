import api from "../api";
import type { PaginatedResponse } from "../../types";
import type {
  BacklogItemCreate,
  BacklogItemRead,
  BacklogItemUpdate,
  BacklogTicketCreate,
  BacklogTicketDraft,
  BacklogVersionRead,
} from "../../types/backlog";

export interface ListBacklogParams {
  project_id?: string;
  status?: string;
  skip?: number;
  limit?: number;
  [key: string]: string | number | boolean | null | undefined;
}

export function listBacklogApi(
  params: ListBacklogParams = {},
): Promise<PaginatedResponse<BacklogItemRead>> {
  return api.get<PaginatedResponse<BacklogItemRead>>("/backlog", { params });
}

/** DEV-361 — the project's versions and why a requirement can no longer be included in some of them. */
export function listBacklogVersionsApi(projectId: string): Promise<BacklogVersionRead[]> {
  return api.get<BacklogVersionRead[]>("/backlog/versions", { params: { project_id: projectId } });
}

export function createBacklogApi(data: BacklogItemCreate): Promise<BacklogItemRead> {
  return api.post<BacklogItemRead>("/backlog", data);
}

/** Edit (title/desc/priority) | reject (status) | assign-to-version (version_id → included). */
export function updateBacklogApi(
  id: string,
  data: BacklogItemUpdate,
): Promise<BacklogItemRead> {
  return api.patch<BacklogItemRead>(`/backlog/${id}`, data);
}

export function deleteBacklogApi(id: string): Promise<void> {
  return api.delete<void>(`/backlog/${id}`);
}

/** DEV-121: what „Založiť tiket" offers — or the requirement's ticket, when it exists. */
export function getBacklogTicketDraftApi(id: string): Promise<BacklogTicketDraft> {
  return api.get<BacklogTicketDraft>(`/backlog/${id}/plane-ticket`);
}

/** DEV-121: found the requirement's ticket in Plane — once; a second call returns the same ticket. */
export function createBacklogTicketApi(
  id: string,
  data: BacklogTicketCreate,
): Promise<BacklogItemRead> {
  return api.post<BacklogItemRead>(`/backlog/${id}/plane-ticket`, data);
}
