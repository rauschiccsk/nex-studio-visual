# F-007 — Gate E (Customer review) v cockpite

> NEX Studio v0.2.0 — dorieši odložený "Gate E cutover" (F-007 Phase 5).
> **Status:** DESIGN — posvätené Directorom 2026-06-05 (3 rozhodnutia).
> Autor návrhu: Dedo. Implementácia: Implementer (plan-first). Spec SK, kód EN.

## 1. Účel

Gate E = zákaznícka previerka návrhu **pred Programovaním**. Agent **Zákazník**
(pohľad bežného používateľa) systematicky preverí návrh, aby vytlačil na povrch
nedomyslené miesta; **Návrhár** ich musí buď vysvetliť (je pokryté) alebo opraviť
návrh. Pri regulated-ledger/payroll je **povinný**. Hodnota = externý
používateľský tlak na špecifikáciu (Tiborova logika).

Dnes cockpit nemá slučku Zákazník↔Návrhár (`gate_e` len dispatchne Zákazníka →
otázka ide Directorovi, nie Návrhárovi). Tento spec to dorieši.

## 2. Rozhodnutie 1 — slučku vedie orchestrátor

Orchestrátor (Koordinátor) **sprostredkúva Zákazník ↔ Návrhár** (hub-and-spoke —
agenti sa nevolajú priamo). Beh:

1. Zákazník položí otázku → orchestrátor ju pošle **Návrhárovi**.
2. Návrhár odpovie (vysvetlí, že je pokryté / **opraví návrh**) → orchestrátor
   odpoveď pošle späť **Zákazníkovi**.
3. Zákazník reaguje (digne hlbšie / ďalšia otázka) → opakuje sa.

Director **nesprostredkúva každú výmenu** (žiadne desiatky mikro-klikov) —
**dozerá na hraniciach kôl** + finálne uzavretie (§3, §4).

## 3. Rozhodnutie 2 — kolo = jeden okruh

Gate E pokrýva **7 okruhov** (per Customer charter §4.1): prihlásenie, hlavný tok,
moduly, obrazovky, chyby (NIB-XXX), edge-cases, integrácie.

- **Kolo = jeden okruh.** Orchestrátor v rámci okruhu vedie Zákazník↔Návrhár
  autonómne (viacero Q&A), kým Zákazník okruh neuzavrie.
- Na **hranici okruhu** orchestrátor zastaví a Directorovi ukáže:
  - **nálezy okruhu** (čo Zákazník našiel),
  - **riešenia Návrhára** (čo vysvetlil / opravil),
  - **veci na rozhodnutie Directora** (politiky — napr. povinná zmena hesla).
- Director na hranici: **schváli riešenia** / **rozhodne politiky** / **pokračuje**
  na ďalší okruh (alebo „ukonči Gate E").
- **Mid-okruh pauza:** keď Zákazník/Návrhár narazí na vec, ktorú **musí rozhodnúť
  Director** (politika), orchestrátor zastaví aj v strede okruhu a počká — nehádže.

## 4. Rozhodnutie 3 — coverage + koniec

- **Coverage:** všetkých 7 okruhov; každý modul/obrazovka/tok aspoň raz poriadne
  preverený (Zákazník sleduje v `.nex-customer-state.md`). Previerku neskracujeme.
- **Gate E sa uzavrie**, keď: všetky okruhy pokryté **A** všetky nálezy
  **vyriešené** (Návrhár vysvetlil pokryté / opravil návrh / Director rozhodol
  politiku). **Žiadny otvorený nález pred Programovaním.**
- Vtedy orchestrátor ukáže **finálny súhrn Gate E** (nálezy + ako boli vyriešené)
  → Director dá **finálne schválenie** → posun na `build`.
- **Director môže ukončiť skôr** („pokrytie stačí") — jeho rozhodnutie.
- **Otvorený nález** (Návrhár nevyriešil a Director nerozhodol) **blokuje
  uzavretie** Gate E.
- **Výstup:** Gate E súhrn (nálezy + riešenia) uložený ako audit záznam
  (`docs/specs/versions/v<X>/customer-dialogue.md` alebo gate-e-report).

## 5. Mechanika (pre Implementer plán)

- **Orchestrátorová Zákazník↔Návrhár slučka v `gate_e`:** nový vzor —
  agent↔agent výmena sprostredkovaná orchestrátorom v rámci jednej fázy. Reuse
  `invoke_agent_with_parse_retry`, `dispatch_directive`, `_coordinator_relay`
  pattern. Customer a Designer = samostatné claude sessiony (orchestrator_session
  per (project, role)).
- **Status-blok signály (§7.2):** Zákazník v bloku signalizuje napr.
  `kind=question` (otázka pre Návrhára), `kind=gate_report` + „okruh dokončený"
  (hranica kola, s nálezmi), a niečo ako „needs_director_decision" (politika →
  mid-okruh pauza). Presné polia dolaď v pláne + ja zladím charter §7.2.
- **Nálezy/riešenia** sa zaznamenávajú ako `pipeline_message` (stage=gate_e);
  poradie cez `seq`. Director-facing texty po slovensky, bežnou rečou (§7.2).
- **Director akcie na hranici:** schváliť (pokračovať / uzavrieť), rozhodnúť
  politiku (odpoveď), vrátiť. Mapovanie na cockpit akcie dolaď v pláne.

## 6. Cutover starého `/dialogue` (dorieši odložené Phase 5 items 2+3)

Tento cockpit Gate E **nahrádza** starý `/dialogue` model. Súčasťou:
- `/dialogue` FE → buď zrušiť, alebo prerobiť na **read-only Gate E pohľad** na
  `pipeline_message` (stage=gate_e). Žiadny druhý paralelný model.
- **Drop `dialogue_*` tabuliek** (backfill do `pipeline_message` už spravený v
  migrácii 052) + drift-test update + odstránenie mŕtveho dialogue kódu
  (model/schema/service/route/FE) per §9.6.
- **Sekvencovať:** najprv postaviť+overiť cockpit Gate E, **až potom** drop
  `dialogue_*` (nič nemazať, kým nový Gate E nefunguje).

## 7. Dedo (NIE Implementer)

Customer charter (`.claude/agents/customer/CLAUDE.md`) zladím na tento cockpit
model — Zákazník reportuje cez orchestrátor (nie starý `/dialogue` inject/approve),
§7.2 status-blok signály, slovenské Director-facing texty. Template + NEX Ledger.
Implementer sa `.claude/agents/**` nedotýka.
