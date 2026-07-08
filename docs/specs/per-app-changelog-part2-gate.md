# obs-2 Part B — Part 2: hard release gate for the Aktualizácie tab + endpoint

Part 1 (NEX Studio auto-writes RELEASE_NOTES.md) is shipped. Part 2 makes the app's OWN Aktualizácie
actually SHIP — the scaffolded FE tab + the charter-mandated BE endpoint are today agent-dependent and
were BOTH dropped in the flagship app (nex-payables: no UpdatesPage, no `/api/v1/release-notes`). Add a
deterministic release check NEX Studio runs ITSELF so a build cannot reach `done`/deploy without a
working per-app changelog. Branch `v2.0.0-dev`. Self-verify: FULL `.venv/bin/python -m pytest -q` from
root + ruff.

Grounding:
- `_run_release_smoke` (orchestrator.py ~4020-4055) boots the app's ephemeral `-p <slug>-smoke` stack and
  probes backend readiness (`_await_acceptance_app_ready`) + frontend serving (`_await_http_ready` on the
  FE service over the isolated project network). Returns `(ok: bool, detail: str)`; a False is a release
  blocker surfaced via `smoke_block` into kontrola (~1388-1473).
- The serving contract (Part 1 + reference impl): `GET /api/v1/release-notes` → 200 + JSON list
  `[{version, released_at, markdown}, …]`, reading the image-baked `docs/specs/versions/v*/RELEASE_NOTES.md`.

## Part 2 checks — both NEX-Studio-owned, both release blockers

### 2a — Behavioural (BE endpoint actually serves)
In `_run_release_smoke`, AFTER the backend is ready, add a probe: `GET /api/v1/release-notes` on the
booted backend (same base/port the readiness poll uses). Assert HTTP 200 AND the body is a JSON list that
INCLUDES the completing version (match on the version number, `v`-normalized, that Part 1 just wrote). On
failure return `(False, "Aktualizácie chýba: /api/v1/release-notes … <status/detail>")` — a release
blocker. (A client-side `/updates` route is an SPA fallback that always 200s index.html, so do NOT probe
it behaviourally — the FE is covered statically in 2b.)

### 2b — Static (FE tab wired in the generated app's source)
Add a source-tree check on `proj_root/frontend/src` (the checkout the smoke was built from) that the app
still has the scaffolded Aktualizácie UI — ALL of:
- an Updates page (e.g. `frontend/src/pages/UpdatesPage.tsx` exists),
- the `/updates` route wired (grep the router for the `updates` path),
- an "Aktualizácie" nav entry (grep the sidebar).
Missing any → a release blocker `(False, "Aktualizácie chýba vo frontende: <which piece>")`. Put this in a
small pure helper (`_check_aktualizacie_frontend(proj_root) -> Optional[str]` returning the missing-piece
message or None) so it is unit-testable without booting.

### 2c — Honest surfacing
Both failures must appear as an explicit blocker line in the kontrola/release report the manager sees
("Aktualizácie chýba …"), routed through the SAME `smoke_block` path — never a silent pass. A build must
NOT reach `done` while either fails.

## Tests (RED→GREEN)
- 2a: mock the booted backend — 200 + list including the version → pass; 404/500 or a list missing the
  version → blocker.
- 2b: `_check_aktualizacie_frontend` returns None when page+route+nav all present; returns the specific
  missing-piece message when any is absent (mirror nex-payables' state: page + route + nav all missing).
- Integration: a smoke run with a compliant app passes; one missing the endpoint or the FE tab is blocked.
- Full `pytest` from root + ruff.

## Out of scope
- Backfilling nex-payables (Dedo; its next version picks up the gate).
- Auto-injecting the FE/BE if missing — the gate BLOCKS (agent must add it); auto-injection into
  agent-built code is fragile, deferred.
