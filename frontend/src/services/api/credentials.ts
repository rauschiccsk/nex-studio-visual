import api from "../api";
import type {
  CredentialRead,
  CredentialCreate,
  CredentialContent,
  CredentialContentUpdate,
} from "../../types/credential";

export function listCredentials(): Promise<CredentialRead[]> {
  return api.get<CredentialRead[]>("/credentials");
}

export function getCredentialContent(id: string): Promise<CredentialContent> {
  return api.get<CredentialContent>(`/credentials/${id}/content`);
}

export function putCredentialContent(
  id: string,
  payload: CredentialContentUpdate,
): Promise<CredentialContent> {
  return api.put<CredentialContent>(`/credentials/${id}/content`, payload);
}

export function createCredential(data: CredentialCreate): Promise<CredentialRead> {
  return api.post<CredentialRead>("/credentials", data);
}

export function deleteCredential(id: string): Promise<void> {
  return api.delete(`/credentials/${id}`);
}
