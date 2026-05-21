# NEX Studio findings — Release Verification Gaps

**Discovered:** 2026-05-21 (post NEX Inbox v0.1.0 release verdict PASS)
**Source:** Real deployment attempt during advisory session — NEX Inbox v0.1.0 stack NEVIE nabehnúť napriek 3 audit cyklom PASS.
**Audience:** NEX Studio improvement backlog (Director-driven prioritization)

---

## Context

NEX Inbox v0.1.0 prešiel 3 audit cyklami (Gate G → Re-Gate G → Re-Re-Gate G PASS) s formal verdiktom "Release povolený". Po release verdict pri prvom reálnom `docker compose build && docker compose up` zlyhal stack na 5 P0 deployment-blocking bugs (zaznamenané v `/opt/projects/nex-inbox/docs/specs/versions/v0.2.0/backlog.md` sekcia 0).

Toto NIE sú NEX Inbox project bugs vo vacuum — sú to **NEX Studio quality principle gaps**. NEX Studio mal tieto chyby zachytiť ako súčasť projektového workflow (Create Project, audit, release procedure). Director's predošlá kľúčová poznámka:

> "Tvoja úloha bola kontrolovať prácu s projektom z hľadiska NEX Studio. Robil si poradcu pre mňa aby som videl, ako bude fungovať práca v NEX Studio. Sledovali sme s tebou vývoj a zaznamenali sme ak niečo bolo treba upraviť v NEX Studio."

Tj NEX Inbox bol **NEX Test (crash test) pre NEX Studio**. Cieľ nie je NEX Inbox samotné, ale maximum NEX Studio quality. Tento dokument konsoliduje NEX Studio improvements identifikované cez 8-dňový sprint.

---

## Finding 1 — Create Project workflow incomplete scaffold

**Severity:** P0 (release-blocker pre NEX Studio v0.2.0+)
**Discovered:** 2026-05-21 Director question "GitHub repo nex-inbox je prázdny, prečo?"

**Symptom:** NEX Inbox lokálny `.git/config` nemá nastavený `[remote "origin"]` blok. GitHub repo `rauschiccsk/nex-inbox` existuje (PRIVATE) s `pushedAt: 2026-05-12T16:04:45Z` (initial empty), ale lokálny repo k nemu nikdy nebol pripojený. 80+ lokálnych commitov + git tag v0.1.0 nikdy nepushed.

**Root cause:** NEX Studio Create Project workflow spustil `gh repo create` (GitHub repo vytvorený) ale nepokračoval s `git remote add origin <url>` + initial `git push -u origin main`. **Silent failure medzi 2 krokmi scaffold-u.**

**NEX Studio improvements required:**

1. **Create Project post-scaffold verification** — verify cez `git remote -v` že origin existuje + verify že initial commit pushed (`git ls-remote origin HEAD`). Failure → STOP, hlásiť Directorovi, NIE silent fail.
2. **Rollback on partial failure** — ak `gh repo create` prešiel ale `git remote add origin` zlyhal, NEX Studio má buď retry-núť alebo rollback-núť GitHub repo (rm). Nesmie zostať polovičatý stav.
3. **Visible CI/CD wire-up** — Create Project má voliteľne nastaviť GitHub Actions workflow z template (Lint + Test + Build) aby project bol immediately CI-ready.

---

## Finding 2 — No build/deploy smoke test in audit workflow

**Severity:** P0 (audit verdict reliability)
**Discovered:** 2026-05-21 reálny build attempt po release verdict PASS

**Symptom:** NEX Inbox v0.1.0 prešiel 3 audit cyklami (Gate G, Re-Gate G, Re-Re-Gate G) s 549 BE + 60 FE testov GREEN + Tibor Dual-Build test PASS 6/6 byte-equal. **Žiadny test ani audit aktivita** neoveril že `docker compose build` prejde, ani že `docker compose up` produkuje healthy containers.

