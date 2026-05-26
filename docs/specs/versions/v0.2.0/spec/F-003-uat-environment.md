# F-003 UAT prostredie

**Feature:** F-003
**Verzia:** NEX Studio v0.2.0
**Stav:** Návrh — Brána C (per-feature spec)
**Závislosti:** F-001 (Koordinátor — orchestruje nasadenie), F-005 (Audítorský smoke test — UAT spúšťa po PASS verdict)

---

## 1. Účel a kontext

UAT (User Acceptance Testing) prostredie je **fáza overenia pred produkčným nasadením**. Rieši kritickú medzeru odhalenú v NEX Inbox v0.1.0 sprinte — release verdict PASS (3 audit cykly) sa ukázal ako false positive, lebo nikto nikdy nepustil stack reálne. UAT vsadzuje **acceptance test pred produkciou** — Direktor (a v budúcnosti QA agent) prejde end-to-end scenáre na live staging zostave.

V 9-fázovom pracovnom postupe (per Customer Requirements §2) UAT pokrýva **fázy 7-8**:
- **Fáza 7:** UAT nasadenie — Koordinátor spustí UAT zostavu
- **Fáza 8:** UAT akceptácia — Direktor + zákaznícky operátor prejdu scenáre

Až **po UAT acceptance** (PASS) prebehne **Fáza 9** produkčný deploy.

---

## 2. Architektonický prehľad

### Per-tenant model

UAT prostredia bežia per **tenant slug** v `/opt/uat/<slug>/`:

```
/opt/uat/
├── dev/                    # Interné UAT — Direktor testuje pred customer rollout
├── mager/                  # MÁGERSTAV UAT — zákaznícka konfigurácia
└── <budúci-zákazník>/      # Ďalší zákazníci keď príde čas
```

Pre každý slug **jedno aktívne UAT prostredie naraz** (per Variant E z customer-requirements §5.4).

### Paralelná štruktúra s produkciou

```
/opt/projects/<slug>/         <projekt>                 (source code + spec)
/opt/uat/<slug>/              UAT staging               (sandbox docker-compose)
/opt/customers/<slug>/        Production deployment     (live customer instance)
```

Tieto 3 adresáre sú **úplne oddelené** — vlastné docker-compose, .env, šifrovacie kľúče, databáza. Žiadny mix dát alebo kredentials.

### Životný cyklus UAT prostredia

```
1. Auditor PASS (vrátane F-005 Activity X buildable + bootable)
       ↓
2. Koordinátor signalizuje Direktorovi pripravenosť pre UAT
       ↓
3. Direktor schvaľuje uat-deploy <dev|zákazník>
       ↓
4. nex-studio uat-deploy <slug>
   ├─ DB snapshot existujúceho UAT (ak je)
   ├─ docker compose down (cleanup)
   ├─ rebuild images z aktuálneho kódu projektu
   ├─ docker compose up -d
   ├─ alokuje port z bloku 19500-19599
   ├─ vystaví URL https://uat-<slug>.isnex.eu
   └─ seedne testovacie dáta
       ↓
5. Direktor pristúpi cez URL + prejde acceptance-checklist
       ↓
6. PASS → produkčný deploy / FAIL → fix-bundle round
       ↓
7. Pri novej verzii → krok 4 znova (Variant E — nahradenie s snapshotom)
```

---

## 3. Adresárová štruktúra

### Per-slug v `/opt/uat/<slug>/`

```
/opt/uat/mager/
├── docker-compose.yml          # UAT-specific (oddelené od /opt/customers/mager/)
├── .env                        # UAT credentials (oddelené šifrovacie kľúče)
├── customer-test-data/         # Reálne zákaznícke faktúry (mimo gitu)
│   ├── invoice-2025-001.pdf
│   └── invoice-2025-002.pdf
├── snapshots/                  # DB snapshots pred cleanup-mi
│   ├── v0.1.0-2026-06-15.sql.gz
│   └── v0.0.5-2026-04-10.sql.gz
└── logs/                       # Container logs (mimo Docker volume)
    └── backend-2026-05-22.log
```

### Per-projekt v `<projekt>/docs/uat/v<version>/`

```
/opt/projects/nex-inbox/docs/uat/v0.2.0/
├── acceptance-checklist.md     # Hlavný akceptačný zoznam (Variant D autorstvo)
├── test-data/
│   ├── test-data-spec.md       # Špecifikácia testovacích dát (Designer kostra)
│   └── synthetic/              # Vygenerované syntetické PDF (uložené v gite)
│       ├── 01-happy-text.pdf
│       ├── 01-happy-text.json  # Metadata + očakávaný extract output
│       └── 02-scan-low-quality.pdf
└── results/                    # Vyplnené checkliste z konkrétnych UAT behov
    ├── 2026-05-22-dev-run-001.md
    └── 2026-05-25-mager-run-001.md
```

---

## 4. CLI nástroje (5 skriptov)

Implementované ako bash + Python skripty v `nex-studio/scripts/`. Volajú sa cez NEX Studio wrapper `nex-studio <command> <args>`.

### 4.1 `nex-studio uat-deploy <slug>`

**Účel:** Nasadiť UAT prostredie z aktuálneho kódu projektu.

**Postup:**

```bash
nex-studio uat-deploy mager
```

