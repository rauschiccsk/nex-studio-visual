/**
 * API client for authentication endpoints.
 *
 * Maps to backend routes defined in ``backend.api.routes.auth``:
 *
 *   - ``POST  /auth/login``  → loginApi
 *   - ``POST  /auth/logout`` → logoutApi
 *   - ``GET   /auth/me``     → getMeApi
 */

import api from "../api";

/** Mirrors ``backend.schemas.auth.AuthUser``. */
export interface AuthUser {
  id: string;
  username: string;
  email: string;
  role: "ri" | "ha" | "shu";
  is_active: boolean;
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
