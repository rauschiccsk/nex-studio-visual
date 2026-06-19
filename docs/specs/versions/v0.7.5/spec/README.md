# v0.7.5 — Full-flow release verification (app-starts smoke + Director-triggered dual-build)

> **Status:** CR-1 BUILT + SHIPPED. **CR-2 DROPPED (Director decision 2026-06-19).**
> **Owner:** Dedo (design) → nex-implementer (build) → independent verify → CI → deploy.
> **Scope (final):** adds an always-on **app-starts behavioural smoke** at the **full-flow `gate_g` ONLY** (CR-1).
> The fast-fix lane (`flow_type='fast_fix'`) stays byte-identical.
>
> **CR-2 (Director-triggered dual-build / Tiborov test) was DROPPED before its execution core was built.**
> Rationale: the dual-build is the most expensive (a full 2nd build), noisiest (two independent LLM builds of a
> complex spec always diverge legitimately → false alarms + costly triage) and latest way to assure spec
> quality. It predates the **Customer agent** (Gate E), which now covers upfront spec-completeness cheaper +
> earlier, and the **behavioural acceptance suite** (CR-1) gives the independent release oracle without a 2nd
> build. The cwd-seam plumbing (CR-2.1/2.2) the Implementer had started was reverted. The CR-2 sections below
> are retained for decision history only — **not to be implemented.** See `CLAUDE.md §2.5` (Release Verification).

---

## 0. Context / root cause (why this CR exists)

The Tiborov **Dual-Build** test (`CLAUDE.md §2.5`) worked in the **pre-engine** cockpit flow (~2026-05-20,
nex-inbox v0.1.0). When the autonomous orchestrator **engine** was built — **CR-NS-018** (orchestrator engine,
2026-06-03) + **CR-NS-020** (per-task build loop, 2026-06-08) — the dual-build was **NOT ported** as an
executable engine step. Evidence: `git log -S "worktree" -- backend/services/orchestrator.py` returns
**nothing** — the orchestrator has **never** contained dual-build/worktree code. So `gate_g` degraded to a
generic Auditor review (`_directive_for("gate_g")` minimal brief). The fast-fix lane (CR-NS-094, 2026-06-16)
is **not** the cause and correctly skips `gate_g`.

This CR makes the full release verification **actually execute**:
1. **App-starts smoke** — always-on at full-flow `gate_g`; **HARD gate** (Director-approved): the built app must
   boot and its acceptance suite must pass, else `gate_g` **FAIL**.
2. **Dual-build (Tiborov test)** — **Director-triggered** action at full-flow `gate_g`: Build A (existing prod
   build) vs **Build B** (one fresh independent build, **re-planned from spec**, in an isolated git worktree).

Director decisions locked: smoke = **HARD gate**; Build B = **re-plan from spec** (faithful Tiborov test, per the
2026-05-20 directive — ONE fresh independent build, NOT two).

**Build it in 2 Implementer passes** (one version): **CR-1 = smoke** (foundational, lower risk), then **CR-2 =
dual-build** (the larger nested-pipeline feature).

---

## CR-1 — App-starts acceptance smoke (always-on, HARD gate at full-flow `gate_g`)

### CR-1.1 Engine-owned smoke runner
Add `async def _run_acceptance_smoke(project_slug: str, version_label: str) -> tuple[bool, str]` next to
`_run_uat_deploy` (`backend/services/orchestrator.py:2141`), modelled exactly on it
(`asyncio.create_subprocess_exec(["docker","compose",...])`, never raises, returns `(ok, detail)`; on
spawn-failure/timeout → `(False, reason)`).

Lifecycle (mirror `create_project_postscaffold._run_smoke_test`, lines 54–136, incl. the graceful SKIP):
1. **Discover** — if `/opt/projects/<slug>/docker-compose.yml` is absent OR the project has no `-m acceptance`
   tests (`backend/tests/acceptance/` convention), **SKIP gracefully**: return `(True, "smoke SKIPPED — no
   acceptance suite / no compose")` and record a `system→director` note. (Same shape as the postscaffold
   "SKIPPED — no docker-compose.yml" and `_fast_fix_auto_deploy` NULL-slug skip.)