1. **Discovery + auto-detection per-projekt config (CR-021 + CR-022):**
   - Verify `/opt/projects/<slug>/` existuje (alebo `/opt/projects/<projekt>/` ak slug ≠ projekt)
   - Check existing `/opt/uat/<slug>/` — ak existuje, signal Direktorovi pred nahradením
   - **Auto-detect per-projekt backend config** z `<source-projekt>/docker-compose.yml` (per CR-021):
     - Parse `services.backend.ports` mapping (napr. `9176:9176` → backend port = 9176; `8000:8000` → 8000)
     - Parse `services.backend.healthcheck.test` (re-use ten istý `test:` v UAT template) alebo derive z detected port
     - Parse `services.backend.build.dockerfile` (CR-021 expansion — oba target projekty používajú `backend/Dockerfile`, generic default `Dockerfile` neplatí)
     - Fallback ak source docker-compose neexistuje: default port 8000 + `/health` endpoint + dockerfile `Dockerfile`
     - Plus override cez CLI flags: `--backend-port <port>` + `--health-endpoint <path>` (pre edge cases)
   - **Auto-detect per-projekt DB credentials** z `<source-projekt>/docker-compose.yml services.db.environment` (per CR-022 C-2):
     - Parse `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` z db (alebo `postgres`) service
     - Použiť v UAT template namiesto generic `postgres/postgres/<project>_uat` (ktorý produkuje invalid identifiers s hyphen-mi pre nex-studio + nex-inbox)
     - Plus auto-detect connection driver — backend `DATABASE_URL` v source compose obsahuje napr. `postgresql+pg8000://...` (nex-studio) alebo split `DB_HOST/PORT/NAME/USER/PASSWORD` (nex-inbox)
     - Fallback ak source neexistuje: `postgres / generated_password / <project>_uat`
   - **Auto-detect per-projekt backend env vars** z `<source-projekt>/.env.example` + `services.backend.environment` (per CR-022 C-1):
     - Parse všetky `KEY=VALUE` z `.env.example` (povinné env vars per projekt)
     - Parse keys z `services.backend.environment` (ďalšie env vars passované cez compose)
     - Generate synthetic UAT `.env` s:
       - Random hodnoty pre keys končiace na `_PASSWORD`, `_SECRET`, `_KEY`, `_TOKEN` (cez `openssl rand -hex 32`)
       - Auto-detected hodnoty pre DB connection vars (per credentials detection vyššie)
       - Placeholder `__UAT_SYNTHETIC__` pre keys ktoré sa nedajú auto-generate (Direktor doplní pred deploy)
     - Plus produkčné secrets sú **vždy synthetic** (per F-003 §11 bezpečnostné aspekty), žiadny passthrough produkčných credentials
   - **Auto-detect per-projekt frontend config** z `<source-projekt>/docker-compose.yml services.frontend` (per CR-022 C-4 + C-5):
     - Parse `services.frontend.build.context` (môže byť projekt root `.` alebo subadresár `./frontend/` — nex-inbox vs nex-studio)
     - Parse `services.frontend.build.dockerfile` (nex-inbox `frontend/Dockerfile`, nex-studio `Dockerfile`)
     - Parse `services.frontend.build.args` (najmä `VITE_API_BASE_URL` — nex-studio `""` vs nex-inbox `/api/v1`)
     - Použiť v UAT template `FRONTEND_CONTEXT`, `FRONTEND_DOCKERFILE`, `FRONTEND_BUILD_ARGS` placeholderoch
     - Fallback ak source nemá frontend service: skip frontend container (backend-only UAT)
   - **Auto-detect alembic strategy** z `<source-projekt>/backend/main.py` + `<source-projekt>/backend/Dockerfile` (per CR-022 C-3):
     - Read `backend/main.py` (alebo equivalent entrypoint): grep pre patterns `command.upgrade` / `alembic.command.upgrade` / `alembic_cfg` v lifespan/startup handleri
     - Ak found → `alembic_strategy: self-bootstrap` (backend internally beží migrations pri starte) → UAT deploy step 8 **skipne** external alembic
     - Read `backend/Dockerfile` runtime stage (`FROM ... AS runtime`): verify či `poetry` binary je v runtime image
     - Ak `self-bootstrap` + poetry **chýba** v runtime → confirm skip rationale (poetry would fail anyway)
     - Inak (no self-bootstrap pattern found) → `alembic_strategy: external` → UAT deploy step 8 vykoná migration command:
       - Pokus 1: `python -m alembic upgrade head` (works ak alembic na PATH bez poetry)
       - Pokus 2 fallback: `poetry run alembic upgrade head` (inbox-style)
       - Pokus 3 fallback: clear error message s pokynom Direktorovi (manual exec)
     - Plus CLI flag `--alembic-strategy {auto|self-bootstrap|external|skip}` pre override

2. **DB snapshot existujúceho UAT (ak relevantné):**
   ```bash
   docker exec uat-<slug>-postgres pg_dump -U postgres | \
     gzip > /opt/uat/<slug>/snapshots/v<existing-version>-$(date +%Y-%m-%d).sql.gz
   chmod 600 /opt/uat/<slug>/snapshots/*.sql.gz
   ```

3. **Cleanup existujúceho stacku:**
   ```bash
   cd /opt/uat/<slug>
   docker compose down
   docker volume rm uat-<slug>_postgres_data || true
   ```

4. **Rebuild images z aktuálneho kódu:**
   ```bash
   cd /opt/projects/<projekt>
   docker compose -f /opt/uat/<slug>/docker-compose.yml build
   ```

