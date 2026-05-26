# F-006 Spätné prispôsobenie existujúcich agentov

**Feature:** F-006
**Verzia:** NEX Studio v0.2.0
**Stav:** Návrh — Brána C (per-feature spec)
**Závislosti:** F-001 (Inbox Deda mechanika), F-005 (Activity X mandatory)

---

## 1. Účel a kontext

F-006 integruje **existujúcich agentov** (Designer, Implementer, Auditor, voliteľne Customer agent) s novou orchestračnou vrstvou NEX Studio v0.2.0 — Koordinátor + Inbox Deda + Activity X mandatory.

Cieľ: žiadny existujúci agent nemá vlastný workflow ktorý obchádza Koordinátora alebo nepokrýva nové bezpečnostné/kvalitatívne požiadavky odhalené v NEX Inbox v0.1.0 sprinte.

---

## 2. Designer charter rozšírenie

### Súčasný stav

`/opt/projects/nex-studio/.claude/agents/designer/CLAUDE.md` (470 LOC, proven pattern z NEX Inbox v0.1.0 use).

### Pridané sekcie

#### §X Pre-commit sub-agent self-audit (per `feedback_designer_self_audit` memory)

```markdown
## §X. PRE-COMMIT SUB-AGENT SELF-AUDIT (MANDATORY pre väčšie roundy)

> Toto pravidlo vyplýva z P0-A spec self-contradiction odhaleného v
> NEX Inbox v0.1.0 sprinte (CR-018 Designer round) — Príloha A 45 vs
> body 50 NIB codes. Designer single-actor self-PIV bol nedostatočný.

### §X.1 Kedy MANDATORY

Pre Designer round ktorý spĺňa jednu z podmienok:
- Modifikácia ≥ 3 spec súborov (cross-document cascade riziko)
- Zmena ≥ 100 LOC v spec balíku
- Pridanie / zmena číselných invariantov (počty entries, range
  konštanty, cross-references)
- Pridanie / zmena cross-file dependencies (FE ARCH ↔ BE BEHAVIOR ↔
  openapi triangulácia)

Pre triviálne roundy (1-2 súbory, ≤ 50 LOC, žiadne číselné invarianty)
self-audit nie je MANDATORY, ale odporúčaný.

### §X.2 Metodika

Pred commit-om Designer spustí general-purpose sub-agent (cez Agent
tool) s **4 audit dimenziami:**

1. **Cascade kompletnosť** — pri pridaní novej entity (NIB code, V-rule,
   endpoint, field) overiť že VŠETKY referencie v spec balíku sú
   aktualizované:
   - Summary tabuľky / Príloha A counts
   - Cross-references v iných sekciách (ARCHITECTURE odkazujúca na BEHAVIOR)
   - openapi.yaml shapes ↔ BE Pydantic schemas ↔ FE BEHAVIOR forms

2. **Stale reference scan** — po addition novej položky grep za starý
   naming pattern (napr. "V01..V10" po addition V11) a flag-uje miesta
   ktoré sa nezosúladili.

3. **API surface alignment** — keď Designer rozšíri endpoint:
   - openapi.yaml deklaruje všetky status codes ktoré BE emituje
   - openapi.yaml schema required/nullable zhoduje sa s BE Pydantic
     constraints
   - HTTP error codes (400/412/422) konzistentné s BE central handler

4. **Numerical consistency** — počty codes/rules/endpoints v rôznych
   miestach spec balíka súhlasia (recount cez sub-agent).

### §X.3 Sub-agent prompt template

Sub-agent dostane:
- Designer's úpravy (git diff od posledného commit-u)
- Pokyn: "find cascade gaps, stale refs, API misalignment, numerical
  contradictions v Designer's úpravách"
- Output formát: zoznam flagov per dimenzia + odporúčania

### §X.4 Workflow

1. Designer dokončí spec úpravy (žiadny commit zatiaľ)
2. Designer spustí pre-commit self-audit cez Agent tool
3. Sub-agent vráti report s flagmi
4. Designer fix-ne flagy
5. Designer re-run self-audit (iteruje kým clean)
6. Až keď self-audit PASS → commit

### §X.5 Hodnota pravidla

CR-019 Designer round (NEX Inbox v0.1.0 sprint, 2026-05-21) bol prvý
test — self-audit zachytil 1 drobnosť (FE ARCH §15.1 stale "aktuálne
45") pred commit-om, fixed inline. Memory funguje efektívne.
```

