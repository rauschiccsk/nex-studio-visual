# NEX Studio — Plán úloh (STEP 3): implementačný podklad

> Detailný návrh kroku 3 „Plán úloh" — po schválení Špecifikácie partner v rozhovore zostaví mapu práce.
> Nadväzuje na REDESIGN.md §3 krok 5, BUILD-PLAN.md krok 3, SPINE-DESIGN.md, STEP2-SPEC-DESIGN.md.
> Grounded v reálnom kóde (v2.0.0-dev, HEAD 5751862). Prešiel DVOMI kolami revízie; adversariálny verdikt: READY.
> Podklad pre Implementera — Implementer číta tento dokument; dispatch prompt je krátky a odkazuje sem.

## Po ľudsky (pre Manažéra projektu)

Keď Manažér schváli Špecifikáciu, partner z nej v tom istom rozhovore zostaví Plán úloh — mapu práce.
V pravom paneli Riadiaceho centra v troch vrstvách: vždy vidno názov úlohy + stav; pod tým krátke ľudské
vysvetlenie každej úlohy; technický detail (súbory/funkcie) len po rozkliknutí. Jeden zdroj pravdy = skutočné
riadky úloh v databáze. Žiadna samostatná „Auditor brána" pred stavbou — dokončenie plánu je obyčajné
„dohodli sme sa" v rozhovore. Kľúčové: plán sa NEparsuje z jedného ťahu (to by pri veľkom pláne pretieklo) —
používa OVERENÚ postupnú mašinériu (kostra → priebeh po častiach). Aditívne; starý (mode NULL) tok ostáva
byte-identický.

## Rozhodnutia (Manažér projektu 2026-07-04)

