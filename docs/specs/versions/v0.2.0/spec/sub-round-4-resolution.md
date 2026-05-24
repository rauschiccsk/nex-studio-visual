# Sub-round 4 — Resolution otvorených otázok

**Verzia:** NEX Studio v0.2.0
**Dátum:** 2026-05-21
**Stav:** Návrh — Brána D (finálne rozhodnutia pred Implementer round-om)
**Autor:** Dedo (Designer rola)

---

## 1. Účel a kontext

Sub-round 4 finalizuje **20 otvorených otázok** identifikovaných cez Sub-round 2 + Sub-round 3 (per-feature specs F-001..F-006). Pre každú otázku Dedo (Designer) navrhuje **resolution per quality-first principle** — jedno odporúčané riešenie s rationale.

Direktor schválil v session 2026-05-21 batch approval: "Schvaľujem všetky úlohy až do konca." Tieto resolution návrhy môžu byť akceptované as-is, alebo Direktor explicit override-uje konkrétnu otázku.

Po Sub-round 4 finalizácii NEX Studio v0.2.0 spec balík je **kompletný a pripravený pre Implementer round**.

---

## 2. Resolution metodika

Per quality-first principle (`feedback_quality_first` memory):
- **Default:** jedno najlepšie riešenie podľa 4 kritérií (najkvalitnejšie, profesionálne, praktické, dlhodobé)
- **Alternatíva ponúknutá LEN ak rovnocenná** podľa rovnakých kritérií
- **Minimal / MVP / stub varianty** ZAKÁZANÉ ako default odporúčanie

Per pre-flight check (z F-001 Koordinátor charter §10 — moja vlastná disciplína):
- **Pre každý variant overím** že má aspoň 1 reálny use case kde je najlepším riešením
- **Eliminate** varianty bez zmyslu pred ponukou

---

## 3. Sub-round 4 decisions

### 3.1 Development-spec otvorené otázky (4)

#### O-DS-1 — UAT acceptance history persistence

**Source:** `development-spec.md` §10 O-1
**Otázka:** Filesystem `<projekt>/docs/uat/v<version>/results/` alebo NEX Studio DB tabuľka?

**Možnosti:**
- A) Filesystem (default)
- B) DB tabuľka
- C) Hybrid (filesystem primary + DB index)

**Resolution: A) Filesystem.**

**Rationale:**
- Konzistentné s celým UAT pattern-om (per-projekt v gite)
- Audit-trail-friendly v gite (commit history zachytáva kedy Direktor schválil)
- Žiadna potreba DB schema migrácie ani UI integration pre v0.2.0
- Defer DB persistence pre v0.3.0+ ak vznikne reálna potreba (napr. cross-projekt UAT analytics)

#### O-DS-2 — Sync command implementácia

**Source:** `development-spec.md` §10 O-3 + `F-006` §8 O-1 (duplicate)
**Otázka:** Bash skript alebo Python CLI s rich UI?

**Možnosti:**
- A) Bash (jednoduchšie)
- B) Python + rich library
- C) NEX Studio backend endpoint volá oboje

**Resolution: B) Python + rich library.**

**Rationale:**
- Diff preview vyžaduje structured comparison (rich Table komponent)
- Interactive confirm flow (rich Prompt) ergonomic
- Python má lepšie file/markdown parsing knižnice (mistune, python-frontmatter)
- Single Python skript v `scripts/sync-charter.py` — jednoduché udržiavanie
- Bash by sa dostal do limitov pri komplexnejšom diff handling

#### O-DS-3 — Designer self-audit sub-agent mechanika

**Source:** `development-spec.md` §10 O-4
**Otázka:** Ako sub-agent dostane scope (Designer's commit diff?), aký output formát?

**Možnosti:**
- A) Sub-agent dostane plný git diff + zoznam 4 audit dimensions
- B) Sub-agent dostane scope per súbor (jeden súbor naraz)
- C) Sub-agent autonomously discoveruje (Designer iba "spusti audit")

**Resolution: A) Plný git diff + 4 dimensions.**

**Rationale:**
- Cascade kompletnosť vyžaduje cross-file analýzu (nemôže byť per-súbor)
- Stale reference scan vyžaduje grep cez celý spec balík (nie len diff)
- API surface alignment vyžaduje triangle (BE Pydantic ↔ openapi ↔ FE form)
- Output formát: štruktúrovaný markdown report so 4 sekciami (jedna per dimension) + odporúčania
- Designer review-uje report + fix-uje flagy + re-run