5. **Port allocation:**
   - `scripts/allocate-port.sh <slug> --uat` z bloku 19500-19599
   - Zapíše do `/opt/uat/<slug>/.env`

6. **Start stack:**
   ```bash
   cd /opt/uat/<slug>
   docker compose up -d
   ```

7. **Wait for healthy:**
   ```bash
   while ! curl -sf http://127.0.0.1:<port>/health; do sleep 5; done
   ```

8. **Migrate database:**
   ```bash
   docker exec uat-<slug>-backend poetry run alembic upgrade head
   ```

9. **Seed testovacie dáta:**
   - Načíta `/opt/projects/<projekt>/docs/uat/v<version>/test-data/synthetic/*.json`
   - Insert do UAT DB (testovacie faktúry, dodávatelia, atď.)

10. **Expose URL:**
    - Add entry do host NGINX config (`/etc/nginx/sites-available/uat-<slug>.conf`)
    - Reload nginx (`sudo systemctl reload nginx`)
    - Print Direktorovi: `https://uat-<slug>.isnex.eu`

11. **Print acceptance checklist preview:**
    ```
    UAT pripravené pre <slug> v<version>:
    - URL: https://uat-<slug>.isnex.eu
    - Scenáre: 25 (z docs/uat/v<version>/acceptance-checklist.md)
    - Test data: 28 syntetických PDF + 5 reálnych
    ```

**Idempotent re-run:** ak uat-deploy beží druhýkrát s rovnakou verziou, urobí len rebuild + restart (žiadny nový snapshot ak verzia nezmenená).

**Error handling:**
- Pri zlyhaní v ktoromkoľvek kroku: STOP, log error, prepustiť cleanup ak treba
- Pri zlyhaní `docker compose up` → log + Direktor decision (retry vs investigate)
- Pri zlyhaní health check po 60s → log container logs + STOP

### 4.2 `nex-studio uat-teardown <slug>`

**Účel:** Demontovať UAT prostredie s zachovaním DB snapshot.

**Postup:**

```bash
nex-studio uat-teardown mager
```

1. **Confirm pred destruktívnymi krokmi:**
   ```
   POZOR: Demontuje sa UAT prostredie pre <slug>.
   - DB snapshot bude uložený do /opt/uat/<slug>/snapshots/
   - Stack down + volumes removed
   - URL https://uat-<slug>.isnex.eu nedostupný

   Pokračovať? [y/N]
   ```

2. **DB snapshot:**
   ```bash
   docker exec uat-<slug>-postgres pg_dump -U postgres | \
     gzip > /opt/uat/<slug>/snapshots/v<version>-$(date +%Y-%m-%d-teardown).sql.gz
   chmod 600 /opt/uat/<slug>/snapshots/*.sql.gz
   ```

3. **Stack down + volumes:**
   ```bash
   cd /opt/uat/<slug>
   docker compose down
   docker volume rm uat-<slug>_postgres_data
   ```

4. **NGINX cleanup:**
   - Remove `/etc/nginx/sites-available/uat-<slug>.conf`
   - Reload nginx

5. **Port release:**
   - `scripts/allocate-port.sh <slug> --release --uat`

6. **Zachované:** `/opt/uat/<slug>/snapshots/` + `/opt/uat/<slug>/customer-test-data/` + `/opt/uat/<slug>/logs/`

7. **Zmazané:** `docker-compose.yml`, `.env` (Direktor môže explicit zmazať po pochopení dôsledkov)

### 4.3 `nex-studio uat-status <slug>`

**Účel:** Zobraziť aktuálny stav UAT prostredia.

**Postup:**

```bash
nex-studio uat-status mager
```

Output:
```
UAT slug: mager
Status: RUNNING
Version: v0.2.0
URL: https://uat-mager.isnex.eu
Containers:
  - uat-mager-backend     Up 2 days (healthy)
  - uat-mager-frontend    Up 2 days (healthy)
  - uat-mager-postgres    Up 2 days (healthy)
Last deploy: 2026-05-20 10:30 UTC
Snapshots: 3 (latest: v0.1.0-2026-06-15.sql.gz)
Disk usage: 8.3 GB
```

Alebo ak nie:
```
UAT slug: mager
Status: NOT DEPLOYED
Last snapshot: v0.0.5-2026-04-10.sql.gz (existuje, ready for restore)
```

### 4.4 `nex-studio generate-test-pdfs <projekt>`

**Účel:** Generovať syntetické PDF z `test-data-spec.md`.

**Postup:**

```bash
nex-studio generate-test-pdfs nex-inbox
```

1. Načíta `/opt/projects/<projekt>/docs/uat/v<active>/test-data/test-data-spec.md`
2. Parse YAML zoznam scenárov (Designer kostra + agenti rozšírenie):
   ```yaml
   - id: "01-happy-text"
     scenario: "Bežná faktúra 23% DPH, text PDF"
     supplier: "Synth Dodávateľ Alpha s.r.o."
     supplier_ico: "12345678"
     supplier_ic_dph: "SK1234567890"
     amount_net: 1000.00
     amount_vat: 230.00
     amount_total: 1230.00
     line_items:
       - description: "Materiál typu A"
         quantity: 10
         unit_price: 100.00
   ```
3. Šablónové PDF generation (Python + reportlab) per scenár
4. Output do `<projekt>/docs/uat/v<active>/test-data/synthetic/`:
   - `01-happy-text.pdf` (samotný PDF)
   - `01-happy-text.json` (metadata + očakávaný extract output)

