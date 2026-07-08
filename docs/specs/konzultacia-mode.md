# Konzultácia — read-only advisory conversation + change→new-version routing (obs-5)

Director-approved design 2026-07-08. The v3 cockpit conversation is welded to starting/running
a build, the AI turn can mutate the project, and a done/released version accepts a message but
produces NO answer (dead-end). Konzultácia fixes this: a strictly read-only advisory chat with
the AI partner, available on a finished/deployed version anytime, that changes NOTHING and routes
any change-request into a NEW version.

Branch: `v2.0.0-dev`. Self-verify BOTH domains (BE: ruff format --check + ruff check + FULL
pytest; FE: build+lint+test). Build in the natural order (Part 1 → 2 → 3); tests per part.

## Approved decisions (do not re-litigate)
- **Version-state decides the mode, no toggle.** A version whose pipeline is terminal
  (`current_stage == 'done'`, incl. PROD-`released`) → the conversation is **read-only
  Konzultácia**. A version mid-build → the conversation stays the build (UNCHANGED). Never-built
  version → first message still cold-starts the build (UNCHANGED).
- **Strictly non-mutating by construction**, not by prompt promise: the consult AI turn runs with
  a READ-ONLY tool profile (Read/Grep/Glob only — NO Bash/Write/Edit), so it *cannot* touch the
  project, only read it.
- **Change-request → new version, manager-started.** On a detected change-request the AI states it
  needs a new version and the UI offers **"Založiť novú verziu z tejto požiadavky"** → mints the
  next version in DRAFT with the request captured as its zadanie. It does **NOT** auto-start the
  build; the manager opens the new version and starts it deliberately.

Grounding (from two Explore maps of the current code — cite when locating):
- Gate: `relay_manazer_message` (orchestrator.py:2429) raises "Pipeline not started" at 2447-2449
  if no `PipelineState`. For a `done` version the relay is accepted but `_begin_dispatch` no-ops
  because `STAGE_ACTOR` has no `done` actor (orchestrator.py:357-362, 3359-3360) → message recorded,
  no answer. THIS is the dead-end to fix.
- AI turn chokepoint: `run_conversation_turn` (4411, already NEVER advances a phase) →
  `invoke_agent_with_parse_retry` (2782) → `invoke_agent` (2521) → `invoke_claude`
  (claude_agent.py:138; 247-294 passes NO tool flags → full-auto). Directive: `_conversation_directive`
  (4340, explicitly instructs writing specification.md).
- Mutation vectors to avoid for consult: (a) write-capable `invoke_claude` tool profile, (b) the
  spec-write directive, (c) `_begin_dispatch` state flip (3351).
- Version lifecycle enum `('planned','active','released')` (versions.py:35). `version.create`
  (version.py:179). Backlog `REQ-N`: `backlog_service.create` (backlog.py:70) +
  `assign_to_version` (backlog.py:116). Fast-fix patch: `fast_fix_service.create_patch_version`.
- FE: `RiadiaceCentrumPage.tsx` (composer disabled only on `!versionId`/`frameworkBlocked`),
  `ConversationComposer.tsx`, `ConversationThread.tsx`, `HonestStatusStrip.tsx`, `SpecApprovalBar.tsx`
  (bar pattern to mirror), `PlanUlohRail.tsx`; WS in `hooks/usePipelineWs.ts`; APIs in
  `services/api/pipeline.ts` (`relayPipelineMessageApi`, `startFastFixApi`); `store/activeContextStore.ts`.

---

## Part 1 — Backend: the read-only consultation turn (the core)

Make a terminal version (`current_stage == 'done'`) answerable, read-only.

1. **Reachability.** When a manager message arrives (`relay_manazer_message`, orchestrator.py:2429)
   for a version whose `PipelineState.current_stage == 'done'` (covers done + released), route it to
   a NEW consult path instead of the no-op `_begin_dispatch`. Do NOT relax the `state is None` guard
   for never-built versions (those still cold-start a build). Consult requires an existing `done`
   state row.
2. **Read-only tool profile.** Add a parameter to `invoke_claude` (claude_agent.py:138) that passes
   an explicit allowed-tools set; for consult pass **only read tools** (`Read`, `Grep`, `Glob`) —
   NO `Bash`, `Write`, `Edit`. Default (unset) = today's full-auto for build turns, byte-identical.
   This is the hard non-mutating guarantee (per the Bash-permission lesson: absence of any write
   tool, not a "read-only Bash", is what makes it safe).
