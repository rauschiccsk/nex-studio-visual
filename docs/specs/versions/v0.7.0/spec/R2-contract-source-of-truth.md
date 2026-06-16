# NEX Studio v0.7.0 — R2: BE↔FE Contract Single-Source-of-Truth

> Design of record. Grounded by `r1-r2-grounding` (every anchor is a real file:line). Class 3 (contract gaps). The
> backend and frontend must agree on the pipeline state machine's enums/sets; today they are hand-mirrored and drift.

## 1. Goal
The cockpit's state machine (stages, actors, statuses, flow types, message kinds, executable Coordinator actions)
is defined in the **backend** (Pydantic schemas, DB CHECK constraints, orchestrator constants) and **re-typed by
hand in the frontend** (`pipeline.ts` unions, `ExchangePanel.tsx` Sets). Any backend change can silently drift the
FE — proven live: `capture_backlog_item` is in the BE executable set but **missing from the FE** (flagged by 3 of
4 auditors; CR-103 synced two other values by hand — exactly the manual step R2 removes). R2 establishes a
**single source of truth** so the FE contract is *generated* + a CI test *fails on drift*.

## 2. Director-approved design decisions
- **D1 — Generated FE types (OpenAPI→TypeScript).** FastAPI exposes the OpenAPI schema at the **default
  `/openapi.json`** (root) — `main.py:169` sets no `openapi_url`, and the routers mount under `/api/v1/*` but the
  schema route itself stays at the app root. Codegen targets the app's ACTUAL route; optionally add
  `openapi_url="/api/v1/openapi.json"` to the `FastAPI(...)` constructor (`main.py:169`) to namespace it for
  consistency with the API. Generate the FE pipeline types from it (`openapi-typescript`); the hand-mirrored
  unions in `pipeline.ts` become *generated*. The generated file is committed; a CI step regenerates and **fails
  if it drifts** from the committed copy.
- **D2 — Literal-typed schemas.** `backend/schemas/pipeline.py` fields that are plain `str` (`flow_type`, `status`,
  `stage`, `kind`, `current_actor`, `author`/`recipient`) become `Literal[...]` matching the DB CHECK constraints
  — so OpenAPI emits real enums for codegen, and the BE validates them too.
- **D3 — `proposed_action` stays `str`.** It is forward-compatible by design (agents emit new actions; validation
  is delegated to the executor gate `_coordinator_directive_executable`, NOT the schema). Documented, not enum'd.
- **D4 — Contract-test for non-response sets.** `_EXECUTABLE_COORDINATOR_ACTIONS` is an internal orchestrator
  constant, NOT a response-model field, so codegen can't cover it. A CI **contract-test** extracts the BE set and
  the FE Set and asserts parity — this is what catches `capture_backlog_item`.
- **D5 — Fix `capture_backlog_item` now, as the proof case.** Add it to the FE Set in this CR; the contract-test
  then guards it forever.
- **D6 — Additive, runtime-safe.** TS unions erase at runtime, so generated types don't change runtime behaviour;
  existing consumers (e.g. `ExchangePanel.tsx:90` `triage_class !== "director_decision"`) keep working.

## 3. Mechanism (grounded)
- **Backend schema hardening** (`backend/schemas/pipeline.py:12-28`): replace `str` with `Literal` on
  `PipelineStateRead` (`flow_type` = `new_version|cr|bug|fast_fix`; `status` = the 5; `current_stage` = the 11;
  `current_actor` = the **6** — coordinator/designer/customer/implementer/auditor/director) and
  `PipelineMessageRead.kind` (the DB-valid kinds). Source the value-lists from the DB CHECK constraints
  (`backend/db/models/pipeline.py:87-106` for the PipelineState enums via the `_STAGES`/`_ACTORS` module
  constants, and `:156` for the message-`kind` CHECK) so they're authoritative. No DB change — schemas stay pure
  pass-through `response_model`s; FastAPI introspects `Literal` → OpenAPI `enum`.
- **Message-kind set is already consistent** (`pipeline.ts:41-50` ↔ `db/models/pipeline.py:156`): an earlier read
  suggested the FE added `directive`/`approval` absent from the DB — **that was wrong**; the DB `kind` CHECK
  already lists `directive`/`approval`. So no reconciliation is needed — the value just becomes *generated* from
  the (already-agreeing) source, removing the hand-mirroring.
- **Codegen pipeline:** add `openapi-typescript` as a FE dev-dep; `npm run codegen` loads the app's OpenAPI schema
  (the `/openapi.json` default, or the namespaced route if D1's `openapi_url` is set) → writes
  `frontend/src/services/api/pipeline.generated.ts`. Re-point `pipeline.ts` to
  re-export the generated enums (keep `pipeline.ts` as the stable import surface so consumers don't churn).
- **CI drift-gate:** a CI step runs `npm run codegen` and `git diff --exit-status` on the generated file — drift
  (someone changed BE without regenerating) **fails the build**.
- **Contract-test (the executable-actions parity)** — a Python test extracts `_EXECUTABLE_COORDINATOR_ACTIONS`
  (`orchestrator.py:2356-2369`) and parses the FE Set from `ExchangePanel.tsx:21-30`, asserting equality. (Or:
  expose the set via a tiny `/api/v1/pipeline/meta` endpoint + generate it too — but the test is the lighter
  durable fix; recommended.) Fix `capture_backlog_item` in the FE Set so the test passes.

## 4. CR breakdown (build order)
- **R2-a (schema):** `str`→`Literal` in `backend/schemas/pipeline.py`; reconcile the message-kind set. + BE schema
  tests (a known-good state round-trips; an out-of-enum value is rejected).
- **R2-b (codegen):** `openapi-typescript` dev-dep + `npm run codegen` + `pipeline.generated.ts` + re-point
  `pipeline.ts`; FE `tsc -b` green.
- **R2-c (CI + contract-test + proof fix):** the codegen drift-gate in CI; the executable-actions parity
  contract-test; add `capture_backlog_item` to the FE Set.
- **Tests:** see §6.

## 5. Seams to preserve (from grounding)
- **TS unions erase at runtime** — generated types are compile-time only; no runtime behaviour change. Validate the
  one live consumer (`ExchangePanel.tsx:90`) still compiles + behaves.
- **`proposed_action` stays `str`** — do NOT enum it (forward-compat; executor-gated). Document why.
- **Manual codegen forgetting** — the CI drift-gate is the backstop; a dev who edits BE without `npm run codegen`
  fails CI (not a silent runtime bug).
- **`/api/v1/openapi.json` is public by default** — note as a (low) info-exposure surface; acceptable for the
  internal trust boundary (per ICC_STANDARDS), revisit if ever exposed to untrusted users.
- **Existing `pipeline.ts` consumers** — keep `pipeline.ts` as the import surface (re-export generated) so
  `ExchangePanel.tsx` et al. don't churn their imports.

## 6. Test points
- UNIT (BE): `PipelineStateRead`/`PipelineMessageRead` accept every DB-valid value and reject an out-of-enum value.
- UNIT (contract): `_EXECUTABLE_COORDINATOR_ACTIONS` == the FE `EXECUTABLE_COORDINATOR_ACTIONS` Set (catches
  `capture_backlog_item`); fails if either drifts.
- CI: `npm run codegen` + `git diff --exit-status` on `pipeline.generated.ts` → drift fails the build.
- INTEGRATION: a real pipeline's serialized response validates against the generated FE types (round-trip).
- REGRESSION (FE): `tsc -b` green after codegen; `ExchangePanel.tsx:90` consumer compiles + the proposal-display
  recognizes the full action set.