**Idempotentné:** existujúce PDF prepíše iba ak `test-data-spec.md` zmenené.

**Anonymizácia:** všetky IČO sú vymyslené (algoritm checksum-valid ale neexistujúce firmy), adresy syntetické, dodávateľské mená "Alpha", "Beta", atď.

### 4.5 `nex-studio uat-snapshot <slug>`

**Účel:** Ad-hoc DB snapshot mimo cleanup cyklu.

**Postup:**

```bash
nex-studio uat-snapshot mager --reason "before-experimental-config-change"
```

1. Verify UAT pre slug beží
2. Pg_dump + gzip + 0600 permissions
3. Filename: `/opt/uat/<slug>/snapshots/v<version>-<dátum>-<reason>.sql.gz`

Použitie: pred riskantnou zmenou konfigurácie, pred testovaním edge case, alebo na žiadosť Direktora.

---

## 5. UAT akceptačný zoznam

### Lokácia

`<projekt>/docs/uat/v<version>/acceptance-checklist.md`

### Hybridné autorstvo (Variant D z customer-requirements §5.2)

| Aktér | Zodpovednosť | Output |
|---|---|---|
| **Designer** | Navrhuje scenáre, mapuje na sekcie Customer Requirements | Zoznam scenárov + očakávané výsledky |
| **Auditor** | Verifikuje pokrytie (coverage matrix), nájde medzery | Pokrytie matrica + zoznam dier |
| **Koordinátor** | Operacionalizuje (poradie behov, prepojenie na test data) | Behu-pripravený checklist s preklikmi |
| **Direktor** | Beží scenáre, odškrtáva, flag-uje problémy | Vyplnený výsledok v `results/` |

### Formát

Markdown s YAML frontmatter + štruktúrované sekcie:

```markdown
---
version: v0.2.0
scenarios_total: 25
generated_by: designer (kostra) + auditor (pokrytie) + coordinator (operacionalizácia)
status: ready | in-progress | completed
last_run: 2026-05-22-dev-run-001
---

# UAT Akceptačný zoznam — NEX Inbox v0.2.0

## Sekcia 1: Príjem faktúr (Customer Requirements §2)

### Scenár 1.1 — Preposlaný email s 1 PDF prílohou

**Predpoklady:**
- Stalwart Mail inbox `inbox-<slug>@isnex.eu` má email s 1 PDF prílohou
  (`test-data/synthetic/01-happy-text.pdf`)
- IMAP credentials nakonfigurované (test creds v `.env`)

**Postup:**
1. Operátor preposiela testovací email z `operator@test.local`
2. Čaká 5 minút (default IMAP poll interval)

**Očakávaný výsledok:**
- Dashboard zobrazí novú faktúru
- Stav prejde: `validating` → `extracted` → `validated` → `exported`
- XML output v `/opt/uat/<slug>/customer-test-data/genesis-out/`

**Akceptačné kritériá:**
- [ ] Faktúra je v zozname Dashboard (do 5 min)
- [ ] PDF originál archivovaný (preklik z faktúry funguje)
- [ ] XML output vygenerovaný v SMB úložisku
- [ ] Audit log obsahuje 5 záznamov (received → exported transitions)

### Scenár 1.2 — Email bez PDF prílohy (negatívny)

[... podobná štruktúra ...]
```

### Coverage matrix sekcia

Auditor pridá tabuľku pokrytia:

```markdown
## Coverage Matrix

| Customer Requirements sekcia | Scenár # | Stav |
|---|---|---|
| §2.1 Príjem faktúr | 1.1, 1.2, 1.3 | ✅ pokryté |
| §2.4 Spracovanie príloh | 1.1 | ✅ |
| §3.1 Emaily bez PDF | 1.2 | ✅ |
| §4.3 Neznámy dodávateľ | 4.1 | ✅ |
| §6.5 Reverse charge | 6.1, 6.2 | ✅ |
| §15.1 Notifikácie | 7.1-7.12 | ⚠ pokryté 11/12 (chýba storage warning) |
```

---

## 6. Testovacie dáta — hybridný prístup

### 6.1 Syntetické dáta v gite

**Lokácia:** `<projekt>/docs/uat/v<version>/test-data/synthetic/`

**Generovanie:** `nex-studio generate-test-pdfs <projekt>` (per §4.4).

**Vstup:** `test-data-spec.md` (Designer kostra + agenti rozšírenie):

| Aktér | Zodpovednosť | Príklad |
|---|---|---|
| **Designer** | Syntetická kostra pre všetkých 25 scenárov | Zoznam: 01-happy-text, 02-scan-blurry, 03-reverse-charge, ... |
| **Customer agent** (ak existuje) | Variácie zo skutočného sveta (typy dodávateľov ktorých zákazník má) | "Slovenský dodávateľ stavebných materiálov", "Český spedičný" |
| **Implementer** | Technické edge cases | Corrupt PDF, encrypted, very large (~50 MB), scan zlej kvality |
| **Koordinátor** | Generuje samotné PDF cez `generate-test-pdfs` skript | 25-30 PDF v `synthetic/` |

**Anonymizácia (povinná):**
- Vymyslené IČO (algoritmus checksum-valid ale neexistujúce firmy)
- Vymyslené IČ DPH, DIČ
- Syntetické dodávateľské mená: "Synth Dodávateľ Alpha s.r.o.", "Beta Materials", atď.
- Syntetické adresy (Lorem Ipsum street + reálne slovenské mestá pre realistic look)

