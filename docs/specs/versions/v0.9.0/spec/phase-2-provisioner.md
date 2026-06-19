# v0.9.0 Phase 2 — UAT provisioner service (backend) + generalized compose + uat_slug setter

> Detailed impl spec for Phase 2 of the v0.9.0 autonomous UAT provisioning design (README.md).
> Phase 1 (infra) is DONE: Traefik (`nex-uat-traefik`) is up on `nex-proxy-net` (loopback :18080), the
> host-nginx wildcard `uat-*.isnex.eu → Traefik` forward is live, and ledger is migrated as the proof. Phase 2
> builds the backend so the engine (Phase 3) can provision a UAT in-process. **No engine wiring yet** (that's
> Phase 3) — Phase 2 delivers the importable service + the generalized template + the uat_slug setter, all
> unit-tested.

> **Fast-fix / existing CLI:** the manual `scripts/uat-deploy.py` CLI must keep working (Dedo/ops use it). The
> refactor extracts logic into an importable module; the CLI becomes a thin wrapper. No behaviour change to the
> CLI's existing output for the current 3-service projects.

---

## CR-1 — Generalize the UAT compose template to N services + Traefik labels
The current `templates/uat/docker-compose.yml.j2` is hardcoded to postgres+backend+frontend; nex-asistent's
**qdrant + external `nex-network` + Ollama `host.docker.internal` (`extra_hosts: host-gateway`)** would be
DROPPED → broken UAT. Generalize:
- **Pass through ALL source-project services** from `/opt/projects/<slug>/docker-compose.yml` (qdrant, redis,
  worker, …), not just db/backend/frontend — preserving their images/build, env, volumes, healthchecks,
  `extra_hosts`, and any `networks` they declare (e.g. the external `nex-network` for Ollama).
- **Routing is via Traefik (Phase 1), NOT host ports.** Add the `nex-proxy-net` (external) network to the compose
  and to the **frontend + backend** services, with these **exact labels** (matching the Phase-1 ledger migration):
  - frontend service:
    ```
    - "traefik.enable=true"
    - "traefik.docker.network=nex-proxy-net"
    - "traefik.http.routers.uat-<slug>.rule=Host(`uat-<slug>.isnex.eu`)"
    - "traefik.http.routers.uat-<slug>.entrypoints=web"
    - "traefik.http.routers.uat-<slug>.priority=10"
    - "traefik.http.services.uat-<slug>.loadbalancer.server.port=<fe-internal-port>"   # e.g. 80
    ```
  - backend service (the `/api` split, higher priority so it wins):
    ```
    - "traefik.enable=true"
    - "traefik.docker.network=nex-proxy-net"
    - "traefik.http.routers.uat-<slug>-api.rule=Host(`uat-<slug>.isnex.eu`) && PathPrefix(`/api`)"
    - "traefik.http.routers.uat-<slug>-api.entrypoints=web"
    - "traefik.http.routers.uat-<slug>-api.priority=20"
    - "traefik.http.services.uat-<slug>-api.loadbalancer.server.port=<be-internal-port>"  # e.g. 8000
    ```
- **Host ports:** drop the required host-port bindings for routing (Traefik routes by network). Optionally keep a
  single loopback FE port for debugging, behind a flag — but do NOT depend on host ports for the domain to work.
  The `.uat-ports.json` allocation can stay (harmless) or be made optional; routing no longer needs it.
- `<slug>` = the UAT slug (uat_slug, CR-3); `<fe/be-internal-port>` = detected from the source compose (the FE
  container's listen port, the BE's app port) — generalize the existing `_uat_lib.detect_*` to find FE/BE among
  arbitrary services (by role/label/convention), not by assuming the service is named `frontend`/`backend`.

## CR-2 — Refactor provisioning into an importable backend service
- Extract the provisioning flow (`scripts/uat-deploy.py:deploy()` steps: detect → render compose/.env/(no
  nginx now — Traefik) → create dirs → optional port alloc) + the `_uat_lib` helpers into
  **`backend/services/uat_provisioner.py`** with an async-friendly entrypoint, e.g.
  `provision_uat(project_slug, uat_slug) -> ProvisionResult` (renders `/opt/uat/<uat_slug>/docker-compose.yml`
  + `.env`, creates dirs; does NOT build/up — that's `_run_uat_deploy` in Phase 3). It must be importable from
  the async orchestrator (sync work wrapped via `asyncio.to_thread` is fine).
- **Drop the nginx-vhost rendering** from the provisioner path (Traefik replaces it). Keep `templates/uat/`
  compose+.env templates.
- `scripts/uat-deploy.py` becomes a **thin CLI wrapper** over the new service (so manual ops still work).
  Verify its existing behaviour for a current 3-service project is unchanged except the nginx step is gone
  (now Traefik labels instead) — update its summary/output accordingly.
- §4: secrets generated as today (synthetic for `${VAR}`), `.env` chmod 600, never logged/printed.

## CR-3 — `uat_slug` autonomous setter + write path
- Add a service `derive_uat_slug(project) -> str` = `project.slug` with a leading `nex-` stripped
  (`nex-ledger→ledger`, `nex-inbox→inbox`, `nex-asistent→asistent`) — matches the existing pattern.
- Add a **write path** to set `project.uat_slug` (a `project_service` method; persist on the Project row). Today
  the column is read-only (no setter). Phase 3 calls this at first-release; for now just provide + unit-test the
  setter (idempotent; does not overwrite a manually-set non-null uat_slug unless forced).

## Scope / safety
- Phase 2 adds the service + template + setter; **does NOT wire the engine** (Phase 3) and does NOT change the
  release flow. The existing `_run_uat_deploy` / `_release_auto_uat_deploy` are untouched here. Fast-fix
  untouched. `claude_agent.py` untouched.

## Self-verify (Implementer, before DONE)
1. `poetry run pytest` (FULL) — baseline-verify the env-only `test_default_claude_config_dir`.
2. `ruff format --check . && ruff check .`.
3. New unit tests: given nex-asistent's source compose (qdrant + BE + FE + external nex-network + Ollama
   extra_hosts), the provisioner renders a UAT compose that (a) includes ALL services incl. qdrant, (b) preserves
   extra_hosts + the external network, (c) adds `nex-proxy-net` + the exact Traefik labels to FE+BE with the
   right internal ports, (d) routes by Traefik not host ports. `derive_uat_slug` cases (nex-*/no-prefix). The CLI
   wrapper still renders a valid compose for a 3-service project.
4. Confirm the existing `_run_uat_deploy`/release path + fast-fix are unchanged (no engine wiring in this phase).

Report exact outputs. STOP + report any gap (§2.4). Do NOT commit — Dedo commits + verifies.
