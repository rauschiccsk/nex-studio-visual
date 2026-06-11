# NEX Studio v0.3.0 — F-008: Coordinator as Operator (E7)

> Detailed design for **WS-A (E7)** — the Phase-1 headline. Refines `development-spec.md §WS-A`.
> Grounded in the `map-cockpit-for-phase1` exploration (2026-06-10). Director approved the design
> direction 2026-06-10. Builds on the live CR-NS-029/030/031 (WS-B + WS-C1) — esp. `accept_merged`
> (CR-031), which is the Director-driven seed of `coordinator_move_baseline`.
> **Decisions:** Coordinator is CONSERVATIVE — it **proposes a concrete decision → Director approves →
> Coordinator executes**; NOT autonomous. Layer-1 mechanical fixes (WS-B) stay fully automatic.

---

## 1. Goal

Today the Coordinator JUDGES + RELAYS (`verify_done`, `_coordinator_relay`, gate_e review) but cannot
EXECUTE. `apply_coordinator_recommendation` (orchestrator.py:1914) only threads advisory TEXT into the
next brief → a **no-op on build** (the failed task stays failed; only `return`/`accept_merged` mutate
state). E7 makes the Coordinator the **active build operator**: it TRIAGES a surfaced problem, proposes a
CONCRETE structured decision, the Director approves, and the orchestrator EXECUTES it. Dedo becomes
escalation-only (the `nex_studio_bug` class).

## 2. A1 — Structured Coordinator directive

When the Coordinator surfaces a problem (a `verify_done` FAIL, a worker question relay, or an engine
failure), it emits — alongside its plain-Slovak relay — a STRUCTURED proposal in the status-block /
pipeline_message payload:

```
coordinator_directive: {
  triage_class:    "spec_problem" | "programmer_guidance" | "nex_studio_bug" | "director_decision",
  proposed_action: "<an executable action from §4, or 'relay' for director_decision>",
  target:          { task_id?: uuid, role?: str, commit?: str },   # what the action operates on
  params:          { ... },                                        # action-specific (guidance text, §ref…)
  rationale:       "<one-line WHY — for the Director's read>",
  confidence:      0.0–1.0
}
```

- Parsed + Pydantic-validated like the rest of the block; persisted in the pipeline_message payload.
- **Bounds (conservative):** `confidence < 0.80` OR `triage_class == "director_decision"` → it is a PURE
  relay (`proposed_action: "relay"`); the Director decides freely, the Coordinator executes nothing.
- The Coordinator NEVER executes on its own — execution is gated on the Director's approval (§5).

## 3. A2 — Triage framework (Coordinator charter)

New charter section (nex-inbox + the `templates/coordinator-charter.md`): after verify/relay, CLASSIFY:

| triage_class | meaning | proposed_action |
|---|---|---|
| `spec_problem` | the spec/design is wrong or ambiguous (else Dual-Build/Tibor fails) | `coordinator_route_to_designer` (+ the §/fix in params) |
| `programmer_guidance` | the Implementer needs direction or a build-mechanics fix | `coordinator_reset_task` / `coordinator_move_baseline` / a guidance answer |
| `nex_studio_bug` | a cockpit limitation, not a project problem | `coordinator_escalate_dedo` (parallel, non-blocking) |
| `director_decision` | a scope/judgment call | `relay` (Director decides) |

**Rules:** ambiguity → `director_decision`; `N≥3` re-routes on the same issue → auto-escalate
(director + dedo); the mandatory buildable/bootable-proof check stays (no P-2 false-PASS); triage SLA
< 5 min. The Coordinator gains READ permission for `docs/specs/**` + `schemas/**` (to spot spec problems).
Prompt change: `verify_done` / relay prompts instruct the Coordinator to append the structured directive.

## 4. A3 — Executable actions (orchestrator)

The Director approving a Coordinator proposal EXECUTES its structured action (not just relays text). Each
is **fail-closed** (guards), mutates state, records a `director→coordinator` audit message, re-dispatches:

| action | effect | reuses |
|---|---|---|
| `coordinator_reset_task` | reset the directive's failed task → `todo` (fresh ≤5 budget) | `_reset_failed_tasks_to_todo` |
| `coordinator_move_baseline` | the Coordinator-driven `accept_merged`: move `task.baseline_sha` to `target.commit`'s parent → re-verify | CR-031 `_repo_parent`, `_failed_build_task` |
| `coordinator_route_to_designer` | open a Designer Q&A/edit on the current task's spec problem (`params.section`), then return to build on resolution | gate_e Branch B `designer_edit` precedent |
| `coordinator_clear_session` | delete `target.role`'s `orchestrator_session` → fresh next dispatch | WS-B1 `delete(OrchestratorSession)` |
| `coordinator_escalate_dedo` | write a structured Dedo-escalation item (parallel; does NOT block the pipeline) | `.dedo-channel` / escalation log |

Wire `apply_coordinator_recommendation` (build stage): read the latest `coordinator_directive` and
dispatch the matching executor instead of threading advisory text — **this is the no-op fix**. Add the
`coordinator_*` actions to `determine_available_actions` (WS-C1) per stage.

> **Note — `coordinator_route_to_designer` is the hardest part** (build→Designer is not native today).
> Model it on the gate_e Branch B `designer_edit` loop. If the mechanism is unclear, the Implementer
> STOPs and asks (do not improvise the routing).