2. **Isolate** — bring the stack up under an isolated compose project name `-p <slug>-smoke` with an override
   that **strips `container_name` and host port bindings** (generate an ephemeral override file in a temp dir),
   so a concurrent **live UAT** of the same project does NOT collide (risk flagged by design: asistent binds
   fixed `container_name` + host ports `10180/10182/10183`).
3. **Up** — `docker compose -p <slug>-smoke -f <compose> -f <override> up -d --build --wait` (services already
   declare healthchecks; `--wait` blocks until healthy). Ollama dependency is satisfied via the app's own
   `extra_hosts: host-gateway` + `OLLAMA_URL` (host:9132) — same as today's deploy.
4. **Seed** — run the app's env-driven idempotent seed (the `SEED_ADMIN_*` contract the acceptance fixtures
   rely on — `conftest.py:24-25`). Reuse the project's documented bootstrap command.
5. **Test** — `docker compose -p <slug>-smoke -f <compose> exec -T backend poetry run pytest
   backend/tests/acceptance -m acceptance -q`. Capture exit code + tail. (`xfail strict=False` tests are
   tolerated — they are not failures.)
6. **Teardown** — ALWAYS in a `finally`: `docker compose -p <slug>-smoke -f <compose> down -v` + remove the temp
   override file.

Module const `ACCEPTANCE_SMOKE_TIMEOUT = 900` (matching `UAT_DEPLOY_TIMEOUT`).

### CR-1.2 HARD-gate wiring (gate_g only)
Hook inside `verify_done` (`orchestrator.py:1884`) **after** `verify_mechanical` passes (~:1905), guarded by
`if block.stage == "gate_g":`. `_run_acceptance_smoke` is a **blocking `await`** (it returns
`tuple[bool, str]`) that runs BEFORE the Coordinator/Auditor judgment turn — so a smoke FAIL short-circuits and
prevents the judgment from running (the smoke is the hard deterministic gate, exactly like `verify_mechanical`).
On smoke **FAIL** return a **non-None reason**
(e.g. `"App-starts smoke FAIL: <tail>"`) so the existing mechanical-block settle (dispatch ~:2469-2478) renders
it as a **`gate_g` FAIL** the Director sees — i.e. the smoke is a deterministic sibling of `verify_mechanical`,
HARD by construction. Record the smoke evidence (pass/fail + tail) as a `system→director` message (reuse
`_record_message`, ~:2522) and feed a one-line smoke verdict into the Auditor's verdict prompt so the synthesis
reflects "app actually boots + acceptance green", not only spec-compliance.

### CR-1.3 Fast-fix safety (CR-1)
The smoke is invoked **solely** under `block.stage == "gate_g"`. `FAST_FIX_STAGE_ORDER` (`orchestrator.py:197`)
= `kickoff→build→release→done` has **no `gate_g`**, so a fast_fix version can never reach this code.
`verify_mechanical`, `invoke_claude`/`invoke_agent`, and every shared path are **untouched**. No new flag on the
shared invoke. Add an explicit `# full-flow only: fast_fix never reaches gate_g` comment at the hook.

---

## CR-2 — Director-triggered dual-build (Tiborov test) at full-flow `gate_g`

Build A = the existing prod build in `/opt/projects/<slug>/`. **Build B = ONE fresh independent build**,
**re-planned from spec**, in an isolated git worktree, with **no access to Build A**. Then a functional A-vs-B
diff. Match → A ships; mismatch → signal spec-gap or creative-drift (fix + repeat).

### CR-2.1 The `cwd` seam (minimum threading; defaults make every existing call byte-identical)
Today the subprocess cwd is hardcoded: `project_root = PROJECTS_ROOT / project_slug` (`claude_agent.py:206`),
`cwd=str(project_root)` (:252). Thread an **optional** override that, when `None`, reproduces today's path
EXACTLY:
1. `claude_agent._invoke_once(..., cwd: Path | None = None)` → `project_root = cwd if cwd is not None else
   PROJECTS_ROOT / project_slug`.
