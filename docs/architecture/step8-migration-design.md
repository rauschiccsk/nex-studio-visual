# NEX Studio — Migrácia existujúcich projektov (STEP 8): implementačný podklad

> Bezpečný kopírovací nástroj: prenesie 8 ostrých projektov zo starého NEX Studia (v1) do nového (v2).
> Nadväzuje na BUILD-PLAN.md krok 8, REDESIGN-SCOPE.md (migrácia = posledná, po hotovej otestovanej stavbe).
> Grounded v reálnom kóde (v2.0.0-dev, HEAD 8764ece). Prešiel 2 kolami revízie; adversariálny verdikt: READY.
> POZOR: nástroj sa teraz LEN POSTAVÍ + otestuje na CVIČNÝCH dátach (test DB :9178). Na OSTRÉ dáta ho pustí
> Manažér neskôr ako vedomý release krok — a PRED tým sa prejde „Otvorené otázky" nižšie.
> Podklad pre Implementera — Implementer číta tento dokument.
> Pozn.: presne 7 priamo-projektových tabuliek sa kopíruje (nie 8): versions, epics, bugs, customers,
> backlog_items, deploy_events, project_members (feats/tasks idú pod epic/feat).

## Po ľudsky (pre Manažéra projektu)

Krok 8 „Migrácia" je bezpečnostný kopírovací nástroj, ktorý prenesie osem reálnych projektov zo starého NEX Studia do nového. Prenesie sa všetko podstatné: projekty, ich verzie, epiky, funkcie, úlohy aj chyby, zákazníci, história nasadení a — čo prvá verzia návrhu ZABUDLA — aj prístupové práva ľudí k projektom (kto má vidieť ktorý projekt). Bezpečnosť je na prvom mieste: nástroj najprv beží „naprázdno" (ukáže presne, čo spraví, ale nič nezapíše), dá sa bezpečne spustiť viackrát, kopíruje projekt po projekte (ak jeden zlyhá, ostatné prejdú), nič sa nestratí a tajomstvá (heslá, kľúče) sa nikdy nevypisujú ani nekopírujú — prenesie sa len odkaz na ne. Preverili sme celý návrh oproti skutočnému kódu a opravili päť vecí, ktoré by inak spôsobili tichú stratu dát alebo poruchu po prenose. Teraz nástroj len POSTAVÍME a otestujeme na testovacej databáze s vymyslenými dátami — na ostré dáta ho pustíš neskôr ako vedomý krok. Predtým potrebujem od teba potvrdiť pár vecí (najmä kde je stará databáza a čo s tromi projektmi, čo už v novom prostredí sú) a rozhodnúť šesť jednoduchých otázok.

## Rozhodnutia (Manažér projektu 2026-07-05)

- **M1 = starú históriu buildov NEkopírovať do novej DB** — ostáva čitateľná na disku (git log, .nex-*-state.md, PTY logy). Nová DB staré enum hodnoty aj tak odmieta (069). = pôvodné rozhodnutie OQ-6.
- **M2 = (A) samostatný príkazový nástroj** `scripts/migrate_v1_to_v2.py` s dry-run náhľadom (spustí sa raz pri vydaní, ukáže presne čo spraví). NIE tlačidlo v appke — najvyššie stávky, ostré dáta.
- **M3 = (A) preniesť CELÚ históriu verzií** (všetky verzie + ich stromy epik/funkcií/úloh/chýb). Nová DB ju udrží, nič navyše.
- **M4 = projekt-po-projekte** (ak jeden zlyhá, ostatné prejdú; oprav a spusti len ten jeden). Nie all-or-nothing.
- **M5 = konflikt slug → preskočiť a nahlásiť** (Manažér rozhodne — nex-agents môže kolidovať s existujúcim v2 projektom). Žiadny auto-rename/merge.
- **M6 = spustiť AŽ PO nasadení nového NEX Studia** (migrácia je kopírovanie dát, nie deploy — oddelené).

## Opravy zapracované (2 kolá revízie — kritik: READY)

