# F-002 Inbox Deda mechanika

**Feature:** F-002
**Verzia:** NEX Studio v0.2.0
**Stav:** Návrh — Brána C (per-feature spec)
**Závislosti:** F-001 (Koordinátor agent — primárny písac do inboxu)

---

## 1. Účel a kontext

Inbox Deda je **filesystem riadiaci kanál** medzi Koordinátorom (per projekt) a Dedom (NEX Studio platforma). Rieši 3 problémy odhalené počas NEX Inbox v0.1.0 sprintu:

| Problém | Riešenie cez Inbox Deda |
|---|---|
| Kopírovanie medzi terminálmi Direktora | Filesystem súbory ktoré Dedo číta priamo cez Read tool |
| Riešenie každej drobnosti samostatne | Dávkové spracovanie — Dedo prejde všetky pending žiadosti pri jednej kontrole inboxu |
| Žiadna stopa Dedových rozhodnutí | Processed archív + decisions-log.md ako trvalá audit stopa |

Plus zaisťuje **single channel komunikácie projekt ↔ Dedo** — žiadne paralelné kanály mimo Koordinátora. Designer/Implementer/Auditor flag-ujú návrhy iba cez Koordinátora.

---

## 2. Adresárová štruktúra v projekte

V každom projekte:

```
<projekt>/docs/dedo-inbox/
├── 2026-05-22-1430-koordinator-charter-typo.md   (pending — nepresunuté)
├── 2026-05-22-1545-uat-naming-convention.md      (pending)
├── processed/
│   ├── 2026-05-21-1730-auditor-charter-update-APPLIED.md
│   ├── 2026-05-21-1900-create-project-flow-REJECTED.md
│   └── 2026-05-22-0830-dockerfile-template-DEFERRED.md
└── decisions-log.md                              (chronologický súhrn)
```

### Životný cyklus žiadosti

```
1. Koordinátor (alebo Direktor) vytvorí žiadosť
       ↓
2. docs/dedo-inbox/YYYY-MM-DD-HHMM-<topic>.md (pending)
       ↓
3. Direktor periodicky alebo on-demand spustí Deda
       ↓
4. Dedo prečíta žiadosť (Read tool)
       ↓
5. Dedo posúdi (projekt-specific vs všeobecná)
       ↓
6. Dedo vykoná zmenu (CLAUDE.md projektu alebo templates/<rola>-charter.md)
       ↓
7. Dedo pridá sekciu "Rozhodnutie Deda" + rename súbor
       ↓
8. processed/YYYY-MM-DD-HHMM-<topic>-{APPLIED|REJECTED|DEFERRED}.md
       ↓
9. Dedo pridá riadok do decisions-log.md
       ↓
10. Dedo ohlási Direktorovi súhrn cez CTL terminál
```

---

## 3. Formát žiadosti

### Konvencia názvu súboru

`YYYY-MM-DD-HHMM-<krátky-názov>.md`

Príklady:
- `2026-05-22-1430-koordinator-charter-typo.md`
- `2026-05-22-1545-uat-naming-convention.md`
- `2026-05-23-0900-implementer-permission-broaden.md`

`<krátky-názov>`: lowercase, kebab-case, max 40 znakov, vystihuje topic.

### Štruktúra obsahu

```markdown
---
topic: Krátky názov problému
agent_affected: designer|implementer|auditor|coordinator|none
priority: urgent|normal
submitted_by: coordinator (alebo direktor)
submitted_at: 2026-05-22T14:30:00Z
---

## Problém

[Detailný opis čo som zistil pri koordinácii projektu. Konkrétne file:line
odkazy ak relevant. Aký dopad ak nevyriešené.]

## Navrhované riešenie

[Konkrétny návrh — napr. "Doplniť do Designer charter §X mandatory sub-agent
self-audit pred commit-om pre rounds ≥ 3 spec súborov". Ak možnosti, uviesť
preferenciu s krátkym dôvodom.]

## Posúdenie Koordinátorom

[Projektovo špecifické / všeobecný charakter — predbežný odhad. Finálne posúdi
Dedo. Ak projekt-specific, kde má byť uložené pravidlo (`<projekt>/.claude/
agents/<rola>/CLAUDE.md`). Ak všeobecné, ktorý template (`templates/<rola>-
charter.md`).]

## Pôvod

[Ak žiadosť vznikla na základe agent DONE reportu, uviesť:
- Ktorý agent flag-oval (Designer/Implementer/Auditor)
- Dátum + session log reference
- Verbatim citácia agent flag-u

Ak vznikla z mojej koordinačnej analýzy, uviesť:
- Aký pattern som identifikoval
- Recurring vs one-off
- Konkrétne udalosti ktoré ku zisteniu viedli]
```

