# NEX Studio — Programovanie (STEP 4): implementačný podklad

> Detailný návrh kroku 4 „Programovanie" — po zostavení plánu partner programuje úlohu po úlohe v rozhovore.
> Nadväzuje na REDESIGN.md §3 krok 6, BUILD-PLAN.md krok 4, SPINE/STEP2/STEP3 design.
> Grounded v reálnom kóde (v2.0.0-dev, HEAD 7a67a2b). Prešiel revíziou; adversariálny verdikt: READY.
> Podklad pre Implementera — Implementer číta tento dokument; dispatch prompt je krátky a odkazuje sem.

## Po ľudsky (pre Manažéra projektu)

Keď je Plán úloh hotový, Manažér klikne tlačidlo „Spustiť stavbu" a partner programuje úlohu po úlohe
v rozhovorovom režime. Manažér to vidí NAŽIVO: plán sa plní (Čaká → Prebieha → Hotovo), hore banner
„Práve robím: #N názov". Ak sa vynorí rozhodnutie/nejasnosť, partner ho vysvetlí a opýta sa priamo
v rozhovore — JEDNU otázku naraz, s odporúčaním (existujúca blocked/answer mašinéria). Po prekročení
token-limitu sa stavba sama pozastaví, napíše prečo, a keď je Manažér preč, pošle Telegram; pokračuje sa
tlačidlom „Pokračovať v stavbe". Po dokončení všetkých úloh príde obyčajné oznámenie „Programovanie
dokončené — pokračujeme v rozhovore" (žiadny audítorský verdikt; kontrola je STEP 5). KĽÚČOVÉ: reuse
CELEJ overenej stavacej mašinérie (`_run_build_round` bez zmeny) — STEP 4 len prepne fázu
priprava → programovanie (mode ostáva conversation); staré (legacy) stavby ostávajú byte-identické.

## Rozhodnutia (Manažér projektu 2026-07-04)

- **MD-A = (A) presunúť fázu priprava→programovanie a použiť existujúci `_run_build_round` bez zmeny.**
  Token-stop, slučka úloha-po-úlohe, mid-build otázky, pauza, pokračovanie aj „práve robím" sú už postavené
  a otestované okolo fázy programovanie — presun ich rozsvieti zadarmo. `_run_build_round` nečíta mode.
- **MD-B = (A) rozvetviť ukončovací chvost `_run_build_round` podľa režimu** — pre conversation preskočiť
  fázový prechod, vrátiť fázu na priprava, usadiť awaiting_manazer + jedno obyčajné oznámenie. Legacy byte-identický.
- **MD-C = (A) mid-build otázka cez existujúcu blocked/answer mašinériu** — jedno usadenie, jedna otázka,
  žiadny paralelný povrch.
- **MD-D = (A) názov tlačidla „Spustiť stavbu"** (akcia `spustit_stavbu`) — číta sa ako ľudský akt, páruje
  sa s „Pokračovať v stavbe", odlišuje akciu od názvu fázy.

## Opravy zapracované (revízia — kritik: READY)

- MAJOR two-layer mirroring zostav_plan, layer 1 pipeline board drops schvalit verdict when mode conversation determine adds schvalit 513-516 verdict 517-520, layer 2 apply_action schvalit 6640 raises for conversation before 6644 guard, closes mid-build settles 6335-6339 6326-6329 6276 5921-5924
- MINOR approve_spec pipeline board drops approve_spec when mode conversation and spec_approved mirrors zostav_plan 119-126 reuses spec_approved 107 removes phantom SpecApprovalBar after tail resets determine 508
- MINOR marker no code change start_build is breadcrumb not trigger nothing reads it _run_build_round starts from get_next_todo_task 6252 trigger is durable current_stage programovanie 3848-3849 plus recover 5895 CTA 5921-5924
- Anchors HEAD 7a67a2b _run_build_round 6118 completion tail 6252-6264 determine 467 schvalit 513-516 apply_action 6431 zostav_plan 6612 schvalit 6640 navrh_plan_materialized 538 spec_approved 560 _record_message 673 _begin_dispatch 3044 drain_relay_turn 2227-2229 run_dispatch 3848-3849 _settle_phase_boundary 5811 recover 5895 pipeline 107 119-126 pipeline_runner 192-195
- Kept sound MD-A MD-B MD-C MD-D two routing changes four FE pieces scope STEP 4

## Backend

Trigger MOVES current_stage priprava to programovanie mode conversation, _run_build_round 6118 UNCHANGED, spustit_stavbu mirrors zostav_plan 6612, run_dispatch to _run_build_round 3848-3849, MAJOR board drops schvalit verdict plus apply_action schvalit 6640 raises for conversation, MINOR board drops approve_spec 107, completion tail 6252-6264 conversation SKIPs _settle_phase_boundary 5811, routing 192-195 plus 2227-2229 conversation only when stage not programovanie

