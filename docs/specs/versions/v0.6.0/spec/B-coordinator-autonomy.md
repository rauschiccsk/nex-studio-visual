# v0.6.0 Cockpit Hardening — Pillar B: Coordinator autonomous first-principles decision

> **Director-approved boundary (2026-06-13).** The HEART of the automation: at a build HALT / Implementer
> question, the Coordinator DECIDES on first principles (professional / quality / reliable — NEVER fast/temp)
> instead of escalating every time to the Director. It escalates ONLY genuine ambiguity. Foundation
> (waterfall): the design pre-answers most things → the build flows; a genuine blocker is a DESIGN-QUALITY
> signal that escalates, never an auto-patch. Full role spec: memory `project-nex-studio-coordinator-role-spec`.
> Pillars A (synthesis) + C (per-task reporting) are LIVE; this is the most behavior-sensitive slice — built
> last on purpose, on top of C's visibility.

## Director-approved autonomy boundary

**Coordinator AUTO-DECIDES + EXECUTES (no Director click)** when ALL hold:
- the fix is clear from the spec + code (the design pre-answered it),
- it is a **routine recovery** action — `proposed_action ∈ AUTO_SET = {coordinator_reset_task,
  coordinator_move_baseline, coordinator_clear_session}` (bounded, reversible),
- `confidence ≥ 0.80`, and `triage_class != "director_decision"`.

**Coordinator ESCALATES to the Director** when ANY holds:
- genuine ambiguity (first principles don't give one clear answer) → `triage_class="director_decision"` or `confidence < 0.80`,
- a **design / scope change** (touches the spec) → `proposed_action="coordinator_route_to_designer"` (DESIGN-QUALITY signal, never auto),
- a destructive / irreversible action, or `coordinator_escalate_dedo`.

## §B.1 — backend: decide-vs-escalate at the build HALT / Implementer question

Today (`_run_build_round`, the failed-task HALT ~2493 + the Implementer-question ~2451): the Coordinator
relays its `coordinator_directive` to the Director, who approves via `apply_coordinator_recommendation` →
`_execute_coordinator_directive`. **B inserts an auto-execute branch BEFORE the escalate:**

```
directive = <Coordinator's coordinator_directive at the HALT/question>
if _coordinator_directive_executable(directive)               # existing gate: conf>=0.80 & not director_decision
   and directive.proposed_action in AUTO_SET                  # NEW: bounded-recovery subset (NOT route_to_designer/escalate_dedo)
   and autonomous_count(task) < _MAX_AUTONOMOUS_PER_TASK:     # NEW cap (see §B.4)
       _execute_coordinator_directive(db, state, directive)   # existing executor (verified safe on every action)
       _record_autonomous_decision(...)                       # NEW: a VISIBLE coordinator→director note (marker payload.is_autonomous=true)
       # continue the build loop (re-dispatch) — NO awaiting_director
else:
       <existing escalate path: _coordinator_relay → blocked → awaiting_director>
```

- The executor + its per-action safety guards already exist (CR-NS-053 verify confirmed each
  `coordinator_*` action rejects/no-ops/safe-errors on bad input). B only changes the TRIGGER (the Coordinator
  itself, when first-principles-clear) vs the Director's click.
- `_record_autonomous_decision`: `author="coordinator"`, `recipient="director"`,
  `payload.is_autonomous=true` + the directive (action, rationale, confidence). This is the VISIBILITY — the
  Director SEES every autonomous decision (never silent), per the approved decision.

## §B.2 — the Coordinator's first-principles triage (prompt)

Extend the Coordinator's invocation prompt at the build HALT / question (where it already emits the directive)
with the decision framework: *"Rozhodni podľa PRVOTNÝCH PRINCÍPOV (profesionálne, kvalitné, spoľahlivé —
NIKDY rýchle/dočasné). Ak je oprava jednoznačná z dizajnu+kódu a je to rutinné zotavenie (reset úlohy / posun
baseline / vyčistenie session), navrhni ju s úprimnou vysokou istotou — **vykoná sa automaticky**. Ak je to
nejednoznačné, zmena dizajnu/rozsahu (`route_to_designer`), alebo deštruktívne → `director_decision` / nízka
istota → eskaluje sa Directorovi. Genuine blocker = signál slabého dizajnu, eskaluj."* Honest confidence is
load-bearing (it gates auto-execution).

## §B.3 — FE: the autonomous-decision note

Render `payload.is_autonomous` messages distinctly (e.g. a "Koordinátor rozhodol" badge + the action + the
rationale, react-markdown) so the Director sees what was auto-decided + why. The per-task card (Pillar C)
already shows the task outcome; this note shows the Coordinator's intervention. Files:
`PipelineMessageBubble.tsx` / `labels.ts` (a new badge + tone), `ExchangePanel.tsx` if needed.

## §B.4 — safeguards (autonomy ≠ loss of control)

1. **Visible:** every autonomous decision = a `is_autonomous` Director-facing note + the C per-task card.
2. **Auditor still audits** each task → a wrong autonomous fix is caught at re-audit (it re-builds + re-audits).
3. **Conservative gate:** `confidence ≥ 0.80` AND `triage_class != director_decision` AND action ∈ AUTO_SET.
4. **Per-task cap (`_MAX_AUTONOMOUS_PER_TASK = 1`):** the Coordinator auto-intervenes at most ONCE per task; a
   SECOND HALT on the same task after an autonomous recovery → ESCALATE (a repeat failure after a clean
   first-principles fix is a DESIGN-QUALITY signal, not an auto-loop). Prevents autonomous reset-loops.
5. **Waterfall:** design/scope ambiguity escalates as a design-quality signal — never auto-patched.

## Acceptance

- At a build HALT with a high-confidence bounded-recovery directive (e.g. `reset_task`, conf 0.9), the
  Coordinator AUTO-EXECUTES + records a VISIBLE `is_autonomous` note + the build continues — NO awaiting_director.
- At a HALT with `director_decision` / `confidence < 0.80` / `route_to_designer` / a destructive action → the
  build ESCALATES to the Director (current behavior, unchanged).
- The per-task cap: a 2nd HALT on the same task after an autonomous fix → escalates (no auto-loop).
- Tests (NEW): auto-execute path (bounded action + high conf → executed + is_autonomous note + continues);
  escalate path (director_decision / low conf / route_to_designer → escalates, no auto-exec); the cap (2nd HALT
  → escalate). FE test (is_autonomous note renders). `pytest` + `vitest` green; build + lint clean.
- Director smoke: a build with a routine recovery flows WITHOUT a Director click, and the Director SEES the
  Coordinator's autonomous decision in the thread.

## Out of scope

gate_g FAIL flow (Class I) — the next/last slice. Per-project Coordinator charters can later reinforce the
§B.2 framework (the orchestrator prompt is the lever here).
