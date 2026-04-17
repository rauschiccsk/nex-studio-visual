/**
 * Tests for ``authStore`` — login persistence, logout clearing, fetchMe.
 */

import { describe, it, expect, vi, beforeEach, type Mock } from "vitest";

// Mock the auth API module before any imports that reference it.
vi.mock("@/services/api/auth", () => ({
  loginApi: vi.fn(),
  logoutApi: vi.fn(),
  getMeApi: vi.fn(),
}));

// Mock the base api module to provide TOKEN_STORAGE_KEY and registerAuthCallback.
vi.mock("@/services/api", () => ({
  TOKEN_STORAGE_KEY: "nex_studio_token",
  registerAuthCallback: vi.fn(),
  default: {},
}));

import { useAuthStore } from "@/store/authStore";
import { loginApi, logoutApi, getMeApi } from "@/services/api/auth";
import type { AuthUser, LoginResponse } from "@/services/api/auth";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const fakeUser: AuthUser = {
  id: "00000000-0000-0000-0000-000000000001",
  username: "zoltan",
  email: "zoltan@isnex.ai",
  role: "ri",
  is_active: true,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const fakeLoginResponse: LoginResponse = {
  access_token: "jwt.token.here",
  token_type: "bearer",
  expires_in: 28800,
  user: fakeUser,
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Reset store to initial state between tests. */
function resetStore(): void {
  useAuthStore.setState({ token: null, user: null });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("authStore", () => {
  beforeEach(() => {
    resetStore();
    vi.clearAllMocks();
    window.localStorage.clear();
  });

  // --- login ---

  it("login persists token and user in store", async () => {
    (loginApi as Mock).mockResolvedValue(fakeLoginResponse);

    await useAuthStore.getState().login("zoltan", "secret");

    const state = useAuthStore.getState();
    expect(state.token).toBe("jwt.token.here");
    expect(state.user).toEqual(fakeUser);
  });

  it("login writes token to localStorage under TOKEN_STORAGE_KEY", async () => {
    (loginApi as Mock).mockResolvedValue(fakeLoginResponse);

    await useAuthStore.getState().login("zoltan", "secret");

    expect(window.localStorage.getItem("nex_studio_token")).toBe(
      "jwt.token.here",
    );
  });

  it("login propagates API errors", async () => {
    (loginApi as Mock).mockRejectedValue(new Error("Invalid credentials"));

    await expect(
      useAuthStore.getState().login("wrong", "creds"),
    ).rejects.toThrow("Invalid credentials");

    const state = useAuthStore.getState();
    expect(state.token).toBeNull();
    expect(state.user).toBeNull();
  });

  // --- logout ---

  it("logout clears token and user from store", async () => {
    // Pre-populate
    useAuthStore.setState({ token: "jwt.token.here", user: fakeUser });
    (logoutApi as Mock).mockResolvedValue(undefined);

    await useAuthStore.getState().logout();

    const state = useAuthStore.getState();
    expect(state.token).toBeNull();
    expect(state.user).toBeNull();
  });

  it("logout removes token from localStorage", async () => {
    window.localStorage.setItem("nex_studio_token", "jwt.token.here");
    useAuthStore.setState({ token: "jwt.token.here", user: fakeUser });
    (logoutApi as Mock).mockResolvedValue(undefined);

    await useAuthStore.getState().logout();

    expect(window.localStorage.getItem("nex_studio_token")).toBeNull();
  });

  it("logout clears state even if API call fails", async () => {
    useAuthStore.setState({ token: "jwt.token.here", user: fakeUser });
    (logoutApi as Mock).mockRejectedValue(new Error("Network error"));

    await useAuthStore.getState().logout();

    const state = useAuthStore.getState();
    expect(state.token).toBeNull();
    expect(state.user).toBeNull();
  });

  // --- fetchMe ---

  it("fetchMe updates user from /auth/me", async () => {
    useAuthStore.setState({ token: "jwt.token.here", user: null });
    (getMeApi as Mock).mockResolvedValue(fakeUser);

    await useAuthStore.getState().fetchMe();

    expect(useAuthStore.getState().user).toEqual(fakeUser);
  });

  it("fetchMe does nothing when no token is present", async () => {
    useAuthStore.setState({ token: null, user: null });

    await useAuthStore.getState().fetchMe();

    expect(getMeApi).not.toHaveBeenCalled();
  });

  it("fetchMe clears state when API returns 401", async () => {
    useAuthStore.setState({ token: "expired.token", user: fakeUser });
    (getMeApi as Mock).mockRejectedValue(new Error("Unauthorized"));

    await useAuthStore.getState().fetchMe();

    const state = useAuthStore.getState();
    expect(state.token).toBeNull();
    expect(state.user).toBeNull();
  });
});
