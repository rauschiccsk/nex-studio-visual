# F-005 Audítorský smoke test

**Feature:** F-005
**Verzia:** NEX Studio v0.2.0
**Stav:** Návrh — Brána C (per-feature spec)
**Závislosti:** F-006 (Auditor charter update — Activity X integrácia)
**Rieši:** P0-RG5 z `docs/findings/2026-05-21-release-verification-gaps.md` (Finding 2)

---

## 1. Účel a kontext

F-005 zavádza **buildable + bootable verification ako mandatórnu Activity X** v Auditor charter-i pre každý audit cyklus (Gate / Re-Gate / Re-Re-Gate). Rieši **P0 systémový gap** odhalený v NEX Inbox v0.1.0 sprinte: napriek 3 audit cyklom PASS s 549 BE + 60 FE testov GREEN + Tibor PASS 6/6 byte-equal, stack reálne **nevedel nabehnúť** kvôli 5 P0 Dockerfile/env bugom. Release verdict bol false-positive.

Root cause môjho NEX Inbox audit zlyhania: Activity Auditor self-PIV SP-02 ("docker compose build BE+FE obrazy") + SP-07 ("docker compose up celý stack smoke") boli klasifikované ako "MÁGERSTAV pre-deploy gates" — out-of-scope release audit. **To bola moja chyba** — buildable + bootable je **release criterion**, nie pre-deploy concern.

Cieľ: žiadny ďalší audit verdict PASS bez `docker compose build` + `docker compose up` + `/health` smoke test PASS.

---

## 2. 4 komponenty

| # | Komponent | Popis |
|---|---|---|
| **K-001** | Activity X mandatory v Auditor charter | Nová sekcia v `templates/auditor-charter.md` + per-projekt kópie |
| **K-002** | Rámcový smoke test set | Štandardizovaný set príkazov ktoré Auditor spúšťa pri každom audit cykle |
| **K-003** | Verdict criteria update | Audit verdict PASS **vyžaduje** Activity X PASS — nemôže sa odkladať |
| **K-004** | CI/CD brána pre release tagy | GitHub Actions workflow ktorý odmietne push tagu `v*.*.*` pri smoke test FAIL |

---

## 3. K-001 Activity X mandatory v Auditor charter

### Lokácia

`/opt/projects/nex-studio/templates/auditor-charter.md` (autoritatívny zdroj) + `<projekt>/.claude/agents/auditor/CLAUDE.md` (kópia po sync).

### Pridaná sekcia

Charter dostane novú sekciu (číslo podľa existujúcej štruktúry — pravdepodobne §6.X alebo §7.X, finalize pri F-006 implementácii):

```markdown
## §X. Activity X — Buildable + Bootable Verification (MANDATORY)

> Toto je MANDATORY aktivita v každom audit cykle (Gate / Re-Gate /
> Re-Re-Gate). Verdict PASS bez Activity X PASS je INVALID a porušuje
> NEX Studio v0.2.0 audit charter.

### §X.1 Účel

Verifikovať že stack je **reálne spustiteľný** z aktuálneho kódu:
- `docker compose build` (BE + FE images) prejde exit 0
- `docker compose up` produkuje healthy kontajnery
- `/health` endpoint vracia non-empty response

Bez Activity X audit verdict PASS je **false-positive** — pattern z NEX
Inbox v0.1.0 sprintu kde napriek 3 audit cyklom PASS stack reálne nevedel
nabehnúť kvôli 5 P0 Dockerfile/env bugom.

### §X.2 Kedy

**Pri každom audit cykle:**
- Pôvodný Gate G (prvý audit verzie)
- Re-Gate G (po Designer/Implementer fix-bundle)
- Re-Re-Gate G (po druhom fix-bundle)
- Atď.

**Pred udelením verdict PASS.** Bez Activity X PASS audit ZOSTÁVA v
in-progress alebo FAIL.

### §X.3 Postup

Activity X má **5 sub-aktivít** (per `F-005 §4 Rámcový smoke test set`):

1. **X.1 Backend build** — `docker compose build backend` (alebo
   ekvivalent) musí prejsť exit 0
2. **X.2 Frontend build** — `docker compose build frontend` musí prejsť
3. **X.3 Database migrations** — alembic upgrade head (alebo equivalent)
   na čistú DB
4. **X.4 Full stack up + healthy** — `docker compose up -d` → všetky
   kontajnery `Up (healthy)` do 60s
5. **X.5 Health endpoint** — `curl -sf http://localhost:<port>/health`
   vracia non-empty JSON (degraded acceptable pre bootstrap mode)

