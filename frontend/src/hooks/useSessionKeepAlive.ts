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
 * store the fresh token under the SAME ``TOKEN_STORAGE_KEY``.
 *
 * A failed renewal is triaged, because the two failures mean opposite things:
 *   - 401 → the token is dead/superseded. The api-client has already bounced to
 *     ``/login``. We stop; asking again cannot help.
 *   - anything else (backend restarting, 502, network blip) → the token is STILL
 *     VALID, usually with hours left. We back off and try again. Treating this
 *     case like a 401 is what used to throw a working user out of the cockpit.
 */

import { useEffect, useRef } from "react";

import { ApiError, TOKEN_STORAGE_KEY } from "@/services/api";
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
/**
 * Backoff after a renewal that did NOT get through (backend restarting, a 502, a network
 * blip). Doubles per consecutive failure up to the cap, so a long outage does not mean a
 * refresh call every 30s — while a brief one costs at most one extra minute.
 */
export const KEEPALIVE_RETRY_BASE_MS = 60_000; // 1 min
export const KEEPALIVE_RETRY_MAX_MS = 10 * 60_000; // 10 min
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
  // A token the server REJECTED (401): dead, superseded or bumped. Renewing it again
  // can only fail, so we stop. Distinct from a renewal that merely did not get through
  // — see ``retryAtRef``.
  const deadTokenRef = useRef<string | null>(null);
  // A request is in the air. Guards against a tick firing mid-flight and double-sending.
  const inFlightRef = useRef(false);
  // Earliest next attempt after a TRANSIENT failure, and how many have failed in a row.
  //
  // Before v4.0.91 there was no such thing: one failed attempt marked the token as
  // "tried" and it was never retried, so a backend restart or a network blip during the
  // renewal window killed a session that was still perfectly valid and had hours left.
  // Five cockpit deploys in one afternoon is exactly that shape of failure — and the
  // symptom is the user being thrown out with no explanation.
  const retryAtRef = useRef(0);
  const failuresRef = useRef(0);

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
      if (token === deadTokenRef.current) return; // server said no — asking again cannot help
      if (inFlightRef.current) return; // a renewal is already on its way

      const timing = decodeTokenTiming(token);
      if (!timing) return;

      const now = Date.now();
      if (now < renewalDueAt(timing)) return; // too early — plenty of life left
      if (now < retryAtRef.current) return; // backing off after a transient failure
      if (now - lastActivityRef.current > KEEPALIVE_ACTIVITY_WINDOW_MS) return; // idle → let it expire

      // Near expiry + recently active → renew SILENTLY.
      inFlightRef.current = true;
      refreshApi()
        .then((res) => {
          failuresRef.current = 0;
          retryAtRef.current = 0;
          // Store the fresh token under the SAME key the api-client reads …
          window.localStorage.setItem(TOKEN_STORAGE_KEY, res.access_token);
          // … and keep the in-memory/persisted auth store in lock-step (WS URLs
          // read store.token; reload rehydrates from it). There is no public
          // token setter, so mirror what login does to the `token` slice.
          useAuthStore.setState({ token: res.access_token });
        })
        .catch((err: unknown) => {
          // The two failures are NOT the same and treating them alike is what used to
          // end the session:
          //
          //   401 — the token is dead, superseded or bumped. The api-client's own
          //         handler has already bounced to /login. Retrying cannot help.
          //   anything else — the backend is restarting, the network hiccuped, a 502
          //         came back from the proxy. The token is STILL VALID and usually has
          //         hours left. Giving up here throws the user out for no reason.
          if (err instanceof ApiError && err.status === 401) {
            deadTokenRef.current = token;
            return;
          }
          failuresRef.current += 1;
          retryAtRef.current =
            Date.now() +
            Math.min(
              KEEPALIVE_RETRY_BASE_MS * 2 ** (failuresRef.current - 1),
              KEEPALIVE_RETRY_MAX_MS,
            );
        })
        .finally(() => {
          inFlightRef.current = false;
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
