# Interaktívna konzultácia — návrh (NEX Studio v2.0.0)

> **Stav: NÁVRH na revíziu Manažérom (Director).** Pripravil Dedo 2026-06-28 po crash-teste buildu
> nex-agents. Implementácia začne až po schválení (waterfall). Otvorené otázky pre Manažéra v §11.

---

## 1. Problém (prečo to vzniká)

Keď Auditor pri upfront previerke (fáza Návrh) nájde diery, dnes **vysype surový verdikt** —
`kind=verdict` so zoznamom `findings[]` + `proposed_fix`. Manažér má potom tie surové nálezy prečítať,
spraviť **architektonické rozhodnutia** (napr. „vlastný Telegram bot vs. zdieľaný", „backend na hostiteľovi
vs. v kontajneri") a **ručne napísať voľnotextový „Uprav" prompt** pre AI Agenta.

V crash-teste to fungovalo **iba preto, že v zákulisí bol Dedo** (expert): prečítal surový verdikt cez
databázu/API, zosyntetizoval rozhodnutia, vysvetlil ich Directorovi + odporučil **po jednom**, a napísal
opravný prompt. **V ostrej prevádzke Dedo nebude.** Tibor a Nazar (nešpecialisti) vidia **iba obrazovku** —
a z nej sa tých 10 nálezov **nedá vyriešiť**. Dnešný stav je pre nich **nepoužiteľný**.

**Požiadavka Directora (opakovane zdôraznená):** celé musí fungovať tak, ako pracuje Director s Dedom — keď
vznikne problém/rozhodnutie, **AI Agent to musí sám vysvetliť ľudskou rečou, ponúknuť možnosti + odporúčanie
a INTERAKTÍVNE to vykonzultovať s Manažérom po jednom rozhodnutí**, zachytiť odpovede klikaním a **sám to
aplikovať** — Manažér nečíta surové nálezy ani nepíše prompty.

---

## 2. Model — „Konzultácia" (AI Agent ako Dedo na obrazovke)

**Auditor sa nemení.** Naďalej beží nezávisle a vydá **jeden** `kind=verdict` (`findings[]` + `proposed_fix`),
zapísaný **skôr, než ho AI Agent vidí** (nezávislosť zachovaná).

**Mení sa konzument verdiktu.** Namiesto toho, aby verdikt settol rovno na `awaiting_manazer` a Manažér
dostal surové nálezy + prázdny „Uprav" box, engine **smeruje verdikt do teplého AI Agenta** ako nový
**konzultačný ťah**. AI Agent (ktorý napísal Špecifikáciu + Návrh a drží celý kontext):

1. **preloží** každý nález do zrozumiteľného **rozhodnutia** (problém po ľudsky, 2–3 možnosti s dôsledkami,
   práve jedno **odporúčanie**),
2. vydá ich ako jeden blok `kind=consultation` s usporiadaným poľom `decisions[]`,
3. pipeline sa zablokuje so `block_reason="decision_needed"`,
4. cockpit zobrazí **jednu rozhodovaciu kartu naraz**; Manažér klikne možnosť (prípadne poznámku),
5. po poslednom rozhodnutí AI Agent **sám prepracuje Špecifikáciu/Návrh** podľa rozhodnutí, Auditor
   **znova preverí**, a Miera autonómie riadi ďalší stop.

**Manažér prečíta nula surových nálezov a napíše nula promptov.** Je to doslova tok Director↔Dedo — len
„Dedo" je teraz produkčný AI Agent.

---

## 3. Kto vedie konzultáciu — **AI Agent** (nie Auditor)

Všetky tri nezávislé návrhy dospeli k tomu istému; vynucuje to kód aj dizajn:

1. **Nezávislosť (nosný dôvod).** Dizajn jasne delí prácu: „Auditor len nachádza/overuje; AI Agent opravuje"
   (`nex-studio-v2-design.md` §2.4), a charta Auditora je READ + RUN-ONLY (nikdy needituje/necommituje,
   AUD-4). Keby konzultáciu viedol Auditor, musel by autorovať odporúčanie **aj** prepísanú Špecifikáciu —
   stal by sa navrhovateľom opráv **aj** nezávislým sudcom tých opráv. To zničí nezávislosť, ktorá robí
   nedozorované buildy bezpečnými.
2. **Teplý kontext + kto aplikuje.** AI Agent napísal Špecifikáciu aj Návrh a drží `--resume` vlákno —
   je jediný, kto vie (a) preložiť nález do ľudskej reči ukotvenej v reálnom návrhu, (b) odporučiť ako Dedo,
   (c) **reálne aplikovať** rozhodnutie editáciou artefaktov. Rozhodnutia musí vlastniť ten, kto ich vykoná.
3. **Je to doslova tok Director↔Dedo** — v crash-teste verdikt prekladal Dedo (vykonávateľ s kontextom),
   nie Auditor. Produkčný AI Agent je Dedo.
4. **Precedens.** Dialóg v Príprave je už dnes vedený AI Agentom po jednom (`_priprava_directive`); aj
   Verifikácia FAIL fix-scope sa vracia na AI Agenta. Konzultácia je ten istý vzor namierený na nálezy.

**Bezpečnostná poistka** (AI Agent prekladá kritiku vlastného návrhu → riziko zmäkčenia/slepej škvrny):
pokyn **vynúti vymenovať KAŽDÝ nález ako samostatné rozhodnutie** (žiadne zlučovanie/vynechávanie), surový
verdikt ostáva viditeľný (disclosure), a **Auditor po prepracovaní znova preverí** — zatajený/zle vyriešený
nález padne v ďalšom verdikte a slučka sa zopakuje (ohraničené `AUDITOR_LOOP_MAX`).

---

## 4. Priebeh konzultácie (kanonický — prípad Auditor-upfront)

- **Spúšťač.** `_run_navrh_round` → `_run_auditor_upfront_review`; Auditor vydá `kind=verdict`; `hole_found`
  pri náleze. **Zmena:** namiesto settle `awaiting_manazer` zavolá `_settle_for_consultation(source="auditor_upfront", verdict=...)`.
- **Vysvetli.** Engine dispatchne **jeden** AI-Agent ťah (`_consultation_directive`): „Auditor našiel tieto
  diery (prečítaj ich vo svojom vlákne). NEopravuj zatiaľ. Vydaj jeden `kind=consultation`: krátky `intro` +
  usporiadané `decisions[]` — KAŽDÝ nález ako vlastné rozhodnutie po ľudsky, 2–3 možnosti s dôsledkom, práve
  jedno odporúčané + jednoriadkové zdôvodnenie. STOP." Pri parse-faile → **fallback na dnešné správanie**
  (awaiting_manazer + surový verdikt + voľný „Uprav") — flaky ťah nikdy nezasekne build.
- **Možnosti + odporúčanie.** Napr.: *„Telegram notifikácie: vlastný bot (viac súkromia, treba založiť) /
  zdieľať existujúceho (hneď funguje). Odporúčam: zdieľať."*
- **Po jednom.** Cockpit číta poslednú `kind=consultation` + zaznamenané odpovede, vypočíta **kurzor** (prvé
  rozhodnutie bez odpovede), zobrazí **iba tú kartu** („Rozhodnutie 2 z 3", odporúčaná možnosť predznačená).
  Manažér klikne → akcia `decide` → zapíše sa `kind=answer`; ak ostávajú ďalšie → **re-block bez dispatchu**
  (čistá DB, **0 tokenov**); ďalšia karta.
- **Aplikuj.** Po poslednom rozhodnutí engine zarámcuje všetky rozhodnutia a dispatchne AI Agenta **raz**;
  ten prepíše `specification.md`/`design.md`, zavrie `kind=gate_report`; `_run_navrh_round` znova spustí
  Auditora. Čistý verdikt → `_settle_phase_boundary` (dial riadi); otvorená diera → znova konzultácia,
  ohraničené re-consult stropom.

**Cena: presne dva AI-Agent ťahy na konzultáciu** (jeden vyrobí karty, jeden aplikuje) — kliky medzitým 0 tokenov.

---

## 5. Zmeny v orchestrátore + dátový model

**Dátový model (`backend/db/models/pipeline.py`):**
- `MESSAGE_KIND_VALUES` + `"consultation"` (správa `ai_agent→manazer`, fronta rozhodnutí v JSONB `payload`).
- `BLOCK_REASON_VALUES` + `"decision_needed"` (odlíši kartičky od voľnotextovej otázky). Existujúci
  `_clear_block_reason_on_unblock` ho už čistí — netreba nový listener.
- **Žiadny nový stĺpec na PipelineState** — kurzor sa **odvodzuje z append-only logu** (audit trail, žiadny
  mutovateľný stav). Dve rozšírenia CHECK hodnôt → jedna malá migrácia.

**Status blok (`backend/services/pipeline_status.py`):**
- `BLOCK_KINDS` + `"consultation"` (mimo `_QUESTION_KINDS`, lebo konzultácia nemá `question`).
- `consultation: Optional[ConsultationBlock]` na `PipelineStatusBlock`, kde
  `ConsultationBlock = {id, intro, source, decisions[]}` a
  `ConsultDecision = {key, question, explanation, options[{id,label,detail,recommended}], rationale, allow_free_text}`.
- Validačné pravidlo: `consultation` vyžaduje neprázdne `decisions` s práve jednou odporúčanou možnosťou.
  `PIPELINE_STATUS_JSON_SCHEMA` sa regeneruje z modelu (grammar-constrainuje výstup zadarmo).

**Orchestrátor (`backend/services/orchestrator.py`):**
- `_consultation_directive(...)` — sused `_auditor_upfront_directive`.
- `_settle_for_consultation(*, source, verdict=None, ...)` — spoločný vstup; dispatchne konzultačný ťah,
  pri parse-faile fallback, inak settle `blocked/decision_needed`.
- `_run_auditor_upfront_review` vráti **verdikt blok** (nie len bool), aby konzultácia dostala nálezy.
- akcia `decide` v `_ACTIONS` (nie v `_ADVANCING_ACTIONS`); handler podľa vzoru `answer` — zapíše rozhodnutie,
  ak ostávajú → re-block **bez** `_begin_dispatch`; pri poslednom → zarámcuj všetky rozhodnutia a dispatchni.
- `determine_available_actions`: pri `decision_needed` ponúkni `{decide, ask}`, **potlač** surové `answer`/`uprav`.
- re-consult strop (zrkadlí `AUDITOR_LOOP_MAX`).

**Znovupoužité bezo zmeny:** blocked→odpoveď→re-dispatch slučka, sole-mutator `apply_action`, single-flight
dispatch, structured-output + parse-retry, payload pump, dial-settle, teplá `--resume` relácia.

### 5.1 Korektnosť priradenia odpovedí (verify-round hardening)

Adversariálna previerka odhalila kolíziu, keď re-konzultácia **znovapoužije** `consultation.id` (LLM nemá
záruku unikátnosti): id-scopované priradenie odpovedí by zmiešalo staré + nové → predčasný dispatch s
neúplnou sadou rozhodnutí. Tri invarianty:

1. **Izolácia medzi konzultáciami — SEQ-scoping (nie id-scoping).** Odpovede patria konzultácii, ktorej
   správa má `seq = S`, práve keď ich `decide`-záznam má `seq > S`. Re-konzultácia dostane novú správu s
   vyšším `seq`, takže staré odpovede sú automaticky mimo rozsahu. `consultation.id` je **už len audit
   štítok** — korektnosť na jeho unikátnosti **nezávisí**. Zrkadlené BE (`_latest_consultation` vracia
   `(payload, seq)`, `_consultation_answers(after_seq)`) aj FE (`answeredLabels(afterSeq)`).
2. **Unikátne `decision.key` v rámci konzultácie** — `ConsultationBlock` má `@model_validator`, ktorý dva
   rovnaké kľúče odmietne pri parse-time (inak by jedno rozhodnutie prepísalo odpoveď druhého).
3. **Agregácia iteruje aktuálne `decisions`** (nie všetky odpovede), takže `dispatch_directive` nikdy
   nepritiahne kľúč mimo aktuálnej konzultácie.

Regresie: `tests/test_orchestrator_v2_consultation.py` (re-konzultácia s reused id+keys; duplicitné kľúče
odmietnuté) + FE `test_DecisionCardStack.test.tsx` (re-konzultácia začína načisto).

---

## 6. UI — rozhodovacie karty

- **`AuditorUpfrontReview.tsx` degradovaný** z akčnej plochy na tichý zbalený „Detail (Auditor)" (audit
  záznam / „prečo sa pýtame"). Surový verdikt ostáva viditeľný pre power-userov, ale nie je akčný.
- **Nový `DecisionCardStack.tsx`** (pri `blocked/decision_needed`): číta poslednú `kind=consultation` +
  zaznamenané `decide` odpovede, vypočíta kurzor, zobrazí **jednu kartu**: postup „Rozhodnutie k z N",
  otázka po ľudsky, vysvetlenie, možnosti ako tlačidlá (odporúčaná s odznakom **„Odporúčané"** + zdôvodnenie),
  nepovinná poznámka, „Iná odpoveď" **iba** keď `allow_free_text` (opt-in únik, nikdy default), tlačidlo
  **„Rozhodnúť"** → `onAction("decide", {...})`. Vybavené rozhodnutia sa zbalia do stopy („✓ … → …").
- `PipelineActionBar`: pri `decision_needed` **neotvára** voľnotextový box; „Spýtať sa" ostáva (Manažér môže
  kartu predtým preskúmať).
- `ExchangePanel` banner: „Na rade: Manažér — rozhodni {k}/{N}". Karty žijú v záložke Návrh (a Verifikácia).

**Výsledok pre nešpecialistu:** rovnaký pocit „jedna obrazovka — jedno rozhodnutie" ako dialóg v Príprave;
predvolené odporúčanie, jasná reč, jedno potvrdenie, viditeľná stopa. **Tibor/Nazar nikdy nevidia surový
nález ani prázdny box.**

### 6.1 Akceptačné kritériá obrazovky (postrehy Manažéra, 2026-06-29 — ZÁVÄZNÉ)

Z reálneho crash-testu (build nex-agents stál pri TASK #3 na blokeri, obrazovka bola pre Manažéra
nečitateľná). Konzultačná obrazovka MUSÍ spĺňať:

1. **Jasný signál „BLOKER / treba rozhodnutie".** Banner ani telo dnes nepovedia, že build **stojí na
   probléme** — píše sa len generické „Na rade: Manažér — odpovedz AI Agentovi", čo vyzerá ako rutina.
   Treba výrazný stavový pruh, napr. **„⛔ Build stojí — treba tvoje rozhodnutie ({k}/{N})"** s odlišnou
   (varovnou) farbou, aby bolo na prvý pohľad jasné, že sa nedá pokračovať bez Manažéra.
2. **Zrozumiteľný jazyk — žiadny žargón.** Telo nesmie obsahovať technické výrazy (asyncpg, DeclarativeBase,
   alembic, DDL, sandbox, docker-compose…). AI Agent MUSÍ napísať **podstatu po ľudsky** tak, aby ju Tibor
   a Nazar pochopili (technický detail max. do zbaleného „Detail" disclosure pre znalca).
3. **Explicitná otázka + kto čo má urobiť.** V texte MUSÍ byť **jasná otázka** a jednoznačné „čo sa od teba
   čaká" — vrátane toho, čo spraví každé tlačidlo. Žiadny stavový report bez otázky.
4. **Zobraz SKUTOČNÚ blokujúcu otázku, nie starý report.** (Dnešný bug: Programovanie tab renderuje posledný
   `gate_report` — starý „TASK #2 hotová, pripravený na TASK #3" — a NIE samotnú blokujúcu `kind=question`
   /`kind=consultation` správu. Manažér tak ani nevidí, na čo má odpovedať.) Akčná plocha MUSÍ zobraziť práve
   tú správu, ktorá build zablokovala.

Tieto štyri body sú **akceptačné kritériá** Fázy 1 — bez nich obrazovka nie je použiteľná pre Tibora/Nazara.

---

## 7. Zovšeobecnenie (nielen Auditor)

Mechanizmus je **nezávislý od zdroja problému** — Auditorove nálezy sú len prvý a najhodnotnejší konzument.
Ten istý actor (AI Agent), tvar `consultation`, blok `decision_needed`, `DecisionCardStack`, verb `decide`
a aplikácia-prepracovaním slúžia **každému** rozhodnutiu počas buildu. Diskriminátor `source`
(`auditor_upfront | verifikacia_fail | build_blocker | agent_ambiguity`) len značí pôvod pre audit + ladí
znenie aplikačného pokynu. Zjednocuje to dnešné tri oddelené plochy (Príprava voľná otázka, Auditor verdikt,
mid-build otázka) do **jedného modelu** — Manažér sa naučí jednu interakciu.

---

## 8. Fázovanie

- **Fáza 1 (CR-A) — schéma + slučka, len `auditor_upfront`.** Backend (kind/block_reason + migrácia, modely +
  validácia, directive, `_settle_for_consultation`, `decide` verb + handler, re-consult strop, zmena návratu
  `_run_auditor_upfront_review`) + Frontend (`DecisionCardStack`, degradácia `AuditorUpfrontReview`, banner,
  potlačenie voľného boxu) + testy (parse-fail fallback, kurzor z logu, medzikliky 0 ťahov, finálny apply,
  re-gate). **Plný pytest** (zmeny zdieľaného status-bloku/orchestrátora). *Samostatne nasaditeľné — rieši
  crash-test bloker.*
- **Fáza 2 (CR-B) — zovšeobecnenie zdrojov** (`verifikacia_fail`, `build_blocker`, `agent_ambiguity`).
  Hlavne zmeny call-site + znenie pokynu; UI a verb už hotové.
- **Fáza 3 (CR-C) — leštenie:** soft-cap pri veľkom verdikte + dávkovanie, Telegram politika (ping na vstupe
  + finálny re-gate, nie na každý klik), metriky (konzultačné ťahy + čas Manažéra na rozhodnutie).

---

## 9. Riziká (a opatrenia)

1. **Slepá škvrna prekladu** (AI Agent prekladá kritiku vlastného návrhu) → pokyn vynúti vymenovať každý
   nález; surový verdikt viditeľný; Auditor re-review chytí zatajené (ohraničené stropom).
2. **Nekonečná slučka** verdikt→konzultácia → re-consult strop, potom voľnotextový stop (dnešné správanie ako podlaha).
3. **Desync kurzora** → kurzor pripnutý na id pôvodnej konzultácie; „ask" ťah nesmie re-emitnúť konzultáciu.
   ⚠️ **Do v4.43.4 to neplatilo (DEV-27, nájdené 08.10.2026 na NEX Inbox 1.7.0):** otázka z rozhovoru počas
   konzultácie išla ako bežné `ask` a vo fáze Návrh každé `ask` spustí celú fázu — agent prepísal Návrh aj
   Špecifikáciu, nasledovala nová previerka a kolo 2 z 5 po jedinom rozhodnutí z deviatich. Od v4.43.5 je to
   jeden ťah bokom (`_answer_during_consultation` v `orchestrator.py`, otázka nesie `during_consultation`):
   agent dostane pokyn odpovedať a dokumenty nemeniť, potom stavba čaká na tých istých kartách s tým istým
   kolom. Pokyn od Poradcu počas konzultácie ide na aktuálnu kartu do poľa „Pokyn pre AI partnera" (DEV-26).
4. **Single-flight** → len posledný `decide` dispatchuje; medzikliky čisté DB; dispatch_in_flight ako poistka.
5. **Otvorené nálezy bez čistých možností** → `allow_free_text` + univerzálna „Iná odpoveď" (nikdy slepá ulička).
6. **Tokeny/latencia** → spúšťa sa len keď problém build aj tak zastaví; kliky 0 tokenov; teplý kontext.
7. **Migrácia** → CR si nesie vlastnú malú migráciu (dve CHECK hodnoty).
8. **FE prod-build** → nový komponent treba `docker compose build frontend` (nginx statický bundle).

---

## 10. Otvorené otázky pre Manažéra (rozhodnutia)

S odporúčaniami; prosím o potvrdenie/úpravu pri revízii:

1. **Re-consult strop** — koľko kôl verdikt→konzultácia pred eskaláciou? *Odporúčam `AUDITOR_LOOP_MAX` (=5).*
2. **Veľký verdikt** (napr. 15 nálezov → 15 kariet) — soft-cap (~6–8) s dávkovaním zvyšku? *Odporúčam áno.*
3. **„Iná odpoveď"** — agentom-opt-in per rozhodnutie + univerzálny únik na každej karte? *Odporúčam oboje.*
4. **Surový Auditor verdikt** — ponechať viditeľný (zbalený „Detail"), alebo skryť nešpecialistovi? *Odporúčam zbalený disclosure.*
5. **Rozsah CR-A** — len `auditor_upfront` teraz, zvyšok v CR-B? *Odporúčam áno (helper navrhnúť source-general).*
6. **„Spýtať sa" počas konzultácie** — povoliť (Manažér preskúma kartu pred rozhodnutím)? *Odporúčam áno, ask-ťah nesmie re-emitnúť konzultáciu.*
7. **Telegram** — ping pri vstupe do `decision_needed` + finálnom re-gate, nie na každý klik? *Odporúčam áno.*
