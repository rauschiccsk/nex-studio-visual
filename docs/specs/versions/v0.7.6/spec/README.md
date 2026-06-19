# v0.7.6 — "Re-run release audit" gate_g action

> **Status:** spec ready.
> **Owner:** Dedo (design) → nex-implementer (build) → independent verify → CI → deploy.
> **Why:** the v0.7.5 behavioural smoke (CR-1) fires inside `verify_done` — i.e. only when the Auditor produces
> a FRESH `gate_g` `gate_report`. A `gate_g` verdict that already settled BEFORE v0.7.5 (e.g. nex-asistent
> v0.1.0, the pre-smoke ~10-min audit) cannot be re-validated: the offered actions are `verdict` (PASS→release,
> FAIL→build), `ask`, `return` — none re-dispatches the Auditor at `gate_g`, so clicking PASS would release
> WITHOUT the app ever being booted/tested. This CR adds a Director action that re-runs the release audit →
> the Auditor re-audits → the existing CR-1 smoke boots the app + runs its `-m acceptance` suite → a fresh,
> real verdict. (This is the small, harmless piece that was dropped together with the retired dual-build — it
> has nothing to do with worktrees / Build B / comparison.)

---

## CR — `rerun_release_audit` action (gate_g, full-flow only)

### 1. Action registration
- Add `"rerun_release_audit"` to `_ACTIONS` (`backend/services/orchestrator.py:236`).
- It re-dispatches at the SAME stage (does not advance) — model it on `continue_build`. **Like `continue_build`,
  ADD it to `_ADVANCING_ACTIONS` (`:258`).** That set is NOT a stage-advance marker — it is the `apply_action`
  guard (`~:4836`) that REJECTS the action while `status == agent_working` (stale-board / double-click
  protection, CR-NS-018 class). The awaiting_director path is unchanged; only a mid-audit re-POST gets rejected.

### 2. Offer it ONLY at a settled full-flow `gate_g`
- In `determine_available_actions` (`:296`), the `elif stage == "gate_g":` branch (`~:343`) currently adds only
  `"verdict"`. Add `"rerun_release_audit"` THERE, gated to `status == "awaiting_director"` (a settled verdict
  the Director is looking at). `stage == "gate_g"` is already the full-flow guard (`FAST_FIX_STAGE_ORDER`
  `:197` has no `gate_g`) — add a `# fast_fix never at gate_g` comment.

### 3. `apply_action` handler
- New `if action == "rerun_release_audit":` block near the `verdict` handler (`~:5040`), mirroring
  `continue_build` (`~:4938`):
  - assert `state.current_stage == "gate_g"` else `OrchestratorError("rerun_release_audit je platné len vo
    fáze gate_g")`;
  - record a `director→auditor` `directive` message (content from `directive_for_action`, point 4);
  - `state.current_actor = "auditor"`; set status to `agent_working` via the same path `continue_build` uses
    (`_begin_dispatch`, `~:2071`) — stage stays `gate_g`;
  - return `state`. The route `pipeline.py:post_action` (`:264`) sees `agent_working` and schedules the
    background dispatch, threading the directive.

### 4. Directive
- In `directive_for_action` (`~:614`) add a case for `"rerun_release_audit"` returning an explicit Slovak
  brief that ends with the status block, e.g.: *"Audítor, spusti ZNOVA kompletný release audit verzie podľa
  charteru §6 — behaviorálny acceptance suite (appka reálne beží + `-m acceptance` proti bežiacej app) +
  spec-drift. Toto je čerstvé prebehnutie release auditu, nie odpoveď na otázku."*
- When the re-dispatched Auditor produces its `gate_g` `gate_report`, the existing dispatch path runs
  `verify_done` (`:1887`) → `_run_acceptance_smoke` (`:1920`) fires automatically. **No smoke code changes.**

### 5. FE
- `frontend/src/components/cockpit/PipelineActionBar.tsx`: add a **"Znova spustiť release audit"** button,
  rendered ONLY when the offered actions include `rerun_release_audit` (i.e. settled gate_g). SK label + a hint
  like *"Auditor spustí release audit znova — appka sa reálne nabootuje a prebehnú acceptance skúšky; pipeline
  počká na čerstvý verdikt."* Place it near the verdict controls; it posts `action: "rerun_release_audit"`.

### 6. Fast-fix safety
- The action is offered only at `gate_g`, which `fast_fix` never reaches — byte-identical for the fast-fix
  lane. No shared path touched.

## Self-verify (Implementer, before DONE)
1. `poetry run pytest` (FULL backend suite).
2. `ruff format --check . && ruff check .`
3. `cd frontend && npm run build && npm run lint`.
4. New tests: `rerun_release_audit` rejected off-`gate_g`; absent for `fast_fix`; offered only at settled
   `gate_g`; the handler re-dispatches the Auditor (status→agent_working, stage stays gate_g) without advancing.
5. Baseline-verify any pre-existing failure (the known env-only `test_default_claude_config_dir`).

Report exact outputs. STOP + report any spec gap (charter §2.4). Do NOT commit — Dedo commits.
