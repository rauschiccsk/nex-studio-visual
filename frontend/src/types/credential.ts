/**
 * TypeScript type definitions for the Credentials domain.
 *
 * Credentials live OUTSIDE the KB. Their REST API is gated by `ri`
 * role on the backend (CLAUDE.md §13). The frontend wires Authorization
 * headers automatically via ``services/api.ts``; an unauthenticated
 * client (or a non-`ri` user) receives 401/403.
 */

export interface CredentialRead {
  id: string;
  title: string;
  file_path: string;
  created_at: string;
  updated_at: string;
}

export interface CredentialCreate {
  title: string;
  filename: string;
  content: string;
}

export interface CredentialContent {
  credential_id: string;
  file_path: string;
  content: string;
  size_bytes: number;
}

export interface CredentialContentUpdate {
  content: string;
}
