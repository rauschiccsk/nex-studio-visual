/**
 * Auth Zustand store — DESIGN.md § 3.3 ``authStore``.
 *
 * Since E1 Phase C (CR-NS-052) the store machinery is the shared
 * ``createAuthStore`` (nex-shared, mode 'login'); this file supplies the
 * NEX-Studio-specific config and keeps the public surface (``useAuthStore`` with
 * ``token``/``user``/``login``/``logout``/``fetchMe``) so every consumer
 * (Sidebar, pages, ProtectedRoute) keeps reading it unchanged.
 *
 * NEX-Studio specifics (config, not in the lib):
 *   - role type ``'ri'|'ha'|'shu'`` (the generic user type ``T = AuthUser``);
 *   - the ``nex_studio_token`` storage key bridged to the api-client (``setToken``);
 *   - the E6 presence reset on login (``onLogin`` hook — app code, not lib);
 *   - Zustand persist under ``nex-auth`` (survives reloads).
 *
 * The 401 → in-memory clear wiring (registerAuthCallback) is handled inside the
 * shared store factory.
 */

import type { StoreApi, UseBoundStore } from "zustand";
import { createAuthStore, type LoginAuthState } from "nex-shared";

import { TOKEN_STORAGE_KEY } from "@/services/api";
import type { AuthUser } from "@/services/api/auth";
import { loginApi, logoutApi, getMeApi } from "@/services/api/auth";
import { useActiveContextStore } from "@/store/activeContextStore";
import { usePresenceStore } from "@/store/usePresenceStore";

export type { AuthUser };

/** The bound auth store. Annotated via NEX Studio's own `zustand` (the shared
 *  factory's return type references `zustand` across the package boundary —
 *  the explicit type keeps the emitted declaration portable). */
const authModule = createAuthStore<AuthUser, [string, string]>({
  mode: "login",
  persistKey: "nex-auth",
  // Probe the current user (GET /auth/me).
  getUser: getMeApi,
  // Authenticate; map the backend LoginResponse → {token, user}.
  login: async (username: string, password: string) => {
    const res = await loginApi(username, password);
    return { token: res.access_token, user: res.user };
  },
  // Invalidate the session on the backend (best-effort).
  logout: logoutApi,
  // Bridge the raw token to where the api-client reads it (nex_studio_token).
  setToken: (token) => {
    if (typeof window === "undefined") return;
    if (token) {
      window.localStorage.setItem(TOKEN_STORAGE_KEY, token);
    } else {
      window.localStorage.removeItem(TOKEN_STORAGE_KEY);
    }
  },
  redirectOnUnauthorized: "/login",
  // E6 (CR-NS-038): a fresh login starts "at computer" — never carry a persisted
  // "away" from a prior session. App-side hook (presence is not lib concern).
  // v4.0.33: the selected/pinned project persists in this browser's localStorage
  // (activeContextStore) — a DIFFERENT user logging in must NOT inherit the previous
  // user's project (bug: Nazar saw the Director's pinned NEX Shopify in the sidebar +
  // Metriky/UAT/PROD).
  //
  // v4.0.90: the persisted key is now scoped per user, so another user's pin is no
  // longer reachable at all. This clear stays as the IN-MEMORY half of the handover:
  // `onLogin` runs before App's effect re-points the store, so without it the previous
  // user's selection would sit on screen for those few frames. The incoming user's own
  // pin is restored by that effect (scopeActiveContextTo → rehydrate) — which is what
  // v4.0.33 could not do, and why a returning user used to lose their project on every
  // expired session.
  onLogin: () => {
    usePresenceStore.getState().setIsAway(false);
    useActiveContextStore.getState().setSelectedProject(null);
  },
});

export const useAuthStore: UseBoundStore<StoreApi<LoginAuthState<AuthUser, [string, string]>>> =
  authModule.useAuthStore;
