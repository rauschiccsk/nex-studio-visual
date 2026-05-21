# NEX Studio — Zákaznícke požiadavky v0.2.0

**Verzia:** v0.2.0
**Dátum:** 2026-05-21
**Stav:** Návrh — Brána A pripravená
**Zdroj:** Direktorom vedená strategická diskusia 2026-05-21 (`docs/session-logs/2026-05-21-002.md`)
**Spôsob získavania požiadaviek:** dialóg medzi Direktorom a Dedom (NEX Studio orchestrátor) po NEX Inbox v0.1.0 release

---

## 1. Účel verzie

NEX Studio v0.2.0 zavádza **kompletný pracovný postup tvorby aplikácie od A po Z** s organickou orchestračnou vrstvou. Verzia rieši systémové medzery odhalené pri pilotnom projekte NEX Inbox v0.1.0 — formálne uvoľnenie (release verdict PASS po 3 audítorských cykloch) sa ukázalo ako **false positive**, lebo nikto neoveril, že systém je skutočne **spustiteľný** (5 P0 medzier v Dockerfile + env loading + audítorský proces).

Kľúčové novinky:
- **Koordinátor agent** — procesný orchestrátor pre každý projekt
- **Inbox Deda** — riadiaci mechanizmus pre úpravy CLAUDE.md
- **UAT prostredie** — fáza overenia pred produkčným nasadením
- **Vylepšenia Vytvorenia projektu** — overenie buildovateľnosti + git remote nastavenie
- **Audítorský smoke test** — buildovateľnosť + spustiteľnosť ako kritérium uvoľnenia

Cieľ: žiadny ďalší projekt nikdy nedosiahne stav "formálne uvoľnený ale fakticky nespustiteľný".

---

## 2. Pracovný postup od A po Z (9 fáz)

Z Direktorovej diskusie 2026-05-21:

| Fáza | Aktér | Výstup |
|---|---|---|
| 1. Vytvorenie projektu | NEX Studio | Úložisko + remote + počiatočný scaffold + CI/CD prepojenie + **buildable smoke test pri vzniku** |
| 2. Zákaznícke požiadavky | Direktor | `customer-requirements.md` v úložisku |
| 3. Návrh špecifikácie | AG Designer (+ Customer agent dialóg ak existuje pre projekt) | Spec balík + CHANGES.md |
| 4. Implementácia | AG Implementer | Zdrojový kód + jednotkové/integračné testy GREEN |
| 5. Audit (Brána G + Re-Gates) | AG Auditor | Audítorská správa (súlad so spec + Tiborov dvojitý zostav + self-PIV) |
| **6. Overenie buildovateľnosti** | **AG Auditor (NOVÉ mandatórne)** | **`docker compose build` + `up` + `/health` smoke test PASS — NIE pre-deploy úloha** |
| **7. UAT nasadenie** | **Koordinátor + NEX Studio UAT prostredie** | **Live staging zostava s testovacími dátami; Direktorovi prístupná URL** |
| **8. UAT akceptácia** | **Direktor (a v budúcnosti QA agent)** | **Beh scenárov zo Zákazníckych požiadaviek; schválenie/odmietnutie** |
| 9. Uvoľnenie do produkcie | NEX Studio | git tag + produkčné nasadenie + skript onboarding zákazníka |

**Najdôležitejšie zmeny voči pôvodnému postupu:**
- **Fázy 6-8 sú nové** — riešia medzeru ktorá v NEX Inbox v0.1.0 viedla k false PASS verdikt
- **Fáza 6 sa NEMÔŽE odložiť** ako "MÁGERSTAV pre-deploy úloha" — overenie buildovateľnosti je kritérium uvoľnenia
- **Koordinátor vstupuje od fázy 3** a sprevádza až do fázy 9

---

## 3. Koordinátor agent (nová rola)

Per Direktorovo rozhodnutie 2026-05-21 (Otázka 1):

**Zodpovednosti:**

