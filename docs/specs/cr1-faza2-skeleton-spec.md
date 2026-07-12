# CR-1 — Fáza 2 „chodiaca kostra" — dev-spec

**Projekt:** NEX Studio Visual (v4) · **Status:** NA SCHVÁLENIE · **Autor:** Dedo · **Dátum:** 2026-07-12
**Nadväzuje na:** `docs/specs/nex-studio-visual-build-plan.md` (CR-1) · discovery mapa 2026-07-12.

## 1. Cieľ (walking skeleton)

Medzi **Špecifikáciu (Fáza 1)** a **Programovanie** vložiť novú fázu **Vizuálna konzultácia** a v nej dokázať jeden ucelený cyklus: projekt s **jedným FE vizuálom** sa zobrazí **živo** v cockpite, Director požiada o zmenu v chate, **AI ju aplikuje**, a zmena sa **premietne za <1 s** (HMR, bez rebuildu). Happy-path, jeden vizuál. Po schválení sa postúpi do Programovania.

**Nie je cieľom CR-1** (neskoršie CR): viac vizuálov/typy (CR-2), reálne dáta+stavy (CR-3), dátový kontrakt (CR-4), auto-fill spec (CR-5), walkable navigácia (CR-6), zamknutý vstup buildu (CR-7).

## 2. Stavebné bloky (z discovery)

- **Pipeline stages** — `STAGE_VALUES` (`backend/db/models/pipeline.py:52`), `STAGE_ORDER`/`STAGE_ACTOR`/`STAGE_TIMEOUT` + `run_dispatch` router + `_settle_phase_boundary` (`orchestrator.py`). Vloženie stage = ohraničená úprava + Alembic migrácia CHECK constraintov + FE openapi regen.
- **AI edituje projekt in-place** cez full-auto `claude -p` v `/opt/projects/<slug>` (`claude_agent.py`); `_run_build_round` je predloha pre `_run_vizual_round`.
- **Sandbox dnes** = read-only consult sidecar (`consult_sandbox.py`, `docker run --rm`, `:ro`, žiadny served port) + prod Traefik deploy. **Žiadny Vite HMR / preview** — net-new.
- **Cockpit** = `RiadiaceCentrumPage.tsx` (grid chat + plán rail); **žiadny iframe/preview** komponent — net-new.
- **Servovanie appky** = ťažký per-customer Traefik deploy; **nič neexponuje rozrobenú verziu mid-pipeline** — to je práve medzera, ktorú Fáza 2 rieši.

## 3. Dizajn

### A. Pipeline: nová stage `vizual`
- `STAGE_VALUES` → `("priprava","navrh","vizual","programovanie","verifikacia","done")`. Alembic migrácia: ALTER `ck_pipeline_state_current_stage` + `ck_pipeline_message_stage`.
- `STAGE_ORDER` (vloži `vizual` po `navrh`), `STAGE_ACTOR["vizual"]="ai_agent"`, `STAGE_TIMEOUT["vizual"]`, `run_dispatch` nový branch `stage=="vizual" → _run_vizual_round`.
- Boundary: `navrh→vizual` a `vizual→programovanie` sú `schvalit` schvaľovacie body (Director schváli vizuál pred buildom). `FAST_FIX_STAGE_ORDER` `vizual` **preskakuje** (fast-fix nedotknutý).
- FE: regen `pipeline.generated.ts` + PhaseBar/labely rozšíriť o `Vizuál`.

### B. AI vizual round — `_run_vizual_round` (zrkadlo `_run_build_round`)
- Dispatch cez `invoke_agent_with_parse_retry(role=AI_AGENT_ROLE, stage="vizual")`, full-auto `claude -p` proti `/opt/projects/<slug>/frontend/`.
- Vstup = Director-požiadavka (relayovaná z Riadiaceho centra). AI upraví FE zdroje (obrazovky z **nex-shared** kitu, mock dáta), commitne, vráti sa `awaiting_manazer`. HMR odzrkadlí zmenu.
- Predpoklad: projekt má pri vstupe do `vizual` **spustiteľný FE scaffold** (Vite + nex-shared zo scaffoldera). CR-1 to overí/zabezpečí.

