# DESIGN: NEX Studio PIPELINE-AUTONOMY — full-flow Director-touch reduction

Status: DESIGN (read-only, žiadne súbory zmenené). Autor: Dedo. Dátum: 2026-06-20.
Scope: `new_version` full-flow autonómia. Fast-fix (F-009) a `cr`/`bug` flow UNTOUCHED.
Verzia po adversariálnom self-audite: load-bearing mechanická chyba (phantom-field guard) opravená; scope-cede a release-cede odstránené; traceability prerobená na deterministickú.

Všetky `orchestrator.py` = `backend/services/orchestrator.py`. Charter = `templates/coordinator-charter.md`. Riadky overené proti aktuálnemu zdroju (nie pamäť).

---

## 0. Princíp (jednou vetou)

Pri PASS gate Koordinátor **už urobil deterministické overenie** (`_verify_with_retries` → `verify_mechanical` + judgment + na gate_g smoke) a syntézu (`_coordinator_synthesis`). Director klik „Schváliť" na takom gate nepridáva nič, čo engine deterministicky neoveril — je to čistá ratifikácia. **Auto-ratifikujeme presne tieto deterministicky-clean ratifikácie**, ponecháme Directorovi presne rozhodnutia, ktoré menia **ČO sa stavia** alebo sú **nezvratné**. Mechanizmus existuje a je live-validovaný (fast-fix CR-NS-103) — **rozširujeme ho, nevymýšľame nový.**

### 0.1 Koreňová oprava oproti prvému návrhu (kritické)

Prvý draft staval auto-ratify guard na „synthesis classification `not director_decision` ∧ confidence ≥ floor". **Tieto polia na PASS site NEEXISTUJÚ** — overené v zdroji:

- `verify_done` (1980-2003) vracia `(reason, directive, is_coordinator_error)`. Na PASS je `reason is None` a **`directive` je naplnený LEN pri `blocked` verdikte** (1986-1987). Čistý PASS nenesie directive.
- `_coordinator_synthesis` (1660-1720) produkuje **voľný slovenský markdown** s `extra_payload={"is_synthesis": True}` — žiadny `triage_class`, žiadna strojová `confidence`, žiadny `proposed_action`. `_is_director_decision_directive` / `_coordinator_directive_executable` operujú nad `coordinator_directive` dictom, ktorý sa tu vôbec neemituje.

Guard proti neexistujúcim poliam by buď **vždy blokoval** (nulová autonómia → design zlyhá v cieli), alebo — ak by „chýbajúci directive" interpretoval ako „clean" — **vždy auto-ratifikoval naslepo** (cede control). Stred neexistuje.

**Oprava (záväzná pre celý design): auto-ratify guard NIE JE confidence-based. Je čisto DETERMINISTICKÝ na signáloch, ktoré na PASS reálne existujú:**

| Signál | Zdroj na PASS site | Význam |
|---|---|---|
| `reason is None` | `_verify_with_retries` @3187 | verify (mechanical + judgment) PASS |
| `is_scope == False` | `_verify_with_retries` @3187 | žiadna scope/design otázka |
| `build_readiness` clean | `build_readiness()` @389 | 0 todo ∧ 0 failed (build approve) |
| smoke green | `verify_done` @2012-2014 | app boots+responds (gate_g; už súčasť `reason`) |
| `designer.gap_found == False` | Gate E Branch A @3442/3445 | reálny boolean — žiadna medzera |

Žiadny z týchto guardov nečíta confidence. Ak by sa graded confidence niekedy chcel, MUSÍ sa **pridať** do verify-judge kontraktu (`verify_done` emituje structured PASS directive `{verdict, confidence, triage_class}` aj na PASS ceste) — to je samostatný CR, **nie „reuse existing".** Tento design vedome volí deterministický set; confidence-floor sa neuvádza ako guard nikde.

---

## 1. ROUTINE-AUTO vs KEY-DIRECTOR — per-gate tabuľka

**ROUTINE = gate, ktorý engine deterministicky overil ako clean (verify PASS ∧ not scope ∧ príslušný readiness). KEY = čokoľvek, čo mení ČO sa stavia, je nezvratné, alebo je reálny konflikt/zlyhanie.** Riadky `orchestrator.py` overené proti zdroju (draftové čísla boli ~80-200 mimo).