### Povinné polia YAML frontmatter

| Pole | Hodnoty | Vynútenie |
|---|---|---|
| `topic` | krátky string | Manuálne (Koordinátor) |
| `agent_affected` | enum {designer, implementer, auditor, coordinator, none} | Manuálne |
| `priority` | enum {urgent, normal} | Manuálne |
| `submitted_by` | string (coordinator alebo direktor) | Manuálne |
| `submitted_at` | ISO 8601 UTC | Manuálne (Koordinátor zaznamenáva pri vytvorení) |

Validácia frontmatter — viď §11 (otvorené otázky pre Sub-round 4).

### Príklad žiadosti (real-world)

```markdown
---
topic: Designer charter nemá sub-agent self-audit pravidlo
agent_affected: designer
priority: normal
submitted_by: coordinator
submitted_at: 2026-05-22T14:30:00Z
---

## Problém

Pri CR-019 Designer round (commit `026eff2`) Designer self-PIV zachytil 1
drobnosť pred commit-om (FE ARCH §15.1:1268 stale "aktuálne 45"). Toto bolo
náhoda — Designer charter aktuálne nemá explicit pravidlo o sub-agent
pre-commit self-audit. Pre rovnaký pattern v budúcich projektoch potrebujeme
mandatórne pravidlo.

## Navrhované riešenie

Doplniť do `templates/designer-charter.md` novú sekciu §X "Pre-commit
sub-agent self-audit" pre Designer rounds ≥ 3 spec súborov / ≥ 100 LOC zmeny:
- Spustiť general-purpose Agent so scope 4 audit dimenzie (cascade
  kompletnosť, stale references, API surface alignment, numerical consistency)
- Vrátený report → fix flag-y → re-run self-audit → commit až keď clean

## Posúdenie Koordinátorom

Všeobecný charakter — pattern platí pre všetky projekty. Riešenie v
`templates/designer-charter.md` + odporúčaný sync command pre existujúce
projekty.

## Pôvod

Pattern identifikovaný cez NEX Inbox v0.1.0 sprint (Re-Gate G Activity 1 —
P0-A spec self-contradiction Príloha A 45 vs body 50). Implementer charter
už má §13.6 P-2 acceptance a §13.7 False PASS — Designer ekvivalent chýba.

Recurring: 2 inštancie cez CR-018 + CR-019 (P0-A + EXPECTED_MIN_COUNT drift).
```

---

## 4. Pravidlá prispievania

### Kto smie písať priamo do `docs/dedo-inbox/`

| Subjekt | Write povolený? | Mechanizmus |
|---|---|---|
| **Koordinátor** | ✅ Áno | `settings.json` allow `Write(<PROJECT_ROOT>/docs/dedo-inbox/*.md)` |
| **Direktor** | ✅ Áno | OS-level filesystem permissions (Direktor je vlastník) |
| **Dedo** | ✅ Áno (read + presúvanie do processed) | Implicitné — Dedo je NEX Studio platform user |
| **Designer** | ❌ Nie | `settings.json` deny |
| **Implementer** | ❌ Nie | `settings.json` deny |
| **Auditor** | ❌ Nie | `settings.json` deny |
| **Customer agent** (ak existuje) | ❌ Nie | `settings.json` deny |

### Postup pre nižších agentov (Designer/Implementer/Auditor)

V DONE reporte Direktorovi sekcia **"Pre Koordinátora — návrh do Inboxu Deda"**:

```markdown
## Pre Koordinátora — návrh do Inboxu Deda

**Problém:** [krátky opis čo som zistil]
**Návrh úpravy:** [konkrétna zmena]
**Charter ktorého agenta:** designer / implementer / auditor / coordinator
**Posúdenie:** projektovo špecifické / všeobecný charakter
**Pôvod (kde sa to objavilo):** [kontext]
```

Direktor uvidí flag v agent DONE report → pri ďalšej koordinačnej úlohe Koordinátor posúdi:

- **Akceptuje** → napíše žiadosť do `docs/dedo-inbox/` so svojím vlastným posúdením
- **Odmietne** → poznámka v ďalšej priebežnej správe Direktorovi prečo (transparentnosť — Direktor môže prehodnotiť)
- **Agreguje** → spojí podobné návrhy od viacerých agentov do jednej žiadosti

### Mechanizmus vynútenia

Per memory `L-016: Agent permission globs must be absolute paths` — `settings.json` v každom agent priečinku obsahuje deny zoznam s absolútnymi cestami:

