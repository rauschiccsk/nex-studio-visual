# E1 Phase B4 — generic API client into `nex-shared` (FE plumbing)

> **E1 Phase B, slice 4 (the last B slice).** Extract the GENERIC API-client machinery into `nex-shared`
> (live v0.3.0); NEX Studio's `services/api.ts` becomes a thin wrapper that configures it. **Text-only** (no
> UI/Tailwind/router/store) — but CRITICAL plumbing (every API call routes through it), so verify thoroughly.
> Grounded 2026-06-14 (2-lens). **The auth-store + the two auth modes + login UI + ProtectedRoute are
> Phase C — NOT B4** (don't split the auth-store from its modes). Cross-repo CR; push nex-shared + tag
> **v0.4.0** first, then nex-studio.

## Part 1 — `nex-shared/src/api-client.ts` (bump → v0.4.0)
A generic, configurable client factory (the machinery is ~identical across NEX Studio / inbox / ledger; the
differences become config). Text-only TS — NO Tailwind, NO react-router, NO stores, NO app imports.
- **`createApiClient(config)`** → `{ get, post, put, patch, delete }` + a low-level `request<T>(method, path,
  body?, options?)`.
- **`ApiClientConfig`**: `{ baseUrl: string; apiPrefix?: string (default '/api/v1'); getToken: () =>
  string | null; onUnauthorized?: () => void; errorParser?: (status, body) => string; timeout?: number;
  requestIdHeader?: string; requestIdGenerator?: () => string }`.
- **`ApiError`** class: `{ status, message, data }` + optional `code`/`symbol`/`resolution` (so the richer
  inbox/ledger envelopes fit later). Exported.
- **`RequestOptions`**: `{ headers?, signal?, params?, skipAuth?, skipAuthRedirect? }`.
- Behavior to preserve from NEX Studio's `api.ts`: Bearer token injection (via `getToken`), the **FormData /
  multipart guard** (don't set Content-Type — let fetch set the boundary), JSON/binary/204 content-type-aware
  parsing, `buildUrl` + `buildQueryString` (params), a **401 dispatcher** that calls `config.onUnauthorized`
  (+ respects `skipAuthRedirect`), AbortSignal + optional timeout. A **default `errorParser`** that handles
  the FastAPI `{detail: string | [{msg}]}` shape (overridable).
- **Circular-dep pattern:** export a `registerAuthCallback(cb)` (or accept `onUnauthorized` lazily) so the
  app's auth-store can wire logout WITHOUT the lib importing the store. (Mirror Studio's current
  `registerAuthCallback`.)
- `src/index.ts` exports `createApiClient`, `ApiError`, `ApiClientConfig`, `RequestOptions`,
  `registerAuthCallback`. Existing exports unchanged.

## Part 2 — NEX Studio consumes it (v0.4.0)
Bump git-dep `#v0.4.0`. **`services/api.ts`** becomes a thin wrapper:
```
export const api = createApiClient({
  baseUrl: resolveBaseUrl(),         // keep Studio's VITE_API_BASE_URL or "" same-origin logic
  apiPrefix: "/api/v1",
  getToken: () => localStorage.getItem("nex_studio_token"),   // Studio's TOKEN_STORAGE_KEY stays app-side
  onUnauthorized: registeredCallback,                         // keep the registerAuthCallback wiring
  errorParser: extractErrorMessage,                           // Studio's FastAPI parser (or use the default)
});
```
- The exact base-URL resolution, the token storage key (`nex_studio_token`), and the 401 redirect behavior
  stay app-side (config). The `registerAuthCallback` the authStore uses keeps working.
- **All ~16 per-endpoint modules under `services/api/*.ts`** (auth, projects, versions, pipeline, etc.)
  keep working unchanged — they import the configured `api`. The `authStore` is **untouched** (Phase C).
- WS/SSE URL helpers (agentTerminal/pipeline) that derive ws:// from VITE_API_BASE_URL stay app-side.

## Acceptance
- `nex-shared`: `npm run build` (tsup) → dist + types updated; `tsc --noEmit` clean.
- NEX Studio: consumes `nex-shared#v0.4.0`; `npm run build` (tsc+vite) + `npm run lint` clean; **vitest GREEN
  (218/218)** — the api.ts refactor must not break ANY endpoint module, the 401/login flow, or the API mocks
  (the test api-mock layer). Keep tests honest.
- **Behavior parity (critical):** every API call works identically — auth (login/logout/me), all CRUD, the
  401→logout redirect, FormData uploads, query params, error messages. The cockpit/pipeline calls work.
- CI green incl. Deploy.

## Seams / out of scope
NO UI changes. The **auth-store, login UI, ProtectedRoute, and the two auth modes are Phase C** — B4 only
ships the api-client factory + wires Studio's `api.ts` to it. Inbox/Ledger NOT migrated (Phase D). Backend
untouched. If a Studio api.ts behavior can't be expressed via config without baking Studio-specifics into the
lib → STOP + flag. Push/lockfile per B1–B3 (Dedo re-resolves v0.4.0).

**This closes Phase B** (B1 tokens+Button, B2 layout shell, B3 primitives+RR7, B4 api-client). Next: Phase C
(shared auth module, two modes) → Phase D (scaffolding + migrate Inbox/Ledger).
