# NEX Studio v0.2.0 — CHANGES

> Chronologický audit záznam spec balíka v0.2.0.
> Newest first.

---

## 2026-05-26 — CR-025 Snapshot/teardown DB credentials propagation (Bug #8 fix)

### Kontext

Po CR-024 fix smoke retry pokračoval cez Krok 7 (`uat-status.py` PASS) na Krok 8 (`uat-snapshot.py dev --reason post-cr-024`) → **HTTP-equivalent FAIL**:

```
ERROR: pg_dump failed: Command '['docker', 'exec', 'uat-dev-postgres',
'pg_dump', '-U', 'postgres']' returned non-zero exit status 1.
```

### Root cause

`uat-snapshot.py` a `uat-teardown.py` volajú `pg_dump -U postgres` s **hardcoded** user "postgres". Ale postgres container beží s `POSTGRES_USER=nexstudio` (detected z source compose per CR-022). pg_dump zlyháva s `role "postgres" does not exist`.

### Spec design root cause (Dedo acknowledgment)

CR-022 detect_db_credentials zaviedol per-project POSTGRES_USER, ale **aplikoval ho len do uat-deploy** (template render + .env write). `uat-snapshot.py` a `uat-teardown.py` neboli v scope CR-022 sub-agent audit (focus bol `uat-deploy.py` + template) → hardcoded "postgres" zostal. Tretí gap **rovnakého patternu** ako CR-022 (DB creds) a CR-024 (frontend port): per-project value hardcoded v generic default.

### Spec amendment

- **F-003 §11** — pridaný nový row "Snapshot/teardown DB credentials propagation (CR-025)" requiring detected POSTGRES_USER + POSTGRES_DB cez `/opt/uat/<slug>/.env` (single source of truth).

### Implementer impl

- `_uat_lib.read_uat_env(slug: str) → dict[str, str]` — nový helper, parse `/opt/uat/<slug>/.env` do dict (single source of truth, no DB creds duplication)
- `uat-snapshot.py.snapshot()`: read `POSTGRES_USER` + `POSTGRES_DB` z `/opt/uat/<slug>/.env`, call `pg_dump -U {user} -d {db}`
- `uat-teardown.py` final-snapshot path: same fix

### Tests

- `test_read_uat_env_parses_basic` — basic key=value parsing
- `test_read_uat_env_ignores_comments_and_blanks` — # comments + blank lines skipped
- `test_read_uat_env_missing_file_returns_empty` — graceful degradation
- `test_uat_snapshot_uses_detected_postgres_user` — integration (mock docker_exec, assert -U detected_user)
- `test_uat_teardown_final_snapshot_uses_detected_user` — same for teardown

### Acceptance

- Smoke Krok 8 (re-run): snapshot file created v `/opt/uat/dev/snapshots/`, non-empty
- Smoke Krok 9 (teardown): final snapshot PASS + clean teardown
- Plus full backend test suite GREEN

---

## 2026-05-26 — CR-024 Frontend container port auto-detection (Bug #7 fix)

### Kontext