#### O-DS-4 — UAT acceptance history persistence — duplicate so O-DS-1

Vyriešené v O-DS-1.

### 3.2 F-001 (Koordinátor charter) — žiadne explicit otvorené otázky

F-001 má charter content kompletný. Pri implementácii Implementer skopíruje do `templates/coordinator-charter.md`.

### 3.3 F-002 Inbox Deda otvorené otázky (4)

#### O-002-1 — Validácia YAML frontmatter

**Source:** `F-002` §11 O-1

**Možnosti:**
- A) `scripts/validate-inbox-request.sh` (lint script ktorý Koordinátor spustí pred commit-om)
- B) Manual review Koordinátorom + Dedom
- C) Žiadne (best effort)

**Resolution: A) Lint script.**

**Rationale:**
- Štruktúrované YAML polia majú konkrétne enum hodnoty (priority, agent_affected) — validate-able
- Pre-commit lint zachytí drobnosti pred Dedovým posúdením (nesprávny formát dátumu, missing required field)
- Implementácia: Python skript v `scripts/validate-inbox-request.py` ktorý parsuje YAML + kontroluje schemu
- Koordinátor spustí pred Write-om (alebo automaticky cez pre-commit hook v `<projekt>/.githooks/pre-commit`)

#### O-002-2 — Notifikácia pri urgent žiadosti

**Source:** `F-002` §11 O-2

**Možnosti:**
- A) Slack/email notifikácia
- B) Manual Direktorov check
- C) Koordinátorov signal v priebežnej správe (default per F-001 §9)

**Resolution: C) Koordinátorov signal v priebežnej správe.**

**Rationale:**
- Existing F-001 §9 DONE report format už obsahuje "Inbox Deda: N pending (M urgent)" riadok
- Žiadna ďalšia infrastruktúra potrebná (žiadny Slack integration, žiadny SMTP setup)
- Direktor číta Koordinátorove správy denne — žiadny additional notification channel
- Pre future ak vznikne potreba: extend ako voliteľné (Slack webhook ako opt-in pri Create Project K-005)

#### O-002-3 — Retencia processed súborov

**Source:** `F-002` §11 O-3

**Možnosti:**
- A) Forever (full audit stopa)
- B) Po N rokoch archív
- C) Manual cleanup Direktorom

**Resolution: A) Forever.**

**Rationale:**
- Processed súbory sú malé (~1-5 KB each)
- Plná audit stopa cenná pre cross-project pattern detection
- Git commit history zachytáva kedy/prečo
- Cleanup overhead > storage cost pre dohľadnú budúcnosť (10+ rokov pre ~100 projektov × ~100 žiadostí ≈ 100 MB total)

#### O-002-4 — Decisions log generovanie

**Source:** `F-002` §11 O-4

**Možnosti:**
- A) Manual Dedo (každý záznam zámerne)
- B) Automatic generátor zo processed/ priečinka

**Resolution: A) Manual Dedo.**

**Rationale:**
- Dedo robí cross-decision sentence pri pridávaní záznamu (kontext z viac žiadostí)
- Automatic generátor by stratil tento kontextový shorthand
- Newest-first chronologický feed je triviálne udržať manuálne
- Decision log ako "executive summary" Dedo prác potrebuje zámerný authorship

### 3.4 F-003 UAT prostredie otvorené otázky (4)

#### O-003-1 — UAT acceptance history persistence

**Duplicate s O-DS-1.** Resolution: A) Filesystem.

#### O-003-2 — Auto-cleanup snapshots

**Source:** `F-003` §15 O-2

**Možnosti:**
- A) Forever
- B) 5 rokov retention
- C) Manual cleanup cez Inbox Deda

**Resolution: A) Forever.**

**Rationale:**
- DB snapshots sú malé (~5-15 MB gzipped)
- Per UAT prostredie typicky 5-15 snapshots celkom (jeden per verzia)
- Cross-version regression testing vyžaduje historické snapshots dostupné
- Manual cleanup cez Inbox Deda zostáva ako mechanizmus pre extreme cases (disk space pressure)
- Konzistentné s F-002 retencia decision

#### O-003-3 — NEX Studio backend endpoint pre UAT status

**Source:** `F-003` §15 O-3

**Možnosti:**
- A) CLI only
- B) HTTP API endpoint GET `/api/v1/uat/<slug>/status`
- C) Hybrid

