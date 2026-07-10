# Nastavenia — slovenské názvy, ľudské popisy + merné jednotky (Fáza 2)

Director-requested 2026-07-10. The Systém tab of Nastavenia shows each setting with the RAW KEY as the
title (`claude_design_doc_timeout_seconds`), an ENGLISH description, and NO unit. Make it Slovak + humane:
**(1)** a human Slovak NAME as the title, the raw key kept only as small grey info; **(2)** the description
in plain Slovak; **(3)** a unit suffix after each editor so the Manažér knows what they're entering.

The per-setting card is rendered by the SHARED `SystemSettingsPanel` in **nex-shared** (nex-studio's
`SettingsPage.tsx` is a thin adapter). So this spans: nex-studio backend (the Slovak content + two new
fields), **nex-shared** (render label/key-info/unit + a release), nex-studio FE (bump the pin + type). The
Slovak content below is AUTHORED — transcribe it verbatim, do NOT re-translate. Branch `v2.0.0-dev`.

## Part A — nex-studio backend (`backend/services/system_setting.py` + schema)

1. `_Default` dataclass — add two fields: `label: str` (human Slovak name; place it among the no-default
   fields, i.e. BEFORE `value_type` which has a default) and `unit: str = ""` (may be empty). Final shape:
   `value: str`, `description: str`, `label: str`, `value_type: SystemSettingValueType = "string"`,
   `unit: str = ""`.
2. For every one of the 35 `DEFAULT_SETTINGS` entries: set `label=`, REPLACE the English `description=` with
   the Slovak one, and set `unit=` — all from the table below (verbatim). Keep `value` + `value_type`.
3. `backend/schemas/system_setting.py` `SystemSettingRead`: add `label: str = ""` and `unit: str = ""`.
4. **Read path (critical):** `label`/`unit`/Slovak-`description` are REGISTRY metadata, never stored per-row.
   - `_to_read_from_default(key, default)` → also pass `label=default.label, unit=default.unit`.
   - `_to_read_from_row(row, username)` → look up `meta = DEFAULT_SETTINGS.get(row.key)` and pass
     `label=meta.label if meta else row.key`, `unit=meta.unit if meta else ""`,
     `description=meta.description if meta else row.description`. The ROW supplies only value + updated_* +
     `is_default=False`. (A stored override must still show the registry's Slovak label/description/unit,
     not a stale row.description.)

### Slovak content — all 35 settings (key → label → unit → description)

