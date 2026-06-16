# NEX Studio v0.6.0 — F-009: Fast-Fix Lane

> Design of record. Built by **Dedo (design) + nex-implementer** (NEX Studio develops cross-project, NOT through
> its own cockpit). Director-approved design 2026-06-16. Grounded by the `fastfix-lane-grounding` exploration
> (6 readers) — every extension point cites a real file:line.

## 1. Goal
A lightweight cockpit lane for **small, obvious fixes** found during debugging (drift/bugs that can't be predefined
upfront). Flow: **Director → Coordinator → Implementer → self-verify → Coordinator-verify → UAT → acceptance → PROD**,
**skipping** the full waterfall (Designer / Customer / Auditor+Dual-Build). Quality is NOT lowered — it is right-sized:
the heavy multi-agent ceremony (disproportionate for a tiny change) is dropped; Implementer self-verify + independent
Coordinator verify + the UAT acceptance gate + full traceability + §4 security all stay. Goal: what costs ~5–10 h
through the full pipeline costs ~10–20 min here.

## 2. Director-approved design decisions
- **D1 — Shape:** the flow above; drop Designer/Customer/Auditor+Dual-Build.
- **D2 — Entry & one-touch (refined CR-NS-097):** the Director submits the fast fix **with the directive** — that
  submission **IS the authorization** (no separate kickoff approval). The directive rides in the kickoff message
  content + is prepended to the Coordinator's kickoff brief. The **Coordinator escalation guard** triages: trivial &
  clear → **AUTO-advance to build (no Director gate)**; non-trivial (ambiguous, multi-module, changes spec'd behaviour
  needing Designer thought, schema/dep change) → **STOP + propose converting to a full version** (never proceeds on its
  own). Net: a single Director touch — `submit → [auto: triage → build → Coordinator-verify → release] → uat_accept`.
- **D3 — Recording:** a **traceable PATCH version** (`vX.Y.Z → vX.Y.Z+1`) with the full trail (directive →
  Implementer report+self-verify → Coordinator verify → UAT/PROD); shown as a distinct fast-lane item on the board.
- **D4 — UX:** a **"Rýchla oprava"** entry on the project page; one prompt → board shows live status + "kto je na rade".

## 3. Mechanism (grounded)
**Key grounding finding:** today `flow_type ∈ ('new_version','cr','bug')` all traverse the SAME global `STAGE_ORDER`
(`orchestrator.py:155-179`) — there is **no lighter flow yet**. Fast-fix is the first. The Version + PipelineState +
dispatch + per-task build-loop + verify infra is reused wholesale.

- **New `flow_type='fast_fix'`** — extend the CHECK constraint (`db/models/pipeline.py:88-90`) + the `start` validation
  (`orchestrator.py:3191-3193`); idempotent migration.
- **Flow-aware stage routing** — fast_fix path = `kickoff → build → release → done` (skips gate_a-e, task_plan, gate_g).
  Implement a flow-aware `_next_stage(stage, flow_type)` (or a per-flow stage map) so fast_fix's `kickoff` advances to
  `build`, and `build` settle advances to `release` — never to gate_a/task_plan/gate_g. Keep `STAGE_ORDER` for
  new_version.
- **Entry** — FE "Rýchla oprava" → backend: **auto-create a PATCH version** (`vX.Y.Z+1` derived from the project's
  latest version_number; semver patch bump helper) → `apply_action(version_id, "start", {flow_type:"fast_fix",
  directive:"<text>"})`. The directive is carried into the **kickoff message content** AND prepended to the
  Coordinator's kickoff brief (CR-NS-097 — the fresh kickoff agent's only context), so it triages the actual fix.
- **Escalation guard (kickoff/coordinator)** — the Coordinator triages: small & obvious? Heuristic = single concern,
  no multi-module / schema / new-dep, no requirement ambiguity. **Trivial & clear → AUTO-advance to build (NO
  `awaiting_director` gate — the submission is the authorization, CR-NS-097)**. **Non-trivial → `status=awaiting_director`
  + a structured proposal to convert to a full version** (reuse the E7 `coordinator_directive` + flag-the-gap-and-STOP).
- **Build reuse** — auto-create **ONE minimal Task** from the Director directive (the directive = the task brief) so the
  existing build loop (`_run_build_round`, per-task dispatch + verify + auto-fix ≤5) runs unchanged. The Programmer
  brief marks the directive **AUTHORITATIVE** — execute it directly, do NOT debate/second-guess it (CR-NS-097); STOP
  only if technically impossible or genuinely unclear WHAT to change. Self-verify (build/lint/test) per charter. A
  **clean build AUTO-advances to release** (no approve) — release settles for the single `uat_accept`. NO Designer task-plan decomposition.
- **Coordinator verify** — on build-task settle, the Coordinator independently verifies (reuse the `verify_done` /
  coordinator-review path) — NOT a full Auditor, **NO Dual-Build**.
- **Release & auto-deploy (CR-NS-098; mechanism revised CR-NS-101)** — after the Coordinator-verify passes, IF
  `project.uat_slug` is set the lane **auto-redeploys the project's UAT** with a plain
  `docker compose -f /opt/uat/<uat_slug>/docker-compose.yml up -d --build --force-recreate` (async; `VITE_APP_VERSION`
  stamped from the repo's commit count). It runs against the UAT's OWN existing compose — **NOT `uat-deploy.py`**, which
  is a PROVISIONER that re-renders the compose + reallocates ports + rewrites nginx (would clobber a hand-authored UAT
  like NEX Ledger). The backend has `/var/run/docker.sock` + `/opt/uat` + `/opt/projects` mounted, so the compose is reachable.
  Success → `release`/`awaiting_director` → the Director verifies on UAT, then the single `uat_accept` → `done`. Deploy
  failure → surfaced to the Director (`blocked`/`awaiting_director`, never hidden). `uat_slug` NULL → deploy skipped with a
  `system→director` note (still awaits `uat_accept`). So the fast fix is end-to-end: submit → [auto: triage → build →
  Coordinator-verify → UAT deploy] → Director checks UAT + `uat_accept`.

## 4. CR breakdown (build order)
- **CR-A (BE core):** `fast_fix` flow_type + migration; flow-aware stage routing (kickoff→build→release skip);
  patch-version auto-create + semver bump; the `start`(fast_fix) entry + Coordinator escalation-guard triage;
  auto-create 1 minimal Task; Coordinator-verify reuse. + BE tests (flow skips the right stages; escalation STOPs;
  patch bump; verify runs).
- **CR-B (FE):** "Rýchla oprava" entry on `ProjectDetailPage` + the cockpit board renders the fast_fix flow (short
  stage path, status, "kto je na rade") + `determine_available_actions` extended for fast_fix stages. + FE tests.
- **CR-C (wiring + tests):** UAT → acceptance → PROD wiring for the patch + deploy-layer touch + integration tests + KB/docs.

## 5. Seams to preserve
PipelineState 1:1 per version; `apply_action` the sole state mutator; `_build_open_findings` the deterministic gate;
hub-and-spoke (Director↔Coordinator only); the escalation guard MUST prevent any Designer/task_plan dispatch on
fast_fix; new_version/cr/bug flows UNCHANGED (additive only).

## 6. Resolved open points
1 minimal Task (reuse the build loop, not task-less plumbing). New `fast_fix` flow_type (NOT reusing cr/bug — those are
full-pipeline labels today). Patch version auto-bump (semver `vX.Y.Z+1`).
