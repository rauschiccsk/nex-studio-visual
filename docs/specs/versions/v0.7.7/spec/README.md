# v0.7.7 — App-starts smoke: readiness poll treats any server HTTP response as "up"

> **Status:** spec ready.
> **Owner:** Dedo (design) → nex-implementer (build) → independent verify → CI → deploy.
> **Why (LIVE-confirmed bug):** the v0.7.5 CR-1 readiness poll `_await_acceptance_app_ready`
> (`backend/services/orchestrator.py`) requires a **2xx from `/health`**. On the nex-asistent v0.1.0 re-audit
> (2026-06-19), the app serves health at the **versioned** `/api/v1/health`, so the poll hit `/health` → **404**
> → looped to the 120s budget → `App-starts smoke FAIL: App not ready within 120s: HTTP Error 404` → a **FALSE
> FAIL on the HARD gate**, even though the app was up (a 404 proves the server is responding) and its acceptance
> suite passed. A readiness probe's job is "is the server accepting + handling HTTP requests", NOT "does this
> exact path return 2xx". Fix the probe to be path-agnostic.

---

## CR — readiness = the server responded (status < 500), not 2xx-from-/health

### 1. New readiness semantics in `_await_acceptance_app_ready`
The app is **READY** as soon as the probe gets an HTTP response with **status `< 500`** (2xx / 3xx / 4xx — the
server is listening and routing; a 404 just means our probe path isn't a declared route, which is irrelevant —
the acceptance suite uses the app's real routes). Keep polling (NOT ready) on:
- connection-level failure — connection refused / reset / DNS / **timeout** (server not accepting yet), OR
- **status `>= 500`** (server up but signalling unavailable/starting — give it more time).

Implementation against `urllib`:
- `urllib.error.HTTPError` carries `.code` → if `e.code < 500` → **READY** (return ok); if `e.code >= 500` →
  keep polling (record as last status).
- a 2xx/3xx `urlopen` success → **READY**.
- `urllib.error.URLError` (no HTTP response — connection refused etc.) / socket timeout → keep polling.
- On budget exhaustion (`ACCEPTANCE_SMOKE_READY_TIMEOUT`) → `(False, "App not ready within <N>s: <last status/err>")`.

The probe path no longer matters — keep probing the existing base URL (e.g. `/health`); a 404 there now means
"up". (Do NOT special-case `/api/v1/health` — the fix is to be path-agnostic, not to hardcode a second path.)

### 2. Tests (update `backend/tests/test_acceptance_smoke.py`)
- **404 → READY** (the live nex-asistent case): a probe returning `HTTPError 404` makes the poll proceed
  immediately (no timeout), and the smoke continues to the acceptance run.
- **200 → READY** (unchanged happy path).
- **connection-refused → keep polling → timeout** → `(False, "App not ready within …")` (the genuine
  not-up case still fails clearly).
- **500/503 → keep polling** (server up but starting → not immediately ready; times out if it never recovers).

### 3. Scope / safety
- ONLY `_await_acceptance_app_ready` changes (+ its tests). No change to the gate_g hook, the acceptance run,
  teardown, the graceful-skip, or any shared path. `claude_agent.py` untouched. Fast-fix unaffected
  (gate_g-only). No FE change.

## Self-verify (Implementer, before DONE)
1. `poetry run pytest` (FULL backend suite) — baseline-verify the known env-only `test_default_claude_config_dir`.
2. `ruff format --check . && ruff check .`.
3. The new/updated readiness tests (404→ready, 200→ready, conn-refused→timeout, 5xx→keep-polling) pass.

Report exact outputs. STOP + report any spec gap (charter §2.4). Do NOT commit — Dedo commits + verifies.
