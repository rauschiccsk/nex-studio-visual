# NEX Studio v0.2.0 — Stručný prehľad

**Verzia:** v0.2.0
**Dátum:** 2026-05-21
**Stav:** Návrh — Brána B (rozpracovaný development-spec)

---

## Čo NEX Studio v0.2.0 robí

NEX Studio v0.2.0 je **prvé veľké rozšírenie** platformy NEX Studio o organickú orchestračnú vrstvu. Verzia pridáva nového agenta (Koordinátor), zavádza riadiaci mechanizmus pre úpravy CLAUDE.md (Inbox Deda), pridáva fázu užívateľskej akceptačnej kontroly pred produkčným nasadením (UAT prostredie), a opravuje systémové medzery odhalené pri pilotnom projekte NEX Inbox v0.1.0 (Create Project workflow incomplete scaffold, audítorský smoke test gap, Dockerfile silent failure mode).

Cieľ verzie: žiadny ďalší projekt nikdy nedosiahne stav "formálne uvoľnený ale fakticky nespustiteľný".

---

## Pre koho je NEX Studio v0.2.0 určený

**Primárny používateľ:** Direktor Zoltán Rausch + interný ICC tím (NEX Studio nemá externého zákazníka, je interný development workbench).

**Pilotný projekt pre v0.2.0:** NEX Inbox v0.2.0 — bude prvý reálny test celého ekosystému (oprava 5 P0 release-gate bugov + 12 P1 backlog + CR-020 expansion pre dodacie listy). Migračný postup per Direktorovo rozhodnutie 2026-05-21 — Variant C (NEX Studio refactor prvý, NEX Inbox v0.2.0 cez nový ekosystém potom).

---

## Ako to bude fungovať — z pohľadu Direktora

Pracovný postup od A po Z (9 fáz):

```
1. Vytvorenie projektu        (NEX Studio + improvements)
       ↓
2. Zákaznícke požiadavky      (Direktor uloží)
       ↓
3. Návrh špecifikácie         (AG Designer + Customer agent ak existuje)
       ↓
4. Implementácia              (AG Implementer)
       ↓
5. Audit                       (AG Auditor — vrátane Tiborovho dvojitého zostava)
       ↓
6. Overenie buildovateľnosti  (AG Auditor — Activity X mandatory) [NOVÉ]
       ↓
7. UAT nasadenie              (Koordinátor → /opt/uat/<slug>/)   [NOVÉ]
       ↓
8. UAT akceptácia             (Direktor schvaľuje scenáre)        [NOVÉ]
       ↓
9. Uvoľnenie do produkcie     (NEX Studio → /opt/customers/<slug>/)
```

**Direktor komunikuje primárne s Koordinátorom** — ten preberá denné riadenie, prekladá Direktorove rozhodnutia agentom, podáva Direktorovi krátke zhrnutia bez technických detailov.

**Dedo (NEX Studio orchestrátor) zostáva eskalačný cieľ** pre platform-level medzery — Koordinátor zaznamenáva žiadosti do Inboxu Deda, Direktor periodicky povie Dedo "prekontroluj inbox projektu X", Dedo vykoná zmeny v CLAUDE.md šablónach.

---

## Čo to nahrádza

**Súčasný (NEX Inbox v0.1.0) workflow:**
- Copy-paste medzi terminálmi (Direktor manuálne prenášal prompty z môjho terminálu do AG Designer / Implementer / Auditor terminálov)
- Manuálna koordinácia kôl (Direktor sám sledoval poradie Designer → Implementer → Auditor)
- Žiadna fáza overenia pred produkčným nasadením
- Žiadny mechanizmus pre úpravy CLAUDE.md (agenti si autonómne ukladali pamäťové pravidlá ktoré nikto nevidel)

**Nový (NEX Studio v0.2.0) workflow:**
- Filesystem riadiaci kanál — Inbox Deda v každom projekte
- Dedikovaný Koordinátor agent koordinuje agentov + komunikuje s Direktorom
- UAT fáza pred produkciou (dev slug pre interné, zákaznícky slug pre acceptance)
- Centralizovaná správa CLAUDE.md — Dedo strážuje šablóny

