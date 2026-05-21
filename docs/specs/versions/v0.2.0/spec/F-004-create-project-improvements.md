# F-004 Create Project vylepšenia

**Feature:** F-004
**Verzia:** NEX Studio v0.2.0
**Stav:** Návrh — Brána C (per-feature spec)
**Závislosti:** F-001 (Koordinátor agent setup), `templates/coordinator-charter.md`, `templates/coordinator-settings.json` musia existovať pred F-004 implementáciou
**Rieši:** P0-RG1 z `docs/findings/2026-05-21-release-verification-gaps.md` (Finding 1)

---

## 1. Účel a kontext

F-004 rieši **P0 NEX Studio bug** odhalený pri NEX Inbox v0.1.0 release: GitHub úložisko `rauschiccsk/nex-inbox` zostalo prázdne 8 dní napriek 80+ lokálnym commitom + git tag `v0.1.0`. Root cause: Create Project workflow **silent failure** medzi `gh repo create` (prešiel) a `git remote add origin` + initial push (nikdy nespustilo).

Plus rieši ďalšie gapy odhalené pri snahe spustiť NEX Inbox v0.1.0:
- **P0-RG3** silent install fail (saxonche bez Java) — Dockerfile bez `set -e` produkoval image bez `.venv`, runtime crash
- **P0-RG5** žiadny end-to-end smoke test v CI/audit — release verdict bol false-positive

Cieľ verzie: **žiadny ďalší projekt po Create Project nie je v stave "polovičatý scaffold"**. Každý projekt po vzniku má funkčný git remote, buildable images, pripravený Koordinátor agent a (voliteľne) CI/CD pipeline.

---

## 2. Existujúci Create Project workflow (čo existuje + medzery)

### Čo existuje

**NEX Studio frontend tlačidlo "Create new project":**
- Per memory `nex-studio-fe-prod-build` — frontend je nginx static bundle, NIE Vite dev. Tlačidlo otvorí formulár (slug, project name, popis, voliteľné parametre).
- Formulár submit → POST request na backend.

**Backend endpoint:**
- `POST /api/v1/projects` (pravdepodobne — overiť pri implementácii F-004)
- Validácia (slug uniqueness, dependencies, atď.)
- Spúšťa scaffold skript

**Scaffold skript `scripts/create-project.sh` (alebo backend logic):**
1. Create `/opt/projects/<slug>/` adresár
2. Generuje základnú štruktúru (backend/, frontend/, docs/, .claude/)
3. Initial git commit
4. `gh repo create rauschiccsk/<slug>`

### Známe medzery (z NEX Inbox v0.1.0 sprintu)

| # | Medzera | Dopad |
|---|---|---|
| **M1** | `gh repo create` prešiel, ale `git remote add origin` + `git push -u origin main` nikdy nespustilo. Silent failure medzi 2 krokmi scaffold-u | 80+ commitov zostalo iba lokálne 8 dní. Riziko stratenia pri ANDROS catastrophic loss. |
| **M2** | Žiadny buildable smoke test pri vzniku | Backend Dockerfile bug (`COPY pyproject.toml poetry.lock ./` build context mismatch) odhalený až pri prvom reálnom rebuild-e — 3 týždne po vzniku projektu. |
| **M3** | Žiadny Koordinátor agent setup | NEX Studio má templates pre Designer/Implementer/Auditor charters, ale Koordinátor neexistuje. Po F-001 implementácii treba ho aj pri Create Project pridať. |
| **M4** | Žiadne overenie že template projekty sú immediately buildable | Pri prvom rebuild-e nájdené 5 P0 release-gate bugov (P0-RG1..P0-RG5). |

F-004 rieši M1-M4 cez 5 komponentov.

---

## 3. 5 pridaných komponentov

### 3.1 K-001 Post-scaffold verification

**Účel:** Overiť že git scaffold prešiel kompletne — origin remote nastavený + initial commit pushed.

**Implementácia:**

