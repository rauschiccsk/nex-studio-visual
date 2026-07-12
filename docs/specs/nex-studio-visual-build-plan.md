# NEX Studio Visual (v4) — Build plán

**Status:** NA SCHVÁLENIE (Director) · **Autor:** Dedo · **Dátum:** 2026-07-12
**Zdrojový dizajn:** vízia + 5 vylepšení settled 2026-07-10; multi-vizuál upresnenie 2026-07-12 (memory `project_nex_studio_visual`).
**Metodika:** waterfall — tento plán je roadmapa; každý CR dostane vlastný detailný dev-spec, keď sa doň pustíme.

---

## 0. Zhrnutie na jednu obrazovku

NEX Studio Visual (v4) je **samostatný pomenovaný projekt** odštiepený z NEX Studio v3 (`/opt/projects/nex-studio-visual`, vlastné repo/porty/nasadenie, koexistuje s v3 ako UAT aj PROD). Pridáva medzi **Špecifikáciu (Fáza 1)** a **Programovanie** novú **Fázu 2 — Vizuálnu konzultáciu**: na 2 monitoroch (chat s AI + živý sandbox) sa interaktívne stavajú a ladia **vizuály** projektu, kým nie sú vizuálne overené — čím sa **zabíja dorábka** po builde. „Vizuál" je prvotriedna **typovaná jednotka (FE = frontend appky, WE = webend/web pre kanál)**, projekt má 1..N vizuálov rôznych typov, každý je vlastný nasaditeľný frontend, ale **všetky zdieľajú JEDEN backend a JEDEN dátový model**. Po Fáze 2 sú schválené vizuály + dátový kontrakt + doplnená špecifikácia **záväzným vstupom buildu** (build ich rozšíri o logiku, nikdy neprekreslí). Stavia sa v 9 CR balíkoch: CR-0 fork projektu, CR-1 chodiaca kostra Fázy 2, CR-2 multi-vizuál + typy, CR-3..7 päť dohodnutých vylepšení, CR-8 perzistencia + dolad.

---

## 1. Kontext a cieľ

**Prečo:** 30 rokov Delphi = visual-first — hneď vidíš, čo vidí zákazník. NEX Studio v3 to stratilo: vývoj naslepo, živá appka až na konci → nesúlad s predstavou → **dorábky**. Fáza 2 validuje dizajn **vizuálne pred prvým riadkom logiky**, čím priamo posilňuje waterfall.

**Named-version model** (memory `project_nex_studio_named_version_projects`): každá veľká verzia = samostatný pomenovaný projekt (Studio v3 · Visual v4 · Titan v5 · King v6), koexistuje ako UAT+PROD, nič sa nemieša. v4 vzniká **forkom v3** presne podľa overeného DELPHI Studio playbooku. **v3 sa nedotkne.**

**Dizajn je settled** — tento plán ho iba prevádza na build; nič sa nere-dizajnuje. Otvorené ostávajú len implementačné detaily jednotlivých CR.

---

## 2. Produktový tvar (čo to robí)

| Fáza | Stav vo v4 |
|---|---|
| **1 — Špecifikácia** (Príprava/Návrh: rozhovor → zadanie → špecifikácia + DB schéma v návrhu) | **Nezmenená** oproti v3. |
| **2 — Vizuálna konzultácia** (NOVÁ) | Monitor 1 = Riadiace centrum (chat s AI). Monitor 2 = **živý sandbox** aktuálneho vizuálu. Director prechádza appku, hovorí čo zmeniť/pridať/opraviť; AI to **hneď aplikuje do sandboxu**; Director **vizuálne overí**. Iteruje cez **všetky vizuály** (FE/WE) projektu, kým nie sú vyladené a schválené. |
| **3 — Programovanie** | **Nezmenené v jadre**, ale štartuje zo **schválených vizuálov ako záväzného vstupu**: pridáva logiku + wiring **za** presné schválené obrazovky, nemení layout/navigáciu/flow (lock-with-escalation, §3.7). |
| **4 — Verifikácia · 5 — Nasadenie** | Nezmenené; koncová poistka navyše overí, že appka sedí so schváleným vizuálom. |

