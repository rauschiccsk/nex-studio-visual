# NEX Studio v2.0.0 — Build Implementation Plan

> Ordered, dependency-aware implementation plan for the v2.0.0 rebuild: the v1 5-role serial waterfall engine is replaced by **one AI Agent + one independent Auditor**, governed by a **Miera autonómie** dial, across **4 phases (Príprava → Návrh → Programovanie → Verifikácia)** + Hotovo. Deploy leaves the pipeline and becomes per-customer. All surrounding infrastructure is kept and adapted.
>
> Authoritative target spec: `docs/architecture/nex-studio-v2-design.md` (the navrh). Decision-log: `docs/architecture/nex-studio-v2-lead-engineer-auditor.md`.
> Dated 2026-06-26. Branch: `v2.0.0-dev` (main frozen at v1.0.0 — see §6). Body English; Slovak only in UI strings.

---

## 1. Goal & scope

Replace **only the build ENGINE** — the v1 5-role serial pipeline (Designer → Customer → Implementer → Auditor → Coordinator, hub-and-spoke `.dedo-channel` file-bus, 11-stage state machine, per-task auditing, forced gates) becomes **two agents** (a strong AI Agent that holds one warm context across Príprava → Návrh → Programovanie and spawns ephemeral helpers, plus an independent Auditor with two touchpoints) on a **4-phase pipeline** governed by a **4-level Miera autonómie dial**, with **deploy lifted out of the pipeline into per-customer Zákazníci/UAT/PROD tabs**. Everything around the engine is **kept and adapted, not rewritten**: project creation/scaffolding, the VERSION → EPIC → FEAT → TASK work hierarchy, the Metrics/ROI feature (recomputed per-phase), the cockpit board UI (becomes Vývoj), KB/RAG integration, UAT-provisioning + deploy machinery (`uat_provisioner.py`, `onboard-customer.sh`, instance-per-customer `/opt/customers/<slug>/`), the fast-fix lane, Settings, NEX Studio's own auth/users, the PTY `claude`-session substrate (`agent_terminal.py`), the headless `invoke_claude` primitive, the `PipelineState`/`PipelineMessage` persistence model, and the Telegram notify integration; multi-module is the one structural removal (`ProjectModule`/`ModuleDependency`/`Epic.module_id` + MM pages).

---

## 2. Keep / Modify / Replace / Remove boundary table

Disposition of every classified component from the four subsystem maps. **K** = keep verbatim, **M** = modify, **R** = replace (re-author against navrh), **X** = remove.

### 2.1 orchestrator-core (the engine)

| Component | Disp | Files |
|---|---|---|
| `PipelineState` + `PipelineMessage` two-table model (who-is-on-turn + append-only log) | **K** | `backend/db/models/pipeline.py` |
| ORM status-set event listeners (director-wait timer, single-flight clear, block_reason clear) | **K** (re-wire to new STATUS) | `backend/db/models/pipeline.py` |
| `invoke_agent` / `invoke_agent_with_parse_retry` / `_resolve_orch_session` / `_resolve_dispatch_overrides` / `OrchestratorSession` keying | **K** (re-key to 2 roles) | `backend/services/orchestrator.py`, `backend/db/models/orchestrator.py` |
| `pipeline_runner.py` single-flight dispatch + WS broadcast + Telegram notify + auto-chain loop | **K** (recompute auto-chain bound) | `backend/services/pipeline_runner.py` |
| `recover_orphaned_builds_on_startup` / `cleanup_old_orchestrator_sessions` / `verify_mechanical` / `_audit_lost_work` / `dispatch_baseline_sha` | **K** | `backend/services/orchestrator.py` |
| `STAGE_VALUES` DB enum + `ck_*_stage` CHECKs (the enum-tuple-single-source pattern) | **M** (tuple → 4 phases) | `backend/db/models/pipeline.py` |
| `schemas/pipeline.py` response/request models + enum-Literal-from-DB-tuple | **M** | `backend/schemas/pipeline.py` |
| 5-role `STAGE_ACTOR` / `ACTOR_VALUES` / `OrchestratorSession` role CHECK | **R** → `{ai_agent, auditor}` | `backend/services/orchestrator.py`, `backend/db/models/pipeline.py`, `backend/db/models/orchestrator.py` |
| `STAGE_ORDER` 11-stage path + `_next_stage`/`_stage_order_for` | **R** → 4 phases | `backend/services/orchestrator.py` |
| `apply_action()` dispatcher + `_ACTIONS`/`_ADVANCING_ACTIONS` (keep sole-mutator invariant) | **R** | `backend/services/orchestrator.py` |
| Gate-E machinery (`_run_gate_e_round`, ~20 `_gate_e_*`, fix/leave/end_gate_e, `_maybe_autonomous_gate_e_continue`, gate_e audit md) | **R** → Auditor upfront review | `backend/services/orchestrator.py` |
| `task_plan` stage + `_run_task_plan_round` + skeleton/per-feat passes + `_write_task_plan`/`_render_task_plan_md` | **M** (folds into Návrh; tree + incremental passes survive) | `backend/services/orchestrator.py` |
| Per-task Auditor loop (`_verify_task`, `_audit_prompt_for_task`, `_task_audit_verdict`, `_record_task_summary`, `_AUTO_FIX_RETRIES`, HALT→relay) | **R** → AI Agent self-check; bounded loop survives at verifier level | `backend/services/orchestrator.py` |
| `gate_g` release-audit + verdict + `_infer_regate_entry_stage` + `_reset_done_tasks_for_regate` + Full Re-Gate + `surgical_fix` + `rerun_release_audit` + `verify_done` smoke | **M** → becomes Verifikácia | `backend/services/orchestrator.py` |
| `release` stage: `_release_auto_publish`, `_release_auto_uat_deploy`, `_run_uat_deploy`, `_verify_uat_serves`, `uat_accept`, `retry_publish`, `_project_is_deployable`, `_latest_uat_deploy` | **R** → moves OUT to deploy subsystem | `backend/services/orchestrator.py` |
| Autonomy system (`_autonomy_enabled` binary toggle + `_maybe_autonomous_*` family + `_record_autonomous_gate` + `autonomous_decisions_summary`) | **R** → 4-level dial evaluator | `backend/services/orchestrator.py` |
| Coordinator role logic (`_coordinator_relay`, `_coordinator_synthesis`, `coordinator_triage`, `_execute_coordinator_directive` + 9 executors, `apply_coordinator_recommendation`, `CoordinatorDirective`) | **R** → AI-Agent-does-it / direct comms | `backend/services/orchestrator.py` |
| Fast-fix lane (`FAST_FIX_STAGE_ORDER`, `_fast_fix_auto_deploy`, release carve-out :1862/:3620, `_prepend_fast_fix_directive`) | **M** → simplified short path | `backend/services/orchestrator.py` |
| `flow_type` values `cr` / `bug` | **X** (confirm w/ Manažér; OQ-1) | `backend/db/models/pipeline.py` |

### 2.2 agent-invocation

| Component | Disp | Files |
|---|---|---|
| `claude_agent.invoke_claude` headless primitive (session create/resume, charter inject, model/effort/json-schema, transient retry, streaming, usage metering) | **K** | `backend/services/claude_agent.py` |
| PTY substrate (spawn + WS broadcast + durable disk log + rotation/retention + cross-restart auto-resume) | **K** | `backend/services/agent_terminal.py` |
| Transient retry / timeout / usage-metering / streaming infra | **K** | `claude_agent.py`, `agent_terminal.py` |
| Per-(project,role) session continuity (`OrchestratorSession` UUID, mint-once → `--resume`) | **M** → per-(project,{ai_agent\|auditor}); AI-Agent session not reset between phases | `backend/db/models/orchestrator.py`, `backend/services/orchestrator.py` |
| Charter injection (`--append-system-prompt <.claude/agents/<role>/CLAUDE.md>`) | **M** → 2 sets + shared base; path slugs `ai-agent`/`auditor` | `backend/services/orchestrator.py`, `templates/coordinator-settings.json` |
| `_resolve_dispatch_overrides` per-(owner,role) model/effort | **M** → 2 roles; Auditor effort default; coexist with dial | `backend/services/orchestrator.py` |
| `agent_terminal._VALID_ROLES` / `_DEBUG_ATTACH_ROLES` / `available_roles()` | **M** → `{ai-agent, auditor}` (exhaustive consumer sweep) | `backend/services/agent_terminal.py`, `backend/api/routes/agent_terminal.py` |
| `/debug-terminal` headless→PTY bridge | **M** → promoted to first-class AI Agent session; unify headless-write vs PTY-watch IO | `backend/api/routes/pipeline.py:289` |
| `json-schema` grammar-constrained status block (`PIPELINE_STATUS_JSON_SCHEMA`) | **M** → new 4-phase schema passed through | `backend/services/claude_agent.py`, `backend/services/pipeline_status.py` |
| `claude_subprocess.run_claude_stream` stateless one-shot chat | **R** → persistent memory-bearing session (salvage stream-parse helper) | `backend/services/claude_subprocess.py` |
| Helper-spawning of dynamic ephemeral agents + Helpers-panel feed | **R** (net-new: CLI sub-agent tool + stream event capture) | new + `backend/services/agent_terminal.py`, `pipeline_activity.py` |

### 2.3 pipeline-status-fastfix (engine↔FE contract)

| Component | Disp | Files |
|---|---|---|
| `task_plan` models (`TaskPlan`/`Epic`/`Feat`/`Task` + skeleton/per-feat passes + `extract_task_plan_json`) | **K** | `backend/services/pipeline_status.py` |
| `extract_report_body` + dual transport (`parse_status_block` / `parse_structured_output`) | **K** | `backend/services/pipeline_status.py` |
| `aggregate_pipeline_usage` (version grand total) + `UsageTotals`/`ModelTokens`/`by_model` | **K** | `backend/services/pipeline_metrics.py` |
| `pipeline_activity.py` (stream-json → Slovak activity lines) | **K** (enrich helper-spawn lines) | `backend/services/pipeline_activity.py` |
| `PipelineWsRegistry` connect/disconnect/broadcast | **K** | `backend/services/pipeline_ws.py` |
| `fast_fix.py` `bump_patch` / `latest_semver_version` / `create_patch_version` | **K** | `backend/services/fast_fix.py` |
| `pipeline.py` `POST /fast-fix` entry (`start_fast_fix`) | **K** | `backend/api/routes/pipeline.py` |
| `BLOCK_KINDS` + core `PipelineStatusBlock` (awaiting → manazer) | **M** | `backend/services/pipeline_status.py` |
| Gate-E signals (`topic`/`coverage_complete`/`findings`/`proposed_fix`) | **M** → repurpose findings shape for Auditor verdict | `backend/services/pipeline_status.py` |
| `pipeline_ws.py` presence reads + `_Conn.away` toggle (sync, lock-free — keep invariant) | **M** → Director→Manazer relabel | `backend/services/pipeline_ws.py` |
| `pipeline.py` `_board()` assembler | **M** → 4-phase state + who's-up + dial buttons | `backend/api/routes/pipeline.py` |
| `pipeline.py` `POST /action` (gate_e/coordinator dispatch branches) | **M** → schvaľovacie body verbs | `backend/api/routes/pipeline.py` |
| `pipeline.py` / `pipeline_ws.py` `require_ri_role` / `verify_ws_token` / `role=='ri'` | **M** → relabel, keep logic | both |
| `fast_fix.py` `ensure_build_task` / `kickoff_directive` | **M** → v2 short-path | `backend/services/fast_fix.py` |
| `pipeline_status.STAGES` (11-stage enum) | **R** → 4 phases | `backend/services/pipeline_status.py` |
| `aggregate_usage_by_role` (per-role-of-origin split) | **R** → per-phase grouping | `backend/services/pipeline_metrics.py` |
| `task_pass` (per-task Auditor verdict) | **X** | `backend/services/pipeline_status.py` |
| `CoordinatorDirective` + `CoordinatorTarget` (triage/confidence/proposed_action) | **X** | `backend/services/pipeline_status.py` |
| `pipeline.py` `POST /debug-terminal` (role-keyed attach) | **R/X** → first-class AI Agent terminal | `backend/api/routes/pipeline.py` |