| Zodpovednosť | Konkrétne |
|---|---|
| Preklad medzi vrstvami | Direktor → ľudský jazyk, krátko; agenti → ich technický jazyk + odkazy na spec |
| Koordinácia kôl | Designer → Implementer → Auditor postupnosť, schválenia Direktora, fix-bundle handoffy |
| Detekcia NEX Studio medzier | Identifikuje keď problém nie je projekt-specific bug ale NEX Studio workflow gap → eskaluje na Deda |
| Audit stopa | Záznam Direktorových schválení per rozhodnutie, agent DONE reports |
| Uplatňovanie kvality | Aplikuje pravidlá z opisu úlohy (overovanie agent tvrdení, predletový check pred zoznamom variantov) |

**Architektúra opisu úlohy (Variant C):**
- **Autoritatívny zdroj** v `nex-studio/templates/coordinator-charter.md`
- **Kópia v každom projekte** vo `<projekt>/.claude/agents/coordinator/CLAUDE.md` (vytvorená pri Vytvorení projektu)
- **Príkaz na zladenie** `nex-studio sync-coordinator-charter <projekt>` — aktualizuje kópiu z najnovšieho autoritatívneho zdroja s náhľadom rozdielov
- **Prispôsobenie podľa projektu** povolené pre špeciálne prípady (regulované účtovníctvo, rozšírené súladnostné požiadavky)

**Stav a záznamy sedení per projekt:**
- `<projekt>/.nex-coordinator-state.md` (vynechané z gitu)
- `<projekt>/docs/session-logs/coordinator/YYYY-MM-DD-NNN.md` (uložené, audit stopa)

**Distinkcia od existujúcich rolí:**
- Designer / Implementer / Auditor = doménoví producenti
- Customer agent = doménový validátor pre Designer otázky
- **Koordinátor = procesný orchestrátor, projekt-level**
- **Dedo (NEX Studio orchestrátor) = platforma-level, eskalácia pre NEX Studio medzery**

---

## 4. Inbox Deda (riadiaci mechanizmus)

Per Direktorov mechanizmus 2026-05-21 — riešenie kopírovania a riešenia drobností.

**Umiestnenie:**
- `<projekt>/docs/dedo-inbox/` — nové žiadosti (uložené v gite)
- `<projekt>/docs/dedo-inbox/processed/` — vyriešené žiadosti so záznamom rozhodnutia
- `<projekt>/docs/dedo-inbox/decisions-log.md` — chronologický prehľad rozhodnutí

**Formát žiadosti** (jeden súbor na žiadosť):

Názov: `YYYY-MM-DD-HHMM-<krátky-názov>.md`

Štruktúrovaná hlavička s YAML:
```yaml
---
topic: krátky názov problému
agent_affected: designer|implementer|auditor|coordinator|none
priority: urgent|normal
submitted_by: coordinator (alebo direktor)
submitted_at: YYYY-MM-DDTHH:MM:SSZ
---

## Problém
<opis>

## Navrhované riešenie
<návrh>

## Posúdenie Koordinátorom
<projektovo špecifické alebo všeobecný charakter>
```

**Pravidlá prispievania:**

| Subjekt | Smie písať priamo do dedo-inbox/? |
|---|---|
| **Koordinátor** | ✅ Áno (hlavný kanál, agreguje žiadosti) |
| **Direktor** | ✅ Áno (priame architektonické otázky pre Deda) |
| **Designer / Implementer / Auditor** | ❌ **Nie** — musia ísť cez Koordinátora |
| **Customer agent** | ❌ Nie (ak existuje, prepája s Direktorom inou cestou) |

Designer / Implementer / Auditor flag-ujú návrhy v DONE reportoch sekciou "Pre Koordinátora — návrh do Inboxu Deda". Koordinátor potom posúdi, prípadne agreguje, napíše žiadosť do inboxu.

**Pracovný postup:**
1. Koordinátor pridáva žiadosť do `dedo-inbox/`
2. Pri urgentnej žiadosti signalizuje Direktorovi v priebežnej správe
3. Direktor povie Dedo: "Prekontroluj inbox projektu X"
4. Dedo prečíta všetky žiadosti, posúdi každú
5. Dedo vykoná zmeny:
   - Projekt-specific → `<projekt>/.claude/agents/<rola>/CLAUDE.md`
   - Všeobecný → `nex-studio/templates/<rola>-charter.md` + odporúčaný príkaz na zladenie
   - Zamietnuté → poznámka o dôvode