## Frontend

PlanUlohRail.tsx CurrentBuildBanner from board.current_task pipeline.ts 95, TRIGGER mirror zostav_plan 231-277 canProgram includes spustit_stavbu, QUESTION reuses ConversationThread, TOKEN-STOP canResume includes pokracovat plus amber note, add spustit_stavbu to union 117-129, static nginx type-check build vitest

## Konzultácia + token-stop

Reuse blocked answer, result kind question blocked 6331-6339 to blocked agent_question, answer 6759 re-dispatches, one settle one question, token-stop seam 6209-6250 to paused plus ONE note, resume pokracovat 6929, test mode conversation THROUGH THE RUNNER 192, restart recover 5895 CTA 5921-5924 marker not consulted

## Úlohy pre Implementera (poradie)
1. **Backend orchestrator**
   - spustit_stavbu in _ACTIONS 411-436, determine priprava 506-512 add, apply_action 6612 guards plus breadcrumb 673 plus programovanie plus _begin_dispatch 3044, _build_started helper, MAJOR schvalit 6640 raise for conversation, completion tail 6252-6264 branched on mode, drain_relay_turn 2227-2229 stage check
   - Súbory: `/opt/projects/nex-studio/backend/services/orchestrator.py`
   - Overenie: cd backend && ruff check services/orchestrator.py
2. **Backend pipeline plus runner**
   - pipeline.py three post-filters spustit_stavbu plus drop schvalit verdict plus drop approve_spec 107, pipeline_runner _run 192-195 stage check
   - Súbory: `/opt/projects/nex-studio/backend/api/routes/pipeline.py`, `/opt/projects/nex-studio/backend/services/pipeline_runner.py`
   - Overenie: cd backend && ruff check api/routes/pipeline.py services/pipeline_runner.py
3. **Backend tests plus FULL pytest**
   - test_conversation_programming.py a-g plus MAJOR-1 board omits schvalit verdict plus MAJOR-2 apply_action schvalit raises plus MINOR-1 board omits approve_spec plus MINOR-3 marker not gating, FULL pytest shared changes
   - Súbory: `/opt/projects/nex-studio/tests/test_conversation_programming.py`
   - Overenie: cd backend && python -m pytest -q
4. **Frontend**
   - pipeline.ts union 117-129, PlanUlohRail CurrentBuildBanner plus ladder 231-277 canProgram canResume, vitest a-e plus REVISION f, type-check build vitest
   - Súbory: `/opt/projects/nex-studio/frontend/src/components/riadiace/PlanUlohRail.tsx`
   - Overenie: cd frontend && npm run type-check && npm run build && npx vitest run

## Súbory (súhrn)
- `modify` `/opt/projects/nex-studio/backend/services/orchestrator.py` — spustit_stavbu action plus apply_action 6612 plus _build_started plus MAJOR schvalit raise 6640 plus completion tail 6252-6264 branched on mode plus drain_relay_turn 2227-2229
- `modify` `/opt/projects/nex-studio/backend/services/pipeline_runner.py` — _run 192-195 conversation only when current_stage not programovanie
- `modify` `/opt/projects/nex-studio/backend/api/routes/pipeline.py` — three board post-filters 119-126 spustit_stavbu plus drop schvalit verdict plus drop approve_spec 107
- `modify` `/opt/projects/nex-studio/frontend/src/services/api/pipeline.ts` — add spustit_stavbu to union 117-129
- `modify` `/opt/projects/nex-studio/frontend/src/components/riadiace/PlanUlohRail.tsx` — CurrentBuildBanner plus ladder 231-277 canProgram canResume amber note, refetch 196-215 unchanged
- `create` `/opt/projects/nex-studio/tests/test_conversation_programming.py` — backend test DB 9178 SAVEPOINT seeds 85 79 197 covers a-g plus REVISION
- `modify` `/opt/projects/nex-studio/frontend/src/__tests__/components/test_PlanUlohRail.test.tsx` — vitest a-e plus REVISION f omit schvalit omit approve_spec

## Overenie

Test DB 9178 NEVER PROD 9198 SAVEPOINT PROD-guard conftest 139 157 193 must pass no docker up, a trigger only under mode spec plan not-started, b routing conversation programovanie to run_dispatch priprava to run_conversation_turn STEP-3 340-359 holds, c question to blocked answer, d token-stop via RUNNER, e completion None to priprava NO _settle_phase_boundary, f drain_relay_turn to run_dispatch, g sole-writer, MAJOR-1 omits schvalit verdict, MAJOR-2 apply_action schvalit raises, MINOR-1 omits approve_spec, MINOR-3 marker not gating, FULL pytest shared changes token_stop plus task_plan green, frontend type-check build vitest

## Kritik — poznámky (READY)