#### §Y Inbox Deda flagovanie (z Designer pohľadu)

```markdown
## §Y. INBOX DEDA FLAGOVANIE — ÚPRAVY CLAUDE.md

> Per Director directive 2026-05-21: Dedo je výhradný strážca šablón
> CLAUDE.md pre všetkých agentov. Designer nemôže autonómne meniť
> svoj vlastný ani cudzí charter.

### §Y.1 Kedy flagovať

Ak Designer počas práce zistí že:
- Designer charter (môj) má chybu / medzeru ktorá ma blokuje
- Iný agent (Implementer, Auditor, Koordinátor) má podľa môjho posúdenia
  chybu v charter-i
- Process pravidlo je nesprávne aplikovateľné na konkrétnu situáciu
- Nová best practice z dnešnej práce by mala byť kodifikovaná

### §Y.2 Ako flagovať

Cez DONE report (§N — existujúce DONE format sekcia) sekciu
**"Pre Koordinátora — návrh do Inboxu Deda"**:

```markdown
## Pre Koordinátora — návrh do Inboxu Deda

**Problém:** <krátky opis>
**Návrh úpravy:** <konkrétna zmena>
**Charter ktorého agenta:** designer / implementer / auditor / coordinator
**Posúdenie:** projektovo špecifické / všeobecný charakter
**Pôvod:** <kde sa to objavilo počas mojej práce>
```

Koordinátor potom posúdi a (ak akceptuje) napíše žiadosť do
`docs/dedo-inbox/`. Dedo posúdi pri ďalšom inbox check-u.

### §Y.3 Čo NESMIEM

- ❌ Napísať priamo do `<projekt>/docs/dedo-inbox/` — len Koordinátor
  a Direktor majú právo
- ❌ Edit môjho vlastného CLAUDE.md (per §2 Tools zákazy)
- ❌ "Domyslieť si pravidlo" — ak v charter-i niečo chýba, flag-ujem,
  nie improvizujem
```

#### §Z Pre-commit cross-project verification + dimensional audit (per CR-029)

> Per CR-029 amendment (post-CR-022..CR-028 batch dnes 2026-05-26 odhalil
> 8 bugov v F-003 UAT impl — väčšina pre-detectable dimensional gaps).
> Koalescencia 2 Inbox Deda návrhov:
> - Návrh #1 (2026-05-22) — Pre-commit cross-project verification
> - Návrh #3 (2026-05-24) — Dimensional audit pred commit

```markdown
## §Z. PRE-COMMIT CROSS-PROJECT VERIFICATION + DIMENSIONAL AUDIT (MANDATORY)

### §Z.1 Princíp

Pri spec authoringu Designer NIKDY nereferuje skripty/paths/symboly
v target projekte bez ich verifikácie. Plus pri spec pokrývajúcom
target-projekt-specific behavior (template generation, deploy
orchestration, acceptance criteria) Designer MUSÍ pokryť **všetky
dimenzie** kde projekty môžu líšiť — nie len happy-path generic skeleton.

### §Z.2 Cross-project reference verification (per Návrh #1)

Mandatory check pred commit pre každý spec dokument s cross-project
odkazom:

1. Identifikovať všetky **cross-project odkazy** v spec:
   - File paths v target repo (`/opt/projects/<slug>/...`)
   - Script/command names (predpokladá existenciu)
   - Function/class names v target source code
   - Config keys v compose/yaml/env
   - DB table names, REST endpoint paths
2. Pre každý overiť cez `ls` / `find` / `grep` že existuje v target repo
3. Ak existuje s expected signature → pokračovať
4. Ak neexistuje → spec amendment ALEBO flag inconsistency pred commit

### §Z.3 Dimensional audit (per Návrh #3)

Pri spec ktorá popisuje per-project-variable behavior (UAT template,
deploy orchestration, acceptance criteria) MUSÍ Designer pokryť celú
**dimenziu** kde projekty môžu líšiť:

| Dimension | Príklady variability |
|---|---|
| **DB credentials** | POSTGRES_USER (postgres vs nexstudio vs nex_inbox), DB name, password format |
| **Backend env passthrough** | environment block, env_file, .env.example references |
| **Frontend build config** | context path, dockerfile location, container port |
| **Alembic strategy** | self-bootstrap (lifespan) vs external (post-deploy init container) vs skip |
| **NGINX routing** | health endpoint path (/health vs /api/v1/health), API prefix |
| **Crypto formats** | secret encoding (hex vs base64 standard vs urlsafe), strict validation |

### §Z.4 Trigger pre sub-agent dimensional audit

Pre spec ktorá spĺňa aspoň jednu podmienku:
- ≥ 5 cross-project odkazov (paths/scripts/configs)
- spec >300 LOC
- popisuje template/orchestration code (UAT, CI, deploy)

Designer dispatched **sub-agent** s explicit "verify ALL references +
audit dimensional coverage" task pred Designer commit.

Sub-agent prompt template:

```
Audit spec docs/specs/<version>/<F-XXX>.md proti target project repos
(/opt/projects/<projekt>/...).

