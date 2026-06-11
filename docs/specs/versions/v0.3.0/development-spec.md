# NEX Studio v0.3.0 — Phase 1: Cockpit Operator + Robustness

> Development spec (waterfall). Built by **Dedo (design) + nex-implementer** — NEX Studio
> develops cross-project, NOT through its own cockpit pipeline.
> Grounded by the `map-cockpit-for-phase1` exploration (2026-06-10, 6 readers): every
> change below cites a real file/line extension point, not an assumption.
> Director approved scope + the two design decisions 2026-06-10.

---

## 1. Context & goal

Dogfooding the cockpit on NEX Inbox (v0.2.0 / v1.0.1 builds) surfaced two systemic problems:

1. **Recurring cockpit fragility** — stale agent sessions across versions, brittle agent-output
   parsing (task_type omission ×2, unescaped quote), merged-task verify dead-end, mislabeled
   action buttons (no-op "Schváliť podľa Návrhára" in build). Each one Dedo had to fix manually
   (clear sessions / move baseline in DB / tell the Director which button).
2. **Coordinator comments, doesn't resolve.** The Coordinator was added to BE the build operator
   (the Dedo-during-build role) but only analyzes + relays. The Director plays telephone between
   the Coordinator (advice) and Dedo (execution).

**Goal of Phase 1:** the build runs with the **Coordinator as active operator** — it triages a
problem, proposes a concrete decision, the Director approves, the Coordinator **executes**.
Deterministic issues are auto-handled by the cockpit (no human). Dedo becomes escalation-only.
Metrics instrumentation starts capturing data (the page is a later phase).

### Director-approved decisions (2026-06-10)
- **D1 — Coordinator autonomy (conservative):** the Coordinator always **proposes a concrete
  decision → Director approves → Coordinator executes**. NOT autonomous. Only the mechanical
  layer-1 issues run automatically. (Matches "agents propose, Director disposes".)
- **D2 — Regate sessions:** new-version kickoff **resets** all agent sessions; a re-gate
  (verdict FAIL → rewind) **preserves** them (re-gate = refinement, not a fresh start).

### Layered resolution model
1. **Cockpit auto** (mechanical, no human): session reset on new version, merged-commit
   recognition, parse-retry names the field.
2. **Coordinator proposes → Director approves → Coordinator executes** (judgment): route to
   Designer, reset failed task, move baseline, escalate.
3. **Dedo** — only genuine NEX Studio bugs the Coordinator escalates.

---

## 2. Workstreams