**Rozsah:** ~25-30 syntetických PDF pre v0.1.0 pilotnú verziu.

### 6.2 Reálne dáta mimo gitu

**Lokácia:** `/opt/uat/<slug>/customer-test-data/` (filesystem-only, NIE v gite)

**Účel:** Reprodukcia konkrétnych reálnych problémov ktoré sa vyskytli u zákazníka.

**Naplnenie:** Direktor (alebo zákaznícky operátor cez Direktora) priamo kopíruje reálne PDF do tejto cesty pri UAT setup. Žiadny automatický proces.

**Bezpečnostné dôvody:**
- Reálne faktúry obsahujú IČO/adresy/bankové údaje dodávateľov — citlivé (PII)
- Per ICC bezpečnostné princípy (CLAUDE.md §4) nepatria do uložených úložísk projektu
- ANDROS-only filesystem, žiadny zákaznícky prístup mimo Direktor

**Rozsah:** 0-5 reálnych PDF per UAT cyklus (per Direktor potreba).

---

## 7. Per-tenant cyklus

### Dvojstupňový workflow

```
Verzia hotová (Auditor PASS)
       ↓
Koordinátor: uat-deploy dev
       ↓
URL: https://uat-dev.isnex.eu
       ↓
Direktor prejde acceptance-checklist v dev UAT (~2-3 dni)
       ↓
PASS → schvaľuje pre customer rollout
       ↓
Koordinátor: uat-deploy mager (alebo iný zákazník)
       ↓
URL: https://uat-mager.isnex.eu
       ↓
Direktor + MÁGERSTAV operátor prejdu acceptance-checklist (~3-7 dní)
       ↓
PASS → produkčný deploy do /opt/customers/mager/
```

### Slugy

| Slug | Účel | Kto pristupuje |
|---|---|---|
| `dev` | Interné UAT pre Direktora pred customer rollout. Syntetické dáta, žiadne zákaznícke špecifiká | Direktor + ICC interný tím |
| `<zákazník>` (napr. `mager`) | Zákaznícke UAT s konkrétnou konfiguráciou (dodávatelia, IČO, address) | Direktor + zákaznícky operátor |
| `<zákazník>-hotfix` (voliteľné, defer to v0.3.0+) | Núdzový hotfix UAT keď produkcia beží predošlú verziu a my chceme rýchlo overiť opravu | Direktor pri urgent scenároch |

### Disk usage management

UAT zostava typicky **5-15 GB disk usage** (BE image + FE image + DB volume + test data). Pre N projektov × M slugov disk priestor narastá.

Koordinátor v každej priebežnej správe Direktorovi (per F-001 charter §9):
```
**UAT disk usage:** X.Y GB total (limit 50% ANDROS = Z GB)
```

Pri prekročení 50% threshold-u Koordinátor flag-uje urgent — Direktor rozhodne ktoré UAT cleanup-núť.

---

## 8. DB snapshot mechanika

### Kedy snapshot vždy

1. **Pred `uat-teardown`** — zachová stav pre prípadnú reprodukciu
2. **Pred `uat-deploy` novej verzie** — zachová predošlú verziu pre regression testing
3. **Ad-hoc cez `nex-studio uat-snapshot`** — pre risk-bound operácie

### Cesta a formát

```
/opt/uat/<slug>/snapshots/
├── v<version>-<dátum>.sql.gz           # Pred uat-teardown
├── v<version>-<dátum>-<reason>.sql.gz  # Ad-hoc
└── v<version>-<dátum>-teardown.sql.gz  # Teardown
```

Konvencia:
- `<version>` = sémantická verzia
- `<dátum>` = `YYYY-MM-DD`
- `<reason>` = optional suffix pre ad-hoc

### Properties

- **Kompresia:** gzip (typicky 5-15 MB pre Postgres DB ~50 MB)
- **Permissions:** `chmod 600` (read iba pre Direktor + sudo)
- **Retencia:** **bez expirácie** — snapshots zostávajú forever
- **Mazanie:** iba s explicit Direktorovým schválením **cez Inbox Deda žiadosť**

### Reštaurácia (manuálna)

```bash
gunzip < /opt/uat/<slug>/snapshots/v0.1.0-2026-06-15.sql.gz | \
  docker exec -i uat-<slug>-postgres psql -U postgres
```

---

## 9. Pravidlá čistenia (Variant E)

### Spúšťače čistenia

1. **Pri novej verzii UAT deploy** — Koordinátor signalizuje Direktorovi pred nahradením
2. **Manuálne** — Direktor cez `nex-studio uat-teardown <slug>`
3. **Pri prekročení disk threshold-u** — Koordinátor flag-uje, Direktor rozhoduje ktoré cleanup-núť

### Pred-deploy signál Direktorovi

Koordinátor v priebežnej správe:

```
Pripravený na UAT nasadenie v0.2.0 pre mager. Existujúce UAT v0.1.0:
- Disk usage: 8.3 GB
- Posledný beh: 2026-06-10
- DB snapshot pred prepísaním: snapshots/v0.1.0-2026-06-15.sql.gz
Schvaľuješ prepísanie?
```

**NIE automatické** — Koordinátor čaká explicit `uat-deploy <slug>` od Direktora.

### Cleanup checklist (per uat-teardown)