```json
{
  "permissions": {
    "deny": [
      "Write(<PROJECT_ROOT>/docs/dedo-inbox/**)",
      "Edit(<PROJECT_ROOT>/docs/dedo-inbox/**)"
    ]
  }
}
```

Pri pokuse o Write/Edit Claude Code permission systém odmietne akciu pred jej vykonaním — žiadny silent bypass.

---

## 5. Pracovný postup Koordinátora

### Kedy vytvoriť žiadosť

Koordinátor analyzuje pri každom agent DONE reporte 6 indikátorov NEX Studio gapu (per F-001 Koordinátor charter §7):

| Indikátor | Príklad |
|---|---|
| Recurring pattern | "P-2 acceptance" v 2+ projektoch |
| Agent claim bez authoritative source | Agent reportuje "per X" ale X nikde dokumentované |
| Spec drift bez clear root cause | Agenti sa rozchádzajú v interpretácii |
| Build/deploy failure ktorý audit prehliadol | Stack nevie nabehnúť napriek PASS verdict |
| Tool gap | Agent potrebuje nástroj ktorý NEX Studio neposkytuje |
| Charter mismatch | Agent správanie nezodpovedá charter-u |

Pri detekcii → vytvoriť žiadosť (cez Write tool — povolený scope per `settings.json`).

### Ako agregovať podobné žiadosti

Pred vytvorením novej žiadosti Koordinátor skontroluje existujúce pending žiadosti v `docs/dedo-inbox/`:

```bash
ls docs/dedo-inbox/*.md
```

Ak nájde podobnú pending žiadosť:
- **Rovnaký topic** → pridať detail k existujúcej (Edit tool)
- **Príbuzný topic** → pridať do existujúcej žiadosti sekciu "Súvisiace prípady" alebo agregovať pod jeden topic
- **Odlišný topic** → vytvoriť novú

Cieľ: Dedo dostane konsolidované žiadosti, nie 10 individuálnych s rovnakým root cause.

### Urgent vs normal — signál Direktorovi

V Koordinátorovej priebežnej správe Direktorovi (per F-001 charter §9):

```
**Inbox Deda:** 3 nové (1 urgentná: <topic>, 2 bežné)
```

Pri **urgent** signal Direktor rozhodne kedy spustí Deda (možno hneď, možno počká s ostatnými). Pri **normal** Koordinátor pokračuje s prácou, žiadosť čaká.

Kritériá urgent (Koordinátorovo posúdenie):
- Blokuje aktuálny audit cyklus / Designer round / Implementer round
- Recurring pattern ktorý ohrozuje kvalitu (napr. 3. inštancia "P-2 acceptance" za týždeň)
- Bezpečnostná chyba v charter / template
- Spec self-contradiction ktorý vedie k mismatched agent behavior

### Po Dedovom rozhodnutí

Direktor cez CTL terminál povie Koordinátorovi výsledok. Koordinátor:

- **APPLIED** → notifikuje agentov ak treba ("Implementer charter bol updated, prosím re-load pred ďalším taskom")
- **REJECTED** → ak žiadosť pochádzala z flag-u nižšieho agenta, notifikuje toho agenta s dôvodom (cez Direktora pri ďalšom prompt-e)
- **DEFERRED** → zaznamenať do `.nex-coordinator-state.md` pre future revisit (kedy revisit)

---

## 6. Pracovný postup Deda

### Krok 1: Direktor signál

Direktor mi povie cez CTL terminál:

> "Dedo, prekontroluj inbox projektu <slug>."

Alebo pri urgent:

> "Dedo, urgentná žiadosť v inboxe <slug>, pozrieš sa?"

### Krok 2: Read inbox

```bash
ls /opt/projects/<slug>/docs/dedo-inbox/*.md
```

Read tool pre každú pending žiadosť. Spočítam: N total, M urgent.

### Krok 3: Posúdiť každú žiadosť

Pre každú:

1. **Validity check** — má žiadosť všetky povinné YAML polia? Je opis problému jasný?
2. **Projekt-specific vs všeobecný** — Koordinátor predbežne posúdil, ja overím
3. **Rozhodnutie:**
   - **APPLIED** — vykonám zmenu (§Krok 4)
   - **REJECTED** — žiadosť nemá hodnotu alebo nesprávne posúdená; dôvod do "Rozhodnutie Deda"
   - **DEFERRED** — má hodnotu ale teraz nie je optimálne riešiť (čaká na ďalší kontext); dôvod + kedy revisit

### Krok 4: Vykonať zmenu

**Projekt-specific:**

```
Edit <projekt>/.claude/agents/<rola>/CLAUDE.md
```

