# F-007 — Task-Plan + Per-Task-Review uzol v cockpite

> NEX Studio v0.2.0 — obnova osvedčeného NEX Command princípu do agentového cockpitu.
> **Status:** DESIGN — posvätené Directorom 2026-06-07. Autor návrhu: Dedo.
> Implementácia: Implementer (plan-first, fázovo). Spec SK, kód EN.
> Podklad: grounded multi-agent rozbor (NEX Studio stav + NEX Command mechaniky + adaptácia).

## 1. Účel a motivácia

Cockpit dnes po Kontrole zákazníkom (Gate E) spustí **monolitický build**: Programátor
dostane jediný pokyn „naprogramuj celý projekt podľa návrhu" a vygeneruje všetko naraz,
potom to ide Audítorovi. **Chýba uzol, ktorý NEX Command má a osvedčil sa:**

1. **Žiadny zdrojový kód, kým nie je schválený TASK PLÁN** (rozklad EPIC → FEAT → TASK).
2. **Per-task review** — po každom tasku audit voči spec + porovnanie diffu oproti
   baseline commitu danej úlohy.
3. **Zlyhaný task zastaví pipeline** — nič sa nestavia na neoverenom základe.

Tento uzol sa stratil počas agentového redizajnu + cleanupu (migrácia 048 dropla starú
per-task mašinériu — delegations / execution_logs / guardian_reviews). Pre **regulovanú
podvojnú účtovnú knihu** (NEX Ledger ≈ 85–115 súborov, 12–17 kLOC, 3 deterministické
výpočtové jadrá) je monolitický diff **nepreskúmateľný** a tichá odchýlka prejde aj
Dual-Build testom (špec sám varuje pri prehodení strán NEX Genesis). Lepší model (Opus
4.8) zlepšuje **generovanie**, nie **review** — preto obnovujeme task-plán + per-task
audit, **hrubozrnne (modul = task)**, aby sme silný model využili (jeden task = veľký,
koherentný, auditovateľný turn).

## 2. Umiestnenie v pipeline

Task-plán ide **PO Gate E** (nie pred) — plán musí rozložiť **finálny** dizajn, a Gate E
dizajn mení (na NEX Ledger opravil 14 nálezov). Plán pred Gate E by bol zastaraný. Navyše
to sedí so súčasnou pozíciou NEX Ledger (Gate E hotová).

```
kickoff → gate_a → gate_b → gate_c → gate_d → gate_e
        → task_plan   ← NOVÝ: Návrhár rozloží finálny dizajn; Director schváli plán RAZ
        → build       ← TRANSFORMOVANÝ: plynulá per-task slučka (nie 1 opaque turn)
        → gate_g → release → done
```

`task_plan` sa vkladá na index 6 (`STAGE_ORDER`). Posun je pozičný (`_next_stage`), takže
`_next_stage("gate_e")` (orchestrator.py:1201 a end_gate_e:1384) sa **automaticky** prepojí
na `task_plan` — netreba editovať tieto call-sites. Registrácia stage = 4 lockstep miesta
(viď §9).

## 3. Roly — kto čo robí

| Aktivita | Rola | Zdôvodnenie |
|---|---|---|
| **Tvorba plánu** | **Návrhár** | Plán = dekompozícia schváleného dizajnu, nie nový dizajn. Vlastník dizajnu vlastní rozpad. Programátor NESMIE plánovať (Shu — kreativita zakázaná); Koordinátor nie je producent artefaktov. |
| **Implementácia tasku** | **Programátor** | Deterministický vykonávateľ jedného tasku za turn; brief + relevantná spec sekcia + cross-cutting rules injektnuté orchestrátorom. |
| **Per-task review** | **Audítor** (audit) + **Koordinátor** (relay) | Audítor robí audit-vs-spec scoped na deliverables tasku (Spec Compliance + Consistency). Koordinátor overí, že report je reálny, a relayne pri zlyhaní/zásahu. |
| **Director** | **schváli plán RAZ** + zasahuje len pri výnimke | Director ↔ Koordinátor výhradne; nikdy priamo Programátorovi (`fix` cestuje cez Koordinátora). |

## 4. Štruktúra plánu — VERSION → EPIC → FEAT → TASK, hrubozrnná

