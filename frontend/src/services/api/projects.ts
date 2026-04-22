import api from "../api";
import type { PaginatedResponse, ProjectCreate, ProjectRead } from "../../types";

export interface ListProjectsParams {
  skip?: number;
  limit?: number;
  status?: string;
  category?: string;
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

export function suggestPortApi(type: "backend" | "frontend" | "db"): Promise<{ suggested_port: number }> {
  return api.get<{ suggested_port: number }>("/projects/ports/suggest", { params: { type } });
}