Po základnom scaffolde (kroky `gh repo create` + `git remote add origin` + `git push -u origin main`) skript overí:

```bash
# Krok A: Overiť že origin remote existuje
if ! git -C /opt/projects/<slug> remote -v | grep -q "^origin"; then
    echo "FAIL: git remote 'origin' nie je nastavený"
    exit 1
fi

# Krok B: Overiť že initial commit prešiel do remote
LOCAL_HEAD=$(git -C /opt/projects/<slug> rev-parse HEAD)
REMOTE_HEAD=$(git -C /opt/projects/<slug> ls-remote origin HEAD | cut -f1)
if [ "$LOCAL_HEAD" != "$REMOTE_HEAD" ]; then
    echo "FAIL: initial commit nie je pushed do remote"
    echo "  Local HEAD:  $LOCAL_HEAD"
    echo "  Remote HEAD: $REMOTE_HEAD"
    exit 1
fi

# Krok C: Overiť že GitHub repo má aspoň 1 commit (nie iba README z gh repo create)
COMMIT_COUNT=$(gh api repos/rauschiccsk/<slug>/commits --paginate | jq length)
if [ "$COMMIT_COUNT" -lt 1 ]; then
    echo "FAIL: GitHub repo má 0 commits"
    exit 1
fi

echo "PASS: post-scaffold verification"
```

**Pri zlyhaní:** STOP, hlásiť Direktorovi cez NEX Studio FE error notification. Aktivuje K-002 Rollback handler.

### 3.2 K-002 Rollback pri partial failure

**Účel:** Ak `gh repo create` prešlo ale následný krok zlyhal (alebo K-001 overuje fail), projekt nesmie zostať v polovičatom stave.

**Implementácia:**

```bash
# Registrovaný trap pri začiatku scaffold-u
trap rollback_on_failure ERR

rollback_on_failure() {
    echo "ROLLBACK: Scaffold zlyhal, čistím partial state..."

    # Krok 1: Retry git push (jeden retry pre transient network issues)
    if [ "$ROLLBACK_RETRY_COUNT" -eq 0 ]; then
        export ROLLBACK_RETRY_COUNT=1
        echo "Retry git push (1/1)..."
        if git -C /opt/projects/<slug> push -u origin main; then
            echo "PASS: retry git push prešiel"
            return 0
        fi
    fi

    # Krok 2: Po druhom zlyhaní rollback
    echo "Retry zlyhal, čistím..."

    # Local cleanup
    rm -rf /opt/projects/<slug>/.git

    # GitHub repo delete (s confirm Direktorovi)
    read -p "Rollback: zmazať GitHub repo rauschiccsk/<slug>? [y/N] " confirm
    if [ "$confirm" = "y" ]; then
        gh repo delete rauschiccsk/<slug> --yes
    fi

    echo "ROLLBACK kompletný. Projekt /opt/projects/<slug>/ existuje bez git."
    echo "Spustite create-project znova alebo manuálne nastavte git."
    exit 1
}
```

**Idempotent re-run safe:** Po rollback Direktor môže spustiť `create-project <slug>` znova bez konfliktu (GitHub repo zmazaný, lokálny .git zmazaný).

### 3.3 K-003 Koordinátor agent setup integrácia

**Účel:** Pri Create Project nainštalovať Koordinátor agent (z F-001 templates).

**Implementácia:**

