/**
 * useSessionKeepAlive — silent sliding-session renewal while the user is active
 * (docs/specs/session-keepalive.md §Tests, Frontend).
 *
 * Asserts the three behaviours the spec pins down:
 *   1. renews when the token is near expiry AND the user was recently active;
 *   2. does NOT renew when the user is idle (no recent activity);
 *   3. a failed refresh falls through to the existing logout path and is NOT
 *      retried for the same token (no loop-spam).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";

const { refreshApi, setState, FakeApiError } = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(status: number) {
      super(`HTTP ${status}`);
      this.status = status;
    }
  }
  return { refreshApi: vi.fn(), setState: vi.fn(), FakeApiError };
});

vi.mock("@/services/api/auth", () => ({ refreshApi }));
vi.mock("@/store/authStore", () => ({ useAuthStore: { setState } }));
vi.mock("@/services/api", () => ({
  TOKEN_STORAGE_KEY: "nex_studio_token",
  ApiError: FakeApiError,
}));

import {
  useSessionKeepAlive,
  KEEPALIVE_CHECK_INTERVAL_MS,
  KEEPALIVE_RENEW_FRACTION,
  KEEPALIVE_RETRY_BASE_MS,
} from "@/hooks/useSessionKeepAlive";

const TOKEN_KEY = "nex_studio_token";

/** Build a JWT-shaped string carrying only the ``iat``/``exp`` timing claims. */
function makeToken(iatMs: number, expMs: number): string {
  const header = btoa(JSON.stringify({ alg: "HS256", typ: "JWT" }));
  const payload = btoa(
    JSON.stringify({
      sub: "u1",
      iat: Math.floor(iatMs / 1000),
      exp: Math.floor(expMs / 1000),
    }),
  );
  return `${header}.${payload}.sig`;
}

/**
 * A token whose lifetime is 4 check-intervals: with RENEW_FRACTION=0.75 its
 * renewal window opens at exactly 3 intervals in, while the token is still
 * valid (it expires at 4 intervals).
 */
function nearExpiryToken(t0: number): string {
  const lifetime = 4 * KEEPALIVE_CHECK_INTERVAL_MS;
  return makeToken(t0, t0 + lifetime);
}
const RENEW_AFTER_INTERVALS =
  (4 * KEEPALIVE_RENEW_FRACTION) as number; // = 3

describe("useSessionKeepAlive", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    refreshApi.mockReset();
    setState.mockReset();
    window.localStorage.clear();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("renews the token when near expiry AND the user is recently active", async () => {
    const t0 = Date.now();
    window.localStorage.setItem(TOKEN_KEY, nearExpiryToken(t0));
    refreshApi.mockResolvedValue({
      access_token: "renewed.jwt.token",
      token_type: "bearer",
      expires_in: 480 * 60,
      user: { username: "admin" },
    });

    renderHook(() => useSessionKeepAlive());

    // The user does something now → marks the session active.
    act(() => {
      window.dispatchEvent(new Event("pointerdown"));
    });

    // Advance into the renewal window (75% of lifetime = 3 intervals).
    await act(async () => {
      await vi.advanceTimersByTimeAsync(
        RENEW_AFTER_INTERVALS * KEEPALIVE_CHECK_INTERVAL_MS,
      );
    });

    expect(refreshApi).toHaveBeenCalledTimes(1);
    // Fresh token stored under the SAME key the api-client reads …
    expect(window.localStorage.getItem(TOKEN_KEY)).toBe("renewed.jwt.token");
    // … and the in-memory store kept in lock-step.
    expect(setState).toHaveBeenCalledWith({ token: "renewed.jwt.token" });
  });

  it("does NOT renew when the user is idle (no recent activity)", async () => {
    const t0 = Date.now();
    window.localStorage.setItem(TOKEN_KEY, nearExpiryToken(t0));

    renderHook(() => useSessionKeepAlive());

    // No activity dispatched → the session is idle → let it expire.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(
        RENEW_AFTER_INTERVALS * KEEPALIVE_CHECK_INTERVAL_MS,
      );
    });

    expect(refreshApi).not.toHaveBeenCalled();
    // Token untouched — the existing 401 → /login flow handles idle logout.
    expect(window.localStorage.getItem(TOKEN_KEY)).toBe(nearExpiryToken(t0));
  });

  it("stops retrying after a 401 — the token is dead, asking again cannot help", async () => {
    const t0 = Date.now();
    const token = nearExpiryToken(t0);
    window.localStorage.setItem(TOKEN_KEY, token);
    refreshApi.mockRejectedValue(new FakeApiError(401));

    renderHook(() => useSessionKeepAlive());
    act(() => {
      window.dispatchEvent(new Event("pointerdown"));
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(
        RENEW_AFTER_INTERVALS * KEEPALIVE_CHECK_INTERVAL_MS,
      );
    });
    expect(refreshApi).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(20 * KEEPALIVE_CHECK_INTERVAL_MS);
    });
    expect(refreshApi).toHaveBeenCalledTimes(1);
    expect(window.localStorage.getItem(TOKEN_KEY)).toBe(token);
    expect(setState).not.toHaveBeenCalled();
  });

  it("RETRIES after a transient failure — the token is still valid", async () => {
    // The regression this exists for: one failed attempt used to mark the token as
    // "tried" and it was never retried, so a backend restart during the renewal window
    // ended a session that had hours left. Five cockpit deploys in an afternoon is
    // exactly that shape of failure.
    const t0 = Date.now();
    window.localStorage.setItem(TOKEN_KEY, nearExpiryToken(t0));
    refreshApi.mockRejectedValue(new Error("network"));

    renderHook(() => useSessionKeepAlive());
    const keepActive = () => window.dispatchEvent(new Event("pointerdown"));
    act(keepActive);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(
        RENEW_AFTER_INTERVALS * KEEPALIVE_CHECK_INTERVAL_MS,
      );
    });
    expect(refreshApi).toHaveBeenCalledTimes(1);

    // Inside the backoff → still just the one attempt.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(KEEPALIVE_RETRY_BASE_MS / 2);
      keepActive();
    });
    expect(refreshApi).toHaveBeenCalledTimes(1);

    // Past the backoff → it tries again instead of giving up.
    await act(async () => {
      keepActive();
      await vi.advanceTimersByTimeAsync(KEEPALIVE_RETRY_BASE_MS);
    });
    expect(refreshApi.mock.calls.length).toBeGreaterThan(1);
  });

  it("a retry that succeeds stores the fresh token", async () => {
    const t0 = Date.now();
    window.localStorage.setItem(TOKEN_KEY, nearExpiryToken(t0));
    refreshApi.mockRejectedValueOnce(new Error("backend restarting"));
    refreshApi.mockResolvedValue({ access_token: "fresh.token.value" });

    renderHook(() => useSessionKeepAlive());
    const keepActive = () => window.dispatchEvent(new Event("pointerdown"));
    act(keepActive);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(
        RENEW_AFTER_INTERVALS * KEEPALIVE_CHECK_INTERVAL_MS,
      );
    });
    await act(async () => {
      keepActive();
      await vi.advanceTimersByTimeAsync(KEEPALIVE_RETRY_BASE_MS + KEEPALIVE_CHECK_INTERVAL_MS);
    });

    expect(window.localStorage.getItem(TOKEN_KEY)).toBe("fresh.token.value");
    expect(setState).toHaveBeenCalledWith({ token: "fresh.token.value" });
  });
});
