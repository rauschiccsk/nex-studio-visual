# v0.9.0 — Autonomous UAT provisioning (DESIGN)

> **Status:** DESIGN — for Director approval before implementation (waterfall §2).
> **Owner:** Dedo (design) → phased build (nex-implementer + Dedo infra) → independent verify → CI → deploy.
> **Goal:** NEX Studio provisions + deploys a project's UAT **with ZERO manual steps and no Dedo** — so a future
> Director (Tibor/Nazar) creates a project, it reaches release, and a working UAT appears at
> `uat-<slug>.isnex.eu` automatically. Director decision 2026-06-19: route via a **containerized
> auto-discovery reverse proxy (B)**, not host-nginx-with-sudo (A) — no host privilege for the cockpit, UAT
> isolated from PROD.

---

## 0. Current reality (from the 2026-06-19 grounding) — what already works
- **DNS:** Cloudflare wildcard `*.isnex.eu` (proxied) already resolves every `uat-<slug>` — **zero DNS action per UAT.**
- **Edge TLS:** Cloudflare Universal SSL `*.isnex.eu` terminates edge TLS → browsers fine without a per-UAT cert.
- **Provisioning logic exists** in `scripts/uat-deploy.py` + `scripts/_uat_lib.py` (detect-from-source-compose,
  port alloc 19500–19599 in `.uat-ports.json`, render compose/.env/nginx-vhost from `templates/uat/*.j2`,
  build+up+wait-healthy) — but it is **CLI-only**, the engine never calls it, and it **stops at printing manual
  `sudo` nginx steps**.
- **Engine has redeploy only:** `_run_uat_deploy` / `_release_auto_uat_deploy` (orchestrator.py:2241/2731)
  redeploy an EXISTING `/opt/uat/<slug>/docker-compose.yml`; honest-skip when absent.

## 1. Gaps to close (root blockers)
1. `project.uat_slug` has **no write path** (hand-edited DB field) → engine skips every unconfigured project.
2. Engine **never provisions** — only redeploys; no first-time create path.
3. **nginx activation = manual `sudo`** (the zero-touch blocker → solved by the Traefik proxy, §2).
4. The UAT **compose template is hardcoded** to postgres+backend+frontend → nex-asistent's **qdrant + external
   network + Ollama `host.docker.internal` route would be DROPPED** → broken UAT. Must generalize to N services.
5. `${VAR}` secrets → `__UAT_SYNTHETIC__` placeholders + the app must **self-seed** (SK dict) for a functional UAT.

---

## 2. Target architecture (B — containerized auto-discovery proxy)

**Traefik** container owns all `uat-*.isnex.eu` routing via **docker-label auto-discovery** — a UAT container
with the right labels is routed automatically: **no per-UAT config file, no `sudo`, no reload.**

Edge → origin path (one-time wiring, then per-UAT is label-only):
```
browser → Cloudflare (*.isnex.eu, edge TLS) → ANDROS:443 (host-nginx, PROD, owns 443)
  → [ONE-TIME host-nginx rule: server_name ~^uat-.*\.isnex\.eu$  →  proxy_pass 127.0.0.1:<traefik-port>]
  → Traefik (docker provider) → routes by Host(uat-<slug>.isnex.eu) → UAT frontend container; /api → backend
```
- The single `uat-*` → Traefik forwarding rule in host-nginx is the **only** privileged/host change, done **ONCE**
  (Dedo/infra setup), **never per UAT**. After that, a new UAT = docker labels only → zero host touch, zero sudo.
- **TLS:** Cloudflare handles edge TLS (wildcard). host-nginx → Traefik hop is loopback HTTP; Traefik → UAT
  containers is the internal docker network. **No per-UAT cert.** (A real wildcard origin cert via DNS-01 is an
  optional future hardening; the current CF-terminated path works — out of scope here.)
- **UAT containers** bind to the Traefik docker network (not host ports), labelled:
  `traefik.http.routers.uat-<slug>.rule=Host('uat-<slug>.isnex.eu')`, frontend as the default route, backend on
  `PathPrefix('/api')`. Port allocation on the host becomes optional (Traefik routes by network, not host port) —
  keep the `.uat-ports.json` allocation only if any service still needs a host port; otherwise drop host binding.