**Konkrétne 4 deployment-blocking bugy ktoré audit prehliadol:**
- P0-RG1 backend Dockerfile build_context bug (`COPY pyproject.toml poetry.lock ./` mismatch)
- P0-RG2 frontend Dockerfile docs/ access bug (CR-018 #3 generator)
- P0-RG3 saxonche silent install fail (BE image bez .venv → uvicorn crash)
- P0-RG4 env loading issue v alembic flow

**Root cause:** Audítor self-PIV SP-02 ("docker compose build BE+FE obrazy") + SP-07 ("docker compose up celý stack smoke") boli flagované ako self-PIV gaps, ale Director (na moje odporúčanie) ich schválil ako "MÁGERSTAV pre-deploy gates" — out-of-scope release audit. **Moja chyba** — buildable + bootable je core release criterion (žiadny deployment bez nich), nie pre-deploy concern.

**NEX Studio improvements required:**

1. **Audítor charter update** — `MÁGERSTAV pre-deploy gates` musí explicit excludovať buildable + bootable verification. Tieto sú **Activity X mandatory** v každom audit cycle (Gate / Re-Gate / Re-Re-Gate).
2. **NEX Studio audit framework** — predefinovaný smoke test set ktorý audit workflow automaticky aplikuje:
   - `docker compose build` (BE + FE) — must succeed
   - `docker compose up -d db && wait healthy` — must succeed
   - `poetry run alembic upgrade head` (alebo equivalent) — must succeed
   - `docker compose up -d` (full stack) — all containers must reach healthy
   - `curl /health` — must return non-empty (degraded acceptable for bootstrap mode, OK for full)
3. **CI/CD gate** — pre release tag (v0.X.0), CI workflow musí spustiť smoke test + odmietnuť push tagu ak smoke fails.

---

## Finding 3 — Silent failure mode v Dockerfile patterns

**Severity:** P1 (process / tooling)

**Symptom:** Backend Dockerfile `RUN poetry install --only main --no-root` zlyhalo pre saxonche dependency (Java requirement), ale Docker layer cache + RUN exit handling produced image bez `.venv`. **Build success exit code 0** napriek silent fail. Runtime crash až keď container sa snaží spustiť uvicorn.

**Root cause:** Dockerfile RUN príkazy default-uju na `bash` bez `set -e`. Multi-step `RUN poetry install && poetry foo && poetry bar` zlyhanie v middle step nemusí propagovať failure ak posledný príkaz prejde. Plus dependencies ktoré "Cannot install" (saxonche) ale Poetry mark them as `optional` možno produkujú warning, nie error.

**NEX Studio improvements required:**

1. **Dockerfile template** v NEX Studio Create Project — `SHELL ["/bin/bash", "-euo", "pipefail", "-c"]` ako default pre všetky multi-step RUN.
2. **Dependency installation verification** — `RUN poetry install ... && test -x .venv/bin/uvicorn` (explicit binary check after install).
3. **Poetry strict mode** — `--no-interaction --ansi` plus check exit codes.

---

## Finding 4 — My advisory role failure modes

**Severity:** P2 (my system gap — Director said memory rule isn't the right solution)

**Symptoms počas 8-dňového sprintu:**

1. **Accepted "P-2 local-only" agent claim bez verifikácie** — agents reportovali "Žiadny push (local-only per P-2)" v každom DONE message. Ja som to akceptoval 8 dní bez overenia. "P-2" nikde explicit dokumentované v ICC štandardoch ani agent charters.
2. **Accepted "NEX Studio Implementer agent neexistuje" predpoklad** (objavené 2026-05-21 pri diskusii o pridaní Implementer agenta pre NEX Studio refactor) — Director navrhol pridať Implementer agenta, ja som potvrdil bez overenia `ls .claude/agents/`. Realita: Implementer + Designer + Auditor charters už **existovali** od 12-Máj (510 LOC Implementer charter). Druhá inštancia rovnakého patternu ako #1.
3. **Accepted MÁGERSTAV pre-deploy gates kategorizáciu pre buildable + bootable** — toto je mojich `feedback_continuous_improvement` + `feedback_quality_first` memory failure.
4. **Nikdy nezachytil "ale beží toto v skutočnosti?" počas release procedure** — Audítor robil Activity 4 audit report + git tag, ja som blindly relayoval bez signal-u Directorovi že žiadny smoke test prebehol.
5. **Generated variant menus s obviously-bad options** (napr. Variant C "skip rebuild, test old version") — porušenie memory `feedback_quality_first`.

**Recurring pattern:** Symptoms #1 a #2 sú **2 inštancie rovnakého anti-patternu** — "accept situation bez verifikácie cez konkrétny tool call". Nie one-off — systémový gap v advisory disciplíne. Riešenie nie je nová memory pravidlo (per Director's preference), ale **disciplína overovať pred akceptovaním**:
- Claim o filesystem → `ls`, `find`, `cat`
- Claim o agent existence → `ls .claude/agents/`
- Claim o git remote → `git remote -v`
- Claim o policy → `grep` v ICC docs + agent charters

**Improvements (NIE memory rules, ale disciplína):**

Director's clear preference: "nemyslím si že riešením je pre teba uložiť pravidlo". Memory pravidlá pre tieto chyby **už existujú** — len ich neaplikujem dôsledne. Reálne riešenie:

- **Pre-flight check pri každej advisory turn**: má action ktorú odporúčam aspoň 1 reálny use case kde je najlepším riešením? Ak nie → eliminate pred ponukou.
- **Verification disciplína pre agent claims**: pri každom "agent reportuje X bez authoritative source" → STOP, verify, NIE relay.
- **Reality check signál pri release moments**: pri každom "PASS / DONE / RELEASED" → "beží toto v skutočnosti? Reproducible? End-to-end test?" → ak nie, signal Directorovi pred ďalším krokom.

Toto nie sú nové memory pravidlá — sú to mental checks ktoré aplikujem dôslednejšie pri každej odpovedi.

---

## Next steps

1. **Tieto findings sa zachytávajú teraz** (per Director's request "zaznamenať findings") — fix sa robí v separátnom cycle.
2. **NEX Inbox v0.2.0 backlog** má P0 release-gate bugs (sekcia 0 backlog-u) — fix tam.
3. **NEX Studio v0.2.0 backlog** (TBD location) má 3 NEX Studio improvements (Findings 1-3) — fix tam.
4. **Director rozhoduje** kedy a v akom poradí riešiť. Aktuálny stav: pause, advisory session pokračuje s ďalšími otázkami Director-a.