- **MD-1 spúšťač plánu → (A) samostatné tlačidlo „Zostaviť plán".** Nová akcia `zostav_plan`, ktorú board
  route ponúkne LEN keď je build v režime rozhovoru, Špecifikácia je schválená a plán ešte neexistuje.
  Čestné-z-konštrukcie (ako „Schváliť Špecifikáciu"), trvalé cez reštart (marker v DB). NIE automaticky.
- **MD-2 úprava plánu → (A) vždy prečítať aktuálnu `specification.md` a prepísať plán na mieste** (SAVEPOINT
  drop-and-recreate). Špecifikácia je jediný zdroj pravdy; nikdy sa nerozíde so schváleným zadaním.

## Prehľad

**Backend:** Stage arg threads the phase everywhere; board route gates the button; durable marker drives the trigger; each node gets a plain line.

**Frontend:** Three-layer rail; plain line with empty placeholder; button only when offered.

**Dáta:** Nullable plain_description Text on Epic Feat Task. Migration 080 down_revision 079. Schemas and services carry it. Models and migration together.

## Opravy zapracované (2 kolá revízie — kritik: READY)
- FIX1 honest stage: _generate_incremental_plan takes a stage arg into every _record_message stage column AND payload phase (4124/4132 4193/4198 4228/4240 4252/4257), the assembled PipelineStatusBlock stage (4209), _invoke_plan_pass stage kw (2732/2737/2721), _settle_plan_pass_failure stage kw (4049/4054), _write_task_plan stage (1987/1992). Conversation round message.stage==payload phase==block stage==priprava; navrh byte-identical (design-doc 1894/1908 and auditor-upfront 4352/4515 are OUTSIDE the extract); _fold_task_plan_into_navrh becomes a wrapper stage navrh. Test asserts payload phase priprava and navrh unchanged.
- FIX2 button split like schvalit: determine_available_actions is state-only so it adds zostav_plan UNCONDITIONALLY in the priprava branch; the pipeline.py board route post-filters it unless mode conversation AND spec_approved AND NOT navrh_plan_materialized, beside the schvalit filter 104-110, reusing spec_approved. No DB read in determine_available_actions; apply_action authoritative. The prior contradictory clause is removed.
- FIX3 restart-safe trigger: dispatch_directive/directive_for_action already return None for zostav_plan (1434, any action not in uprav/ask/answer); run_conversation_turn delegates SOLELY on the durable kind directive compose_plan marker (a DB read at the top, restart-safe; the in-memory directive is None and lost on restart), never on the in-memory directive arg. No branch added.
- FIX4 epic plain_description weak link: _SKELETON_EXAMPLE 1311 and _FEAT_TASKS_EXAMPLE 1321 are updated to SHOW plain_description on epic, feat and task; directives _task_plan_skeleton_directive 1338 and _task_plan_feat_directive 1378 instruct a jargon-free one-liner per node; the Epic ORM has no technical description so plain_description is its ONLY prose. Field default empty so an omission parses. FE fallback: empty shows a muted (bez ludskeho vysvetlenia) placeholder, never the technical description, asserted in vitest.

## Úlohy pre Implementera (poradie)
1. **column migration 080 schemas services**
   - Nullable Text plain_description on Epic Feat Task (Epic has no description); 080 down_revision 079 ADD/DROP COLUMN IF EXISTS mirror 079; plain_description Optional None on 3 Create + 3 Read; services create pass it (epic 243, feat 226, task 257). Models+migration together.
   - Súbory: `backend/db/models/tasks.py`, `migrations/versions/080_task_plan_plain_description.py`, `backend/schemas/epic.py`, `backend/schemas/feat.py`, `backend/schemas/task.py`, `backend/services/epic.py`, `backend/services/feat.py`, `backend/services/task.py`
   - Overenie: pytest tests/test_alembic_migrations.py tests/test_migration_versions.py tests/ -k 'epic or feat or task' -x
2. **FIX1 stage everywhere + FIX4 examples and directives**
   - plain_description default empty on pipeline_status 89/100/109/151/160. stage kw default navrh on _invoke_plan_pass (2732 stage, 2737 payload phase, 2721 _audit_lost_work), _settle_plan_pass_failure (4049, 4054), _write_task_plan (1987, 1992, plus plain_description into Create 1943/1953/1964), _render_task_plan_md 1721 + _write_task_plan_doc 1763 drop the Auditor Navrh clause 1753-1755 for non-navrh. Extract 4095-4266 into async _generate_incremental_plan(stage); thread stage into 4124/4132 4193/4198 4209 4228/4240 4252/4257 + both plan-pass 4098/4145 + both settle 4114/4159; copy epic/feat/task plain_description in the assembly; _fold becomes wrapper stage navrh. FIX4 _SKELETON_EXAMPLE 1311 (epic+feat), _FEAT_TASKS_EXAMPLE 1321 (task), directives 1338/1378. Do not touch 1894/1908 or 4352/4515.
   - Súbory: `backend/services/pipeline_status.py`, `backend/services/orchestrator.py`
   - Overenie: pytest tests/test_orchestrator_v2_navrh.py tests/test_orchestrator_token_stop.py tests/test_pipeline_schemas.py -x
3. **FIX2 FIX3 zostav_plan action + plan round + routing + post-filter + seam**
   - orchestrator.spec_approved from exists 121-128; zostav_plan in _ACTIONS 411 (not _ADVANCING); apply_action guard conversation+spec_approved+not navrh_plan_materialized (raise else) + durable kind directive compose_plan marker (manazer->ai_agent, stage priprava) + _begin_dispatch; _run_conversation_plan_round points at _priprava_spec_rel like _navrh_directive 894, _generate_incremental_plan stage priprava, settle awaiting_manazer current_stage unchanged no advance; run_conversation_turn top reads latest unanswered marker, compose_plan truthy delegate else reply path (dispatch_directive/directive_for_action unchanged 1434); determine_available_actions add zostav_plan unconditionally in priprava; board route post-filter unless conversation+spec_approved+not navrh_plan_materialized mirror 104-110; get_task_plan plain_description task 300-308, description+plain_description feat 314-320, plain_description epic 325-329.
   - Súbory: `backend/services/orchestrator.py`, `backend/api/routes/pipeline.py`, `backend/api/routes/versions.py`
   - Overenie: pytest tests/test_conversation_task_plan.py tests/test_orchestrator_v2_routes.py tests/test_conversation_spine.py -x
4. **backend tests + FE + full self-verify**
   - tests/test_conversation_task_plan.py (seed conversation like test_conversation_spine; mock invoke_claude+_split_claude_result+_resolve_orch_session+_resolve_dispatch_overrides like navrh _stub_plan_passes with plain_description; DB 9178): FIX1 message.stage AND payload phase priprava vs navrh unchanged; FIX2 zostav_plan only under 3 DB conditions + apply_action raises each; FIX4 empty+populated plain_description; no advance; run_dispatch never entered (patch raise); second zostav_plan replaces in place; navrh byte-identical. FE: plain_description on 3 node types + description on FeatNode; zostav_plan in PipelineActionName 117-128; rewrite PlanUlohRail three-layer (salvage getTaskPlan+refetch+localStorage from TaskPlanPanel; L1 SpecMarkdown empty->muted placeholder never technical; trigger on available_actions); wire messages/board/onBoard in RiadiaceCentrumPage 142; vitest. Then full pytest + FE type-check/build/vitest.
   - Súbory: `tests/test_conversation_task_plan.py`, `frontend/src/types/task-plan.ts`, `frontend/src/services/api/pipeline.ts`, `frontend/src/components/riadiace/PlanUlohRail.tsx`, `frontend/src/pages/RiadiaceCentrumPage.tsx`, `frontend/src/__tests__/components/test_PlanUlohRail.test.tsx`
   - Overenie: full pytest green and npm run type-check and npm run build and npx vitest run

## Súbory (súhrn)
- `modify` `backend/services/orchestrator.py` — FIX1 stage arg, FIX4 examples, plan round, zostav_plan
- `modify` `backend/api/routes/pipeline.py` — FIX2 post-filter, spec_approved
- `modify` `backend/services/pipeline_status.py` — plain_description schemas 89 100 109 151 160
- `modify` `backend/db/models/tasks.py` — plain_description column
- `modify` `backend/api/routes/versions.py` — get_task_plan plain_description 300-329
- `modify` `frontend/src/components/riadiace/PlanUlohRail.tsx` — three-layer map with placeholder
- `create` `tests/test_conversation_task_plan.py` — FIX1-4 tests DB 9178

## Overenie
TEST DB 9178 only, never PROD 9198. No docker compose up. FIX1 message.stage AND payload phase priprava, navrh unchanged. FIX2 button only under conversation+spec_approved+not navrh_plan_materialized, apply_action raises each. FIX3 dispatch_directive None, delegate on marker. FIX4 non-empty plain_description, FE placeholder not technical. Plan round no Auditor clause, no advance, run_dispatch never entered, second replace. Full pytest (v2_navrh token_stop v2_routes conversation_spine pipeline_schemas). FE type-check build vitest.

## Kritik — poznámky (READY)
Adversarially re-verified against real code on v2.0.0-dev at HEAD 5751862 (confirmed). All 4 remaining items are genuinely fixed:

FIX1 (honest stage everywhere) — FIXED. Verified the hardcoded sites the fix must reach: _settle_plan_pass_failure payload phase at orchestrator.py:4054 (+ needs new stage kw at 4049), the fold-body payload={"phase":"navrh"} at 4132/4198/4240/4257 with their stage columns, the assembled PipelineStatusBlock(stage="navrh") at 4209, _invoke_plan_pass stage 2732 + payload phase 2737 + _audit_lost_work(stage="navrh") 2721, and _write_task_plan 1987/1992. The design threads `stage` into ALL of them (stage column AND payload["phase"] AND assembled block AND both pass-helpers), states the invariant message.stage==payload['phase']==block.stage==stage, keeps legacy 'navrh' byte-identical, and correctly excludes the out-of-extract design-doc/auditor-upfront sites. Test asserts payload['phase']=='priprava' on the conversation round and 'navrh' unchanged. Closes critique #1.

FIX2 (button split like schvalit) — FIXED. determine_available_actions is state-only (docstring 467-470; sole backend caller is pipeline.py:103 — no leak path) so the design adds zostav_plan UNCONDITIONALLY in the priprava branch and POST-FILTERS in the board route beside the schvalit filter at pipeline.py:104-110 (drop unless mode=='conversation' AND spec_approved AND NOT navrh_plan_materialized). No DB read inside determine_available_actions; apply_action stays authoritative. The prior contradiction is removed. state.mode/current_stage are both in route scope. Closes critique #2.

FIX3 (dispatch_directive note + restart-safe trigger) — FIXED. Verified directive_for_action returns None for any action not in {uprav,ask,answer} (orchestrator.py:1452), so zostav_plan yields a None directive; the design delegates SOLELY on the durable kind=directive payload.compose_plan DB marker read at the top of run_conversation_turn, never the in-memory directive arg (lost on restart). Seam verified end-to-end: _begin_dispatch in priprava is non-no-op (STAGE_ACTOR['priprava']=='ai_agent', 357) and arms agent_working+dispatch_in_flight; pipeline_runner._run routes mode=='conversation' → run_conversation_turn (192-195); the plan round settles awaiting_manazer so chain_limit stays 0 and run_dispatch is never entered (a real invariant — auto-chain gated on status=='agent_working' at 216/242). Closes critique #3.

FIX4 (epic/feat/task plain_description examples + FE muted fallback) — FIXED. Verified _SKELETON_EXAMPLE (1311) shows epic+feat with NO plain_description and the skeleton directive (1341) gives epic only `title` — Epic ORM genuinely has no description column (only Feat:66/Task:96 do), so plain_description is the epic's ONLY prose. The design updates BOTH _SKELETON_EXAMPLE and _FEAT_TASKS_EXAMPLE (1321) to SHOW plain_description on epic/feat/task and updates both directives (1338/1378) to instruct a jargon-free one-liner per node, keeps the field default empty so omissions parse, and enforces the FE muted "(bez ľudského vysvetlenia)" placeholder that NEVER falls back to technical text. Schema threading is correct: plain_description on TaskPlanTask(89)/TaskPlanSkeletonFeat(151)/TaskPlanSkeletonEpic(160) auto-derives into the generating passes via model_json_schema() (203-204), TaskPlanFeatTasks reuses TaskPlanTask (196), and the assembly (4172-4188) copies it; get_task_plan passthrough adds it to task(300-308)/feat(313-320, which also currently lacks description)/epic(324-329). Closes critique #4.

Standing invariants all hold: ONE source of truth (specification.md single copy; task rows the plan source); manager-map honesty (plain default, muted fallback never technical, technical under expand); sole-writer + append-only + parse-retry (run_conversation_turn always via invoke_agent_with_parse_retry; _write_task_plan SAVEPOINT scoped-delete replace); additive/non-breaking (legacy mode-NULL byte-identical; migration 080 down_revision '079' present at HEAD, mirrors 079's idempotent ADD/DROP IF [NOT] EXISTS; plain_description nullable vs the NOT NULL Feat/Task description); reuse of the incremental machinery (extract of the proven skeleton+per-feat passes, no single-turn full-tree); no fake boundary (durable DB marker restart-safe where the in-memory directive arg is not); app runnable after each task (task 1 migration+models together, task 2 additive default, tasks 3-4 wire the new path without touching legacy). Test harness grounded: test_conversation_spine.py exists as the home, test_conversation_task_plan.py to be created, _stub_plan_passes mock pattern (invoke_claude + _split_claude_result) confirmed in test_orchestrator_v2_navrh.py, _db_guard hard-abort on non-distinct DB present.

Zero blocker/major issues remain. The two minor issues are documentation-hygiene (polluted summary_plain field; approximate line numbers) and do not affect implementability — VERDICT: ready.

> Pozn.: citované čísla riadkov ber ako približné začiatky rozsahov — pravé kotvy sú názvy funkcií/symbolov.