# NEX Studio — Kontrola (STEP 5): implementačný podklad

> Detailný návrh kroku 5 „Kontrola" — po Programovaní partner čestne prekontroluje vlastnú robotu.
> Nadväzuje na REDESIGN.md §3 krok 7 + §8, BUILD-PLAN.md krok 5, SPINE/STEP2/STEP3/STEP4 design.
> Grounded v reálnom kóde (v2.0.0-dev, HEAD d609205). Prešiel 2 kolami revízie; jadro potvrdené (žiadny blocker).
> Podklad pre Implementera — Implementer číta tento dokument; dispatch prompt je krátky a odkazuje sem.

## Po ľudsky (pre Manažéra projektu)

Keď Programovanie dobehne, objaví sa tlačidlo „Skontrolovať". Partner (ten istý AI Agent, čo písal kód)
NAOZAJ spustí appku v jednorazovom kontajneri (bez akéhokoľvek nasadenia) + prebehne akceptačné skúšky,
prečíta schválenú specification.md a napíše PO ĽUDSKY, čo je PEVNÉ a čo VRATKÉ — ako obyčajná správa
v rozhovore (kind='gate_report'), žiadna verdiktová karta. KĽÚČOVÉ: engine spustí dôkaz PRVÝ (nesfalšovateľné),
a keď je červené (appka nenaštartuje / neprejde), správa to čestne ukáže — partner nemôže tvrdiť „PASS", keď
stroj vidí, že je to rozbité. Kontrola NIČ nepodpisuje ani nenasadzuje — vždy vráti kormidlo Manažérovi.
Zostáva na stage='priprava' (neviditeľná pre release/deploy — verdikt na stage='verifikacia' by deploy čítal
ako PASS, čomu sa vyhýbame). Lišta fáz pre rozhovorovú stavbu ukazuje Špecifikácia → Plán → Programovanie →
Kontrola (odvodené z board signálov). Všetko aditívne; staré (legacy) buildy fungujú byte-identicky.

## Rozhodnutia (Manažér projektu 2026-07-05)

- **K-1 = (A) kontrola appku NAOZAJ spustí** — engine ju v izolovanom kontajneri naštartuje + otestuje (deploy-free _run_release_smoke), partnerovi dá reálny výsledok. NIE len „čítať a uvažovať". (Reálny beh = skutočnosť; oprava starej choroby DONE=pečiatka.)
- **K-2 = (A) dva štítky „Pevné/Vratké"** nad plnou správou (zelený/amber), plná ľudská správa ostáva pod nimi.
- **K-3 = (A) pri červenom ZASTAVIŤ a vrátiť Manažérovi** — kontrola nikdy sama nepodpíše Hotovo; opravu vedie Manažér (žiadna auto-slučka).
- **K-4 = (A) jedna kontrola na dokončenú stavbu** (nová stavba/oprava ju znovu otvorí) — honest-by-construction.
- **K-5 = (A) hĺbku pokrytia berieme základnú teraz** (reálny boot + akceptačný beh + anti-empty floor + partner porovná so specification.md); per-feature/negatívne pokrytie NIE je vynútené v rozhovorovom toku — čestne priznané, dotiahne sa neskôr.

## MUSÍ sa zapracovať (zvyšné 2 opravy z kritiky)

- **MAJOR — sprístupniť `mode` do frontendu.** Lišta fáz vetví na rozhovorový režim (`board.state.mode==="conversation"`), ale FE `PipelineStateRead` dnes `mode` NEnesie → nekompiluje sa. Pridať `mode: Optional[str] = None` do `PipelineStateRead` (backend/schemas/pipeline.py) A do FE typu `PipelineState`. Aditívne (`mode` už na modeli PipelineState existuje — len ho vystaviť v read schéme).
- **MINOR — oprava odkazu:** `_record_message` je na `orchestrator.py:704` (nie :645) — opraviť v podklade.


## Backend

