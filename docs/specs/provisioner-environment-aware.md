# Spec: Environment-aware provisioner (PROD + UAT, both via Traefik)

**Author:** Dedo · **Date:** 2026-07-07 · **For:** Implementer
**Phase-2 part-3** of the /opt directory cleanup ([[project_icc_opt_directory_canonical]]).
**Approach B — APPROVED by Director:** PROD routes via **Traefik, exactly like UAT** (NOT host-nginx-from-container). This keeps the refactor small and safe.

---

## 1. Goal

Make `backend/services/{uat_provisioner,deploy,orchestrator}.py` **environment-aware** so a NEX Studio deploy produces a clean, systematic instance automatically for both `prod` and `uat`, instead of the manual one-offs done for andros on 2026-07-07. Today everything is hardcoded to `uat` (root `/opt/uat`, `uat-` name prefix, `uat-<slug>` Traefik host).

## 2. Target convention (prod vs uat)

| Aspect | UAT (current, keep) | PROD (new branch) |
|---|---|---|
| Root dir | `/opt/uat/<instance-slug>/` | `/opt/customers/<customer-slug>/<full-project-slug>/` |
| Example dir | `/opt/uat/andros-uat/` | `/opt/customers/andros/nex-payables/` |
| Compose project name | `uat-<instance-slug>` | `<customer-slug>-<app>` |
| Container / image names | `uat-<instance-slug>-<svc>` | `<customer-slug>-<app>-<svc>` |
| Example containers | `uat-andros-uat-backend` | `andros-payables-backend` |
| Public URL / Traefik host | `uat-<instance-slug>.isnex.eu` | `<customer-slug>-<app>.isnex.eu` |
| Traefik router names | `uat-<instance-slug>` / `-api` | `<customer-slug>-<app>` / `-api` |
| restart policy | `no` (keep) | `unless-stopped` |

Where:
- **`customer-slug`** = `(customer.subdomain or customer.slug).strip().lower()` — e.g. `andros`.
- **`app`** = project slug with leading `nex-` stripped — REUSE existing `derive_uat_slug()` logic (uat_provisioner.py:139, `removeprefix('nex-')`) — e.g. `nex-payables` → `payables`.
- **`full-project-slug`** = the project's real slug, kept verbatim for the DIR only — e.g. `nex-payables`.
- **`instance-slug`** (UAT) = the current `_instance_slug` = `{customer}-{environment}` (e.g. `andros-uat`) — UNCHANGED.

**BOTH environments use Traefik** (nex-proxy-net + Traefik labels). PROD is NOT empty-labels; it keeps Traefik labels, just with the clean host `<customer>-<app>.isnex.eu` and router names without the `uat-` prefix. Host-nginx routing for prod is already wired at the infra level (`/etc/nginx/sites-available/prod-traefik.conf` catch-all → Traefik) — the provisioner does NOT touch nginx.

## 3. Networking fix (BOTH envs)

The current compose puts `backend` (and `web`) ONLY on `nex-proxy-net`, so backend cannot reach `db` (which is on the compose default net) — the bug we hit manually. FIX: backend must be on **both** `nex-proxy-net` (Traefik) AND the compose `default` network (db). Apply to both prod and uat. (db + migrate stay on default.)

## 4. Code changes (env parameter threaded through)

Add an `environment: str` parameter (values `'uat'` | `'prod'`, default `'uat'` for back-compat) threaded from `deploy.deploy()` → runner → `provision_uat` → `build_uat_compose` → the label/name helpers.

**`backend/services/uat_provisioner.py`:**
- `:57` add `PROD_ROOT = Path("/opt/customers")` alongside `UAT_ROOT`.
- `:624-635 frontend_traefik_labels(slug, fe_port, environment, host)` — add params. Build `host` per env: uat → `uat-{slug}.isnex.eu`, prod → passed-in `{customer}-{app}.isnex.eu`. Router/service id: uat → `uat-{slug}`, prod → `{customer}-{app}`. KEEP Traefik enabled for both.
- `:637-648 backend_traefik_labels(...)` — same, id `...-api`.
- `:717 container_name` — uat `uat-{slug}-{name}`, prod `{customer}-{app}-{name}`.
- `:723 image` — uat `uat-{slug}-{name}:latest`, prod `{customer}-{app}-{name}:latest`.
- `:811 compose name` — uat `uat-{slug}`, prod `{customer}-{app}`.
- backend service networks: ensure BOTH `default` + `nex-proxy-net` (networking fix), both envs.
- backend restart: prod `unless-stopped`, uat keep `no`.
- `build_uat_compose(...)` + `provision_uat(...)`: add `environment`, `customer_slug`, `app`, `full_project_slug` params; select `PROD_ROOT / customer_slug / full_project_slug` vs `UAT_ROOT / uat_slug`.

**`backend/services/deploy.py`:**
- `:499-507` keep `_instance_slug` for UAT. Add `_prod_customer_app(customer, project)` → `(customer_slug, app, full_project_slug)`.
- `:510-522 _url_for_instance_slug` / `_instance_url` — branch on env: prod → `https://{customer}-{app}.isnex.eu`, uat → current.
- `:468-477` deploy() — pass `environment` + customer + project through to the runner/provisioner.

**`backend/services/orchestrator.py`:**
- `:3259` add `PROD_ROOT = Path("/opt/customers")`.
- `:3264 _uat_compose_path` — add env; prod → `PROD_ROOT / customer_slug / full_project_slug / "docker-compose.yml"`.
- `:3373 fe_host` — prod → `{customer}-{app}-{fe_role}`.
- `_run_uat_deploy` — make env-aware (or add a sibling `_run_prod_deploy`) that computes the prod compose path + container names. Verify-serves probes via the Traefik URL (both envs), not host ports.

## 5. Out of scope (Dedo handles separately)

- The `/opt/customers` mount into the NEX Studio backend container + control-plane redeploy (Dedo, infra).
- The `prod-traefik.conf` nginx catch-all (Dedo, already done).
- Migrating the existing manual andros instances (Dedo, later).
- No new port table / published host ports (Traefik routes without them).

## 6. Tests (safety oracle — MUST pass)

- Existing UAT tests pass UNCHANGED (environment defaults to `'uat'`).
- Add prod-mode tests in `backend/tests/` mirroring the UAT ones: assert prod compose has container names `{customer}-{app}-{svc}`, compose name `{customer}-{app}`, Traefik host `{customer}-{app}.isnex.eu`, router ids without `uat-`, root `/opt/customers/{customer}/{full-project-slug}`, restart `unless-stopped`, backend on both `default`+`nex-proxy-net`.
- Add a test asserting UAT still yields `uat-` names + `uat-{slug}.isnex.eu`.
- Run the FULL backend suite (`pytest`), not just the new file (shared provisioner module — CR-061 lesson).

## 7. Acceptance

`environment='prod'` produces a `/opt/customers/<customer>/<full-project-slug>/docker-compose.yml` with clean `<customer>-<app>-*` names, Traefik host `<customer>-<app>.isnex.eu`, backend reaching db, restart unless-stopped — and `environment='uat'` is byte-identical to today. Full test suite green. Report the diff summary + test result to Dedo.
