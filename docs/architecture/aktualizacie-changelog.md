# DESIGN: „Aktualizácie" — per-version user-facing changelog

**Model (LOCKED, Director option A):** pipeline generuje user-facing release notes per verzia → Director ich reviewuje pred `released`. Žiadne ručné písanie, žiadne fabrikované zmeny, reuse existujúcich kúskov, NEX Studio = vzor.

**Status:** READ-ONLY design. Žiadny súbor nebol zmenený. Všetky cesty a riadky overené proti živému stromu (`/opt/projects/nex-studio`, `/home/icc/knowledge/templates/claude-project`).

---

## 0. Rozhodnutie naprieč celým dizajnom (serving) — load-bearing

Rozhodnutie serving je **determinované faktami, nie preferenciou** — preto ho neeskalujem ako otvorenú otázku:

1. **FE-bundle (Vite `import.meta.glob`) je MŔTVE pri starte.** FE Docker build context je `./frontend` (`docker-compose.yml:115`), Dockerfile robí `COPY . .` z `frontend/`. `docs/` je súrodenec `frontend/`, teda **mimo build kontextu** — `import.meta.glob("../../docs/**")` nevidí žiadne súbory.
2. **`docs/` nie je v žiadnom image dnes.** Backend runtime stage kopíruje len `backend/ alembic.ini migrations/` (`backend/Dockerfile:146-148`). nginx servíruje len `dist/` (žiadny `location /docs/`).

**Rozhodnutie: BE endpoint, ktorý číta committed RELEASE_NOTES z image-baked kópie.** Konkrétne (sekcia 4): pridať úzky `COPY` release-notes súborov do `backend/Dockerfile` + verejný (non-auth) `GET /api/v1/release-notes`. Toto je jediná možnosť, ktorá:
- funguje pre deployed app (číta z vlastného containera, nie z NEX Studio `/opt/projects` mount-u),
- je honest (číta presne to, čo sa committlo),
- má precedens v dvoch existujúcich vzoroch: `backend/services/project_specs.py` (číta `.md` z disku, path-traversal guard) a `backend/api/routes/health.py:40` (verejný, bez auth, vystavuje `app_version`).

Token-launch app bez vlastného backendu (nex-inbox model) je out-of-scope pre v1 (sekcia 6, Asymetria).

---

## 1. Content generation — pipeline krok, ktorý produkuje notes

### Kto a kde

**Autor: Designer node** (Coordinator/Designer v cockpite), na **rovnakých dvoch bodoch, kde už dnes píše `CHANGES.md`** — žiadny nový pipeline node, žiadna engine zmena.

| Flow | Bod produkcie | Charter ref (template) |
|---|---|---|
| NEW_PROJECT | Po Gate E close, spolu s `CHANGES.md` | designer `CLAUDE.md.tmpl` (Po Gate E) |
| CHANGE_REQUEST | step 4 (CHANGES.md changelog), do Delta-Gate bundle | designer `CLAUDE.md.tmpl §7 step 4-5` |
| BUG_FIX Class 2/3 | sleduje CR flow → ten istý bod | designer `CLAUDE.md.tmpl §8 step 4` |

**Prečo Designer, nie engine pri release PASS:** alternatíva (generovať notes v orchestrator-i pri release `gate_report` PASS) by viedla k engine-written doc, čo je nekonzistentné s tým, ako `CHANGES.md` vzniká (Designer charter, **nie** engine; jediný engine-written version doc je best-effort `customer-dialogue.md`). Designer-authorship dáva: (a) konzistenciu s existujúcim doc-tree modelom, (b) `--resume` session, kde Designer už drží oba honest inputy, (c) commit ride na existujúcom hand-off `git add docs/specs/versions/v<X>/` — RELEASE_NOTES.md sa committne zadarmo, žiadny druhý push. Sekvenčný caveat (generate-at-PASS vs push pár riadkov neskôr) tým úplne mizne.

### Inputy (oba honest, žiadna fabrikácia)

1. **Zámer/hodnota** → `docs/specs/versions/v<X>/customer-requirements.md` (Director-written, Designer ho číta v Discovery).
2. **Reálne dodané zmeny** → `CHANGES.md`, ktorý Designer práve napísal (per-CR/BUG audit trail) + EPIC/BUG scope verzie.

### Output

