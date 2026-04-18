/**
 * API client for Design Documents.
 *
 * Maps to ``backend.api.routes.design_documents``:
 *
 *   - ``GET    /design-documents``          → listDesignDocuments
 *   - ``GET    /design-documents/{id}``     → getDesignDocument
 *   - ``POST   /design-documents``          → createDesignDocument
 *   - ``PATCH  /design-documents/{id}``     → updateDesignDocument
 *   - ``DELETE /design-documents/{id}``     → deleteDesignDocument
 */

import api from "../api";
import type { PaginatedResponse } from "../../types/common";
import type {
  DesignDocumentCreate,
  DesignDocumentRead,
  DesignDocumentType,
  DesignDocumentUpdate,
} from "../../types/designDocument";

export interface ListDesignDocsParams {
  project_id?: string;
  doc_type?: DesignDocumentType;
  skip?: number;
  limit?: number;
}

export function listDesignDocuments(
  params?: ListDesignDocsParams,
): Promise<PaginatedResponse<DesignDocumentRead>> {
  return api.get<PaginatedResponse<DesignDocumentRead>>(
    "/design-documents",
    { params: params as Record<string, string | number | undefined> },
  );
}

export function getDesignDocument(id: string): Promise<DesignDocumentRead> {
  return api.get<DesignDocumentRead>(`/design-documents/${id}`);
}

export function createDesignDocument(
  data: DesignDocumentCreate,
): Promise<DesignDocumentRead> {
  return api.post<DesignDocumentRead>("/design-documents", data);
}

export function updateDesignDocument(
  id: string,
  data: DesignDocumentUpdate,
): Promise<DesignDocumentRead> {
  return api.patch<DesignDocumentRead>(`/design-documents/${id}`, data);
}

export function deleteDesignDocument(id: string): Promise<void> {
  return api.delete<void>(`/design-documents/${id}`);
}
