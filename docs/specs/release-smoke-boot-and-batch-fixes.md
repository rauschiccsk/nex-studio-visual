# Batch: release-smoke boot fix + honest FAIL + 2 deferred UX fixes

Director-approved 2026-07-10 after nex-payables 1.1.0 Verifikácia FAILed. Root cause investigated by Dedo.
Four fixes (A/B backend = the release blocker; C/D1 = deferred UX). Dedo does D2 (a targeted PROD DB update)
+ the deploy. Branch `v2.0.0-dev`. Self-verify: FULL `.venv/bin/python -m pytest -q` from REPO ROOT + ruff
(backend); FE build+lint+test (C). Pre-existing full-suite baseline: ~13 order-dependent isolation errors +
1 HOME-dependent `test_default_claude_config_dir` FAIL are NOT yours — introduce no NEW failures.

## A — release smoke boots with an INCOMPLETE `.env` → `docker compose up` fails (THE BLOCKER)

Symptom (v3 PROD, nex-payables 1.1.0): the Verifikácia release-smoke boot leg failed —
`up exit 1: error while interpolating services.db.environment.POSTGRES_PASSWORD`. Root cause CONFIRMED:
- the generated app's `docker-compose.yml` (line 27) requires it: `POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD in .env …}` (fail-fast guard);
- the app's `.env.example` HAS a consistent dev default (`POSTGRES_PASSWORD=nexpay_dev`, `DATABASE_URL=…nexpay_dev@db…`);
- but the app's LIVE `.env` (what the smoke booted with) is INCOMPLETE — it does NOT contain `POSTGRES_PASSWORD` → compose interpolation fails → the app never boots → verification can't pass.

The project already ships `scripts/ci_render_dotenv.py` (seeded by `_seed_ci_render_helper`,
create_project_postscaffold.py ~464) which renders a complete `.env` from `.env.example` (CI's `migrate` job
uses it). **The release smoke doesn't do the same** — it boots against the possibly-incomplete live `.env`.

**Fix:** the smoke boot leg must give `docker compose up` a COMPLETE, `.env.example`-derived env, WITHOUT
clobbering the app's live `.env` (which may hold real secrets). Render a throwaway env from `.env.example`
(reuse the seeded `ci_render_dotenv.py` logic, or an equivalent renderer) and boot with it via
`docker compose --env-file <rendered>` — so `POSTGRES_PASSWORD=nexpay_dev` (and the rest) are always present.
Locations: the smoke boot / `_SmokeStack` setup and `_run_release_acceptance` (orchestrator.py ~4197–4490;
the boot-FAIL surfaces at ~4477 `up exit {up_rc}`). Add a test: a generated-app fixture whose live `.env`
lacks `POSTGRES_PASSWORD` but whose `.env.example` has it → the smoke boot renders + succeeds (no
interpolation error). Keep it archetype-correct (web app with a backend service).

## B — a boot-FAIL must settle a HONEST Verifikácia FAIL, not "verdict couldn't be parsed" (oracle gap)

Because the app didn't boot, the pipeline ended `verifikacia / blocked`, next_action *"Verdikt Auditora sa
nepodarilo spracovať ani po opakovaných pokusoch"* — the Auditor timed out + its verdict didn't parse, so
the manager sees a confusing "verdict couldn't be parsed / blocked" instead of the truth: **the app didn't
boot.** A boot-FAIL is a decisive product FAIL and must NOT depend on the Auditor emitting a parseable verdict.

**Fix:** when the release-acceptance/boot leg returns a boot-FAIL (`(False, "up exit …")`), settle a clean
**Verifikácia FAIL verdict carrying the boot reason** — deterministically, independent of the Auditor's
verdict parseability. The manager (and the fix↔re-verify loop) must see *"Appka sa nespustila: <reason>"*,
not "verdikt sa nepodarilo spracovať". Trace `_run_verifikacia_round` → `_settle_verifikacia_verdict`
(orchestrator.py ~6041) and the boot-FAIL path (~4458 "the caller settles on the boot FAIL" — verify it
actually fires ahead of the Auditor-verdict-parse block). Test: a boot-FAIL result → a recorded
`kind=verdict` FAIL message with the boot reason + state settled FAIL (not blocked-on-parse).

## C — SchvalitBar shows the wrong copy at a COMPLETED build

At `programovanie / awaiting_manazer` with ALL tasks done, the cockpit offers `schvalit` (correct — it
advances programovanie → verifikacia). But `SchvalitBar.tsx` is hardcoded for the Návrh gate: label
**"Schváliť plán"** (line ~95) + consequence *"Schválením potvrdíš návrh a plán; projekt sa posunie do
stavby (Programovanie)"* (line ~58) — nonsense at a finished build. (Regression from the Bug-1 timeout fix
that made `schvalit` appear here.)

**Fix:** make SchvalitBar copy context-aware off the board's current stage:
- `current_stage === "navrh"` → keep today's copy ("Schváliť plán" / "…posunie do stavby (Programovanie)").
- `current_stage === "programovanie"` (build done, schvalit offered) → label **"Prejsť na overenie"**,
  consequence *"Potvrdíš dokončenú stavbu; projekt sa posunie na Verifikáciu (overenie Auditorom)."*
The action stays `schvalit`; only the label + consequence + (optionally) the primary-button text change.
Update/extend the SchvalitBar test for both stages. `frontend/src/components/riadiace/SchvalitBar.tsx`.

## D1 — stop the AI embedding "EPIC N —" in epic/feat TITLES

The plan-gen skeleton let the AI write per-version "EPIC 1 —", "EPIC 2 —" prefixes INTO epic titles (v1.1.0),
so the cockpit shows a confusing double number ("8. EPIC 1 — …") — the DB numbers are already continuous +
correct. The plan prompt example (orchestrator.py ~1573 `{"epics":[{"title":"Foundation",…}]}`) already shows
clean titles but doesn't FORBID numbering.

**Fix:** in the skeleton/plan-gen prompt (orchestrator.py ~1573/1603, the `epics[].title` instruction), add an
explicit rule: **the title is the NAME only — do NOT prefix it with "EPIC N", a number, or any ordinal; the
system numbers epics/feats itself.** Same for feat titles. Keep it short + in the prompt's existing Slovak.

## Verify + hand-off
- Backend: full `pytest` from root + `ruff format --check .` + `ruff check .`.
- FE (C): build + lint + test.
- Do NOT deploy, do NOT commit the app version, do NOT touch the v3 PROD DB (Dedo does D2 = strip the
  "EPIC N — " prefix from nex-payables v1.1.0's 6 epic titles + deploys via `scripts/deploy-v3.sh`). Leave
  changes in the working tree; report evidence (files + test counts).
