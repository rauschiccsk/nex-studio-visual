/**
 * API client for authentication endpoints.
 *
 * Maps to backend routes defined in ``backend.api.routes.auth``:
 *
 *   - ``POST  /auth/login``   → loginApi
 *   - ``POST  /auth/refresh`` → refreshApi
 *   - ``POST  /auth/logout``  → logoutApi
 *   - ``GET   /auth/me``      → getMeApi
 */

import api from "../api";

/** Mirrors ``backend.schemas.auth.AuthUser``. */
export interface AuthUser {
  id: string;
  username: string;
  email: string;
  role: "ri" | "ha" | "shu";
  is_active: boolean;
  /** Given name — nullable (legacy users may lack it). CR-NS-089. */
  first_name?: string | null;
  /** Family name — nullable (legacy users may lack it). CR-NS-089. */
  last_name?: string | null;
  created_at: string;
  updated_at: string;
}

/** Mirrors ``backend.schemas.auth.LoginResponse``. */
export interface LoginResponse {
  access_token: string;
  token_type: "bearer";
  expires_in: number;
  user: AuthUser;
}

/**
 * Authenticate a user with username + password.
 *
 * ``skipAuth`` prevents attaching a (possibly stale) JWT to the login
 * request itself.
 */
export function loginApi(
  username: string,
  password: string,
): Promise<LoginResponse> {
  return api.post<LoginResponse>(
    "/auth/login",
    { username, password },
    { skipAuth: true },
  );
}

/**
 * Silently renew the current (still-valid) session — sliding expiration.
 *
 * Mirrors ``POST /auth/refresh``. Unlike login it needs no body: the current
 * bearer token (attached by the api-client) authenticates the request, and the
 * backend re-issues a token for the same user + same ``token_version`` with a
 * fresh expiry — same ``LoginResponse`` shape.
 *
 * A FAILED refresh (already-expired / bumped token → 401) intentionally does
 * NOT set ``skipAuthRedirect``: it falls through to the existing 401 →
 * ``/login?next=…`` behavior, exactly as the spec requires. The keep-alive
 * hook only ever calls this for a still-valid, recently-active session, so the
 * happy path is silent (no redirect).
 */
export function refreshApi(): Promise<LoginResponse> {
  return api.post<LoginResponse>("/auth/refresh", undefined);
}

/**
 * Invalidate the current user's session (bumps ``token_version``).
 *
 * Returns ``void`` — the backend responds with 204 No Content.
 * ``skipAuthRedirect`` prevents the 401 interceptor from firing if
 * the token is already expired when the user clicks "logout".
 */
export function logoutApi(): Promise<void> {
  return api.post<void>("/auth/logout", undefined, {
    skipAuthRedirect: true,
  });
}

/**
 * Fetch the currently authenticated user's profile.
 */
export function getMeApi(): Promise<AuthUser> {
  return api.get<AuthUser>("/auth/me");
}
