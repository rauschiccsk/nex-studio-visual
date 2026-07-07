# Fix batch 2026-07-07 — graduation §15 + UX observations #3/#4/#6

Director observations collected during the andros/nex-payables A→Z crash-test. Four
independent fixes. Dedo did the diagnosis (live DB + code read); this doc is the spec.
Branch: `v2.0.0-dev`. Self-verify BOTH domains (BE: ruff+pytest; FE: build+lint+test).

---

## Part 1 — Graduation must promote the built version IN PLACE (§15, backend)

### Problem
First PROD deploy of a project graduates it to `v1.0.0` (§3.6). Today the graduation
creates a **brand-new empty `v1.0.0` version row** and leaves the version that was
actually built (with all its pipeline history, epics, backlog, tokens) under its old
number (e.g. `0.1.0`). Result: two versions — one with all the work, one empty
"released" shell. This also broke the metrics page (all tokens sit on the old version,
the empty `v1.0.0` shows nothing) and confused the version list. (Live incident on
nex-payables; the data was hand-collapsed already — this fixes the code so it never
recurs.)

### Root cause
`backend/services/deploy.py`:
- graduation block (~lines 474–484): calls `_ensure_version(db, project.id, FIRST_PROD_VERSION, source=version_number)`.
- `_ensure_version` (~line 555): creates a NEW `Version` row for `v1.0.0` when none exists.

### Fix
Replace the "create a new v1.0.0 row" behaviour with **promote-in-place**:

1. Rename `_ensure_version` → `_graduate_version_in_place(db, version, target)` where
   `version` is the **Version object being deployed** (already in scope at the graduation
   site — used at `version_verified(db, version.id)`).
2. Behaviour:
   - If `version.version_number == target`: no rename (idempotent) — still ensure released
     status below.
   - Else if another version row with `(project_id, target)` already exists AND it is not
     `version`: raise `ValueError` with a clear message (anomaly — a `v1.0.0` already
     exists on a project doing its *first* prod deploy; must not silently collide on the
     `uq_versions_project_id_version_number` UNIQUE). This branch should be unreachable
     given the `project_had_prod_deploy` guard; the raise is defensive + honest.
   - Otherwise: set `version.version_number = target`.
   - In all cases mark it released: `version.status = "released"`,
     `version.release_date = date.today()` (import `date` from `datetime` if not already).
     Rationale: a first-prod graduation IS the release; today graduation left the version
     `planned`. Set the two fields directly (do NOT route through `version_service.release`
     — avoid coupling to its `done`-state precondition; graduation already passed the
     deploy gates: `version_verified` + `is_accepted`).
   - `db.flush()`; return the version.
3. Update the caller (graduation block): pass the `version` object, not `project.id` +
   number. `deployed_version` / `bumped_to` stay `FIRST_PROD_VERSION` (the provisioning +
   deploy-event row are unchanged — they already use `v1.0.0`).
4. Do NOT overwrite `version.name` / `version.description` (keep the built version's own
   identity — §8 anti-destructive).

### Tests
`backend/tests/integration/test_workflow_deploy_matrix.py` (existing graduation coverage):
- Update the graduation assertion: after first prod deploy the built version's
  `version_number` becomes `v1.0.0`, `status == "released"`, `release_date` set — and
  there is **exactly one** version row for the project (no new empty row).
- Add: a child row on the pre-graduation version (e.g. an epic or a pipeline_message) is
  still reachable under the graduated `v1.0.0` (same `version.id` — history preserved).
- Idempotency / non-first-prod: a second prod deploy of a *different* version does not
  re-graduate (existing `project_had_prod_deploy` guard) — keep/confirm coverage.

---

## Part 2 — obs #3: Plán úloh collapse state not preserved across tab navigation (FE)

### Problem
Collapsing an EPIC/FEAT in `Plán úloh` (Riadiace centrum) is forgotten when the user
switches to another tab and comes back — the tree re-expands.