```bash
# Po základnom scaffolde, pred K-004 smoke test

PROJECT_ROOT="/opt/projects/<slug>"
NEX_STUDIO_TEMPLATES="/opt/projects/nex-studio/templates"

# Krok A: Vytvor Koordinátor agent priečinok
mkdir -p "$PROJECT_ROOT/.claude/agents/coordinator"

# Krok B: Skopíruj charter
cp "$NEX_STUDIO_TEMPLATES/coordinator-charter.md" \
   "$PROJECT_ROOT/.claude/agents/coordinator/CLAUDE.md"

# Krok C: Skopíruj settings.json + path substitution
sed "s|<PROJECT_ROOT>|$PROJECT_ROOT|g" \
    "$NEX_STUDIO_TEMPLATES/coordinator-settings.json" \
    > "$PROJECT_ROOT/.claude/agents/coordinator/settings.json"

# Krok D: Vytvor Inbox Deda priečinok + .gitkeep
mkdir -p "$PROJECT_ROOT/docs/dedo-inbox/processed"
touch "$PROJECT_ROOT/docs/dedo-inbox/.gitkeep"
touch "$PROJECT_ROOT/docs/dedo-inbox/processed/.gitkeep"

# Krok E: Inicializačný decisions-log.md
cat > "$PROJECT_ROOT/docs/dedo-inbox/decisions-log.md" <<'EOF'
# Decisions Log — Inbox Deda

> Chronologický súhrn rozhodnutí Deda. Newest first.
> Detail v `processed/<súbor>.md`.

---

(žiadne rozhodnutia zatiaľ — projekt práve vznikol)
EOF

# Krok F: Vytvor session log priečinok pre Koordinátora
mkdir -p "$PROJECT_ROOT/docs/session-logs/coordinator"
touch "$PROJECT_ROOT/docs/session-logs/coordinator/.gitkeep"

# Krok G: Initial state file (vynechané z gitu)
cat > "$PROJECT_ROOT/.nex-coordinator-state.md" <<EOF
# Coordinator state — <slug>

**Last update:** $(date -Iseconds)
**Active version:** v0.1.0 (planned)
**Active round:** žiadne (projekt práve vznikol)
**Open decisions:** žiadne
**Inbox Deda:** 0 pending
**Next step:** Čaká na Designer round (Customer Requirements upload)
EOF
```

**Path substitution `<PROJECT_ROOT>`:** Per F-001 settings.json template — všetky cesty v Allow/Deny zoznamoch sa nahradia konkrétnym `/opt/projects/<slug>/`.

**Validation:** Po setup overí že súbory existujú:
```bash
test -f "$PROJECT_ROOT/.claude/agents/coordinator/CLAUDE.md" || exit 1
test -f "$PROJECT_ROOT/.claude/agents/coordinator/settings.json" || exit 1
test -d "$PROJECT_ROOT/docs/dedo-inbox" || exit 1
```

### 3.4 K-004 Buildable smoke test pri vzniku

**Účel:** Overiť že template projekt je **immediately buildable** — žiadny ďalší Dockerfile bug ako P0-RG1 v NEX Inbox.

**Implementácia:**

```bash
# Po K-003 (Koordinátor setup) a pred K-005 (CI/CD opt-in)

PROJECT_ROOT="/opt/projects/<slug>"

# Smoke test má 2 úrovne:
# A) Minimal (default) — len docker compose build (rýchle ~1-2 min)
# B) Full (opt-in) — build + up + health check (~5-7 min)

cd "$PROJECT_ROOT"

# === A) Minimal smoke test ===
echo "Spúšťam minimal smoke test (docker compose build)..."

if ! docker compose build 2>&1 | tee /tmp/create-project-build-<slug>.log; then
    echo "FAIL: docker compose build zlyhal"
    echo "Logy v /tmp/create-project-build-<slug>.log"
    exit 1
fi

echo "PASS: minimal smoke test"

# === B) Full smoke test (voliteľný cez --full-smoke flag) ===
if [ "$FULL_SMOKE" = "true" ]; then
    echo "Spúšťam full smoke test (build + up + health)..."

    # Spusti DB
    docker compose up -d db
    sleep 10

    # Alembic migrácie (ak backend má alembic)
    if [ -f "$PROJECT_ROOT/backend/alembic.ini" ]; then
        cd "$PROJECT_ROOT/backend"
        poetry install --only main --no-root
        poetry run alembic upgrade head
        cd "$PROJECT_ROOT"
    fi

    # Plný stack
    docker compose up -d

    # Wait healthy
    for i in {1..30}; do
        if curl -sf http://localhost:8000/health 2>/dev/null; then
            echo "PASS: /health endpoint dostupný"
            break
        fi
        sleep 5
    done

    # Cleanup po smoke test
    docker compose down -v
    echo "PASS: full smoke test"
fi
```

