# NEX Studio v0.7.0 — R1: Dispatch Resilience

> Design of record. Grounded by `r1-r2-grounding` (every anchor is a real file:line). Class 1 (stale sessions &
> dispatch lifecycle). The engine must never silently lose agent work and must serialize dispatch durably.

## 1. Goal
**Scope: the cockpit's OWN dispatch path** — the pipeline-driven `invoke_agent` → `claude_agent.py` →
`pipeline_runner`, the engine a future non-Dedo Director relies on. (`dedo-dispatch-implementer` is Dedo's
EXTERNAL meta-dev tool: a separate process, not pipeline-driven, touching no `PipelineState` — its CR-094 timeout
was real but Dedo handles that harness manually; it is explicitly **out of R1 scope**.)

The cockpit's own dispatched agent turn can be **killed by its timeout mid-output** — the JSON envelope is lost,
but the agent may already have **committed real work**. Today that surfaces as a `ClaudeAgentError` →
`ParseFailure` → `blocked`, with **no signal that work exists** (the same failure mode that bit Dedo's harness in
CR-094). Plus the single-flight guard is in-memory only (lost on restart → concurrent builds, CR-027), and
`OrchestratorSession` rows never expire. R1 makes all three durable.

## 2. Director-approved design decisions
- **D1 — Lost-work detection by commit audit.** On a timeout/envelope-loss, the engine compares the dispatch's
  **baseline HEAD** to the current HEAD; if commits exist, it tells the Director *work may have landed — review &
  continue*, instead of a bare `blocked`. Never auto-merges — surfaces, never hides, never silently re-does.
- **D2 — Durable single-flight.** One dispatch per version, enforced at the DB level (survives a backend restart),
  complementing — not replacing — the existing in-memory `_ACTIVE_DISPATCH` guard.
- **D3 — Session hygiene.** `OrchestratorSession` gets a `last_input_at` + a TTL retention task, mirroring the
  proven `agent_terminal.idle_cleanup` pattern. Hygiene, not a crash-preventer — conservative 7-day TTL.
- **D4 — All-stage orphan recovery.** `recover_orphaned_builds_on_startup` extends from build-only to every stage,
  with the same baseline..HEAD commit audit, so a restart mid-kickoff/gate/release is recovered + flagged.
- **D5 — Additive only.** No change to the parse-retry machinery, the per-task `baseline_sha`, or any non-fast_fix
  flow's behaviour. New columns are nullable/defaulted; the dual baseline (dispatch-level + task-level) coexists.

## 3. Mechanism (grounded)
**Per-version dispatch state lives on `PipelineState`** (1:1 per version, matches the single-flight key + the
version lifecycle); **session hygiene lives on `OrchestratorSession`** (1 per project+role).

- **New columns (idempotent migration):**
  - `PipelineState.dispatch_baseline_sha` (String(40), nullable) — repo HEAD captured at dispatch start.
  - `PipelineState.dispatch_in_flight` (Boolean, default False) — durable single-flight flag.
  - `OrchestratorSession.last_input_at` (DateTime, default=created_at) — for TTL (`backend/db/models/orchestrator.py:24-40`).