### Root cause
`frontend/src/components/riadiace/PlanUlohRail.tsx`:
- `collapsed` state initializer (~line 288) + the `[versionId]` hydration `useEffect`
  (~lines 299–309). On remount, the initializer runs while `versionId` may still be
  `null` (prop arrives async) → `collapsed` inits empty; the `[versionId]` effect only
  re-hydrates when `versionId` *changes*, so a remount with the same non-null `versionId`
  never re-reads localStorage. Additionally the auto-collapse effect (~339–366) can run
  before hydration and clobber the persisted manual set.

### Fix
Make the collapsed set hydrate reliably from `localStorage` regardless of `versionId`
timing, and never let the auto-collapse effect overwrite a not-yet-hydrated set:
- Re-hydrate `collapsed` (and `expanded` if it shares the bug) from `readCollapsed(versionId)`
  on mount AND whenever `versionId` becomes a valid non-null value (not only on change) —
  e.g. a mount effect + a hydrated-guard, or fold the read into a single effect that runs
  once `versionId` is present.
- Gate the auto-collapse-on-done effect so it runs only after hydration has completed
  (so it augments, never replaces, the restored manual set).
- Preserve the batch-1 smart rules (auto-collapse a FEAT/EPIC on its `done` transition;
  auto-expand the active `in_progress` task's ancestors at render time; manual toggle wins).

### Tests
Extend the existing PlanUlohRail vitest: remount with the same `versionId` restores the
persisted collapsed set; a null-first `versionId` that resolves late still hydrates before
auto-collapse runs.

---

## Part 3 — obs #4: Riadiace-centrum chat editor still underlines Slovak words (FE)

### Problem
The chat composer in Riadiace centrum underlines Slovak words as misspellings.

### Root cause
Batch-1 (#1) added `lang="sk"` to ~11 free-text editors. `lang="sk"` only suppresses
underlines when the browser has a **Slovak dictionary installed** — the Director's does
not, so the browser flags Slovak words anyway. `ConversationComposer.tsx` (and ~7 other
`lang="sk"` editors) set the language but never disable spellcheck; only
`SlovakTextarea.tsx` correctly sets `spellCheck={false}`.

### Fix
Standardise: add `spellCheck={false}` to the SK free-text editors that lack it, matching
`SlovakTextarea`'s pattern (keep `lang="sk"` — harmless/semantic). This guarantees no
underlines for SK **or** EN in a Slovak-primary internal tool (matches the batch-1
component's own choice).
- Primary (reported): `components/riadiace/ConversationComposer.tsx`.
- Also sweep the other `lang="sk"` editors that have NO `spellCheck` and are genuine
  free-text (prose/name/description) fields; apply the same `spellCheck={false}`.
- Do NOT touch code / URL / JSON / password / slug fields.

### Tests
A composer test asserting the textarea renders `spellCheck={false}`; adjust any batch-1
test that asserted `lang="sk"` alone.

---

## Part 4 — obs #6: Cannot edit an already-saved customer (FE)

### Problem
The Zákazníci (Customers) tab can only add + delete customers — there is no way to edit an
existing customer's fields.

### Root cause
`frontend/src/pages/CustomersPage.tsx` renders an add-only form (`createCustomer`) + a list
with name/slug/subdomain/secret badge + a delete button. There is **no edit UI**. The
backend `PATCH /customers/{id}` + `updateCustomer` service already exist and work.

### Fix
Add edit to `CustomersPage.tsx`:
- `editingCustomerId` state.
- An Edit (pencil) button on each customer row → loads that customer's fields into the
  form and switches it to edit mode.
- In edit mode the form submits via `updateCustomer` (PATCH) instead of `createCustomer`;
  on success clear `editingCustomerId` + reload the list. Keep an obvious "cancel edit"
  affordance. Follow the existing form's field set + validation.

### Tests
Extend the CustomersPage vitest: entering edit mode pre-populates the form; submit calls
the update path (not create) + refreshes.

---

## Self-verify (report evidence per §9.5)
- BE: `ruff format --check` + `ruff check` + `pytest -q` (report pass/fail counts; the
  single pre-existing `test_default_claude_config_dir` env baseline failure is expected).
- FE: `tsc -b` + `eslint` + `vitest run` (report counts).
- Migration: none (no schema change).
- Do NOT commit/push — report DONE and wait for Dedo (rebuild + redeploy to v3 + commit).