`docs/specs/versions/v<X>/RELEASE_NOTES.md` — plain end-user slovenčina, „Čo je nové", markdown. **Žiadne ICC kódy (CR-NS-xxx), root-cause, názvy súborov/funkcií/testov** — to ostáva v `CHANGES.md`. Honesty pravidlo do charteru: každý bullet v RELEASE_NOTES MUSÍ mať pokrytie v reálne dodanom EPIC/BUG/CR; žiadna feature, ktorá sa nedodala. Vynútenie tohto pravidla je mandatory Auditor check (sekcia 7 honesty + sekcia 6 Fáza 3).

---

## 2. Director review/edit — fold do existujúceho approval bodu (žiadny nový gate)

**Fold do `uat_accept`** — Director sa verzie dotýka práve raz, po tom, čo je nasadená na UAT a notes popisujú **to, čo reálne shiplo** (honesty). Overené flow logic v `backend/services/orchestrator.py`:

1. Release stage PASS → engine auto-publish → auto-UAT-deploy → settle `status="awaiting_director"`, `next_action="Director: over a akceptuj…"` (`orchestrator.py:2769-2770`; pri projekte bez UAT slug settle s rovnakým awaiting_director stavom).
2. RELEASE_NOTES.md je už committed (Designer ho napísal pred release, sekcia 1).
3. **Review/edit bez nového action verbu:** na settled release stave `determine_available_actions` (`orchestrator.py:320`) ponúka base `{ask, return}` (`:345`) + `uat_accept` (`:379`). Director použije existujúci **`return`** (handler `:663`) — re-dispatchne Coordinatora s framed komentárom („Director vrátil: …, prepracuj RELEASE_NOTES"), rovnaký propose→review→approve pattern ako Gate E (`return` + `gate_e` vetva `:1060`). Coordinator regeneruje notes, committne, settne znova.
4. Keď je Director spokojný → **`uat_accept`** → `released` + PROD per UAT acceptance gate.

**Surfacing — KDE Director vidí finálny text (povinné, nie hand-wave):** pri release settle (`awaiting_director`) Coordinator-ov settle summary / `next_action` MUSÍ obsahovať buď (a) rendered finálne RELEASE_NOTES inline, alebo (b) board link na `/updates` preview, resp. na committed súbor `docs/specs/versions/v<X>/RELEASE_NOTES.md`. Director tak číta **reálne shipnutý text** pred `uat_accept`. Bez definovaného surface by „Director reviews" zostalo neoveriteľné. Notes sa **NEsurfacujú** na Delta Gate (pre-build) — tam by popisovali plánovanú-nie-dodanú scope → honesty violation. Preto je review bod výlučne post-build (`uat_accept`).

**Charter zmena (jediný blok):** coordinator-charter `templates/coordinator-charter.md` (Krok 7 PASS, `:209-216`) dostane pravidlo: release settle summary surfacuje per-version RELEASE_NOTES (rendered alebo link) pre Director review/edit pred flipom na `released`.

---

## 3. Storage — committed per-version súbor

- **Path:** `docs/specs/versions/v<X>/RELEASE_NOTES.md` (sibling `CHANGES.md`).
- **Format:** markdown, plain end-user SK.
- **NIE `Version.description`** (odmietnutie): je už sémanticky obsadené ako dev „Release notes / scope" (`schemas/version.py`), žije len v NEX Studio control-plane DB (deployed app k nej nemá prístup), a je `Text` editovaný cez PATCH bez immutability. Súbor v repo: ships-with-app, full diff history, immutable-po-release zadarmo.
- **Immutability:** rozšíriť charter pravidlo „CHANGES.md immutable po release" aj na RELEASE_NOTES (historický záznam).
- **Konvencia obsahu:** H2 nadpis = verzia, napr. `## v0.2.0`, pod ním user-facing bullets. **Dátum sa do nadpisu NEpíše ako autoritatívny zdroj** — endpoint ho berie z DB (sekcia 4), nie z parsovaného textu. Striktná H2 štruktúra teda nie je nutná na korektnosť dátumu; je len kozmetika pre čitateľnosť committed súboru.

---

## 4. Serving — ako deployed app vystaví VLASTNÝ changelog

**Backend endpoint čítajúci image-baked committed notes**, spojený s versions DB pre ordering + dátum.

### 4a. Baknúť LEN release-notes do backend image (úzky scope — security)

Pridať do **`backend/Dockerfile`** (autoritatívny — `docker-compose.yml:27-28` ho používa; root `Dockerfile` je vestigiálny, compose ho nereferencuje — flag na potvrdenie, needitovať ho ako load-bearing). Úzky COPY, **NIE celé `docs/specs`**:

```dockerfile
# release notes only — NEVER the full spec tree (development-spec, F-xxx, customer-dialogue)
COPY --chown=andros:andros docs/specs/versions/*/RELEASE_NOTES.md ./docs/specs/versions/
```

Ak by glob-COPY nezachoval adresárovú štruktúru spoľahlivo, alternatíva je stage-nuť release-notes do dočasného adresára v build context-e a COPY-núť ten. Cieľ je nemenný: **do customer-facing image ide len `RELEASE_NOTES.md`, nič iné z `docs/specs`** (dnes `docs/specs` = 960K interného dev obsahu — architecture, charters — ktorý do deployed app nepatrí). Root `.dockerignore` (`/opt/projects/nex-studio/.dockerignore`) docs nevylučuje, takže build context `.` ich vidí.

### 4b. Verejný endpoint

Nový `backend/api/routes/release_notes.py`, mounted **bez auth** (changelog je user-facing, žiadne credentials — precedens `health.py:40`):

```
GET /api/v1/release-notes
  → [{ "version": "v0.2.0", "released_at": "2026-06-20", "markdown": "..." }, ...]  newest-first
```

Service vrstva `backend/services/release_notes.py` (mirror path-traversal guard z `project_specs.py:298-311`, ale číta z **vlastného** containera, nie z `/opt/projects` mount):
- `DOCS_ROOT = Path(__file__).../docs/specs/versions` (relatívne k baknutému image),
- **hard-coded leaf glob `v*/RELEASE_NOTES.md`** — žiadny caller-supplied path param (na rozdiel od `project_specs.read_content(slug, path_within_project)`), takže neexistuje path-traversal povrch; defenzívne aj tak aplikuj `.resolve()` + `relative_to(DOCS_ROOT)` guard,
- **`released_at` z DB, NIE z nadpisu:** join file-discovery (glob → ktoré verzie sa zobrazia) s versions tabuľkou pre `version_number` ordering (`version_number DESC`, rovnaké ako `list_versions`, `backend/api/routes/versions.py:108-119`) + `Version.release_date` (`backend/db/models/versions.py:38`, `Column(Date)`). **File presence driv-uje KTORÉ verzie sa objavia; DB driv-uje ordering + dátum.** Caveat: deployed generovaná app má vlastnú DB s vlastnou versions tabuľkou — ak riadok pre danú `version_number` chýba, **graceful fallback na file mtime** (nikdy parse z H2). Nikdy nezlyhať na chýbajúcom DB riadku.

**Prečo nie reuse `project_specs` endpointu:** `/api/v1/project-specs/content` je `require_ri_role`, číta `/opt/projects/<slug>` mount → NEX-Studio-internal, deployed app nemá ani mount ani `ri` JWT. Nový dedikovaný endpoint je nutný.

**FE → BE väzba:** `frontend/nginx.conf` už proxuje `/api/` na backend, FE volá `api.get("/api/v1/release-notes")` cez existujúci `createApiClient`. Žiadna nginx zmena.

---

## 5. FE — „Aktualizácie" nav + page

### 5a. Nav item NAD „Nastavenia"

`frontend/src/components/layout/Sidebar.tsx` — vložiť **bezprostredne pred** `<SectionLabel label="Nastavenia" />` (overené `:205`):
```tsx
<NavItem icon={<IconUpdates />} label="Aktualizácie" active={isActive("/updates")} onClick={() => navigate("/updates")} />
<SectionLabel label="Nastavenia" />
```
Glyph `IconUpdates` (napr. `<NavIcon glyph="✨" />`) pridať k ostatným helperom (`:28-41`). Primitíva `NavItem`/`SectionLabel`/`NavIcon` z `nex-shared` (`Sidebar.tsx:3`) — žiadna nová.

**Grouping decision (explicitne, nie incidentálne):** „Aktualizácie" sedí ako posledná položka skupiny **nad** `SectionLabel "Nastavenia"`, t. j. vizuálne priamo nad sekciou Nastavenia bez vlastného section headeru. Toto je zamýšľaný výstup pre splnenie Directorovho „nad Settings". (Alternatíva — fold pod jeden „Nastavenia" header — je odmietnutá, lebo Aktualizácie nie je nastavenie.)

### 5b. Route

`frontend/src/App.tsx` — import + protected route:
```tsx
<Route path="updates" element={<UpdatesPage />} />
```
SPA fallback (`nginx.conf` `/` → `index.html`) pokrýva refresh.

### 5c. Page — `frontend/src/pages/UpdatesPage.tsx` (nový)

- Fetch `GET /api/v1/release-notes` cez `@/services/api/...`.
- **Per-version, newest-first, expandable:** každá verzia = `<Card>` (z `nex-shared`) s `<details>`/`<summary>` (alebo controlled accordion); summary = `v0.2.0 — 20. jún 2026` (dátum z `released_at` poľa response); najnovšia default expanded, ostatné collapsed.
- **Markdown render — v1 inline idiom (žiadna nová závislosť):** skopíruj existujúci inline block z `ProjectSpecsPage.tsx` / `KnowledgeBasePage.tsx` — `ReactMarkdown remarkPlugins={[remarkGfm]}` + `CodeBlock` override (`@/components/markdown/CodeBlock`) + `className="prose dark:prose-invert prose-sm max-w-none"`. `react-markdown@^10` + `remark-gfm@^4` sú už v `frontend/package.json:27,30`. **Pozn.:** `frontend/src/components/markdown/` obsahuje LEN `CodeBlock.tsx` — žiadny `<Markdown>` wrapper komponent neexistuje; „reuse idiom" = kópia inline bloku, nie import komponentu.

### 5d. Renderer DRY — INDEPENDENT follow-up, NIE prerekvizita rolloutu

ReactMarkdown je dnes v **6 call-sites**: `ProjectSpecsPage`, `KnowledgeBasePage`, `PipelineMessageBubble`, `TaskSummaryCard`, `CredentialsPage` (+ test `test_PipelineMessageBubble.test.tsx`). UpdatesPage by bol 7. inline kópia.

**Kritický fakt:** `nex-shared` je **externý publikovaný git-tag dep** (`github:rauschiccsk/nex-shared#v0.9.1`, `frontend/package.json:23`), NIE vendored v tomto repo. Promovať `<Markdown>` do `nex-shared` preto znamená: editovať **iný** git repo, cut nový tag (0.9.1 → 0.10.0), bump dep v **každom** consumer-i (NEX Studio + každá generovaná app) a rebuild. To je cross-repo release koordinácia a „každá generovaná app musí bumpnúť nex-shared" je ongoing-maintenance krok, ktorý **koliduje s full-autonomy princípom** (zero-ongoing-maintenance, žiadne per-project manuálne kroky).

**Rozhodnutie: DRY konsolidácia je samostatný, NEblokujúci follow-up** — nie prerekvizita. Keď sa raz spraví, skonsoliduje všetkých 6+ sites cez `nex-shared <Markdown>`. Rollout (sekcia 6) na ňom **NEsmie** visieť. v1 NEX Studio aj skeleton renderujú cez inline idiom.

---

## 6. Scope + rollout

**Poradie: NEX Studio FIRST (serving/FE vzor) → create-project scaffold → charters. nex-shared renderer DRY = paralelný, neblokujúci.**

### Fáza 1 — NEX Studio (vzor pre serving + FE)
1. BE: `release_notes.py` route + `release_notes.py` service + úzky `COPY .../RELEASE_NOTES.md` do `backend/Dockerfile`.
2. FE: nav (`Sidebar.tsx`) + route (`App.tsx`) + `UpdatesPage.tsx` (inline markdown idiom).
3. **Content authorship pre NEX Studio = Dedo (manuál, akceptovateľné LEN pre vzor app):** NEX Studio sa vyvíja Dedo-direct, **bez Designer agenta** (memory `project_nex_studio_dev_model`). Dedo ručne napíše `docs/specs/versions/v<aktuálna>/RELEASE_NOTES.md`. **NEX Studio teda dogfooduje LEN serving + FE vrstvu, NIE autonómnu generáciu.** Autonómny pipeline-generovaný path (Designer produkuje, Director reviewuje) sa preukáže na **generovanej app** (nex-asistent / nex-ledger), nie na NEX Studio. Tým sa odstraňuje vnútorný rozpor „NEX Studio = vzor autonómneho flow".
4. Deploy + dogfood: NEX Studio ukáže vlastný changelog na `/updates`.

### Fáza 2 — create-project scaffold (každá generovaná app dedí)
Súbory v `/home/icc/knowledge/templates/claude-project/`:
- `frontend-skeleton/src/components/layout/Sidebar.tsx.tmpl`: pridať „Aktualizácie" `<NavItem>` do **spodnej nav skupiny** s explicitným komentárom-placeholderom `{/* Settings NavItem goes directly BELOW this — keep Aktualizácie above */}`. Ordering sa **vyjadrí štruktúrou, nie prózou**: keď Designer pridá Settings item, padne pod Aktualizácie. (Skeleton dnes má „Prehľad" NavItem na `:48-50` a Settings ešte nemá.)
- `frontend-skeleton/src/App.tsx`: protected route `/updates`.
- NEW `frontend-skeleton/src/pages/UpdatesPage.tsx.tmpl` (model na `DashboardPage.tsx.tmpl`, render cez inline ReactMarkdown idiom; skeleton nesie minimálny lokálny `CodeBlock`, alebo v1 renderuje bez code-block highlightu — žiadna nex-shared väzba).
- NEW `docs/specs/versions/v0.1.0/RELEASE_NOTES.md.tmpl` (sibling `CHANGES.md.tmpl`) + `copy_file` riadok v `init.sh` hneď za CHANGES.md copy (overené `init.sh:353`).

**Backend serving konvencia — enforceable, NIE iba charter próza:** template NEMÁ backend skeleton (backend generuje Designer/Implementer per-app), takže prostá charter veta je najslabšia možná záruka pre „EVERY generated app gets it". Preto:
- **(a) `development-spec` template dostane povinnú sekciu** „Release-notes serving" → Designer VŽDY špecifikuje `GET /api/v1/release-notes` endpoint + úzky `COPY .../RELEASE_NOTES.md` do backend Dockerfile. Spec-level mandát, nie odporúčanie.
- **(b) Auditor release check pridaný do behaviorálnej acceptance suite (§2.5):** „app vystavuje `GET /api/v1/release-notes` (200, JSON array) a `/updates` route renderuje". Toto je executable gate, nie aspirácia — chytí app, ktorá má `/updates` page bez backendu (FE 404).
- Sprievodné charter zmeny (podporné, nie jediná záruka): designer `CLAUDE.md.tmpl` doc-tree + produkčné body + immutability; coordinator-charter fold review do `uat_accept`.

### Fáza 3 — honesty enforcement (mandatory, nie odporúčaný)
auditor `CLAUDE.md.tmpl` Activity-1 spec-compliance check, **MANDATORY**: každý `RELEASE_NOTES.md` bullet musí trace-ovať na dodaný EPIC/BUG/CR vo `CHANGES.md` tej verzie. Konkrétne executable: Auditor cross-referencuje RELEASE_NOTES claims proti (1) `CHANGES.md` scope a (2) reálne merged EPIC/BUG riadkom verzie; bullet bez pokrytia = FAIL „fabricated changelog claim". Pre autonómne behy Tibor/Nazar appiek je soft charter veta nedostatočná — preto mandatory, definovaný, vykonateľný.

### Fáza 4 (paralelná, neblokujúca) — nex-shared renderer DRY
Promote `<Markdown>` + `CodeBlock` do `nex-shared` (cross-repo tag bump z 0.9.1, sekcia 5d), skonsoliduj všetkých 6+ NEX Studio call-sites. **Nezdržuje Fázy 1-3.**

### Asymetria (flag, nie blocker)
Backend-less / token-launch apps (nex-inbox model) nemajú vlastný backend na hosting endpointu. v1 cieli apps s backendom (väčšina + NEX Studio + Ledger + asistent). Pre backend-less app je fallback: changelog endpoint na zdieľanej API vrstve danej app, alebo build-time injection JSON-u cez existujúci `VITE_APP_VERSION` build-arg seam. Riešiť až keď príde prvá taká app — nezdržuje vzor.

---

## 7. Backfill

**Forward by default.** Od najbližšej verzie každá nová verzia dostane RELEASE_NOTES automaticky (sekcia 1). Staré verzie bez `RELEASE_NOTES.md` endpoint jednoducho vynechá (glob ich nenájde) — žiadna chyba, page ich neukáže.

**Optional backfill kľúčových minulých verzií:** Dedo (pre NEX Studio), resp. Designer (pre generované apps) napíše `RELEASE_NOTES.md` do existujúcich `docs/specs/versions/v<stará>/` adresárov z ich `CHANGES.md` + `customer-requirements.md`. Pretože endpoint je čisto file-driven (glob `v*/RELEASE_NOTES.md`) + DB join pre dátum, backfill je len pridanie súborov — žiadna migrácia, žiadna kódová zmena, ride na ďalšom deploy. Odporúčam backfillnúť len verzie s viditeľnou user-facing hodnotou, nie každý interný cleanup release.

**Honesty pri backfille:** rovnaký Auditor check (sekcia 6 Fáza 3) sa aplikuje aj na backfillnuté notes — žiadny rekonštruovaný bullet bez pokrytia v dobovom `CHANGES.md`.

---

## Súhrn súborov, ktorých sa dizajn dotkne (NEZMENENÉ — read-only)

**NEX Studio (vzor):**
- `backend/api/routes/release_notes.py` (NEW route, no-auth) + mount v app factory
- `backend/services/release_notes.py` (NEW; glob `v*/RELEASE_NOTES.md` + join versions DB pre `release_date`/ordering, mtime fallback; traversal guard mirror `project_specs.py:298-311`)
- `backend/Dockerfile` (po `:146-148` pridať úzky `COPY docs/specs/versions/*/RELEASE_NOTES.md`); root `Dockerfile` = pravdepodobne dead, flag, needitovať ako load-bearing
- `frontend/src/components/layout/Sidebar.tsx:205` (NavItem nad `SectionLabel "Nastavenia"`) + `:28-41` (IconUpdates glyph)
- `frontend/src/App.tsx` (import + route `/updates`)
- `frontend/src/pages/UpdatesPage.tsx` (NEW; inline ReactMarkdown idiom + `@/components/markdown/CodeBlock`)
- `docs/specs/versions/v<aktuálna>/RELEASE_NOTES.md` (NEW content — Dedo-authored pre vzor)

**Scaffold (`/home/icc/knowledge/templates/claude-project/`):**
- `frontend-skeleton/src/components/layout/Sidebar.tsx.tmpl` (NavItem do spodnej skupiny + ordering komentár nad budúcim Settings)
- `frontend-skeleton/src/App.tsx` (route `/updates`)
- NEW `frontend-skeleton/src/pages/UpdatesPage.tsx.tmpl`
- NEW `docs/specs/versions/v0.1.0/RELEASE_NOTES.md.tmpl` + `init.sh:353` copy_file riadok
- `development-spec` template: NEW povinná sekcia „Release-notes serving" (endpoint + úzky COPY)

**Charters:**
- `.claude/agents/designer/CLAUDE.md.tmpl` (produce RELEASE_NOTES na CHANGES.md bodoch + doc-tree + immutability po release)
- `templates/coordinator-charter.md:209-216` (Krok 7 PASS: surface RELEASE_NOTES v release settle summary pre Director review pred `uat_accept`)
- `.claude/agents/auditor/CLAUDE.md.tmpl` (**MANDATORY** honesty check: každý bullet trace na dodaný EPIC/BUG/CR + behaviorálny acceptance check „endpoint 200 + /updates renderuje")

**nex-shared (DRY — PARALELNÝ, neblokujúci follow-up):**
- externý repo `rauschiccsk/nex-shared`: `src/Markdown.tsx` + `src/CodeBlock.tsx` (NEW, promote), tag bump 0.9.1 → 0.10.0; consume v 6+ NEX Studio call-sites + dep bump

**Kľúčové odchýlky od pôvodného groundingu (odôvodnené vyššie):** (1) autor notes = Designer (generované apps) / Dedo (NEX Studio vzor), NIE engine pri release PASS; (2) serving = BE endpoint, NIE FE-bundle; (3) renderer DRY je **paralelný neblokujúci** follow-up, NIE prerekvizita (nex-shared je externý git-tag repo, bump = cross-repo + ongoing maintenance); (4) `released_at` z `Version.release_date` DB (mtime fallback), NIE z parsovaného H2 nadpisu; (5) úzky `COPY` len `RELEASE_NOTES.md`, NIE celé `docs/specs` (security + image surface); (6) honesty check MANDATORY + behaviorálny acceptance gate, NIE odporúčaný; (7) NEX Studio dogfooduje serving/FE, nie autonómnu generáciu.

---

## Otvorené rozhodnutia pre Directora

**Žiadne blokujúce.** Všetky review-flagged otázky sú vyriešené v dizajne. Dva drobné potvrdzovacie body (neblokujú implementáciu, default je bezpečný):

1. **Root `Dockerfile` (`/opt/projects/nex-studio/Dockerfile`)** sa javí ako nepoužívaný compose-om (`docker-compose.yml` referencuje len `backend/Dockerfile` + `frontend/Dockerfile`). Default: needitovať ho, baknúť COPY len do `backend/Dockerfile`. Potvrdiť že je vestigiálny (alebo odstrániť ako samostatný cleanup) — nezdržuje feature.
2. **Glyph pre „Aktualizácie"** (`✨` / `🆕` / iný) — kozmetické, ponechané na Director preferenciu; default `✨`.