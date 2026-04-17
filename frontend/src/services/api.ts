/**
 * Centralized HTTP client for the NEX Studio frontend.
 *
 * DESIGN.md § 3.4 specifies a single `api.ts` entry point that all feature
 * API modules build on top of. Its responsibilities are:
 *
 *   1. Resolve the backend base URL from the Vite env var
 *      `VITE_API_BASE_URL` (bake-time) and prefix every request with the
 *      standard `/api/v1` path segment.
 *   2. Inject the JWT access token stored under `nex_studio_token` into
 *      every outbound request as a `Bearer` Authorization header.
 *   3. Automatically log the user out (clear the token, redirect to
 *      `/login`) on a `401 Unauthorized` response — mirroring the NEX
 *      Command pattern referenced in DESIGN.md.
 *   4. Expose ergonomic `api.get/post/put/patch/delete` helpers with full
 *      TypeScript generics for the response body.
 *
 * Implementation notes:
 *   - We deliberately use the platform `fetch` API to avoid adding a
 *     runtime dependency (axios). The wrapper provides the same
 *     ergonomics — typed methods plus a central "interceptor" chain —
 *     while staying dependency-free.
 *   - All network or decoding failures are surfaced as `ApiError`
 *     instances so callers can discriminate on `status` and `data`.
 *   - The module is designed to be stateless at import time. The only
 *     side effect occurs inside `handleUnauthorized` when a live 401
 *     response is received at runtime.
 */

/** localStorage key holding the JWT access token (see ProtectedRoute). */
export const TOKEN_STORAGE_KEY = "nex_studio_token";

/** REST version prefix shared by every backend route (see DESIGN.md § 6). */
const API_PREFIX = "/api/v1";

/**
 * Resolve the base URL used to build absolute request URLs.
 *
 * In development Vite proxies `/api/*` to the backend (see vite.config.ts)
 * so the env var is typically empty and we issue same-origin requests.
 * In production builds `VITE_API_BASE_URL` is baked in and points at the
 * deployed backend host (e.g. `http://localhost:9176`).
 */
function resolveBaseUrl(): string {
  const fromEnv = import.meta.env.VITE_API_BASE_URL;
  if (typeof fromEnv === "string" && fromEnv.length > 0) {
    // Strip a single trailing slash to keep join logic predictable.
    return fromEnv.replace(/\/$/, "");
  }
  return "";
}

/** Base URL resolved once at module load — see `resolveBaseUrl`. */
const BASE_URL = resolveBaseUrl();

/** HTTP verbs supported by the `request` helper. */
type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

/**
 * Optional per-request overrides.
 *
 * `skipAuth` is useful for endpoints that must not carry a token (for
 * example `POST /auth/login`). `signal` enables standard abort
 * integration from React components.
 */
export interface RequestOptions {
  /** Extra headers merged after the defaults — callers can override them. */
  headers?: Record<string, string>;
  /** Passed straight to `fetch` for cancellation support. */
  signal?: AbortSignal;
  /** When true, do not attach the Authorization header. */
  skipAuth?: boolean;
  /** When true, do not redirect to /login on a 401 response. */
  skipAuthRedirect?: boolean;
  /** Extra query string parameters, serialized with `URLSearchParams`. */
  params?: Record<string, string | number | boolean | undefined | null>;
}

/**
 * Error raised by the API client whenever a request does not complete
 * with a 2xx status. The original parsed body (if any) is preserved on
 * `data` so feature code can surface backend validation messages.
 */
export class ApiError extends Error {
  public readonly status: number;
  public readonly data: unknown;

  constructor(status: number, message: string, data: unknown = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.data = data;
  }
}

/** Read the current JWT from localStorage (SSR-safe). */
function readToken(): string | null {
  if (typeof window === "undefined") {
    return null;
  }
  return window.localStorage.getItem(TOKEN_STORAGE_KEY);
}

/**
 * Clear the stored JWT and bounce the user to `/login`.
 *
 * Centralizing this in the client lets every call site rely on a single
 * "session expired" behaviour without duplicating navigation logic. The
 * redirect uses `window.location.assign` rather than React Router because
 * the client is framework-agnostic and runs outside the component tree.
 */
function handleUnauthorized(): void {
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

/** Append serialized query params to a path (skipping undefined/null). */
function buildQueryString(
  params: RequestOptions["params"] | undefined,
): string {
  if (!params) {
    return "";
  }
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null) {
      continue;
    }
    search.append(key, String(value));
  }
  const serialized = search.toString();
  return serialized.length > 0 ? `?${serialized}` : "";
}