Celá 4-vrstvová hierarchia **už existuje v ORM** (`backend/db/models/versions.py`,
`tasks.py`) a je reusable — nie je to TODO:

| Vrstva | Tabuľka | Reuse | Doplniť |
|---|---|---|---|
| VERSION | `versions` | `version_number`, `status`, vzťah na epics | — |
| EPIC | `epics` | `module_id` (multi-module core), `version_id`, `number`, `title`, `status` | — |
| FEAT | `feats` | `epic_id`, `number`, `title`, `status`, `task_count`, `auto_fix_count` | — |
| TASK | `tasks` | `feat_id`, `number`, `title`, `description`, `task_type`, `status todo\|in_progress\|done\|failed`, `priority`, `checklist_type` | **`baseline_sha` String(40) nullable** (kotva diffu, prežije retry) |

Tieto tabuľky sú dnes **dormant voči orchestrátoru** (neimportuje ich). Build je 1 opaque
stage. Schéma je ready, write-path absentuje (§9).

**Granularita = hrubozrnná, modul = task.** Pre NEX Ledger ~6–8 taskov: import pipeline =
1 task; každé z 3 výpočtových jadier (hlavná kniha, 7-stĺpcová predvaha, uzávierka) = 1
task; + foundation (schema/migrácie, audit_log) + UI. **Nikdy netrhať** koherentný modul —
polovičný calc core sa nedá zmysluplne auditovať voči spec. Coarse tasky držia aj plynulý
beh + dohľad tractable.

**Cross-cutting pravidlá v KAŽDOM task brief** (regulated-ledger invarianty nevlastní
jeden modul; orchestrátor ich templatuje do každého dispatch directive): zdieľaná
transakčná hranica (partial writes zakázané), immutable audit záznam, scoping na firmu,
podvojnosť, sequence integrity, period locking, dane z konštánt. Návrhár ich kodifikuje
raz v pláne, orchestrátor injektuje do každého briefu.

## 5. Stage `task_plan` — plán sa schvaľuje RAZ

Beží ako gate: dispatch Návrhárovi → Návrhár dekomponuje finálny dizajn na EPIK/FEAT/TASK a
emituje ich ako **typovaný `plan` payload** v status bloku (§9) → orchestrátor ho
**deterministicky zapíše** do ORM (`_write_task_plan` — idempotentný *replace* epík verzie
pri re-pláne; atomicky alebo `blocked`, žiadny polovičný plán) → `awaiting_director`.
**Žiadny Koordinátor-judge turn** (CR-2 decision 2026-06-07): deterministický write-path JE
mechanická gate a **plán reviewuje Director** (schváli materializovaný strom RAZ — hrubozrnný
~6–8-task plán prečíta sám). Konzistentné s design-gate vzorom (gate_a–d sú Návrhár→Director
priamo; globálny Koordinátor-reroute design-gate je parkovaný). **Director schváli plán RAZ**
(`approve`). Potom sa do plánu per-task nezasahuje — beží plynulo (§6). Director môže plán
`vrátiť` Návrhárovi alebo `Konzultovať s Koordinátorom` (jeho INPUTY idú cez Koordinátora —
guard `current_stage in (gate_e, task_plan)`).

## 6. Stage `build` — plynulá per-task slučka (gate len pri výnimke)

**Kontrolný model (per Director 2026-06-07):** plán schválený raz → tasky bežia **plynule
za sebou**; orchestrátor sa zastaví na Directora **len pri výnimke** (zlyhaný task po
vyčerpaní auto-fixov, alebo Directorov zásah). **Žiadny per-task Director klik na úspešný
task** — Director **sleduje** (panel + activity, §7) a zasahuje podľa potreby.

Per-task cyklus (`_run_build_round`, near-copy `_run_gate_e_round`):

1. **Baseline** — `Task.baseline_sha` = repo HEAD pri dispatch-i tasku. **Fail-closed (CR-4.1):**
   ak sa baseline nedá zachytiť (HEAD nečitateľný, git zlyhá), **HALT** na `awaiting_director`
   (relay cez Koordinátora), úloha ostáva `todo` — nikdy sa nestavia na neznámej báze.
