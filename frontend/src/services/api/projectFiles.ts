// DEV-44: files the Manažér hands the AI Agent — `private/` of the project, under the build they belong to.
import api from "../api";
import type { components } from "./pipeline.generated";

export type PrivateFile = components["schemas"]["PrivateFileRead"];
export type PrivateFiles = components["schemas"]["PrivateFilesRead"];
export type PrivateFileUploaded = components["schemas"]["PrivateFileUploaded"];

export function listPrivateFilesApi(versionId: string): Promise<PrivateFiles> {
  return api.get<PrivateFiles>(`/pipeline/${versionId}/files`);
}

export function uploadPrivateFileApi(versionId: string, file: File): Promise<PrivateFileUploaded> {
  const body = new FormData();
  body.append("file", file, file.name);
  return api.post<PrivateFileUploaded>(`/pipeline/${versionId}/files`, body);
}

export function deletePrivateFileApi(versionId: string, path: string): Promise<PrivateFiles> {
  return api.delete<PrivateFiles>(`/pipeline/${versionId}/files?path=${encodeURIComponent(path)}`);
}