**Pri zlyhaní:** STOP, log v `/tmp/create-project-build-<slug>.log`, hlásiť Direktorovi cez NEX Studio FE.

**Aktivuje K-002 rollback** ak Direktor schvaľuje (Create Project workflow zlyhal po vytvorení GitHub repa).

**Default = minimal smoke** (rýchly ~1-2 min). Full smoke test opt-in cez `--full-smoke` flag pre projekty kde je BE/DB stack jednoduchý.

### 3.5 K-005 Voliteľná CI/CD wire-up

**Účel:** Pre nové projekty ponúknuť template GitHub Actions workflow (Lint + Test + Build).

**Implementácia:**

```bash
# Po K-004 smoke test

# Direktor explicit opt-in (NIE default — niektoré projekty môžu mať vlastný CI setup)
if [ "$ENABLE_CICD" = "true" ]; then
    PROJECT_ROOT="/opt/projects/<slug>"
    NEX_STUDIO_TEMPLATES="/opt/projects/nex-studio/templates"

    # Skopíruj workflow template
    mkdir -p "$PROJECT_ROOT/.github/workflows"
    cp "$NEX_STUDIO_TEMPLATES/github-actions-workflow.yml" \
       "$PROJECT_ROOT/.github/workflows/ci.yml"

    # Commit + push CI configuration
    cd "$PROJECT_ROOT"
    git add .github/workflows/ci.yml
    git commit -m "feat(ci): initial CI workflow from NEX Studio template"
    git push origin main

    # Verify workflow registered
    sleep 5
    if gh workflow list --repo rauschiccsk/<slug> | grep -q "ci.yml"; then
        echo "PASS: CI workflow registrovaný"
    else
        echo "WARN: CI workflow committed ale gh workflow list nezachytil (manual check potrebný)"
    fi
fi
```

**Template `github-actions-workflow.yml`** (placeholder pre Sub-round 4 detail):

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Lint backend (ruff)
        run: cd backend && poetry run ruff check . && poetry run ruff format --check .
      - name: Lint frontend (ESLint)
        run: cd frontend && npm run lint && npm run type-check

  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Backend tests
        run: cd backend && poetry run pytest
      - name: Frontend tests
        run: cd frontend && npm test -- --run

  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Docker build
        run: docker compose build
```

**Direktor opt-in:** Pri Create Project formulári checkbox "Enable CI/CD (GitHub Actions)". Default neoznačené.

---

## 4. Pracovný postup nového Create Project (krok-za-krokom)

```
1. Direktor klikne "Create new project" (NEX Studio FE)
       ↓
2. Direktor vyplní formulár:
   - Slug: <slug>
   - Project name: <full name>
   - Description: <krátky opis>
   - [✓] Enable Koordinátor agent (default, opt-out možný)
   - [ ] Enable CI/CD (default off, K-005 opt-in)
   - [ ] Full smoke test (default off, K-004 opt-in)
       ↓
3. Backend validuje (slug uniqueness, GitHub repo neexistuje, atď.)
       ↓
4. Backend spúšťa scaffold skript scripts/create-project.sh <slug>
       ↓
5. Scaffold základná štruktúra:
   /opt/projects/<slug>/
     backend/
     frontend/
     docs/
     .claude/agents/{designer,implementer,auditor}/  (kópie templates)
     .gitignore
     README.md
     CLAUDE.md  (hlavný CLAUDE.md z templates/main-claude-template.md)
       ↓
6. git init + initial commit
       ↓
7. gh repo create rauschiccsk/<slug> --private --description "..."
       ↓
8. git remote add origin
       ↓
9. git push -u origin main
       ↓
10. K-001 Post-scaffold verification → STOP ak FAIL
       ↓