3. **Read-only directive.** New sibling of `_conversation_directive` (orchestrator.py:4340) WITHOUT
   the specification.md write instruction, with an explicit contract: "You are in read-only
   Konzultácia for a finished version. Answer, analyze and explain, grounded in the project's specs,
   code, plan, metrics and history. You must not change anything. If the manager asks for a change to
   the app, do NOT attempt it — state plainly that it requires a new version, and emit a
   change_request (Part 2)."
4. **No build mutation.** The consult turn must NOT call `_begin_dispatch` (3351) mutations
   (no `status→agent_working` flip that sticks, no baseline-SHA capture, no phase advance). Reuse
   `run_conversation_turn`'s non-advancing settle shape (4507-4511). After a consult answer the
   version returns to its terminal resting state (still `done`/`released`).
5. **Metrics safety (must-have).** Consult turns must NOT pollute the honest build-phase metrics
   (navrh/programovanie/verifikacia) we just shipped. Stamp consult messages so their usage does NOT
   attribute to a `COMPARISON_PHASES` bucket — keep them at `stage='done'` (or a `payload.phase`
   outside the 4 comparison phases) so `aggregate_usage_by_phase` folds them into system-overhead,
   not a build phase. Add a test asserting a consult turn does not change navrh/programovanie/
   verifikacia token totals.

Deliverable of Part 1: on a done/released version the manager can ask anything and get a real
answer; the AI provably cannot modify the project; build state and build metrics are untouched.

## Part 2 — Backend: change-request detection + capture + version mint

1. **Detection.** The read-only directive tells the AI to emit a structured `change_request` marker
   when the manager's ask would require modifying the built app (reuse the block/enum pattern of
   `ConsultationBlock` in pipeline_status.py:266-311, or a lightweight `payload.change_request`
   field + a short `summary`). The AI's plain-language reply still says "this needs a new version".
2. **Capture + mint endpoint.** New backend action/endpoint: given the change-request summary +
   `selectedProject`, (a) record it as a project backlog `REQ-N` via `backlog_service.create`
   (backlog.py:70), and (b) mint the NEXT version via `version.create` (version.py:179) in DRAFT
   (`planned`, NO `PipelineState`, NO build running), linking the REQ to it via `assign_to_version`
   (backlog.py:116) so the new version's Špecifikácia starts from the request. Return the new
   version id/number.
3. **No auto-start.** Do NOT call `apply_action("start", …)`. The build begins only when the manager
   opens the new version and engages — a deliberate act.

## Part 3 — Frontend: consult indicator + change-request bar

1. **Answered + labelled.** For a `done`/`released` version the composer already renders and is
   enabled; now the backend answers. Add a clear read-only indicator in `HonestStatusStrip.tsx`
   (e.g. "Konzultácia — poradím, nič nezmením") so the manager knows the mode. No mode toggle.
2. **Change-request bar.** When a consult message carries the `change_request` marker, render a bar
   mirroring `SpecApprovalBar.tsx` (new `ChangeRequestBar.tsx` mounted next to it in
   RiadiaceCentrumPage.tsx) with **"Založiť novú verziu z tejto požiadavky"**. Click → call the Part 2
   endpoint (project from `activeContextStore.selectedProject`, summary from the message) → on success
   navigate to the new DRAFT version (manager reviews + starts build deliberately). Add the
   `change_request` field to the `PipelineMessage`/`PipelineBoard` types (services/api/pipeline.ts).
3. Reuse existing WS/relay plumbing (`usePipelineWs.ts`, `relayPipelineMessageApi`) — the consult
   answer arrives on the same channel.

---

## Tests (mandatory, RED→GREEN where reproducible)

- **Part 1:** consult message on a `done`/`released` version PRODUCES an answer (RED: dead-end today);
  the consult AI turn is invoked with the read-only tool profile (no Write/Edit/Bash); build state
  (`status`, `current_stage`, baseline SHA) UNCHANGED after a consult turn; consult usage does NOT
  change navrh/programovanie/verifikacia phase token totals; a never-built version STILL cold-starts
  a build (unchanged); a mid-build version's conversation is UNCHANGED.
- **Part 2:** a `change_request` marker → the capture endpoint records a backlog `REQ-N` AND mints a
  DRAFT next version (`planned`, NO `PipelineState`) linked to the REQ, and does NOT start any build.
- **Part 3:** composer answers for done/released; the read-only indicator renders; the change-request
  bar appears on a marked message and its click calls the mint endpoint then navigates.
- Full `pytest` (shared spine); FE build+lint+test.

## Out of scope (note, don't build)
- Project-level version-independent consult thread (a fuller "conversation-as-foundation" channel) —
  this design is version-anchored (the finished version), which covers the Director's post-deploy
  scenario. Flag as a natural future extension; do not build now.
- Mid-build read-only consult — during an active build the conversation stays the build.