- **Baseline capture + lifecycle** — at `_begin_dispatch` (before the agent is invoked), set
  `state.dispatch_baseline_sha = _repo_head(project_root)` **only if it is currently NULL** (`if not
  state.dispatch_baseline_sha:`), and set `dispatch_in_flight = True`; flush. (`_repo_head` already exists, used by
  the per-task baseline at `orchestrator.py:3370-3379` — reuse it. Per-task `baseline_sha` is UNCHANGED — a
  separate concern for verify_mechanical.) **Lifecycle:** the same cleanup that clears `dispatch_in_flight` (the
  `pipeline_runner._run` done-callback + the settle paths) ALSO resets `dispatch_baseline_sha = NULL`. Net: the
  baseline is captured ONCE at the start of a dispatch and **frozen across all parse-retries within that dispatch**
  (the `if not` guard means a retry re-entering `_begin_dispatch` does NOT overwrite it — Seam #4); a fresh
  dispatch (after settle) re-captures from a clean NULL.
- **Timeout / lost-work detection** — the timeout is raised at `claude_agent.py:236-241` (`asyncio.TimeoutError` →
  `proc.kill()` → `ClaudeAgentError`); caught at `orchestrator.py:982-995` (→ `ParseFailure`, `usage=None`).
  EXTEND the catch: read current HEAD, compute `git rev-list --count baseline..HEAD`. Record a `system→director`
  **`notification`** message carrying `{dispatch_baseline_sha, post_timeout_head_sha, timeout_seconds,
  detected_commit_count}` in its payload. If `count >= 1` → `next_action = "Vypršal čas agenta — môžu byť zapísané
  zmeny (N commitov). Over 'git log' a pokračuj."`; if `count == 0` → `next_action = "Vypršal čas agenta — žiadna
  zmena nezistená. Pokračuj."`. Status stays `blocked`/`awaiting_director` (never auto-proceeds). The existing
  `turn_metrics` timing is preserved (`usage=None`, timing from `perf_counter`) — Seam #6.
- **Durable single-flight** — in `apply_action` (the sole mutator), BEFORE `_begin_dispatch`: if an
  `agent_working` action arrives while `dispatch_in_flight == True`, raise `OrchestratorError("Dispečer už beží
  pre túto verziu")`. `_begin_dispatch` sets `dispatch_in_flight = True`; `pipeline_runner._run`'s cleanup
  (the `done_callback` clearing `_ACTIVE_DISPATCH`, `pipeline_runner.py:48-84`) ALSO sets it `False`. The route
  (`api/routes/pipeline.py:212-267`) is unchanged — the guard is in `apply_action`, the single mutator.
- **All-stage orphan recovery** — `recover_orphaned_builds_on_startup` (`orchestrator.py:2982-3022`) today matches
  `stage=='build' AND status=='agent_working'` with BUILD-specific messages (`orchestrator.py:3006-3019`). Extend
  the match to **all stages** at `agent_working`; for each, capture baseline..HEAD (use the stored
  `dispatch_baseline_sha` if present, else `_repo_head`), record the commit-audit `notification`, flip to
  `awaiting_director`, and **clear `dispatch_in_flight` + reset `dispatch_baseline_sha`** (a killed process left
  them set). **Use a generic stage-parametrized message** (keep the existing BUILD wording for `stage=='build'`
  for back-compat): `next_action = "Fáza '{stage}' prerušená reštartom — {audit}. Pokračuj."` where `{audit}` =
  `"môžu byť zapísané zmeny ({N} commitov), over 'git log'"` if `count≥1` else `"žiadna zmena nezistená"`. Build
  keeps its existing in-`_run_build_round` task-reclaim — additive, not a replacement.
- **Session TTL** — add a `cleanup_old_orchestrator_sessions()` (TTL 7d on `last_input_at`), mirroring
  `agent_terminal.idle_cleanup` (`agent_terminal.py:610-621`). Wire it as a background loop in the `main.py`
  `lifespan()` context manager, next to the existing `_agent_terminal_idle_loop` / `_agent_terminal_log_retention_loop`
  tasks (`main.py:71-146`) — same `asyncio.create_task` + sleep-interval pattern. Update `last_input_at` on each
  `invoke_agent`. New-version kickoff already deletes sessions — unchanged.

## 4. CR breakdown (build order)
- **R1-a (schema):** the 3 columns + idempotent migration (init `dispatch_in_flight=False`,
  `dispatch_baseline_sha=NULL`, `last_input_at=created_at`).
- **R1-b (baseline + single-flight):** capture baseline + set/clear `dispatch_in_flight` in
  `_begin_dispatch`/`apply_action`/`pipeline_runner` cleanup; the durable guard.
- **R1-c (lost-work detection):** the timeout catch extension + the commit-audit `notification` + next_action.
- **R1-d (recovery + TTL):** all-stage orphan recovery with commit audit; `OrchestratorSession` TTL task.
- **Tests:** see §6.

## 5. Seams to preserve (from grounding)
- **#1 stale baseline if history rewritten mid-turn** — accept; rebase/force-push mid-dispatch is out of scope, the
  audit is advisory (Director reviews).
- **#2 `dispatch_in_flight` lost on a DB crash mid-flush** — the all-stage orphan recovery (D4) clears it on
  startup, so a crash self-heals.
- **#3 session deleted by new-version kickoff** — guard `_begin_dispatch`/cleanup against a missing session row.
- **#4 parse-retry reuses one baseline** — capture ONCE per dispatch, not per attempt.
- **#7 dual baseline** — dispatch-level (PipelineState, turn-start snapshot) and task-level (`Task.baseline_sha`,
  verify anchor) live independently; do not conflate.
- **Untouched:** parse-retry machinery, non-fast_fix flows, the per-task baseline, the in-memory `_ACTIVE_DISPATCH`
  (kept as the fast first-line guard).

## 6. Test points
- UNIT: `_begin_dispatch` captures `dispatch_baseline_sha` + sets `dispatch_in_flight`, flushes.
- UNIT: `apply_action` at `agent_working` + `dispatch_in_flight=True` raises `OrchestratorError`.
- UNIT: cleanup clears `dispatch_in_flight`; a missing session row doesn't crash.
- UNIT: timeout catch records the commit-audit notification (count≥1 and count==0 branches) without breaking the
  ParseFailure escalation.
- INTEGRATION: timeout during a Coordinator turn with commits → audit recorded, `awaiting_director`, next_action
  names the commit count; timeout with no commits → "žiadna zmena".
- INTEGRATION: restart with `agent_working` at kickoff/release → recovery flips to `awaiting_director`, clears the
  flag, records the audit.
- INTEGRATION: two `schedule_dispatch` on one version → blocked in-memory AND the durable flag prevents a
  post-restart duplicate.
- REGRESSION: non-fast_fix flows + parse-retry + per-task baseline unchanged.