- BLOCKER 1 (project_members silently dropped) — FIXED. Re-scanned every ForeignKey("projects.id") in backend/db/models/; the complete project-scoped set is 8 tables. Added project_members (project_member.py:32-46, project_id CASCADE + user_id CASCADE — gates shu KB access, project_member.py:6-8) to the FK copy order (after projects, alongside the user-scoped tree since it FKs both projects.id and users.id), copied 1:1 preserving PK/timestamps, and added per-project count parity for it in verify.py. Confirmed NO other project-scoped copy-worthy table is missed: versions/epics/bugs/customers/backlog_items/deploy_events are all present; feats/tasks reach project via epic/feat; orchestrator_session + agent_terminal_sessions key project by a project_slug String (operational, excluded).
- BLOCKER 2 (incomplete referenced-user pre-flight) — FIXED. Scanned every ForeignKey("users.id") on COPIED tables. The pre-flight now asserts existence of the FULL set: projects.created_by (RESTRICT, projects.py:68-72), projects.owner_id (SET NULL, projects.py:77-82), bugs.created_by (NOT NULL + RESTRICT, bugs.py:45-49 — would otherwise hard-crash mid-tree), deploy_events.actor_id (SET NULL, deploy.py:103-107), project_members.user_id (CASCADE, project_member.py:38-43). Fail-closed 'sync users first' listing missing ids BEFORE any per-project write TX. customers/versions/epics/feats/tasks/backlog_items confirmed to have NO user FK.
- MAJOR 3 (deploy_events.seq IDENTITY high-water-mark) — FIXED. deploy_events.seq is Identity(always=False), unique=True (deploy.py:67) and the load-bearing 'latest event' key (deploy service orders by seq.desc() at deploy.py:136/142/160/227 for the never-bypass UAT-acceptance gate). After copying deploy_events in --apply, the tool advances the target IDENTITY sequence: setval(pg_get_serial_sequence('deploy_events','seq'), MAX(copied seq)). Scanned ALL Identity/serial columns on copied tables — deploy_events.seq is the ONLY one (pipeline_message.seq is Identity but NOT copied; versions/epics/feats/tasks/bugs use an app-assigned Integer number with no DB sequence). e2e test 9 asserts a post-migration deploy_event write succeeds without collision and orders after migrated rows.
- MINOR 4 (two distinct DB guards conflated) — FIXED. Stated explicitly as TWO pre-flight calls: (1) assert_test_db_distinct(source_url, target_url) for source!=target (a single 2-arg helper, _db_guard.py:26-43), and (2) a SEPARATE compare of database_name(target_url) vs database_name(settings.database_url) refusing unless allow_prod_target. test_migration_guards.py tests each as an independent assertion.
- MINOR 5 (pipeline-empty assertion mis-scoped) — FIXED. pipeline_state/pipeline_message are version-scoped (pipeline.py:108/216, no project_id) so a global 'assert EMPTY' false-fails against a v2 target that already has 3 projects with forward v2 builds. Reworded verify.py to capture target pipeline_state/pipeline_message counts BEFORE and AFTER the copy and assert DELTA == 0 (the migration inserts ZERO such rows); e2e test 8 pre-seeds an unrelated forward pipeline_state row to prove the delta-based check.

## Prístup

STEP 8 "Migrácia" is a SEATBELT DATA-COPY TOOL that lifts the 8 real projects from the running v1 PROD cockpit into a fresh v2 PROD, honouring already-resolved product decisions (OQ-6 cutover strategy at nex-studio-v2-build-plan.md:500; CR-V2-032 is the cutover CR). BUILT + TESTED NOW against synthetic fixtures on the TEST DB (:9178/nexstudio_test) ONLY; RUN later as a deliberate Director-driven release action. It is NOT a product-design change and NOT the deploy itself.

SHAPE (unchanged, verified SOUND): a standalone CLI scripts/migrate_v1_to_v2.py backed by a reusable, unit-testable service package backend/services/migration/. Two DB URLs are passed as explicit args (--source-url, --target-url) — NOT read from settings.py, which hardcodes only :9178/nexstudio (settings.py:8) via a single engine (session.py:16) that cannot express two DBs at once. This dual-engine requirement is the core design constraint (verified).