### 2.4 frontend-pipeline-ui

| Component | Disp | Files |
|---|---|---|
| Sidebar shell composition (nex-shared Sidebar/NavItem/Brand/UserCard, collapse, pin, presence toggle) | **K** | `frontend/src/components/layout/Sidebar.tsx` |
| `PipelineWsRegistry`-fed live-update wiring (xterm/WS plumbing) | **K** | `frontend/src/pages/AgentTerminalPage.tsx`, stores |
| Unified tone palette (`StatusTone`, `TONE_*`, `TASK_STATUS_*`) | **K** (salvage from labels.ts) | `frontend/src/components/cockpit/labels.ts` |
| Sidebar nav list (rename AG Koordinátor→AI Agent, Orchestrácia→Vývoj; remove Špecifikácie; add Zákazníci/UAT/PROD; reorder FINAL) | **M** | `Sidebar.tsx` |
| Sidebar Director→Manažér labels + `čaká na Director-a` badge + presence copy | **M** | `Sidebar.tsx` |
| `TaskPlanPanel` tree (fetch, rollup, expand/collapse, progress %) | **M** → persist expand state + EPIC=purple/FEAT=yellow/TASK=blue + re-home to Návrh + drive Programovanie split; remove per-task `TaskAuditPanel` | `frontend/src/components/cockpit/TaskPlanPanel.tsx` |
| `AgentTerminalPage` (single-role xterm chrome) | **M** → AI Agent tab chrome (header status, 4-phase strip, Helpers panel) | `frontend/src/pages/AgentTerminalPage.tsx` |
| `NewProjectPage` (single/multi-module choice + F-004 flags) | **M** → archetype (Štandardný/Web) + MANDATORY auth-mode; drop multimodule + `enable_coordinator` | `frontend/src/pages/NewProjectPage.tsx` |
| `NewVersionPage` (version#/name/short intent, spec deferred) | **M** → inline free-text Zadanie + `Spustiť tvorbu špecifikácie`; first default v0.1.0 | `frontend/src/pages/NewVersionPage.tsx` |
| `VersionDetailPage` (read-only stats, stale 5-role names) | **M** → AI Agent/Auditor; link into Vývoj | `frontend/src/pages/VersionDetailPage.tsx` |
| `App.tsx` route table | **M** → drop /project-specs + /mm*; add /customers /uat /prod; rename routes | `frontend/src/App.tsx` |
| `CockpitPage` build-board shell | **R** → Vývoj horizontal 4-phase bar (chips = tabs) | `frontend/src/pages/CockpitPage.tsx` |
| `PipelineRail` (11-stage list + 5-agent chips) | **R** → horizontal 4-phase chip bar | `frontend/src/components/cockpit/PipelineRail.tsx` |
| `PipelineActionBar` (767 LOC gate-action surface) | **R** → schvaľovacie body buttons (Schváliť/Uprav/Pokračovať/Spustiť) | `frontend/src/components/cockpit/PipelineActionBar.tsx` |
| `ExchangePanel` (Coordinator/Gate-E/gate_g signal wiring) | **R** → phase-tab content + terminal peek | `frontend/src/components/cockpit/ExchangePanel.tsx` |
| `labels.ts` STAGE/ROLE maps (11 stages / 6 actors) + Coordinator/triage/regate labels | **R** → 4 phases / 2-3 actors (salvage palette) | `frontend/src/components/cockpit/labels.ts` |
| `ProjectSpecsPage` + /project-specs route + 📖 Špecifikácie nav item | **X** | `frontend/src/pages/ProjectSpecsPage.tsx`, `App.tsx`, `Sidebar.tsx` |
| MM pages (`MMOverviewPage`/`MMDepMapPage`/`MMModulePage`) + /mm* routes | **X** | `frontend/src/pages/MM*.tsx`, `App.tsx` |
| Zákazníci page (per-project customer registry, form) | **R** (net-new) | new `frontend/src/pages/CustomersPage.tsx` |
| UAT page (version × customer matrix + Nasadiť + Akceptovať) | **R** (net-new) | new `frontend/src/pages/UatPage.tsx` |
| PROD page (version × customer matrix + Nasadiť) | **R** (net-new) | new `frontend/src/pages/ProdPage.tsx` |

### 2.5 infra-keep-boundary

| Component | Disp | Files |
|---|---|---|
| Credentials registry + filesystem store | **K** | `backend/services/credentials.py` |
| Port registry (D-020) | **K** | `backend/services/port_registry.py` |
| UAT provisioner (Traefik, synthetic secrets, `uat-<slug>` namespacing) | **K** (becomes per-customer deploy backend) | `backend/services/uat_provisioner.py` |
| KB trio (writer / manager / search) | **K** | `backend/services/knowledge_base_writer.py`, `knowledge_manager.py`, `knowledge_search.py` |
| Release notes serving | **K** | `backend/services/release_notes.py` |
| Version + Task CRUD + models (VERSION → EPIC → FEAT/BUG → TASK) | **K** | `backend/services/version.py`, `task.py`, `db/models/versions.py`, `db/models/tasks.py` |
| `system_settings.py` key-value model | **K** | `backend/db/models/system_settings.py` |
| `project_service.py` alias | **K** | `backend/services/project_service.py` |
| `system_setting.py` DEFAULT_SETTINGS registry | **M** → add `miera_autonomie` + per-phase metrics keys; retire 11 per-role keys | `backend/services/system_setting.py` |
| `metrics.py` per-role ROI logic | **R** → per-phase | `backend/services/metrics.py` |
| `api/routes/metrics.py` response shape | **M** → per-phase | `backend/api/routes/metrics.py` |
| `project.py` CRUD + `set_uat_slug` (category semantics, Director naming) | **M** | `backend/services/project.py` |
| `create_project_postscaffold.py` (Director naming, module seeding) | **M** → archetype scaffold, Manažér | `backend/services/create_project_postscaffold.py` |
| `projects.py` model: `Project` keep; `category` CHECK; add auth-mode | **M** | `backend/db/models/projects.py` |
| `Epic.module_id` column + FK | **X** | `backend/db/models/tasks.py` |
| `ProjectModule` + `module_dependency` services/models/routes | **X** | `backend/services/project_module.py`, `module_dependency.py`, `db/models/projects.py`, routes |
| `live_documents.py` (ProjectModule import, `multimodule` branch, module-event fns) | **M** → strip MM; folds into AI-Agent memory | `backend/services/live_documents.py` |
| NEW: Zákazníci registry table + service | **R** (net-new) | new `backend/db/models/customers.py`, `backend/services/customer.py`, routes |
| NEW: per-customer deploy/acceptance audit-log + UAT/PROD matrix service | **R** (net-new, wraps uat_provisioner) | new `backend/services/deploy.py`, routes |
| NEW: AI-Agent per-project memory store | **R** (net-new) | new memory module + path convention |

---

## 3. CR breakdown (ordered, dependency-aware)

Each CR is independently buildable + verifiable; dependencies precede dependents. **Verification** is concrete (a command / observed behavior that proves done). Requirement coverage is mapped in §3.x and summarized in the matrix at the end.

> Convention: backend CRs verified with `ruff format --check && ruff check && pytest` + the named drift/contract test; FE CRs with `npm run type-check && npm run build`. No mypy (NEX Studio backend has no Python type-checker by decision).

### Milestone A — Foundations: data model, enums, renames, multi-module removal

**CR-V2-001 — Stage & actor enum rebuild (4 phases / 2 roles) + migration**
- Scope: rewrite `STAGE_VALUES` → `(priprava, navrh, programovanie, verifikacia, done)`; `ACTOR_VALUES` → `(ai_agent, auditor)` (+ `system` participant); rebuild `STAGE_ACTOR` map; `OrchestratorSession` role CHECK → 2 roles; **also rewrite `UserAgentSettings.agent_role` CHECK (`ck_user_agent_settings_role`, `backend/db/models/foundation.py:90`) from the 5 v1 roles to `{ai_agent, auditor}`** — this is a SECOND surviving role CHECK (migration 061) that CR-V2-007's collapse to 2 roles would otherwise be DB-rejected by. Alembic migration 069: rewrite `ck_pipeline_state_current_stage` / `ck_pipeline_message_stage` / `ck_pipeline_state_current_actor` / `OrchestratorSession.role` CHECK **AND `ck_user_agent_settings_role`** + a data-migration step for any non-frozen rows (existing `user_agent_settings` rows for retired roles deleted; the project-owner row re-seeded for the 2 v2 roles) (cutover strategy = OQ-6; default: legacy v1 rows on the branch DB are wiped, since v2 starts fresh builds).
- **DB-value vs charter-path spelling (carried through every role-keyed CR):** the **DB enum/CHECK values use underscore** (`ai_agent`, `auditor`) — matching the existing `actor`/`role` snake_case DB convention — while the **charter filesystem path uses hyphen** (`.claude/agents/ai-agent/CLAUDE.md`). CR-V2-007 maps DB `ai_agent` ↔ path slug `ai-agent` explicitly; do not let the two spellings diverge silently (R-SWEEP).
- Files: `backend/db/models/pipeline.py`, `backend/db/models/orchestrator.py`, `backend/db/models/foundation.py`, `migrations/versions/069_*.py`.
- Depends-on: —
- Verification: `alembic upgrade head` clean on a fresh DB; the schema-drift test passes (`pytest -k drift`); new CHECK rejects a `current_stage='gate_a'` insert; **`INSERT … agent_role='implementer'` into `user_agent_settings` is rejected, `agent_role='ai_agent'` accepted.**
- Requirements: ARCH-1 (partial), PIPE-1, VERSION-3 (enum side).

**CR-V2-002 — Multi-module removal (BE models, services, routes, drift) + migration**
- Scope: drop `ProjectModule`, `ModuleDependency`, `Epic.module_id`; remove `project_module.py`, `module_dependency.py` services + their routes; strip MM from `live_documents.py` (ProjectModule import, `category=='multimodule'` branch, `generate_module_event_entry`/`append_module_event`/`ModuleEventData`); remove imports from `db/base.py`, `db/models/__init__.py`, `main.py`, schemas. Migration 070: drop-table `project_modules` + `module_dependencies`, drop-column `epics.module_id`.
- Files: `backend/db/models/projects.py`, `backend/db/models/tasks.py`, `backend/services/project_module.py`, `module_dependency.py`, `live_documents.py`, `db/base.py`, `db/models/__init__.py`, `main.py`, `backend/schemas/*module*`, `migrations/versions/070_*.py`.
- Depends-on: —
- Verification: `pytest` green; `grep -rn "module_id\|ProjectModule\|ModuleDependency" backend/` returns nothing in live code; drift test updated and passing; `alembic upgrade head` drops the tables.
- Requirements: (supports CREATE-1/CREATE-2; the "dropped" multi-module line).

**CR-V2-003 — Multi-module removal (FE pages, routes, types)**
- Scope: delete `MMOverviewPage`/`MMDepMapPage`/`MMModulePage`, the /mm* routes, `types/projectModule.ts`/`moduleDependency.ts`, `api/projectModules.ts`; remove single/multi-module references (let type-check be the gate after delete).
- Files: `frontend/src/pages/MM*.tsx`, `frontend/src/App.tsx`, `frontend/src/types/*`, `frontend/src/api/projectModules.ts`.
- Depends-on: CR-V2-002.
- Verification: `npm run type-check && npm run build` clean; `grep -rn "multimodule\|ProjectModule\|/mm" frontend/src` empty.
- Requirements: (supports CREATE-1/CREATE-2).

**CR-V2-004 — Operator rename Director → Manažér + `awaiting_director` → `awaiting_manazer`**
- Scope: rename the operator label across BE + FE: `STATUS_VALUES` `awaiting_director` → `awaiting_manazer` (migration 071, data-migrate existing STATUS *values*), FE labels, `čaká na Director-a` → `čaká na Manažéra`, doc-title `Na rade: Director` → `Manažér`, ROLE_LABEL `Director · Ri`, `pipeline_ws` presence relabel, project owner/notification docstrings, scaffold comments. **Only the STATUS *value* rename is DDL** (the CHECK that lists `awaiting_*`); the `awaiting_director_since` / `director_wait_total` **columns keep their names** (no rename DDL — renaming live columns is needless churn; their docstrings/comments may be reworded in-code only). `status` is `String(20)` — no resize. Keep the `ri` role gate logic (auth unchanged per design §8 Open #6) — relabel only.
- Files: `backend/db/models/pipeline.py`, `backend/services/{orchestrator,pipeline_ws,project,create_project_postscaffold}.py`, `frontend/src/components/layout/Sidebar.tsx`, `frontend/src/pages/CockpitPage.tsx`, `labels.ts`, `migrations/versions/071_*.py`.
- Depends-on: CR-V2-001 (touches STATUS).
- **Note on the 3 ORM status listeners** (director-wait timer, single-flight clear, block_reason clear, `pipeline.py`): they key on the STATUS *value*. CR-V2-004 renames the value but the **listener re-wire to the new value is owned by CR-V2-009** (R-LISTENERS) so the value rename and the engine's new-status logic land together — a rename here alone would silently stop the listeners firing. CR-V2-004 only updates the value + labels; it does NOT touch listener bodies.
- Verification: `pytest` green; `grep -rin "director" backend/services frontend/src` returns only intentional historical/rationale references; FE renders "čaká na Manažéra"; no column-rename appears in migration 071 (only the CHECK + data-migrate).
- Requirements: UI-7, RULES-2 (comms-naming part), partial COMMS-4.

**CR-V2-005 — Project archetype + mandatory auth-mode + per-archetype surface scaffold (model + create flow)**
- Scope: replace `Project.category` ('singlemodule'|'multimodule') with `type` ('standard'|'web') and add mandatory `auth_mode` ('password'|'token') column; migration 072 (data-migrate existing 'singlemodule'→'standard', set default auth_mode for legacy); update `project.py` create + `ProjectCreate` schema; remove `enable_coordinator` F-004 flag.
- **Archetype = scaffold template that emits a per-type SURFACE COMPOSITION** (design §4.2 "a project = one backend + one-or-more frontend surfaces; the type is a preset composition / scaffold template"). `create_project_postscaffold.py` branches on `type`:
  - **Standard** → BE + a single app-FE surface (today's shape).
  - **Web** → BE + **admin-FE surface + public-site surface** (the second frontend surface — a managed/monitored site whose admin-FE configures the site and shows its metrics). nex-shared supplies the cross-surface web-platform solutions.
  - Both branches additionally pick login flavour from `auth_mode` (password-login like Studio / token-launch like Inbox) on the BE + each FE surface.
- **Web-archetype commerce add-on (cart / checkout / payments + bidirectional IS-integration) is DEFERRED** — see §7 Open #11. v0.x Web = managed/monitored site **without** commerce; the add-on is a later, carefully-designed capability (the IS-integration is the real complexity, design §4.2). The scaffold leaves a documented extension seam but emits no commerce code in v2.0.0.
- **Mobil archetype is DEFERRED** (design §8 Open #1, Director 2026-06-26) — the `type` enum ships only `standard`/`web`; Mobil is a dedicated future design round (toolchain / build+test / Auditor verification), recorded not specified, so the v2 archetype set is provably intentional.
- Files: `backend/db/models/projects.py`, `backend/services/{project,create_project_postscaffold}.py`, `backend/schemas/project.py`, `migrations/versions/072_*.py`, scaffold templates (per-surface).
- Depends-on: CR-V2-002.
- Verification: `pytest` green; create a project with `type=web, auth_mode=token` → scaffold produces **two FE surfaces (admin-FE + public-site)** AND token-launch login on each; create with `type=standard` → exactly one app-FE surface; create-without-auth_mode rejected (422); no commerce/cart/checkout/payment artifact is scaffolded for Web (grep the emitted tree).
- Requirements: CREATE-1, CREATE-2, CREATE-3 (BE), CREATE-4.

### Milestone B — New engine core (state machine + actions + dial)

**CR-V2-006 — Status-block schema rebuild (4-phase, drop Coordinator/task_pass)**
- Scope: rewrite `pipeline_status.STAGES` → 4 phases; `awaiting` → manazer; drop `CoordinatorDirective`/`CoordinatorTarget`/`task_pass`; keep `TaskPlan`/`Epic`/`Feat`/`Task` + skeleton/per-feat passes + `extract_task_plan_json` + `extract_report_body` + dual transport; repurpose `findings`/`proposed_fix` for Auditor; update `PIPELINE_STATUS_JSON_SCHEMA`. Update `schemas/pipeline.py` Literals + FE codegen regen.
- Files: `backend/services/pipeline_status.py`, `backend/services/claude_agent.py`, `backend/schemas/pipeline.py`.
- Depends-on: CR-V2-001.
- Verification: `pytest -k pipeline_status` green; parse a sample 4-phase block → `PipelineStatusBlock`; parse a malformed block → `blocked` (never guess); FE union regenerates to 4 phases.
- Requirements: PIPE-3 (contract), ARCH-5 (status side).

**CR-V2-007 — Two-agent invocation: re-key sessions, charters, model/effort**
- **✅ OQ-2 RESOLVED (Model B — headless `--resume` + relay; §7 OQ-2).** This CR keeps `invoke_claude` verbatim for the AI Agent: the engine is the sole serialized writer driving a warm persistent session via `-p --resume` (NOT a true concurrent-writer PTY); the AI-Agent session is not reset across phases. SPIKE-IO is now just the confirmation/wiring of the relay model, not a blocker — CR-V2-007 is buildable once SPIKE-IO confirms the relay wiring.
- Scope: collapse `_AGENT_SESSION_ROLES`, `_resolve_orch_session`, charter-path slugs, `_resolve_dispatch_overrides`, `agent_terminal._VALID_ROLES`/`_DEBUG_ATTACH_ROLES`/`available_roles()` to `{ai_agent, auditor}` (DB values; charter-path slugs `ai-agent`/`auditor` — see CR-V2-001 spelling note) (exhaustive FE+BE sweep — debug-attach was missed twice historically); 2 `Pravidlá agenta` charter files + 1 shared base concatenated via `--append-system-prompt`; AI-Agent session NOT reset across phases; Auditor a separate session (independence enforced at invocation); Auditor effort default. Keep `invoke_claude` verbatim (contingent on OQ-2 above).
- Files: `backend/services/{orchestrator,agent_terminal}.py`, `backend/api/routes/agent_terminal.py`, `backend/db/models/orchestrator.py`, `templates/` (new `ai-agent`/`auditor` charters + shared base), project scaffolder.
- Depends-on: CR-V2-001; **SPIKE-IO (OQ-2 resolved).**
- Verification: `pytest` green; spawn AI Agent + Auditor sessions → 2 distinct `OrchestratorSession` rows; `grep -rn "coordinator\|designer\|customer\|implementer" backend frontend/src` shows no surviving role identifiers; charter file injected as system prompt on first call.
- Requirements: ARCH-1, ARCH-6, RULES-1, RULES-2, RULES-3, RULES-4, AUTON-5 (Auditor-effort hook).

**CR-V2-008 — Miera autonómie 4-level dial (settings + evaluator + override storage)**
- Scope: add `miera_autonomie` to `DEFAULT_SETTINGS` (4 presets: `plna`/`len_na_konci`/`pri_klucovych_bodoch`/`po_kazdej_faze`) as the **global default**; new dial-driven schvaľovací-bod evaluator replacing `_autonomy_enabled` + `_maybe_autonomous_*`; carve out the two always-stops (Špecifikácia approval; deploy); dial scales Auditor depth + sets fast-fix=full-auto. Keep "record every auto-decision Manažér-visibly" + auto-chain.
- **Override storage (AUTON-6 — was logic-only, now data-model too):** resolution order `per-build → per-project → global`. Persist all three layers:
  - **global** = `DEFAULT_SETTINGS['miera_autonomie']` (system_settings KV, no migration).
  - **per-project** = new nullable `projects.miera_autonomie` column (NULL = inherit global).
  - **per-build** = new nullable `pipeline_state.miera_autonomie` column (NULL = inherit per-project).
  - Migration **076** adds both nullable columns (net-new, NULL-default → safe). Resolver reads build → project → global, first non-NULL wins.
- Files: `backend/services/system_setting.py`, `backend/services/orchestrator.py`, `backend/db/models/{projects,pipeline}.py`, `migrations/versions/076_*.py`.
- Depends-on: CR-V2-006, CR-V2-007.
- Verification: `pytest -k autonomy` green; with `plna` a build runs Návrh→Verifikácia non-stop; with `po_kazdej_faze` it halts after Návrh/Programovanie/Verifikácia but NOT a dial-added Príprava stop; **a per-build value beats a per-project value beats the global default (assert resolver returns the build value when set, falls back when NULL)**; drift test passes with the two new columns.
- Requirements: AUTON-1, AUTON-2, AUTON-3, AUTON-5, AUTON-6, SET-1.

**CR-V2-009 — `apply_action` + `STAGE_ORDER` rebuild (4-phase state machine, sole mutator)**
- **✅ OQ-1 RESOLVED (Manažér, 2026-06-26): `cr`/`bug` flow_types are DROPPED.** Today `_stage_order_for` (`orchestrator.py:6261-6262`) returns the full `STAGE_ORDER` for `cr`/`bug`; since they are removed (CR-V2-031), only `new_version` (full 4-phase) + `fast_fix` (short path) survive, so no separate stage-order variant is needed. The BUG *work-item* node type (EPIC→FEAT/BUG→TASK) is unaffected — only the build-entry flow_type is dropped.
- Scope: rebuild `STAGE_ORDER` → 4 phases + `_next_stage`; collapse `apply_action` to: `start` (Spustiť tvorbu špecifikácie), the ALWAYS-mandatory `approve_spec` stop (end Príprava, dial-independent), dial-governed `schvalit`/`uprav`/`pokracovat` between Návrh/Programovanie/Verifikácia, Auditor verdict looping fixes to AI Agent (bounded ~5, then escalate to Manažér). Keep sole-mutator invariant. Remove Coordinator relay/triage executors + `apply_coordinator_recommendation`.
- **Re-wire the 3 ORM status listeners (R-LISTENERS — owned here, not CR-V2-004):** the director-wait timer, single-flight clear, and block_reason clear listeners (`pipeline.py`) key on STATUS values; CR-V2-004 renamed the value, this CR re-points the listener bodies to the new STATUS and new-status engine semantics so they keep firing (sync, lock-free invariant preserved — no async refactor).
- **Per-turn phase stamp (owned here; consumed by CR-V2-029):** each engine fold/seed message payload gets a `phase` field stamped with the build's current phase (`priprava`/`navrh`/`programovanie`/`verifikacia`), the per-phase analogue of today's `payload.metrics_role`. CR-V2-029's per-phase metrics group on this stamp; without it CR-V2-029 has no phase to aggregate by.
- **auto_chain bound — re-wire DEFERRED to CR-V2-014 (R-AUTOCHAIN):** today `auto_chain_limit` (`orchestrator.py:1004`) = `len(STAGE_ORDER) + ceiling + _GATE_E_TOPIC_SLACK`. This CR drops the Gate-E self-loop slack but the new backstop must also budget the Auditor self-loops (the bounded ~5 fix↔re-verify rounds), which only exist after CR-V2-014. So this CR sets a provisional bound = `len(4-phase STAGE_ORDER) + ceiling`; **CR-V2-014 finalizes it** by adding a named `AUDITOR_LOOP_MAX` term once the Verifikácia/upfront fix-loops exist, or a legit long Auditor loop mis-trips the backstop.
- Files: `backend/services/orchestrator.py`, `backend/services/pipeline_runner.py`, `backend/db/models/pipeline.py`.
- Depends-on: CR-V2-008.
- Verification: `pytest -k "orchestrator or apply_action"` green; `approve_spec` is offered at end-Príprava regardless of dial; an Auditor FAIL loops to AI Agent, escalates after the 5th attempt; `apply_action` remains the only function mutating `pipeline_state` (assert via grep); the 3 ORM listeners fire against the renamed STATUS (assert a director-wait timer entry still records); a fold message payload carries a `phase` field.
- Requirements: ARCH-1, ARCH-2, AUTON-2, AUTON-3, PIPE-1, PIPE-2, PROG-1, AUD-2, AUD-3, AUD-4.

### Milestone C — Phases as agent behaviour (Príprava / Návrh / Programovanie)

**CR-V2-010 — Príprava phase (interactive spec dialogue → Špecifikácia)**
- Scope: implement the Príprava round — start trigger (`Spustiť tvorbu špecifikácie` auto-activates AI Agent tab + injects init prompt "Načítaj zadanie a začni prípravu špecifikácie"); interactive terminal dialogue (read Zadanie, systematize, ask clarifying questions until understood, propose improvements); output Špecifikácia `.md` persisted to the Príprava phase artifact; hard `approve_spec` stop.
- Files: `backend/services/orchestrator.py`, new Príprava round + artifact persistence; AI Agent charter (Príprava behaviour).
- Depends-on: CR-V2-009.
- Verification: a test build starting with a vague Zadanie produces clarifying questions before any design; Špecifikácia renders in the Príprava artifact; Návrh cannot start until `approve_spec`.
- Requirements: PREP-1, PREP-2, PREP-3, PREP-4, AUTON-3, VERSION-3, RULES-3 (read-first/ask-until-understood).

**CR-V2-011 — Návrh phase (one design doc + task plan folds in)**
- Scope: implement the Návrh round — AI Agent produces ONE coherent design `.md` (overview/data-model/API/BE+FE, sized to project) with EPIC→FEAT→TASK task plan as its LAST part (fold `_run_task_plan_round` incremental skeleton/per-feat passes into Návrh, written via existing Task/Epic/Feat ORM); automated per dial; at the post-Návrh schvaľovací bod surface BOTH the AI Agent's clarification questions and the Auditor's upfront review (CR-V2-013).
- Files: `backend/services/orchestrator.py` (Návrh round, fold task_plan), `backend/services/pipeline_status.py` (task-plan passes retained).
- Depends-on: CR-V2-010.
- Verification: Návrh produces a single `.md` artifact ending in the task-plan tree; the standalone task_plan stage no longer exists; a large plan still generates via incremental passes (no parse exhaustion); the dial governs whether the post-Návrh stop fires.
- Requirements: NAVRH-1, NAVRH-2, NAVRH-3, NAVRH-4, ARCH-2.

**CR-V2-012 — Programovanie phase (AI Agent self-checking coding loop)**
- Scope: rebuild `_run_build_round` as AI-Agent self-checking coding executing the task plan (own tests/verification per task; NO per-task Auditor); remove `_verify_task`/`_audit_prompt_for_task`/`_task_audit_verdict`/`_record_task_summary`/per-task `_AUTO_FIX_RETRIES`/HALT→relay; keep lost-work audit + mechanical commit verify; helper-spawn during bulk tasks (CR-V2-018 surfaces them).
- Files: `backend/services/orchestrator.py`.
- Depends-on: CR-V2-011.
- Verification: `pytest -k build` green; a multi-task build runs with self-checks and no per-task audit verdict messages; `verify_mechanical`/`_audit_lost_work` still fire; committed-but-lost work is surfaced not dropped.
- Requirements: PROG-1, ARCH-5, AUD-1 (Auditor-not-per-task side).

### Milestone D — Auditor (independent verifier, two touchpoints)

**CR-V2-013 — Auditor upfront spec/design review (replaces Gate-E Customer function)**
- Scope: replace the entire Gate-E sub-state-machine (`_run_gate_e_round`, `_gate_e_*`, fix/leave/end_gate_e, `_maybe_autonomous_gate_e_continue`, gate_e audit md) with ONE Auditor upfront-review invocation after Návrh — independently scans brief + design for holes/ambiguities/contradictions; surfaces at the post-Návrh schvaľovací bod alongside the AI Agent's questions; intensity dial-scaled; findings escalate per AUD-4 (spec/design hole → Manažér).
- Files: `backend/services/orchestrator.py`, Auditor charter (upfront-review behaviour).
- Depends-on: CR-V2-011.
- Verification: a brief with an obvious hole → Auditor surfaces it at the post-Návrh stop; no per-question Customer↔Designer ping-pong exists; light vs full review depends on the dial.
- Requirements: AUD-1(a), AUD-5, NAVRH-4, AUTON-5.

**CR-V2-014 — Verifikácia phase (Auditor end verification, replaces gate_g)**
- Scope: rebuild gate_g as Verifikácia — release-acceptance behavioural check (run app via `_run_release_smoke`) + adversarial spot-checks (security/money/core contract) + explicit §4 hard-security verification; NOT per-task; verdict + findings persist to the Verifikácia artifact; FAIL → AI Agent fix → re-verify bounded loop (~5, named constant `AUDITOR_LOOP_MAX`) → escalate; replace Director PASS/FAIL + `_infer_regate_entry_stage` regate inference with dial-governed approval; salvage `surgical_fix` targeted re-run as an AI-Agent fix scope; then Hotovo (terminal).
- **Release smoke runs against INTERNAL FIXTURES, not a customer instance.** Verifikácia's `_run_release_smoke` exercises the built app in an ephemeral/local fixture context (the behavioural acceptance pillar) — it does NOT deploy to or touch any registered customer's UAT/PROD. Deploy is entirely OUT of the pipeline (design D6, §3.1); reaching Hotovo means "verified", not "deployed". (Closes the test-lens contradiction that release smoke might hit a customer instance.)
- **Finalize the auto_chain backstop (R-AUTOCHAIN, deferred from CR-V2-009):** now that the Verifikácia + upfront fix↔re-verify loops exist, set the auto-chain bound to include a named `AUDITOR_LOOP_MAX` term so a legitimate long Auditor loop does not mis-trip the backstop. Update `orchestrator.py` auto_chain computation + the `pipeline_runner` consumer.
- Files: `backend/services/orchestrator.py`, `backend/services/pipeline_runner.py`.
- Depends-on: CR-V2-012, CR-V2-013.
- Verification: a build with an injected behavioural failure → Verifikácia FAILs, AI Agent fixes, Auditor re-verifies, reaches Hotovo; a §4 credential leak in code → Auditor flags it; verdict persisted to Verifikácia tab artifact; release smoke runs without provisioning any customer instance (grep: no `uat_provisioner`/`deploy.py` call from the Verifikácia path); the auto-chain bound includes `AUDITOR_LOOP_MAX` (a 5-round Auditor loop does not trip the backstop).
- Requirements: VERIF-1, VERIF-2, VERIF-3, AUD-1(b), AUD-2, AUD-3, AUD-6.

### Milestone E — AI Agent terminal (PTY warm context + helpers)

**SPIKE-IO (pre-Milestone-B confirmation spike — OQ-2 RESOLVED = Model B; de-risks R-IO, feeds CR-V2-007 & CR-V2-015)**
- **OQ-2 is decided (Model B: headless `--resume` + relay, engine = sole writer — §7 OQ-2).** This spike is therefore a small time-boxed CONFIRMATION + wiring step (no longer a risky 2-writer locking design): confirm the relay/enqueue model end-to-end — engine `-p --resume` turns + the live WS-rendered view of the session + the injection point where a Manažér message becomes the next turn — and assert there is exactly ONE writer to the session. Output: the confirmed relay wiring recorded in the decision-log.
- Files: throwaway prototype + decision-log entry (`docs/architecture/nex-studio-v2-lead-engineer-auditor.md`).
- Depends-on: —
- Verification: a 2-writer prototype demonstrates non-interleaved input into one `claude` session under the chosen model; OQ-2 marked resolved; CR-V2-007 (and thus Milestone B) unblocked.
- Requirements: (de-risks ARCH-1/ARCH-3; gates ARCH-3 delivery in CR-V2-015).

**CR-V2-015 — Promote /debug-terminal to first-class AI Agent PTY session (unified IO model)**
- Scope: promote the headless→PTY bridge to the primary AI Agent session — one warm, disk-logged, browser-observable `claude` PTY per project that the engine drives AND the Manažér watches/talks to live; **implement the IO/locking model decided in SPIKE-IO** (engine prompts + Manažér keystrokes into one session — see Risk R-IO); retire role-keyed debug-attach.
- Files: `backend/api/routes/pipeline.py:289`, `backend/services/{agent_terminal,orchestrator}.py`.
- Depends-on: CR-V2-007 (which itself depends on SPIKE-IO).
- Verification: starting Príprava activates the AI Agent PTY; the Manažér can type into it mid-build without corrupting engine input (the SPIKE-IO model demonstrated end-to-end); the durable PTY log persists across BE restart.
- Requirements: ARCH-3, UI-8 (PTY side), UI-9, COMMS-2.

**CR-V2-016 — AI-Agent own persistent per-project memory (NEW capability)**
- Scope: net-new per-project memory the AI Agent reads at session start and writes freely (decisions/lessons/context/Manažér feedback), recalled on future builds; KB read via existing RAG (Qdrant+Ollama) + direct file; deliberate shared-KB writes + mandatory RAG reindex.
- **Store decision (OQ-4) — ✅ RESOLVED (Manažér, 2026-06-26) = per-project `MEMORY.md`** the charter rules tell the agent to read at session start and write to (Dedo-style; no schema, no migration; human-readable; mirrors the proven Dedo pattern). No migration 077.
- **R-DOUBLEWRITE — owned by this CR.** `live_documents` today renders STATUS.md / HISTORY.md; this CR folds that into the AI-Agent memory. Decide **retire-into-memory** (default: MEMORY.md is the single source; STATUS.md/HISTORY.md become rendered views OR are dropped) — do NOT keep both as independent writers, or they diverge. This CR explicitly resolves which of the two is the source of truth and removes the second writer.
- Files: new memory module + path convention (per-project `MEMORY.md` + topic files); `backend/services/{knowledge_*,live_documents}.py` (reused/folded); AI Agent charter (KB+memory rules). No migration (OQ-4 resolved = file-based `MEMORY.md`).
- Depends-on: CR-V2-007.
- Verification: AI Agent writes a decision to project memory; a second build of the same project recalls it; a shared-KB write triggers a RAG reindex (no filesystem↔vector drift); only ONE writer of the memory/STATUS content exists (grep: no dual STATUS.md + MEMORY.md independent write path).
- Requirements: KB-1, KB-2, KB-3, RULES-3 (KB+own-memory).

**CR-V2-017 — Communications: retire file-bus; direct Manažér↔AI Agent + notifications**
- Scope: confirm the `.dedo-channel`/Coordinator hub-and-spoke is fully removed (engine no longer buses 5 roles); Manažér↔AI Agent direct via PTY + Telegram-away (presence toggle kept); AI Agent↔Auditor = verdict into fix-loop on Vývoj; helpers internal; audit trail = PTY log + phase tabs + per-customer deploy/acceptance log; system→Manažér away/escalation/done notifications.
- Files: `backend/services/{pipeline_runner,pipeline_ws,orchestrator}.py`; remove any residual `.dedo-channel` writers.
- Depends-on: CR-V2-009, CR-V2-015.
- Verification: `grep -rn "dedo-channel" backend` empty; an away Manažér gets a Telegram notify on a build-done/escalation; audit trail reconstructable from PTY log + phase artifacts.
- Requirements: COMMS-1, COMMS-2, COMMS-3, COMMS-4, COMMS-5.

**CR-V2-018 — Ephemeral helper spawning + Helpers-panel feed (NEW)**
- Scope: net-new — AI Agent dynamically spawns ephemeral helpers (via the `claude` session's own sub-agent/Task tool — CLI-internal, not a backend helper orchestrator) for parallel/bulk sub-tasks; capture sub-agent activity from the stream-json so `pipeline_activity.py` emits Slovak helper lines; Helpers-panel feed ("+ N pomocníci" with one-line descriptions, hidden when none); Auditor explicitly excluded from helpers (independence).
- Files: `backend/services/{pipeline_activity,agent_terminal}.py`, helper-event capture; FE Helpers panel (CR-V2-022).
- Depends-on: CR-V2-015.
- Verification: a bulk task spawns ≥1 helper visible in the Helpers panel feed; a small task spawns none; the Auditor is never registered as a helper.
- Requirements: ARCH-4, UI-8 (Helpers panel side).

### Milestone F — Frontend: Vývoj board + AI Agent tab + sidebar/create flows

**CR-V2-019 — Sidebar FINAL nav + labels.ts vocabulary collapse**
- Scope: rebuild the FINAL sidebar order/scopes (Prehľad, Projekty[+📌 pin], Verzie, Zásobník, AI Agent, Vývoj, Zákazníci, UAT, PROD, Metriky, Dokumentácia, Prístupy, Aktualizácie, Nastavenia); rename AG Koordinátor→AI Agent (👨‍💻), Orchestrácia→Vývoj (🔄); remove 📖 Špecifikácie; project-scoped items disabled-when-no-project (reuse existing pattern); footer presence 🟢/🌙 + user card; collapse `labels.ts` STAGE/ROLE maps to 4 phases / {AI Agent, Auditor, Manažér}, drop Coordinator/triage/regate labels, salvage the tone palette.
- **AUD-7 (Auditor intentionally absent from the sidebar) — explicit deliverable, not implicit.** The FINAL 14-item list deliberately has NO Auditor nav item; the Auditor's verdict is reachable ONLY via Vývoj → Verifikácia (CR-V2-021). State this as an explicit invariant so a future sidebar edit cannot silently re-add it.
- Files: `frontend/src/components/layout/Sidebar.tsx`, `frontend/src/components/cockpit/labels.ts`, `frontend/src/App.tsx`.
- Depends-on: CR-V2-003, CR-V2-006.
- Verification: `npm run build` clean; sidebar renders the 14 items in order; project-scoped items greyed with tooltip when no pin; Špecifikácie absent; **no Auditor/Audítor nav item renders (assert the rendered nav list contains no Auditor entry) — the Auditor verdict is only reachable via Vývoj → Verifikácia.**
- Requirements: UI-1, UI-2, UI-3, UI-4, UI-5, UI-6, AUD-7.

**CR-V2-020 — Remove ProjectSpecsPage + /project-specs route**
- Scope: delete the page, route, nav entry; keep `KbTree`/`kbTreeBuilder` (still used by /kb); confirm the Príprava/Návrh tab markdown renderer reuses `ReactMarkdown`+`CodeBlock` (salvage before delete — OQ).
- Files: `frontend/src/pages/ProjectSpecsPage.tsx`, `App.tsx`, `Sidebar.tsx`.
- Depends-on: CR-V2-019.
- Verification: `npm run build` clean; /project-specs 404s; /kb still renders.
- Requirements: UI-2 (removal side).

**CR-V2-021 — Vývoj board (horizontal 4-phase bar, chips = tabs, persistent artifacts)**
- Scope: re-author `CockpitPage`/`PipelineRail`/`ExchangePanel`/`PipelineActionBar` → Vývoj: horizontal top phase bar (Príprava ✓ › Návrh ● › Programovanie ○ › Verifikácia ○; ✓/●/○ states; chips ARE the tabs; ●=build-position auto-advances, highlighted=viewed-tab, may differ); permanent per-phase content (Príprava=Špecifikácia .md + Schváliť špecifikáciu; Návrh=design doc incl. task plan; Programovanie=split view; Verifikácia=Auditor verdict); who's-up status; schvaľovacie body buttons (Schváliť/Uprav/Pokračovať/Spustiť, dial-governed); raw-terminal peek drawer; durable artifact persistence (audit trail).
- Files: `frontend/src/pages/CockpitPage.tsx`, `frontend/src/components/cockpit/{PipelineRail,ExchangePanel,PipelineActionBar}.tsx`.
- Depends-on: CR-V2-009, CR-V2-019.
- Verification: `npm run build` clean; the 4 phase chips render with states and are clickable; the Schváliť špecifikáciu button always shows in Príprava; finished phases stay viewable after build completes (no vanish); ●/highlighted can diverge.
- Requirements: PIPE-1, PIPE-3, UI-10, UI-11, UI-12, UI-13, UI-14, UI-15, UI-16.

**CR-V2-022 — AI Agent tab chrome (header status + 4-phase strip + Helpers panel)**
- Scope: enrich `AgentTerminalPage` → AI Agent tab: header (name + Voľný / Pracuje na <projekt> v<ver> — fáza X / Čaká na súhlas); thin 4-phase strip linking to Vývoj; durable PTY console (xterm/WS kept); Helpers panel ("+ N pomocníci", hidden when none); input box; Idle=ad-hoc consult / Building=watch+answer schvaľovacie body inline + "čaká na Manažéra" badge in Vývoj; project-scoped (follows pin); rename AgentRole 'coordinator'→'ai_agent' (sweep store/api/PersistentTerminalsLayer without breaking sessions).
- Files: `frontend/src/pages/AgentTerminalPage.tsx`, agent terminal store/api, `PersistentTerminalsLayer`.
- Depends-on: CR-V2-015, CR-V2-018, CR-V2-019.
- Verification: `npm run build` clean; header shows live status; Helpers panel appears only when helpers active; typing into the PTY reaches the live session.
- Requirements: ARCH-3, UI-8, UI-9, COMMS-2 (FE side).

**CR-V2-023 — Task-plan UX (persist expand/collapse + level colors + split view)**
- Scope: `TaskPlanPanel` — persist expand/collapse across navigation + page reload per user (store TBD per OQ; default localStorage); EPIC=purple/FEAT=yellow/TASK=blue (tune yellow on light theme); re-home into Návrh tab (durable after build) + drive the Programovanie split view (activity LEFT, plan RIGHT); remove per-task `TaskAuditPanel`/`readTaskAudit`.
- Files: `frontend/src/components/cockpit/TaskPlanPanel.tsx`.
- Depends-on: CR-V2-021.
- Verification: navigate to Metriky and back → tree state preserved; reload → preserved (per chosen store); levels color-coded; Programovanie shows split view; no per-task audit panel.
- Requirements: UI-17, UI-18, PROG-2, NAVRH-2 (UX side).

**CR-V2-024 — Create-project & create-version FE forms**
- Scope: `NewProjectPage` — replace single/multi-module toggle with archetype (Štandardný/Web) + MANDATORY auth-mode field (password/token); drop `enable_coordinator`; `NewVersionPage` — inline free-text Zadanie editor writing `docs/specs/versions/v<N>/customer-requirements.md` on save + `Spustiť tvorbu špecifikácie` action (auto-activates AI Agent tab + injects init prompt); first-version default v0.1.0; revisit/remove the "Zdediť DESIGN.md" checkbox under single-design-doc model (OQ); `VersionDetailPage` stale 5-role refs → AI Agent/Auditor + link into Vývoj.
- Files: `frontend/src/pages/{NewProjectPage,NewVersionPage,VersionDetailPage}.tsx`.
- Depends-on: CR-V2-005, CR-V2-010, CR-V2-021.
- Verification: `npm run build` clean; submit without auth-mode is blocked; saving a Zadanie writes `customer-requirements.md` and surfaces Spustiť tvorbu špecifikácie; first version suggests v0.1.0.
- Requirements: CREATE-3 (FE), VERSION-1, VERSION-2, VERSION-3, PREP-1 (FE trigger), DEPLOY-9 (v0.1.0 side).

### Milestone G — Deploy & Customers (per-customer, outside the pipeline)

**CR-V2-025 — Zákazníci registry (model + service + form page)**
- Scope: net-new per-project customer registry table + service + `CustomersPage` form capturing name+slug, subdomain, integrations, per-customer secrets (reuse `credentials.py` store; never echoed/stored in source per §4/§5 — OQ on ownership), deploy target = customer's own UAT+PROD instance/DB/data; internal apps = ICC s.r.o. through the same form (one code path, no internal/external branch).
- Files: new `backend/db/models/customers.py`, `backend/services/customer.py`, routes; migration 073; new `frontend/src/pages/CustomersPage.tsx`, `App.tsx` route, sidebar wiring (from CR-V2-019).
- Depends-on: CR-V2-005, CR-V2-019.
- Verification: add a customer via the form → row persisted; secret entered → not echoed back, not in source/logs; ICC s.r.o. registered through the identical form.
- Requirements: DEPLOY-1, DEPLOY-2, DEPLOY-3.

**CR-V2-026 — Per-customer deploy backend (productize uat_provisioner + acceptance log)**
- Scope: net-new deploy service wrapping `uat_provisioner.py` for per-customer instance provisioning/update (own DB, subdomain, integrations, URL); enforce §3.7 fresh-first-then-data-preserving (first install empty; every later deploy preserves data + secrets, runs migrations, never wipes/rotates — the inbox-UAT lesson); per-customer UAT acceptance audit-log (who/when/version/customer); versioning v0.1.0 → v1.0.0 on first PROD deploy; deploy ALWAYS manual + outside the dial.
- Files: new `backend/services/deploy.py`, routes; migration 074 (acceptance/deploy audit-log table); reuse `uat_provisioner.py`, `port_registry.py`, `credentials.py`.
- Depends-on: CR-V2-014 (verified-version boundary), CR-V2-025.
- Verification: deploy a verified version to a customer → instance provisioned; redeploy a later version → data + secrets preserved (assert no secret rotation, migrations ran); first PROD deploy bumps to v1.0.0; acceptance event logged.
- Requirements: DEPLOY-6, DEPLOY-8, DEPLOY-9, DEPLOY-10, AUTON-4.

**CR-V2-027 — UAT & PROD tabs (version × customer matrix + Nasadiť + Akceptovať gate)**
- Scope: net-new `UatPage` + `ProdPage` — version × customer matrices showing each customer's deployed version + Nasadiť (pick verified version); UAT per-customer link to UAT URL + Akceptovať (logs who/when/version/customer, marks accepted-for-PROD, opens PROD); NO PROD deploy without UAT acceptance (never bypassed); different customers may run different versions simultaneously.
- Files: new `frontend/src/pages/{UatPage,ProdPage}.tsx`, `App.tsx`, sidebar wiring.
- Depends-on: CR-V2-026.
- Verification: `npm run build` clean; PROD Nasadiť disabled until that customer's UAT is accepted; two customers shown on different versions; Akceptovať logs the event.
- Requirements: DEPLOY-4, DEPLOY-5, DEPLOY-7, DEPLOY-8.

### Milestone H — Fast-fix, metrics, settings

**CR-V2-028 — Fast-fix lane simplified (AI Agent short path + light Auditor)**
- **✅ OQ-3 RESOLVED (Manažér, 2026-06-26): fast-fix stops at "verified"; deploy via the normal manual per-customer path; `_fast_fix_auto_deploy` retired.** (Was a genuine design tension, R-FF: v1 fast-fix ended in `_fast_fix_auto_deploy`, but D6 makes deploy always manual + per-customer + outside the dial, behind the never-bypassed UAT acceptance gate.) Fast-fix stops at the same `Hotovo`/verified boundary as a normal version; the patch then goes through the ordinary manual per-customer UAT/PROD click. The faster-path value is in skipping heavy Návrh + the per-task Auditor, NOT in auto-deploy.
- Scope: keep `bump_patch`/`create_patch_version`/`POST /fast-fix`; re-target `ensure_build_task` off the per-task-audited loop onto the v2 short path (directive IS the brief, skip heavy Návrh → AI Agent fixes → light focused Auditor check (fix works + no regression) → **stop at verified per the OQ-3 resolution**); autonomous through verification (dial=full-auto, dial-able to require approval for sensitive fixes); the deploy hop is the normal manual per-customer action (CR-V2-027), NOT in-lane.
- Files: `backend/services/{fast_fix,orchestrator}.py`, `backend/api/routes/pipeline.py`.
- Depends-on: CR-V2-014, CR-V2-026; **OQ-3 resolved.**
- Verification: a fast-fix directive creates a patch version, runs the short path with a light Auditor check, and reaches the verified boundary WITHOUT auto-deploying to any customer (grep: no `_fast_fix_auto_deploy`/`deploy.py` call from the lane); zero mid-flight approvals through verification by default; the patch then appears in the UAT tab for the normal manual Nasadiť.
- Requirements: FASTFIX-1, FASTFIX-2.

**CR-V2-029 — Metrics per-phase basis (owns retiring the 11 per-role keys)**
- Scope: replace `aggregate_usage_by_role` grouping key (role-of-origin = `payload.metrics_role`, `pipeline_metrics.py:154`) with PHASE (AI Agent→current phase, helpers→spawning phase, Auditor→Verifikácia) — grouping on **the per-turn `phase` stamp added in CR-V2-009** (replacing `metrics_role`); keep `aggregate_pipeline_usage`/`UsageTotals`/`by_model`; rewrite `metrics.py` `COMPARISON_ROLES`/`_build_roles`/`_role_wage` → per-phase; per-customer deploy = separate ops cost; agent-vs-human comparison kept per phase; `api/routes/metrics.py` response → per-phase shape.
- **This CR is the OWNER of retiring the 11 v1 per-role metrics keys.** Remove the 11 per-role rate/wage settings keys from `DEFAULT_SETTINGS` (the "retire 11 per-role keys" disposition in the §2.5 boundary table) and add the per-phase rate/wage keys (4 phases) in their place — `system_setting.py` registry. No other CR retires them; left in place they would be dead settings driving nothing.
- Files: `backend/services/{pipeline_metrics,metrics,system_setting}.py`, `backend/api/routes/metrics.py`, FE Metriky page.
- Depends-on: CR-V2-009 (phase stamping), CR-V2-014 (Auditor turns).
- Verification: `pytest -k metrics` green; a build's tokens roll up into 4 phase buckets (grouped on the `phase` stamp, not `metrics_role`); Metriky shows per-phase agent-vs-human ROI; per-phase rate/wage from Settings; **`grep` confirms the 11 v1 per-role settings keys are gone from `DEFAULT_SETTINGS` and 4 per-phase keys exist.**
- Requirements: METRIC-1, SET-4.

**CR-V2-030 — Nastavenia surface (dial UI + model/effort + credentials + per-phase metrics)**
- Scope: FE Nastavenia — Miera autonómie dial (4 presets, global default + per-project/per-build override, two always-outside-the-dial exceptions documented); AI model+effort selection (2 roles); Credentials config (kept); per-phase metrics rates/wages; keep Users&roles (label Manažér), Cesty a šablóny, Notifikácie (Telegram) as today.
- Files: FE Nastavenia page(s), `backend/services/system_setting.py` (wiring), `api/routes/system_settings` if needed.
- Depends-on: CR-V2-008, CR-V2-029.
- Verification: `npm run build` clean; the dial shows 4 presets + the two exceptions; model/effort set per agent; per-phase rates/wages editable.
- Requirements: SET-1, SET-2, SET-3, SET-4.

### Milestone I — Cleanup & cutover

**CR-V2-031 — Remove `cr`/`bug` flow_type (✅ OQ-1 resolved: drop)**
- Scope: drop `cr`/`bug` from `FLOW_TYPE_VALUES` + CHECK (migration 075); only `new_version` + `fast_fix` survive. (OQ-1 confirmed by Manažér 2026-06-26 — every change is a new version (full 4-phase) or a fast-fix; the BUG work-item node type is unaffected.)
- Files: `backend/db/models/pipeline.py`, migration 075.
- Depends-on: CR-V2-009; OQ-1 resolved.
- Verification: `pytest` green; inserting `flow_type='cr'` rejected; drift test updated.
- Requirements: (supports the "dropped" cr/bug line).

**CR-V2-032 — NEX Studio tag-based self-deploy redesign (cutover, deferred design)**
- Scope: co-designed with the engine at cutover (Open item #3) — redesign NEX Studio's own self-deploy to tag-based prod/dev/UAT so the dev branch runs safely alongside the live v1 tool; replaces "main = only deployable".
- Files: `.github/workflows/*`, deploy scripts, `docker-compose*`.
- Depends-on: all engine CRs (built first with deploy stubbed — OQ-5); design TBD.
- Verification: a dev-tag pushes to a dev instance without touching the live PROD tag; PROD tag deploys only on the release tag.
- Requirements: BUILD-2.

**CR-V2-033 — Comparison validation build (the cheap pre-cutover check)**
- Scope: run ONE end-to-end build with the new engine on the validation project (§5) through all 4 phases + a per-customer deploy; measure vs the v1 baseline; pass criteria gate the merge.
- **The validation deploy uses the CR-V2-026 `deploy.py` / `uat_provisioner` per-customer backend on the `v2.0.0-dev` branch instance — it does NOT require CR-V2-032's tag-based self-deploy.** CR-V2-032 (NEX Studio's own tag-based self-deploy) is NOT a dependency of running the validation build; it only gates the **final merge-to-main + cutover** that happens AFTER CR-V2-033 passes. Ordering: CR-V2-033 validates on the dev branch → on pass, CR-V2-032's tag-based self-deploy carries the merged v2 onto the live host. (Closes the test-lens gap that CR-033's dependency set omitted the deferred CR-032.)
- Files: none (operational); produces a validation record.
- Depends-on: CR-V2-001..030 (the full v2 engine + UI + deploy). (NOT CR-V2-032 — see note.)
- Verification: see §5 pass criteria. On pass → CR-V2-032 tag-based self-deploy performs the merge-to-main cutover.
- Requirements: BUILD-3.

**(BUILD-1** — "develop on a branch, main frozen" — is satisfied operationally: all CRs land on `v2.0.0-dev`; no CR delivers it as code. Tracked as the standing constraint, see §6.)

---

## 4. Build sequence & milestones

```
A. Foundations          CR-001 enums/migration(+user_agent_settings CHECK) · 002 MM-removal-BE ·
                        003 MM-removal-FE · 004 Director→Manažér · 005 archetype+auth-mode+surface scaffold
   ── SPIKE-IO ──        resolve OQ-2 / R-IO (IO-locking model) — GATES Milestone B
B. New engine core      CR-006 status schema · 007 two-agent invocation(OQ-2-gated) · 008 autonomy dial(+076) ·
                        009 apply_action 4-phase(+listener re-wire +phase stamp; auto_chain→014)
C. Phases as behaviour   CR-010 Príprava · 011 Návrh(+task plan) · 012 Programovanie self-check
D. Auditor              CR-013 upfront review · 014 Verifikácia end check(+auto_chain finalize)
E. AI Agent terminal    CR-015 PTY warm session(impl SPIKE-IO) · 016 own memory(R-DOUBLEWRITE) ·
                        017 comms/file-bus retire · 018 helper spawning + panel
F. Frontend             CR-019 sidebar+labels(+AUD-7) · 020 remove ProjectSpecsPage · 021 Vývoj board ·
                        022 AI Agent tab · 023 task-plan UX · 024 create forms
G. Deploy & Customers   CR-025 Zákazníci · 026 deploy backend · 027 UAT/PROD tabs
H. Fast-fix/metrics/set  CR-028 fast-fix(OQ-3-blocked) · 029 metrics per-phase(retire 11 keys) · 030 Nastavenia
I. Cleanup & cutover     CR-031 drop cr/bug(OQ-1) · 032 tag-based self-deploy · 033 comparison build → MERGE
```

Rationale for the order: the **enum/data-model + renames + multi-module removal (A)** unblock everything (every later layer reads the new STAGE/ACTOR contract and the archetype model). **SPIKE-IO runs between A and B** to resolve OQ-2 (warm PTY vs headless) before any 2-role invocation code commits to one substrate (R-IO). The **engine core (B)** must exist before phases can be implemented as agent behaviour. **C → D** because the Auditor's upfront review attaches to the end of Návrh and its end-check to after Programovanie. **E** (the live PTY AI Agent) depends on the re-keyed 2-role invocation from B and implements the SPIKE-IO model. **F** (frontend) renders the new board/contract, so it follows the engine + terminal. **G** depends on the verified-version boundary from Verifikácia (D) and the archetype/customer model (A/E). **H** layers fast-fix/metrics/settings on the finished engine + deploy (CR-V2-028 stays blocked until OQ-3). **I** does the deferred-design self-deploy + the single comparison build (which uses the CR-026 deploy backend, not CR-032), then cutover via CR-032.

---

## 5. Cheap validation build

**One comparison build before cutover (BUILD-3).**

- **Project:** a small, low-cost greenfield ICC app of **Štandardný** archetype with **token-launch auth** — e.g. a fresh fast-fix-class utility or the next small internal tool (NOT nex-inbox/ledger PROD; pick a throwaway-grade target so a failed validation costs nothing). Final target chosen by the Manažér; the spec only requires it be small enough to build end-to-end in one sitting.
- **Run:** full v2 pipeline end-to-end — Príprava (interactive spec dialogue → Špecifikácia approved) → Návrh (one design doc + task plan, Auditor upfront review) → Programovanie (AI Agent self-checking) → Verifikácia (Auditor end check) → Hotovo → one per-customer deploy (register ICC s.r.o. in Zákazníci → Nasadiť to UAT → Akceptovať → PROD bump to v1.0.0).
- **Measure vs the v1 5-role baseline:** (1) **agent-vs-human ratio per phase** (target: better than v1's ~1.1× human; Programmer-phase no longer 0.6×); (2) **rework count** (target: far below v1's 100+); (3) **Director/Manažér touches** (target: ~5 — the two always-stops + dial-governed stops — vs the v1 2d4h-wait gate burden); (4) **correctness** — the produced app does what the Zadanie promised, confirmed by the Auditor's Verifikácia AND a Manažér UAT acceptance.
- **Pass criteria (all must hold):** the app is functionally correct (Verifikácia PASS + Manažér Akceptovať); the §4 hard-security rules verified by the Auditor in code + at runtime; no engine safeguard regressed (single-flight, lost-work audit, resume-safety, no-silent-done-without-UAT); the per-phase metrics show the agent-vs-human ratio is not worse than v1. **Only then** does v2.0.0 merge to `main` and assume the live host under the new tag-based self-deploy (CR-V2-032), retiring v1.

---

## 6. Risks & migrations

### Data migrations (ordered)
| # | Migration | Risk / strategy |
|---|---|---|
| 069 | STAGE/ACTOR/role CHECK rewrite + **`ck_user_agent_settings_role`** | Existing v1 `pipeline_state`/`pipeline_message`/`orchestrator_sessions` rows carry retired stage/actor/role values → new CHECK rejects them. **The `user_agent_settings.agent_role` CHECK (migration 061) is a SECOND surviving 5-role CHECK** — 069 MUST rewrite it to `{ai_agent, auditor}` and delete/re-seed its rows, or CR-V2-007's 2-role collapse is DB-rejected. Strategy (OQ-6): branch DB starts fresh for v2 builds; legacy rows wiped or migrated to nearest equivalent. **Main frozen at v1.0.0**, so live v1 data is untouched until cutover. |
| 070 | drop project_modules, module_dependencies, drop epics.module_id | `module_id` already NULL for single-module projects → safe drop, no data loss. Must land WITH the BE import sweep (CR-002) or imports break. |
| 071 | `awaiting_director` → `awaiting_manazer` STATUS (value only, no column rename) | Data-migrate existing status VALUES (the CHECK), NOT the `awaiting_director_since`/`director_wait_total` column names (no rename DDL). The 3 ORM listeners (director-wait timer, single-flight clear, block_reason clear) key on the value — **their re-wire is owned by CR-V2-009**, landing with the new-status engine logic, or they silently stop firing (R-LISTENERS). |
| 072 | category→type + add auth_mode | Data-migrate 'singlemodule'→'standard'; set a default auth_mode for legacy rows; lands WITH the create-project schema change so the form is never half-migrated. |
| 073 | customers table | net-new, no legacy data. |
| 074 | deploy/acceptance audit-log table | net-new. |
| 075 | drop cr/bug flow_type | pending OQ-1; existing rows with `cr`/`bug` must be migrated/removed first. |
| 076 | `projects.miera_autonomie` + `pipeline_state.miera_autonomie` (both nullable) | net-new nullable columns for the per-project / per-build dial override (AUTON-6); NULL = inherit the next layer up. Safe add, no backfill needed. |
| ~~077~~ | per-project memory table — **NOT NEEDED** | OQ-4 resolved (Manažér 2026-06-26) = file-based per-project `MEMORY.md`; no migration. (CR-V2-016.) |

### Kept-infra breakage risks
- **R-BLAST:** `orchestrator.py` is 6874 LOC and `apply_action`/`run_dispatch` are the whole control flow; a near-total rewrite risks losing hard-won safeguards (single-flight guard, lost-work audit, resume-safety, no-silent-done-without-UAT) each fixed via a specific incident. Each must be **deliberately carried forward** (asserted in CR verification), not lost in the rewrite.
- **R-IO (biggest unknown):** the headless-write vs PTY-watch duality — in v2 the engine drives the AI Agent PTY AND the Manažér types into it. Concurrent writers to one `claude` session can interleave/corrupt input without a single coherent IO/locking model. Resolve in CR-V2-015 before building dependents.
- **R-LISTENERS:** the 3 sync, lock-free ORM status listeners depend on `STATUS_VALUES` staying stable and on sync iteration; any rename (CR-004) or async refactor must re-wire / preserve lock-holding or risk "dict changed size during iteration".
- **R-METRICS-DEP:** `metrics.py` cannot be rebuilt until the engine defines per-phase usage capture (CR-009 phase stamping) — sequence metrics (CR-029) after the engine. Historical v1 per-role data is **not 1:1 comparable** to per-phase; do not back-attribute across the v1→v2 boundary.
- **R-FF (RESOLVED, Manažér 2026-06-26):** v1 fast-fix ended in `_fast_fix_auto_deploy`; D6 makes deploy always manual + per-customer — a hard contradiction, now resolved: fast-fix stops at "verified", the in-lane auto-deploy is retired, and the patch goes through the normal manual per-customer Nasadiť (consistent with D6 and CR-V2-014's "Hotovo ≠ deployed"). CR-V2-028 is unblocked.
- **R-SWEEP:** role identity is hard-coded in ≥5 BE places + FE pickers + `UserAgentSettings` rows; debug-attach was missed twice historically — CR-V2-007 requires an **exhaustive** FE+BE sweep, not sampling, with type-check as the gate.
- **R-AUTOCHAIN:** `auto_chain_limit` depends on `STAGE_ORDER` length + Gate-E self-loop slack; collapsing to 4 phases (no gate_e self-loop) changes the bound — recompute in CR-V2-009 or the backstop mis-trips.
- **R-DOUBLEWRITE:** `live_documents` STATUS.md/HISTORY.md folds into AI-Agent memory (CR-016); decide retire-into-memory vs keep-as-rendered-view to avoid divergence.

### Rollback
- **Main is frozen at v1.0.0 throughout** (BUILD-1) — the live tool is unaffected by the entire v2 build. Rollback = abandon/reset `v2.0.0-dev`; no PROD impact. The v1 pipeline is retired ONLY after the comparison build (CR-V2-033) passes and v2 merges under the new tag-based self-deploy. If the comparison build fails, v2 does not merge and v1 keeps running.

---

## 7. Open questions for the Manažér

1. **OQ-1 (cr/bug flows) — ✅ RESOLVED (Manažér, 2026-06-26): DROP `cr`/`bug` flow_types.** Every change has two clean entry modes — a new version (full 4-phase) or a fast-fix (short path); a third full-pipeline `cr`/`bug` mode is redundant v1 baggage. Only `new_version` + `fast_fix` survive in `FLOW_TYPE_VALUES`. NB: this drops the build-entry *flow_type* only — the **BUG work-item node** (EPIC→FEAT/BUG→TASK) is unaffected; bugs are still tracked inside a version's plan. CR-V2-031 confirmed.
2. **OQ-2 (warm-context execution model) — ✅ RESOLVED (Manažér, 2026-06-26): Model B (headless `--resume` + relay).** The AI Agent is NOT a true concurrent-writer PTY. The ENGINE is the sole serialized writer and drives the build via `claude -p --resume <session>` turns (the proven `invoke_claude`); warm context is held by the persistent session (`--resume` reloads the full conversation each turn); the Manažér's AI Agent tab shows a live WS-rendered view of the session + an input box whose messages the engine RELAYS as the next turn into the same session. One session, one writer, warm context throughout, Manažér talks to it live (messages land at turn boundaries — how Claude Code processes input anyway). `invoke_claude` survives for the AI Agent. R-IO largely dissolves; SPIKE-IO shrinks to a confirmation/wiring step (no risky 2-writer locking model). The OQ-2 gates on CR-V2-007/010/012/015/016/022 are cleared.
3. **OQ-3 (fast-fix auto-deploy vs D6) — ✅ RESOLVED (Manažér, 2026-06-26): stop at "verified", deploy via the normal manual per-customer path.** Fast-fix is autonomous only through the build to "verified" (skips heavy Návrh + per-task Auditor; light Auditor checks fix-works + no-regression); the patch then flows through the ordinary manual per-customer Nasadiť → (UAT) → Akceptovať → PROD like every other version. The legacy `_fast_fix_auto_deploy` is retired. Consistent with D6 (deploy always manual + per-customer + outside the dial), the never-bypassed UAT acceptance gate, and CR-V2-014's "Hotovo ≠ deployed". CR-V2-028 is UNBLOCKED.
4. **OQ-4 (AI-Agent memory store) — ✅ RESOLVED (Manažér, 2026-06-26): per-project `MEMORY.md`.** File-based memory (`MEMORY.md` + topic files) the charter rules tell the AI Agent to read at session start and write to (Dedo-style) — zero schema, no migration, human-readable, git-diffable, travels with the project. Shared cross-project knowledge stays in KB/RAG; per-project memory is local file context. **Migration 077 is NOT needed.** Sets R-DOUBLEWRITE direction: `MEMORY.md` = single source of truth; `live_documents` STATUS.md/HISTORY.md become rendered views or are dropped. (Drives CR-V2-016.)
5. **OQ-5 (deploy boundary / build order) — ✅ RESOLVED (Manažér, 2026-06-26): build Verifikácia first with deploy stubbed; deploy backend POINTS INTO the existing `credentials.py` store.** Verifikácia (CR-V2-014) is built with deploy stubbed; the per-customer deploy backend (CR-V2-026) does NOT own secrets directly — it points into the existing `credentials.py` store (§4 governance via the credentials API, no duplicate secret ownership), keyed per customer. CR-V2-032 self-deploy stays deferred to cutover.
6. **OQ-6 (cutover data strategy) — ✅ RESOLVED (Manažér, 2026-06-26): fresh branch DB during dev; historical v1 rows preserved read-only at cutover.** During development the `v2.0.0-dev` branch DB starts fresh (legacy stage/actor/role rows wiped — migration 069); main is frozen at v1.0.0 so live v1 data is untouched. At cutover, completed historical v1 `pipeline_state`/`message` rows are preserved **read-only** (NOT retro-migrated to the 4-phase enum — they are finished history); only forward builds use the v2 enums.
7. **OQ-7 (route naming) — ✅ RESOLVED (Manažér, 2026-06-26): rename routes to the new vocabulary + redirects; Zákazníci/UAT/PROD project-scoped reading the pin.** `/cockpit`→`/vyvoj`, `/coordinator`→`/ai-agent` (cleaner long-term, matches the new vocabulary), with redirects from the old paths and PersistentTerminalsLayer re-keyed in CR-V2-022. Zákazníci/UAT/PROD read the active-project pin (consistent with other project-anchored features), not hard-nested URLs.
8. **OQ-8 (task-plan persistence scope) — ✅ RESOLVED (Manažér, 2026-06-26): localStorage (per-browser).** Expand/collapse tree state is a UI convenience; per-browser localStorage is sufficient and zero-backend. A backend per-user pref is over-engineering for tree state. (Drives CR-V2-023.)
9. **OQ-9 (Auditor effort + dial coupling) — ✅ RESOLVED (Manažér, 2026-06-26): YES — Auditor depth/effort scales with the dial.** Higher autonomy → deeper Auditor (higher `--effort` + more adversarial spot-checks); lower autonomy (Manažér checks often) → lighter Auditor. The Auditor is the safety net that compensates for fewer human stops — verification intensity is inversely proportional to human oversight (core safety principle of the autonomy model). Couples the effort resolver (CR-V2-007) to the dial (CR-V2-008); depth applied in CR-V2-013/014.
10. **OQ-10 (status-block survival) — ✅ RESOLVED (Manažér, 2026-06-26): KEEP the machine-readable `<<<PIPELINE_STATUS>>>` block.** Keep the deterministic status-block contract (parse → `blocked` on malformed, never guess), adapted to 4 phases (CR-V2-006); do NOT infer phase/await from live PTY text (fragile, non-deterministic). The proven dual-transport (status block / structured output) survives.
11. **OQ-11 (Web-archetype commerce add-on) — DEFERRED:** The Web archetype's optional eshop/commerce capability (cart / checkout / payments + **bidirectional IS-integration**, the latter being the real complexity per design §4.2) is **deferred out of v2.0.0**. v0.x Web = managed/monitored site WITHOUT commerce; CR-V2-005 scaffolds the two surfaces (admin-FE + public-site) and leaves a documented extension seam but emits NO commerce code. When the first real eshop project arrives, the commerce add-on gets its own design round (like Mobil, design §8 Open #1). **✅ Deferral CONFIRMED (Manažér, 2026-06-26)** — v2.0.0 ships Štandardný + Web (managed site, no commerce); commerce + Mobil are dedicated future rounds.

---

**✅ All 11 open questions RESOLVED (Manažér, 2026-06-26) — the build plan is fully unblocked.**

---

## Requirement → CR coverage matrix

| Req | CR | Req | CR | Req | CR |
|---|---|---|---|---|---|
| ARCH-1 | 001,007,009 | AUTON-1 | 008 | PREP-1 | 010,024 |
| ARCH-2 | 009,011 | AUTON-2 | 008,009 | PREP-2 | 010 |
| ARCH-3 | 015,022 | AUTON-3 | 008,009,010 | PREP-3 | 010 |
| ARCH-4 | 018 | AUTON-4 | 026 | PREP-4 | 010 |
| ARCH-5 | 006,009,012 | AUTON-5 | 007,008,013 | NAVRH-1 | 011 |
| ARCH-6 | 007 | AUTON-6 | 008 | NAVRH-2 | 011,023 |
| PIPE-1 | 001,009,021 | AUD-1 | 012,013,014 | NAVRH-3 | 011 |
| PIPE-2 | 009 | AUD-2 | 009,014 | NAVRH-4 | 011,013 |
| PIPE-3 | 006,021 | AUD-3 | 009,014 | PROG-1 | 009,012 |
| VERIF-1 | 014 | AUD-4 | 009 | PROG-2 | 023 |
| VERIF-2 | 014 | AUD-5 | 013 | UI-1..6 | 019 |
| VERIF-3 | 014 | AUD-6 | 014 | UI-7 | 004 |
| FASTFIX-1 | 028 | AUD-7 | 019,021 | UI-8 | 015,018,022 |
| FASTFIX-2 | 028 | KB-1 | 016 | UI-9 | 015,022 |
| DEPLOY-1 | 025 | KB-2 | 016 | UI-10..16 | 021 |
| DEPLOY-2 | 025 | KB-3 | 016 | UI-17 | 023 |
| DEPLOY-3 | 025 | RULES-1 | 007 | UI-18 | 023 |
| DEPLOY-4 | 027 | RULES-2 | 004,007 | SET-1 | 008,030 |
| DEPLOY-5 | 027 | RULES-3 | 007,010,016 | SET-2 | 030 |
| DEPLOY-6 | 026 | RULES-4 | 007 | SET-3 | 030 |
| DEPLOY-7 | 027 | COMMS-1 | 017 | SET-4 | 029,030 |
| DEPLOY-8 | 026,027 | COMMS-2 | 015,017,022 | METRIC-1 | 029 |
| DEPLOY-9 | 024,026 | COMMS-3 | 017 | CREATE-1 | 005 |
| DEPLOY-10 | 026 | COMMS-4 | 004,017 | CREATE-2 | 005 |
| UI-2 (rm) | 019,020 | COMMS-5 | 017 | CREATE-3 | 005,024 |
| VERSION-1 | 024 | VERSION-2 | 024 | CREATE-4 | 005 |
| VERSION-3 | 001,010,024 | BUILD-1 | (§6 constraint) | BUILD-2 | 032 |
| BUILD-3 | 033 | | | | |

All **92** target requirement IDs (ARCH 6 + AUTON 6 + PIPE 3 + PREP 4 + NAVRH 4 + PROG 2 + VERIF 3 + AUD 7 + FASTFIX 2 + DEPLOY 10 + UI 18 + SET 4 + METRIC 1 + CREATE 4 + VERSION 3 + RULES 4 + KB 3 + COMMS 5 + BUILD 3 = 92) are placed in at least one CR. **AUD-7** is now an explicit deliverable+verification of CR-V2-019 (no longer matrix-only). **BUILD-1** ("develop on a branch, main frozen") is a standing operational constraint satisfied by landing every CR on `v2.0.0-dev` (no code deliverable) — tracked in §6, not assigned a CR. The deferred-out-of-scope items (Mobil archetype, Web commerce add-on) are recorded under §7 Open #1/#11 + CR-V2-005, not silent omissions.

---

## Review applied

Adversarial completeness + test review applied 2026-06-26. Changes:

**Blockers**
- **2nd surviving role CHECK (`ck_user_agent_settings_role`, migration 061 / `foundation.py:90`):** CR-V2-001 + migration 069 now also rewrite the `user_agent_settings.agent_role` CHECK to `{ai_agent, auditor}` and delete/re-seed rows (was DB-rejecting CR-V2-007's 2-role collapse). Added the DB-value (`ai_agent`, underscore) vs charter-path (`ai-agent`, hyphen) spelling rule across all role-keyed CRs.
- **Verifikácia/fast-fix vs D6 deploy contradiction:** CR-V2-014 now states release smoke runs against INTERNAL FIXTURES, never a customer instance ("Hotovo ≠ deployed"). CR-V2-028 marked **⚠ OQ-3-BLOCKED** (like CR-031) with a plan default (stop at verified, retire `_fast_fix_auto_deploy`, normal manual per-customer Nasadiť).

**Majors**
- **OQ-2 (warm PTY vs headless) promoted to a pre-Milestone-B blocker:** added **SPIKE-IO** gating spike (IO/locking model carved out of CR-V2-015) that resolves OQ-2 and feeds/unblocks CR-V2-007; CR-V2-007 marked OQ-2-gated; §4 sequence + OQ-2 updated.
- **Dependency-ordering defects:** the 3 ORM status-listener re-wire moved into CR-V2-009 (out of CR-V2-004); the auto_chain bound recompute split — provisional in CR-V2-009, finalized with a named `AUDITOR_LOOP_MAX` in CR-V2-014; a per-turn `phase` payload stamp added to CR-V2-009 (consumed by CR-V2-029); CR-V2-029 named the OWNER of retiring the 11 v1 per-role metrics keys.
- **CREATE-1/CREATE-2 (archetype) completeness:** CR-V2-005 expanded to scaffold the per-archetype SURFACE COMPOSITION (Standard = BE+app-FE; Web = BE+admin-FE+public-site) with verification of the second surface; the Web eshop/commerce add-on (cart/checkout/payments + bidirectional IS-integration) explicitly DEFERRED (new §7 Open #11) — v0.x Web = managed site without commerce, documented extension seam, no commerce code; Mobil deferral acknowledged in CR-V2-005.

**Minors**
- Requirement count corrected 70 → **92** (with the per-category breakdown) and the closing sentence re-stated.
- AUTON-6 override storage defined at the data-model level: per-project `projects.miera_autonomie` + per-build `pipeline_state.miera_autonomie` nullable columns + migration **076** in CR-V2-008.
- AUD-7 added to CR-V2-019's Requirements with an explicit "no Auditor nav item renders" verification.
- CR-V2-009 flagged OQ-1-contingent for `cr`/`bug` (4-phase path serves them unchanged or they're removed — no separate variant needed).
- CR-V2-016 memory store default = per-project `MEMORY.md` (no migration); DB store ⇒ conditional migration **077**; CR-V2-016 now OWNS R-DOUBLEWRITE (single source of truth for STATUS/HISTORY content).
- CR-V2-004 clarified: only the STATUS *value* rename is DDL (no column rename, `status` stays `String(20)`).
- CR-V2-033's validation deploy uses the CR-V2-026 deploy backend on the dev branch; CR-V2-032 (tag-based self-deploy) gates only the post-pass merge-to-main, not the validation run.
- Migration table (§6) extended with 069 (user_agent_settings), 071 (value-only, listener re-wire owned by 009), 076 (dial overrides), 077 (conditional memory); R-FF strengthened.

CR count unchanged at **33 numbered CRs (CR-V2-001 … CR-V2-033)** plus **1 pre-Milestone-B gating spike (SPIKE-IO)**.