---

## Kľúčové prínosy

1. **Eliminácia false PASS release verdict** — audítorský smoke test (docker compose build + up + /health) je MANDATORY Activity X v každom audit cykle. Žiadny ďalší "audit PASS ale stack nevie nabehnúť" pattern.

2. **UAT phase pred produkciou** — Direktor (a v budúcnosti QA agent) prejde end-to-end scenáre na live staging zostave pred produkčným rollout. Dvojstupňový workflow: `dev` slug (interné UAT) → `<zákazník>` slug (zákaznícke UAT).

3. **Centralizovaná správa CLAUDE.md** — Dedo je výhradný strážca šablón. Agenti navrhujú zmeny cez Inbox Deda. Žiadny drift medzi tým čo agenti si pamätajú a čo je v ICC štandardoch.

4. **Single channel komunikácie projekt ↔ Dedo** — Direktor namiesto "Dedo, povedz Implementerovi že..." hovorí "Koordinátor, preber to". Plus Inbox Deda eliminuje copy-paste pre architektonické otázky.

5. **Forward-compatible architektúra** — per-tenant UAT slugy umožňujú postupný rollout pre viacerých zákazníkov rovnakého projektu (napr. NEX Inbox pre MÁGERSTAV + budúci zákazníci).

---

## Hlavné technologické rozhodnutia

| Oblasť | Voľba |
|---|---|
| **Koordinátor charter** | Variant C — autoritatívna šablóna v `nex-studio/templates/coordinator-charter.md` + kópia v každom projekte cez Vytvorenie projektu + príkaz na zladenie + povolené prispôsobenie |
| **UAT hosting** | NEX Studio centralizovane (`/opt/uat/<slug>/`) — sandbox docker-compose paralelne s produkčnou `/opt/customers/<slug>/` |
| **Testovacie dáta** | Hybridné — syntetické v gite (`docs/uat/v<version>/test-data/synthetic/`) + reálne mimo gitu (`/opt/uat/<slug>/customer-test-data/`) |
| **UAT cyklus** | Per-tenant + dvojstupňový workflow (`dev` interné UAT → `<zákazník>` zákaznícke UAT) |
| **Inbox Deda** | Filesystem riadiaci kanál v `<projekt>/docs/dedo-inbox/` + `processed/` archív + `decisions-log.md` |
| **Pravidlo prispievania do Inboxu** | Iba Koordinátor + Direktor smie písať priamo. Designer/Implementer/Auditor flag-ujú cez Koordinátora v DONE reportoch |
| **Memory model agentov** | Variant C — Dedo strážuje procesné pravidlá, agenti strážujú doménové pravidlá |
| **Migračný postup pre NEX Inbox** | Variant C — NEX Studio v0.2.0 prvý, NEX Inbox v0.2.0 cez nový ekosystém potom |

---

## Rozsah verzie v0.2.0

NEX Studio v0.2.0 obsahuje 5 nových oblastí + spätné prispôsobenie existujúcich agentov:

**Nové oblasti:**

1. **Koordinátor agent** — opis úlohy, šablóna, sync príkaz, integrácia do Vytvorenia projektu
2. **Inbox Deda mechanika** — adresárová štruktúra, formát žiadostí, pracovný postup, archivácia
3. **UAT prostredie** — `nex-studio uat-deploy <slug>` + `nex-studio uat-teardown <slug>` príkazy, akceptačný zoznam formát, testovacie dáta generovanie
4. **Create Project vylepšenia** — post-scaffold overenie (`git remote -v` + initial push verify), rollback pri partial failure, voliteľná CI/CD wire-up
5. **Audítorský smoke test** — Activity X mandatory v audit charter-i, rámcový smoke test set, CI/CD brána pre release tagy

**Spätné prispôsobenie:**

