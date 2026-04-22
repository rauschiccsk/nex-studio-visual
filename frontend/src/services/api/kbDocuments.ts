import api from "../api";
import type { PaginatedResponse } from "../../types";
import type {
  KbDocumentCreate,
  KbDocumentUpdate,
  KbDocumentRead,
  KbDocumentCategory,
} from "../../types/kbDocument";

export interface ListKbDocumentsParams {
  project_id?: string | null;
  module_id?: string | null;
  doc_category?: KbDocumentCategory;
  skip?: number;
  limit?: number;
}

export function listKbDocuments(
  params: ListKbDocumentsParams = {},
): Promise<PaginatedResponse<KbDocumentRead>> {
  return api.get<PaginatedResponse<KbDocumentRead>>("/kb-documents", {
    params: params as Record<string, string | number | boolean | null | undefined>,
  });
}

export function getKbDocument(id: string): Promise<KbDocumentRead> {
  return api.get<KbDocumentRead>(`/kb-documents/${id}`);
}

export function createKbDocument(data: KbDocumentCreate): Promise<KbDocumentRead> {
  return api.post<KbDocumentRead>("/kb-documents", data);
}

export function updateKbDocument(id: string, data: KbDocumentUpdate): Promise<KbDocumentRead> {
  return api.patch<KbDocumentRead>(`/kb-documents/${id}`, data);
}

export function deleteKbDocument(id: string): Promise<void> {
  return api.delete(`/kb-documents/${id}`);
}