| Krok | Zachované? |
|---|---|
| DB snapshot do `snapshots/` | ✅ vždy |
| `customer-test-data/` (reálne PDF) | ✅ zachované |
| `logs/` (container logs) | ✅ zachované |
| Docker volumes | ❌ removed |
| `docker-compose.yml`, `.env` | ❌ removed pri full teardown |
| NGINX config + port allocation | ❌ released |

---

## 10. URL exposure

### NGINX reverse proxy

UAT URL `https://uat-<slug>.isnex.eu` cez **host-level NGINX** (mimo Docker, na ANDROS host).

**Per CR-022 C-6:** vhost musí proxy-ovať aj `/api/` na backend host port (inak browser pod Tailscale URL nemôže dosiahnuť backend). Pôvodný len `location /` na frontend nestačí pre projekty s cross-origin backend volaniami (nex-studio frontend cez `VITE_API_BASE_URL` volá backend direct, nie cez frontend nginx proxy).

```nginx
# /etc/nginx/sites-available/uat-mager.conf
server {
    listen 443 ssl http2;
    server_name uat-mager.isnex.eu;

    ssl_certificate /etc/letsencrypt/live/isnex.eu/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/isnex.eu/privkey.pem;

    # Backend API (CR-022 C-6 — explicit route na backend host port)
    location /api/ {
        proxy_pass http://127.0.0.1:19601;  # backend host port = UAT_PORT + 100
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Backend health endpoint (smoke + monitoring)
    location /health {
        proxy_pass http://127.0.0.1:19601/health;
    }

    # Frontend (default route — static bundle alebo SPA)
    location / {
        proxy_pass http://127.0.0.1:19501;  # frontend = UAT_PORT
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

**Poznámka pre nex-inbox vs nex-studio frontend nginx:** nex-inbox frontend container má vlastnú internal nginx ktorá môže proxy-ovať `/api/` na backend service v Docker network. Pre nex-studio frontend (Vite static bundle bez internal proxy) je host-level NGINX `/api/` route **nutný**. UAT generic template podporuje obe — frontend container dostane request na `/api/` len ak host NGINX nepřechytí prvý, čo závisí od poradia `location` blokov (specific `/api/` má prednosť pred `/`).

### Cert termination

Wildcard cert `*.isnex.eu` (Let's Encrypt) — UAT subdomény automaticky pokryté. Per memory ICC standards.

### Sieťová ochrana

UAT URL prístupné iba **Tailscale / RDP / intranet** (žiadny verejný internet). NGINX server name resolution + Cloudflare access list zabezpečí (per host nginx config).

### Per-slug subdomain routing

Konvencia:
- `uat-<slug>.isnex.eu` — production-like UAT (pre Direktora + zákazníka)
- `uat-dev.isnex.eu` — interné UAT (pre ICC tím)
- `uat-<slug>-hotfix.isnex.eu` (voliteľné, defer to v0.3.0+) — núdzový hotfix UAT

---

## 11. Bezpečnostné aspekty

| Aspekt | Riešenie |
|---|---|
| **UAT credentials** | Vlastné šifrovacie kľúče v `/opt/uat/<slug>/.env` (oddelené od `/opt/customers/<slug>/.env`). Generované pri uat-deploy (openssl rand -hex 16/32). UAT credentials sa NIKDY nepoužívajú v produkcii. |
| **Synthetic credentials pre per-projekt env vars (CR-022 C-1)** | uat-deploy parse-uje `<source-projekt>/.env.example` + `services.backend.environment` z source compose → generate synthetic UAT `.env` s random hodnotami pre keys končiace na `_PASSWORD/_SECRET/_KEY/_TOKEN` (cez `openssl rand -hex 32`). Žiadny passthrough produkčných credentials. Keys ktoré sa nedajú auto-generate (napr. `OPERATOR_EMAIL`, `MOCKUP_ADMIN_URL`) dostanú placeholder `__UAT_SYNTHETIC__` — Direktor doplní pred deploy. |
| **Shared synthetic DB password (CR-023)** | Synthetic DB password sa generuje **iba raz** per UAT env build a **zdiela sa** medzi všetkými DB credential consumers v rovnakej env: `POSTGRES_PASSWORD` (postgres container init), `DB_PASSWORD` (split-config backendy), `DATABASE_URL` embedded password (URL-form backendy), plus akýkoľvek ďalší DB-credential-bearing env var. Inak postgres container a backend dostávajú rôzne náhodne generované passwordy a auth zlyháva pri prvom backend connect (Bug #5 zachytený smoke testom 2026-05-26). Implementer impl: `_uat_lib.detect_backend_env_vars(...)` prijíma optional `synthetic_db_password` parameter; ak chýba, precomputes ho raz pred loopom; thread-uje ho do `_rewrite_db_connection_var` pre konzistentné použitie. |
| **Frontend container port auto-detection (CR-024)** | UAT compose template `services.frontend.ports` musí použiť **detegovaný** container port z source compose, nie hardcoded `:80`. Source compose má `services.frontend.ports` v jednom z formátov: `"HOST:CONTAINER"` (napr. nex-studio `"9177:9177"`) alebo `"IP:HOST:CONTAINER"` (napr. nex-inbox `"127.0.0.1:5173:80"`). `_uat_lib.detect_frontend_config(...)` extrahuje rightmost numeric segment (po strip protocol suffix ako `/tcp`) ako `container_port` (default 80). UAT template renderuje `"127.0.0.1:{{ UAT_PORT }}:{{ FRONTEND_CONTAINER_PORT }}"`. Inak NGINX reverse proxy → docker-proxy → wrong container port → connection reset (Bug #7 zachytený smoke testom 2026-05-26). |
| **Snapshot/teardown DB credentials propagation (CR-025)** | `uat-snapshot.py` + `uat-teardown.py` musia použiť **detegovaný** `POSTGRES_USER` (a optionally `POSTGRES_DB`) namiesto hardcoded `pg_dump -U postgres`. Zdroj: `/opt/uat/<slug>/.env` (single source of truth — uat-deploy už zapísal `POSTGRES_USER` + `POSTGRES_DB` lines per CR-022). Helper `_uat_lib.read_uat_env(slug)` parse-uje .env do dict; oba scripty volajú `pg_dump -U {detected_user} -d {detected_db}`. Inak pg_dump zlyháva s `role "postgres" does not exist` (Bug #8 zachytený smoke testom 2026-05-26). Plus password — postgres image default `trust` pre local socket, ale safe to always pass `-e PGPASSWORD={detected}` cez docker exec. |
| **Testovacie dáta — syntetické** | Anonymizované (vymyslené IČO, neexistujúci dodávatelia). Audit-friendly, uložené v gite bez rizika PII leak. |
| **Testovacie dáta — reálne** | `/opt/uat/<slug>/customer-test-data/` mimo gitu, mimo Docker volumes. Filesystem permissions 0700 (read iba Direktor). |
| **DB snapshots** | Kompresia + `chmod 600` + uložené v `/opt/uat/<slug>/snapshots/` (NIE v gite, NIE v zákazníckom úložisku). |
| **CLI nástroje** | `scripts/uat-*.sh` vyžadujú sudo pre `docker` + `mkdir /opt/uat/`. Nejde cez HTTP, žiadny auth bypass risk. |
| **Network isolation** | UAT NGINX config iba Tailscale/RDP/intranet. Žiadny public internet. |
| **Žiadny mix UAT a production** | UAT a production majú vlastné DB, vlastné credentials, vlastné docker networks. Žiadny shared state. |

---

## 12. Pracovný postup nasadenia

Per Customer Requirements §2 (9-fázový workflow), UAT pokrýva fázy 7-8. Detailný postup:

### Krok 1: Auditor PASS (post-fáza 5/6)

- Auditor dokončí Activity 1-4 + Activity X (Buildable + Bootable verification per F-005)
- Audit report PASS v `docs/audits/v<version>-re-gate-g-audit.md`
- Smoke test prešiel — `docker compose build` + `up` + `/health` confirmed

### Krok 2: Koordinátor signalizuje Direktorovi

Priebežná správa per F-001 §9:

```
**Akcia:** Auditor PASS pre v0.2.0
**Stav agentov:** Designer/Implementer dokončený, Auditor verdict PASS
**Otvorené pre Direktora:**
- Schvaľuješ uat-deploy do dev slug pre interné UAT?
**Inbox Deda:** 0 nových
**Ďalší krok:** Po schválení spustím `nex-studio uat-deploy dev` (cca 5-7 min)
```

### Krok 3: Direktor schvaľuje dev UAT

Direktor: "Schvaľujem, spusti dev UAT."

### Krok 4: uat-deploy dev

Koordinátor spustí `nex-studio uat-deploy dev` (alebo Direktor priamo cez CLI). Po dokončení URL signalizovaný Direktorovi:

```
UAT dev nasadené pre v0.2.0:
- URL: https://uat-dev.isnex.eu
- Scenáre: 25 (z docs/uat/v0.2.0/acceptance-checklist.md)
- Test data: 28 syntetických PDF
```

### Krok 5: Direktor prejde acceptance-checklist v dev UAT

Časový odhad: 2-3 dni (závisí od počtu scenárov a komplexnosti).

Direktor vyplní `docs/uat/v0.2.0/results/2026-XX-XX-dev-run-001.md`:
- ✅ / ❌ per akceptačné kritérium
- Komentáre k problémom

### Krok 6: Direktor verdict dev UAT

**PASS** → schvaľuje deploy do `<zákazník>` slug.
**FAIL** → fix-bundle round (späť Designer/Implementer).

### Krok 7: uat-deploy <zákazník>

```
nex-studio uat-deploy mager
```

Vlastná konfigurácia per zákazník (.env so zákazníckym IČO, address, dodávatelia).

### Krok 8: Direktor + zákaznícky operátor prejdú acceptance-checklist

Časový odhad: 3-7 dní (komplexnejšie kvôli zákazníckym špecifikám).

### Krok 9: PASS → produkčný deploy

Po PASS verdict:

```
nex-studio production-deploy mager
```

Toto deployne v0.2.0 do `/opt/customers/mager/` (mimo F-003 scope — je to produkčný deployment, nie UAT).

---

## 13. Acceptance criteria

| # | Kritérium | Verifikácia |
|---|---|---|
| 1 | `uat-deploy <slug>` vie nasadiť UAT zostavu z aktuálneho kódu (s **auto-detected** per-projekt backend port + healthcheck + dockerfile + DB credentials + env vars + frontend context + alembic strategy per CR-021 + CR-022) | Spustenie príkazu → po 5-7 min stack healthy + URL dostupné. **Acceptance verified pre obe target projekty:** nex-inbox (BE 8000, DB user `nex_inbox`, FE context repo-root, alembic external) + nex-studio (BE 9176, DB user `nexstudio`, FE context `./frontend/`, alembic self-bootstrap, NGINX `/api/` proxy fungujúci) |
| 2 | Direktor pristúpi cez vystavené URL z Tailscale/RDP/intranetu | `curl https://uat-<slug>.isnex.eu/health` cez Tailscale → 200 |
| 3 | `uat-teardown <slug>` zachová DB snapshot pred destrukciou | Po teardown `ls snapshots/` ukáže nový súbor `v<version>-<dátum>-teardown.sql.gz` |
| 4 | Akceptačný zoznam zobrazený Direktorovi po deploy | Output uat-deploy obsahuje preview počet scenárov + cesta k checklist-u |
| 5 | Dvojstupňový workflow `dev` → `<zákazník>` funguje paralelne | `dev` a `mager` UAT bežia naraz na rôznych portoch + rôznych URL |
| 6 | DB snapshot je reštaurovateľný | `gunzip + psql` z snapshot file vráti DB do pôvodného stavu |
| 7 | Per-tenant credentials oddelené | `/opt/uat/<slug>/.env` ≠ `/opt/customers/<slug>/.env` (rôzne keys) |
| 8 | NGINX URL routing per-slug funguje | Multiple UAT slugs naraz s rôznymi subdoménami |
| 9 | Syntetické test data anonymizované | Audit syntetického PDF — žiadne real IČO/PII |
| 10 | Direktor schvaľuje cleanup pred nahradením | Koordinátor flag-uje pred uat-deploy novej verzie keď existujúce existuje |

