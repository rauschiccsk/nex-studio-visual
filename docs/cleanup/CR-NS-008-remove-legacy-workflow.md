# CR-NS-008 — Remove legacy in-app DESIGN/EXECUTION pipeline (atomic)

**Author:** Dedo · **Date:** 2026-06-02 (rev.2) · **Executor:** AG Implementer (single atomic CR)
**Approved by:** Director — atomic CR; report_configs incl. seeding; dead-service deletion.

**⚠ rev.2 SCOPE CHANGE — MULTI-MODULE IS PRESERVED.** Multi-module support is CORE to NEX Studio
(its original raison d'être; NEX Automat will be multi-module). We remove ONLY the dead pre-agent
**per-module design/execution pipeline**, NOT the multi-module structure.

Removes: architect chat → design.md → in-app spec/UI authoring → task-plan generation →
delegate-to-CC → execution-logs/auto-fix → guardian review → project cost report. Replaced by agents.

## KEEP — CORE (do NOT touch / do NOT drop)
- **Multi-module structure:** `ProjectModule`, `ModuleDependency` (models/schemas/services/routes `/api/v1/project-modules`, `/api/v1/module-dependencies`), **`Epic.module_id`** (FK epics→project_modules — KEEP), Module-Map FE pages (`MMOverviewPage`/`MMModulePage`/`MMDepMapPage`), `services/api/projectModules.ts` + module types, ProjectDetailPage MM link, `live_documents.py` Modules section + module-event history.
- Agent infra: Versions/Epics/Feats/Tasks/Bugs, agent terminals, Dialogue, Projects, KB, Project Specs, Credentials, Settings, Dashboard, auth/users/sessions/project_members/project_specs/system_settings/knowledge/rag/uploads.

## Scope guard (§ Implementer no autonomy)
Delete/edit ONLY what is listed. STOP+report on any coupling/gap not covered. INCLUDES migration 048 + drift-test update (§9.6).

## 0. Clean restart
The prior run deleted MM backend + stripped Epic.module_id — that is now WRONG (MM is kept). Working tree is uncommitted, so: **`git checkout .`** to restore all tracked files to HEAD, and delete any partial `migrations/versions/048_*.py` you created. Then execute this rev.2 manifest fresh.

---

## A. Backend — FULL-DELETE files (dead pipeline only; MM excluded)

**Routes (14):** `backend/api/routes/` → `architect.py`, `architect_sessions.py`, `architect_messages.py`, `design_documents.py`, `professional_specifications.py`, `raw_specifications.py`, `ui_designs.py`, `delegations.py`, `execution_logs.py`, `auto_fix_attempts.py`, `bug_fix_tasks.py`, `guardian_reviews.py`, `guardian_precedents.py`, `report_configs.py`
  *(KEEP: `project_modules.py`, `module_dependencies.py`.)*

**Schemas:** architect.py, architect_session.py, architect_message.py, design_document.py, professional_specification.py, professional_spec_chat_message.py, raw_specification.py, ui_design.py, ui_design_chat_message.py, delegation.py, execution_log.py, auto_fix_attempt.py, bug_fix_task.py, guardian.py, report_config.py
  *(KEEP: `module_dependency.py`, `project_module.py`.)*

**Services:** architect_session.py, architect_message.py, architect_context.py, design_document.py, professional_specification.py, professional_spec_chat_message.py, raw_specification.py, ui_design.py, ui_design_chat_message.py, delegation.py, execution_log.py, auto_fix_attempt.py, bug_fix_task.py, guardian_review.py, guardian_precedent.py, report_config.py, **feat_executor.py**, **task_plan_generator.py**
  *(KEEP: `module_dependency.py`, `project_module.py`.)*

**Models (FULL delete):** `architect.py`, `delegations.py`, `guardian.py`, `reports.py`, **`specifications.py`** (all 6 classes removed).
  *(KEEP `projects.py` UNCHANGED — ProjectModule + ModuleDependency stay.)*

## B. Backend — PARTIAL model edit
- `backend/db/models/bugs.py` → remove `BugFixTask`, **keep `Bug`**.
  *(projects.py is NO LONGER edited — rev.1's ProjectModule/ModuleDependency removal is reverted.)*

## C. Aggregator edits (remove refs for the 14 features ONLY)
`backend/main.py`, `backend/schemas/__init__.py`, `backend/db/models/__init__.py`, `backend/db/base.py` — remove imports/`__all__`/`include_router`/`ALL_MODELS` entries for the removed features. **KEEP** all `ProjectModule`, `ModuleDependency`, `project_modules`, `module_dependencies` entries.

## D. KEEP-file de-tangle (surgical)
- `api/routes/tasks.py` — remove `Delegation, ExecutionLog` import + `_build_task_completion_data` join + call sites; return neutral data (ExecutionLog no longer written by live code).
- `services/live_documents.py` — remove **ONLY** the `ExecutionLog` import + `_task_commit_hashes` (~L36, L389-404); call site → `{}`. **KEEP** the `ProjectModule` import + Modules section + module-event code (MM is kept).
- `services/user.py` — remove the created_by restrict-dependency entries for `ArchitectSession` + specifications models (architect/raw/professional/design) and their imports. KEEP any `ProjectModule` entry if present.
- `api/routes/feats.py` — remove `feat_executor` import + `POST /{feat_id}/execute` (`execute_feat`).
- `api/routes/versions.py` — remove `task_plan_generator` import + `generate_task_plan` + `append_epic` endpoints + `GenerateTaskPlanRequest`. KEEP get_task_plan/reset_tasks/reset_plan/release_version + version CRUD.
- **`Epic.module_id` — KEEP everywhere** (db/models/tasks.py, schemas/epic.py, services/epic.py, routes/epics.py). project_modules table stays → no FK problem.

## E. report_configs seeding — confirmed NO-OP
0 live `ReportConfig(` sites. Just remove the assertion in `test_workflow_create_project.py::..._default_report_config` (see F).

## F. Tests
**DELETE (dedicated to dead pipeline):** `backend/tests/integration/` → test_workflow_view_project_report.py, test_workflow_start_architect_session.py, test_workflow_generate_design_md.py, test_workflow_approve_design_md.py, test_workflow_extend_design_md.py, test_workflow_handle_delegation_failure.py, test_workflow_guardian_review.py, test_workflow_create_raw_specification.py, test_workflow_approve_professional_spec.py, test_workflow_generate_professional_spec.py. Plus FE `__tests__/services/test_architect_api.test.ts`.

**STRIP (keep — covers kept feature):**
- test_workflow_create_project.py — remove ReportConfig import + the default_report_config assertion/method.
- test_workflow_generate_epic_feat_task_plan.py — strip Architect/DesignDocument refs; if the test is wholly the removed `generate-task-plan` flow → DELETE; if it covers manual epic/feat/task → keep that. Classify; STOP+report if ambiguous.
- test_workflow_set_module_status.py — **KEEP** (module status uses project_modules = KEPT); strip only the `ArchitectSession` ref.
- test_workflow_add_module.py — **KEEP** (project_modules + module_dependencies = KEPT); strip nothing unless it touches a removed feature.
- test_workflow_delegate_feat_to_cc.py — delegation/guardian/feat-execute removed → DELETE; classify+report.
- test_workflow_accept_bug_for_fix.py / resolve_bug.py / delegate_bug_fix.py — strip `BugFixTask`/`Delegation`/`ExecutionLog`; keep `Bug`. If purely bug-fix-task/delegation → DELETE.
- test_rbac_matrix.py — already on credentials_router (CR-NS-007); verify green.
- FE `__tests__/components/test_Sidebar.test.tsx` + `__tests__/navigation/test_routes.test.tsx` — strip step-pipeline/Workflow assertions; **keep** MM-route assertions.
- **STOP+report** any test you cannot cleanly classify.

## G. Migration 048 + drift test
- `migrations/versions/048_drop_dead_features.py` — `revision="048"`, `down_revision="047"`.
- Drop **15 tables** in FK-safe order (children→parents) — **NOT project_modules / module_dependencies**:
  `architect_messages`, `bug_fix_tasks`, `guardian_precedents`, `raw_specifications`, `report_configs`, `ui_designs`, `architect_sessions`, `delegations`, `design_documents`, `professional_specifications`, `ui_design_chat_messages`, `auto_fix_attempts`, `execution_logs`, `guardian_reviews`, `professional_spec_chat_messages`.
  *(dropped architect_sessions/design_documents had module_id FK → project_modules; those FKs vanish with the tables, project_modules stays. NO `drop_column` on epics — module_id stays.)*
- `downgrade()` recreate all 15 (mirror their create migrations 002/010/011/012/017/018/019/022/027/035/036).
- `backend/tests/test_alembic_migrations.py` — remove the 15 dead names from `expected_tables`; **KEEP** `project_modules`, `module_dependencies`, `bugs`, `epics`, `feats`, `projects`, `tasks`, `user_sessions`, `users`. Update docstring count. `test_alembic_upgrade_head_on_clean_database` → zero drift, no ignore-filter.

## H. Frontend
**Delete step pages** (`pages/step/`): SpecPage, ProfSpecPage, SummaryPage, ArchitecturePage, AuditPage, TaskPlanPage, ImplementaciaPage, UIDesignPage. **Component:** `components/pipeline/SolutionTabs.tsx`.
  *(KEEP MM pages: MMOverviewPage, MMModulePage, MMDepMapPage.)*
**App.tsx** — remove the 8 step routes + imports. **KEEP** the 3 `/mm*` routes.
**Sidebar.tsx** — remove `pipelineSteps` array + "Workflow" NavItem + its render block + step-only icons. **REMOVE the "Specification" NavItem too** (it links to the deleted `SpecPage` → broken link; the kept "Project Specs" NavItem already covers spec viewing — link-domain-integrity). **KEEP** agent-terminal NavItems + MM access.
**API clients (delete):** architect.ts, designDocuments.ts, professionalSpecifications.ts, rawSpecifications.ts, uiDesigns.ts, taskPlan.ts. **KEEP `projectModules.ts`.**
**Types (delete):** architect.ts, architectSession.ts, architectMessage.ts, taskPlan.ts (+ design/spec/ui type files imported only by removed code) + their index.ts re-exports. **KEEP** module/projectModule types.
**De-tangle KEEP pages:**
- VersionDetailPage.tsx — remove step-stepper + pipeline API imports + onOpenProfSpec/onOpenUIDesign; KEEP version status + epic_count/bug_count.
- ProjectDetailPage.tsx — **KEEP** the MM link + Multi-Module badge (MM is core).
**FE tests:** see F.

## I. Verify → commit → push → CI
- **Systematic FK scan (before migration 048):** grep KEEP models for `ForeignKey("<dropped_table>"` against the 15 dropped tables; report any KEPT→dropped FK before dropping (Epic.module_id→project_modules is NOT one — project_modules kept).
- BE: `ruff check` + `ruff format --check` + `poetry run pytest backend/ tests/` (FULL) + drift tests green (zero drift).
- FE: `npm run build` + `npm run lint` + `tsc --noEmit` (SpecPage.tsx eslint error disappears with the file; confirm no NEW errors).
- Grep clean: no live refs to removed features (benign docstrings OK).
- Single commit `chore(cleanup): CR-NS-008 — remove legacy in-app design/execution pipeline (multi-module preserved)` → push → CI → DONE report (§9.5 evidence).
- **STOP+report** any coupling/gap not covered.