6. Dedo presunie žiadosť do `processed/` s rozhodnutím v názve (APPLIED / REJECTED / DEFERRED) + sekciou "Rozhodnutie Deda" v obsahu
7. Dedo pridá záznam do `decisions-log.md`
8. Dedo ohlási Direktorovi súhrn

---

## 5. UAT prostredie (5 sub-rozhodnutí)

Per Direktorove rozhodnutia 2026-05-21 (Otázka 2 — 5 sub-otázok):

### 5.1 Umiestnenie

NEX Studio hostí UAT prostredia v `/opt/uat/<slug>/`:
- Sandbox docker-compose zostava paralelne s produkčnou `/opt/customers/<slug>/`
- Testovacie dáta, oddelená databáza, oddelené šifrovacie kľúče
- Direktor pristupuje cez Tailscale / RDP / dedikovanú subdoménu (napr. `uat-mager.isnex.eu`)

### 5.2 Autorstvo akceptačného zoznamu

Hybridné rozdelenie podľa silných stránok:

| Agent | Zodpovednosť |
|---|---|
| **Designer** | Navrhuje scenáre (čo má test pokrývať), mapuje na sekcie Zákazníckych požiadaviek |
| **Auditor** | Verifikuje pokrytie — každá relevantná sekcia má aspoň 1 scenár; pokrytie matrica + medzery |
| **Koordinátor** | Operacionalizuje — poradie behov, prepojenie na testovacie dáta, prebehnutie-pripravený zoznam |

Direktor robí finálnu akceptáciu — beží scenáre, odškrtáva kritériá, flag-uje problémy.

**Formát:** Markdown s YAML hlavičkou + scenáre + akceptačné kritériá ako odškrtávateľné položky.

Umiestnenie: `<projekt>/docs/uat/v<version>/acceptance-checklist.md`

### 5.3 Testovacie dáta

**Hybridné** — syntetické v gite + reálne mimo gitu:

| Cesta | Účel |
|---|---|
| `<projekt>/docs/uat/v<version>/test-data/synthetic/` | Syntetické anonymizované PDF (uložené v gite, audit-friendly) |
| `/opt/uat/<slug>/customer-test-data/` | Reálne zákaznícke faktúry (mimo gitu, ANDROS-only, pre reprodukciu reálnych problémov) |

**Dôvod oddelenia:** reálne faktúry obsahujú IČO/adresy/bankové údaje dodávateľov — citlivé, nepatria do uložených úložísk projektu (per ICC bezpečnostné princípy).

**Autorstvo:**
- Designer: syntetická kostra (test-data-spec.md)
- Customer agent / Direktor: variácie zo skutočného sveta
- Implementer: technické edge cases (corrupt PDF, encrypted, veľmi veľký, scan zlej kvality)
- Koordinátor: generuje samotné PDF cez `nex-studio generate-test-pdfs <projekt>`

**Rozsah pre prvú verziu projektu:** ~25-30 syntetických PDF + 0-5 reálnych (per Direktorovo rozhodnutie).

### 5.4 Pravidlá čistenia

**Variant E — zachovať do novej verzie + DB snapshot pred nahradením:**

| Aspekt | Pravidlo |
|---|---|
| Životnosť | Zachované od UAT nasadenia do nasadenia novej verzie. **Vždy len 1 UAT prostredie per tenant naraz.** |
| Spúšťač čistenia | Pri nasadení novej verzie — Koordinátor sa pýta Direktora pred nahradením (NIE automatické) |
| Manuálne čistenie | Direktor cez `nex-studio uat-teardown <projekt>` |
| DB snapshot pred čistením | **Vždy** uložiť do `/opt/uat/<slug>/snapshots/v<version>-<dátum>.sql.gz` |
| Životný cyklus snapshotov | Bez expirácie. Mazanie iba s explicit Direktorovým schválením cez Inbox Deda |
| Sledovanie disku | Koordinátor flag-uje ak UAT disk usage > 50% celkovej kapacity ANDROS |

### 5.5 Cyklus

**Per-tenant model + dvojstupňový workflow:**