**Vizuál — prvotriedna jednotka:**
- **Typovaný:** `FE` (frontend appky — má každý projekt) · `WE` (webend — webshop, web pre mobilnú appku…). Systém typov **rozšíriteľný** pre budúce druhy.
- **1..N na projekt**, rôznych typov. Každý vizuál = vlastný nasaditeľný frontend s vlastným publikom/URL.
- **Zdieľaný backend + JEDEN dátový model** naprieč všetkými vizuálmi → jeden súdržný systém s viacerými tvárami, nie roztrúsené appky.

---

## 3. Architektúra

**3.1 Projekt = fork v3 (delphi-studio playbook).** Fork backend+frontend z `/opt/projects/nex-studio` (v2.0.0-dev HEAD) do nového repa `nex-studio-visual`; premenovať; vlastná DB/porty/CI/Docker. NEZahadzuje sa nič z v3 (Visual je nadstavba, nie osekanie — na rozdiel od DELPHI Studia, ktoré fázy odoberalo).

**3.2 Pipeline zmena — vloženie Fázy 2.** Nový stage medzi `navrh` a `programovanie` (pracovný názov `vizual`). Nedotkne sa release/deploy cesty (podobne ako kontrola/konzultácia sú „neviditeľné" pre release oracle). Vstup do Fázy 2 = schválená Špecifikácia; výstup = schválené vizuály + dátový kontrakt + doplnená špecifikácia.

**3.3 Sandbox → živý dev-server (kľúčová technická zmena, #4).** Fáza 2 beží **Vite dev-server (HMR)**, NIE prod nginx build — aby sa zmena premietla za <1 s (oneskorenie = AI rozmýšľa, nie rebuild). Základ existuje: `docker-compose.sandbox.yml` (beží reálnu appku živo+izolovane) + `consult_sandbox.py`. Visual ich sprodukční na **per-vizuál dev-server** + **monitor-2 kontext** (sandbox hlási, ktorý vizuál/obrazovku Director práve pozerá → „zväčši súčet" sa vyrieši ako pri otvorenej Delphi forme).

**3.4 Vizuál model.** Každý vizuál nesie: `typ (FE/WE)`, `názov`, `publikum`, `obrazovky` (screens), `stavy` (screen states, #1), `dátové väzby` (číta/zapisuje → #2), `sandbox náhľad`, `zamknutý kontrakt` (#5). Backend a dátový model sú **spoločné pre projekt**, nie per-vizuál.

**3.5 Dátový kontrakt (#2).** Auto-odvodí sa z reálnych vizuálov (čo každá obrazovka číta+zapisuje) → zosúladené entity (Faktúra/Dodávateľ/Platba + polia) naprieč VŠETKÝMI vizuálmi = blueprint pre DB + backend (viaže sa na `project_nex_studio_v3_flow_db_schema_in_design`). Viditeľná plocha je BINDING; backend smie dodať neviditeľné (audit/computed), nikdy nezahodí čo vizuál potrebuje. Zaznamená aj externé zdroje (SLSP/Genesis/Peppol). **Na konci Fázy 2 ľahký odklep entít.**

**3.6 Auto-fill špecifikácie (#3).** Vizuálna konzultácia premieňa ROZHOVOR na SPEC: každá schválená zmena → čistá požiadavka (nie surový chat), organizovaná po obrazovkách, odrážajúca FINÁLNY dohodnutý stav, čitateľná slovenčina, zaznamenáva aj schválenia („dobré, ďalej" = schválené-ako-je). Režim: **tichý background capture + čítanie na konci** (neodvádza pozornosť od vizuálu). Výsledok = kompletná, vizuálne validovaná špecifikácia = záväzný vstup buildu (kŕmi #5).

**3.7 Zamknutý vizuál = záväzný build input (#5), lock-with-escalation.** Po Fáze 2 sú schválený frontend + dátový kontrakt + spec ZÁVÄZNÝM vstupom: builder dostane schválený frontend ako **východiskový bod** (rozširuje/wiruje, NIKDY neprekresľuje/nereštyluje), pridáva len logiku+wiring **za** presné obrazovky, nesmie meniť layout/nav/flow. Ak realita naozaj vynúti vizuálnu zmenu → build **STOP + spýta sa Directora** (nikdy tichý drift, nikdy zaseknutie). Koncová poistka overí, že finálna appka sedí so schváleným vizuálom. Drží štrukturálne (frontend už existuje → drift by vyžadoval aktívne prepísanie = chytené) + ladí s pravidlom Implementer = deterministický vykonávateľ (`feedback_implementer_no_autonomy`).

**3.8 Perzistencia vizuálov.** Vizuál NEZmizne po vývoji — ostáva súčasťou projektu (ako Delphi formy), aby ho neskoršia **Konzultácia** mala k dispozícii (viaže sa na `project_nex_studio_konzultacia_sidecar`).

**3.9 Porty a nasadenie.** Voľný blok 9210–9250. Návrh (finálne potvrdiť voči port registry pri CR-0):
- **v4 PROD:** backend 9216 · frontend **9217** · db 9218 (nadväzuje na FE progresiu 9177/9197/9207/**9217**).
- **v4 UAT:** backend 9219 · frontend 9220 · db 9221.
- Koexistuje s v3 (9206/07/08 PROD, 9189/90/91 UAT). Vlastný CI, vlastný `scripts/deploy-*.sh`, vlastné Aktualizácie.

---

## 4. Päť vylepšení → funkcie (mapa na CR)

| # | Vylepšenie | Rozhodnutie (2026-07-10) | CR |
|---|---|---|---|
| 1 | Reálne dáta + stavy obrazoviek | (C) hybrid dáta + povinný checklist A–F na každú obrazovku + state-switcher | CR-3 |
| 2 | Dátový kontrakt ako zachytený výstup | auto-odvod z vizuálov → entity; ľahký odklep na konci | CR-4 |
| 3 | Každá vizuálna zmena → do špecifikácie | tichý background capture + čítanie na konci | CR-5 |
| 4 | Klikateľný/walkable prototyp + okamžitá reflexia | živý dev-server (HMR) + screen-level pointing teraz, element-level klik neskôr | CR-6 |
| 5 | Zamknutý vizuál = záväzný vstup buildu | lock-with-escalation + koncová poistka | CR-7 |

---

## 5. Build sekvencia (CR balíky)

Každý CR: samostatný, končí funkčným prírastkom, vlastný dev-spec pri štarte, Manažérsky odklep na hranici.

- **CR-0 — Fork + seed projektu.** Fork v3 backend+frontend+infra do `nex-studio-visual`, premenovať, vlastná DB/porty (§3.9)/CI/Docker, prvý deploy „prázdneho" v4 (identický s v3, len pomenovaný a na vlastných portoch). *Done:* v4 beží na 9217, DB connected, CI green, v3 nedotknuté.
- **CR-1 — Fáza 2: chodiaca kostra.** Nový stage `vizual` v pipeline + živý dev-server sandbox pre JEDEN vizuál + Director prechádza obrazovky na monitore 2 + AI aplikuje zmenu naživo (HMR <1 s). Zatiaľ jeden FE, happy-path. *Done:* projekt prejde Špecifikácia → Fáza 2 (žiadaj zmenu, vidíš ju) → build.
- **CR-2 — Multi-vizuál + typy.** Vizuál ako prvotriedna typovaná jednotka (FE/WE, rozšíriteľné); 1..N vizuálov/projekt; per-vizuál dev-server + prepínanie „ktorý vizuál tvarujem"; monitor-2 kontext (ktorý vizuál/obrazovka). *Done:* projekt s FE + WE, prepínam medzi nimi, oba žijú v sandboxe.
- **CR-3 — #1 Reálne dáta + stavy obrazoviek.** Hybrid dáta (AI seed + Director vloží reálne anonymizované vzorky) + povinný checklist A–F (stavy/edge-cases/tabuľky/formuláre/financie/zariadenie) + state-switcher vo vizuáli. *Done:* každú obrazovku preklikám cez jej stavy (plná/prázdna/loading/error/…).
- **CR-4 — #2 Dátový kontrakt.** Auto-capture čo každý vizuál číta+zapisuje → zosúladené entity naprieč vizuálmi + externé zdroje; ľahký odklep entít na konci Fázy 2. *Done:* na konci Fázy 2 vidím entity/polia = blueprint pre DB+backend.
- **CR-5 — #3 Auto-fill špecifikácie.** Tichý background capture zmien+schválení → kompletná čitateľná spec organizovaná po obrazovkách, finálny dohodnutý stav; čítanie na konci. *Done:* po Fáze 2 mám kompletnú vizuálne-validovanú špecifikáciu bez písania.
- **CR-6 — #4 Klikateľný/walkable + kontext.** Reálna navigácia (prejdem celú appku ako user; flow problémy: veľa klikov, slepé uličky, chýbajúci späť) + monitor-2 screen-level pointing. *Done:* appku prejdem ako používateľ; „urob súčet väčší" sa vyrieši na aktuálnej obrazovke.
- **CR-7 — #5 Zamknutý vizuál = záväzný build input.** Build štartuje zo schváleného frontendu (rozširuje, neprekresľuje); lock-with-escalation (STOP+spýtaj sa pri vynútenej vizuálnej zmene); koncová poistka (finálna appka vs schválený vizuál). *Done:* build za schválenými obrazovkami; pokus o drift zastaví a eskaluje.
- **CR-8 — Perzistencia + dolad.** Vizuály ostávajú súčasťou projektu pre neskoršiu Konzultáciu; desktop+mobil náhľad; skladanie z nex-shared (auto-konzistencia); leštenie. *Done:* Konzultácia nad hotovou verziou má vizuál k dispozícii.

**Poradie závislostí:** CR-0 → CR-1 → CR-2 sú základ (bez nich nič); CR-3..7 sa navrstvujú na kostru (dajú sa čiastočne preusporiadať podľa priorít); CR-8 na koniec.

---

## 6. Zámerne mimo scope (v4)

- Element-level klik-to-select (plný Delphi form-designer) — až po screen-level pointing (dohoda #4).
- Plný Genesis-style RAG / Delphi tooling — to je DELPHI Studio, nie sem.
- Zmena Fázy 1 (Špecifikácia) — ostáva ako v3.
- Multi-module architektúra — v4 drží v3 model.

---

## 7. Riziká a otvorené otázky (na doriešenie pri príslušnom CR)

- **Živý dev-server v izolovanom sandboxe** (HMR + per-vizuál) — hlavný technický risk CR-1; over PoC skoro.
- **Monitor-2 kontext** (ako sandbox spoľahlivo hlási aktuálny vizuál/obrazovku) — CR-2/CR-6.
- **Mock→real prechod backendu** pri lock-with-escalation — presné pravidlá „čo smie build pridať/nesmie zmeniť" doladiť v CR-7.
- **Port registry** — finálne čísla potvrdiť voči centrálnej evidencii pri CR-0.

---

## 8. Naming & porty (súhrn)

- **Projekt:** „NEX Studio Visual" · slug `nex-studio-visual` · `/opt/projects/nex-studio-visual`.
- **Porty (návrh):** PROD 9216/**9217**/9218 · UAT 9219/9220/9221.
- **Vetva/verzia:** vlastné repo, vlastná verzná os (v4.x); nezávislé od v3.