### C. Živý dev-server sandbox (NET-NEW — jadro CR-1)
- **Kontajner na projekt-vizuál:** `node:20`, `npm install` (raz), `npm run dev -- --host 0.0.0.0 --port 5173`, s **bind-mountom `/opt/projects/<slug>/frontend`** → Vite HMR sleduje `frontend/src`; edit AI = reflexia <1 s, žiadny rebuild.
- **Izolácia** (vzor podľa consult sidecaru): vlastná sieť, žiadny docker.sock / `/opt/customers` / `/opt/uat` / `/opt/infra` / credentials. Jediné mounty: `frontend` (rw pre HMR), node_modules volume.
- **Mock backend:** pre skeleton FE beží s **mock dátami** (MSW / fixtures vo FE), žiadny reálny backend. (Reálne dáta = CR-3.)
- **Servovanie do cockpitu:** **Traefik efemérna route** `https://vizual-<slug>.isnex.eu` (znovu použije uat_provisioner Traefik vzor) → HTTPS, iframe-friendly. *(Rozhodnutie 1 nižšie.)*
- **Lifecycle:** vstup do `vizual` → spin-up sandbox; výstup (schvalit → programovanie) alebo pause → teardown. Idempotentný (re-entry pripojí bežiaci).

### D. Cockpit „Vizuál" plocha (NET-NEW)
- Nová route `/vizual` + ľavý-nav vstup (vzor `UatPage`/`ProdPage` thin wrapper). Zobrazí **iframe** živého dev-servera (monitor 2). *(Rozhodnutie 2 nižšie: samostatná route vs panel v Riadiacom centre.)*
- Chat (požiadavky na zmeny) ostáva v Riadiacom centre (monitor 1) — reuse existujúci relay + WS board. Žiadne nové comms.
- Stav: „sandbox sa spúšťa / beží / chyba" honest strip; link „Otvoriť vizuál" keď je route live.

### E. Vstup/výstup
`navrh` (schválená Špecifikácia+Návrh) → **schvalit** → `vizual` (sandbox up) → Director walk+chat, AI aplikuje (HMR) → **schvalit** (vizuál OK) → sandbox teardown → `programovanie` (existujúci build beží ďalej).

## 4. Kľúčové rozhodnutia (na tvoj odklep)

1. **Servovanie dev-servera:** **Traefik efemérna route** `vizual-<slug>.isnex.eu` (odporúčam — HTTPS, iframe-friendly, konzistentné s UAT) **vs** priamy host-port (jednoduchšie, ale HTTP/localhost v iframe je krehké).
2. **Cockpit plocha:** **samostatná route `/vizual`** (odporúčam — presne „monitor 2 = vizuál", Director si ju otvorí na druhom monitore) **vs** tretí stĺpec/tab v Riadiacom centre (všetko na jednom monitore).

## 5. Riziká + mitigácia
- **Živý dev-server v izolovanom kontajneri (#4)** — hlavný risk. Mitigácia: **PoC hneď ako prvá pod-úloha CR-1** (spustiť Vite HMR v kontajneri s bind-mountom + overiť <1 s reflexiu + iframe), skôr než sa stavia stage/round.
- **npm install latencia** pri spin-up — cache node_modules vo volume; „sandbox sa pripravuje" stav.
- **iframe cross-origin** (cockpit :9217 vs vizual route) — Traefik HTTPS route + povoliť embedding (CSP/frame-ancestors) pre cockpit origin.

## 6. Pod-úlohy (poradie v rámci CR-1)
1. **PoC živého dev-servera** — kontajner + Vite HMR + bind-mount + iframe reflexia <1 s (rieši hlavný risk skoro).
2. **Sandbox service** — `vizual_sandbox.py` (spin-up/teardown/status, izolácia, Traefik route), vzor podľa `consult_sandbox.py` + `uat_provisioner` Traefik.
3. **Pipeline stage `vizual`** — enums, STAGE_*, `run_dispatch` branch, boundary, Alembic migrácia, FE openapi regen + PhaseBar.
4. **`_run_vizual_round`** — AI aplikuje zmenu do FE (full-auto), mock dáta z nex-shared.
5. **Cockpit „Vizuál" plocha** — route + iframe + honest stav.
6. **E2E lepenie + test** — projekt prejde Špecifikácia → Vizuál (žiadaj zmenu, vidíš ju <1 s) → schválenie → Programovanie.

## 7. Done-kritériá
- Projekt prejde `navrh → vizual → programovanie`.
- Vo Fáze 2: cockpit „Vizuál" ukáže živý FE; Director napíše požiadavku, AI ju aplikuje, zmena sa premietne **<1 s** bez rebuildu.
- Sandbox je izolovaný (žiadny prístup k customers/uat/infra/docker.sock) a po výstupe sa upratuje.
- v3 aj ostatné v4 fázy nedotknuté; plný `pytest` + FE type-check zelené; CI zelené.