**Resolution: A) CLI only pre v0.2.0.**

**Rationale:**
- Konzistentné s development-spec §5 rozhodnutím (CLI nástroje pre v0.2.0)
- `nex-studio uat-status <slug>` je dostatočný pre Koordinátora a Direktora
- HTTP API odložené na v0.3.0+ ak vznikne UI integration potreba
- Žiadny FE display zatiaľ potrebný

#### O-003-4 — Hotfix UAT slug

**Source:** `F-003` §15 O-4

**Možnosti:**
- A) Defer to v0.3.0+ (default per customer-requirements §10)
- B) Implementovať v v0.2.0 ako voliteľná feature

**Resolution: A) Defer to v0.3.0+.**

**Rationale:**
- Per customer-requirements §10 explicit "Mimo rozsahu pre v0.2.0"
- Žiadne urgent biz potreba zatiaľ (NEX Inbox pilot u MÁGERSTAV ešte beží)
- Implementácia voliteľnej feature pridáva komplexnosť bez clear benefit
- Riešime keď nastane reálny hotfix scenár

### 3.5 F-004 Create Project otvorené otázky (4)

#### O-004-1 — create-project skript implementácia

**Source:** `F-004` §8 O-1

**Možnosti:**
- A) Bash
- B) Python + rich library
- C) NEX Studio backend endpoint volá oboje

**Resolution: B) Python + rich library.**

**Rationale:**
- Konzistentné s O-DS-2 (sync command tiež Python + rich)
- Rich progress bars pre dlhé build operácie (K-004 smoke test)
- Štruktúrované error reporting (per krok, s log file references)
- Path substitution (sed for `<PROJECT_ROOT>`) ergonomic v Python (Path.write_text)
- Single technology stack pre všetky NEX Studio CLI nástroje

#### O-004-2 — main-claude-template.md generic alebo per-project-type

**Source:** `F-004` §8 O-2

**Možnosti:**
- A) Generic
- B) Per-type (backend-only, full-stack, library)

**Resolution: A) Generic pre v0.2.0.**

**Rationale:**
- NEX Studio v0.2.0 má 2 typy projektov: full-stack (NEX Inbox, NEX Manager) — žiadna potreba diferenciácie
- Generic template + per-projekt customization cez Inbox Deda (Variant C pattern)
- Per-type rozšírenie defer until 3+ rôzne project types existujú

#### O-004-3 — Branch protection rules

**Source:** `F-004` §8 O-3

**Možnosti:**
- A) Automatic (require PR, no force push)
- B) Opt-in

**Resolution: B) Opt-in.**

**Rationale:**
- Interné ICC projekty nemajú multi-developer collaboration (Implementer agent commit-uje priamo)
- Branch protection by zablokoval Implementer push → broken workflow
- Opt-in pre projekty s external contributors alebo regulated požiadavkami
- Default off konzistentne s ICC pattern (`per memory feedback_audits_from_ctl`)

#### O-004-4 — nex-studio delete-project príkaz

**Source:** `F-004` §8 O-4

**Možnosti:**
- A) Out-of-scope F-004
- B) Implementovať v F-004

**Resolution: A) Out-of-scope F-004.**

**Rationale:**
- F-004 fokus je Create, nie Delete
- Delete je niche operation (väčšina projektov ide do produkcie, nie sa maže)
- F-007+ alebo manual cleanup zostáva pre special cases
- Konzistentné s scope discipline

### 3.6 F-005 Audit smoke test otvorené otázky (4)

#### O-005-1 — Branch protection rule pre release tagy

**Source:** `F-005` §8 O-1

**Možnosti:**
- A) Automatic pre všetky projekty
- B) Opt-in cez --branch-protection flag
- C) Per-project type (regulated-ledger automatic)

**Resolution: C) Per-project type.**

**Rationale:**
- Regulated-ledger projekty (NEX Inbox MÁGERSTAV) majú vysokú compliance latku — automatic branch protection rozumný default
- Interné NEX Studio platform projekty (sám seba) — opt-in (žiadny external accountability requirement)
- Flag pri Create Project: `--project-type {internal|regulated-ledger}` → určuje default branch protection
- Future: Customer agent input ovplyvní (regulated customer = stricter rules)

#### O-005-2 — Activity X timeout

**Source:** `F-005` §8 O-2

**Možnosti:**
- A) Keep defaults (120s healthy, 30 min workflow)
- B) Per-project konfigurovateľné
- C) Adaptive