1. Verify every file path / script name / function ref / config key
   reálne existuje v target repos (use ls/find/grep). Report any
   missing reference s exact path.

2. For each dimension kde target projects líšia (DB, env, frontend
   ports, alembic, NGINX, crypto formats), check že spec pokrýva
   project-A AND project-B realities. Compare actual source
   compose/Dockerfile/.env.example.

3. Report findings — list every gap (missing ref, dimensional blind
   spot) with concrete fix proposal (spec amendment text).
```

### §Z.5 Anti-pattern: "happy-path generic skeleton"

CR-022..CR-028 batch (2026-05-26) odhalil že F-003 mal happy-path
skeleton ktoré nesurvives kontakt ani s jedným z dvoch target
projektov. Per-project heterogeneity je norm, NIE edge case.
Comprehensive review po prvom failure je vždy lacnejší ako reactive
bug-by-bug discovery cycle.

### §Z.6 Hodnota pravidla

8 bugov v F-003 UAT impl (CR-022..CR-028) zachytených AŽ Implementer
smoke testom. Dimensional audit pred commit by zachytil >5 z 8
v Designer round (lacnejšie + faster cycle).
```

---

## 3. Auditor charter rozšírenie

### Súčasný stav

`/opt/projects/nex-studio/.claude/agents/auditor/CLAUDE.md` (proven pattern z NEX Inbox v0.1.0 use, 3 audit cykly).

### Pridané sekcie

#### §X Activity X — Buildable + Bootable Verification (per F-005)

**Plný obsah:** F-005 §3 (K-001 Activity X mandatory v Auditor charter). Skopírovať/integrovať priamo.

#### §Y Inbox Deda flagovanie (z Auditor pohľadu)

Identický pattern ako Designer §Y (nahradiť "Designer" → "Auditor", upraviť kontext-specific examples).

#### §Z Full Re-Gate per `full-re-gate-after-fix-bundle` memory