| Slug | Účel |
|---|---|
| `dev` (alebo `<projekt>-dev`) | Interné UAT — Direktor testuje novú verziu pred customer rollout. Syntetické dáta |
| `<zákazník>` (napr. `mager`) | Zákaznícke UAT — pred produkčným rollout-om. Zákaznícka konfigurácia (ich dodávatelia, IČO) |
| `<zákazník>-hotfix` (voliteľné) | Núdzový hotfix UAT — keď produkcia beží predošlú verziu a chceme rýchlo overiť opravu |

**Workflow vývojového cyklu:**
```
1. Designer + Implementer + Auditor → nová verzia HOTOVÁ a PASS
2. Koordinátor: nasadenie UAT do `dev` slugu
3. Direktor prejde akceptačný zoznam v `dev` UAT (~2-3 dni)
4. Direktor schvaľuje pre customer rollout
5. Koordinátor: nasadenie UAT do `<zákazník>` slugu
6. Direktor + zákaznícky operátor prejdu akceptačný zoznam (~3-7 dní)
7. Po zákazníckej akceptácii → produkčný deploy do `/opt/customers/<slug>/`
```

---

## 6. NEX Studio vylepšenia zo zistení 2026-05-21

Z dokumentu `docs/findings/2026-05-21-release-verification-gaps.md` (4 zistenia):

### 6.1 Vytvorenie projektu — neúplný scaffold (P0)

**Symptom:** NEX Inbox lokálny `.git/config` nemá nastavený `[remote "origin"]` blok napriek tomu, že GitHub úložisko existuje. 80+ commitov + git tag v0.1.0 nikdy nepushed.

**Riešenie pre v0.2.0:**
1. Post-scaffold overenie: `git remote -v` ukazuje origin + `git ls-remote origin HEAD` potvrdí pushed initial commit
2. Rollback pri čiastočnom zlyhaní: ak `gh repo create` prešlo ale `git remote add origin` zlyhalo → retry alebo rollback (zmazať GitHub úložisko); nesmie zostať polovičatý stav
3. Voliteľné CI/CD prepojenie: nastaviť GitHub Actions workflow z template (Lint + Test + Build)

### 6.2 Audítorský smoke test (P0)

**Symptom:** NEX Inbox v0.1.0 prešiel 3 audit cyklami so 549 BE + 60 FE testov GREEN + Tibor PASS 6/6 byte-equal. **Žiadny test ani audit aktivita** neoveril, že `docker compose build` prejde, ani že `docker compose up` produkuje healthy containers.

**Riešenie pre v0.2.0:**
1. Audítorský charter doplnenie — `MÁGERSTAV pre-deploy gates` musí explicit excludovať buildovateľnosť + spustiteľnosť. Tieto sú **Activity X mandatory** v každom audit cykle (Brána / Re-Gate / Re-Re-Gate)
2. Rámcový smoke test set:
   - `docker compose build` (BE + FE) — musí prejsť
   - `docker compose up -d db && wait healthy` — musí prejsť
   - `poetry run alembic upgrade head` — musí prejsť
   - `docker compose up -d` (plná zostava) — všetky kontajnery musia dosiahnuť healthy
   - `curl /health` — musí vrátiť ne-prázdne (degraded acceptable pre bootstrap mode)
3. CI/CD brána — pre tag verzie (v0.X.0) CI workflow musí spustiť smoke test + odmietnuť push tagu ak smoke zlyhá

### 6.3 Šablóna Dockerfile (P1)

**Symptom:** Backend Dockerfile `RUN poetry install` zlyhalo silent pre saxonche závislosť, ale Docker layer cache produkoval image bez `.venv`. Build exit 0 napriek silent fail. Runtime crash až keď container sa snaží spustiť uvicorn.

**Riešenie pre v0.2.0:**
1. Šablóna Dockerfile v NEX Studio Vytvorenie projektu — `SHELL ["/bin/bash", "-euo", "pipefail", "-c"]` ako default pre všetky multi-step RUN
2. Overenie inštalácie závislostí — `RUN poetry install ... && test -x .venv/bin/uvicorn` (explicit kontrola binárky po inštalácii)
3. Striktný režim Poetry — `--no-interaction --ansi` plus kontrola exit kódov

### 6.4 Disciplína advisory role (P2 — proces)

