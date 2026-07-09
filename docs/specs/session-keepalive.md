# Session keep-alive — don't bounce an ACTIVELY-working user to login

Director-reported: NEX Studio bounced him to the login screen mid-work. Root cause (verified): the JWT
access token has a fixed 8h lifetime (`access_token_expire_minutes`=480), there is NO refresh/renewal,
and the frontend api client bounces to `/login` on any 401 (auth/api.ts). So a session running past the
token lifetime is kicked to login regardless of activity. (NOT caused by backend redeploys — SECRET_KEY
is a fixed .env value, tokens survive redeploys; confirmed.) Branch `v2.0.0-dev`. Self-verify: FULL
`.venv/bin/python -m pytest -q` from root + ruff; FE build+lint+test.

Goal: while the user is ACTIVELY working, silently renew the session so they never hit the login screen;
only genuine INACTIVITY lets the session expire (correct + secure). Keep the token short-lived (security);
do NOT just lengthen the lifetime.

## Part 1 — Backend: a renew endpoint
Add `POST /api/v1/auth/refresh` (mirror the auth router; reuse `create_access_token`/the login token
path in `backend/services/auth.py`): given a VALID, non-expired bearer token (same auth dependency the
protected routes use), issue a NEW access token for the same user + same `token_version`, with a fresh
`access_token_expire_minutes` expiry. Return the same shape as login (`access_token`, `expires_in`, …).
- If the token is already EXPIRED / invalid → 401 (the user must re-login; do NOT renew a dead session).
- Respects `token_version` (a bumped version — e.g. after a password change — invalidates the session as
  today; a renew of a superseded version 401s).
- No refresh-token / cookie needed — this renews a still-valid session (sliding expiration). Keep it simple.

## Part 2 — Frontend: silent renewal while active
In the api client / a small `useSessionKeepAlive` hook mounted at the app root: while the tab is open AND
the user has been active recently, silently renew the token BEFORE it expires, so an active session never
reaches a 401.
- Track last user activity (e.g. pointer/key/visibility events, throttled).
- On a timer, if the token is within a renewal window of expiry (e.g. renew at ~75% of lifetime / a few
  min before `expires_in`) AND there was recent activity → call `/auth/refresh`, store the new token
  (same TOKEN_STORAGE_KEY), reschedule. If NO recent activity → do nothing (let it expire → the existing
  401 → login flow handles idle logout, unchanged).
- If a renew fails (already expired / network) → fall through to the existing 401 → `/login?next=…`
  behavior (no change). Never loop-spam refresh.
- The renewal must be SILENT (no UI flicker, no redirect) for the active user.

Security posture (state it in code comments): active user → stays logged in indefinitely (renewed);
walk-away / idle past the lifetime → session expires (no infinite idle session). Don't renew a
backgrounded+idle tab forever.

## Tests (RED→GREEN)
- Backend: `/auth/refresh` with a valid token → 200 + a new token whose exp is later than the old one +
  same user/token_version; with an EXPIRED token → 401; with a bumped token_version → 401. Full pytest.
- Frontend: the keep-alive hook calls refresh when near-expiry + recently-active (mock timers/token), and
  does NOT call refresh when idle; a failed refresh falls through to the existing logout path. FE build+lint+test.

## Out of scope
- Refresh-token / httpOnly-cookie rotation (a bigger auth redesign) — the sliding-renew of a valid session
  covers the reported problem. Note as a future option, don't build now.
