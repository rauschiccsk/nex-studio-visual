# F-007 — Gate E (Customer review) v cockpite

> NEX Studio v0.2.0 — dorieši odložený "Gate E cutover" (F-007 Phase 5).
> **Status:** DESIGN — posvätené Directorom 2026-06-05; **revízia 2026-06-05** po
> live teste: per-otázka schvaľovanie, Návrhár neopravuje sám (nález → Koordinátor → Director).
> Autor návrhu: Dedo. Implementácia: Implementer (plan-first). Spec SK, kód EN.

## 1. Účel

Gate E = zákaznícka previerka návrhu **pred Programovaním**. Agent **Zákazník**
(pohľad bežného používateľa) systematicky preverí návrh, aby vytlačil na povrch
nedomyslené miesta; **Návrhár** ich musí buď vysvetliť (je pokryté), alebo — ak je
to medzera — **navrhnúť** opravu na schválenie Directorom (Návrhár neopravuje sám
počas Gate E, viď §2). Pri regulated-ledger/payroll je **povinný**. Hodnota = externý
používateľský tlak na špecifikáciu (Tiborova logika).

Dnes cockpit nemá slučku Zákazník↔Návrhár (`gate_e` len dispatchne Zákazníka →
otázka ide Directorovi, nie Návrhárovi). Tento spec to dorieši.

## 2. Rozhodnutie 1 — slučku vedie orchestrátor, schvaľuje sa po jednej otázke

Orchestrátor (Koordinátor) **sprostredkúva Zákazník ↔ Návrhár** (hub-and-spoke —
agenti sa nevolajú priamo). Beh **po jednej otázke**, každú dvojicu schvaľuje
Director:

1. Zákazník položí **jednu** otázku → orchestrátor ju pošle **Návrhárovi**.
2. Návrhár odpovie — **iba vysvetlí** (pokryté / medzera). Ak je to medzera,
   **len NAVRHNE** riešenie textom; **needituje žiadny spec súbor**.
3. Orchestrátor odpoveď ukáže **Directorovi**, ktorý rozhodne:
   - **(A) bez medzery** — odpoveď OK → Director schváli „v poriadku" →
     **ďalšia otázka**.
   - **(B) Návrhár našiel medzeru** — návrh riešenia ide **Koordinátorovi** →
     Koordinátor prekontroluje + dá **odporúčanie** → **Director** rozhodne
     **opraviť / ponechať** → rozhodnutie ide **cez Koordinátora** Návrhárovi
     (nie Director→Návrhár priamo) → Návrhár **až teraz** opraví (alebo ponechá)
     → **ďalšia otázka**.

**Tvrdé pravidlo:** Návrhár **nikdy needituje spec sám** počas Gate E — výhradne
na základe Directorom schváleného pokynu `fix`, ktorý mu **doručí Koordinátor**
(vetva B). **Director nepíše Návrhárovi priamo** — pri náleze idú cez Koordinátora
**oba smery** (návrh hore, rozhodnutie dole), inak by Koordinátor vypadol z procesu.
Routine otázka-odpoveď (vetva A) ide priamo Directorovi. Žiadny autonómny beh
vnútri okruhu — **každá** výmena stojí na schválení Directora.

## 3. Rozhodnutie 2 — okruhy = organizácia previerky (nie jednotka schvaľovania)

Gate E pokrýva **7 okruhov** (per Customer charter §4.1): prihlásenie, hlavný tok,
moduly, obrazovky, chyby (NIB-XXX), edge-cases, integrácie.

- **Okruh = organizácia previerky.** Zákazník vedie otázky po okruhoch, ale
  **jednotkou schvaľovania je jedna otázka** (§2) — nie okruh. Žiadny autonómny
  beh vnútri okruhu.
- Na **hranici okruhu** Zákazník pošle `gate_report` (okruh dokončený + súhrn
  nálezov a ako boli vyriešené) → Director potvrdí prechod na ďalší okruh
  (alebo „ukonči Gate E").
- Politiky (napr. povinná zmena hesla) sú **nález ako každý iný** — idú cez vetvu
  B (§2): Návrhár navrhne → Koordinátor → Director rozhodne. Žiadne osobitné
  „mid-okruh" vetvenie netreba — každá otázka aj tak stojí na Directorovi.

## 4. Rozhodnutie 3 — coverage + koniec

- **Coverage:** všetkých 7 okruhov; každý modul/obrazovka/tok aspoň raz poriadne
  preverený (Zákazník sleduje v `.nex-customer-state.md`). Previerku neskracujeme.
- **Gate E sa uzavrie**, keď: všetky okruhy pokryté **A** všetky nálezy
  **vyriešené** (Návrhár na Directorom schválený pokyn opravil / Director rozhodol
  ponechať). **Žiadny otvorený nález pred Programovaním.**
- Vtedy orchestrátor ukáže **finálny súhrn Gate E** (nálezy + ako boli vyriešené)
  → Director dá **finálne schválenie** → posun na `build`.
- **Director môže ukončiť skôr** („pokrytie stačí") — jeho rozhodnutie.
- **Otvorený nález** (Director ešte nerozhodol opraviť/ponechať) **blokuje
  uzavretie** Gate E.
- **Výstup:** Gate E súhrn (nálezy + riešenia) uložený ako audit záznam
  (`docs/specs/versions/v<X>/customer-dialogue.md` alebo gate-e-report).

## 5. Mechanika (pre Implementer plán)

- **Per-otázka cyklus v `gate_e`:** orchestrátor po **každej** Návrhárovej
  odpovedi zastaví (`status=awaiting_director`) — nikdy nereťazí ďalšiu Zákazníkovu
  otázku bez schválenia Directora. Reuse `invoke_agent_with_parse_retry`,
  `dispatch_directive`, `_coordinator_relay`. Customer a Designer = samostatné
  claude sessiony (orchestrator_session per (project, role)).
- **Návrhár v Gate E needituje spec.** Dispatch Návrhárovi v `gate_e` ho
  inštruuje: „odpovedz / pri medzere LEN navrhni — NEUPRAVUJ žiadny súbor". Edit
  povolí až Directorom schválený pokyn `fix` (vetva B), ktorý príde ako directive
  („teraz uprav podľa schváleného návrhu").
- **Status-blok signály (§7.2):** Návrhárova odpoveď nesie `gap_found`
  (true/false); ak `true`, pridá `proposed_fix` (textový návrh, žiaden edit).
  Zákazník: `kind=question` (otázka), `kind=gate_report` (okruh dokončený +
  nálezy). Presné polia dolaď v pláne; charter §7.2 zladím ja.
- **Vetva B routing (cez Koordinátora OBOMA smermi):** pri `gap_found` orchestrátor
  pošle návrh **Koordinátorovi** na kontrolu + odporúčanie (reuse `_coordinator_relay`),
  výsledok Directorovi. Director rozhodne **opraviť** / **ponechať** → rozhodnutie
  ide **cez Koordinátora** Návrhárovi (NIE Director→Návrhár priamo) — `fix` directive
  Návrhárovi skomponuj zo schváleného návrhu + Koordinátorovho odporúčania.
  Vetva A: **schváliť odpoveď** → ďalšia otázka. Mapovanie na cockpit akcie dolaď.
- **Nálezy/riešenia** ako `pipeline_message` (stage=gate_e); poradie cez `seq`.
  Director-facing texty po slovensky, bežnou rečou (§7.2).

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