**Resolution: B) Per-project konfigurovateľné.**

**Rationale:**
- NEX Inbox (s Ollama + Tesseract) pravdepodobne potrebuje viac než 120s pre healthy (large image pulls)
- Internal NEX Studio (lightweight FastAPI + Vite) 60s je dosť
- Konfigurácia v `<projekt>/.claude/agents/auditor/CLAUDE.md` §X (extending generic template)
- Default 120s/30min ako baseline + per-projekt override

#### O-005-3 — Local Auditor smoke test implementation

**Source:** `F-005` §8 O-3

**Možnosti:**
- A) Bash skript
- B) Sub-agent
- C) Hybrid

**Resolution: C) Hybrid.**

**Rationale:**
- Bash skript pre X.1-X.5 sub-aktivity (fail-fast, well-defined commands)
- Sub-agent pre interpretation + audit report writing (rich error analysis, structured output)
- Auditor charter §X explicit volá `scripts/audit-smoke-test.sh` ktorý vykonáva X.1-X.5
- Auditor agent prečíta výstup + zapíše do audit report-u

#### O-005-4 — Activity X failure → automatic Inbox Deda flag

**Source:** `F-005` §8 O-4

**Možnosti:**
- A) Áno (recurring failures → systematic gap)
- B) Nie (per-failure Direktor decision)
- C) Threshold-based (N+ failures za týždeň)

**Resolution: A) Áno (recurring failures only).**

**Rationale:**
- Single Activity X failure = projekt-specific bug (Implementer fix)
- 2+ Activity X failures pre rovnaký bug pattern naprieč projektmi = NEX Studio gap
- Auditor charter §X.6 doplniť pravidlo: pri detekcii rovnakého failure pattern v 2+ projektoch → automatic Inbox Deda flag (Koordinátor agreguje)
- Pre v0.2.0 implementácia jednoduchá: Auditor checkuje `decisions-log.md` pre similar past failures pred flag-om

### 3.7 F-006 Charter updates otvorené otázky (4)

#### O-006-1 — Sync command implementácia — duplicate so O-DS-2

Vyriešené: B) Python + rich.

#### O-006-2 — Per-projekt prispôsobenie tracking

**Source:** `F-006` §8 O-2

**Možnosti:**
- A) Git history
- B) Dedikovaný metadata súbor (.charter-customizations.md)
- C) Hybrid

**Resolution: A) Git history.**

**Rationale:**
- Git commit message + diff zachytáva who/when/what zmeny
- Žiadna ďalšia metadata súbor potrebná (single source of truth = git)
- Sync command (per O-DS-2) môže analyzovať git history pre divergence detection
- Konzistentné s ICC pattern (vždy git history ako audit stopa)

#### O-006-3 — Auto-sync pri update autoritatívnej šablóny

**Source:** `F-006` §8 O-3

**Možnosti:**
- A) Manual only
- B) Opt-in
- C) Notification

**Resolution: C) Notification.**

**Rationale:**
- Manual only → Direktor zabudne aktualizovať projekty
- Auto-sync → riziko prepísania per-projekt prispôsobenia
- Notification cez Koordinátorov priebežný report: "NEX Studio updated <agent>-charter.md template — odporúčam spustiť sync pri ďalšej príležitosti"
- Direktor explicit triggernuté + per-projekt diff review

#### O-006-4 — Customer agent template pre v0.3.0+

**Source:** `F-006` §8 O-4

**Možnosti:**
- A) Defer until concrete need (default)
- B) Začať návrh teraz

**Resolution: A) Defer.**

**Rationale:**
- Per customer-requirements §10 explicit mimo rozsahu v0.2.0
- Customer agent je doménový — vzniká s konkrétnou customer doménou
- Začať návrh teraz bez konkrétneho use case = over-engineering
- Riešime keď v v0.3.0+ vznikne 2+ projektov s rôznymi customer doménami

---

## 4. Súhrnná tabuľka resolution

