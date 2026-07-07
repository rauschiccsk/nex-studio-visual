# Follow-up — corrections from adversarial verification of batch-2

Adversarial verification (4 verifiers) of the batch-2 fixes surfaced one INEFFECTIVE fix
(obs #3) + three small gaps. This spec corrects them. Branch `v2.0.0-dev`. The rest of the
batch (graduation §15 logic, obs #4 chat, obs #6 edit) verified correct — leave it.

---

## Correction 1 — obs #3 collapse: the prior fix is INEFFECTIVE, real fix needed

### Why the prior fix missed
The prior fix hardened a "null-first async versionId" window that does NOT occur in Riadiace
centrum (`versionId` comes from the zustand `activeContextStore`, rehydrated synchronously →
non-null from render 1). The verifier confirmed the new tests pass against the UNFIXED
component (non-discriminating). Keep the `hydratedRef` gating (it is correct + harmless), but
it does not fix the observed bug.

### Real root cause (Dedo re-diagnosis, PlanUlohRail.tsx)
`seenStatusRef` (line 296) is RESET to an empty Map on every mount (hydration effect, line 309).
The auto-collapse-on-done effect (lines 350-379) then treats EVERY already-`done` EPIC/FEAT as a
fresh `* → done` transition on the first plan-fetch after a mount (because `seen.get(id)` is
`undefined ≠ "done"`), so it RE-COLLAPSES them and PERSISTS that — clobbering the Manažér's
manual EXPAND. Repro: a fully-done version (e.g. nex-payables), the Manažér expands a done
FEAT/EPIC, switches tab (unmount) and returns (remount) → the node is re-collapsed. The
line-348 comment "a subsequent manual EXPAND is never re-collapsed" holds only WITHIN one mount
(after the first pass seeds `seen`), NOT across a remount.

### Fix
Separate the two behaviours the effect currently conflates via the empty-`seen` trick:
1. **done-on-load default** — done nodes start collapsed. Must apply only ONCE per version
   (the first time that version is ever seen), NOT on every mount.
2. **runtime `→done` auto-collapse** — a node observed transitioning to done DURING the session.

Implementation in the auto-collapse effect (keep the `hydratedRef` guard):
- Detect the FIRST pass after a mount (e.g. `seen.size === 0`, since `seenStatusRef` resets on
  mount). On that first pass:
  - **Seed `seen` with ALL current node statuses** from the plan (so already-done nodes are
    recorded as seen and are NOT treated as fresh transitions on any later pass this mount).
  - Determine "first ever for this version" = the collapsed localStorage key is ABSENT. Add a
    helper (e.g. `collapsedKeyExists(versionId)` doing `localStorage.getItem(<key>) !== null`)
    — distinct from `readCollapsed`, which returns an empty Set for BOTH absent and empty `[]`.
  - **Only if first-ever**: add all currently-`done` EPIC/FEAT to `collapsed` + persist (the
    done-on-load default, applied once). If the key already exists, do NOT collapse already-done
    nodes — respect the persisted set verbatim (this is what preserves the manual expand across
    remounts).
- On SUBSEQUENT passes (`seen` non-empty): keep the existing genuine-transition logic
  (`status === "done" && seen.get(id) !== "done"` → collapse + persist).

Net effect: first-ever visit → done nodes collapsed; manual expand → removed from `collapsed` +
persisted; tab switch + return → key exists → no re-collapse → the expand SURVIVES; active build
→ a node reaching done still auto-collapses (genuine transition). Batch-1 req 3/5 + active-
ancestor force-expand unchanged.

### Test — MUST be discriminating (verifier flagged the prior tests as non-discriminating)
Add a vitest that reproduces the REAL scenario and FAILS against the current code:
- A fully-`done` plan + a versionId, with the collapsed key ALREADY present in localStorage and
  NOT containing a specific done FEAT (i.e. the Manažér previously expanded it). Mount fresh
  (fresh component instance = a remount), let the plan resolve, and assert that FEAT stays
  EXPANDED (its `children` render / it is NOT in the effective collapsed set) and that
  localStorage was not re-written to include it.
- Confirm RED: the test FAILS against the pre-fix component and PASSES with the fix. State this
  in the report (§9.5). Keep the prior 2 tests only if still meaningful; replace them if not.

---

## Correction 2 — Part 1 graduation: add the missing failed-deploy + idempotency tests

Graduation logic verified correct, but the KEY behaviour (mutation only on `first_prod and ok`)
is untested — no test exercises a FAILED deploy (the `fake_deploy_runner` fixture always returns
`(True, ...)`). Add to `backend/tests/integration/test_workflow_deploy_matrix.py` (or the deploy
test module):
- **Failed first-prod deploy**: a runner returning `ok=False` → the version is NOT graduated
  (still under its original number, status unchanged), NO graduated `v1.0.0` row, and the version
  stays resolvable for retry. (Inject a failing runner variant.)
- **Idempotency**: deploying an already-`v1.0.0` version does not error or double-graduate.
Also soften the `_graduate_version_in_place` docstring: the UNIQUE-collision branch is NOT
strictly "unreachable" — a manually-created free-form `v1.0.0` row makes it reachable while
`project_had_prod_deploy` still reports no prod history. State it is a defensive guard.

---

## Correction 3 — Part 3 spellcheck: cover the one missed free-text field

`frontend/src/pages/KnowledgeBasePage.tsx` (~line 564) — the "Názov dokumentu" create-document
TITLE input (placeholder "Popisný názov…") is genuine Slovak free-text but has NEITHER `lang="sk"`
NOR `spellCheck={false}` (its exact twin `CredentialsPage` createTitle WAS covered). Add both, to
match. (The search box at ~385 is fine to leave — noted, not required.)

---

## Correction 4 — Part 4 edit-customer: backend secret-preservation test

The load-bearing invariant (a blank/`null` secret on update must NOT wipe the stored secret) has
no backend test. Add one asserting `customer_service.update(...)` with `secret=None` leaves the
stored credential intact (only the `if data.secret is not None:` write is skipped). Credentials
never rendered/logged (§4).

---

## Self-verify (§9.5)
- BE: `ruff format --check` + `ruff check` + `pytest -q` (report counts; the single
  `test_default_claude_config_dir` env baseline fail is expected).
- FE: `tsc -b` + `eslint` + `vitest run`. For Correction 1, explicitly report the RED→GREEN
  evidence (test fails on pre-fix, passes on fix).
- Do NOT commit/push — report DONE + wait for Dedo (rebuild + redeploy to v3).