| # | Settle point | orchestrator.py | Trieda | Rozhodnutie | DETERMINISTICKÝ guard | HALT-on-exception (už existuje) |
|---|---|---|---|---|---|---|
| 1 | kickoff approve | 3243-3245 | KEY (boundary) | **Keep** — jediný „start build" dotyk, Director nastaví zámer | — | — |
| 2 | gate_a PASS | 3225-3239 | ROUTINE | **AUTO-ratify → gate_b** | `reason is None` ∧ `not is_scope` | FAIL → `blocked` @3215-3224 |
| 3 | gate_b PASS | 3225-3239 | ROUTINE | **AUTO-ratify → gate_c** | dtto | dtto |
| 4 | gate_c PASS | 3225-3239 | ROUTINE | **AUTO-ratify → gate_d** | dtto | dtto |
| 5 | gate_d PASS | 3225-3239 | ROUTINE | **AUTO-ratify → gate_e** | dtto | dtto |
| 6 | gate_e topic boundary (clean) | 3413-3423 | ROUTINE | **AUTO-continue → ďalší okruh** | `topic_done` ∧ 0 open findings ∧ pod budget | gap → blokuje close @5438-ekv. |
| 7 | gate_e Branch A (no gap) | 3445-3446 | ROUTINE | **AUTO-continue → ďalšia otázka** | `designer.gap_found == False` ∧ pod budget ∧ no ParseFail | budget vyčerpaný → **escalate** (nie close) |
| 8 | gate_e Branch B (gap_found) | 3442-3444 | **KEY** | **Keep** — gap = spec decision, vždy Director | — | `_coordinator_review_gap` → `awaiting_director` |
| 9 | gate_e final close | `apply_action` end_gate_e | KEY | **Keep (jeden bounded close)** | — | open findings > 0 → blokuje close |
| 10 | task_plan approve | 3243-3245 | KEY | **Keep (jediná plan approval)** | — | — |
| 11 | build per-task HALT | build loop | KEY (exception) | **Keep** — už exception-only (charter §4.5) | — | `_AUTO_FIX_RETRIES` then HALT |
| 12 | build final approve | apply_action approve@build | ROUTINE | **AUTO-ratify → gate_g** | `build_readiness` clean (0 todo ∧ 0 failed) | todo/failed → settle, nikdy auto |
| 13 | gate_g PASS verdict | 3225-3239 | **KEY → DEFER auto** | **Keep verdict click v1** (viď §1.1) | — (auto deferred) | scope Q → `blocked` @3188-3214 |
| 14 | gate_g scope Q (1./2.) | 3188-3214 | **KEY** | **Keep** — genuine otázka rozsahu | — | cap `_MAX_SCOPE_ESCALATIONS_PER_ITERATION` |
| 15 | release PASS (publish) | 3236-3237 | engine-owned | **Keep engine-owned** — `_release_auto_publish` už beží bez dotyku | — | publish FAIL → `blocked` + `retry_publish` |
| 16 | release uat_accept | 5657-ekv. | **KEY nezvratné** | **Keep absolútne** — PROD acceptance gate | — | — |
| 17 | release retry_publish | 5687-ekv. | KEY | **Keep** — recovery z reálneho zlyhania | — | — |

**Auto-ratify množina v1: #2,3,4,5 (gates a-d) + #6,7 (Gate E clean) + #12 (build approve).**
**KEY-Director množina: #1 (start) + #8 (Gate E gap) + #9 (Gate E close) + #10 (task_plan) + #11 (build HALT) + #13 (gate_g verdict — DEFER auto) + #14 (gate_g scope) + #16 (uat_accept) + #17 (retry_publish).**

### 1.1 gate_g (#13) — odporúčanie: DEFER auto-PASS na v2

V drafte gate_g auto-PASS bolo „BORDERLINE, odporúčam IN". **Reverzia po self-audite: odporúčam DEFER na v2.** Dôvod (overené v zdroji):

