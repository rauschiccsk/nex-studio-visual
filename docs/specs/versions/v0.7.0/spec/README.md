# NEX Studio v0.7.0 — Cockpit Robustness

> Design of record. Authored by **Dedo** (NEX Studio develops cross-project — Dedo design + nex-implementer build,
> NOT through its own cockpit). A **hardening pass**: durability of the engine, not new features. Grounded by two
> exploration sweeps (the `cockpit-robustness-backlog-map` 4-class survey + the `r1-r2-grounding` anchor map) —
> every extension point cites a real file:line.

## 1. Origin & goal
The fast-fix lane build (v0.6.0 / F-009, CR-NS-094..103) **dogfooded the cockpit hard** and surfaced recurring
operational fragility — not bugs in a feature, but weaknesses in the *engine* that made a one-word fix take six
symptom-patches. A 4-class survey of the cockpit found **24 concrete fragility instances (8 high)**. This version
hardens them so the engine is durable for a future **non-Dedo Director** (Tibor/Nazar) operating it unaided.

**Goal:** the cockpit never silently loses work, never drifts its BE↔FE contract, never leaves the operator
guessing why it stopped — without lowering any quality gate.

## 2. The four fragility classes
1. **Stale sessions & dispatch lifecycle** (6 instances, 2 high) — timeout kills `claude` mid-output → work done
   but the envelope is lost; no per-version durable single-flight lock; `OrchestratorSession` never GC'd.
2. **Brittle parsing & schema drift** (8 instances, 3 high) — truncated-envelope `json.loads` crash; the
   `<<<PIPELINE_STATUS>>>` fence-regex is fragile to model drift; silent degradation paths.
3. **BE↔FE contract gaps** (3 instances, 1 high) — enums/sets hand-mirrored across BE & FE with no single source
   of truth (`capture_backlog_item` still missing in the FE set — flagged by 3 of 4 auditors).
4. **Opaque UX** (7 instances, 2 high) — `next_action` is the sole, generic signal; the Director can't see WHY it
   blocked, the Coordinator's triage, or autonomous decisions at a glance.

## 3. CR roadmap & build order
| CR | Scope | Class | Priority | Effort | Status |
|---|---|---|---|---|---|
| **R1** | Dispatch resilience — lost-work detection + durable single-flight + session TTL + all-stage orphan recovery | 1 | HIGH | M–L | **BUILT + deployed** |
| **R2** | BE↔FE contract single-source-of-truth — OpenAPI→TS codegen + Literal schemas + parity contract-test | 3 | HIGH | M | **BUILT + deployed** |
| **R3** | Parsing hardening — native structured output (`claude --json-schema`) for the status block + fence fallback | 2 | MED | L | **specified (this version)** |
| R4 | Operator legibility — `block_reason` enum + banner/next_action decoupling + autonomous-decision board summary + triage legibility + PipelineRail legend | 4 | MED | M–L | roadmap (coordinate with the agent-comms-transparency design) |

**Director-approved (2026-06-16):** develop the backlog into this spec version, **start with R1 + R2** (both HIGH,
highest impact on what actually hurt during the F-009 build). R3 is the deepest investment (parser→tool_use); R4
coordinates with the existing agent-comms-transparency design — both are specified after R1+R2 land.

## 4. Specs
- **R1** — `R1-dispatch-resilience.md`
- **R2** — `R2-contract-source-of-truth.md`
- **R3** — `R3-structured-output.md`

## 5. Cross-cutting standouts (why R1+R2 first)
1. **Timeout/work-loss** (R1) — flagged by 2 classes; it bit us live in CR-094 (envelope empty after 1800s, work
   actually committed). The single most operationally painful fragility.
2. **BE↔FE single source of truth** (R2) — flagged by 2–3 classes; one codegen step + a contract-test kills the
   whole contract-gap class. `capture_backlog_item` is the live proof it's needed.