Zmena lokálne. Iné projekty zostávajú nezmenené.

**Všeobecný charakter:**

```
Edit /opt/projects/nex-studio/templates/<rola>-charter.md
```

Plus odporúčanie pre ostatné projekty: spustiť `nex-studio sync-coordinator-charter <projekt>` (alebo ekvivalent pre Designer/Implementer/Auditor).

### Krok 5: Pridať "Rozhodnutie Deda" sekciu

Edit pôvodnú žiadosť — pridať na koniec:

```markdown

---

## Rozhodnutie Deda

**Verdikt:** APPLIED | REJECTED | DEFERRED
**Dátum:** 2026-05-22T18:00:00Z

### Posúdenie

[Moje hodnotenie problému + návrhu + Koordinátorovej analýzy]

### Vykonanie (ak APPLIED)

- **Cieľový súbor:** [napr. `templates/designer-charter.md` alebo
  `<projekt>/.claude/agents/<rola>/CLAUDE.md`]
- **Zmena:** [krátky popis čo som upravil + commit hash ak relevantný]

### Dôvod (ak REJECTED alebo DEFERRED)

[Konkrétny dôvod prečo nie APPLIED]

### Notifikácia agentom (ak APPLIED)

- [napr. "Implementer charter updated — pri ďalšom session init musí
  re-load. Koordinátor: prosím signalizuj."]
```

### Krok 6: Presunúť do processed

```bash
mv docs/dedo-inbox/2026-05-22-1430-koordinator-charter-typo.md \
   docs/dedo-inbox/processed/2026-05-22-1430-koordinator-charter-typo-APPLIED.md
```

### Krok 7: Aktualizovať decisions-log

Edit `docs/dedo-inbox/decisions-log.md` — pridať riadok hore (newest first).

### Krok 8: Ohlásiť Direktorovi súhrn

```
Prešiel som inbox projektu <slug>:
- N pending → vyriešených: M APPLIED, K REJECTED, L DEFERRED
- Detaily v docs/dedo-inbox/processed/
- Notifikácie pre Koordinátora: [zoznam ak nejaké]
```

---

## 7. Formát processed súborov

Pôvodný obsah žiadosti **zachovaný v plnom rozsahu**. Pridaná iba sekcia "Rozhodnutie Deda" na koniec (per §6 Krok 5).

### Príklad processed súboru

```markdown
---
topic: Designer charter nemá sub-agent self-audit pravidlo
agent_affected: designer
priority: normal
submitted_by: coordinator
submitted_at: 2026-05-22T14:30:00Z
---

[... pôvodný obsah žiadosti zachovaný ...]

---

## Rozhodnutie Deda

**Verdikt:** APPLIED
**Dátum:** 2026-05-22T18:00:00Z

### Posúdenie

Pattern je všeobecný — Designer round bez sub-agent self-audit zaviedol P0-A
spec self-contradiction v CR-018 (NEX Inbox sprint). Riešenie je správne —
mandatórny self-audit pre rounds ≥ 3 spec súborov / ≥ 100 LOC.

### Vykonanie

- **Cieľový súbor:** `templates/designer-charter.md`
- **Zmena:** Pridaná sekcia §17 "Pre-commit sub-agent self-audit" so 4 audit
  dimenziami (cascade kompletnosť, stale references, API surface alignment,
  numerical consistency). Commit `<hash>` v nex-studio.

### Notifikácia agentom

- Koordinátor: pre projekty s existujúcim Designer charter spustiť
  `nex-studio sync-designer-charter <projekt>` pri ďalšej príležitosti
- Designer agenti pri ďalšom session init re-load
```

---

## 8. `decisions-log.md` formát

Chronologický zoznam, **newest first**. Per záznam jeden riadok (alebo 2 ak treba kontext).

### Štruktúra

```markdown
# Decisions Log — Inbox Deda

> Chronologický súhrn rozhodnutí Deda. Newest first.
> Detail v `processed/<súbor>.md`.

---

## 2026-05-22

- **18:00 APPLIED** [designer-self-audit-pravidlo] — Designer charter §17
  pre-commit sub-agent self-audit pridaný do templates/. Sync command pre
  existujúce projekty. Processed: `2026-05-22-1430-koordinator-charter-typo-APPLIED.md`

- **17:30 REJECTED** [uat-naming-convention] — Žiadosť premenovať `/opt/uat/<slug>/`
  → `/opt/uat-staging/<slug>/`. Existujúca konvencia konzistentná s
  `/opt/customers/<slug>/` paralelizmus. Processed: `2026-05-22-1545-uat-naming-convention-REJECTED.md`

## 2026-05-21

- **19:00 APPLIED** [implementer-docker-patterns] — §9.1 Docker/build patterns
  pridaný do Implementer charter (templates/ + nex-studio kópia). Commit 934fd0b.
  Processed: `2026-05-21-1830-implementer-docker-patterns-APPLIED.md`

[...]
```