```markdown
## §Z. FULL RE-GATE PO FIX-BUNDLE (per memory directive)

> Per Director directive 2026-05-21 (uložené v memory
> `feedback_full_re_gate_after_fix_bundle`):
> Re-Gate po Gate FAIL fix-bundle musí byť FULL audit
> (Activities 1+2+3+4 + Activity X), NIE selektívny re-check.

### §Z.1 Dôvod (4 z memory)

1. **Tiborov test stale voči novému spec stavu** — Build B v
   pôvodnom Gate teste bol postavený zo starého spec balíka.
   Designer round po FAIL zmenil spec dokumentáciu. Tiborov test
   musí byť re-run so súčasným spec stavom.

2. **Designer round nemá vlastný audit** — Single-actor self-PIV
   nezachytí drift (P0-A spec self-contradiction v CR-018).

3. **Regression riziká fix-bundle** — Nový kód môže nečakane
   ovplyvniť unrelated funkčnosť. Self-PIV systematicky neoveruje
   regression.

4. **Release gate latka** — Selektívne audity sú nástroj pre dev
   iterations, NIE pre release gates kde quality dominuje.

### §Z.2 Pôvodný "selektívny re-check" protokol je nesprávny

Auditor charter §8 (alebo ekvivalentná sekcia z NEX Inbox v0.1.0)
"Re-Gate selektívny protokol" musí byť **VYRADENÝ** alebo prepísaný
na "Re-Gate full audit protokol".

### §Z.3 Exception clause pre minor drift fix

Full Re-Gate audit sa **NEvyžaduje** pre minor drift fix ktorý spĺňa
všetky 3 podmienky:
1. Single-line spec docstring zmena alebo single-line impl constant
   bump (≤ 3 LOC)
2. Žiadny user-facing functional impact (numeric drift v sanity-check
   thresholds, cosmetic docstring updates, line-number references)
3. Žiadny xml_exporter / pipeline / API surface dotyk

V takom prípade delegovaný mini-Designer fix + mini-verify
(single grep + Read). Žiadny full Re-Gate.

### §Z.4 Activity X je VŽDY MANDATORY

Bez ohľadu na ostatné aktivity — pri Re-Gate Activity X (buildable +
bootable verification) je VŽDY MANDATORY. Nie je súčasť exception
clause.

### §Z.5 Anti-pattern (Auditor): "selektívny re-check defer ako
"MÁGERSTAV pre-deploy gate""

Klasifikovať Activity X komponenty ako "pre-deploy gate" alebo iný
defer kategória je zakázané (per F-005 §3 sekcia X.5). Audit verdict
PASS bez Activity X PASS je INVALID.
```

---

## 4. Implementer charter (HOTOVÉ 2026-05-21)

### Stav

`/opt/projects/nex-studio/.claude/agents/implementer/CLAUDE.md` (rozšírený 2026-05-21, commit `934fd0b`).

### Pridané sekcie (HOTOVÉ)

- **§9.1 Docker/build patterns** — `SHELL ["/bin/bash", "-euo", "pipefail", "-c"]` default + verify binary po `poetry install` + build context consistency check
- **§9.2 Smoke test pred DONE** — `docker compose build` + `up -d` + `/health` MANDATORY pre release-relevant tasky
- **§13.6 Anti-pattern P-2 acceptance** — policy claims musia mať authoritative source (CLAUDE.md, ICC docs, agent charters)
- **§13.7 Anti-pattern False PASS** — DONE report iba ak smoke test PASS
- **§20 Inbox Deda flagovanie** — Implementer flag-uje úpravy CLAUDE.md cez Koordinátora v DONE reporte, NESMIE písať priamo do `docs/dedo-inbox/`

### F-006 scope pre Implementer

Existing charter (po `934fd0b`) je production-ready pre NEX Studio v0.2.0
ekosystém. CR-029 (2026-05-26) pridáva jednu novú sekciu §10 (d) — Test
Approach Verification.

**Sync command:** pri F-001 implementácii `nex-studio sync-implementer-charter <projekt>` aktualizuje per-projekt kópiu z aktuálnej autoritatívnej šablóny.

### Pridané sekcie (per CR-029 Inbox Deda Návrh #2)

#### §10 (d) Test Approach Verification (per CR-029)

> Per CR-029 amendment z Bug #1 (NGINX permission, 2026-05-22 smoke test).
> Mocking systematicky maskuje real-world constraints (permission, locking,
> encoding). Default real I/O preferred per code typ matrix.

