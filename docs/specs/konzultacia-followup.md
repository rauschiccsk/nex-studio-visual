# Follow-up — corrections from full-suite + adversarial verification of Konzultácia

Full 2208-test suite (root `tests/` + `backend/tests/`) + two adversarial verifiers surfaced one
regression, one leaky safety guarantee, and one duplicate-version bug. Metrics-safety (consult
tokens fold into system-overhead, never a build phase) and no-build-mutation are CONFIRMED correct
— leave them. Branch `v2.0.0-dev`.

**Self-verify scope (mandatory):** the change touches the shared spine (`orchestrator.py`,
`claude_agent.py`), so run the **FULL** suite from repo root: `.venv/bin/python -m pytest -q`
(≈2208 tests), NOT just `pytest backend`. FE: build+lint+test.

---

## Fix 1 — REGRESSION: test double missing `allowed_tools` (12 failing tests)

`invoke_agent` now passes `allowed_tools=` to the claude callable on every turn. The root-suite
fake `FakeClaude.__call__` (`tests/test_orchestrator_v2_invoke_agent.py`) doesn't accept it →
`TypeError: FakeClaude.__call__() got an unexpected keyword argument 'allowed_tools'` × 12
(orchestrator.py:2652). Production is fine (real `invoke_claude` has the param).
Fix: update the fake(s) to accept `allowed_tools` (accept + record it, or `**kwargs`). Grep for
EVERY test double that stands in for `invoke_claude`/the claude callable across `tests/` AND
`backend/tests/` and give each the param. Then the full suite must be green (minus the known
pre-existing `test_default_claude_config_dir` env-path baseline).

## Fix 2 — READ-ONLY GUARANTEE hardening (the safety core)

Today consult passes `--allowedTools Read,Grep,Glob` + `--disallowedTools <_MUTATING_TOOLS>` but NO
`--permission-mode`, so it inherits the project's `bypassPermissions` (`.claude/settings.json`) →
the allow-list is redundant and the **deny-list is the only guard** (leaky: MCP write tools and any
future tool are auto-approved, not blocked). Two fixes:

- **2a — add `Task` to `_MUTATING_TOOLS`** (`claude_agent.py:121`). The deny-list has `Agent` but not
  `Task`; the CLI spawns sub-agents via `Task` (claude_agent.py:330's own comment) and the sibling
  `pipeline_activity.py:30-32` deliberately keys on BOTH names. Mirror it:
  `("Bash","Write","Edit","MultiEdit","NotebookEdit","Agent","Task")`. Without this a consult could
  spawn a write-capable sub-agent.
- **2b — make the allow-list EXCLUSIVE (deny-by-default), not a deny-list under bypass.** When
  `allowed_tools` is set (consult), also pass **`--permission-mode default`** (override the project's
  `bypassPermissions`) so ONLY the allowed read tools are auto-approved and EVERY other tool —
  including unenumerated MCP (`mcp__*`) and future tools — is denied by default in headless, not
  auto-approved. Keep `--disallowedTools` as defense-in-depth. Build turns (`allowed_tools is None`)
  are UNCHANGED (no `--permission-mode` added → today's behavior).
  - NOTE: the headless enforcement of this will be PROVEN by a live read-only smoke (Dedo, before v3
    deploy): a real consult turn that attempts a `Write` and a `Bash` write must be REFUSED while
    normal Q&A still answers (no hang). If `--permission-mode default` makes an unapproved tool HANG
    in headless instead of deny, STOP and report — do not silently swap modes.

## Fix 3 — change_request marker never consumed → duplicate versions + stale bar (one root cause)

`capture()` mints a new backlog REQ + new version on every call; the FE bar re-renders on any
lingering `change_request` marker (`ChangeRequestBar.tsx:35-42` scans newest→oldest, returns the
first marked message — a later advisory answer with no marker does NOT clear it). Repeated clicks /
revisits / a double-click race mint duplicate draft versions (1.1.0, 1.2.0, …).

- **BE:** make capture idempotent per SOURCE consult message. The endpoint takes the source message
  id; on capture, stamp that message's payload with `change_request.captured_version_id` (+ number).
  A second capture of an already-captured marker returns the EXISTING minted version (no new mint).
- **FE:** the bar renders ONLY when the **latest** message carries a `change_request` that has NO
  `captured_version_id` **and** `current_stage === 'done'` (terminal). It disappears after capture and
  is never shown by a stale/older marker or mid-build. Guard the click against double-submit
  (disable synchronously, not via a post-render state race).

## Fix 4 — return the minted version's project + number (FE navigation correctness)

The capture endpoint must return the minted version's `{project_slug, version_number, version_id}`.
The FE navigates using the RETURNED slug (not `selectedProject.slug`), so navigation is correct even
if the pin ever diverges from the consulted version's project.

## Also gate the marker to terminal state (hardening)
Since `change_request` is now in `PipelineStatusBlock`'s schema, any turn is grammar-permitted to emit
it. Ensure only a consult (`stage=='done'`) turn's marker is honored by the capture endpoint (reject a
capture whose source version is not terminal) so a mid-build marker can never mint a version.

---

## Tests (mandatory, RED→GREEN)
- Fix 1: full suite green.
- Fix 2a: `_MUTATING_TOOLS` includes `Task`; a consult invocation's deny-list contains Bash/Write/Edit/
  MultiEdit/NotebookEdit/Agent/Task. Fix 2b: a consult invocation passes `--permission-mode default`;
  a build invocation (allowed_tools None) passes NO `--permission-mode`.
- Fix 3: capturing the SAME source marker twice mints exactly ONE version (2nd returns the existing);
  FE bar hidden after capture, on an advisory follow-up (no marker), and mid-build.
- Fix 4: endpoint returns slug+number; FE navigates to the returned slug.
- FULL `.venv/bin/python -m pytest -q`; FE build+lint+test.