| key | label | unit | description |
|---|---|---|---|
| github_org | GitHub organizácia | | Organizácia na GitHube, ktorou sa predvyplnia adresy repozitárov na formulári nového projektu (v tvare „organizácia/názov-projektu"). |
| miera_autonomie | Miera autonómie | | Ako často sa AI agent zastaví a počká na tvoje schválenie: plná / len na konci / pri kľúčových bodoch / po každej fáze. Dá sa prepísať pre jednotlivý projekt aj konkrétnu stavbu. |
| programovanie_token_stop_millions | Limit tokenov na stavbu | mil. tokenov | Keď spotreba tokenov počas programovania prekročí túto hranicu, systém sa slušne zastaví na najbližšej hranici úlohy a upozorní ťa. 0 = bez limitu (beží naraz). |
| claude_stream_timeout_seconds | Časový limit toku AI | sekúnd | Najdlhší čas, počas ktorého môže bežať jeden tok AI, kým sa proces ukončí. Zvýš, ak dlhé výpisy celej špecifikácie občas narazia na limit. |
| claude_design_doc_timeout_seconds | Časový limit generovania návrhovej dokumentácie | sekúnd | Časový limit na vygenerovanie návrhových dokumentov (BEHAVIOR.md / DESIGN.md) zo schválenej vývojovej dokumentácie. |
| claude_task_plan_timeout_seconds | Časový limit generovania plánu úloh | sekúnd | Časový limit na vygenerovanie plánu úloh (Epika → Funkcia → Úloha). |
| github_api_timeout_seconds | Časový limit GitHub API | sekúnd | Časový limit HTTP volaní na GitHub (overenie, vytvorenie a zmazanie repozitára). |
| conversation_history_limit | Limit histórie konverzácie | správ | Koľko posledných správ konverzácie s Architektom sa načíta ako kontext pre AI. Staršie správy sa uchovávajú, ale AI sa už neposielajú. |
| design_doc_max_chars | Maximálna dĺžka návrhovej dokumentácie | znakov | Najviac znakov z DESIGN.md, ktoré sa vložia do AI promptu pri programovaní úlohy, aby sa nezahltil kontext. |
| access_token_expire_minutes | Platnosť prihlásenia | minút | Ako dlho platí prihlásenie, kým sa musíš znova prihlásiť (480 = 8 hodín). |
| port_range_min | Najnižší port pre projekty | | Najnižšie číslo portu, ktoré NEX Studio prideľuje projektom. Zmena za behu je riziková pre existujúce nasadenia. |
| port_range_max | Najvyšší port pre projekty | | Najvyššie číslo portu v rozsahu pre komerčné projekty. |
| port_block_size | Veľkosť bloku portov | portov | Koľko portov dostane jeden projekt. Štandard: bloky po 10 (backend / frontend / databáza + 7 rezervných). |
| default_source_path_template | Predvolené umiestnenie zdrojového kódu | | Kam sa štandardne uloží zdrojový kód projektu. „{slug}" sa nahradí názvom projektu. Podľa konvencie žijú nové projekty v /opt/projects/<názov>/. |
| default_kb_path_template | Predvolené umiestnenie znalostnej bázy | | Predvolený priečinok pre živé dokumenty jednotlivého projektu. |
| template_init_script_path | Cesta k inicializačnému skriptu šablóny | | Úplná cesta k skriptu init.sh, ktorý pri vytvorení projektu založí jeho priečinok a základné súbory. Prázdne = automatické zakladanie je vypnuté. |
| template_init_timeout_seconds | Časový limit inicializácie šablóny | sekúnd | Najdlhší čas na dobehnutie skriptu init.sh. Bežné založenie trvá do 5 sekúnd; 60 pokrýva prvé vytváranie priečinkov a Docker. |
| reserved_port_ranges | Rezervované rozsahy portov | | Rozsahy portov vyhradené pre projekty spravované mimo NEX Studia (oddelené čiarkou, napr. „10110-10159"). Z týchto rozsahov systém port nepridelí. Prázdne = žiadne rezervácie. |
| developer_hourly_rate | Hodinová sadzba vývojára | € / hod | Priemerná hodinová sadzba ľudského vývojára pre porovnanie na stránke Metriky. 0 = nenastavené → návratnosť sa nezobrazí (nikdy sa nevymyslí číslo). |
| api_price_input_per_mtok | Cena za vstupné tokeny | $ / mil. tokenov | Cena Claude API za 1 milión vstupných tokenov — pre výpočet nákladov na stránke Metriky. 0 = nenastavené → náklad sa nezobrazí. |
| api_price_output_per_mtok | Cena za výstupné tokeny | $ / mil. tokenov | Cena Claude API za 1 milión výstupných tokenov — pre výpočet nákladov na stránke Metriky. 0 = nenastavené → náklad sa nezobrazí. |
| metrics_minutes_per_mtok_priprava | Ľudský čas — fáza Príprava | min / mil. tokenov | Koľko minút ľudskej práce zodpovedá 1 miliónu tokenov vo fáze Príprava. 0 = nenastavené → čas a náklad tejto fázy sa nezobrazia. |
| metrics_minutes_per_mtok_navrh | Ľudský čas — fáza Návrh | min / mil. tokenov | Koľko minút ľudskej práce zodpovedá 1 miliónu tokenov vo fáze Návrh. 0 = nenastavené → nezobrazí sa. |
| metrics_minutes_per_mtok_programovanie | Ľudský čas — fáza Programovanie | min / mil. tokenov | Koľko minút ľudskej práce zodpovedá 1 miliónu tokenov vo fáze Programovanie. 0 = nenastavené → nezobrazí sa. |
| metrics_minutes_per_mtok_verifikacia | Ľudský čas — fáza Verifikácia | min / mil. tokenov | Koľko minút ľudskej práce zodpovedá 1 miliónu tokenov vo fáze Verifikácia. 0 = nenastavené → nezobrazí sa. |
| metrics_hourly_wage_priprava | Hodinová mzda — fáza Príprava | € / hod | Hodinová mzda ľudskej práce pre fázu Príprava (na výpočet ľudského nákladu). 0 = nenastavené → nezobrazí sa. |
| metrics_hourly_wage_navrh | Hodinová mzda — fáza Návrh | € / hod | Hodinová mzda ľudskej práce pre fázu Návrh. 0 = nenastavené → nezobrazí sa. |
| metrics_hourly_wage_programovanie | Hodinová mzda — fáza Programovanie | € / hod | Hodinová mzda ľudskej práce pre fázu Programovanie. 0 = nenastavené → nezobrazí sa. |
| metrics_hourly_wage_verifikacia | Hodinová mzda — fáza Verifikácia | € / hod | Hodinová mzda ľudskej práce pre fázu Verifikácia. 0 = nenastavené → nezobrazí sa. |
| api_price_input_per_mtok_opus | Cena vstupu — Opus | $ / mil. tokenov | Cena za 1 milión vstupných tokenov pre modely Opus. Ak je 0, použije sa všeobecná cena vstupu. |
| api_price_output_per_mtok_opus | Cena výstupu — Opus | $ / mil. tokenov | Cena za 1 milión výstupných tokenov pre modely Opus. Ak je 0, použije sa všeobecná cena výstupu. |
| api_price_input_per_mtok_sonnet | Cena vstupu — Sonnet | $ / mil. tokenov | Cena za 1 milión vstupných tokenov pre modely Sonnet. Ak je 0, použije sa všeobecná cena vstupu. |
| api_price_output_per_mtok_sonnet | Cena výstupu — Sonnet | $ / mil. tokenov | Cena za 1 milión výstupných tokenov pre modely Sonnet. Ak je 0, použije sa všeobecná cena výstupu. |
| api_price_input_per_mtok_haiku | Cena vstupu — Haiku | $ / mil. tokenov | Cena za 1 milión vstupných tokenov pre modely Haiku. Ak je 0, použije sa všeobecná cena vstupu. |
| api_price_output_per_mtok_haiku | Cena výstupu — Haiku | $ / mil. tokenov | Cena za 1 milión výstupných tokenov pre modely Haiku. Ak je 0, použije sa všeobecná cena výstupu. |

## Part B — nex-shared (`/opt/projects/nex-shared`) render + release

1. `src/settings-types.ts` `SystemSettingRead` — add `label?: string | null;` and `unit?: string | null;`
   (optional, backward-compatible; other apps that don't send them still render exactly as today).
2. `src/SystemSettingsPanel.tsx` per-setting card (the `rows.map((s) => …)` block, ~line 138):
   - **Title** = `s.label || s.key` in the human style (drop the `font-mono` when a label exists; a label is
     prose, not a key). Currently: `<div className="text-sm font-medium … font-mono">{s.key}</div>`.
   - **Raw key as small info** — under the title, only when `s.label` is present (else the key IS the title):
     one small muted monospace line combining key + type, e.g.
     `<div className="text-[10px] text-[var(--color-text-muted)] font-mono mt-0.5">{s.key} · {s.value_type}</div>`.
     Keep showing `value_type`; fold it into this info line (don't render the separate uppercase value_type
     line twice).
   - **Unit suffix** — render `s.unit` right after the number/text input (not for the checkbox), e.g. wrap the
     input in `<div className="flex items-center gap-2">` and append
     `{s.unit && <span className="text-xs text-[var(--color-text-muted)] shrink-0">{s.unit}</span>}`. The unit
     is a passive hint, not part of the value.
   - Description stays `{s.description && …}` (now Slovak from the backend). No other behaviour change (save
     button, dirty/flash, categories all unchanged).
3. Bump `package.json` version `0.12.0` → `0.13.0`, `npm run build` (tsup) to regenerate `dist/` (COMMIT the
   dist — it's the git-dep artifact), commit, `git tag v0.13.0`, push branch + tag.

## Part C — nex-studio FE wire-up
1. `frontend/package.json` — bump the pin `github:rauschiccsk/nex-shared#v0.11.0` → `#v0.13.0`; run
   `npm install` so `package-lock.json` locks the new commit.
2. `frontend/src/types/system_setting.ts` `SystemSettingRead` — add `label?: string | null;` +
   `unit?: string | null;` (mirror the backend schema).
3. `SettingsPage.tsx` needs NO change — it already passes the API rows straight to the panel; the new fields
   ride through.

## Verify (report evidence — files + test counts)
- Backend: FULL `.venv/bin/python -m pytest -q` from the REPO ROOT + `ruff format --check .` + `ruff check .`.
  Add/adjust a system_setting service test asserting a read (default AND overridden-row path) carries the
  Slovak `label`, `unit`, and Slovak `description` from the registry. Pre-existing full-suite baseline:
  ~13 order-dependent isolation errors are NOT yours (see the fullsuite-isolation note) — introduce no NEW ones.
- nex-shared: `npm run build` clean; the panel still renders a setting WITHOUT label/unit unchanged
  (backward-compat) AND with label/unit shows label-title + key-info + unit-suffix (a unit test if the repo
  has the harness, else note the manual check).
- nex-studio FE: `npm run build` + lint + test GREEN with `nex-shared#v0.13.0` resolved.
- Do NOT deploy and do NOT bump the nex-studio app version — Dedo deploys via scripts/deploy-v3.sh (which
  stamps the version + publishes the Aktualizácie note). Leave changes committed on `v2.0.0-dev`; report the
  nex-shared tag + the commits.
