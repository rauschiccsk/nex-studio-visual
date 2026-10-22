/**
 * Centralized HTTP client for the NEX Studio frontend.
 *
 * Since E1 Phase B4 (CR-NS-051) the generic machinery lives in `nex-shared`
 * (`createApiClient`); this module is a THIN WRAPPER that supplies the
 * NEX-Studio-specific configuration and keeps the public surface the feature
 * modules under `services/api/*` and the `authStore` import (`api`, `ApiError`,
 * `TOKEN_STORAGE_KEY`, `fetchBlob`, `downloadFile`). The user and the logout live in
 * `services/api/auth.ts` (DEV-155 removed the unused copies that stood here).
 *
 * What stays NEX-Studio-specific (config, not in the lib):
 *   1. Base URL resolution from `VITE_API_BASE_URL` (bake-time) → `/api/v1`.
 *   2. The JWT token storage key `nex_studio_token` (localStorage).
 *   3. The 401 → clear-token + redirect-to-`/login?next=…` behavior.
 *
 * The default FastAPI `{detail}` error parser now ships in the lib, so this
 * wrapper relies on it (the previous local `extractErrorMessage` was identical).
 */

import { createApiClient, ApiError } from "nex-shared";

// Re-export the lib error under the historical path (`import { ApiError } from "@/services/api"`).
export { ApiError };

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

/**
 * Download a file the backend serves only to a signed-in user (a CSV statement, DEV-50) and hand it to the browser
 * as a file. A plain link cannot carry the token; the name comes from the server when it sends one.
 */
/**
 * A file the backend serves only to a signed-in reader (DEV-52: a screenshot of a Poradca question), as a Blob —
 * an `<img src>` cannot carry the Authorization header, so the screen fetches it and shows an object URL.
 */
export async function fetchBlob(path: string): Promise<Blob> {
  const token = readToken();
  const res = await fetch(`${resolveBaseUrl()}/api/v1${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw new Error(`Súbor sa nepodarilo načítať (${res.status}).`);
  return res.blob();
}

export async function downloadFile(path: string, fallbackName: string): Promise<void> {
  const token = readToken();
  const res = await fetch(`${resolveBaseUrl()}/api/v1${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw new Error(`Súbor sa nepodarilo stiahnuť (${res.status}).`);
  const disposition = res.headers.get("content-disposition") ?? "";
  const name = /filename="([^"]+)"/.exec(disposition)?.[1] ?? fallbackName;
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

export default api;