**Why B:** no host-sudo for the cockpit (a compromised cockpit can't touch PROD routing), UAT isolated from the
shared PROD host-nginx blast radius, and per-UAT routing is truly zero-touch (labels, auto-discovered).

---

## 3. Phased plan (each phase = a CR; build + verify in order)

### Phase 1 — Infra: Traefik + one-time host-nginx forward + migrate the 3 live UATs (Dedo/ops)
- Stand up the Traefik container (own compose under `/opt/infra/traefik/` or `/opt/uat/_proxy/`; docker provider;
  the UAT docker network; dashboard off/secured).
- Add the **one-time** host-nginx `uat-*.isnex.eu → Traefik` forwarding rule (the single sudo, done once).
- **Migrate ledger / inbox / dev** UAT routing from their host-nginx vhosts to Traefik labels — **one at a time,
  keep the old vhost until the Traefik route is validated, rollback-ready** (inbox ≈ MÁGERSTAV, careful).
- **Validate** each UAT still serves over its `uat-*.isnex.eu` domain after migration.
- This phase is infra/ops (not project code) — Dedo executes, with `--dry-run`/validation at each step.

### Phase 2 — Provisioner service (backend, importable)
- Refactor the provisioning logic out of `scripts/uat-deploy.py` + `_uat_lib.py` into an **importable backend
  service** (`backend/services/uat_provisioner.py`) the async orchestrator can call in-process; keep the CLI as a
  thin wrapper (so `uat-deploy.py` still works for manual ops). No behaviour change for the existing CLI.
- **Generalize the compose template** (`templates/uat/docker-compose.yml.j2`): pass through ALL source-project
  services (qdrant, redis, …), networks, `extra_hosts` (Ollama `host.docker.internal`), volumes — not just the
  fixed 3. Add Traefik labels to the FE (default) + BE (`/api`) services. nex-asistent forces this (qdrant +
  Ollama + external net).
- **`uat_slug` autonomous setter + write path:** derive `uat_slug` = `project.slug` minus a leading `nex-`
  (ledger/inbox/dev pattern), persist on the Project (new service method + migration if a default is wanted);
  set at first-release (§Phase 3). Register a write path (service, not necessarily public API).

### Phase 3 — Engine first-release auto-provision + teardown symmetry
- `_release_auto_uat_deploy` (orchestrator.py:2731): when `/opt/uat/<slug>/docker-compose.yml` is **missing**
  (today an honest-skip), instead → **derive+set uat_slug → call `uat_provisioner.provision()`** (render compose
  + .env + create dirs + allocate port if needed + Traefik labels) → then `_run_uat_deploy` (build+up). If present
  → redeploy as today. Success → `awaiting_director` ("Nasadené na UAT — over a akceptuj"); failure → `blocked`
  with the error. Gated `flow_type == "new_version"` (fast-fix already deploys to its existing UAT — untouched).
- **Teardown symmetry:** on version supersede / project delete, `docker compose down` the UAT + reclaim the port;
  Traefik auto-de-routes when the container is gone (no host change). (Wire into the existing teardown path.)

### Phase 4 — Self-seed + secrets (functional UAT with zero touch)
- **App self-seed on boot** (env-driven, idempotent): admin + any required data (e.g. nex-asistent SK
  dictionary) seeded during the app's FastAPI lifespan — a **create-project template requirement** so every
  NEX-Studio-built app comes up functional in UAT with no manual data step. (Ties to
  the create-project backlog.)
- **`${VAR}` real external secrets:** Ollama is the shared host-gateway (works). For projects that genuinely need
  a real external secret in UAT (IMAP/Telegram/…), a **project-declared "UAT needs these secrets" contract**
  sourced from the `ri`-gated `/api/v1/credentials` store (per §4) — minimal; synthetic + graceful-degradation
  otherwise. (nex-asistent needs none beyond Ollama.)

### Port registry
- Register the UAT block **19500–19599** (+ derived +100/+200, or N/A if Traefik-network-only) in KB
  `DECISIONS.md` D-020, then RAG-reindex (§13).

---

## 4. Risks
- **Migrating live UATs (Phase 1)** — ledger/inbox/dev are in use; migrate one at a time, validate, rollback-ready.
- **One-time host-nginx change** still needs sudo ONCE (acceptable one-time infra; never per-UAT).
- **Cloudflare wildcard + CF token** are an external dependency NEX Studio doesn't manage; the wildcard makes
  per-UAT DNS zero-touch today. CF token ownership/rotation = flag for "no expiring credentials" (relevant only
  if we later add DNS-01 origin certs — out of scope here).
- **Traefik = new infra** to run, but standard + low-maintenance (auto-discovery, no per-UAT config).

## 5. Validation (the whole feature)
End-to-end test = **re-provision nex-asistent's UAT autonomously**: from `release`, the engine derives uat_slug,
provisions (qdrant+BE+FE+Ollama route, Traefik labels), deploys, and `uat-asistent.isnex.eu` serves the running
app — with **zero manual steps**. Then `uat_accept` is meaningful (a real UAT was tested). The 3 migrated UATs
keep serving.

## Out of scope (separate)
DNS-01 wildcard origin cert; a scoped CF API token + health monitoring; replacing the PROD host-nginx (only the
UAT routing moves to Traefik).