Adversarially re-checked the corrected STEP 4 design against the REAL code on v2.0.0-dev (HEAD 7a67a2b). All three MANDATORY fixes are correctly and completely addressed; zero blocker/major issues remain.

VERIFIED SEAMS (all anchors in the corrected design's fixes_applied list match real code):
- determine_available_actions :467 — navrh/programovanie branch (:513-516) adds ONLY `schvalit`; verdict is verifikacia-only (:517-520). State-only, no mode check → confirms the MAJOR exposure.
- apply_action('schvalit') :6640 — guard at :6644 accepts programovanie with NO mode check; :6673 _next_stage('programovanie')→verifikacia + _begin_dispatch → drops the conversation build into the Auditor phase. Exactly the corruption the critique described.
- _run_build_round :6118; token-stop seam :6209-6250 (reads no mode); completion tail :6252-6264 with _settle_phase_boundary :6258.
- _settle_phase_boundary :5811 — for a new_version conversation build at programovanie returns False at :5863-5864 (mandatory phase gate) → would settle awaiting_manazer at programovanie, where determine re-offers schvalit. Confirms BOTH the MD-B completion-tail branch AND the MAJOR schvalit guard are required.
- Board post-filters pipeline.py: schvalit-empty-plan :108-114, zostav_plan :119-126 (the mirror shape), spec_approved :107.
- run_dispatch→_run_build_round :3848-3849 (routes on STAGE, not marker — validates MINOR-3); _begin_dispatch :3044 (ai_agent actor at programovanie); zostav_plan branch :6612; recover_orphaned_builds :5895/CTA :5921-5924; drain_relay_turn :2227-2229; pipeline_runner _run :192-195; navrh_plan_materialized :538; spec_approved :560; _record_message :673; apply_action :6431; _ACTIONS :411-436; _ADVANCING_ACTIONS :441-449 (excludes zostav_plan/overit_znovu — spustit_stavbu correctly follows).

FIX 1 (MAJOR) CONFIRMED: two-layer belt mirroring zostav_plan — board post-filter drops schvalit (defensively also verdict) when mode==conversation; apply_action('schvalit') raises for conversation before the :6644 stage-guard. Enumeration complete: schvalit is the sole legacy phase-gate verb in the navrh/programovanie branch; verdict is verifikacia-only and unreachable once schvalit is blocked, but the design drops it too. Reachable settle states that would have offered schvalit (blocked agent_question :6331-6339; awaiting_manazer lost_work :6326 / task-failed; restart-recovery :5921-5924) are all covered — note the paused/token-stop state already returns only {pokracovat,uprav} (:487-490) so it was never exposed. Tests MAJOR-1 (board omits) + MAJOR-2 (apply raises).

FIX 2 (MINOR) CONFIRMED: approve_spec board post-filter drops it when mode==conversation AND spec_approved true; correctly still offered pre-approval, hidden post-approval; also cleans a pre-existing latent re-offer. Test MINOR-1.

FIX 3 (MINOR) CONFIRMED: marker reframed as audit breadcrumb (sole-writer/append-only); actual trigger + restart-safety = durable current_stage='programovanie' + _begin_dispatch, runner routes on STAGE via run_dispatch→_run_build_round; orphan recovery + pokracovat give restart-safety; _run_build_round must NOT read the marker. Test MINOR-3.

STANDING INVARIANTS all hold: _run_build_round reused verbatim (no fork, reads no mode); sole-writer/append-only/parse-retry preserved; live progress via append-only log + WS + STEP-3 rail refetch; one-at-a-time consult structural (one blocked/agent_question settle = one question); token-stop pauses this path + Telegram + pokracovat resume (gated to programovanie); gate→plain completion via MD-B branch; additive/legacy mode-NULL byte-identical (completion-tail branch + two routing predicates leave NULL path untouched); honest-by-construction trigger via board post-filter (mode==conversation AND spec_approved AND navrh_plan_materialized AND NOT _build_started); no fake boundary; app runnable after each task (per-task ruff/pytest/type-check/build/vitest). STEP-3 invariant test (test_conversation_task_plan.py:340-359) still holds because zostav_plan keeps stage=priprava, so the new mode+stage routing predicate still routes the plan round to run_conversation_turn.

One non-blocking note (not an issue, no revision needed): the design JSON prose occasionally writes "pipeline.py 107" as the approve_spec-filter anchor — :107 is where spec_approved is computed; the new approve_spec filter is added beside the zostav_plan filter (:119-126). The fixes_applied text already states it "mirrors zostav_plan 119-126 reuses spec_approved 107", which the Implementer will read correctly. Scope stayed strictly on STEP 4; nothing already-sound was changed.

> Pozn.: citované čísla riadkov ber ako približné začiatky rozsahov — pravé kotvy sú názvy funkcií/symbolov.