FOUR NON-NEGOTIABLE SAFETY PROPERTIES → concrete mechanism (unchanged core):
1. DRY-RUN FIRST (default) — open target in a transaction, do all INSERTs, verify, ROLLBACK, print the plan. --apply commits per-project. Identical code path in both modes (mirrors uat-deploy.py's dry-run discipline).
2. IDEMPOTENT — per-project: SELECT projects WHERE slug=? in target; present → SKIP (logged). No UPSERT (never overwrites operator edits).
3. PER-PROJECT TRANSACTIONAL — each project's whole tree in ONE target transaction; a failure rolls back that project only, others continue.
4. NO DATA LOSS + SECRETS PRESERVED + LEGACY-AS-HISTORY — rows copy 1:1 with PK UUIDs + created_at/updated_at preserved; new v2 columns get safe defaults; credentials files under /opt/data/nex-studio/credentials/ are SHARED (only registry rows copied, never a secret value read/logged, §4); v1 pipeline history is NOT copied (OQ-6 — survives on-disk in .nex-*-state.md + git + PTY logs).

FIVE CORRECTIONS FROM CRITIQUE (verified real against v2.0.0-dev HEAD 8764ece):
(B1) The copied project-scoped table set is now COMPLETE — I re-scanned every ForeignKey("projects.id") in backend/db/models/ and the closed set is exactly 7 tables: versions, epics, bugs, customers, backlog_items, deploy_events, AND project_members (project_member.py:32-46) which the first pass silently dropped. project_members carries real RBAC (a shu user only sees KB for projects they are a member of; project_member.py:6-8). It is now in the FK copy order and the verify count-parity. feats/tasks reach project via epic/feat (no direct project_id) and are copied under their epic. NO other project-scoped copy-worthy table exists (agent_terminal_sessions/orchestrator_session key project by project_slug String, are operational, excluded).
(B2) The referenced-user PRE-FLIGHT now covers the FULL user-ref set. I scanned every ForeignKey("users.id") on COPIED tables: projects.created_by (RESTRICT, projects.py:68-72), projects.owner_id (SET NULL, :77-82), bugs.created_by (NOT NULL + RESTRICT, bugs.py:45-49), deploy_events.actor_id (SET NULL, deploy.py:103-107), project_members.user_id (CASCADE, project_member.py:38-43). Every one must EXIST in target before opening any per-project write TX, fail-closed "sync users first". (customers/versions/epics/feats/tasks/backlog_items have NO user FK — confirmed.)
(M3) deploy_events.seq is GENERATED BY DEFAULT AS IDENTITY, unique=True (deploy.py:67) and the load-bearing "latest event" key (deploy service orders by seq.desc() at deploy.py:136/142/160/227 for the never-bypass UAT-acceptance gate). After copying deploy_events with explicit seq (in --apply), the tool ADVANCES the target IDENTITY sequence: setval(pg_get_serial_sequence('deploy_events','seq'), MAX(copied seq)). I scanned ALL Identity/serial columns on copied tables: deploy_events.seq is the ONLY one (pipeline_message.seq is Identity but is NOT copied; versions/epics/feats/tasks/bugs use an app-assigned Integer `number`, no DB sequence). So exactly one setval, run once per --apply after the deploy_events copy.
(m4) TWO distinct pre-flight guard calls, stated explicitly: (1) assert_test_db_distinct(source_url, target_url) refuses source==target; (2) a SEPARATE compare of database_name(target_url) vs database_name(settings.database_url) refusing target==PROD-name UNLESS --i-understand-target-is-prod. assert_test_db_distinct (_db_guard.py:26-43) is a single 2-arg helper and cannot cover both.
(m5) Verification reworded: pipeline_state/pipeline_message are version-scoped (pipeline.py:108/216, no project_id) so "assert EMPTY in target" is wrong when the v2 target already has forward v2 builds. Instead capture target pipeline_state/pipeline_message counts BEFORE and AFTER the copy and assert DELTA == 0 (the migration inserts ZERO such rows).

NOT IN SCOPE: running against real PROD (:9198) — build+test on TEST DB (:9178/nexstudio_test) with synthetic fixtures ONLY; STEP 7 cleanup; the deploy/cutover (CR-V2-032). On-disk directory copy is an OPT-IN --copy-dirs flag (default OFF); the DB copy is the load-bearing testable core.

## Rozdiel schémy v1 → v2

The redesign (v2.0.0-dev, migrations 001-081) is NOT an additive superset of v1 — it DIVERGED at migrations 068-072. The migration bridges that divergence (verified against the models + migrations 069/070/072 + git show main):

DROPPED in v2 (nothing to migrate INTO): project_modules, module_dependencies (migration 070); epics.module_id FK+index (070 — v1 main tasks.py:30 HAS module_id; v2 tasks.py:24-35 has project_id + nullable version_id, NO module_id → the copier NEVER reads/writes module_id); dialogue tables (068); architect_sessions/messages, kb_documents (dropped earlier).

RENAMED / TRANSFORMED (the transform IS the migration logic):
- projects.category('singlemodule'/'multimodule', v1 main projects.py:26/62-63) → projects.type('standard'/'web') (migration 072, v2 projects.py:34/87-90). Map: singlemodule→'standard', multimodule→'standard', unknown→'standard'. CHECK ck_projects_type would REJECT any v1 value → transform is MANDATORY.
- projects gains MANDATORY auth_mode('password'|'token', NOT NULL, projects.py:39/91-94) — backfill 'password'.
- pipeline enums rebuilt (069): flow_type v1{new_version,cr,bug,fast_fix}→v2{new_version,fast_fix} (pipeline.py:51); current_stage v1{kickoff,gate_a..e,task_plan,build,gate_g,release,done}→v2{priprava,navrh,programovanie,verifikacia,done} (pipeline.py:52-58); current_actor v1{coordinator,designer,customer,implementer,auditor,director}→v2{ai_agent,auditor} (pipeline.py:62). NO semantic forward mapping → pipeline_state/pipeline_message NOT copied (OQ-6).

ADDED in v2 (defaults, no v1 source): pipeline_state table (v1 used file-bus), customers(075), deploy_events(076), backlog_items(062), credentials(038 registry pointers); projects.miera_autonomie(074,NULL)/uat_slug(065,NULL)/guardian_enabled(false)/custom_development_enabled(081,false)/owner_id(SET NULL→creator); pipeline_state.mode(079); epics/feats/tasks.plain_description(080,NULL); tasks.baseline_sha(NULL).

UNCHANGED (copy 1:1): Project core (name/slug/status/description/created_by), Version (versions.py:22-50), Epic/Feat/Task core (tasks.py, minus module_id), Bug core (bugs.py), Customer (customers.py), Credential (credentials.py), User (foundation.py:19-43 — pre-seeded, NOT copied).

RESOLVED CONSTRAINT I DO NOT CHANGE: migration 069 docstring (069_v2_pipeline_enums_two_agents.py:24-26): "Preserving HISTORICAL v1 build rows read-only is a CUTOVER concern (CR-V2-032), NOT this migration's job." Build plan OQ-6 (nex-studio-v2-build-plan.md:500): completed historical v1 pipeline_state/message rows are preserved READ-ONLY (NOT retro-migrated). → tool does NOT copy v1 pipeline_state/pipeline_message.

CORRECTED FK COPY ORDER (verified per model; 8 project-scoped tables now, +project_members +the referenced-credentials set):
users (PRE-SEEDED, NOT copied — pre-flight asserts existence)
→ projects (projects.py; created_by RESTRICT, owner_id SET NULL)
→ project_members (project_member.py:32-46; project_id CASCADE + user_id CASCADE) [NEW — was dropped]
→ credentials (credentials.py; the SUBSET of credential rows referenced by this project's customers.credential_id — credentials has NO project_id, it is a flat registry reached via Customer.credential_id SET NULL)
→ customers (customers.py; project_id CASCADE, credential_id→credentials SET NULL)
→ versions (versions.py; project_id CASCADE)
→ epics (tasks.py; project_id CASCADE + version_id RESTRICT — NO module_id)
→ feats (tasks.py; epic_id CASCADE)
→ tasks (tasks.py; feat_id CASCADE)
→ bugs (bugs.py; project_id CASCADE + version_id RESTRICT + created_by users.id NOT NULL RESTRICT)
→ backlog_items (backlog.py; project_id CASCADE + version_id SET NULL)
→ deploy_events (deploy.py; customer_id CASCADE + project_id CASCADE + actor_id users.id SET NULL; seq IDENTITY → setval after copy)

NOT copied (transient/operational — explicit exclusion list): pipeline_state, pipeline_message (OQ-6, version-scoped), orchestrator_session (keyed project_slug String), agent_terminal_sessions (user-owned), user_agent_settings, user_sessions (foundation.py), system_settings (global ICC config). Re-scanned ALL ForeignKey("projects.id") — the 8 copied are the complete project-scoped set; nothing else silently dropped.

## Mechanizmus + bezpečnosť

CONNECTIONS: two explicit engines from --source-url/--target-url (NOT settings.py — settings.py:8 hardcodes one url, session.py:16 one engine). Source engine issues SELECTs ONLY; v1 is NEVER written.

PRE-FLIGHT GUARDS (TWO distinct DB-name guards + one full user guard — corrections m4 + B2):
1. assert_test_db_distinct(source_url, target_url) — reuse tests/_db_guard.py:26-43 (a single 2-arg name compare); refuse if source DB name == target DB name.
2. SEPARATE prod-target guard — compare database_name(target_url) against database_name(settings.database_url) (the cockpit PROD name); refuse UNLESS allow_prod_target (--i-understand-target-is-prod). In build/CI the target is always nexstudio_test → accidental PROD writes impossible. These are TWO calls, not one reused helper.
3. REFERENCED-USER existence (fail-closed, FULL set — B2): collect the union of users.id referenced by ALL to-be-copied rows for the selected projects — projects.created_by + projects.owner_id + bugs.created_by + deploy_events.actor_id + project_members.user_id — and assert every id EXISTS in target BEFORE opening any per-project write TX. Missing → abort with "sync users first" listing the missing ids; target untouched. (Especially bugs.created_by NOT NULL + RESTRICT (bugs.py:45-49) would otherwise crash mid-tree.)

DRY-RUN (default): one target transaction, full migration into it, verification inside it, then rollback; print report. --apply swaps rollback→commit per-project. Same code path → dry-run truly rehearses apply.

PER-PROJECT TX: iterate source projects (filter --only-slug/--exclude-status). Each: BEGIN; skip if target slug exists (logged, M5); INSERT the tree in the corrected FK order preserving PK UUIDs + created_at/updated_at; on error rollback THIS project + record failure + CONTINUE; on success (--apply) COMMIT. Re-run skips committed ones.

ID/FK PRESERVATION: copy source PK UUIDs verbatim (INSERT id=) so FKs line up without a remap table; created_at/updated_at verbatim.

IDENTITY HIGH-WATER-MARK (M3): after copying deploy_events for the whole apply run (in --apply, after the last committed project's deploy_events), advance the target IDENTITY sequence: SELECT setval(pg_get_serial_sequence('deploy_events','seq'), (SELECT MAX(seq) FROM deploy_events)) so the next in-app deploy/accept insert cannot collide on the unique seq nor mis-order the UAT-acceptance gate (deploy service orders by seq.desc() — deploy.py:136/142/160/227). deploy_events.seq is the ONLY Identity/serial among copied tables (verified scan); versions/epics/feats/tasks/bugs use an app-assigned Integer number (no DB sequence).

NEW-COLUMN DEFAULTS: type=map(category), auth_mode='password', miera_autonomie=NULL, uat_slug=NULL, guardian_enabled=false, custom_development_enabled=false, owner_id=source.owner_id or created_by, plain_description=NULL, baseline_sha=NULL. source_path REWRITTEN /opt/projects/<slug> → PROJECTS_ROOT/<slug> (default /opt/projects-v2/<slug>, --projects-root override). project_members.role copied verbatim (default 'member').

SECRETS (§4): credentials rows copy only (id, title, file_path) — the SUBSET referenced by the project's customers.credential_id. On-disk files in SHARED /opt/data/nex-studio/credentials/ are NEVER opened/read/logged. secrets_guard uses Path(file_path).exists() ONLY — it MUST NOT call the credentials service read_content (credentials.py:138) which reads bytes. WARN on a dangling pointer. No secret value in any log/exception/report.

VERIFICATION (inside the tx in dry-run; post-commit in apply): per-project per-table row-count parity source vs target for ALL 8 copied tables (INCLUDING project_members — B1); field-by-field deep compare on the core trees; credential file_paths exist (existence-only). PIPELINE DELTA (M5): capture target pipeline_state + pipeline_message counts BEFORE and AFTER the copy, assert DELTA == 0 (NOT "globally empty" — the v2 target may already have forward v2 builds). CRITICAL failure (count mismatch, missing user, non-zero pipeline delta) → HALT+rollback that project; non-critical (dangling credential, missing dir) → WARN. Report → --report-path (default /opt/data/nex-studio/migration-log/<ts>.json), NEVER secret values.

REVERSIBILITY: dry-run = rollback; --apply reversible per-project until commit. Run-time runbook (open item) requires pg_dump target + tar target dir BEFORE --apply.

ON-DISK COPY: OPT-IN --copy-dirs (shutil.copytree with git-idle precondition + post-copy `git status` health check), default OFF. DB copy is the load-bearing testable core.

## Súbory (13)
- `create` `backend/services/migration/__init__.py` — Package init exporting the migration service API (run_migration, MigrationConfig, MigrationReport).
- `create` `backend/services/migration/config.py` — MigrationConfig dataclass: source_url, target_url, projects_root, dry_run, only_slugs, exclude_statuses, copy_dirs, report_path, allow_prod_target. Plus DEFAULT_PROJECTS_ROOT_V2='/opt/projects-v2'.
- `create` `backend/services/migration/transforms.py` — Pure functions: map_category_to_type(), default_auth_mode(), rewrite_source_path(old, projects_root), new_column_defaults() (incl. guardian_enabled=false, custom_development_enabled=false, owner_id fallback). No DB access — 100% unit-testable.
- `create` `backend/services/migration/copier.py` — Core per-project copier: reads the tree from source (SELECT-only), applies transforms, INSERTs into target preserving PK UUIDs + timestamps, in the CORRECTED FK order INCLUDING project_members and the referenced-credentials subset. NEVER copies module_id (dropped). EXCLUDES pipeline_state/pipeline_message + operational tables. Per-project transaction boundary. Advances deploy_events.seq IDENTITY via setval after copy (in apply).
- `create` `backend/services/migration/verify.py` — Post-copy verification: per-table count parity for ALL 8 project-scoped tables INCLUDING project_members; field-by-field core compare; credential file existence (via secrets_guard, content never read); pipeline_state/pipeline_message BEFORE/AFTER delta==0 assertion (NOT global-empty). Classifies findings CRITICAL vs WARN.
- `create` `backend/services/migration/secrets_guard.py` — Thin existence-only checker: Path(file_path).exists()/is_file() WITHOUT reading content; MUST NOT call credentials.py:read_content. Enforces §4 (never returns/logs content). Used by copier + verify.
- `create` `backend/services/migration/runner.py` — Orchestrates: TWO DB-name pre-flight guards (assert_test_db_distinct + separate prod-target-name gate) + FULL referenced-user existence (created_by/owner_id/bugs.created_by/deploy_events.actor_id/project_members.user_id), iterate projects with skip/transaction/verify, deploy_events seq setval in apply, build MigrationReport, write report_path. Dry-run vs apply commit/rollback switch.
- `create` `scripts/migrate_v1_to_v2.py` — CLI entrypoint: argparse (--source-url, --target-url, --apply, --projects-root, --only-slug, --exclude-status, --copy-dirs, --report-path, --i-understand-target-is-prod), prints plan/report, exit codes ok/partial/fail. Delegates to runner.run_migration.
- `create` `tests/services/test_migration_transforms.py` — Unit tests for transforms.py (category map, auth_mode, source_path rewrite, all new-column defaults) and secrets_guard (existence-only True/False, content never returned/logged, read_content never called).
- `create` `tests/services/test_migration_guards.py` — Unit tests for the pre-flight guards: same-DB refusal; prod-target refusal without flag (as a SEPARATE assertion from same-DB); missing-referenced-user fail-closed for EACH of created_by/owner_id/bugs.created_by/deploy_events.actor_id/project_members.user_id; report serialization contains no secret material.
- `create` `tests/integration/test_migration_e2e.py` — Integration: seed synthetic v1 (distinct test DB), prove dry-run rolls back, apply preserves all rows field-by-field INCLUDING project_members, module_id absent in target, pipeline delta==0 (OQ-6), deploy_events seq advanced (post-migration deploy_event write succeeds without collision + orders after migrated rows), idempotent re-run skips, slug-conflict skips, per-project isolation on failure, credential pointer WARN vs OK, missing-user fail-closed for a bug/deploy_event/member.
- `create` `tests/integration/fixtures/synthetic_v1.py` — Fixture factory building a synthetic v1-shaped source (multimodule project, 2 versions, epics with module_id, feats/tasks/bugs with created_by, credentials row, customer→credential, deploy_event with seq+actor_id, backlog, PROJECT_MEMBERS rows, a v1-enum pipeline_state row). Purely synthetic — no real data.
- `create` `docs/architecture/step8-migration-design.md` — The STEP 8 design doc (this corrected design): approach, schema_diff, mechanism+safety, the corrected FK order + project_members + full user pre-flight + deploy_events seq setval + two DB-name guards + pipeline delta verification, the resolved OQ-6 handling, the run-time runbook, and the six manager decisions. Committed as the spec the Implementer reads.

## Úlohy pre Implementera (poradie)
1. **Scaffold the migration package + config**
   - Create backend/services/migration/ with __init__.py and config.py (MigrationConfig dataclass + DEFAULT_PROJECTS_ROOT_V2='/opt/projects-v2'). No logic yet. Read backend/config/settings.py:8-9 and backend/db/session.py:16 first to confirm why the tool must NOT reuse the single hardcoded engine.
   - Súbory: `backend/services/migration/__init__.py`, `backend/services/migration/config.py`
   - Overenie: python -c 'from backend.services.migration.config import MigrationConfig'; ruff check backend/services/migration/
2. **Pure transforms + tests**
   - Implement transforms.py: map_category_to_type (singlemodule/multimodule→standard, web→web, unknown→standard), default_auth_mode→'password', rewrite_source_path(old, projects_root) replacing /opt/projects/<slug>→<projects_root>/<slug>, new_column_defaults() for ALL v2-added columns per schema_diff (type, auth_mode, miera_autonomie=NULL, uat_slug=NULL, guardian_enabled=false, custom_development_enabled=false, owner_id fallback to created_by, plain_description=NULL, baseline_sha=NULL). Write tests/services/test_migration_transforms.py. No DB access.
   - Súbory: `backend/services/migration/transforms.py`, `tests/services/test_migration_transforms.py`
   - Overenie: pytest tests/services/test_migration_transforms.py -q
3. **Secrets guard (existence-only, §4)**
   - Implement secrets_guard.py: check a file_path EXISTS+is_file via Path(file_path).exists()/is_file() WITHOUT reading content. It MUST NOT import or call backend/services/credentials.py:read_content (credentials.py:138 reads bytes). Never return or log content. Add existence/absence + never-read-content unit cases to test_migration_transforms.py.
   - Súbory: `backend/services/migration/secrets_guard.py`, `tests/services/test_migration_transforms.py`
   - Overenie: pytest tests/services/test_migration_transforms.py -q; grep -nE 'read_content|read_bytes|read_text' backend/services/migration/secrets_guard.py (must be EMPTY)
4. **Pre-flight guards + tests (TWO DB-name guards + FULL user check)**
   - In runner.py add pre-flight: (1) assert_test_db_distinct(source_url, target_url) from tests/_db_guard.py for source!=target; (2) a SEPARATE guard comparing database_name(target_url) vs database_name(settings.database_url), refusing unless allow_prod_target; (3) FULL referenced-user existence: collect projects.created_by + projects.owner_id + bugs.created_by + deploy_events.actor_id + project_members.user_id for the selected projects and assert every id exists in target, fail-closed listing missing ids. Write tests/services/test_migration_guards.py with SEPARATE assertions for same-DB, prod-target, and one missing-user case PER referencing table.
   - Súbory: `backend/services/migration/runner.py`, `tests/services/test_migration_guards.py`
   - Overenie: pytest tests/services/test_migration_guards.py -q
5. **Per-project copier (DB core) — corrected FK order + project_members + seq setval**
   - Implement copier.py: read the tree from source (SELECT-only engine), apply transforms, INSERT into target preserving PK UUIDs + created_at/updated_at, in the CORRECTED FK order: projects → project_members → credentials(subset via customers.credential_id) → customers → versions → epics(NO module_id) → feats → tasks → bugs → backlog_items → deploy_events. One transaction per project. EXCLUDE pipeline_state/pipeline_message + operational tables. In --apply, after copying deploy_events, run setval(pg_get_serial_sequence('deploy_events','seq'), MAX(seq)) to advance the IDENTITY high-water-mark (deploy.py:67). Reference every model in backend/db/models/ for exact columns; NEVER read/write epics.module_id.
   - Súbory: `backend/services/migration/copier.py`
   - Overenie: ruff check backend/services/migration/copier.py; python -c 'from backend.services.migration.copier import copy_project'
6. **Verification module — project_members parity + pipeline delta**
   - Implement verify.py: per-project per-table count parity source vs target for ALL 8 copied tables INCLUDING project_members; field-by-field core compare (Project/Version/Epic/Feat/Task/Bug/Customer/Credential/DeployEvent/Backlog/ProjectMember); credential file existence via secrets_guard. PIPELINE DELTA: capture target pipeline_state + pipeline_message counts before/after the copy and assert delta == 0 (do NOT assert globally empty — the target may have forward v2 builds). Classify CRITICAL (count mismatch, missing user, non-zero pipeline delta) vs WARN (dangling credential, missing dir).
   - Súbory: `backend/services/migration/verify.py`
   - Overenie: ruff check backend/services/migration/verify.py; python -c 'from backend.services.migration.verify import verify_project'
7. **Runner: dry-run/apply, skip, report, seq setval wiring**
   - Complete runner.py: run the 3 pre-flight guards; iterate projects (filters), per-project skip-if-slug-exists, transaction, copier, verify, collect MigrationReport; dry-run rolls back + prints plan, --apply commits per-project; after the apply loop run the deploy_events seq setval on target. Write JSON report to report_path; NEVER include secret values. Wire secrets_guard + verify + copier.
   - Súbory: `backend/services/migration/runner.py`
   - Overenie: ruff check backend/services/migration/runner.py; python -c 'from backend.services.migration.runner import run_migration'
8. **CLI entrypoint**
   - Create scripts/migrate_v1_to_v2.py: argparse (--source-url, --target-url, --apply, --projects-root, --only-slug, --exclude-status, --copy-dirs, --report-path, --i-understand-target-is-prod), delegate to runner.run_migration, print report, exit codes ok/partial/fail. --copy-dirs (default OFF) does shutil.copytree with git-idle precondition + post-copy git-status health check.
   - Súbory: `scripts/migrate_v1_to_v2.py`
   - Overenie: python scripts/migrate_v1_to_v2.py --help
9. **Synthetic v1 fixture + e2e integration test**
   - Create tests/integration/fixtures/synthetic_v1.py building a synthetic v1 source (multimodule project, 2 versions, epics WITH module_id, feats/tasks/bugs WITH created_by, credentials row, customer→credential_id, deploy_event WITH seq+actor_id, backlog, PROJECT_MEMBERS rows, a v1-enum pipeline_state row). Write tests/integration/test_migration_e2e.py covering: dry-run rollback (target unchanged), apply field-by-field parity INCLUDING project_members, module_id absent in target, pipeline delta==0 (OQ-6), deploy_events seq advanced (a post-migration deploy_event write into a migrated project succeeds without seq collision and orders after migrated rows), idempotent re-run skip, slug-conflict skip, per-project isolation on failure, credential pointer WARN vs OK, missing-user fail-closed for a bug/deploy_event/member. Source+target are distinct test DBs/schemas, both != PROD.
   - Súbory: `tests/integration/fixtures/synthetic_v1.py`, `tests/integration/test_migration_e2e.py`
   - Overenie: pytest tests/integration/test_migration_e2e.py -q
10. **Design doc + full suite + lint**
   - Write docs/architecture/step8-migration-design.md (this corrected design, incl. project_members inclusion, full user pre-flight, deploy_events seq setval, two DB-name guards, pipeline delta verification, resolved OQ-6 handling, run-time backup runbook, the six manager decisions). Run the FULL suite (shared-module discipline) + ruff format --check + ruff check. Do NOT run the tool against any real DB.
   - Súbory: `docs/architecture/step8-migration-design.md`
   - Overenie: ruff format --check . && ruff check . && pytest -q

## Overenie

ALL tests run against the TEST DB (:9178/nexstudio_test) via the existing SAVEPOINT-per-test conftest (tests/conftest.py) — NEVER PROD (:9198) and NEVER the cockpit DB (tests/_db_guard.py:assert_test_db_distinct enforces this). The tool needs TWO DBs, so integration tests simulate v1↔v2 as two distinct test schemas/DBs (a synthetic source + nexstudio_test target), both distinct from PROD so the guards pass. SYNTHETIC fixtures ONLY — no real project data, never the real v1 source, never PROD :9198.

UNIT (tests/services/, pure/SAVEPOINT):
1. category→type map (singlemodule/multimodule→standard, web→web, unknown→standard); auth_mode backfill='password'; ALL new-column defaults (incl. guardian_enabled=false, custom_development_enabled=false, owner_id fallback); source_path rewrite /opt/projects/x→/opt/projects-v2/x.
2. secrets_guard: existence-only True/False; assert content is NEVER returned/logged and read_content is never called.
3. Guards (SEPARATE assertions): (a) assert_test_db_distinct raises on same source/target DB name; (b) prod-target refusal — database_name(target)==database_name(settings.database_url) raises unless allow_prod_target; (c) missing referenced-user fail-closed — one case each for a project.created_by, project.owner_id, bugs.created_by, deploy_events.actor_id, project_members.user_id absent in target → abort, target untouched; (d) report serialization contains NO secret material.

INTEGRATION (tests/integration/test_migration_e2e.py, synthetic v1 source):
4. SEED synthetic v1 (multimodule project, 2 versions, epics WITH module_id, feats/tasks/bugs WITH created_by, credentials row, customer→credential_id, deploy_event WITH seq+actor_id, backlog, PROJECT_MEMBERS rows, AND a v1-enum pipeline_state row flow_type='cr'/stage='gate_a').
5. DRY-RUN: target row counts UNCHANGED after run (rollback proven); report lists the project + multimodule→standard mapping + conflicts.
6. APPLY: per-table source==target counts for ALL 8 project-scoped tables INCLUDING project_members; PKs preserved (same UUIDs); created_at/updated_at preserved; epics arrive WITHOUT module_id; customer.credential_id FK intact.
7. NO-DATA-LOSS: field-by-field deep compare source vs target for Project/Version/Epic/Feat/Task/Bug/Customer/Credential/DeployEvent/Backlog/ProjectMember.
8. PIPELINE DELTA (OQ-6): target pipeline_state + pipeline_message counts BEFORE == AFTER (delta==0) even when the target is pre-seeded with an unrelated forward v2 pipeline_state row (proves the assertion is delta-based, not global-empty).
9. DEPLOY SEQ HIGH-WATER-MARK: after --apply, INSERT a fresh deploy_event into a migrated project via the deploy service → succeeds without a UNIQUE(seq) collision AND its seq > every migrated seq (orders after, so the UAT-acceptance-recency query returns the new event as latest).
10. IDEMPOTENCY: --apply twice → second run SKIPS existing project (logged), counts unchanged, no duplicate-key error, exit 0.
11. SLUG CONFLICT: pre-create colliding target slug → dry-run REPORTS, --apply SKIPS with reason (no overwrite).
12. MISSING USER: source references a created_by / actor_id / member user_id absent in target → pre-flight aborts, target untouched (one sub-case per referencing table).
13. PER-PROJECT ISOLATION: two projects, second crafted to fail → first commits, second rolls back, report 1 ok/1 failed; re-run migrates only the failed one after fix.
14. CREDENTIAL POINTER: missing file → WARN (non-critical), migration succeeds; present file → existence-verified, content never read.

STATIC: ruff format --check + ruff check (.githooks/pre-commit); NO mypy (NEX Studio backend has no Python type-checker). Full `pytest` run (shared-module discipline). RUN-AGAINST-PROD (:9198) IS FORBIDDEN in this scope.

## OTVORENÉ OTÁZKY — POTVRDIŤ PRED OSTRÝM BEHOM (nie pred stavbou)
- V1 SOURCE DB exact location/credentials at RUN time: maps agree it is v1 PROD at localhost:9178/nexstudio (settings.py:8), but the actual host/port/db/credentials must be Director-confirmed. Build does not need it (synthetic fixtures); the RUN does.
- V1 SOURCE SCHEMA at release time: models on v2.0.0-dev are the TARGET; the live v1 schema (frozen v1.0.0 on main) is inferred from the 069/070/072 downgrades. Before RUNNING, dump the real v1 schema and diff against the tool's expected source columns — any extra v1 column/table not read is a silent-drop risk to review.
- TARGET ENV & PROJECTS_ROOT: memory says v2 PROD=/opt/projects-v2 (:9198/nexstudio_v2). The map notes 3 projects ALREADY exist there (nex-agents, nex-analyzer, orthodox-register) → real slug-conflict candidates (nex-agents!). Confirm the final target DB URL, PROJECTS_ROOT, and whether those 3 are keepers (skip) or test rows (delete before migrate).
- USERS in v2 target: the tool references created_by/owner_id/bugs.created_by/deploy_events.actor_id/project_members.user_id but does NOT copy the users table. Confirm v2 PROD is pre-seeded with the same ICC users (matching UUIDs) — else the FULL referenced-user pre-flight fails closed. Decide: pre-sync users, or extend the tool to copy the referenced-users subset.
- PROJECT_MEMBERS disposition: memberships are now migrated (B1 fix). Confirm this is the desired behaviour vs re-seeding RBAC fresh in v2. If v2 should start with fresh memberships, that becomes an explicit opt-out (a documented decision), NOT a silent drop.
- ON-DISK DIRECTORY COPY need: is /opt/projects-v2/<slug> expected to already be populated, or must the tool copy /opt/projects/<slug>→/opt/projects-v2/<slug>? Decides whether --copy-dirs is used at run time (default OFF).
- CREDENTIALS INVENTORY: are v1 secrets already registered as credentials rows in /opt/data/nex-studio/credentials/, or only in project .env files? The tool copies the REGISTRY rows referenced by customers.credential_id; if secrets live only in .env, an operator step (register them) precedes the run.
- MULTI-MODULE reality: how many of the 8 real projects are category='multimodule'? The tool FLATTENS to type='standard' (epics already project-level via version_id; module_id not copied). Confirm no project depended on module→epic nesting that flatten would misrepresent (nex-inbox may be multimodule).
- ACTIVE BUILDS / git-idle at run time: v1 must be quiesced for the on-disk --copy-dirs git-idle precondition (pipeline_state is not migrated anyway). Confirm v1 will be idle during the run.
- BACKUP RUNBOOK for the RUN: post-commit rollback = restore-from-backup. Confirm ops step: pg_dump target + tar target projects dir BEFORE --apply. Out of build scope, required for the run.