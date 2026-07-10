# Cockpit fixes — build-timeout resume + live-activity truncation

Two NEX Studio v3 bugs the Director hit live 2026-07-09/10 during the nex-payables v1.1.0 build.
Branch `v2.0.0-dev`. Self-verify: FULL `.venv/bin/python -m pytest -q` from root (Bug 1 = backend) + ruff;
FE build+lint+test (Bug 2 = frontend). A running build is STOPPED at programovanie/awaiting_manazer — do
NOT touch the pipeline DB; these are code fixes, Dedo resumes the build after deploy.

## Bug 1 — a build TIMEOUT leaves no clean "continue the build" action (offers `schvalit` = FINISH)

Repro: the Programovanie build agent timed out mid-task (by design → settles to `awaiting_manazer`,
"review & continue"). But `determine_available_actions` (orchestrator.py ~561-563) ALWAYS adds `schvalit`
at a settled `programovanie` WITHOUT checking whether tasks remain — and `pokracovat` is offered ONLY when
`status=="paused"` (~517). So mid-build (tasks remaining) the cockpit offers `schvalit` (which ADVANCES
programovanie → verifikacia, FINISHING a 1/33-built version — a footgun, made worse by the new SchvalitBar
button showing there) and NO clean "Pokračovať v stavbe".

Fix — at a settled `programovanie`, gate `schvalit` vs `pokracovat` on **tasks-remaining**:
- The tasks-remaining signal already exists: `all_tasks_done = task_service.get_next_todo_task(db, version_id) is None`
  (see `build_readiness` ~572-581). `determine_available_actions(state)` is state-only (no db); the finer
  DB-derived refinement belongs in the BOARD ROUTE where available_actions is finalized for the FE (it
  already intersects with finer conditions) — OR pass a `build_ready`/`all_tasks_done` flag in. Implementer
  picks the cleanest seam.
- Logic: settled `programovanie` AND NOT all_tasks_done (tasks remain) → offer **`pokracovat`** (resume the
  build loop), and do NOT offer `schvalit`. all_tasks_done → offer `schvalit` (advance to Verifikácia), as
  today. `uprav`/`ask` stay in both cases.
- Ensure `apply_action("pokracovat")` RESUMES the per-task build loop (`_run_build_round`) from this
  `awaiting_manazer`-with-tasks-remaining state, not only from `paused` — relax its guard if it currently
  requires `paused`. (pokracovat already re-dispatches `_run_build_round` via the directive path ~4563.)
- FE is FREE: `PlanUlohRail` already renders "Pokračovať v stavbe" on `pokracovat` (~577) and the
  SchvalitBar footgun disappears (schvalit no longer offered mid-build).

## Bug 2 — live-activity feed still truncates long lines

`frontend/src/components/cockpit/PipelineActivityFeed.tsx:45`: `<span className="truncate">{a.line}</span>`
— Tailwind `truncate` = `overflow:hidden; text-overflow:ellipsis; white-space:nowrap` → each line is
clipped to ONE line. Widening the panel didn't help; the `truncate` class is the cause.

Fix — let long activity lines WRAP + be fully readable:
- Replace `truncate` with wrapping: `whitespace-pre-wrap break-words` (or `break-all` if needed for long
  unbroken tokens like a URL/JSON) so the full line shows across multiple rows. Keep the panel's
  vertical scroll (overflow-y-auto) — it already scrolls, per the line-29 comment.
- (Same-theme, do it if clean:) the feed also leaks raw internal markers — `<<<TASK_PLAN_JSON>>> {…}`,
  raw markdown/JSON — instead of a human line. If the activity `line` still carries those, strip/replace
  the `<<<…>>>` markers + collapse raw JSON to a short human summary before render. If that's a larger
  change, do the WRAP fix now and note the raw-marker cleanup as a follow-up (don't silently skip it).

## Tests (RED→GREEN)
- Bug 1: `determine_available_actions` (or the board finalizer) at settled `programovanie` returns
  `pokracovat` (NOT `schvalit`) when a todo task remains, and `schvalit` (NOT `pokracovat`) when
  all_tasks_done; `apply_action("pokracovat")` resumes the build round from awaiting_manazer-with-todo.
  Full `pytest` from root.
- Bug 2: PipelineActivityFeed renders a long line WITHOUT the `truncate` class (wraps) — assert the class
  set / that a long line isn't clipped to one row. FE build+lint+test.
