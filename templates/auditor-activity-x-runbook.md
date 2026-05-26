# Auditor Activity X — Buildable + Bootable Verification Runbook

> **Per F-005 §4 (CR-030 K-002 deliverable, NEX Studio v0.2.0).**
> Bash snippet set ktorý Auditor spúšťa pri každom audit cykle (Gate /
> Re-Gate / Re-Re-Gate) ako súčasť Activity X (MANDATORY per Auditor
> charter §X).
>
> **Bez Activity X PASS audit verdict NEMÔŽE byť PASS** (per §12 K-003
> + §X.4 acceptance).

---

## Štandardizovaný postup (5 sub-aktivít)

Auditor pri Activity X spúšťa **5 sub-aktivít** v presnom poradí. Per
audit cyklus jeden run pre celý set; pri zlyhaní ľubovoľnej sub-aktivity
verdict Activity X = FAIL.

### Sub-aktivita X.1 Backend build

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

### Sub-aktivita X.2 Frontend build

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

### Sub-aktivita X.3 Database migrations

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

### Sub-aktivita X.4 Full stack up + healthy

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

### Sub-aktivita X.5 Health endpoint

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

---

## Cleanup po Activity X

```bash
# Cleanup containers po smoke test (audit prebehol)
docker compose down -v

# Activity X je verification, nie persistent deployment
```

---

## Aggregate output

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

## Per-charter references

- **Auditor charter `CLAUDE_AUDITOR.md §X`** — Activity X mandatory rule
  + 5 sub-aktivít abstract list. Tento runbook poskytuje bash detail.
- **Auditor charter §12 K-003** — verdict criteria "PASS VYŽADUJE
  Activity X PASS"
- **Spec doc** — `docs/specs/versions/v0.2.0/spec/F-005-audit-smoke-test.md §4`
  je ground-truth (tento template je verbatim copy + cross-project deliverable
  cez F-004 K-005 auto-copy pattern, analogicky `coordinator-charter.md`).