2. `claude_agent.invoke_claude(..., cwd: Path | None = None)` → pass-through to `_invoke_once`.
3. `orchestrator.invoke_agent(..., cwd: Path | None = None)` → forward into `invoke_claude` (call ~:1076-1088).
   **Charter resolution (~:1058-1060) must also honor `cwd`** (read the charter from the worktree).
4. `orchestrator.invoke_agent_with_parse_retry(..., cwd: Path | None = None)` → forward into BOTH inner
   `invoke_agent` calls (~:1233 primary, ~:1249 retry).
5. `orchestrator._plan_pass_once` / `_invoke_plan_pass` / `_run_task_plan_round` / `_run_build_round` → thread
   `cwd` down to their dispatches.

**Invariant:** every existing call site passes nothing → `cwd=None` → byte-identical behavior. Verify by `grep`
that no existing call site is changed except to accept the new defaulted kwarg.

### CR-2.2 Worktree + spec baseline
- **Spec baseline** — add `_spec_baseline_sha(db, version_id)`: the `dispatch_baseline_sha` of the **first**
  build dispatch for the version (the commit state of the spec BEFORE any build work). Document the ordering
  rule (earliest build dispatch, not a re-gate).
- **Worktree** — `git worktree add /opt/projects/<slug>-worktrees/dual-build-v<X.Y.Z>/ <spec-baseline-sha>`.
  Location is a **sibling** of the repo (outside the working tree, required by git worktree) and under the
  mounted `/opt/projects` (writable from the backend container). It is NOT a DB project, so project discovery
  ignores it.
- **.env provisioning** — copy the gitignored `.env` files (root + `backend/` + `frontend/`) from Build A into
  the worktree so Build B can build + run its tests. (P0 §4: never log/print contents; just `shutil.copy`.)
- **Cleanup** — `git worktree remove --force` + delete the dir in a `finally`, always.

### CR-2.3 Build B = a fresh, isolated nested build
Build B re-runs the build pipeline **from spec** in the worktree:
- **State isolation via a shadow Version** — the build functions (`_run_task_plan_round`/`_run_build_round`)
  operate on a `PipelineState` and write `Task`/`PipelineMessage` rows keyed by `version_id` (unique constraint
  on `version_id`, `db/models/pipeline.py:152`), so Build B CANNOT reuse the live version's row. Mechanism:
  create a **dedicated shadow `Version`** in the same project, flagged `is_dual_build=True` (new boolean column
  on `Version` — Alembic migration in `migrations/versions/`; default False; DROP not needed). The shadow
  version gets its own `PipelineState` row, and Build B's pipeline runs against it with `cwd=<worktree>` — so
  the build machinery is reused **unchanged** and Build B's Tasks/Messages persist isolated under the shadow
  `version_id`, never touching the live version's row. The shadow version + its rows + the worktree are torn
  down in the `finally` (CR-2.2) after the comparison; keep nothing unless the comparison FAILs (then retain
  for the Director's inspection, still flagged hidden).
- **FE/dispatch exclusion** — `is_dual_build=True` versions are excluded from the normal version list
  (FE + the versions query) and from normal auto-dispatch — they are reachable ONLY by the dual-build runner.
- **Session isolation** — Build B must NOT `--resume` Build A's claude session. Namespace the `(slug, role)`
  key in `_resolve_orch_session` (`orchestrator.py:406`) with a `-dualbuild-v<ver>` suffix → fresh cold
  sessions for every Build-B agent.
- **Re-plan from spec** (Director-locked) — Build B independently runs `_run_task_plan_round` (fresh task plan)
  THEN `_run_build_round` (per-task loop + auto-fix), all with `cwd=<worktree>`, reading the **same** spec
  package (`docs/specs/versions/v<X.Y.Z>/spec/**`). It is a full nested pipeline → a background multi-turn run,
  not a single agent turn.
- **Concurrency** — Build B runs against the shadow `version_id`, so the live version's single-flight guard
  (`dispatch_in_flight` set/cleared at `orchestrator.py:2091`/`3874`, checked at `:4658`; runner at
  `pipeline_runner.py:205,207`) keys on a DIFFERENT version and is not tripped — the live version stays parked at
  `gate_g` while Build B runs. Verify the in-flight check is genuinely per-`version_id` (not a global flag); if
  global, scope it per version as part of this CR.

### CR-2.4 A-vs-B comparison (functional diff PRIMARY)
After Build B completes, compute the diff (per `CLAUDE.md §2.5` + Auditor charter §6):
- **Functional (primary)** — **cross-run** the test suites: run **A's tests against B's code** AND **B's tests
  against A's code**; both must pass (the orchestrator runs these deterministically — it does not trust an agent
  claim of PASS, anti-blind-DONE).
