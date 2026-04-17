/**
 * Tests for the ProtectedRoute auth guard component.
 *
 * Verifies:
 *   1. Unauthenticated users are redirected to /login
 *   2. Authenticated users can access protected content
 *   3. fetchMe is called on mount when a token exists
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

/* ------------------------------------------------------------------ */
/*  Mock authStore                                                     */
/* ------------------------------------------------------------------ */

const mockFetchMe = vi.fn().mockResolvedValue(undefined);

let mockToken: string | null = null;

vi.mock("@/store/authStore", () => {
  // Minimal Zustand-like store mock
  const store = {
    getState: () => ({
      token: mockToken,
      user: mockToken
        ? { id: 1, username: "admin", email: "a@b.c", role: "ri" }
        : null,
      fetchMe: mockFetchMe,
      login: vi.fn(),
      logout: vi.fn(),
    }),
    setState: vi.fn(),
    subscribe: vi.fn(() => vi.fn()),
    destroy: vi.fn(),
  };

  // Zustand selector hook — call selector with current state
  const useAuthStore = (selector?: (s: ReturnType<typeof store.getState>) => unknown) => {
    const state = store.getState();
    return selector ? selector(state) : state;
  };
  useAuthStore.getState = store.getState;
  useAuthStore.setState = store.setState;
  useAuthStore.subscribe = store.subscribe;

  return { useAuthStore };
});

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

/**
 * Dynamically import ProtectedRoute so the mock is applied first.
 */
async function getProtectedRoute() {
  const mod = await import("@/components/auth/ProtectedRoute");
  return mod.default;
}

function ProtectedContent() {
  return <div data-testid="protected-content">Secret Page</div>;
}

function LoginStub() {
  return <div data-testid="login-page">Login</div>;
}

/* ------------------------------------------------------------------ */
/*  Tests                                                              */
/* ------------------------------------------------------------------ */

describe("ProtectedRoute", () => {
  beforeEach(() => {
    mockToken = null;
    mockFetchMe.mockClear();
    mockFetchMe.mockResolvedValue(undefined);
  });

  it("redirects to /login when there is no token", async () => {
    mockToken = null;
    const ProtectedRoute = await getProtectedRoute();

    render(
      <MemoryRouter initialEntries={["/dashboard"]}>
        <Routes>
          <Route path="/login" element={<LoginStub />} />
          <Route
            path="/dashboard"
            element={
              <ProtectedRoute>
                <ProtectedContent />
              </ProtectedRoute>
            }
          />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("login-page")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("protected-content")).toBeNull();
    expect(mockFetchMe).not.toHaveBeenCalled();
  });

  it("renders children when a valid token exists", async () => {
    mockToken = "valid-jwt-token";
    const ProtectedRoute = await getProtectedRoute();

    render(
      <MemoryRouter initialEntries={["/dashboard"]}>
        <Routes>
          <Route path="/login" element={<LoginStub />} />
          <Route
            path="/dashboard"
            element={
              <ProtectedRoute>
                <ProtectedContent />
              </ProtectedRoute>
            }
          />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("protected-content")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("login-page")).toBeNull();
  });

  it("calls fetchMe on mount when token is present", async () => {
    mockToken = "valid-jwt-token";
    const ProtectedRoute = await getProtectedRoute();

    render(
      <MemoryRouter initialEntries={["/dashboard"]}>
        <Routes>
          <Route
            path="/dashboard"
            element={
              <ProtectedRoute>
                <ProtectedContent />
              </ProtectedRoute>
            }
          />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(mockFetchMe).toHaveBeenCalledTimes(1);
    });
  });
});