Kontrola=NEW conversation round STAYS current_stage='priprava', gate_report -> INVISIBLE to _verifikacia_passed(orchestrator.py:1717-1729)/version_verified(:1752-1773)/deploy.list_verified_versions(backend/services/deploy.py:186-211 needs 'done':204). skontrolovat MIRRORS zostav_plan apply_action(:6685-6711) NOT spustit_stavbu(:6713-6746). _ACTIONS(:411-442) not _ADVANCING(:447-455)+actions.add(:522). Board post-filter pipeline.py :132-142 unless conversation+spec_approved(:107)+programming_complete+NOT kontrola_done. Guard(:6690-6695)+durable payload.check marker(:6700-6709). programming_complete=stage=programovanie∧notification∧payload.programming_complete(:6302-6311); kontrola_done=latest priprava kontrola gate_report seq>latest programming_complete seq. run_conversation_turn(:4029) 2nd marker beside compose_plan(:4066-4067): _pending_check_marker(copy :4113-4135)->_run_conversation_kontrola_round reuse _run_verifikacia_round SMOKE(:5195-5241) WITHOUT verdict/gate tail(:5280+): _declared_release_coverage(:3788->(0,0)); _run_release_smoke(:3738); notif stage='priprava'(:5197-5226+payload.kontrola); runtime_floor_red(:5241); smoke_block(:5228-5236); invoke_agent_with_parse_retry role=state.current_actor stage='priprava' recipient='manazer' extra_payload kontrola prompt=_kontrola_directive->chokepoint(:2447) gate_report(:2441). _kontrola_directive reads specification.md(:847). Sole-writer _record_message(:645); ConversationThread.tsx:76-100 plain bubble. SETTLE awaiting_manazer no _settle_phase_boundary/_next_stage(:4171-4172); red->kontrola_floor_red. ROUTING verify pipeline_runner._run(:197). ADDITIVE legacy UNTOUCHED.

## Frontend

RUNG PlanUlohRail.tsx(:318-376) canCheck(:266-271) else-if AFTER canPause rung(:363-375); skontrolovat->PipelineActionName pipeline.ts(:117-130). MAJOR-1 PhaseBar.tsx(:21-31) reads current_stage but redesign STAYS priprava; FE has NO mode (backend/schemas/pipeline.py:34+; RiadiaceCentrumPage.tsx:59). :128 board={board ?? null}; PhaseBar optional board derives specifikacia/plan/programovanie/kontrola from BOARD SIGNALS (PlanUlohRail :266-271/:273; NOT CurrentBuildBanner :132-146): kontrola=skontrolovat OR payload.kontrola msg OR (agent_working AND programming_complete notif); programovanie=pause/pokracovat/spustit_stavbu OR current_stage=programovanie; plan=spec_approved+no build/check; else specifikacia; reuse MARK_GLYPH/MARK_TONE/TONE_TEXT(:18-19); else phaseMarkFor BYTE-IDENTICAL; no PHASE_LABELS.verifikacia rename (cockpit/labels.ts:26-40). type-check+build+vitest ONLY, never PROD :9197/:9198.

## Čestnosť + dôkaz

H1 _run_release_smoke(:3738) BOOTS+acceptance records legs stage='priprava' BEFORE partner(:5195-5226); Bash allowed(:29-34). H2 runtime_floor_red(:5241)->kontrola_floor_red. H3 NAMED tests+SOLID/SHAKY; oracle specification.md(:847). MINOR-3(K-5): _declared_release_coverage(:3788-3799) reads navrh gate_report(:3775-3784) never produced by conversation->(0,0), floor(:3652)->ASSERTIONS_RUN>0 anti-empty; per-feature/negative NOT enforced; stated. MINOR-4: SOLELY programming_complete probe on stage='programovanie' notif(:6310); NO next_action hook(:6301/:6309). SOLE-WRITER(:645/:2447); no verdict/git-tag(:5292-5294).

## Súbory (11)
- `modify` `/opt/projects/nex-studio/backend/services/orchestrator.py` — skontrolovat _ACTIONS/actions.add(:522); probes(:6302-6311); apply_action mirror zostav_plan(:6685-6711); _pending_check_marker(:4113-4135); delegation :4066-4067; _run_conversation_kontrola_round(:3788/:3738/:5241); _kontrola_directive(:847).
- `modify` `/opt/projects/nex-studio/backend/api/routes/pipeline.py` — Board post-filter mirror :132-142 (reuse spec_approved :107).
- `modify` `/opt/projects/nex-studio/backend/services/pipeline_runner.py` — VERIFY ONLY :197.
- `create` `/opt/projects/nex-studio/tests/test_conversation_kontrola.py` — Backend test DB :9178 mirror test_conversation_programming.py + _stub_smoke.
- `modify` `/opt/projects/nex-studio/frontend/src/services/api/pipeline.ts` — skontrolovat in PipelineActionName(:117-130).
- `modify` `/opt/projects/nex-studio/frontend/src/pages/RiadiaceCentrumPage.tsx` — MAJOR-1 :128 board prop.
- `modify` `/opt/projects/nex-studio/frontend/src/components/riadiace/PhaseBar.tsx` — MAJOR-1 optional board->derived strip; else BYTE-IDENTICAL; no PHASE_LABELS.verifikacia rename.
- `modify` `/opt/projects/nex-studio/frontend/src/components/riadiace/PlanUlohRail.tsx` — canCheck(:266-271); rung AFTER canPause(:363-375).
- `modify` `/opt/projects/nex-studio/frontend/src/components/riadiace/ConversationThread.tsx` — OPTIONAL chips above :85 (K-2).
- `modify` `/opt/projects/nex-studio/frontend/src/__tests__/components/test_PlanUlohRail.test.tsx` — rung only when offered.
- `create` `/opt/projects/nex-studio/frontend/src/__tests__/components/test_PhaseBar.test.tsx` — conversation->labels+derived; legacy->4 phases.