---

## 13.1 Inline template fixes (CR-022 M-3 + M-4 + M-5)

**M-3 healthcheck `start_period`:** UAT template má backend `healthcheck.retries: 12 × interval: 10s = 120s` budget. Pre projekty s 50+ migrations (nex-studio má 46 migrations) self-bootstrap môže prekročiť budget. **Doplnené** `start_period: 90s` v backend healthcheck — Docker waits pred counting failures.

**M-4 `restart: unless-stopped` vs UAT ephemeral:** UAT je per-cycle (deploy → test → teardown). Production-style `restart: unless-stopped` znamená ANDROS reboot oživí stale UAT containers competing for ports. **Zmenené** na `restart: "no"` pre všetky UAT services.

**M-5 explicit `networks:` block:** Compose default network konvencia `<directory>_default` funguje coincidentally (UAT slug-scoped cez `/opt/uat/<slug>/`), ale nie explicit. **Doplnené** explicit `networks: uat-<slug>-net` + assignment per service.

## 14. Mimo rozsahu — defer to v0.3.0+

| # | Položka | Dôvod odkladu |
|---|---|---|
| **M-1** | Custom services passthrough (Ollama, Redis, mockup, postgres-exporter, monitoring-net) | Per Sub-round 4 Q1 "per-projekt full compose customization defer to v0.3.0+". Workaround pre v0.2.0: dokumentovať v acceptance-checklist že mockup-dependent features sú **out of UAT scope** (production-only). |
| **M-2** | Backend volume mounts pre nex-studio (`.claude`, `knowledge`, `projects`, `credentials`, `uploads`, `terminal_logs`, Docker socket) | Strategic question — môže byť NEX Studio reasonably UAT-ovaný keď bootstrap-uje projekty? Suggest: nex-studio dogfooding mode = `dev` UAT s stripped-down volume profile; spec amendment pre v0.3.0+ keď scope clear. |

