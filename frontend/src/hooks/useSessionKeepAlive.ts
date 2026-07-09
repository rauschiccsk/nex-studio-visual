/**
 * useSessionKeepAlive — keep an ACTIVELY-working user logged in (sliding session).
 *
 * The JWT access token is deliberately short-lived (see
 * ``access_token_expire_minutes``); without renewal an active user is bounced
 * to ``/login`` the moment it expires (the api-client redirects on any 401).
 * This hook, mounted once at the app root, silently renews the token BEFORE it
 * expires — but ONLY while the user is actually present.
 *
 * Security posture (the whole point):
 *   - ACTIVE user  → the token is renewed indefinitely → they never see login.
 *   - IDLE / walked-away tab → NO recent activity → we do NOTHING → the token
 *     expires → the existing 401 → ``/login`` flow logs them out. A
 *     backgrounded, untouched tab is NOT kept alive forever.
 *
 * Mechanics: every ``KEEPALIVE_CHECK_INTERVAL_MS`` we check the stored token.
 * Once it enters its renewal window (~``KEEPALIVE_RENEW_FRACTION`` of its
 * actual lifetime, derived from the ``iat``/``exp`` claims) AND the user was
 * active within ``KEEPALIVE_ACTIVITY_WINDOW_MS``, we call ``/auth/refresh`` and
 * store the fresh token under the SAME ``TOKEN_STORAGE_KEY``. A failed refresh
 * falls through to the existing logout path and is NOT retried for the same
 * token (no loop-spam).
 */

import { useEffect, useRef } from "react";

import { TOKEN_STORAGE_KEY } from "@/services/api";
import { refreshApi } from "@/services/api/auth";
import { useAuthStore } from "@/store/authStore";

/** How often we re-evaluate whether the token needs renewing. */
export const KEEPALIVE_CHECK_INTERVAL_MS = 30_000; // 30s
/** "Recently active" = the last user input was within this window. */
export const KEEPALIVE_ACTIVITY_WINDOW_MS = 5 * 60_000; // 5 min
/** Renew once this fraction of the token's lifetime (exp - iat) has elapsed. */
export const KEEPALIVE_RENEW_FRACTION = 0.75;
/**
 * Fallback renewal lead for legacy tokens that carry no ``iat`` claim (issued
 * before this feature shipped) — renew this long before ``exp``.
 */
export const KEEPALIVE_FALLBACK_LEAD_MS = 5 * 60_000; // 5 min
/** Coalesce high-frequency activity events (mousemove/scroll) to at most 1/10s. */
const ACTIVITY_THROTTLE_MS = 10_000;

/** Activity signals that mark the user as present. */
const ACTIVITY_EVENTS = [
  "pointerdown",
  "pointermove",
  "keydown",
  "scroll",
  "touchstart",
] as const;

interface TokenTiming {
  expMs: number;
  iatMs: number | null;
}

/** Decode the ``iat``/``exp`` timing claims from a JWT (no verification). */
function decodeTokenTiming(token: string): TokenTiming | null {
  try {
    const parts = token.split(".");
    if (parts.length !== 3) return null;
    const json = atob(parts[1]!.replace(/-/g, "+").replace(/_/g, "/"));
    const payload = JSON.parse(json) as { exp?: number; iat?: number };
    if (typeof payload.exp !== "number") return null;
    return {
      expMs: payload.exp * 1000,
      iatMs: typeof payload.iat === "number" ? payload.iat * 1000 : null,
    };
  } catch {
    return null;
  }
}

/** The wall-clock time (ms) at which the token enters its renewal window. */
function renewalDueAt({ expMs, iatMs }: TokenTiming): number {
  if (iatMs !== null && expMs > iatMs) {
    return iatMs + (expMs - iatMs) * KEEPALIVE_RENEW_FRACTION;
  }
  return expMs - KEEPALIVE_FALLBACK_LEAD_MS;
}

function readToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_STORAGE_KEY);
}

export function useSessionKeepAlive(): void {
  // Timestamp of the last user activity. 0 = "no activity yet" — we never
  // assume presence on mount, so a cold-start near-expiry token is only
  // renewed once the user actually does something (security).
  const lastActivityRef = useRef(0);
  // Coalesce burst events.
  const activityThrottleRef = useRef(0);
  // The token we already attempted to renew — prevents loop-spamming refresh
  // when a renewal fails (the token is unchanged → we skip it until it changes).
  const attemptedTokenRef = useRef<string | null>(null);

  useEffect(() => {
    const markActive = () => {
      const now = Date.now();
      if (now - activityThrottleRef.current < ACTIVITY_THROTTLE_MS) return;
      activityThrottleRef.current = now;
      lastActivityRef.current = now;
    };
    const onVisibility = () => {
      if (document.visibilityState === "visible") markActive();
    };

    ACTIVITY_EVENTS.forEach((evt) =>
      window.addEventListener(evt, markActive, { passive: true }),
    );
    document.addEventListener("visibilitychange", onVisibility);

    const tick = () => {
      const token = readToken();
      if (!token) return; // not logged in → nothing to keep alive
      // Already tried (and failed) to renew this exact token → do not loop-spam.
      if (token === attemptedTokenRef.current) return;

      const timing = decodeTokenTiming(token);
      if (!timing) return;

      const now = Date.now();
      if (now < renewalDueAt(timing)) return; // too early — plenty of life left
      if (now - lastActivityRef.current > KEEPALIVE_ACTIVITY_WINDOW_MS) return; // idle → let it expire

      // Near expiry + recently active → renew SILENTLY. Mark before awaiting so
      // a tick firing mid-flight cannot double-fire the request.
      attemptedTokenRef.current = token;
      refreshApi()
        .then((res) => {
          // Store the fresh token under the SAME key the api-client reads …
          window.localStorage.setItem(TOKEN_STORAGE_KEY, res.access_token);
          // … and keep the in-memory/persisted auth store in lock-step (WS URLs
          // read store.token; reload rehydrates from it). There is no public
          // token setter, so mirror what login does to the `token` slice.
          useAuthStore.setState({ token: res.access_token });
        })
        .catch(() => {
          // Renew failed — already-expired/bumped (the api-client's 401 handler
          // has already bounced to /login) or a transient network error. Fall
          // through to the existing logout flow; the attemptedTokenRef guard
          // keeps us from retrying THIS token.
        });
    };

    const intervalId = window.setInterval(tick, KEEPALIVE_CHECK_INTERVAL_MS);

    return () => {
      ACTIVITY_EVENTS.forEach((evt) =>
        window.removeEventListener(evt, markActive),
      );
      document.removeEventListener("visibilitychange", onVisibility);
      window.clearInterval(intervalId);
    };
  }, []);
}
