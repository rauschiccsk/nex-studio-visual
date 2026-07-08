# Konzultácia — OS-level read-only sidecar sandbox (Fix 2, hard guarantee)

Director-approved 2026-07-08. The read-only consult guarantee must be enforced by the KERNEL, not
by a CLI deny-list (a deny-list can't be completed — a live smoke found Task/Workflow/Skill/
ToolSearch/EnterWorktree… kept leaking; per the Bash-permission lesson only OS isolation is robust).
Feasibility PROVEN by Dedo (see below). Branch `v2.0.0-dev`. Self-verify: FULL
`.venv/bin/python -m pytest -q` from repo root + ruff; FE unaffected (no FE change).

## Proven feasibility (do not re-investigate — build on these facts)
- Backend container has the docker CLI (29.5.3) + `/var/run/docker.sock` (rw) → it can launch a
  sibling container. It runs as user `andros`.
- `claude` is already baked into `nex-studio-backend:v3.0.0` (`/usr/lib/node_modules/@anthropic-ai/
  claude-code`) → the sidecar reuses that SAME image; nothing to install.
- Proven live: a throwaway `docker run --rm` with the project bind-mounted `:ro`, `~/.claude`
  bind-mounted `:ro` + `--tmpfs` scratch + `CLAUDE_CONFIG_DIR`, NO docker.sock, `--user andros`,
  `--entrypoint claude … --permission-mode default --allowedTools Read,Grep,Glob`:
  claude authenticates, READS the project, and a RAW SHELL write/append/delete into the project is
  refused by the kernel ("Read-only file system"); host files untouched; docker.sock absent inside.
- Host↔container project path mapping: backend sees `/opt/projects/<slug>` = host
  `/opt/projects-v3/<slug>`; customer projects `/opt/customers/<slug>` = host `/opt/customers/<slug>`.
  A sibling `docker run -v` needs the HOST path (the daemon resolves binds on the host), so the
  backend must translate its in-container path → host path when composing the run.

## Goal
Run the CONSULT turn (only) inside an isolated sidecar container so the project is physically
read-only and the host is unreachable. Build turns are UNCHANGED (in-process subprocess as today).

## Part 1 — Sidecar launcher (backend)
1. New module `backend/services/consult_sandbox.py`: `run_consult_in_sandbox(...)` that composes and
   runs the sidecar via `docker run --rm` (using the mounted docker.sock, same as any sibling launch).
   Exact contract (all mandatory — these ARE the guarantee):
   - `--rm` (ephemeral), `--user andros`, `--entrypoint claude`, image = the running backend image
     tag (read it from env/`APP_VERSION`, default `nex-studio-backend:v3.0.0`).
   - Mounts, EXHAUSTIVE — nothing else:
     - project → `-v <HOST_PROJECT_PATH>:/opt/projects/<slug>:ro` (READ-ONLY — the hard guarantee)
     - auth → `-v /home/andros/.claude:/home/andros/.claude:ro` + `--tmpfs /home/andros/.claude-scratch`
       + `-e CLAUDE_CONFIG_DIR=/home/andros/.claude` (claude authenticates read-only; its own
       session scratch goes to tmpfs).
     - NO `/var/run/docker.sock`, NO `/opt/customers`, NO `/opt/uat`, NO credentials store, NO
       `/opt/infra`, NO knowledge mount. The sidecar sees ONLY the one project (`:ro`) + auth (`:ro`).
   - `-w /opt/projects/<slug>` (cwd = project, as today).
   - Pass through the SAME per-turn flags the in-process consult uses: `--permission-mode default
     --allowedTools Read,Grep,Glob --disallowedTools <_MUTATING_TOOLS>` (defense-in-depth kept),
     plus `--output-format json`, `--model`/`--effort` if set, `--session-id`/`--resume`,
     `--append-system-prompt <charter>`, and the prompt. Reuse the arg-builder from
     `claude_agent.invoke_claude` so the sidecar and in-process turns stay identical except transport.
   - Network: kept. AUTH IS THE CLAUDE MAX 20× SUBSCRIPTION (OAuth token in
     `~/.claude/.credentials.json`), NOT the Anthropic developer API (ICC rule §15 — never the direct
     Anthropic API). The mounted `~/.claude` carries exactly that OAuth token, so the sidecar
     authenticates via the MAX subscription just as the backend does today; no API key is involved.
     HARDENING (build it): restrict the sidecar to egress only — attach it to a dedicated network with
     no route to the other compose services / host-internal services (it must reach the Claude
     subscription endpoint the CLI uses, but NOT the DB, the credentials store, or sibling containers).
     Do NOT hardcode `api.anthropic.com` — allow whatever host(s) the MAX-subscription `claude` CLI
     actually contacts (discover empirically). If a no-internal-egress network is not cleanly
     achievable in this pass, ship with default bridge + the deny-by-default permission-mode
     (WebFetch/WebSearch are NOT in the allow-set → denied) and LOG that network-egress-restriction is
     a follow-up — do NOT silently claim it.
   - Timeout + process-tree kill parity with `invoke_claude` (a hung sidecar must be `docker kill`ed
     and `--rm` reaped; never leak containers).
2. Parse the sidecar's stdout envelope with the EXISTING `_usage_from` / result parsing (identical
   `--output-format json` envelope), returning the same `(text, usage, structured_output)` tuple so
   the caller is transport-agnostic.

## Part 2 — Route the consult turn through the sidecar
- In the consult path (`run_consult_turn` → `invoke_agent_with_parse_retry` → `invoke_agent` →
  `invoke_claude`), when the turn is a CONSULT (read-only profile active), execute via
  `consult_sandbox.run_consult_in_sandbox` INSTEAD of the in-process subprocess. The cleanest seam:
  branch inside `invoke_claude` when `allowed_tools` is set AND a `sandbox=True` (or a
  `CONSULT_SANDBOX=1` env, default on in prod) flag is passed by the consult caller; build turns
  (`allowed_tools is None`) never touch the sidecar path. Keep the in-process read-only path as a
  fallback ONLY if the sidecar is unavailable, and if so, `_record`/log the degraded mode honestly
  (do not silently fall back to a weaker guarantee without a trace).
- Metrics/state: unchanged — the sidecar produces the same envelope; `run_consult_turn` still stamps
  `stage='done'`, `metrics_phase=None`, no build mutation.

## Part 3 — Tests
- Unit: `run_consult_in_sandbox` composes the EXACT argv — assert the `-v …:ro` project mount is
  read-only, `~/.claude:ro`, tmpfs scratch, `CLAUDE_CONFIG_DIR`, `--user andros`, `--entrypoint
  claude`, and that docker.sock / customers / uat / credentials / infra are NOT mounted. (Mock the
  subprocess; inspect argv — mirror `test_claude_agent_readonly_tools.py`.)
- Unit: a BUILD turn (`allowed_tools=None`) NEVER calls the sandbox launcher (in-process only).
- Unit: sidecar envelope parse → same `(text, usage, structured_output)` tuple as in-process.
- Full `pytest` from root (shared spine) + ruff. FE unaffected.

## Live acceptance (Dedo, before v3 deploy — NOT the Implementer's)
A real consult sidecar against a done version: (a) answers a question, (b) a raw-shell write into the
`:ro` project is kernel-refused, (c) docker.sock/customers/uat absent inside, (d) container reaped
(no leak). Only then flip `CONSULT_SANDBOX` on in v3.

## Out of scope
- Sandboxing BUILD turns (they legitimately write the project) — unchanged.
- Full network micro-segmentation beyond the egress restriction above — note as follow-up if not clean.