Po CR-023 fix (DB password sharing) smoke retry pokračoval Krok 5 (NGINX activation PASS) + Krok 6 (URL test). Backend `/health` cez nginx → HTTP 200 ✓. Frontend `/` cez nginx → **HTTP 502** (Bug #7).

### Root cause

- **NEX Studio `frontend/Dockerfile`**: `EXPOSE 9177` + nginx `listen 9177` (production convention)
- **UAT compose template `templates/uat/docker-compose.yml.j2`**: ports mapping hardcoded `"127.0.0.1:{{ UAT_PORT }}:80"`
- Docker-proxy forwards host:UAT_PORT → container:80, ale container nginx listens na 9177 → connection reset

CR-022 §C-5 (frontend build context + args) detegoval kontext/Dockerfile/args, ale **NIE container port**. F-003 generic template predpokladal port 80 (Docker default), ale nex-studio listens na 9177. nex-inbox má `"127.0.0.1:5173:80"` (rôzny formát) → 80 OK, ale obecná detection chýba.

### Spec design root cause (Dedo acknowledgment)

CR-022 §C-5 row addresoval frontend build configuration ale **vynechal port mapping**. Rovnaký pattern ako C-2 (DB creds) — template hardcoded jeden konvenciu pre vlastnosť, ktorá je per-project variable. Comprehensive design review (CR-022 sub-agents) **mal** zachytiť aj toto, ale fokus bol na build context + args, port mapping zostal "default = 80" predpoklad.

### Spec amendment

- **F-003 §11** — pridaný nový row "Frontend container port auto-detection (CR-024)" requiring detection z `services.frontend.ports` source compose.

### Implementer impl

- `_uat_lib.detect_frontend_config(...)` rozšírený o `container_port: int` (default 80) — parse `services.frontend.ports[0]`:
  - Support string formats `"HOST:CONTAINER"`, `"IP:HOST:CONTAINER"`, optional `/protocol` suffix
  - Support dict format (Docker compose extended): `{"target": int, "published": int, ...}` → take `target`
  - Take rightmost numeric segment per Docker spec
- `templates/uat/docker-compose.yml.j2`: ports mapping `"127.0.0.1:{{ UAT_PORT }}:{{ FRONTEND_CONTAINER_PORT }}"` placeholder
- `uat-deploy.py`: thread `container_port` z detected frontend_cfg → `FRONTEND_CONTAINER_PORT` template var (fallback 80)

### Tests

- `test_detect_frontend_config_extracts_container_port_short_form` — nex-studio "9177:9177" → 9177
- `test_detect_frontend_config_extracts_container_port_with_ip_prefix` — nex-inbox "127.0.0.1:5173:80" → 80
- `test_detect_frontend_config_defaults_container_port_to_80` — no ports → 80
- `test_detect_frontend_config_supports_dict_target` — `{target: 8080, published: 80}` → 8080

### Acceptance

- Smoke Krok 6 (re-run): frontend `/` cez NGINX → HTTP 200 (Vite bundle HTML)
- Plus full backend test suite GREEN

---

## 2026-05-26 — CR-023 Shared synthetic DB password (Bug #5 fix)

### Kontext

Real smoke test Krok 4 spustený 2026-05-26 (po CR-022 Implementer round 3 DONE) zachytil **Bug #5**: backend container exitne s kódom 3 pri startup s `password authentication failed for user "nexstudio"`. Postgres healthcheck OK, backend build OK, ale prvý connect cez SQLAlchemy fail.

### Root cause

`_uat_lib._rewrite_db_connection_var()` volá `secrets.token_hex(32)` **dvakrát nezávislo**:

```python
if key in {"DB_PASSWORD", "POSTGRES_PASSWORD"}:
    return secrets.token_hex(32)       # synth A → POSTGRES_PASSWORD .env line
if key == "DATABASE_URL":
    password = secrets.token_hex(32)   # synth B (DIFFERENT) → embedded v DATABASE_URL
    return f"postgresql://{user}:{password}@..."
```

Plus `uat-deploy.py:generate_uat_env()` generuje **tretí** `postgres_password = secrets.token_hex(32)` (synth C) pre top-level `POSTGRES_PASSWORD={postgres_password}` .env line (línia 77).

Výsledok: postgres container init použije synth C, backend's `DATABASE_URL` má embedded synth B → auth FAIL. Komentár v kóde (`# Use synthetic password (matches what _PASSWORD suffix would generate).`) prezrádza zámer, ale impl nezdiela secret state medzi DB-credential consumers.

### Spec design root cause (Dedo acknowledgment)

CR-022 spec §11 row "Synthetic credentials pre per-projekt env vars" **neobsahoval explicit požiadavku** že synthetic DB password musí byť **jedna hodnota zdielaná** medzi všetkými DB credential consumers (POSTGRES_PASSWORD, DB_PASSWORD, DATABASE_URL embedded password). Implementer impl rozumne predpokladal "_PASSWORD suffix = random hex" ale dva rôzne calls produkujú dve rôzne hodnoty.

### Spec amendment

- **F-003 §11** — pridaný nový row "Shared synthetic DB password (CR-023)" explicitly požaduje single-source-of-truth synthetic DB password per UAT env build, zdielaný medzi všetkými DB credential consumers v rovnakej env.

### Implementer impl

- `_uat_lib.detect_backend_env_vars(source_project_path, *, synthetic_db_password: str | None = None)` — nový optional kwarg. Ak `None`, precomputes raz pred loopom (existing tests neporušené).
- `_uat_lib._rewrite_db_connection_var(...)` — required `synthetic_password: str` parameter, reuse pre POSTGRES_PASSWORD/DB_PASSWORD return AND DATABASE_URL embedded password.
- `uat-deploy.py:generate_uat_env(...)` — generate `postgres_password` ONCE early, pass downstream:
  - Top-level `POSTGRES_PASSWORD={postgres_password}` .env line (postgres container init)
  - `detect_backend_env_vars(synthetic_db_password=postgres_password)` (backend connect)
  - DB_PASSWORD overlap loop override (zachované)
- Plus regression test: `assert env["POSTGRES_PASSWORD"] == extract_password_from(env["DATABASE_URL"])` (post-generate).

### Tests

- `test_detect_backend_env_vars_shared_db_password` — nový test pre nex-studio-shape compose (DATABASE_URL embedded), asserting POSTGRES_PASSWORD .env line and DATABASE_URL embedded password match.

### Acceptance

- Real smoke test Krok 4 (re-run): backend startup PASS, no auth FAIL.
- Plus full backend test suite GREEN.

---

## 2026-05-24 — CR-022 F-003 comprehensive per-projekt auto-detection (alembic + env + DB + frontend + NGINX)

### Kontext

Bug #3 (alembic strategy mismatch) zachytený manual smoke testom 2026-05-24 (NEX Studio backend self-bootstraps alembic v lifespan, poetry nie je v runtime image → `docker exec poetry run alembic upgrade head` zlyhalo). Pred reaktívnym fixom Direktor schválil **comprehensive F-003 review** (per memory `feedback_quality_first` quality-first analýza).

Sub-agent comprehensive design audit (~22 min) odhalil **14 findings** v F-003 generic template vs real-world target projekty (nex-studio + nex-inbox): 6 critical (blokujú UAT deploy), 5 medium, 3 low. Plus pending Bug #3 alembic.

**Low findings (L-1, L-2, L-3) — žiadny fix potrebný (verified clean):**

- L-1 Volume naming inconsistency (hyphens v UAT template vs underscores v nex-inbox vs single-word v nex-studio) — cosmetic, žiadne breaking, template konvencia ostáva
- L-2 `container_name: uat-<slug>-*` — slug-scoped, žiadne collision cez sluggy, status command §4.3 matches
- L-3 Missing `version:` field v compose — modernizovaný Compose ignoruje, oba source composes ho tiež vynechávajú

**Headline:** F-003 template bol "happy-path generic skeleton" ktorý nesurvives kontakt s ani jedným z dvoch target projektov. Template encodes ONE shape of project, real projects sú heterogeneous.

### Spec design root cause (Dedo acknowledgment)

Toto je **moja chyba ako spec writer** rovnaká kategória ako CR-021. Designer pre-commit verification (môj vlastný návrh do Inbox Deda) by zachytil 4 z 6 critical findings (C-1, C-2, C-4, C-5) cez file-read comparison. C-3 (alembic) + C-6 (NGINX flow) by potrebovali **extended verification** — read backend `main.py` lifespan + Dockerfile runtime stage + mental simulation user request cez NGINX.

### 6 Critical findings (zaradené do CR-022 spec amendment)

| # | Finding | Resolution |
|---|---|---|
| **C-1** | Backend env-var schema project-specific (nex-studio 12+ vars `postgresql+pg8000://`, nex-inbox 8+ vars split `DB_HOST/PORT/NAME/USER/PASSWORD`), template má 2 generic vars | `_uat_lib.detect_backend_env_vars(source)` — parse `services.backend.environment` + `.env.example` → generate synthetic UAT `.env` cez `env_file:` |
| **C-2** | Postgres user/password/DB name (template `postgres/.../<project>_uat` neplatí — nex-studio `nexstudio/nexstudio/nexstudio`, nex-inbox `nex_inbox/.../nex_inbox_dev`) | `_uat_lib.detect_db_credentials(source)` — parse `services.db.environment` → passthrough do template |
| **C-3** | Alembic strategy (Bug #3) — nex-studio self-bootstraps v lifespan + poetry chýba v runtime; nex-inbox external `poetry run alembic` | `_uat_lib.detect_alembic_strategy(source)` — grep `backend/main.py` pre `command.upgrade` patterns + read Dockerfile runtime stage. Strategy: `self-bootstrap` (skip step 8) / `external` (try `python -m alembic` → `poetry run alembic` fallback) |
| **C-4** | Frontend build context (template `<project>/frontend/`, nex-inbox potrebuje repo root pre `COPY frontend/...` paths) | `_uat_lib.detect_frontend_config(source)` — parse `services.frontend.build.{context,dockerfile,args}` |
| **C-5** | `VITE_API_BASE_URL` build arg (nex-studio default `http://localhost:9176` broken pod NGINX, nex-inbox `/api/v1` OK) | Auto-detect z `services.frontend.build.args`, propagate do template `FRONTEND_BUILD_ARGS` |
| **C-6** | NGINX vhost chýba `/api/` route na backend host port (browser pod Tailscale cannot reach backend) | NGINX template doplnený o `location /api/` proxy_pass na `BACKEND_HOST_PORT` + `/health` route |

### 3 Medium findings inline v CR-022

- **M-3 healthcheck `start_period: 90s`** — extended pre 50+ migrations (nex-studio self-bootstrap takes ~60-120s)
- **M-4 `restart: "no"`** — UAT je ephemeral (per-cycle), production-style restart inappropriate
- **M-5 explicit `networks:` block** — `uat-<slug>-net` namiesto coincidental default network

### Defer to v0.3.0+ (per Sub-round 4 Q1)

- **M-1 Custom services passthrough** (Ollama, Redis, mockup, postgres-exporter, monitoring-net) — Workaround v acceptance-checklist: mockup-dependent features explicit out of UAT scope
- **M-2 nex-studio backend volume mounts** (7 mounts pre KB, Claude CLI auth, Docker socket) — Strategic question pre v0.3.0+: môže byť NEX Studio reasonably UAT-ovaný keď bootstrap-uje projekty?

### Zmeny

**F-003-uat-environment.md:**

- §4.1 Discovery — rozšírená o 5 auto-detection patterns (alembic + env vars + DB credentials + frontend config + extended backend dockerfile per CR-021 expansion)
- §10 NGINX vhost — pridaný `location /api/` proxy + `location /health` (C-6)
- §11 Bezpečnostné aspekty — nový row "Synthetic credentials pre per-projekt env vars" (C-1)
- §13 Acceptance #1 — expanded ("Acceptance verified pre obe target projekty: nex-inbox + nex-studio full attribute matrix")
- §13.1 Inline template fixes (NEW) — M-3 healthcheck start_period 90s + M-4 restart no + M-5 explicit networks
- §14 Mimo rozsahu (NEW heading) — M-1 + M-2 defer to v0.3.0+ s workaround notes
- §15 Otvorené otázky (renumber from §14) — žiadne nové otázky

### Implementer round 3 expected work

Per Variant A (Single CR) schválený Direktorom 2026-05-24:

1. **4 nové detection helpers + 1 refactor v `scripts/_uat_lib.py`:**
   - `detect_db_credentials(source_path) → dict` (POSTGRES_USER/PASSWORD/DB z source compose db service)
   - `detect_backend_env_vars(source_path) → dict` (parse `services.backend.environment` + `.env.example`, generate synthetic UAT .env content)
   - `detect_frontend_config(source_path) → dict` (build context + dockerfile + build args vrátane VITE_API_BASE_URL)
   - `detect_alembic_strategy(source_path) → str` ("self-bootstrap" / "external" / "skip")
   - Plus refactor existujúceho CR-021 `detect_backend_config` ak vznikne shared utility duplicácia
2. **`scripts/uat-deploy.py` integration:**
   - Call všetkých 5 detection helpers v Discovery phase
   - Generate synthetic UAT .env (write to `/opt/uat/<slug>/.env`)
   - Conditional step 8: ak alembic_strategy == "self-bootstrap" → skip; ak "external" → try python -m alembic → fallback poetry run alembic
   - Plus CLI flags: `--alembic-strategy {auto|self-bootstrap|external|skip}`, `--skip-env-detection`, `--db-user`, `--db-name`
3. **`templates/uat/docker-compose.yml.j2`:**
   - Change `environment:` block na `env_file:` (pointing na `/opt/uat/<slug>/.env`)
   - DB credentials passthrough (POSTGRES_USER/PASSWORD/DB z detected)
   - Frontend `build.context` + `dockerfile` + `args` placeholders
   - Inline fixes M-3 (start_period 90s) + M-4 (restart no) + M-5 (networks)
4. **`templates/uat/nginx-uat-vhost.conf`:**
   - Add `location /api/` + `location /health` blocks (C-6)
   - Plus `BACKEND_HOST_PORT` placeholder
5. **Tests `tests/test_uat_lib.py` + `tests/test_uat_deploy.py`:**
   - Per real I/O pattern (no mocking) — fixture composes for both nex-studio + nex-inbox styles
   - Coverage: 6 critical scenarios (table-driven testy per finding) + edge cases (no source compose, partial env_file, malformed YAML)

**Estimate Implementer round 3: ~5-7 hodín reálnej práce.**

### Continuous improvement signal

**Designer charter §X.Y "Pre-commit spec verification" pravidlo expansion** (z pôvodného CR-021 návrhu rozšírené):

Sub-agent confirmoval že pravidlo by zachytilo 4 z 6 critical findings cez file-read. Pre C-3 + C-6 potrebné rozšírenie o:

1. **"Read backend entrypoint" mandatory pre UAT/deploy specs** — Designer musí read `backend/main.py` lifespan + `backend/Dockerfile` runtime stage pred finalizáciou spec ktorá touch-uje alembic/bootstrap workflow
2. **"Trace a user request" mental simulation pre NGINX/reverse-proxy specs** — Designer mental walkthrough: browser → reverse proxy → which container → which endpoint → response path. Surface chýbajúce routes pred deploy

Toto bude aplikované v F-006 Designer charter rozšírenie (Implementer Fáza 3 work).

### Lekcia — comprehensive review vs reaktívne discovery

Manual smoke test discovery pattern (1 bug → fix → re-deploy → next bug) je nákladný (~3-5 h per cycle × 14 findings = ~42-70 hodín). Comprehensive sub-agent review (~22 min) odhalil všetkých 14 findings + consolidation návrh **single CR-022** vs piecemeal 14 CRs. **Time saved: ~12-18 hodín** (or more if smoke test cycles re-run).

Tento pattern by mal byť aplikovaný **PRED prvým deploy attempt** pri každom F-spec ktorý dotyká real-world integration (deploy, audit, UAT). Pravidlo navrhnutý do Designer charter §X.Y "Pre-commit spec verification" v F-006.

---

## 2026-05-22 — CR-021 F-003 §4.1 auto-detection per-projekt backend config

### Kontext

NEX Studio v0.2.0 Implementer round 2 fáza — real-world smoke test odhalil bug v F-003 generic compose template. Template predpokladal štandardný FastAPI backend port 8000 (matches nex-inbox), ale NEX Studio backend beží na custom port 9176 → Docker healthcheck `curl http://localhost:8000/health` zlyhal napriek tomu že backend bol skutočne up (`uvicorn running on http://0.0.0.0:9176`).

### Spec design gap (Dedo acknowledgment)

Toto bola **moja chyba ako spec writer** — Sub-round 4 Q1 schválil generic Jinja2 template ("per-projekt override defer to v0.3.0+"), ale ja som v generic template hardcoded port 8000 bez explicit poznámky o assumption. Per memory `feedback_read_spec_before_paraphrasing` mal som overiť target projekt assumptions cez `grep -r "port" /opt/projects/<target>/docker-compose.yml` PRED finalizáciou F-003 spec.

### Zmeny

**F-003-uat-environment.md §4.1 Discovery rozšírená:**

Po `Check existing /opt/uat/<slug>/` doplnené:
- Auto-detect per-projekt backend config z `<source-projekt>/docker-compose.yml` (ak existuje):
  - Parse `services.backend.ports` mapping
  - Parse `services.backend.healthcheck.test`
  - Render UAT template s detected hodnotami
- Fallback ak source neexistuje: default port 8000 + `/health` endpoint
- Plus CLI override: `--backend-port <port>` + `--health-endpoint <path>` pre edge cases

**F-003-uat-environment.md §13 acceptance #1 doplnené:**

Pôvodné: "uat-deploy vie nasadiť UAT zostavu z aktuálneho kódu"
Nové: "...s auto-detected per-projekt backend port + healthcheck per CR-021. Auto-detection verified pre nex-inbox (8000) + nex-studio (9176)"

### Implications pre v0.3.0+

Mimo rozsahu tohto CR (defer per Sub-round 4 Q1 + Customer Requirements §10):
- **Per-projekt full compose customization** (volumes, env vars, custom services Ollama/Redis) — NEX Studio sám seba má 5 custom volumes (`.claude`, `knowledge`, `projects`, `credentials`, `uploads`) ktoré generic template nepokrýva. Defer to v0.3.0+ per-projekt override mechanism.
- **Clarification "valid UAT targets":** NEX Studio sám seba je platform-level service s custom volumes — nie typický "deploy as UAT" cieľ. Validné UAT targets sú projekty deployed cez NEX Studio (nex-inbox, nex-manager, atď.). Documentation amendment defer to v0.3.0+ (low priority).

### Continuous improvement notes

Plus Designer charter Inbox Deda flag (Dedo's own návrh):

**Problém:** Designer charter aktuálne nemá explicit "Pre-commit spec verification" pravidlo. Generic template assumptions (port 8000) som finalize bez overenia v target projektoch.

**Návrh úpravy:** Doplniť do `templates/designer-charter.md` (až bude vytvorený v F-006) novú sub-sekciu "§X.Y Pre-commit spec verification":
- Pred finalizáciou spec ktorá assume-uje per-projekt config (porty, paths, services) → grep/Read target projektov pre verification
- Anti-pattern: "Generic template assume X" bez verifikácie že existing projects matches X

**Charter ktorého agenta:** Designer
**Posúdenie:** Všeobecný charakter — platí pre všetky Designer spec writing s cross-project assumptions
**Pôvod:** F-003 §4.1 port 8000 assumption + real-world bug 2026-05-22 (nex-studio backend 9176 mismatch)

### Implementer round 2 expected work

Per Variant B schválený Direktorom 2026-05-22:
1. Auto-detection logic v `scripts/_uat_lib.py` (parse source docker-compose.yml)
2. Update `scripts/uat-deploy.py` — call detection + render template s detected values
3. CLI flags `--backend-port` + `--health-endpoint` (override mechanism)
4. Update `templates/uat/docker-compose.yml.j2` — placeholders pre auto-detected values
5. Update tests (real I/O testing per Implementer's vlastný memory návrh + auto-detection coverage)

Estimate: ~3-4 hodín Implementer práce + re-run smoke test.

---

## 2026-05-21 — Spec balík v0.2.0 vytvorený (Brána A → B → C → D)

### Brána A (Customer requirements)

- **Customer requirements** transformuje Direktorovu strategickú víziu do 11-sekciového dokumentu
- **Customer dialogue** zachytáva Q&A audit stopu diskusie 2026-05-21 medzi Direktorom a Dedom

### Brána B (High-level spec)

- **Summary.md** — Direktor-friendly prehľad (11 sekcií)
- **Development-spec.md** — Designer mid-level plán (11 sekcií, 6 features F-001..F-006, 4 fázy implementácie, 4 otvorené otázky)

### Brána C (Per-feature spec)

6 production-ready specs:

- **F-001 Koordinátor charter** + settings.json template (13 sekcií, ~470 LOC + 90 LOC settings)
- **F-002 Inbox Deda mechanika** (12 sekcií, ~470 LOC)
- **F-003 UAT prostredie** (15 sekcií, ~640 LOC — najväčší)
- **F-004 Create Project vylepšenia** (9 sekcií + 5 sub-sekcií, ~450 LOC, rieši P0-RG1)
- **F-005 Audítorský smoke test** (9 sekcií, ~600 LOC, rieši P0-RG5 cez Activity X mandatory)
- **F-006 Spätné prispôsobenie existujúcich agentov** (9 sekcií, ~450 LOC, Designer + Auditor charter updates)

### Brána D (Sub-round 4 Resolution)

- **Sub-round 4 Resolution** — 20 otvorených otázok z F-001..F-006 + development-spec rešené per quality-first principle
- 6 položiek explicit deferred to v0.3.0+

---

## Spec balík totality

| Dokument | LOC | Účel |
|---|---|---|
| `customer-requirements.md` | ~385 | WHAT — zákaznícke požiadavky (11 sekcií) |
| `customer-dialogue.md` | ~357 | WHY — Q&A audit stopa diskusie |
| `spec/summary.md` | ~173 | Direktor-friendly prehľad |
| `spec/development-spec.md` | ~343 | HOW high-level — Designer mid-level plán |
| `spec/F-001-coordinator-charter.md` | ~470 | F-001 production-ready charter template |
| `spec/F-001-coordinator-settings.json` | ~90 | F-001 permissions template |
| `spec/F-002-inbox-deda.md` | ~470 | F-002 inbox mechanika |
| `spec/F-003-uat-environment.md` | ~640 | F-003 UAT prostredie (najväčší) |
| `spec/F-004-create-project-improvements.md` | ~450 | F-004 Create Project vylepšenia |
| `spec/F-005-audit-smoke-test.md` | ~600 | F-005 Activity X mandatory |
| `spec/F-006-agent-charter-updates.md` | ~450 | F-006 charter updates |
| `spec/sub-round-4-resolution.md` | ~430 | Sub-round 4 resolution otvorených otázok |
| **Total** | **~4858 LOC** | 12 spec dokumentov |

---

## Pripravený na Implementer round

Spec balík v0.2.0 je **kompletný** a pripravený pre **Implementer round** (Fáza 4 v Customer Requirements §2 workflow).

**Migračný postup per Customer Requirements §9 (Variant C):**

1. **Fáza 1 NEX Studio v0.2.0 development** (~3-5 týždňov):
   - F-001 + F-002 (najpriamejšie) — 3-5 dní
   - F-003 UAT prostredie — 5-7 dní
   - F-004 Create Project + F-006 spätné prispôsobenie — 3-5 dní
   - F-005 Audítorský smoke test — 2-3 dni
2. **Fáza 2 NEX Inbox v0.2.0** cez nový ekosystém — 1-2 týždne

Pre-flight optimization (Implementer charter extension) **HOTOVÉ** 2026-05-21 (commit `934fd0b` v nex-studio main).

---

## Zdroje

- `docs/session-logs/2026-05-21-002.md` — plný kontext strategickej diskusie
- `docs/findings/2026-05-21-release-verification-gaps.md` — 4 NEX Studio improvements z NEX Inbox v0.1.0 sprint
- `/opt/projects/nex-inbox/docs/specs/versions/v0.2.0/backlog.md` sekcia 0 — 5 P0 release-gate gaps (NEX Inbox)
