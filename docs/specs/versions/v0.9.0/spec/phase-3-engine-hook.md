# v0.9.0 Phase 3 — Engine first-release auto-provision + teardown

> Wires the Phase-2 provisioner into the release flow so a full-flow release **autonomously provisions + deploys
> a UAT** when the project has none yet. Phase 1 (Traefik infra) + Phase 2 (`uat_provisioner.provision_uat`,
> `derive_uat_slug`, `project_service.set_uat_slug`, generalized template) are DONE + deployed.

> **Fast-fix UNTOUCHED:** all changes are in the full-flow path (`flow_type == "new_version"`).
> `_fast_fix_auto_deploy` + the fast_fix release block are NOT modified. `claude_agent.py` untouched.

---

## CR-1 — first-release auto-provision in `_release_auto_uat_deploy`
`_release_auto_uat_deploy` (orchestrator.py:~2731) today: resolves `project.uat_slug`; if NULL or
`/opt/uat/<slug>/docker-compose.yml` missing → **honest-skip** (`awaiting_director`, "Žiadny UAT
nakonfigurovaný"). Replace the skip with autonomous provisioning:
1. **Derive + persist uat_slug** if NULL: `slug = uat_provisioner.derive_uat_slug(project)` →
   `project_service.set_uat_slug(db, project, slug)` (persist; idempotent — keeps a manual non-null).
2. **Provision if compose missing:** if `/opt/uat/<uat_slug>/docker-compose.yml` does not exist →
   `result = await asyncio.to_thread(uat_provisioner.provision_uat, project_slug, uat_slug, version=<version_label>)`.
   On provision failure (raises / error result) → `state.status="blocked"`, `next_action` = the error; record a
   `system→director` notification; return. (Never silently skip.)
3. **Deploy** (provision succeeded, or compose already existed): `ok, detail = await _run_uat_deploy(project_slug, uat_slug)`
   (existing — `docker compose up -d --build --force-recreate`). Traefik auto-routes via the labels the
   provisioner baked in (no nginx/host change).
4. **Settle:** success → `state.status="awaiting_director"`, `next_action="Nasadené na UAT — over a akceptuj"`,
   and the notification includes the **`https://uat-<uat_slug>.isnex.eu`** URL so the Director can open it.
   Failure → `blocked` with the deploy error.
5. Record the outcome `system→director` notification (payload `{"uat_deploy": {...}}` as today, so the v0.8.1
   honest `uat_accept` message keys on a real deploy).
- Keep the existing redeploy path intact: if the compose already exists → skip provisioning, just `_run_uat_deploy`.
- This runs ONLY in the full-flow (`new_version`) release settle (where `_release_auto_uat_deploy` is already
  called, gated). Fast-fix's own `_fast_fix_auto_deploy` is separate + unchanged.

## CR-2 — teardown on project delete (orphan prevention)
When a **project is deleted**, tear down its UAT (it is orphaned): if `project.uat_slug` set and
`/opt/uat/<uat_slug>/docker-compose.yml` exists → `docker compose -f … down -v` (engine subprocess, mirror
`_run_uat_deploy`'s shellout; never raises) + reclaim the allocated port (if any in `.uat-ports.json`). Traefik
auto-de-routes when the containers are gone (no host/nginx change). Wire into the existing project-delete path
(find it — `projects.py` delete route / `project_service`). **Version supersede is NOT a teardown** — a new
version of the same project redeploys the SAME `uat_slug` (handled by CR-1's redeploy path).

## Scope / safety
- Full-flow (`new_version`) only for CR-1; CR-2 is the project-delete path. Fast-fix, `_fast_fix_auto_deploy`,
  `_run_uat_deploy` signature, `claude_agent.py` — untouched.
- nex-asistent end-to-end validation has a SEPARATE prerequisite (its source compose lacks a `frontend` service →
  no Traefik default route). NOT a Phase-3 code issue; resolved separately (observer: project/build gap).

## Self-verify (Implementer, before DONE)
1. `poetry run pytest` (FULL) — baseline-verify the env-only `test_default_claude_config_dir`.
2. `ruff format --check . && ruff check .`.
3. New tests: full-flow release, uat_slug NULL + no compose → derive+set uat_slug + provision_uat called +
   _run_uat_deploy called → awaiting_director with the uat-<slug>.isnex.eu URL; provision failure → blocked;
   existing compose → skip provision, redeploy only; project-delete → teardown invoked when uat_slug+compose
   present, no-op otherwise. **Fast-fix release tests still green** (its path unchanged).
4. Confirm `_fast_fix_auto_deploy` + the fast_fix release block are byte-identical (grep/diff).

Report exact outputs. STOP + report any gap (§2.4). Do NOT commit — Dedo commits + verifies.