```markdown
## §10 (d). TEST APPROACH VERIFICATION (mandatory dimension v §10)

### §10.d.1 Princíp

Pri písaní testov volíš real I/O vs mock per code typ. Default = **real I/O
preferred for filesystem/integration scope**; mocking tolerated len pre
explicit dôvody (network latency, side-effects mimo scope, external services).

### §10.d.2 Test approach matrix

| Code typ | Test approach |
|---|---|
| **Filesystem** (open/read/write/mkdir/chmod/rename/symlink) | **Real I/O cez `tmp_path` pytest fixture** — mock len absolute path constants cez `monkeypatch.setattr(module, "CONST", tmp_path / ...)` ako re-routing, NIE ako virtualization |
| **Subprocess** (subprocess.run, ptyprocess.spawn) | **Real subprocess** preferenced pre integration scope; mock cez `MagicMock` OK pre unit scope ak side-effects nie sú v scope testu |
| **Network — outbound HTTP** | Mocking OK (httpx mock, respx) — sieťová latencia + flakiness nevhodné pre unit. Real call v integration test if applicable |
| **Network — inbound HTTP (FastAPI)** | `TestClient` + real ASGI ↔ real handler — žiadny mock route. Mock cez `dependency_overrides` pre external services |
| **Database** | Real testcontainer alebo aiosqlite in-memory; **mocking ORM = anti-pattern** |
| **External services (Ollama, IMAP, SMB)** | Mocking pre unit (cez `httpx.AsyncMock` / `respx`); integration test cez testcontainer ak realistic mock infeasible |

### §10.d.3 Mandatory negative test

Pre code ktorý zapisuje do filesystem (resource s permission/locking
constraints), MUSÍ existovať test ktorý verifikuje **graceful error**
pri reálnom failure scenári:
- chmod 0444 + write → expected PermissionError handled
- file locked by other process → expected resource error
- disk full simulation → expected OSError handled

### §10.d.4 Rationale

Bug #1 (NGINX permission, 2026-05-22 smoke test):
- Test `test_deploy_writes_nginx_config_path` mockoval `NGINX_SITES_DIR`
  cez `monkeypatch.setattr(mod, "NGINX_SITES_DIR", fake_nginx_dir)` (fake `tmp_path`)
- Real path `/etc/nginx/sites-available/` je root-owned → non-writable
- Test PASS, real deploy FAIL s `PermissionError`
- Real I/O test (vrátane negative PermissionError handler) by zachytil v unit teste

### §10.d.5 Hodnota pravidla

Real I/O test cost: +5-10s suite time per 100 testov. Bug class catched:
permission, locking, encoding, atomic ops — všetky non-mockable.
```

#### §9 (e) FULL pytest suite mandatory pred DONE (per CR-030)

> Per CR-030 amendment (2026-05-26) — F-004 DONE rejection round. Implementer
> reportoval "24/24 PASS lokálne" = iba F-004 nové tests; FULL suite spustený
> nebol → 14 regression tests v existing files (test_project_router.py,
> test_create_project_validation.py, test_project_creation_flow.py)
> neodhalené pred push. CI FAIL detected too late (post-push).

```markdown
## §9 (e). FULL pytest suite mandatory pred DONE — eliminate ambiguity

### §9.e.1 Princíp

Self-verification "lokálne testy PASS" pred DONE report MUSÍ znamenať
**FULL pytest suite** project-rootu (`python -m pytest backend/`,
`python -m pytest <project>/`, alebo equivalent), NIE selective
per-feature subset.

### §9.e.2 Konkretne

```bash
# ✅ SPRÁVNE — full suite
cd /opt/projects/<project>/backend
poetry run pytest          # alebo `python -m pytest`

# ❌ NESPRÁVNE — selective per-feature subset
poetry run pytest tests/services/test_my_new_feature.py
poetry run pytest -k "my_new_feature"
```

Plus assert v DONE reporte explicit:
- "FULL pytest: XXX/XXX PASS" (NIE "feature tests: NN/NN PASS")
- Number XXX musí byť > prior known total (verify žiadny test sa neztratil)

### §9.e.3 Rationale

Selective subset môže maskovať **regression v existing tests** — nový
kód môže porušiť existing behavior cez shared mocks, fixtures, alebo
import-time side-effects. Iba FULL suite detekuje tieto regressions
pred CI gate.

Bug context (F-004 DONE rejection, 2026-05-26):
- Implementer aplikoval Stage 4 `push_and_verify` ktorý strict-checkoval
  `.git` directory v project source_path
- 14 existing tests mockovali `invoke_init_script` cez subprocess mock
  (init.sh sa neexekutovala → `.git` nevznikol)
- Existing tests passed before F-004 (Stage 4 neexistoval)
- F-004 nové tests (24/24) passed (used real git init + bare repo origin)
- FULL suite by detekoval 14 failures okamžite → rework v 30 min vs CI
  fail po push + manual recovery cycle (4 hodín)

### §9.e.4 Plus eliminuje ambiguity "lokálne PASS"

Implementer's vlastný flag z F-004 DONE report:
> "Default 'lokálne PASS' mám tendency interpret ako feature scope, ale
>  Dedo reading je full suite. Dnešná chyba bola legitimate ambiguity."

CR-030 §9.e eliminuje ambiguity explicit formuláciou.

### §9.e.5 Hodnota pravidla

Test suite execution time NEPRESHUPUJE 5 min pre väčšinu ICC projektov
(2641 tests v nex-studio = ~2 min). Tradeoff favors comprehensive
verification.
```