- `verdict=PASS` @gate_g postupuje na `release` → `_release_auto_publish` @3237 (engine-owned push + CI). Reťazec „Auditor PASS → kód je verejne publikovaný" by mal **nulový Director dotyk.**
- Smoke check vo `verify_done` je LEN **boot check** (2010: „BOOT check … NOT a runtime acceptance run") — confident-but-wrong Auditor PASS by auto-publikoval.
- uat_accept (PROD) zostáva gated → PROD je bezpečný. Ale zlý **publish/release** (verejný tag/release) nie je nič.

Gates a-d + Gate E auto-ratify už dodajú drvivú väčšinu redukcie (~50 → ~6). gate_g auto-PASS ušetrí JEDEN klik za cenu nesupervízovaného publishu. **Nestojí to za to vo v1.** Verdict click ostáva prirodzeným, nízkofrekvenčným release rozhodnutím („áno, publikuj toto") — presne ten typ, čo HARD CONSTRAINTS hovoria ponechať.

Ak by Director napriek tomu chcel IN: musí to byť za **kickoff opt-inom** „auto-publish on green" (nikdy default), plus `verify_mechanical` ∧ smoke green ∧ `is_scope == False`. Toto je jediné otvorené rozhodnutie (§8).

---

## 2. Bounded Customer / Gate E review

Marathon, kvôli ktorému Director opustil build. Tri zložené príčiny (overené): (a) každá výmena settluje `awaiting_director` (3441); (b) hĺbka je fixná (charter „7 okruhov × ≥3 otázky") bez ohľadu na scope; (c) žiadny „clean single close". Štyri zložky — **bez §2.4 auto-defer (vypustené, cede scope — viď nižšie).**

### 2.1 Scope-scaled depth (koreňový fix) — škáluje zo SPEC, nie z task_plan

Hĺbka review sa odvodí z reálneho footprintu verzie. **Oprava oproti draftu: NIE z task_plan footprintu** — task_plan je stage AŽ PO gate_e (`_next_stage("gate_e") → task_plan`), pri Gate E ešte neexistuje. Pri Gate E existuje LEN spec.

- **Vstup:** `development-spec.md` (Gate A scope) + `customer-requirements.md`. Customer charter §3 už `development-spec.md` číta → input existuje.
- **Seam:** gate_e directive `_directive_for("gate_e")` (~496-525, dnes generický) rozšíriť o **review surface** odvodený zo spec scope: ktoré okruhy/moduly/screeny verzia reálne dotýka.
- **Charter:** Customer charter §4.1/§4.5 (`<target>/.claude/agents/customer/CLAUDE.md`) — fixnú „7 okruhov × ≥3 otázky" zmeniť na **„walk LEN dotknuté okruhy; depth škáluje so spec scope: 1-feat tweak → afektované okruhy/screeny, greenfield → full walk."**
- **Question budget = floor-plus-ceiling, NIE silent stop (oprava proti threshold-downgrade anti-patternu):**
  - **floor:** minimum N otázok per dotknutá requirement-area (Gate E existuje práve na chytenie spec medzier — pod-review je opačné zlyhanie).
  - **ceiling:** horný strop autonómneho Branch A behu. **Vyčerpanie ceiling = escalate na Directora** („Customer chce pokračovať → predĺž alebo zatvor"), **NIKDY auto-close ako keby bolo complete.** Ceiling teda nikdy neznižuje kvalitu review — len presúva pokračovanie na Directora.
- `_gate_e_question_budget(version)` — odvodený z review surface (dotknuté moduly × min-per-modul). Konkrétna derivácia patrí do build CR; design fixuje sémantiku (floor+ceiling-with-escalation).

### 2.2 Auto-ratify Branch A (zrkadlí fast-fix) — DETERMINISTICKÝ guard

Gate E analóg `_maybe_autonomous_answer`: keď Designer odpovie `gap_found == False` (Branch A, „je to pokryté", @3445-3446) → auto-continue na ďalšiu otázku BEZ Director stopu.

- **Mechanizmus (úprimne):** `_run_gate_e_round` je JEDEN turn (Customer → Designer → settle @3441) — **NEcykli.** Auto-continue preto musí v settle mieste **self-issue `_begin_dispatch` ďalšieho Customer turnu** (presne ako `apply_action` approve@gate_e robí `_begin_dispatch(db, state)`), nie „chain in-round". Žiadny in-round loop.
- **Guard (čisto deterministický — oprava proti phantom confidence):** `flow_type == "new_version"` ∧ `designer.gap_found == False` ∧ žiadny ParseFailure ∧ `_gate_e_question_count < _gate_e_question_budget` (§2.1 ceiling). **Žiadny „conf ≥ 0.85"** — Designer status block nesie `gap_found`, `proposed_fix`, NIE graded confidence (overené @815-851-ekv. a Branch A @3442-3446). Confidence-floor by čítal phantom pole.
- **Branch B (gap_found, @3442) NEDOTKNUTÝ** → `_coordinator_review_gap` → `awaiting_director`. Genuine spec gap = `director_decision`-class.

### 2.3 Clean single close (#6 + #9)

- **Topic boundary (#6, @3413-3423):** okruh zavrie s 0 open findings → auto-continue na ďalší okruh, **ale per-topic Customer `gate_report` ZOSTÁVA individuálne viditeľný na boarde** (oprava Issue 3: `topic_done` neznamená „nič hodné Directora" — môžu byť non-blocking observations). Auto-continue je OK len preto, že (a) každý per-topic report je durable message na boarde a (b) deterministický roll-up (§3) je vždy prítomný. Nie je to silent skip.
- **Final close (#9):** Director vidí **JEDEN** finálny ratify pri `coverage_complete` namiesto per-topic + per-question approvals. Deterministický open-finding gate ostáva (close blokuje pri open findings > 0). Jediný bounded Gate-E dotyk pri čistom review.

### 2.4 Auto-defer out-of-scope findings — VYPUSTENÉ z v1 (cede scope)

Draft mal Koordinátora klasifikovať `gap_found` ako out-of-scope → auto-`leave`-with-backlog. **Odstránené.** Dôvod: „je tento nález v scope tejto verzie?" **JE scope/cost fork**, ktorý HARD CONSTRAINTS rezervujú Directorovi („anything that changes WHAT gets built"). Guard „not director_decision" je cirkulárny — rozhodnúť že je out-of-scope JE to director_decision. Zle-deferred in-scope gap = verzia odíde **bez reálnej requirementy**, auto-backlognutá kde sa Director nemusí nikdy pozrieť = tiché zúženie scope. Priamy konflikt s „agents propose, Director disposes".

- **v1: každý `gap_found` (Branch B) escaluje na Directora ako dnes.** Volume bolesti rieši §2.1 (scope-scaled depth → menej off-topic otázok vôbec položených), nie auto-resolving už raisnutých nálezov.
- Ak sa deferral mechanizmus niekedy chce: musí byť **Director-proposed** (Koordinátor odporučí „leave + backlog", Director jedným klikom potvrdí), nikdy Coordinator-executed.

**Odporúčaná kombinácia v1: 2.1 (scale depth) + 2.2 (auto-ratify Branch A) = jadro; 2.3 (clean close) = polish. 2.4 mimo v1.** Hodnota review zostáva — Customer stále aplikuje externý user pressure, každý reálny spec gap stále dosiahne Directora — pri zabití fixed-depth, every-exchange-gated marathonu.

---

## 3. TRACEABILITY — každý auto-ratify logged + DETERMINISTICKY viditeľný

Mechanizmus existuje (`_record_autonomous_decision` @4457, `autonomous_decisions_summary` board feed) — ale je task-bound. Rozšírenie:

1. **Samostatný `_record_autonomous_gate` (NIE overload task-bound funkcie).** `_record_autonomous_decision` (@4457-4490) vyžaduje `task: Task`, hardkóduje `stage="build"` (@4474), zapisuje `task_id`/`task_number`. Per-task capy `_autonomous_count`/`_autonomous_answer_count` (@4295-4344) filtrujú na `p.get("task_id") == str(task_id)`. Nový gate-level recorder:
   ```
   _record_autonomous_gate(db, version_id, stage, action, rationale) -> None
   ```
   zapíše `coordinator→director` `notification` s `payload.is_autonomous=true` + **`stage`** (board renderuje stĺpec stage) + `action` + `rationale` + **NO `task_id`**. Null-`task_id` riadok je per-task capmi prirodzene vylúčený (filter `task_id ==` ho nezarátá) — **build CR doplní test, ktorý to assertuje.** Žiadny auto-ratify NESMIE obísť tento záznam. Confidence pole sa NEzapisuje (na PASS neexistuje — §0.1).

2. **`autonomous_decisions_summary` (board feed) filtruje na `is_autonomous`** → gate-level záznamy doň automaticky padnú. FE `is_autonomous` kontrakt sa NEMENÍ; len sa rozšíri čo sa zapisuje (a pridá `stage` do payloadu, ktorý FE už vie renderovať ako stĺpec).

3. **Deterministický „čo som auto-rozhodol" roll-up — NIE na synthesis LLM turne (oprava Issue 7).** Draft prependoval roll-up do `_coordinator_synthesis`, ktorý **smie ParseFailovať na `None`** (3707-3719, „additive observability only … never a dead-end") — audit trail by ticho zmizol. Oprava: roll-up sa **počíta deterministicky z `is_autonomous` správ** (tie isté dáta, čo `autonomous_decisions_summary` query) a renderuje sa na boarde ako **durable, vždy-prítomný** prvok pri každom settled KEY state — nezávisle od toho, či synthesis text vznikol. Synthesis ho môže referencovať, ale **board count je source of truth.** Director pri každom reálnom dotyku vidí „Auto-schválil som: gate_a ✓, gate_b ✓, gate_c ✓, gate_d ✓, Gate E okruhy 1-5 (8 otázok, 0 medzier)" — po fakte review (TRACEABILITY constraint splnený, ParseFail-proof).

4. **Director override — úprimne (oprava Issue 11).** `return`/`ask` sú universally valid pri **settled** stave (`determine_available_actions` @343-345). ALE počas auto-advanced behu je pipeline `agent_working` na NASLEDUJÚCEJ stage → `determine_available_actions` vráti `{"pause"}` (build) alebo `set()` (@334-336). **Director NEMÔŽE `return` auto-ratifikovaný gate mid-run.** Override je dostupný pri **najbližšom settle** (cez roll-up + `return`). Design tvrdí toto úprimne: „override auto-ratifikovaného gate je dostupný pri najbližšom settled state, NIE mid-run." Mid-run revert by potreboval novú „rewind to stage" akciu — mimo scope, design ju neimplikuje.

---

## 4. HALT-ON-EXCEPTION — kedy engine STOPNE a vtiahne Directora

Auto-ratify firuje LEN na deterministicky-clean PASS. Každá z týchto podmienok už štrukturálne settluje `blocked`/`awaiting_director` PRED PASS site — auto-ratify ich nikdy nevidí. Overené proti zdroju:

| Podmienka | Trieda | Kde už halt-uje | Auto-ratify NIKDY (deterministicky) |
|---|---|---|---|
| **Verify FAIL** (mechanical/judgment) | failure | `blocked` + `system_error` @3219-3224 | `reason is not None` → preskočí auto |
| **Scope flag** (`is_scope`) | scope fork | gate_g `blocked` @3188-3214 | guard `not is_scope` |
| **Gate E gap_found** | mení spec | Branch B @3442 → `awaiting_director` | Branch B mimo auto-set; guard `gap_found == False` |
| **Build todo/failed task** | not ready | settle, never auto | guard `build_readiness` clean |
| **gate_g (v1)** | release | verdict click vždy | DEFER — auto sa netriggruje |
| **Build per-task FAIL po retries** | failure | HALT → `awaiting_director` | mimo gate-ratify scope |
| **Question/answer budget exceeded** | repeat | per-task capy / Gate E ceiling escalate | Gate E ceiling → escalate |
| **Parse exhaustion** | agent error | `awaiting_director`/`blocked` `parse_exhaustion` | nie je gate_report PASS / `_block_failed` |
| **Publish/deploy FAIL** | system error | `blocked` + `retry_publish` | engine-owned, nie auto-ratify |
| **uat_accept (PROD)** | irreversible | nikdy auto-fired | explicitne mimo auto-set |

**Invariant: čokoľvek failing / scope / over-budget / gap / irreversible → STOP na `awaiting_director`/`blocked` s `block_reason`, NIKDY silent pass.** Auto-ratify nesluje žiadny `block_reason` (`agent_question`/`agent_error`/`system_error`/`parse_exhaustion`) do tichého prechodu. Halt-on-exception backbone je sound pre **mechanical aj judgment** zlyhania (verify FAIL pre-emptuje obe).

### 4.1 Runaway-confidence kontrola (Issue 12)

Per-task capy (`_MAX_AUTONOMOUS_PER_TASK`, …) existujú práve aby opakovanie eskalovalo. Gate auto-ratify nemá analóg „po N gate-och v rade urob checkpoint" — z dizajnu auto-advancuje A→B→C→D→(E clean)→build-approve. To je zámer (každý gate má vlastný deterministický FAIL→block), ale kompenzačná kontrola je:
- **Deterministický roll-up (§3.3)** = vždy-prítomný checkpoint pri každom KEY settle.
- **Kickoff toggle (default ON pre new_version) „auto-ratify routine gates"** — Director, ktorý chce per-gate sign-off pre high-stakes release, vypne bez code change (§3.2 quality-first: jeden default, opt-out pre výnimočný prípad).

---

## 5. Mechanizmus — rozšírenie existujúcich patternov (reuse, nie reinvent)

### 5.1 Routine-gate auto-advance (gates a-d, build approve)

Jeden chokepoint: `result.kind == "gate_report"` PASS branch — `else` vetva @3225-3239 (PO `_coordinator_synthesis` @3227, PRED `state.status = "awaiting_director"` @3228). Nový helper, sibling `_maybe_autonomous_recovery`:

```
_maybe_autonomous_gate_ratify(db, state, reason, is_scope) -> bool
```

- **Guard (čisto deterministický — VŠETKO musí platiť):**
  - `state.flow_type == "new_version"` (fast_fix/cr/bug byte-identické),
  - `reason is None` ∧ `is_scope == False` (z `_verify_with_retries` @3187),
  - `state.current_stage in {gate_a, gate_b, gate_c, gate_d}`,
  - **explicitne `state.current_stage NOT IN {release, gate_g}`** (oprava Issue 10 — release advance je `_release_auto_publish` path; gate_g je DEFER),
  - kickoff toggle „auto-ratify routine gates" zapnutý (§4.1).
- **Ak True:** self-issue `approve` advance — reuse advance logic (`state.current_stage = _next_stage(...)` + `_begin_dispatch`). Zapíše `_record_autonomous_gate(stage=current_stage, action="auto_ratify_gate")`. Vráti True → dispatch pokračuje (nové dispatch už beží).
- **Ak False:** padá na existujúci `awaiting_director` settle @3228 — žiadna zmena správania.
- **build final approve (#12):** rovnaký pattern v approve@build settle mieste; gated `state.current_stage == "build"` ∧ `build_readiness` clean (@389).

Presne engine-analóg fast-fix kickoff carve-out @3152-3162: po verify self-issue advance namiesto settle.

### 5.2 Gate E auto-continue (Branch A + topic boundary)

Sibling helper pre gate_e, volaný v `_run_gate_e_round`:

```
_maybe_autonomous_gate_e_continue(db, state, designer_result_or_topic) -> bool
```

- **Branch A (@3445-3446):** guard `flow_type == "new_version"` ∧ `designer.gap_found == False` ∧ no ParseFail ∧ `_gate_e_question_count < _gate_e_question_budget` (ceiling). Ak True → self-issue `_begin_dispatch` ďalšieho Customer turnu (`gate_e_dispatch=None` @3398). Zapíše `_record_autonomous_gate(stage="gate_e")`.
- **Topic boundary (@3413-3423):** guard `topic_done` ∧ 0 open findings ∧ pod budget → self-issue `_begin_dispatch` ďalšieho okruhu; per-topic report ostáva durable na boarde (§2.3).
- **Branch B (@3442) NEDOTKNUTÝ** — vždy `_coordinator_review_gap` → `awaiting_director`.
- **Budget ceiling vyčerpaný** → `awaiting_director` s next_action „Gate E dosiahol strop — predĺž alebo zatvor" (escalate, nie auto-close — §2.1).

### 5.3 Konštanty / funkcie (nové)

- `_gate_e_question_budget(version)` — funkcia spec scope (floor+ceiling), NIE konštanta.
- **Žiadne nové confidence-floor konštanty** — guardy sú deterministické (§0.1). (`_GATE_E_ANSWER_FLOOR` / `_GATE_G_AUTOPASS_FLOOR` z draftu sa NEzavádzajú.)

### 5.4 Charter zmeny

- `templates/coordinator-charter.md` §4.5 / §7.1: rozšíriť „engine-auto exceptions" o **routine-gate ratification na full-flow** (deterministic PASS: verify clean + not scope + readiness + visible record).
- Nová **§4.7** (analóg §4.6 fast-fix, ale pre `new_version` gates): „Operujem routine gates (a-d, Gate E clean, build approve) na deterministickom clean signáli; relayujem LEN KEY rozhodnutia (kickoff, Gate E gap/close, task_plan, gate_g verdict, uat_accept)."
- Customer charter §4.1/§4.5 (per-project): scope-scaled depth + floor/ceiling budget (§2.1).

### 5.5 Fast-fix INTACT

Fast-fix kickoff carve-out @3152-3162 a release block @3164-3184 majú **vlastné early returns PRED** gate_report PASS site @3186 → `_maybe_autonomous_gate_ratify` ich nikdy nevidí. Všetky guardy gated `flow_type == "new_version"`. Nový helper navyše explicitne vylučuje `stage in {release, gate_g}` (Issue 10). `cr`/`bug` zachovajú generický `awaiting_director` settle. **Build CR doplní test: `new_version` release PASS stále ide cez `_release_auto_publish`, NIE cez gate-ratify advance.** Nulový autonomy leak.

---

## 6. Net effect — worked example (normálny new_version build)

Verzia s 2 modulmi, čistý priebeh (žiadny gap, žiadny HALT, audit PASS):

### PRED (dnes)
| Krok | Director klik |
|---|---|
| kickoff | approve |
| gate_a–d PASS | 4× approve |
| Gate E (7 okruhov × ~5 otázok + topic, fixed) | ~42 klikov |
| Gate E close | approve |
| task_plan | approve |
| build (clean) | 0 (charter §4.5) |
| build approve | approve |
| gate_g PASS | verdict |
| release publish | 0 (engine) |
| uat_accept | accept |
| **SPOLU** | **~50+ klikov („every gate" marathon)** |

### PO (tento design, v1 — gate_g DEFER)
| Krok | Director klik |
|---|---|
| kickoff (start) | **start** (1 — nastaví zámer) |
| gate_a–d PASS | 0 (auto-ratify; deterministicky, loggované) |
| Gate E (scope-scaled: 2 moduly → ~8 otázok, 0 gaps) | 0 (auto-ratify Branch A + topic) |
| Gate E close (clean) | **close** (1 — bounded sign-off, vidí roll-up „8 otázok, 0 medzier") |
| task_plan | **approve** (1 — definuje decomposition) |
| build (clean) | 0 |
| build approve | 0 (auto-ratify) |
| gate_g PASS | **verdict** (1 — release rozhodnutie, DEFER auto) |
| release publish | 0 (engine) |
| uat_accept | **accept** (1 — nezvratné) |
| **SPOLU (clean)** | **~5 KEY rozhodnutí** (start, Gate E close, task_plan, gate_g verdict, uat_accept) |

**Úprimný rozsah (oprava Issue 9 — nie headline „4" potom realita 8):**
> **clean build ~5** (start, task_plan, Gate E close, gate_g verdict, uat_accept); **+1 za každý reálny Gate E gap** (genuine spec decision); **+1 za každý build HALT.** Typický build s 1-2 gapmi ≈ **6-7 dotykov.**

To stále plní cieľ Directora: „a few key decisions, nie ratifying every gate." Director touches: **~50 → ~5-7.** Trivial change → ~0 cez fast-fix (už hotové).

---

## 7. Phased rollout + validácia

**Fáza 1 — routine gates a-d auto-ratify (nulový downside).** Najbezpečnejšie: každý FAIL ich pre-emptne do `blocked`. CR rozsah: `_maybe_autonomous_gate_ratify` (§5.1, deterministický guard) gated na gates a-d + explicit `stage NOT IN {release, gate_g}` + `_record_autonomous_gate` (§3) + deterministický board roll-up (§3.3) + charter §4.7 + kickoff toggle (§4.1) + test „release PASS ide cez publish, nie ratify". Validácia: real new_version build → gates a-d auto-advancujú, board `autonomous_decisions_summary` ukazuje 4 záznamy s `stage`, Director ich vidí v roll-up pri Gate E.

**Fáza 2 — build approve auto-ratify.** Gated `build_readiness` clean. (gate_g auto-PASS NIE — DEFER na v2 per §1.1, pokiaľ Director nerozhodne inak.) Validácia: build dôjde po gate_g verdict click bez build-approve kliku; todo/failed task stále settluje.

**Fáza 3 — Gate E bounding (najväčšia hodnota, najviac práce).** 2.1 scope-scaled depth (floor+ceiling) + 2.2 Branch A auto-ratify (deterministický gap_found guard) + 2.3 clean close (per-topic reports durable). **Bez 2.4 auto-defer.** Validácia: malá verzia (1-feat) → Gate E walk LEN dotknuté okruhy, ~zero Director klikov pri 0 gaps, jeden close; gap_found stále escaluje (Branch B); budget ceiling → escalate (nie silent close); per-topic reports viditeľné na boarde. Toto je fáza, ktorá zabije marathon.

**Akceptačné kritérium (live build, krok-za-krokom s Directorom):** spustiť reálny `new_version` build (napr. ďalšia nex-asistent verzia / malá NEX Studio feature). Director sa dotkne LEN: start, task_plan, Gate E close, gate_g verdict, uat_accept (+1 za reálny gap). Každý auto-ratify musí byť na boarde s `is_autonomous=true` + `stage` + rationale (deterministicky, prežije synthesis ParseFail). Žiadny FAIL/gap/scope sa nesmie prekĺznuť bez stopu. Director `return` re-opene ktorýkoľvek auto-decided gate **pri najbližšom settle** (nie mid-run — §3.4).

---

## 8. Otvorené rozhodnutia pre Directora (krok-za-krokom, §3.3 — pred build CR, po jednom)

Vidím 2 rozhodnutia. Najprv to load-bearing (routine-vs-key čiara), potom gate_g.

**Rozhodnutie 1 (load-bearing — TVOJA čiara na potvrdenie): potvrď routine-vs-key klasifikáciu.**
Navrhovaná čiara, ktorú auto-rozhoduje Koordinátor (deterministicky, loggované, visible):
> **AUTO (routine):** gate_a, gate_b, gate_c, gate_d (verify clean ∧ not scope) · Gate E čisté okruhy/otázky (gap_found == False, pod budget) · build approve (build_readiness clean).
>
> **TY (key):** start (kickoff) · Gate E gap (Branch B) · Gate E close · task_plan · build HALT · gate_g verdict · gate_g scope otázka · uat_accept · retry_publish.

Toto je TVOJE rozhodnutie na sign-off — všetko ostatné z neho vyplýva. Odporúčam túto čiaru: každý AUTO gate má vlastný deterministický FAIL→`blocked` pred PASS site, takže auto-ratify nikdy nevidí problém; každý KEY gate buď mení ČO sa stavia, alebo je nezvratný.

**Rozhodnutie 2: gate_g auto-PASS — DEFER (v2) alebo IN (v1)?** (§1.1).
Odporúčam **DEFER**. gate_g PASS → engine-owned publish (verejný release) s nulovým dotykom; smoke je len boot-check; verdict click je prirodzené nízkofrekvenčné release rozhodnutie. Gates a-d + Gate E auto-ratify už dodajú ~50→~5-7. IN ušetrí 1 klik za cenu nesupervízovaného publishu — nestojí to za to vo v1. (Ak IN, tak len za kickoff opt-inom „auto-publish on green", nikdy default.)

Po vyriešení týchto dvoch je design kompletný a pripravený na build CR rozklad (3 fázy, §7).

---

Súbory na zmenu (build CR, nie teraz): `/opt/projects/nex-studio/backend/services/orchestrator.py` (nový `_maybe_autonomous_gate_ratify` @3225 `else`-vetva seam s explicit `release`/`gate_g` exclude; `_maybe_autonomous_gate_e_continue` @3445 + @3413 seam; nový `_record_autonomous_gate` vedľa `_record_autonomous_decision` @4457; deterministický board roll-up z `is_autonomous` správ; `_gate_e_question_budget` floor+ceiling; `_directive_for("gate_e")` scope injection zo spec ~496-525; kickoff toggle). `/opt/projects/nex-studio/templates/coordinator-charter.md` (§4.5/§7.1 + nová §4.7). Per-project `<target>/.claude/agents/customer/CLAUDE.md` (§4.1/§4.5 scope-scaled depth). `/opt/projects/nex-studio/backend/api/routes/pipeline.py` (autonomous-summary feed — len rozšíriť o gate-level `stage` záznamy; FE `is_autonomous` kontrakt nezmenený). Žiadne nové confidence-floor konštanty (guardy deterministické — §0.1).