## 15. Otvorené otázky pre Sub-round 4

| # | Otázka | Možnosti |
|---|---|---|
| **O-1** | UAT acceptance history persistence — filesystem `results/` alebo NEX Studio DB tabuľka? | A) Filesystem (default, audit-trail-friendly v gite); B) DB tabuľka (queryable, UI integration); C) Hybrid (filesystem primary, DB index) — viď development-spec O-2 |
| **O-2** | Auto-cleanup snapshots po N rokoch? | A) Forever (žiadny auto-cleanup); B) 5 rokov retention; C) Manual cleanup Direktorom cez Inbox Deda |
| **O-3** | NEX Studio backend endpoint pre UAT status (alebo iba CLI)? | A) CLI only (default, jednoduchšie); B) GET `/api/v1/uat/<slug>/status` endpoint pre UI integration; C) Hybrid |
| **O-4** | Hotfix UAT slug (`<slug>-hotfix`) — implementovať v v0.2.0 alebo defer? | A) Defer to v0.3.0+ (default per customer-requirements §10); B) Implementovať v v0.2.0 ako voliteľná feature |

---

## 16. Krížové odkazy

| Dokument | Súvislosť |
|---|---|
| `customer-requirements.md` §2 (Fáza 7-8) + §5 (5 sub-rozhodnutí) | High-level UAT design |
| `customer-dialogue.md` §2.4 | Direktorove rozhodnutia o UAT (5 sub-rozhodnutí) |
| `summary.md` § Technologické rozhodnutia | UAT hosting + cyklus + dáta |
| `development-spec.md` §3.3 F-003 (7 komponentov) | High-level dizajn |
| `F-001-coordinator-charter.md` §4 (workflow) + §9 (DONE report format) | Koordinátor signalizuje UAT pripravenosť |
| `F-005-audit-smoke-test.md` (TBD Sub-round 3) | Activity X pred UAT deploy |
| Customer Requirements §5.3 testovacie dáta | Hybridný prístup syntetické vs reálne |

---

**Koniec dokumentu — F-003 UAT prostredie.**