11. K-003 Koordinátor agent setup (copy templates + path substitution +
    docs/dedo-inbox/ + session-logs/coordinator/ + state file)
       ↓
12. (Trap K-002 Rollback registrovaný od kroku 6 — aktívny pre kroky 6-13)
       ↓
13. K-004 Buildable smoke test → STOP ak FAIL
       ↓
14. K-005 Voliteľná CI/CD opt-in (ak Direktor zaškrtol)
       ↓
15. Final report Direktorovi cez NEX Studio FE:
    Project <slug> created successfully:
    - GitHub repo: https://github.com/rauschiccsk/<slug>
    - Local path: /opt/projects/<slug>/
    - Initial commit: <hash> pushed to origin
    - Koordinátor agent: ready (charter + settings)
    - Inbox Deda: empty
    - Buildable smoke test: PASS
    - CI/CD workflow: <enabled | not configured>

    Next step: Upload customer-requirements.md → spustite AG Designer.
```

---

## 5. Error handling + rollback

### Failure modes a riešenia

| Krok | Možný failure | Riešenie |
|---|---|---|
| 5 Scaffold | Disk space, permissions | STOP, hlásiť Direktorovi (pred git init — žiadny rollback potrebný) |
| 6 git init | (zriedkavé) | STOP, hlásiť |
| 7 gh repo create | GitHub auth, slug konflikt, network | STOP, hlásiť, manual fix Direktorom |
| 8 git remote add | (zriedkavé — nesprávny URL) | Retry 1x s explicit URL |
| 9 git push | Network, GitHub auth, branch protection | K-002 Rollback aktivuje: retry 1x → fail → cleanup |
| 10 K-001 verify | git ls-remote zlyhal | K-002 Rollback |
| 11 K-003 Koordinátor setup | Template súbor chýba | STOP, hlásiť (NEX Studio v0.2.0 deployment bug) |
| 13 K-004 smoke test | docker compose build / up zlyhal | STOP, log uložený, Direktor decision (retry vs investigate vs rollback) |
| 14 K-005 CI/CD | GitHub Actions API error | WARN log, neabortovať (CI/CD je voliteľné) |

### Rollback scope

**K-002 trap pokrýva kroky 6-13** (od git init po K-004 smoke test). Po úspešnom kroku 14 (CI/CD opt-in) projekt je v stabilnom stave — rollback by zničil legitímnu prácu.

**Manual rollback** (pre Direktora) pre už-úspešné Create Project:
```bash
nex-studio delete-project <slug>  # interactive confirm
```

Tento je out-of-scope F-004 (existing functionality alebo voliteľný F-007+).

---

## 6. Šablóny (template súbory)

V `nex-studio/templates/` musia existovať pred F-004 implementáciou:

| Súbor | Účel | Súvislosť |
|---|---|---|
| `coordinator-charter.md` | Koordinátor agent charter | F-001 (HOTOVÉ ako spec v F-001-coordinator-charter.md) |
| `coordinator-settings.json` | Koordinátor permissions | F-001 (HOTOVÉ ako spec v F-001-coordinator-settings.json) |
| `designer-charter.md` | Existujúci Designer charter | F-006 (spätné prispôsobenie) |
| `implementer-charter.md` | Existujúci Implementer charter | HOTOVÉ 2026-05-21 (commit `934fd0b`) |
| `auditor-charter.md` | Existujúci Auditor charter | F-006 (spätné prispôsobenie) |
| `main-claude-template.md` | Hlavný CLAUDE.md template pre projekty | Existujúci (476 LOC pre NEX Studio, treba zovšeobecniť) |
| `github-actions-workflow.yml` | CI/CD workflow template (Lint + Test + Build) | F-004 K-005 |
| `gitignore-template` | Štandardný .gitignore pre Python + JS projekty | Existujúci alebo nový |
| `readme-template.md` | Štartovný README s placeholder polmi (project name, slug, popis) | Existujúci alebo nový |
| `docker-compose-template.yml` | Dev docker-compose template | Existujúci |
| `dockerfile-backend-template` | Backend Dockerfile s SHELL set -e (per Implementer §9.1) | Implementer charter §9.1 (HOTOVÉ) |
| `dockerfile-frontend-template` | Frontend Dockerfile s docs/ access (per CR-018 lessons) | F-006 (Implementer charter rozšírenie alebo nový) |

Implementer pri F-004 implementácii overí všetky templates existujú alebo ich vytvorí.

---

## 7. Acceptance criteria

| # | Kritérium | Verifikácia |
|---|---|---|
| 1 | Nový projekt po Create Project je plne git-connected | `git remote -v` ukáže origin URL + `git ls-remote origin HEAD` PASS |
| 2 | Buildable smoke test prešiel pri vzniku | `docker compose build` exit 0 počas Create Project |
| 3 | Koordinátor agent súbory existujú | `test -f <projekt>/.claude/agents/coordinator/CLAUDE.md` + `test -f .../settings.json` |
| 4 | Inbox Deda priečinky vytvorené | `test -d <projekt>/docs/dedo-inbox/` + `test -d .../processed/` |
| 5 | Žiadny silent failure medzi krokmi scaffold-u | Pri zlyhaní v ktoromkoľvek kroku STOP + report Direktorovi |
| 6 | Rollback funguje pri partial failure | Manual test: simulate git push failure → K-002 cleanup → repo state je clean (možno re-run) |
| 7 | Idempotent re-run po rollback | Po rollback `create-project <slug>` znova prejde bez konfliktu |
| 8 | CI/CD opt-in funguje | Pri zaškrtnutí workflow `ci.yml` committed + pushed + registered v GitHub Actions |
| 9 | Path substitution v Koordinátor settings.json | `grep <PROJECT_ROOT>` v `<projekt>/.claude/agents/coordinator/settings.json` vráti 0 (všetky placeholders nahradené) |
| 10 | NEX Inbox v0.1.0 by mal pri rebuild-e cez nový Create Project pass-núť | Validation: ak by sme znovu vytvorili NEX Inbox cez F-004, P0-RG1 by sa nikdy nestal |

---

## 8. Otvorené otázky pre Sub-round 4

| # | Otázka | Možnosti |
|---|---|---|
| **O-1** | Implementácia create-project.sh — bash skript alebo Python CLI s rich UI? | A) Bash (default, jednoduchšie); B) Python + rich library (lepšie UX, validation); C) NEX Studio backend endpoint volá oboje |
| **O-2** | `main-claude-template.md` — generic template alebo per-project-type? | A) Generic (default); B) Per-type (backend-only, full-stack, library, atď.) — komplexnejšie ale lepšie tailored |
| **O-3** | Branch protection rules pri `gh repo create` — automatic alebo opt-in? | A) Automatic (require PR, no force push) — bezpečné default; B) Opt-in — flexibility pre interne projekty |
| **O-4** | Rollback policy pre už-úspešné projekty — nex-studio delete-project príkaz? | A) Out-of-scope F-004 (separate F-007 alebo manual); B) Implementovať v F-004 ako súčasť |

---

## 9. Krížové odkazy

| Dokument | Súvislosť |
|---|---|
| `customer-requirements.md` §2 (Fáza 1) + §6.1 (NEX Studio improvements) | High-level Create Project zlepšenia |
| `customer-dialogue.md` §1.1 (GitHub repo prázdny diskusia) | WHY za P0-RG1 finding |
| `development-spec.md` §3.4 F-004 (5 komponentov) | High-level dizajn |
| `docs/findings/2026-05-21-release-verification-gaps.md` Finding 1 | Detail root cause |
| `F-001-coordinator-charter.md` | K-003 závisí — template musí existovať |
| `F-001-coordinator-settings.json` | K-003 path substitution |
| `F-002-inbox-deda.md` | K-003 vytvára docs/dedo-inbox/ štruktúru |

---

**Koniec dokumentu — F-004 Create Project vylepšenia.**