## 5. Flow

```
Programmer problem → Coordinator verify/relay + emits coordinator_directive
  → Director sees a CONCRETE proposal (rationale + proposed_action)
  → Director approves  (or returns / overrides)
  → orchestrator executes the directive's action (§4)
  → re-dispatch
```

Director-facing UI (per WS-C class-D labels): the proposal text + a single action whose label NAMES the
concrete effect — e.g. **"Schváliť Koordinátorov návrh (posunúť baseline)"** — never a generic
"Schváliť podľa Návrhára". Low-confidence / `director_decision` → the plain relay + the usual decision set.

## 6. Seams to preserve (do NOT break)
- `_build_open_findings` / `_gate_e_open_findings` stay the **deterministic gates** — the Coordinator's
  proposal + Director approval mutate them; never a silent Coordinator override.
- Hub-and-spoke (Director↔Coordinator only); the Coordinator NEVER advances a pipeline stage; the
  orchestrator owns agent dispatch order.
- `determine_available_actions` stays the single source of truth for offerable actions (extend it).

## 7. Sub-CR breakdown
- **CR-NS-032 (A1 + A3 core, backend-heavy):** the `coordinator_directive` schema + the executable
  actions (`reset_task`, `move_baseline`, `clear_session`, `escalate_dedo`) + wire
  `apply_coordinator_recommendation` to execute them + `determine_available_actions` + the FE proposal UI.
  `route_to_designer` may split out if the Designer-routing needs its own design pass.
- **CR-NS-033 (A2, charter + prompts):** the Coordinator charter triage framework (nex-inbox + template)
  + the `verify_done`/relay prompt changes to emit the structured directive + READ permissions.

## 8. Acceptance
A build blocker (failed task / merged-commit / spec gap) → the Coordinator triages, emits a concrete
proposal → the Director approves ONCE → the orchestrator executes (reset / move-baseline / route /
escalate) → the build continues. **Zero manual DB by Dedo; Dedo involved only on `nex_studio_bug`.**

## 9. Build clarifications (CR-NS-032 Q&A, 2026-06-11)

Implementer STOP+ask before building; decisions below REFINE §4–§7:

1. **Approve-action contract = (A) single action.** `apply_coordinator_recommendation` (build) is THE
   offerable Director action — it reads the latest `coordinator_directive` and dispatches the matching
   INTERNAL executor (`_coordinator_reset_task` / `_move_baseline` / `_clear_session` / `_escalate_dedo`);
   low-confidence / `director_decision` → today's advisory-text relay (no execution). The FE shows ONE
   button (gated on "an executable directive exists", message-derived) labelled from `proposed_action`
   (WS-C class-D). `determine_available_actions` offers `apply_coordinator_recommendation` — the
   `coordinator_*` are executor functions, NOT first-class offerable actions (keeps it state-only).
   *(Supersedes §4's "add the coordinator_* actions to determine_available_actions" — that was imprecise.)*
2. **`coordinator_escalate_dedo`** = a Director-approved executor in CR-032 (the auto-escalate-on-N-reroutes
   triage rule is A2 / CR-NS-033). Writes to the PROJECT's
   `.dedo-channel/inbox/coordinator-to-dedo-<YYYY-MM-DD-HHMM>-<topic>-escalation.md` (structured frontmatter
   `from: coordinator` / `to: dedo` / `type: escalation` + the directive payload; create the dir if missing).
   Non-blocking: write + audit message + leave the pipeline settled (`awaiting_director`); the Director
   decides next — do NOT halt waiting for Dedo.
3. **`coordinator_route_to_designer` → CR-NS-034** (its own design pass: the build→Designer round-trip +
   return path; the gate_e Branch B `designer_edit` precedent is within-gate_e, not a build→Designer
   round-trip). **CR-NS-032 = A1 + the four executors + wiring + FE; `route_to_designer` is OUT.**

## 10. `route_to_designer` design (CR-NS-034) — build→Designer spec-fix round-trip

The hardest E7 action (build→Designer is NOT native today). Mirror the gate_e Branch B `designer_edit`
precedent (a within-gate dispatch of the Designer for an edit + a return path), adapted to build:

- **`coordinator_route_to_designer` executor** (approved at a build HALT for a `spec_problem`): dispatch
  the **Designer** with the failed task's context + `params.section` — "fix the spec/design for this build
  task" (it edits `docs/specs/…`, reports DONE). Mark the pipeline_state with a `returns_to="build"`
  marker (analogous to `gate_e_dispatch="designer_edit"`) so the dispatch-completion handler returns to
  **build**, not to a gate.
- **On the Designer's DONE:** reset the failed task → `todo` (fresh ≤5 budget, now against the corrected
  spec) + re-enter `_run_build_round` → the Programmer re-attempts with the fixed design.
- Add `coordinator_route_to_designer` to the **executable** set (so an approved directive now EXECUTES it,
  not relays). `determine_available_actions` unchanged — it stays an executor, not an offerable action
  (contract A, §9.1).
- **Seam:** the build task stays `failed` (held) while the Designer fixes; it resets to `todo` ONLY on the
  Designer's DONE. Hub-and-spoke preserved (the orchestrator dispatches the Designer; the Coordinator only
  proposed it; the Director approved).

**End of F-008.**