### Konvencia

- Jeden riadok per záznam (max 2)
- Format: `**HH:MM VERDICT** [topic-slug] — krátky súhrn. Processed: <súbor>.md`
- Skupina podľa dátumu (sekcia `## YYYY-MM-DD`)
- Newest first (najnovšie hore)

---

## 9. Bezpečnosť

| Aspekt | Riešenie |
|---|---|
| **Citlivý obsah v žiadostiach** | Žiadne credentials, žiadne secrets. Žiadosti opisujú procesné problémy / charter úpravy / mechanizmus tweaks. Bezpečné v gite. |
| **PII (osobné údaje)** | Per Customer Requirements §16.2 — žiadne PII v inboxe (žiadne real customer mená, IČO, adresy). Ak Koordinátor flag-uje problém ktorý súvisí s konkrétnym customer record, anonymizuje (napr. "supplier #1234" namiesto reálneho mena). |
| **Permissions** | `settings.json` v každom agent priečinku vynucuje že len Koordinátor + Direktor smie písať. Designer/Implementer/Auditor majú deny. |
| **Audit stopa** | Plný history v gite (žiadosť + rozhodnutie + commit hash zmeny v charter-i). |

---

## 10. Acceptance criteria

| # | Kritérium | Verifikácia |
|---|---|---|
| 1 | Koordinátor vie pridať žiadosť cez Write tool | `Write(<PROJECT_ROOT>/docs/dedo-inbox/2026-XX-XX-XXXX-test.md)` prejde |
| 2 | Designer/Implementer/Auditor nemajú write právo | Pokus o `Write(<PROJECT_ROOT>/docs/dedo-inbox/...)` zamietnutý permission systémom |
| 3 | Dedo vie prečítať všetky pending žiadosti | `ls docs/dedo-inbox/*.md` + Read tool pre každú |
| 4 | Dedo vie presunúť do `processed/` | `mv` alebo Write nového súboru + rm pôvodného |
| 5 | `decisions-log.md` zachytáva chronologicky všetky rozhodnutia | Manual review po Dedovom inbox check-u |
| 6 | Žiadosť má povinné YAML frontmatter polia | Validation cez script alebo manual review (viď §11) |
| 7 | Pôvodný obsah žiadosti zachovaný v processed | Diff pred-presunutie vs po-presunutie = len pridaná "Rozhodnutie Deda" sekcia |

---

## 11. Otvorené otázky pre Sub-round 4

| # | Otázka | Možnosti |
|---|---|---|
| **O-1** | Validácia YAML frontmatter — automatic alebo manual? | A) `scripts/validate-inbox-request.sh` (lint script) ktorý Koordinátor spustí pred commit-om; B) Manual review Koordinátorom + Dedom; C) Žiadne (best effort) |
| **O-2** | Notifikácia pri novej urgent žiadosti — automatic alebo manual? | A) Slack/email notifikácia pri pridaní s `priority: urgent`; B) Manual Direktorov check; C) Koordinátorov signal v priebežnej správe (default per F-001 charter §9) |
| **O-3** | Retencia processed súborov — forever alebo cleanup? | A) Forever (full audit stopa); B) Po N rokoch archív; C) Manual cleanup Direktorom |
| **O-4** | Decisions log generovanie — manual Dedo alebo automatický skript? | A) Manual Dedo (každý záznam zámerne); B) Automatic generátor zo processed/ priečinka (po každom inbox check-u) |

Tieto otázky **neblokujú** F-002 implementáciu. Sub-round 4 ich rieši alebo defer-uje do v0.3.0+.

---

## 12. Krížové odkazy

| Dokument | Súvislosť |
|---|---|
| `F-001-coordinator-charter.md` §8 | Inbox Deda mechanika z Koordinátorovho pohľadu |
| `F-001-coordinator-settings.json` | Permissions pre Inbox Deda write |
| `customer-requirements.md` §4 | Vysoko-úrovňový popis Inbox Deda |
| `customer-dialogue.md` §2.3.5-§2.3.6 | Direktorove rozhodnutia o Inbox Deda mechanike + pravidlo prispievania |
| `development-spec.md` §3.2 F-002 | High-level dizajn (5 komponentov) |

---

**Koniec dokumentu — F-002 Inbox Deda mechanika.**