Detail spúšťania v `F-005-audit-smoke-test.md` §4.

### §X.4 Acceptance

Activity X PASS = všetkých 5 sub-aktivít PASS.

Activity X FAIL = ľubovoľná sub-aktivita zlyhala → audit verdict
**NEMÔŽE** byť PASS. Auditor reportuje konkrétny bug ako P0 release-gate.

### §X.5 Anti-pattern — defer ako "pre-deploy gate"

Klasifikovať Activity X ako "MÁGERSTAV pre-deploy gate" alebo iný
projekt-specific pre-deploy concern je **zakázané**. Buildable +
bootable je **release criterion**, nie pre-deploy concern.

Tento anti-pattern odhalený v NEX Inbox v0.1.0 sprinte (moja audit
chyba — Auditor self-PIV SP-02 + SP-07 boli defer-nuté). Per memory
`feedback_full_re_gate_after_fix_bundle` exception clause: Activity X
nikdy NIE súčasť exception scope.

### §X.6 Activity X v Re-Gate audit cykloch

Per memory `feedback_full_re_gate_after_fix_bundle` — Re-Gate musí
byť **full audit** vrátane Activity X. Žiadny "selektívny re-check"
ktorý preskakuje Activity X.

Plus exception clause pre minor drift fix (1-line spec docstring,
žiadny code change) — Activity X **NIE potrebná** pre tieto drobné
fixy (lebo nepôvodzajú build/run zmeny).
```

---

## 4. K-002 Rámcový smoke test set

### Štandardizovaný postup (5 sub-aktivít)

Auditor pri Activity X spúšťa **5 sub-aktivít** v presnom poradí:

#### Sub-aktivita X.1 Backend build

```bash
cd /opt/projects/<slug>
docker compose build backend 2>&1 | tee /tmp/audit-<slug>-x1-backend-build.log
EXIT=$?
if [ $EXIT -ne 0 ]; then
    echo "FAIL X.1: docker compose build backend exit $EXIT"
    echo "Log v /tmp/audit-<slug>-x1-backend-build.log"
    exit 1
fi
echo "PASS X.1"
```

**Verifikácia že build skutočne vyrobil image (nie iba cached miss):**

```bash
# Overiť že image bol vyrobený (alebo refresh-nutý)
docker images | grep -q "<slug>-backend" || {
    echo "FAIL X.1: backend image neexistuje napriek úspešnému build-u"
    exit 1
}
```

**Verifikácia že `.venv` (alebo runtime artifacts) existuje v image:**

```bash
# Spustiť temporary container a overiť binary existence
docker run --rm --entrypoint="" <slug>-backend test -x /app/.venv/bin/uvicorn || {
    echo "FAIL X.1: uvicorn binary chýba v backend image (silent install fail?)"
    exit 1
}
echo "PASS X.1: backend image obsahuje runtime binárky"
```

Toto explicit ošetruje **P0-RG3 saxonche silent install fail** ktorý sa stal v NEX Inbox v0.1.0.

#### Sub-aktivita X.2 Frontend build

```bash
docker compose build frontend 2>&1 | tee /tmp/audit-<slug>-x2-frontend-build.log
EXIT=$?
if [ $EXIT -ne 0 ]; then
    echo "FAIL X.2: docker compose build frontend exit $EXIT"
    exit 1
fi