- **Structural** — module/file/component organization diff (informational).
- **Behavioral** — for the acceptance inputs, do A and B produce the same outputs.
- **Verdict** — the Auditor synthesizes A≡B (functionally) → A ships ✅ / A≢B → ROLLBACK signal (spec gap or
  creative drift) with the concrete divergences. Persist a structured payload on the `gate_report` message:
  `{dual_build: {pass, functional_diff, structural_diff}, smoke: {pass}}` for the FE.

### CR-2.5 Director-triggered action + FE
- **Action `run_dual_build`** — register in `_ACTIONS` (`orchestrator.py:236`) + `_ADVANCING_ACTIONS` (:258,
  settle-guarded like `verdict`).
- **Offer ONLY at full-flow `gate_g`** — in `determine_available_actions` (:296) the `elif stage == "gate_g"`
  branch (:343) currently adds only `"verdict"`; add `"run_dual_build"` THERE. `stage == "gate_g"` IS the
  full-flow guard (fast_fix never reaches gate_g); add an explicit `# fast_fix never at gate_g` comment.
- **`apply_action` handler** (new `if action == "run_dual_build":` near verdict, ~:4837) — assert
  `state.current_stage == "gate_g"` (else `OrchestratorError "run_dual_build je platné len vo fáze gate_g"`);
  record a `director→auditor` directive; kick off the background Build-B run (CR-2.3) + comparison (CR-2.4); the
  result reaches the Director via the existing `gate_report → awaiting_director` path (run_dispatch ~:2440-2484).
- **Timeout** — Build B is a full build; do NOT bound it by `STAGE_TIMEOUT["gate_g"]=1200`. The nested build
  uses the normal per-task build timeouts; the action itself returns immediately (background run).
- **FE** — `frontend/src/components/cockpit/PipelineActionBar.tsx`: add a "Spusti dual-build (Tiborov test)"
  button rendered ONLY when the offered actions include `run_dual_build` (i.e. full-flow gate_g). SK label.

### CR-2.6 Fast-fix safety (CR-2)
- The action is offered only at `gate_g` (fast_fix never there).
- The `cwd` seam defaults to `None` everywhere → every existing dispatch (incl. all fast-fix dispatches) is
  byte-identical. **MANDATORY verification:** run the full fast-fix test suite + grep that no existing
  `invoke_*` call site changed except adding the defaulted kwarg.

---

## Self-verify (Implementer, before DONE — per charter §9)
1. `cd /opt/projects/nex-studio && poetry run pytest` (FULL backend suite, not just the touched file — shared
   modules `orchestrator.py`/`claude_agent.py` are imported widely).
2. `poetry run ruff format --check . && poetry run ruff check .`
3. `cd frontend && npm run build && npm run lint` (CR-2.5 FE).
4. **Fast-fix byte-identical proof:** the fast-fix tests pass unchanged; `git grep -n "invoke_claude\|invoke_agent\|_invoke_once"` shows existing call sites only gained a defaulted kwarg.
5. New tests: `_run_acceptance_smoke` graceful-skip + FAIL→reason; the `cwd` default-None path; the
   `run_dual_build` action gating (rejected off-gate_g, absent for fast_fix); `_spec_baseline_sha` ordering.

## Out of scope
Making the dual-build auto/MANDATORY at every gate_g (Director chose Director-triggered). A dedicated FE A-vs-B
panel beyond the structured payload (the payload is emitted; rich rendering can follow).