2. **Programátor stavia JEDEN task** — orchestrátor dispatchne ďalší `todo` task s briefom
   (title/description + spec sekcia + cross-cutting blok). Commit, `gate_report` s
   `commits[]` + `deliverables[]`.
3. **Mechanical verify** (deterministic, žiadny agent) — `verify_mechanical`: commit
   existuje, deliverables na disku, diff scoped na `baseline_sha..HEAD`. **Mechanical FAIL
   short-circuituje** — Audítor sa nevolá (netreba auditovať chýbajúci commit; šetrí turn).
4. **Audítor audit** (len po mechanical PASS) — audit-vs-spec scoped na deliverables (diff
   `baseline_sha..HEAD` + spec sekcia + cross-cutting); status block `task_pass` (true/false)
   + findings. **Fail-closed:** `task_pass` chýba/None → FAIL (task neprejde bez explicit
   `task_pass=true`).
5. **Rozhodnutie:**
   - **PASS** → task `done`, baseline ďalšieho = current HEAD, **automaticky ďalší task**
     (žiadny Director klik).
   - **FAIL** → **auto-fix slučka, max 5 pokusov** (Audítor re-audit medzi pokusmi,
     eskalujúci context — reuse pattern z `_auto_fix_loop` / verify-retry). Ak niektorý
     pokus prejde → `done`, ďalší task.
   - **FAIL aj po 5 pokusoch** → task `failed`, **HALT na `awaiting_director`**;
     Koordinátor relayne Directorovi. Nič ďalšie sa nestavia.
6. **Deterministická gate** — `_build_open_findings` (count z orchestrátorovho **logu**,
   nie self-report): počet `failed`/`in_progress` (neoverených) taskov > 0 blokuje advance do
   `gate_g`, kým ich Director nevyrieši. `todo` sa **nepočíta** (aby `end_build` mohol pokročiť
   so zvyškom). (Vzor z gate_e open-finding gate, 2026-06-05.)