- Designer/Implementer/Auditor charters — pridanie odkazov na Inbox Deda mechaniku (flag-ovanie cez DONE report sekciu)
- Implementer charter — rozšírený o 5 sekcií z NEX Inbox poučení (HOTOVÉ 2026-05-21, commit `934fd0b`)

---

## Mimo rozsahu pre v0.2.0

Tieto položky boli zvážené, ale odložené:

| Položka | Dôvod odloženia |
|---|---|
| **VERSION layer v NEX Studio** (EPIC-4) | Plánované ale neblokuje v0.2.0 ciele. Designer/Implementer/Auditor charters už majú referencie pripravené pre EPIC-4. Defer to v0.3.0+ |
| **Customer agent pre NEX Studio** | NEX Studio je interný ICC projekt bez external customer. Customer agent dáva zmysel len pre projekty so zákazníckou doménou (NEX Inbox = MÁGERSTAV) |
| **Hotfix UAT** (`<slug>-hotfix`) | Voliteľná feature pre núdzové scenáre. Riešime keď nastane reálna potreba |
| **Migrácia obsahu z hlavného CLAUDE.md** | Hlavný NEX Studio CLAUDE.md má 476 LOC strategický obsah. Code-specific obsah už je v Implementer charter-i (existoval od 12-Mája) |

---

## Časový plán a vývoj

| Brána | Aktivita | Stav |
|---|---|---|
| **Brána A** ✅ | Customer Requirements + dialógy | Hotové 2026-05-21 |
| **Brána B** ⏳ | development-spec + summary (Sub-round 2) | Práve prebieha |
| **Brána C** | Per-feature spec (Sub-round 3) — Koordinátor, Inbox, UAT, Create Project, Audit smoke test | Po Brane B |
| **Brána D** | API + BE + FE spec (Sub-round 4) — ak treba NEX Studio backend rozšírenie alebo UI zmeny | Po Brane C |
| **Implementácia** | Implementer agent (existujúci, rozšírený) realizuje spec | ~2-3 týždne |
| **UAT (dev slug)** | Direktor prejde scenáre na internom UAT | 2-3 dni |
| **Pilot** | Použitie pre NEX Inbox v0.2.0 fix cyklus (1-2 týždne) | Po dokončení v0.2.0 |

Celkový odhad **NEX Studio v0.2.0 development**: 3-5 týždňov od štartu Designer kola.

---

## Postavenie v NEX ekosystéme

NEX Studio je **development workbench** — platforma cez ktorú sa robí vývoj všetkých ICC projektov (NEX Inbox, NEX Manager, NEX Test, budúce projekty). Po v0.2.0:

- **NEX Studio orchestruje projekty** cez per-projektového Koordinátor agenta
- **Dedo (NEX Studio orchestrátor)** zostáva strategická úroveň + eskalačný cieľ
- **Designer/Implementer/Auditor** sú projekt-level doménoví producenti
- **UAT prostredie** je súčasťou každého projektového životného cyklu

Toto je **pivot momentum** pre NEX Studio — z manuálnej platformy (kde Direktor priamo komunikuje so všetkými agentmi) na poloautomatický ekosystém (kde Koordinátor robí denné riadenie, Direktor schvaľuje strategické rozhodnutia).

---

## Odkazy

| Dokument | Obsah |
|---|---|
| `customer-requirements.md` (úroveň verzie) | Formálne Zákaznícke požiadavky (11 sekcií) |
| `customer-dialogue.md` (úroveň verzie) | Q&A audit stopa diskusie 2026-05-21 |
| `development-spec.md` (toto sub-round) | Designer transformation Customer Requirements do konkrétneho plánu |
| `docs/findings/2026-05-21-release-verification-gaps.md` | 4 NEX Studio improvements zo zistení |
| `docs/session-logs/2026-05-21-002.md` | Plný kontext strategickej diskusie |

---

**Koniec dokumentu — Stručný prehľad NEX Studio v0.2.0.**
