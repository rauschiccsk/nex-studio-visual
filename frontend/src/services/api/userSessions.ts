/**
 * API client for user-session management endpoints (CR-NS-079).
 *
 * Maps to backend routes in ``backend.api.routes.user_sessions`` — the
 * whole router is gated ``require_ha_or_above``, so these calls are only
 * issued from the Settings → Relácie tab when the current user is ha+:
 *
 *   - ``GET    /user-sessions``        -> listUserSessionsApi
 *   - ``DELETE /user-sessions/{id}``   -> deleteUserSessionApi (revoke)
 *
 * A session row is a per-user JWT lifecycle anchor; deleting it bumps the
 * server-side ``token_version`` so all outstanding tokens for that user
 * are invalidated.
 *
 * ``UserSessionRead`` is the canonical kit shape (nex-shared) — it mirrors
 * the backend serializer field-for-field, so it doubles as the wire type
 * and feeds straight into the kit ``SessionsPanel`` with no mapping.
 */

import api from "../api";
import type { PaginatedResponse } from "../../types";
import type { UserSessionRead } from "nex-shared";

/** Query parameters accepted by the list endpoint. */
export interface ListUserSessionsParams {
  /** Filter to the sessions of a single user (defaults to all). */
  user_id?: string;
  skip?: number;
  limit?: number;
}

/**
 * Fetch a paginated list of user sessions (ordered ``created_at DESC``).
 *
 * Maps to ``GET /api/v1/user-sessions``.
 */
export function listUserSessionsApi(
  params: ListUserSessionsParams = {},
): Promise<PaginatedResponse<UserSessionRead>> {
  return api.get<PaginatedResponse<UserSessionRead>>("/user-sessions", {
    params: {
      user_id: params.user_id,
      skip: params.skip,
      limit: params.limit,
    },
  });
}

/**
 * Revoke (hard-delete) a user session.
 *
 * Maps to ``DELETE /api/v1/user-sessions/{id}``.
 */
export function deleteUserSessionApi(id: string): Promise<void> {
  return api.delete<void>(`/user-sessions/${id}`);
}