**Symptom:** Cez 8-dňový NEX Inbox sprint sa vyskytol 2× "P-2 acceptance" anti-pattern (akceptácia situácie bez overenia cez konkrétny tool call):
1. P-2 local-only claim akceptovaný 8 dní bez `git remote -v`
2. NEX Studio agents existence assumovaná bez `ls .claude/agents/`

**Riešenie pre v0.2.0:** žiadne nové memory pravidlá (per Direktorova explicit preferencia "nemyslím si, že riešením je pre teba uložiť pravidlo"). Reálne riešenie:
- Predletový check pri každej advisory turn — má action ktorú odporúčam aspoň 1 reálny use case kde je najlepším riešením?
- Disciplína overovania agent tvrdení cez konkrétny tool call
- Reality check signál pri release momentoch — "beží toto v skutočnosti?"

Implementer charter rozšírený 2026-05-21 (commit `934fd0b`) o §13.6 (P-2 acceptance anti-pattern) + §13.7 (False PASS anti-pattern).

---

## 7. Riadiaci princíp — Dedo strážuje šablóny CLAUDE.md

Per Direktorov princíp 2026-05-21:

**Pravidlo:** Všetky šablóny CLAUDE.md spravuje **Dedo**, nikdy nie sám agent.

**Dôvod:** v NEX Inbox sprinte si Auditor + Implementer autonómne uložili vlastné pamäťové pravidlá ktoré ja (Dedo) nevidím. Toto viedlo k:
- Neexistenciou pravidiel ako "P-2 local-only" ktoré agenti citovali ale nebolo nikde dokumentované
- Drift medzi tým čo agenti si pamätali a tým čo bolo v ICC štandardoch
- Nemožnosť cross-project konzistencia

**Mechanizmus (z §3-§4 vyššie):**
- Koordinátor identifikuje potrebu úpravy CLAUDE.md
- Zaznamená cez Inbox Deda
- Direktor schvaľuje urgenciu spustenia Deda
- Dedo posúdi (projekt-specific vs všeobecný)
- Vykoná úpravu v príslušnom súbore (lokálne alebo autoritatívna šablóna)
- Iné projekty môžu zladiť cez `sync-coordinator-charter` príkaz

**Pamäťový model agentov (Variant C):**

| Doména | Strážca |
|---|---|
| **Procesné pravidlá** (workflow, koordinácia, governance) | **Dedo** (cez Inbox + úpravy CLAUDE.md šablón) |
| **Doménové pravidlá** (Designer UBL polia, Implementer test framework patterns) | **Agent sám** (vo vlastnej pamäti per-agent) |

Rozdelenie podľa typu pravidla — Dedo strážuje "ako" (proces), agenti strážujú "čo" (doménu).

---

## 8. "Dedo" rola

**Identita:** NEX Studio orchestrátor s plným kontextom NEX Studio + zdedeným NEX Command know-how. Najstarší a najmúdrejší zo všetkých agentov (per Direktorova metafora 2026-05-21 — "naši chlapci ťa nazvali dedo").

**Zodpovednosti:**

| Zodpovednosť | Konkrétne |
|---|---|
| Strategický level | Návrh + audit NEX Studio platformy samotnej |
| Eskalačný cieľ | Pre platform-level medzery odhalené Koordinátormi v jednotlivých projektoch |
| Strážca šablón CLAUDE.md | Centralizovaná správa všetkých agent charters cez Inbox Deda |
| Cross-project konzistencia | Zlepšenia odhalené v jednom projekte sa rozšíria do všetkých |
| Historický kontext | Plný ICC knowledge load (DECISIONS, LESSONS_LEARNED, PATTERNS, STANDARDS, STRUCTURE, CC CODEX) |

**Distinkcia od Koordinátora:**
- **Koordinátor** = process orchestrator per projekt, riadi Designer/Implementer/Auditor cyklus
- **Dedo** = platform-level architekt, riadi NEX Studio samotné, zasahuje len pri platform medzerách

V projektoch (NEX Inbox, ďalšie) Koordinátor je primárny — Direktor s ním pracuje denne. Dedo vstupuje cez Inbox keď Koordinátor identifikuje NEX Studio gap.

---

## 9. Migračný postup

