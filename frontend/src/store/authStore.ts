/**
 * Auth Zustand store — DESIGN.md § 3.3 ``authStore``.
 *
 * Holds JWT token + user object. Persists to ``localStorage`` under
 * key ``nex-auth`` via Zustand ``persist`` middleware so the session
 * survives page reloads.
 *
 * The store deliberately does NOT store the full ``LoginResponse`` —
 * only the token string and the safe ``AuthUser`` projection.
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";

import { TOKEN_STORAGE_KEY, registerAuthCallback } from "@/services/api";
import type { AuthUser } from "@/services/api/auth";
import { loginApi, logoutApi, getMeApi } from "@/services/api/auth";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface AuthState {
  /** JWT access token (null when logged out). */
  token: string | null;
  /** Authenticated user profile (null when logged out). */
  user: AuthUser | null;

  /** Authenticate with username + password. Persists token. */
  login: (username: string, password: string) => Promise<void>;
  /** Clear session locally and invalidate on the backend. */
  logout: () => Promise<void>;
  /** Refresh the user profile from ``GET /auth/me``. */
  fetchMe: () => Promise<void>;
}

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      token: null,
      user: null,

      async login(username: string, password: string): Promise<void> {
        const res = await loginApi(username, password);

        // Persist the raw token under the key the api.ts interceptor reads.
        if (typeof window !== "undefined") {
          window.localStorage.setItem(TOKEN_STORAGE_KEY, res.access_token);
        }

        set({ token: res.access_token, user: res.user });
      },

      async logout(): Promise<void> {
        try {
          await logoutApi();
        } catch {
          // Best-effort — clear local state regardless.
        }

        if (typeof window !== "undefined") {
          window.localStorage.removeItem(TOKEN_STORAGE_KEY);
        }

        set({ token: null, user: null });
      },

      async fetchMe(): Promise<void> {
        const { token } = get();
        if (!token) return;

        try {
          const user = await getMeApi();
          set({ user });
        } catch {
          // Token expired / invalid — clear state.
          if (typeof window !== "undefined") {
            window.localStorage.removeItem(TOKEN_STORAGE_KEY);
          }
          set({ token: null, user: null });
        }
      },
    }),
    {
      name: "nex-auth",
      // Only persist token + user; actions are recreated by Zustand.
      partialize: (state) => ({
        token: state.token,
        user: state.user,
      }),
    },
  ),
);

// ---------------------------------------------------------------------------
// Wire authStore → api.ts 401 interceptor
// ---------------------------------------------------------------------------
// Clear in-memory Zustand state when the API client detects a 401. This
// avoids a circular import (api.ts never imports authStore) by using the
// callback registration pattern exposed by ``registerAuthCallback``.
registerAuthCallback(() => {
  useAuthStore.setState({ token: null, user: null });
});
