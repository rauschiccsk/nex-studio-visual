# v0.8.0 — Engine-owned release publish to GitHub (autonomous)

> **Status:** spec ready.
> **Owner:** Dedo (design) → nex-implementer (build) → independent verify → CI → deploy.
> **Why (LIVE):** nex-asistent v0.1.0 reached the `release` stage; the Coordinator finalized the release
> LOCALLY (clean, secure) but **`git push` to GitHub failed — the agent's headless environment has no GitHub
> credentials** → `release | coordinator | blocked`, 70 commits unpushed, CI never ran. This breaks the
> full-autonomy goal (NEX Studio must publish without a manual step). The **cockpit backend already has the
> credentials** (`GH_TOKEN`, docker-compose.yml:103) and create-project already pushes via `gh auth setup-git`
> + `git push` (template_bootstrap.py:339-377) — the gap is only that the RELEASE publish is done by the
> AGENT (no creds), not the ENGINE (has creds). Make the publish **engine-owned**.

> **§4 SECURITY:** reuse the EXISTING `GH_TOKEN` + the `gh auth setup-git` credential helper (exactly what
> create-project does). **No new credential, nothing written to code/commits/logs/output.** The token stays in
> the backend's runtime env.

---

## CR-1 — `_run_release_publish` (engine-owned push + CI verify)
Add `async def _run_release_publish(project_slug, repo_full_name) -> tuple[bool, str]` next to `_run_uat_deploy`
(`backend/services/orchestrator.py:2203`), modelled on it (`asyncio.create_subprocess_exec`, stderr→stdout,
`wait_for`; spawn-fail → `(False, …)`, timeout → `(False, …)`, **never raises**):
1. **Wire creds** — `gh auth setup-git` (idempotent; sets the HTTPS credential helper — reuse the
   template_bootstrap pattern, lines 339-348). Non-zero is non-fatal; the push surfaces the real error.
2. **Push** — `git push origin main` in `/opt/projects/<slug>` with a retry (mirror template_bootstrap
   354-377). Push failure after retries → `(False, "git push failed: <err>")`.
3. **Verify CI** — after a successful push, watch the run for the pushed HEAD:
   `gh run watch <run-id> --exit-status` (resolve the latest run for the branch/sha via `gh run list`),
   **bounded** by a module const `RELEASE_PUBLISH_TIMEOUT` (≈ `STAGE_TIMEOUT["release"]` = 900s). CI green →
   `(True, "published + CI green (<run-id>)")`; CI red → `(False, "CI failed (<run-id>): <failed jobs>")`; can't
   determine / watch times out → `(True, "pushed; CI still running (<run-id>) — monitor")` (push succeeded; do
   NOT false-block on a slow CI).
4. Module const `RELEASE_PUBLISH_TIMEOUT = 900`.

## CR-2 — Engine auto-publish in the FULL-FLOW release stage
Add `async def _release_auto_publish(db, state, *, on_message=None) -> None`, modelled **exactly** on
`_fast_fix_auto_deploy` (`:2415`): resolve `project_slug` + the repo from `project.repo_url`
(`backend/db/models/projects.py:32`, format `{github_org}/{slug}` — same source create-project uses; if NULL,
skip gracefully with a `system→director` note + `awaiting_director`, like the `_fast_fix_auto_deploy` NULL-slug
skip); run `_run_release_publish`; record the outcome as a
`system→director` notification (payload `{"release_publish": {ok, detail, run_id?}}`); then:
- **success** → `state.status = "awaiting_director"`, `next_action = "Publikované na GitHub + CI zelené — over a akceptuj (uat_accept)."`
- **failure** → `state.status = "blocked"`, `next_action = "GitHub publish/CI zlyhal: <detail>"` (surfaced, never hidden).

**Hook:** `_fast_fix_auto_deploy` is invoked at `orchestrator.py:2678` (the `fast_fix` release block). The
FULL-FLOW (`flow_type == "new_version"`) release settle is the new_version branch in the SAME release-handling
area — AFTER the Coordinator's `release` gate_report is verified, where the flow currently settles to
`awaiting_director` for `uat_accept`. Insert `await _release_auto_publish(db, state, on_message=on_message)`
there (the Coordinator finalizes LOCALLY; the engine publishes), gated to `flow_type == "new_version"` so the
fast_fix block (`:2678`) is untouched.

## CR-3 — `retry_publish` action (for a `release`/`blocked` state) + FE
For a release whose publish failed (incl. nex-asistent's CURRENT `release|blocked`, already committed locally):
- Register `"retry_publish"` in `_ACTIONS` (`:236`); it re-dispatches an engine step (not a stage advance) — put
  it in `_ADVANCING_ACTIONS` (`:258`) like `continue_build` so the settled-state guard protects it.
- `determine_available_actions` (`:296`), the `elif stage == "release":` branch (`~:359`, currently adds
  `"uat_accept"`): also add `"retry_publish"` when `status == "blocked"`.
- `apply_action` handler: assert `stage == "release"`; run `_release_auto_publish` (re-attempt push + CI);
  set `awaiting_director`/`blocked` per the result. (Engine step, synchronous within the action like the other
  engine steps, or via the dispatch path — match how `_fast_fix_auto_deploy`'s result is applied.)
- FE `PipelineActionBar.tsx`: a **"Publikovať na GitHub"** button at `release` + `blocked` (when
  `allowed("retry_publish")`). SK label + hint (*"Engine pushne lokálne commity na GitHub a sleduje CI."*).

## Scope / safety
- **Fast-fix lane UNTOUCHED** — `_fast_fix_auto_deploy` + the fast-fix release path are NOT modified. The
  engine auto-publish (CR-2) is gated to `flow_type == "new_version"`. (Whether fast-fix should also publish to
  GitHub is a separate question — OUT OF SCOPE here; do not touch it.)
- `claude_agent.py` untouched. §4: no credential in code/commits/output.

## Self-verify (Implementer, before DONE)
1. `poetry run pytest` (FULL) — baseline-verify the env-only `test_default_claude_config_dir`.
2. `ruff format --check . && ruff check .`; `cd frontend && npm run build && npm run lint`.
3. New tests (mock the subprocess/gh like the existing `_run_uat_deploy`/smoke tests): `_run_release_publish`
   push-fail → (False, …), push-ok+CI-green → (True, …), push-ok+CI-red → (False, …), push-ok+CI-timeout →
   (True, "still running"); `_release_auto_publish` success→awaiting_director, fail→blocked; `retry_publish`
   offered only at release/blocked, rejected elsewhere, absent for fast_fix.
4. `git grep -nE "GH_TOKEN|token" backend/services/orchestrator.py` shows NO token VALUE — only env/name usage.

Report exact outputs. STOP + report any gap (§2.4). Do NOT commit — Dedo commits + verifies.