## Úlohy pre Implementera (poradie)
1. **Probes**
   - programming_complete(:6302-6311)+kontrola_done beside _build_started(:570-588).
   - Súbory: `/opt/projects/nex-studio/backend/services/orchestrator.py`
   - Overenie: pytest green.
2. **Register skontrolovat**
   - _ACTIONS(:411-442) not _ADVANCING(:447-455); actions.add(:522).
   - Súbory: `/opt/projects/nex-studio/backend/services/orchestrator.py`
   - Overenie: offered at priprava.
3. **apply_action guard**
   - MIRROR zostav_plan(:6685-6711 NOT spustit_stavbu :6713-6746); payload.check marker(:6700-6709) stays priprava.
   - Súbory: `/opt/projects/nex-studio/backend/services/orchestrator.py`
   - Overenie: raises per precondition; stage unchanged.
4. **Board post-filter**
   - pipeline.py :132-142 unless conversation+spec_approved+programming_complete+not kontrola_done.
   - Súbory: `/opt/projects/nex-studio/backend/api/routes/pipeline.py`
   - Overenie: offered ONLY on all four.
5. **Kontrola round**
   - _pending_check_marker(:4113-4135); delegation :4066-4067; _run_conversation_kontrola_round(:3788/:3738/:5241, invoke role=state.current_actor stage='priprava' recipient='manazer', red->kontrola_floor_red); _kontrola_directive(:847) gate_report NIE verdict.
   - Súbory: `/opt/projects/nex-studio/backend/services/orchestrator.py`
   - Overenie: gate_report stage='priprava' NOT verdict/verifikacia; never _settle_phase_boundary/_next_stage.
6. **Verify routing**
   - pipeline_runner._run(:197).
   - Súbory: `/opt/projects/nex-studio/backend/services/pipeline_runner.py`
   - Overenie: grep.
7. **FE union+rung**
   - pipeline.ts(:117-130); PlanUlohRail canCheck(:266-271)+rung AFTER canPause(:363-375).
   - Súbory: `/opt/projects/nex-studio/frontend/src/services/api/pipeline.ts`, `/opt/projects/nex-studio/frontend/src/components/riadiace/PlanUlohRail.tsx`
   - Overenie: type-check+build.
8. **FE MAJOR-1 PhaseBar**
   - RiadiaceCentrumPage.tsx:128 board prop; PhaseBar optional board derived strip; else BYTE-IDENTICAL.
   - Súbory: `/opt/projects/nex-studio/frontend/src/pages/RiadiaceCentrumPage.tsx`, `/opt/projects/nex-studio/frontend/src/components/riadiace/PhaseBar.tsx`
   - Overenie: test_PhaseBar.test.tsx.
9. **Tests+suites**
   - test_conversation_kontrola.py; test_PhaseBar.test.tsx; FULL pytest+FE.
   - Súbory: `/opt/projects/nex-studio/tests/test_conversation_kontrola.py`
   - Overenie: pytest DB :9178 green; type-check+build+vitest green.

## Overenie

DB :9178 ONLY (conftest SAVEPOINT+_guard_prod_db_isolation); PROD NEVER touched; no docker (_run_release_smoke monkeypatched). test_conversation_kontrola.py mirror test_conversation_programming.py + _stub_smoke + STEP-4 completion notification. Cases: offered ONLY on conversation+spec_approved+programming_complete+not kontrola_done; apply_action raises per precondition, marker stays priprava; gate_report stage='priprava' payload.kontrola NOT verdict/verifikacia; RED->kontrola_floor_red; _settle_phase_boundary+_next_stage never called; SAFETY _verifikacia_passed/version_verified False+deploy.list_verified_versions excludes it, second refused, new programming_complete re-opens. FULL pytest DB :9178; confirm test_conversation_programming.py+backend/tests/integration/test_workflow_deploy_matrix.py green. FE type-check+build+vitest; test_PlanUlohRail.test.tsx+test_PhaseBar.test.tsx.