Sequencing: **WS-B + WS-C1 first** (cheapest, stop today's hiccups) → **WS-A (E7)** builds on
them → **WS-D** in parallel (independent).

### WS-A — Coordinator as operator (E7) — the axis

**Problem (grounded):** `apply_coordinator_recommendation` (orchestrator.py:1914 + dispatch_directive:559)
relays the Coordinator's report as ADVISORY TEXT into the next agent brief — it does NOT mutate
state. On a build-blocked/failed task it is a **no-op** (the failed task stays failed; only the
`return` action resets it, :1868). The Coordinator charter (nex-inbox/.claude/agents/coordinator
+ templates/coordinator-charter.md) defines a JUDGE/RELAY role with no triage and no execution
authority.

**Changes:**
- **A1 — Structured Coordinator directives.** The Coordinator's recommendation carries a
  STRUCTURED payload (not just prose): `{triage_class, proposed_action, target, params, confidence}`.
  Extend the status block / pipeline_message payload + parsing. `triage_class ∈
  {spec_problem, programmer_guidance, nex_studio_bug, director_decision}`.
- **A2 — Triage framework (charter).** Add to the Coordinator charter (+ template): a triage
  decision tree — classify each surfaced problem → **spec_problem** (propose Designer re-work of
  §X.Y), **programmer_guidance** (propose Implementer instruction / build-mechanics fix),
  **nex_studio_bug** (escalate to Dedo via inbox), **director_decision** (relay). READ permission
  for docs/specs + schemas. Bounds: ambiguity → director_decision; N≥3 re-routes on one issue →
  auto-escalate; <5 min triage SLA. Mandatory **buildable/bootable proof** check preserved
  (no P-2 false-PASS).
- **A3 — Executable actions (orchestrator).** New actions that EXECUTE an approved structured
  directive (Director approves the Coordinator's proposal → the action runs the state mutation,
  not just re-dispatches text):
  - `coordinator_reset_task` — reset a specific failed task to todo (fresh ≤5 budget).
  - `coordinator_move_baseline` — set `task.baseline_sha` (recognizes a merged/prior commit; this
    is exactly the manual DB fix Dedo did for v1.0.1 task #3). Shares WS-B2.
  - `coordinator_route_to_designer` — send the current task to a Designer Q&A (spec problem).
  - `coordinator_clear_session` — fresh session for a named role.
  - `coordinator_escalate_dedo` — write a structured Dedo-inbox item (parallel, non-blocking).
  - Wire `apply_coordinator_recommendation` (or a new `approve_coordinator_decision`) so that on
    build it dispatches the structured directive's executor, then re-dispatches — fixing the no-op.

**Seams to preserve:** `_build_open_findings` stays the deterministic gate (Coordinator's proposal
+ Director approval mutates it, never silent override); hub-and-spoke (Director↔Coordinator only);
the Coordinator never advances a pipeline stage; orchestrator owns agent dispatch order.

**Acceptance:** a build blocker like v1.0.1's merged-commit / failed-task → the Coordinator
triages, proposes "move baseline" (or "reset task") with the concrete params → Director gives ONE
approval → the Coordinator executes it → build continues. **Zero manual DB by Dedo.**

### WS-B — Auto-robustness (layer 1, no human)

- **B1 — Session reset on new-version kickoff.** In `apply_action` "start" (:1740-1767), before
  `_begin_dispatch`, `delete(OrchestratorSession).where(project_slug == ...)` so every agent starts
  fresh (no cross-version stale context). Per **D2**: regate (verdict FAIL, :1979-1987) **preserves**
  sessions. *Acceptance:* new-version kickoff → all 5 agents fresh; regate → sessions kept.
- **B2 — Accept merged / advance baseline.** `verify_mechanical` (:872-873) rejects a commit that
  predates the task baseline → merged tasks dead-end (today's incident). Add a first-class path to
  recognize a Director/Coordinator-approved merged or prior commit by moving `task.baseline_sha`
  to its parent (the manual fix Dedo did). Exposed via `coordinator_move_baseline` (WS-A3) and,
  where deterministic (the new task's baseline == the previous merged commit), auto-recognized.
  *Acceptance:* a status+transitions merged commit no longer dead-ends; no manual baseline edit.
- **B3 — Parse-retry names the field.** `pipeline_status.py:183` returns a stringified Pydantic
  error array. Add `_format_validation_errors(exc)` → `"tasks[2].task_type: Field required"` and
  inject it into the re-prompt (orchestrator.py:781). *Acceptance:* a `task_type` omission → the
  retry names the exact field+index → the agent fixes on the first retry (no 3-round loop).

### WS-C — Backend-authoritative actions + "kto je na rade" (class D)

- **C1 — `available_actions` (single source of truth).** Today the FE hardcodes which buttons
  show per (stage, status) in PipelineActionBar.tsx → drift → "Schváliť podľa Návrhára" renders in
  a build-blocked state where it is a no-op. Add backend `determine_available_actions(state) →
  set[str]` (extract the `apply_action` guards) + expose on the board/endpoint. The FE renders ONLY
  the actions the backend says are valid. *Acceptance:* a build-blocked state never offers a
  Designer-gate no-op button; the Programmer-question state offers "Odpoveď".
- **C2 — "Kto je na rade" board + honest labels.** Show current actor + decision-type
  (answer / approve / return) + Coordinator-relay transparency (Director → Coordinator → worker).
  Action labels reflect the real actor/stage, not a generic Designer-gate label.

### WS-D — Metrics instrumentation (E5 foundation — start now)

Today **nothing** is captured (invoke_claude discards usage). The metrics PAGE is Phase 3, but the
DATA capture must start now (history cannot be backfilled).
- `invoke_claude` (claude_agent.py): parse usage from `claude -p` output → return
  `(text, UsageMetadata{input_tokens, output_tokens, model})`.
- `invoke_agent` / `invoke_agent_with_parse_retry`: wrap with `perf_counter`; accumulate tokens +
  duration across parse-retries and per task; write to `PipelineMessage.payload.usage` /
  `.timing`.
- `PipelineState.awaiting_director_since` (new) → compute Director-wait time.
- Aggregation helper: sum tokens + time per EPIC / FEAT / TASK.
- **Settings:** add developer hourly rate + API token price (IN / OUT separately) for the later
  human-baseline comparison.
- *Acceptance:* every new dispatch records tokens + time attributable to a task; Director-wait
  computable. Historical pipelines show no data (documented — starts fresh).

### WS-E — Internal-turn parse-failure observability (Class F) — follow-up, added 2026-06-11 post-CR-036 review

**Problem (grounded, verified by the CR-036 adversarial review — all 5 sites PRE-EXISTING, untouched by
WS-D):** when an INTERNAL Coordinator/verify-judge turn (not a build worker) exhausts its parse-retries,
the orchestrator DISCARDS the terminal `ParseFailure` → (a) its accumulated usage/timing LEAK (no
`PipelineMessage` ⇒ absent from `aggregate_pipeline_usage`, WS-D), and (b) the failure is INVISIBLE to the
Director (no escalation recorded). Sites: `_coordinator_relay` (~:1240, returns `None`; callers fall back
to the raw worker question — graceful but silent), `_coordinator_review_gap` (~:1459, result discarded —
fully silent no-op), the baseline-unreadable relay (~:2136) and the failed-task HALT relay (~:2249) in
`_run_build_round` (relay result not captured), `_verify_with_retries` (~:1619, returns the prior
reason-string, dropping usage; the `verify_done` coordinator judge ~:1196 stringifies its own ParseFailure
too). RARE² (the Coordinator is relay-only — the most reliable agent — and needs 3 consecutive invalid
blocks; `_PARSE_RETRIES`=2); BOUNDED (pipeline state stays correct in every case — the graceful fallbacks
already settle to `awaiting_director`/`blocked`; no P0/P1). This is an **observability + metrics-completeness**
gap, not a control-flow bug — and silent internal-turn failures directly contradict the E7/WS-C2
transparency goal.

**Changes (uniform pass — observability + metrics ONLY, NOT control-flow):**
- **E-1 — Capture the metrics.** At each site capture the internal turn's accumulated usage/timing (reuse
  CR-036 `_failure_metrics_payload(result)`) so it reaches a `PipelineMessage` and rolls up in
  `aggregate_pipeline_usage`. For `_verify_with_retries`, propagate the `ParseFailure` (or its metrics) to
  the caller instead of dropping to a bare reason-string.
- **E-2 — Make the failure VISIBLE.** Record one `system→director` note (plain Slovak per CR-NS-022 §2)
  naming the failed internal turn (Coordinator relay / gap-review / verify-judge) and that it exhausted
  retries — so the Director knows the framing they see is a fallback (e.g. the raw worker question), not
  the Coordinator's intended relay. Reuse the `_block_failed` note pattern. The visibility note is recorded
  ALWAYS on internal-turn parse-exhaustion (visibility ≠ metrics — unlike `_block_failed`'s usage-gating);
  the metrics payload is attached when present.
- **PRESERVE the graceful fallback (HARD constraint).** Sites keep their existing settled outcome — `:1240`
  still falls back to the raw worker question + `awaiting_director`; `:1459` stays non-blocking advisory;
  `:2136`/`:2249` still settle to `awaiting_director` with the existing `next_action`; `:1619`'s caller
  still blocks. We ADD the metrics + the visible note; we do NOT add decision branches, change offerable
  actions, or change the stage/status outcome. If any site's control-flow makes a non-invasive add unclear,
  STOP+ask (do not refactor control flow).
- **Single drift-proof helper.** One shared `_record_internal_turn_parse_failure(...)` used by all 5 sites
  so a future internal-turn relay cannot silently re-introduce the gap; per-site test.

**Seams to preserve:** hub-and-spoke (the note is `system→director`, no new agent dispatch); deterministic
gates unchanged; the `_coordinator_relay` raw-question fallback stays; no control-flow change to any site's
settled state.

**Acceptance:** each of the 5 sites, on an internal-turn parse-exhaustion, (a) records the internal turn's
tokens/timing into a `PipelineMessage` that `aggregate_pipeline_usage` counts, and (b) records a
Director-visible plain-Slovak note naming the failed internal turn — while the pipeline's settled state
(`awaiting_director`/`blocked` + the existing `next_action` fallback) is UNCHANGED. Per-site tests; affected
failure-path message-count tests updated to expect the new note; full suite green.

---

## 3. Out of scope (later phases)
- **Phase 2:** E6 Telegram presence toggle, E3 sidebar cleanup + model/effort-in-Settings, E2 Backlog.
- **Phase 3:** E5 metrics PAGE (after WS-D has accumulated data), E4 i18n SK/EN, E1 cross-project
  unification (shared frontend + shared modules e.g. auth).

## 4. Build order
1. WS-B1, WS-B3, WS-C1 (small, deterministic, stop today's hiccups).
2. WS-B2 + WS-A3 baseline action (shared).
3. WS-A1/A2/A3 (E7 — the axis; depends on B2 + structured directives).
4. WS-C2, WS-D (parallel, independent).
5. WS-E (follow-up robustness CR-NS-037, after WS-D — reuses CR-036 `_failure_metrics_payload`).

**End of Phase 1 development spec.**