# Verify image existuje + obsahuje built assets
docker run --rm --entrypoint="" <slug>-frontend ls /usr/share/nginx/html/index.html || {
    echo "FAIL X.2: frontend build artifacts chýbajú v image"
    exit 1
}
echo "PASS X.2"
```

#### Sub-aktivita X.3 Database migrations

```bash
# Spustí len DB
docker compose up -d db
sleep 10

# Wait for DB healthy
for i in {1..30}; do
    if docker compose exec -T db pg_isready -U postgres; then
        break
    fi
    sleep 2
done

# Alembic upgrade head (na čistej DB)
docker compose exec -T backend poetry run alembic upgrade head || {
    echo "FAIL X.3: alembic upgrade head zlyhal"
    docker compose logs db backend
    exit 1
}
echo "PASS X.3"
```

#### Sub-aktivita X.4 Full stack up + healthy

```bash
docker compose up -d

# Wait pre healthy status pre všetky kontajnery
TIMEOUT=120  # 2 minúty
ELAPSED=0
while [ $ELAPSED -lt $TIMEOUT ]; do
    UNHEALTHY=$(docker ps --filter "name=<slug>" --filter "health=unhealthy" --format "{{.Names}}" | wc -l)
    STARTING=$(docker ps --filter "name=<slug>" --filter "health=starting" --format "{{.Names}}" | wc -l)

    if [ $UNHEALTHY -eq 0 ] && [ $STARTING -eq 0 ]; then
        echo "PASS X.4: všetky kontajnery healthy do ${ELAPSED}s"
        break
    fi
    sleep 5
    ELAPSED=$((ELAPSED + 5))
done

if [ $ELAPSED -ge $TIMEOUT ]; then
    echo "FAIL X.4: kontajnery nedosiahli healthy do 2 minút"
    docker ps --filter "name=<slug>"
    docker compose logs
    exit 1
fi
```

#### Sub-aktivita X.5 Health endpoint

```bash
# Discover port z docker-compose
PORT=$(docker compose port backend 8000 | cut -d: -f2)

# Curl /health s timeout
RESPONSE=$(curl -sf -m 10 "http://localhost:${PORT}/health")
EXIT=$?

if [ $EXIT -ne 0 ]; then
    echo "FAIL X.5: /health endpoint neprístupný"
    exit 1
fi

if [ -z "$RESPONSE" ]; then
    echo "FAIL X.5: /health vrátil prázdnu response"
    exit 1
fi

echo "PASS X.5: /health response: $RESPONSE"
```

**Acceptable response types:**
- Plný response s status "ok" (production-ready stack)
- Degraded response (napr. status "degraded" + dôvod "IMAP credentials missing — bootstrap mode") — acceptable pre bootstrap mode
- **NIE acceptable:** prázdna response, HTTP error status (4xx/5xx), connection refused, timeout

### Cleanup po Activity X

```bash
# Cleanup containers po smoke test (audit prebehol)
docker compose down -v

# Activity X je verification, nie persistent deployment
```

### Aggregate output

Po dokončení Activity X Auditor reportuje v audit report-e:

```markdown
## Activity X — Buildable + Bootable Verification

| Sub-aktivita | Verdict | Detail |
|---|---|---|
| X.1 Backend build | PASS / FAIL | <commit message + binary verification> |
| X.2 Frontend build | PASS / FAIL | <build assets verification> |
| X.3 Database migrations | PASS / FAIL | <alembic version> |
| X.4 Full stack up + healthy | PASS / FAIL | <time to healthy + container statuses> |
| X.5 Health endpoint | PASS / FAIL | <response body> |