| Otázka | Resolution | Kde implementovať |
|---|---|---|
| O-DS-1 UAT history persistence | A) Filesystem | F-003 |
| O-DS-2 Sync command implementácia | B) Python + rich | F-001 sync skript + F-006 |
| O-DS-3 Designer self-audit mechanika | A) Plný diff + 4 dimensions | F-006 §2 §X.3 |
| O-002-1 YAML validation | A) Lint script (Python) | F-002 nový skript |
| O-002-2 Urgent notifikácia | C) Koordinátorov signal | F-001 §9 (existing) |
| O-002-3 Processed retencia | A) Forever | F-002 §9 |
| O-002-4 Decisions log generovanie | A) Manual Dedo | F-002 §6 Krok 7 (existing) |
| O-003-1 UAT history | (duplicate O-DS-1) | — |
| O-003-2 Snapshot retention | A) Forever | F-003 §8 (existing) |
| O-003-3 UAT status endpoint | A) CLI only | F-003 §4.3 (existing) |
| O-003-4 Hotfix UAT slug | A) Defer to v0.3.0+ | — |
| O-004-1 create-project skript | B) Python + rich | F-004 implementation |
| O-004-2 main-claude-template | A) Generic | F-004 §6 (existing) |
| O-004-3 Branch protection | B) Opt-in | F-004 K-005 (existing) |
| O-004-4 delete-project príkaz | A) Out-of-scope | — |
| O-005-1 Release tag branch protection | C) Per-project type | F-005 K-004 + F-004 K-005 |
| O-005-2 Activity X timeout | B) Per-project konfigurovateľné | F-005 §3 §X.3 |
| O-005-3 Smoke test impl | C) Hybrid bash + agent | F-005 §4 (existing) |
| O-005-4 Failure → Inbox flag | A) Áno (recurring only) | F-005 §3 §X.6 doplnenie |
| O-006-1 Sync command impl | (duplicate O-DS-2) | — |
| O-006-2 Customization tracking | A) Git history | F-006 §6 (existing) |
| O-006-3 Auto-sync | C) Notification | F-006 §6 + F-001 §9 |
| O-006-4 Customer agent template | A) Defer | — |

---

## 5. Otvorené pre v0.3.0+ (z resolution decisions)

| Položka | Dôvod odkladu |
|---|---|
| **DB-based UAT acceptance history** | Filesystem dostatočný pre v0.2.0; DB ak vznikne cross-projekt analytics potreba |
| **HTTP API pre UAT status** | CLI dostatočný; HTTP ak vznikne UI integration potreba |
| **Hotfix UAT slug** | Žiadny biz pressure; voliteľné keď nastane |
| **delete-project príkaz** | Niche operation; manual cleanup zostáva |
| **Customer agent template** | Bez konkrétneho use case = over-engineering |
| **Per-type project templates** | Súčasné 2 typy nestačia pre justified diferenciáciu |

---

## 6. Acceptance criteria pre Sub-round 4

| # | Kritérium | Verifikácia |
|---|---|---|
| 1 | Všetky F-001..F-006 otvorené otázky majú navrhované resolution | Manual review §3 — 20 otázok pokrytých |
| 2 | Resolution per quality-first principle (jedno najlepšie riešenie) | §3 každá otázka má 1 explicit doporučenie + rationale |
| 3 | Žiadne minimal/MVP/skip varianty ako default odporúčania | Manual review §3 — žiadne také odporúčanie |
| 4 | Cross-otázka konzistencia (duplicates resolved konzistentne) | O-DS-1 ≡ O-003-1, O-DS-2 ≡ O-006-1 — rovnaké odpovede |
| 5 | Otvorené pre v0.3.0+ explicit zaznamenané | §5 obsahuje 6 položiek s dôvodom odkladu |
| 6 | Direktor batch approval rešpektovaný | Resolution návrhy presnejšie ako Direktor's pôvodné variants |

---

## 7. Krížové odkazy

| Dokument | Súvislosť |
|---|---|
| `development-spec.md` §10 | 4 otvorené otázky (O-DS-1 až O-DS-4) |
| `F-001-coordinator-charter.md` | Žiadne explicit otvorené otázky (kompletný charter) |
| `F-002-inbox-deda.md` §11 | 4 otvorené otázky (O-002-1 až O-002-4) |
| `F-003-uat-environment.md` §15 | 4 otvorené otázky (O-003-1 až O-003-4) |
| `F-004-create-project-improvements.md` §8 | 4 otvorené otázky (O-004-1 až O-004-4) |
| `F-005-audit-smoke-test.md` §8 | 4 otvorené otázky (O-005-1 až O-005-4) |
| `F-006-agent-charter-updates.md` §8 | 4 otvorené otázky (O-006-1 až O-006-4) |

---

**Koniec dokumentu — Sub-round 4 Resolution.**