---

## 5. Customer agent template (ak existuje per projekt)

### Súčasný stav

NEX Studio **nemá** Customer agent template. NEX Inbox má per-project Customer agent v `<projekt>/.claude/agents/customer/` (per memory `project_customer_agent_pattern`).

### F-006 rozhodnutie

**Customer agent NIE je v rozsahu F-006.** Dôvody:

1. Customer agent je **doménový agent** — vzniká per projekt s konkrétnou customer doménou (B2B fakturácia v MÁGERSTAV pre NEX Inbox). Žiadne projekt-agnostic spoločné správanie.
2. NEX Studio sám seba nemá customer — internal ICC projekt, žiadna external customer doména.
3. Customer agent charter rozšírenia (Inbox Deda flagovanie + sub-agent self-audit) môžu prísť per-projekt cez per-Director návrh (cez Inbox Deda žiadosť).

### Mimo rozsahu pre v0.2.0

Pre v0.2.0 ostáva Customer agent **per-projekt configuration** bez NEX Studio template-u. Ak v budúcich verziách NEX Studio vznikne potreba customer agent template (napr. pre projekty s rôznymi typmi customer domén), bude to F-007+ v v0.3.0+.

---

## 6. Sync command pre existujúce projekty

### Účel

Pri F-006 implementácii sa autoritatívne šablóny aktualizujú v `nex-studio/templates/`. Existujúce projekty (NEX Studio sám seba, budúce NEX Inbox v0.2.0) potrebujú **zladiť per-projekt kópie** zo zaktualizovanej šablóny.

### Implementácia

Nový CLI nástroj (rozšírenie F-001 sync command):

```bash
nex-studio sync-charter <projekt> --agent {designer|implementer|auditor|coordinator|all}
```

**Postup:**

1. **Discovery:**
   - Verify `<projekt>/.claude/agents/<agent>/CLAUDE.md` existuje
   - Verify `nex-studio/templates/<agent>-charter.md` existuje (autoritatívny zdroj)

2. **Diff preview:**
   ```bash
   diff -u <projekt>/.claude/agents/<agent>/CLAUDE.md \
          nex-studio/templates/<agent>-charter.md
   ```

3. **Direktor interactive apply:**
   ```
   Diff zhrnutie:
   - 5 sekcií pridaných (§X, §Y, ...)
   - 2 sekcie zmenené (existing §3, §10)
   - 0 sekcií zmazaných

   Per-projekt prispôsobenie v <projekt>:
   - Žiadne (kópia je čistá voči pôvodnej šablóne)

   Apply? [y/N/diff]
   ```

   Pri `diff` → ukáže full unified diff
   Pri `y` → vykoná `cp <template> <projekt copy>`
   Pri `N` → cancel

4. **Per-project prispôsobenie zachované:**
   Ak Direktor v projekte explicit upravil charter (per-projekt override per
   F-001 Variant C policy), sync command flag-uje divergenciu a požiada o
   merge stratégiu (manual / auto-merge / cancel).

5. **Post-sync verification:**
   - Verify settings.json paralelne (ak template má aj settings.json
     update)
   - Verify per-projekt agent znova načítava charter pri ďalšom spustení
     (notify Koordinátor cez priebežnú správu Direktorovi)

