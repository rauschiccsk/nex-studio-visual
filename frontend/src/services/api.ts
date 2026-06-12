/**
 * Centralized HTTP client for the NEX Studio frontend.
 *
 * Since E1 Phase B4 (CR-NS-051) the generic machinery lives in `nex-shared`
 * (`createApiClient`); this module is a THIN WRAPPER that supplies the
 * NEX-Studio-specific configuration and keeps the historical public surface
 * (`api`, `request`, `ApiError`, `RequestOptions`, `registerAuthCallback`,
 * `TOKEN_STORAGE_KEY`, `getCurrentUser`, `logoutUser`) so the ~16 feature
 * modules under `services/api/*` and the `authStore` keep importing it unchanged.
 *
 * What stays NEX-Studio-specific (config, not in the lib):
 *   1. Base URL resolution from `VITE_API_BASE_URL` (bake-time) → `/api/v1`.
 *   2. The JWT token storage key `nex_studio_token` (localStorage).
 *   3. The 401 → clear-token + redirect-to-`/login?next=…` behavior.
 *
 * The default FastAPI `{detail}` error parser now ships in the lib, so this
 * wrapper relies on it (the previous local `extractErrorMessage` was identical).
 */

import { createApiClient, ApiError, registerAuthCallback } from "nex-shared";
import type { RequestOptions } from "nex-shared";

// Re-export the lib primitives under the historical paths so existing imports
// (`import { ApiError, RequestOptions, registerAuthCallback } from "@/services/api"`)
// keep resolving.
export { ApiError, registerAuthCallback };
export type { RequestOptions };

/** localStorage key holding the JWT access token (see ProtectedRoute). */
export const TOKEN_STORAGE_KEY = "nex_studio_token";

/**
 * Resolve the base URL used to build absolute request URLs.
 *
 * In development Vite proxies `/api/*` to the backend (see vite.config.ts) so
 * the env var is typically empty and we issue same-origin requests. In
 * production builds `VITE_API_BASE_URL` is baked in and points at the deployed
 * backend host (e.g. `http://localhost:9176`).
 */
function resolveBaseUrl(): string {
  const fromEnv = import.meta.env.VITE_API_BASE_URL;
  if (typeof fromEnv === "string" && fromEnv.length > 0) {
    // Strip a single trailing slash to keep join logic predictable.
    return fromEnv.replace(/\/$/, "");
  }
  return "";
}

/** Read the current JWT from localStorage (SSR-safe). */
function readToken(): string | null {
  if (typeof window === "undefined") {
    return null;
  }
  return window.localStorage.getItem(TOKEN_STORAGE_KEY);
}

/**
 * Clear the stored JWT and bounce the user to `/login` (the configured 401
 * behavior). Runs AFTER the registered authStore callback (which clears the
 * in-memory Zustand state). Uses `window.location.assign` rather than React
 * Router because the client runs outside the component tree.
 */
function handleUnauthorizedRedirect(): void {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.removeItem(TOKEN_STORAGE_KEY);
  // Preserve the pre-logout path so LoginPage can bounce the user back.
  const current = window.location.pathname + window.location.search;
  if (!window.location.pathname.startsWith("/login")) {
    const next = encodeURIComponent(current);
    window.location.assign(`/login?next=${next}`);
  }
}

/**
 * The configured client. Feature modules import `api` and call
 * `api.get/post/put/patch/delete`; `request` is the low-level escape hatch.
 */
export const api = createApiClient({
  baseUrl: resolveBaseUrl(),
  apiPrefix: "/api/v1",
  getToken: readToken,
  onUnauthorized: handleUnauthorizedRedirect,
  // errorParser omitted → the lib's default FastAPI `{detail}` parser is used.
});

/** Low-level request primitive (exported for advanced/streaming cases). */
export const request = api.request;

// ---------------------------------------------------------------------------
// Convenience auth helpers — thin wrappers over the verb helpers
// ---------------------------------------------------------------------------

/**
 * Convenience type mirroring ``AuthUser`` from ``api/auth.ts``.
 *
 * Duplicated here as a lightweight structural type to avoid importing from the
 * feature module (which would make the base client depend on a feature layer).
 */
export interface CurrentUser {
  id: string;
  username: string;
  email: string;
  role: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

/** Fetch the currently authenticated user's profile (`GET /auth/me`). */
export function getCurrentUser(options?: RequestOptions): Promise<CurrentUser> {
  return api.get<CurrentUser>("/auth/me", options);
}

/**
 * Invalidate the current session on the backend (`POST /auth/logout`). Uses
 * ``skipAuthRedirect`` to prevent the 401 interceptor from firing when the
 * token is already expired at the moment the user clicks logout.
 */
export function logoutUser(): Promise<void> {
  return api.post<void>("/auth/logout", undefined, {
    skipAuthRedirect: true,
  });
}

export default api;
