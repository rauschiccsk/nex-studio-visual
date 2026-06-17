# NEX Studio v0.7.1 — Cleanup

> Design of record. Authored by **Dedo** (cross-project: Dedo design + nex-implementer build). A small cleanup
> version: finish the last two NEX Studio polish items so a fresh **new-project end-to-end test** runs clean.
> Grounded by `polish-punchlist-grounding` (every anchor verified against real code/DB/git). Two items the
> ledger originally implied — KB ghost-dir on delete + per-agent Telegram — were verified **non-issues** (already
> handled) and are NOT in this version. NEX Ledger backlog (unified user-model/login, settings-kit pilot) is a
> different project, explicitly out of scope (Director 2026-06-17).

## CR breakdown

### P1 — Gate E "Phase 5": decommission the dead `dialogue_*` layer
**State (verified):** the `/dialogue` FE is removed (CR-NS-065), and Gate E data was backfilled to
`pipeline_message` (migration `052`). BUT the `dialogue_sessions` + `dialogue_messages` tables still exist in the
DB, and the dead backend layer is still present + registered: `backend/db/models/dialogue.py` (+ `__init__.py`
exports), `backend/services/dialogue.py`, `backend/api/routes/dialogue.py` (+ `main.py` import & `include_router`
~`:238`), `backend/schemas/dialogue.py`. No drop migration exists. The drift-test passes only because it builds
fresh metadata from the still-present models — it does NOT catch "live tables that should be gone".

**Fix:**
1. **Migration `068`** (`Revises: 067`): `op.drop_table("dialogue_messages")` then `op.drop_table("dialogue_sessions")`
   (children first); a correct `downgrade` recreating them.
2. **Remove the ORM models** — delete `backend/db/models/dialogue.py` + its `__init__.py` exports.
3. **Remove the dead service layer** — delete `backend/services/dialogue.py`, `backend/api/routes/dialogue.py`,
   `backend/schemas/dialogue.py`; remove from `backend/main.py`: the route import + `include_router` (~`:16`/`:238`),
   AND the `dialogue_service` import (`:39`) + its **startup call `dialogue_service.mark_orphaned_on_startup(db)`
   (`main.py:159`)** — a still-live consumer (self-audit found it); it's dead post-cutover, remove it with the service.
4. **Test cleanup** — remove/adjust `test_dialogue_service.py`; strip dialogue-specific assertions from
   `test_gate_e_backfill.py` (keep the backfill→pipeline_message validation).

**Critical seam (replay-safety):** migration `052` (the backfill) reads the dialogue tables. **Verify `052` (and
any migration ≤067) does NOT import the dialogue ORM models** — migrations must use literal table defs / raw SQL,
so deleting the models in P1 does not break `alembic upgrade head` from a clean DB. If `052` imports the models,
refactor it to a literal `sa.table(...)` first (part of P1). Data: already backfilled → no data loss.

### P2 — RAG reindex on live-document writes
**State (verified):** the live-doc writes — `STATUS.md` / `HISTORY.md` on project create (`projects.py:481`),
task update (`tasks.py:287-288`), feat update (`feats.py:286`), module events (`project_modules.py:193/253/318`)
— go through `LiveDocumentService` (`append_history` / `regenerate_status`) but **never trigger RAG reindex**,
while the `/knowledge` routes (`knowledge.py:206/261/311`) DO. So Qdrant drifts from the live KB files —
violating CLAUDE.md §13 ("žiadna KB zmena bez následného reindexu").

**Fix:** integrate `RAGIndexer` into `LiveDocumentService` (one place — DRY): an optional `indexer` dep (like the
existing optional `writer`); after `_writer.save(...)` in `regenerate_status()` and `_writer.append(...)` in
`append_history()`, reindex the written file. Propagate the indexer to the 5 call sites. **Graceful on failure**
— a RAG error logs a warning and does NOT fail the endpoint (mirror `knowledge.py:211-220`). The `/knowledge`
routes are unchanged (already reindex).

## CR order & build
P1 then P2 (independent; P1 first — it's the bigger deletion + migration). Each: nex-implementer build → Dedo
independent verify (full pytest + adversarial audit) → single push at version end → CI (incl. R2 drift-gate +
migration 068 deploy) → deploy verify.

## Seams to preserve
- P1: migration replay-safe (no ORM imports in migrations — verified; `052` uses raw `sa.text` SQL); `052`
  backfill validation kept; the drift-test must pass AFTER both the drop migration AND the model removal (they
  must land together). **Do NOT touch the false-match `dialogue` references** that are a DIFFERENT concept:
  `customer-dialogue.md` (the Gate E Customer spec doc path at `orchestrator.py:735/750`) + deprecation comments
  (`claude_agent.py:6`, `pipeline_status.py:281`, `App.tsx:57`) — only the `dialogue_*` table/model/service/route
  layer is removed.
- P2: never fail an endpoint on a RAG error (graceful, like `/knowledge`); `/knowledge` routes unchanged; the R2
  OpenAPI codegen is unaffected (no schema field change) — but run `npm run codegen` if any response model shifts.
- Both: new_version / cr / bug / fast_fix pipeline flows + the v0.7.0 R1-R4 work UNCHANGED (additive cleanup only).

## Test points
- P1: migration 068 applies on a clean DB (`alembic upgrade head`) + a correct downgrade; the drift-test passes
  with the models gone + tables dropped; `grep -ri dialogue backend/` → only comments, no active code; the app
  boots with no dialogue router.
- P2: each live-doc write (create / task / feat / module) fires a reindex (assert the indexer was called); a RAG
  failure logs + does NOT fail the endpoint; `/knowledge` routes still reindex.