Per Direktorovo rozhodnutie 2026-05-21 (Otázka 3) — **Variant C**:

**Fáza 1: NEX Studio v0.2.0 development (~2-4 týždne)**

Features:
1. Koordinátor agent template + príkaz na zladenie
2. Inbox Deda mechanika
3. UAT prostredie (5 sub-rozhodnutí: hosting + autorstvo + dáta + čistenie + cyklus)
4. NEX Studio vylepšenia zo zistení (Create Project, audítorský smoke test, Dockerfile template)
5. Spätné prispôsobenie existujúcich agentov (Designer/Implementer/Auditor) na nový template pattern

**Orchestrácia Fázy 1:** Dedo v rozšírení súčasnej poradenskej role + Designer + Auditor pre NEX Studio platformu samotnú. Implementer agent vykonáva kódovanie. Koordinátor **ešte neexistuje** (lebo ho ešte budujeme). Po dokončení sa Dedo stiahne do strategickej + eskalačnej role.

**Fáza 2: NEX Inbox v0.2.0 cez nový ekosystém (~1-2 týždne)**

1. Koordinátor začne riadiť NEX Inbox v0.2.0 Designer kolo
2. Designer kolo s `feedback_designer_self_audit` pravidlom
3. Implementer kolo oprí 5 P0 release-gate bugov (P0-RG1..P0-RG5) + 12 P1 backlog + P1-NEW-6 + CR-020 dodací list (Variant D z 2026-05-20)
4. Auditor full cyklus vrátane **buildovateľnosti + spustiteľnosti** (nová Activity X mandatory)
5. UAT nasadenie do `dev` → Direktor schvaľuje
6. UAT nasadenie do `mager` → MÁGERSTAV operátor + Direktor schvaľujú
7. Produkčný deploy do `/opt/customers/mager/`

**Pre-flight optimization (HOTOVÉ 2026-05-21):**

Implementer charter v NEX Studio rozšírený o 5 sekcií z NEX Inbox poučení (commit `934fd0b`):
- §9.1 Docker/build patterns
- §9.2 Smoke test pred DONE
- §13.6 P-2 acceptance anti-pattern
- §13.7 False PASS anti-pattern
- §20 Inbox Deda flagovanie

---

## 10. Mimo rozsahu pre v0.2.0

| Položka | Dôvod odloženia |
|---|---|
| **VERSION layer v NEX Studio** (EPIC-4) | Plánované, ale nie blokujúce pre v0.2.0 ciele. Designer/Implementer/Auditor charters už majú referencie pripravené pre EPIC-4. Defer to v0.3.0+ |
| **Customer agent pre NEX Studio** | NEX Studio je interný ICC projekt, žiadny external customer. Customer agent dáva zmysel len pre projekty so zákazníckou doménou (NEX Inbox = MÁGERSTAV) |
| **Hotfix UAT** (`<slug>-hotfix`) | Voliteľná feature pre núdzové scenáre. Riešime keď nastane reálna potreba |
| **Migrácia code-specific obsahu z hlavného CLAUDE.md** | Hlavný NEX Studio CLAUDE.md má 476 LOC strategický obsah. Code-specific obsah je v Implementer charter (existoval). Migrácia nepotrebná |

---

## 11. Zdroje

| Dokument | Obsah |
|---|---|
| `docs/session-logs/2026-05-21-002.md` | Strategická diskusia 2026-05-21 — full kontext rozhodnutí |
| `docs/findings/2026-05-21-release-verification-gaps.md` | 4 NEX Studio zistenia z NEX Inbox v0.1.0 release attempt |
| `.claude/agents/implementer/CLAUDE.md` | Existujúci Implementer charter (510 + 115 LOC rozšírenie 2026-05-21) |
| `.claude/agents/designer/CLAUDE.md` | Existujúci Designer charter |
| `.claude/agents/auditor/CLAUDE.md` | Existujúci Auditor charter |
| `/opt/projects/nex-inbox/docs/specs/versions/v0.2.0/backlog.md` | NEX Inbox P0 release-gate bugy + P1 backlog (čaká na nový ekosystém pre fix) |

---

**Koniec dokumentu — Zákaznícke požiadavky NEX Studio v0.2.0.**