/** Ensure the request path starts with the `/api/v1` prefix exactly once. */
function buildUrl(path: string, params?: RequestOptions["params"]): string {
  const normalized = path.startsWith("/") ? path : `/${path}`;
  const withPrefix = normalized.startsWith(API_PREFIX)
    ? normalized
    : `${API_PREFIX}${normalized}`;
  return `${BASE_URL}${withPrefix}${buildQueryString(params)}`;
}

/**
 * Parse the response body into JSON when possible, otherwise return the
 * raw text (so callers can still read 204/empty responses).
 */
async function parseBody(response: Response): Promise<unknown> {
  if (response.status === 204 || response.status === 205) {
    return null;
  }
  const contentType = response.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) {
    // Guard against valid 200 responses with an empty body.
    const text = await response.text();
    return text.length > 0 ? (JSON.parse(text) as unknown) : null;
  }
  const text = await response.text();
  return text.length > 0 ? text : null;
}

/**
 * Low-level request primitive used by every verb helper.
 *
 * Callers should usually reach for `api.get/post/put/patch/delete`
 * instead — this function is exported for advanced cases (streaming,
 * custom methods) and to keep the wrapper consistent.
 */
export async function request<T>(
  method: HttpMethod,
  path: string,
  body?: unknown,
  options: RequestOptions = {},
): Promise<T> {
  const headers: Record<string, string> = {
    Accept: "application/json",
    ...options.headers,
  };

  // Only attach Content-Type when we actually send a JSON body — otherwise
  // FormData / multipart uploads would be corrupted by a fixed header.
  let serializedBody: BodyInit | undefined;
  if (body !== undefined && body !== null) {
    if (body instanceof FormData || body instanceof URLSearchParams) {
      serializedBody = body;
    } else {
      headers["Content-Type"] = headers["Content-Type"] ?? "application/json";
      serializedBody = JSON.stringify(body);
    }
  }

  if (!options.skipAuth) {
    const token = readToken();
    if (token && !headers.Authorization) {
      headers.Authorization = `Bearer ${token}`;
    }
  }

  const response = await fetch(buildUrl(path, options.params), {
    method,
    headers,
    body: serializedBody,
    signal: options.signal,
    // Same-origin credentials keep any future cookie auth working without
    // leaking tokens to third-party hosts.
    credentials: "same-origin",
  });

  if (response.status === 401 && !options.skipAuthRedirect) {
    handleUnauthorized();
  }

  const parsed = await parseBody(response);

  if (!response.ok) {
    const message = extractErrorMessage(parsed, response.statusText);
    throw new ApiError(response.status, message, parsed);
  }

  return parsed as T;
}

/**
 * Pull a human-readable error message from a FastAPI-style error body.
 *
 * FastAPI returns `{ "detail": "..." }` for HTTPException and
 * `{ "detail": [{ "msg": "...", ... }] }` for validation errors. We try
 * both shapes and fall back to the HTTP status text.
 */
function extractErrorMessage(parsed: unknown, fallback: string): string {
  if (parsed && typeof parsed === "object" && "detail" in parsed) {
    const detail = (parsed as { detail: unknown }).detail;
    if (typeof detail === "string") {
      return detail;
    }
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as { msg?: unknown };
      if (first && typeof first.msg === "string") {
        return first.msg;
      }
    }
  }
  return fallback || "Request failed";
}

/**
 * Ergonomic HTTP verb helpers — the public surface of the module.
 *
 * Usage:
 *
 *   const project = await api.get<Project>(`/projects/${slug}`);
 *   await api.post<void>("/projects", { name: "..." });
 */
export const api = {
  get<T>(path: string, options?: RequestOptions): Promise<T> {
    return request<T>("GET", path, undefined, options);
  },
  post<T>(path: string, body?: unknown, options?: RequestOptions): Promise<T> {
    return request<T>("POST", path, body, options);
  },
  put<T>(path: string, body?: unknown, options?: RequestOptions): Promise<T> {
    return request<T>("PUT", path, body, options);
  },
  patch<T>(path: string, body?: unknown, options?: RequestOptions): Promise<T> {
    return request<T>("PATCH", path, body, options);
  },
  delete<T>(path: string, options?: RequestOptions): Promise<T> {
    return request<T>("DELETE", path, undefined, options);
  },
};

export default api;