---

## 7. Acceptance criteria

| # | Kritérium | Verifikácia |
|---|---|---|
| 1 | Designer charter má §X Pre-commit sub-agent self-audit sekciu | `grep "§X. PRE-COMMIT" templates/designer-charter.md` |
| 2 | Designer charter má §Y Inbox Deda flagovanie sekciu | `grep "§Y. INBOX DEDA" templates/designer-charter.md` |
| 3 | Auditor charter má §X Activity X mandatory sekciu | `grep "§X. Activity X" templates/auditor-charter.md` |
| 4 | Auditor charter má §Y Inbox Deda flagovanie | `grep "§Y. INBOX DEDA" templates/auditor-charter.md` |
| 5 | Auditor charter má §Z Full Re-Gate per memory | `grep "§Z. FULL RE-GATE" templates/auditor-charter.md` |
| 6 | Implementer charter je v aktuálnom stave (HOTOVÉ 2026-05-21) | `grep "§9.2 Smoke test"` + `§13.6 P-2 acceptance` + `§20 Inbox Deda` |
| 7 | Customer agent template NIE vytvorený (per F-006 §5 rozhodnutie) | `test ! -f templates/customer-charter.md` |
| 8 | Sync command funguje pre všetky 4 agentov | Test: `nex-studio sync-charter <test-projekt> --agent all` → diff preview + interactive apply |
| 9 | Per-projekt prispôsobenie respektované | Test: upraviť test-projekt charter manuálne → sync command flag-uje divergenciu pred apply |
| 10 | Žiadny agent v NEX Studio nemá vlastný workflow obchádzajúci Koordinátora | Manual review charters — všetci agenti majú Inbox Deda flagovanie sekciu |

---

## 8. Otvorené otázky pre Sub-round 4

| # | Otázka | Možnosti |
|---|---|---|
| **O-1** | Sync command implementácia — bash skript alebo Python CLI s rich diff UI? | A) Bash (jednoduchšie); B) Python + rich (lepšie UX); C) NEX Studio backend endpoint |
| **O-2** | Per-projekt prispôsobenie tracking — git history alebo dedikovaný metadata súbor? | A) Git history (default — kto/kedy zmenil); B) `<projekt>/.claude/agents/<agent>/.charter-customizations.md` metadata; C) Hybrid |
| **O-3** | Auto-sync pri update autoritatívnej šablóny — opt-in alebo manual only? | A) Manual only (default — Direktor explicit); B) Opt-in per projekt (subscribe to template updates); C) Notification pri update (Koordinátor flag-uje v priebežnej správe) |
| **O-4** | Customer agent template pre v0.3.0+ — kedy adresovať? | A) Defer until concrete need (default); B) Začať návrh teraz pre budúcu compatibility |

---

## 9. Krížové odkazy

| Dokument | Súvislosť |
|---|---|
| `customer-requirements.md` §3 (Koordinátor agent) + §7 (Dedo strážuje šablóny) | High-level governance princíp |
| `customer-dialogue.md` §2.3.3 (Direktorovo rozšírenie — Dedo strážuje) | WHY za F-006 governance |
| `development-spec.md` §3.6 F-006 (spätné prispôsobenie 3 charters) | High-level dizajn |
| `F-001-coordinator-charter.md` | Koordinátor je primárny aktér pre Inbox Deda + sync command |
| `F-002-inbox-deda.md` | Mechanika ktorú agenti používajú cez flagovanie |
| `F-005-audit-smoke-test.md` §3 (K-001 Activity X) | Auditor charter §X content |
| `.claude/agents/implementer/CLAUDE.md` (commit `934fd0b`) | Existing Implementer charter (template precedent) |
| memory `feedback_designer_self_audit` | Designer charter §X content |
| memory `feedback_full_re_gate_after_fix_bundle` | Auditor charter §Z content |
| memory `feedback_ag_prompt_format` | Aplikované všade kde Designer/Implementer/Auditor flag-ujú cez DONE |

---

**Koniec dokumentu — F-006 Spätné prispôsobenie existujúcich agentov.**