**Verdict Activity X:** PASS / FAIL
```

---

## 5. K-003 Verdict criteria update

### Pravidlo

Audit verdict PASS **VYŽADUJE** Activity X PASS. Bez Activity X PASS:
- Audit verdict NEMÔŽE byť PASS
- Audit verdict je buď **in-progress** (Activity X ešte nebol spustený) alebo **FAIL** (Activity X zlyhal)

### Defer scope explicit zakázaný

**Anti-pattern z NEX Inbox v0.1.0:** Auditor defer-uje `docker compose build` ako "MÁGERSTAV pre-deploy gate" → Direktor schvaľuje "out-of-scope release audit" → release verdict PASS bez buildable verification.

**Pravidlo v Auditor charter:** Activity X NIKDY nesmie byť klasifikované ako "pre-deploy gate", "MÁGERSTAV-specific concern", "customer IT responsibility" ani iný defer kategória. Buildable + bootable verification je **release criterion**.

### Vzťah s memory `full-re-gate-after-fix-bundle` exception clause

Exception clause pre minor drift fix (1-line spec docstring, žiadny code change) — Activity X **nie potrebná** pre tieto drobné fixy lebo nepôvodzajú build/run zmeny.

Ale: každý fix ktorý dotýka:
- Dockerfile
- docker-compose.yml
- pyproject.toml / package.json
- backend/ / frontend/ source code
- alembic migration

→ **Activity X mandatórne** pre validujúci audit.

---

## 6. K-004 CI/CD brána pre release tagy

### Účel

Pri push git tagu `v*.*.*` (release tag) GitHub Actions workflow spustí Activity X smoke test set. Pri zlyhaní → reject push tagu (release nemôže prejsť).

### Implementácia

GitHub Actions workflow `templates/release-gate-workflow.yml` (template ktorý sa kopíruje do projektov pri Create Project per F-004 K-005):

```yaml
name: Release Gate (Activity X)

on:
  push:
    tags:
      - 'v*.*.*'

jobs:
  smoke-test:
    runs-on: ubuntu-latest
    timeout-minutes: 30

    steps:
      - uses: actions/checkout@v4

      - name: Setup Docker
        uses: docker/setup-buildx-action@v3

      - name: Setup Python + Poetry
        run: |
          curl -sSL https://install.python-poetry.org | python3 -
          echo "$HOME/.local/bin" >> $GITHUB_PATH

      - name: Activity X.1 Backend build
        run: |
          docker compose build backend 2>&1 | tee build-backend.log
          docker run --rm --entrypoint="" ${{ github.repository }}-backend \
            test -x /app/.venv/bin/uvicorn

      - name: Activity X.2 Frontend build
        run: |
          docker compose build frontend 2>&1 | tee build-frontend.log
          docker run --rm --entrypoint="" ${{ github.repository }}-frontend \
            ls /usr/share/nginx/html/index.html

      - name: Activity X.3 Database migrations
        run: |
          docker compose up -d db
          sleep 10
          poetry --directory backend install --only main --no-root
          poetry --directory backend run alembic upgrade head

      - name: Activity X.4 Full stack healthy
        run: |
          docker compose up -d
          for i in {1..30}; do
            UNHEALTHY=$(docker ps --filter "health=unhealthy" -q | wc -l)
            STARTING=$(docker ps --filter "health=starting" -q | wc -l)
            if [ $UNHEALTHY -eq 0 ] && [ $STARTING -eq 0 ]; then
              echo "PASS X.4"; break
            fi
            sleep 5
          done

      - name: Activity X.5 Health endpoint
        run: |
          PORT=$(docker compose port backend 8000 | cut -d: -f2)
          curl -sf -m 10 "http://localhost:${PORT}/health"

      - name: Cleanup
        if: always()
        run: docker compose down -v

      - name: Upload logs on failure
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: activity-x-logs
          path: |
            build-backend.log
            build-frontend.log
