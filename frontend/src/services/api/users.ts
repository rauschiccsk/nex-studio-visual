/**
 * API client for user management endpoints.
 *
 * Maps to backend routes defined in ``backend.api.routes.users``:
 *
 *   - ``GET    /users``                       -> listUsersApi
 *   - ``POST   /users``                       -> createUserApi
 *   - ``PATCH  /users/{id}``                  -> updateUserApi
 *   - ``DELETE /users/{id}``                  -> deleteUserApi
 *   - ``POST   /users/{id}/change-password``  -> changePasswordApi
 */

import api from "../api";
import type {
  PaginatedResponse,
  UserCreate,
  UserRead,
  UserUpdate,
} from "../../types";

/** Query parameters accepted by the list endpoint. */
export interface ListUsersParams {
  skip?: number;
  limit?: number;
  role?: string;
  is_active?: boolean;
}

/**
 * Fetch a paginated list of users.
 *
 * Maps to ``GET /api/v1/users`` with optional query filters.
 */
export function listUsersApi(
  params: ListUsersParams = {},
): Promise<PaginatedResponse<UserRead>> {
  return api.get<PaginatedResponse<UserRead>>("/users", {
    params: {
      skip: params.skip,
      limit: params.limit,
      role: params.role,
      is_active: params.is_active,
    },
  });
}

/**
 * Create a new user.
 *
 * Maps to ``POST /api/v1/users``.  The backend hashes the plaintext
 * password with bcrypt before storage.
 */
export function createUserApi(data: UserCreate): Promise<UserRead> {
  return api.post<UserRead>("/users", data);
}

/**
 * Partially update an existing user.
 *
 * Maps to ``PATCH /api/v1/users/{id}``.  Only the fields present in
 * ``data`` are sent — omitted keys are left unchanged on the server.
 */
export function updateUserApi(
  id: string,
  data: UserUpdate,
): Promise<UserRead> {
  return api.patch<UserRead>(`/users/${id}`, data);
}

/**
 * Hard-delete a user.
 *
 * Maps to ``DELETE /api/v1/users/{id}``.  The backend rejects the call
 * with HTTP 409 when the user is referenced by other tables
 * (RESTRICT FK to projects, bugs, architect_sessions, etc.) — surface
 * the message to the operator and recommend deactivation instead.
 */
export function deleteUserApi(id: string): Promise<void> {
  return api.delete<void>(`/users/${id}`);
}

/**
 * Change a user's password.
 *
 * Maps to ``POST /api/v1/users/{id}/change-password``.  The backend hashes the new password with
 * bcrypt and bumps ``token_version`` to invalidate all existing JWTs for the target user.
 *
 * ``currentPassword`` is required for a SELF-service change (a user changing their OWN password) — the
 * backend verifies it (v4.0.32). An ``ri`` admin resetting ANOTHER user's password omits it.
 */
export function changePasswordApi(
  userId: string,
  newPassword: string,
  currentPassword?: string,
): Promise<UserRead> {
  return api.post<UserRead>(`/users/${userId}/change-password`, {
    new_password: newPassword,
    ...(currentPassword !== undefined ? { current_password: currentPassword } : {}),
  });
}