7. **Sign-off invariant (CR-4.1 option B)** — finálne schválenie (`approve`) je platné len keď
   nezostáva žiadna `todo` úloha (`get_next_todo_task is None`) **a zároveň** `_build_open_findings
   == 0`. Nemožno finálne schváliť build s nepostavenými úlohami — toto uzatvára aj baseline-HALT
   dieru (HALT-nutá `todo` úloha sa do gate nepočíta, ale blokuje `approve`). `end_build` ostáva
   vedomá výnimka („zvyšok do auditu": `todo` povolené, `failed`/`in_progress` blokuje).

Director kedykoľvek môže zasiahnuť (pauza / `return` / `Konzultovať`). `end_build` (mirror
`end_gate_e`) — Director „zvyšok do auditu", ale failed task stále blokuje.

## 7. Director control + Frontend (ako NEX Command)

- **`TaskPlanPanel` vpravo** — **rozklikateľný strom EPIC → FEAT → TASK** (NEX Command
  ekvivalent), aktuálny task zvýraznený, status pri každom uzle (todo/in_progress/done/
  failed). Director **priebežne vidí, ktorý task Programátor robí**.
- **Živý priebeh Programátora** — activity feed (NEX Command `DelegationProgress`
  ekvivalent), reuse cockpit `agent_activity` + real-active-role signál.
- **Per-task audit výsledok rozkliknuteľný** (`VerificationPanel` ekvivalent) — diff +
  findings danej úlohy.
- Director ↔ Koordinátor výhradne; gate len pri HALT (zlyhaný task) alebo vlastnom zásahu.

### 7.1 CR-5 implementačné ukotvenie (grounding 2026-06-08)

- **Žiadny nový backend endpoint** — `GET /versions/{version_id}/task-plan` (`api/routes/versions.py`)
  už vracia EPIC→FEAT→TASK strom so statusmi (`epic`: planned/in_progress/done; `feat`/`task`:
  todo/in_progress/done/failed) + počty. FE pridá len klienta `getTaskPlan(versionId)` do
  `services/api/versions.ts`.
- **Per-task audit dáta** (`task_pass`, `findings`, `verify_reason`) sú v `PipelineMessage.payload`
  (JSONB) a už tečú na FE cez WS (`usePipelineWs`, frame `message_added`). Per-task audit panel ich
  číta z message streamu (filter `stage in (build, gate_g)` + napáruj na task podľa `payload.task_id`),
  netreba nový endpoint.
- **Živá aktivita Programátora** — existujúci `PipelineActivityFeed` (frame `agent_activity`:
  stage/actor/kind/line) ho už zobrazuje počas `agent_working`. CR-5 ho len ponechá viditeľný v
  novom layoute.
- **Layout** — dnes 2-stĺpcový (`PipelineRail` w-56 + `ExchangePanel` flex-1); CR-5 = 3-stĺpcový
  (rail + ExchangePanel užší + `TaskPlanPanel` vpravo). Štítky reuse `labels.ts`.

### 7.2 Resume po HALT — Director akcia „Pokračovať v builde"

- Build sa po HALT (baseline-HALT alebo Directorova pauza) ustáli na `awaiting_director` so
  zostávajúcou `todo` úlohou. Dnes resume vie len `return` (vyžaduje komentár, resetuje
  `failed`→`todo`) — pre čistý „prostredie opravené, pokračuj" je to zbytočné trenie.
- **Nová akcia `continue_build`** (bez komentára): pri `stage==build && status==awaiting_director`
  re-dispatchne build slučku (`_begin_dispatch`). Odlišná od `return` (rework failed tasku, komentár
  povinný) a `end_build` (preskoč zvyšok). FE button v `PipelineActionBar` za rovnakých podmienok.
- Registrácia = `_ACTIONS` frozenset + handler v `apply_action` (reuse `_begin_dispatch`).

### 7.3 Známy follow-up (NIE CR-5) — restart-mid-build recovery

Build slučka beží ako background task; pri reštarte backendu zomrie a pipeline ostane stuck na
`agent_working` (žiadny auto-resume). `lifespan` (`main.py`) reclaimuje orphaned agent_terminal +
dialogue, **ale nie pipeline**. Pred ostrým NEX Ledger buildom treba samostatnú robustness CR:
na štarte preklopiť stuck `agent_working` build → `awaiting_director` (Director resumne cez
„Pokračovať v builde"), prípadne auto-resume. `_run_build_round` už reclaimuje orphaned `in_progress`
→`todo` (CR-3), takže chýba len štartový trigger. **Mimo CR-5 scope.**

**Auditovateľnosť** zostáva plná: Audítor audituje **každý** task automaticky + výsledok
sa zaznamená a je viditeľný/rozkliknuteľný — Director to nemusí klikať per-task, ale vidí
a vie preveriť. (Pôvodná požiadavka „po každom tasku porovnaj diff + over" je splnená
Audítorom priebežne.)

## 8. Reuse vs build

**Reuse (žiadny nový kód):** VERSION→EPIC→FEAT→TASK modely; `_run_gate_e_round` ako
template; `invoke_agent_with_parse_retry`, `dispatch_directive`, `gate_e_dispatch` selector;
deterministická open-finding gate; consult akcia; recipient chain; incremental `on_message`
broadcast; real-active-role rail signál; `verify_mechanical`/`verify_done`; FE
`ExchangePanel`, `PipelineRail`.

**Build new (malé, ohraničené):** 1 stage `task_plan` (4 lockstep miesta + migrácia
widen-ujúca oba CHECK); `_run_build_round` + build dispatch selector; 2 helpery
(`_build_open_findings` + per-task brief directive s cross-cutting blokom); stĺpec
`Task.baseline_sha` + migrácia; orchestrátorov **write-path** do Epic/Feat/Task; Audítor
per-task turn wiring; auto-fix slučka (max 5); FE `getTaskPlan` klient + `TaskPlanPanel` (strom) +
per-task audit panel (z message payloadu) + `continue_build` akcia (CR-5). `PipelineActivityFeed`
(živá aktivita) sa reuseuje — žiadny nový komponent.

**Charter úpravy (Dedo, NIE Implementer — `.claude/agents/**` deny):** Návrhár (task_plan
output + coarse granularita + cross-cutting kodifikácia), Audítor (per-task audit mode,
ľahší než release Dual-Build, `task_pass`+findings), Programátor (per-task build, jeden
task/turn, injektnutý cross-cutting blok), Koordinátor (relay per-task verdict pri HALT).

## 9. Mechanika (pre Implementer plán)

- **Registrácia stage `task_plan`** na 4 miestach v lockstep: `STAGE_ORDER` + `STAGE_ACTOR`
  (`designer`) + `STAGE_TIMEOUT` (orchestrator.py), `pipeline_status.STAGES`, DB `_STAGES`
  CHECK (pipeline.py) → migrácia (`migrations/versions/`, NIE `backend/alembic/`) widen-ujúca
  `ck_pipeline_state_current_stage` + `ck_pipeline_message_stage`.
- **`build` actor** zostáva `implementer`; build dispatch sa stáva loopom cez nový sub-flow
  selector (mirror `gate_e_dispatch`).
- **Write-path** Návrhárovho plánu → Epic/Feat/Task rows pod `version_id`. Plán príde ako
  **typované polia** v status bloku (`PipelineStatusBlock` má `extra="ignore"` → free-form
  payload sa ticho zahodí, preto typované): `plan` (nested `TaskPlan→Epic→Feat→Task`: title,
  `task_type`, description, checklist_type, priority, estimated_minutes; epic `module_id`) +
  `cross_cutting_rules` (markdown, CR-3 injektuje per task; perzistuje v gate_report message).
  **Čísla** auto-assign (services MAX+1, Návrhár emituje v poradí), **status** vynútený
  (todo/planned — Návrhár nič nepredznačí done), `task_count`/`auto_fix_count` server-managed
  (CR-3), `baseline_sha` CR-3. Parser: `stage==task_plan` → `kind=gate_report` + neprázdny
  `plan`, inak `ParseFailure`. `_write_task_plan` = idempotentný replace, atomicky alebo `blocked`.
- **Status-blok signály (§7.2 zladím ja):** plán = `gate_report` + štruktúra v `payload`;
  per-task Programátor `gate_report` (`commits`/`deliverables`); Audítor `task_pass` +
  findings. `_build_open_findings` počíta failed tasky z logu.
- **Auto-fix N=5** — reuse verify-retry / coordinator-relay pattern; eskalujúci context
  (predošlé pokusy späť do briefu); po 5 → HALT.

## 10. Fázový plán (CR sekvencia)

| CR | Obsah | Závisí |
|---|---|---|
| **CR-1** | `Task.baseline_sha` + migrácia; `task_plan` stage na 4 lockstep miestach + migrácia (widen oba CHECK). Bez behaviorálnej zmeny. | — |
| **CR-2** | Task-plán stage: write-path Návrhárovho plánu → Epic/Feat/Task; beží ako gate. Návrhár charter. | CR-1 |
| **CR-3** | `_run_build_round` + `_build_open_findings` + per-task brief directive (cross-cutting); `build` = plynulá slučka + auto-fix N=5. Programátor + Koordinátor charter. | CR-2 |
| **CR-4** | Audítor per-task turn wiring; `task_pass`+findings; mechanical short-circuit + fail-closed. Audítor charter. | CR-3 |
| **CR-4.1** | Fail-closed na baseline (HALT keď HEAD nečitateľný) + sign-off invariant (option B: `approve` blokuje zostávajúce `todo`). | CR-4 |
| **CR-5** | FE: `getTaskPlan` klient + `TaskPlanPanel` (rozklikateľný EPIC-FEAT-TASK strom, current task) + per-task audit panel (z message payloadu) + `continue_build` akcia; `PipelineActivityFeed` reuse. | CR-3, CR-4.1 |

## 11. Rozhodnutia (posvätené 2026-06-07)

- **Plán sa schvaľuje RAZ**, potom plynulý beh, gate len pri výnimke.
- **Kto plánuje:** Návrhár (LLM turn).
- **Kto reviewuje per-task:** Audítor (audit) + Koordinátor (relay).
- **Auto-fix limit:** **5 pokusov**, potom Director gate.
- **Granularita:** hrubozrnná (modul = task).

## 12. Sekvencia voči NEX Ledger

Uzol postavíme **PRED tým, než Director zavrie Gate E** — aby NEX Ledger po zavretí Gate E
šiel na `task_plan` → per-task `build`, NIE na starý monolit. (Gate E je už `awaiting`,
open=0; čaká.)