```

### Vzťah s F-004 K-005

F-004 K-005 (voliteľná CI/CD wire-up pri Create Project) môže pri opt-in **automaticky pridať** aj `release-gate-workflow.yml` k základnému `ci.yml`.

Direktor preferencia (default):
- Per-project — `release-gate-workflow.yml` zapnutý pre regulated-ledger projekty (NEX Inbox), voliteľný pre internal NEX Studio sám seba
- Per-project opt-in cez `--release-gate` flag pri create-project

### Branch protection rule (voliteľné)

GitHub branch protection na `main`:
- Pri tag push z forked branch → require workflow PASS pred merge
- Pri direct tag push (Direktor admin) → workflow beží, pri zlyhaní notification (ale tag nie je revertnutý)

Toto je open question pre Sub-round 4 (§8).

---

## 7. Acceptance criteria

| # | Kritérium | Verifikácia |
|---|---|---|
| 1 | Žiadny audit verdict PASS bez Activity X PASS | Auditor charter §X explicit pravidlo + audit report formát vyžaduje Activity X sekciu |
| 2 | Activity X má všetkých 5 sub-aktivít (X.1-X.5) | Auditor charter §X.3 list |
| 3 | Backend binary verification (uvicorn existuje v image) | X.1 explicit `docker run ... test -x .venv/bin/uvicorn` |
| 4 | Frontend assets verification | X.2 explicit `ls /usr/share/nginx/html/index.html` |
| 5 | Smoke test set je reprodukovateľný | Spustenie X.1-X.5 dvakrát po sebe = rovnaké výsledky |
| 6 | CI/CD brána odmietne release tag pri smoke fail | Test: vytvoriť testovací bug v Dockerfile → push tagu → workflow FAIL → tag nie deployable |
| 7 | NEX Inbox v0.1.0 by zlyhal na Activity X | Validation: ak by sme znovu auditovali NEX Inbox v0.1.0 s F-005, P0-RG1 + P0-RG2 + P0-RG3 by sa zachytili pri X.1 |
| 8 | Memory `full-re-gate-after-fix-bundle` exception clause respektovaná | Activity X NIE súčasť exception scope (per §3 Auditor charter sekcia X.5) |
| 9 | Audit report obsahuje Activity X sekciu so štruktúrovaným verdict | Manual review audit reportov post-F-005 |
| 10 | Defer ako "pre-deploy gate" zakázané v charter-i | §X.5 charter explicit zákaz |

---

## 8. Otvorené otázky pre Sub-round 4

| # | Otázka | Možnosti |
|---|---|---|
| **O-1** | GitHub branch protection rule pre release tagy — automatic pri Create Project alebo opt-in? | A) Automatic pre všetky projekty (require workflow PASS pred merge tagu); B) Opt-in cez `--branch-protection` flag pri create-project; C) Per-project type (regulated-ledger automatic, internal optional) |
| **O-2** | Activity X timeout — 120s pre healthy, 30 min total workflow — vyhovuje? | A) Keep defaults (NEX Inbox baseline); B) Per-project konfigurovateľné v `auditor-charter.md` per-project section; C) Adaptive (longer pre projekty s complex stack — Ollama, atď.) |
| **O-3** | Local Auditor smoke test cez Bash skript alebo Auditor agent sub-process? | A) Bash skript (jednoduchšie, fail-fast); B) Sub-agent (rich error analysis, lepší recovery); C) Hybrid — bash pre X.1-X.5 + sub-agent pre interpretation a audit report writing |
| **O-4** | Activity X failure → automatic Inbox Deda flag? | A) Áno (recurring failures → systematic NEX Studio gap); B) Nie (per-failure Direktor decision); C) Threshold-based (N+ Activity X failures za týždeň → automatic flag) |

---

## 9. Krížové odkazy

| Dokument | Súvislosť |
|---|---|
| `customer-requirements.md` §2 Fáza 6 + §6.2 audítorský smoke test | High-level mandatory verification |
| `customer-dialogue.md` §1.6 + §1.7 (False PASS diskusia + findings záznam) | WHY za P0-RG5 finding |
| `development-spec.md` §3.5 F-005 (4 komponenty) | High-level dizajn |
| `docs/findings/2026-05-21-release-verification-gaps.md` Finding 2 | Root cause + 5 P0 release-gate bugov detail |
| `F-006-agent-charter-updates.md` | Auditor charter update integrácia §X sekcia |
| `F-001-coordinator-charter.md` §10 Anti-patterns (False PASS relay) | Koordinátor mandatórne verifikuje Activity X pri DONE relay |
| `.claude/agents/implementer/CLAUDE.md` §9.2 Smoke test pred DONE | Implementer self-smoke pred audit handoff (parallel verification) |

---

**Koniec dokumentu — F-005 Audítorský smoke test.**
