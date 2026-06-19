# v0.8.1 — Full-flow release: engine UAT-deploy + honest "no UAT" completion

> **Status:** spec ready.
> **Owner:** Dedo (design) → nex-implementer (build) → independent verify → CI → deploy.
> **Why (LIVE):** the FULL-FLOW release goes straight from the GitHub publish (v0.8.0) → `awaiting_director` →
> `uat_accept` → `done` with **no UAT-deploy step** — so `uat_accept` is HOLLOW (nex-asistent: ~1 second,
> nothing deployed, nothing to test) yet the completion claims **"UAT akceptované zákazníkom"** (misleading —
> no UAT existed). The fast-fix lane already deploys to UAT before accept (`_fast_fix_auto_deploy` →
> `_run_uat_deploy`); the full flow is behind. Bring it in line + make the no-UAT completion honest.

---

## CR-1 — engine UAT-deploy in the full-flow release settle
After the v0.8.0 `_release_auto_publish` **succeeds** (full-flow `new_version` release settle), run an engine
UAT-deploy, modelled on `_fast_fix_auto_deploy` (`orchestrator.py:2415`) and reusing the SAME low-level
`_run_uat_deploy` (`:2203`):
- Resolve `project.uat_slug`.
- **`uat_slug` set + compose exists** → `_run_uat_deploy` → success ⇒ `status="awaiting_director"`,
  `next_action="Nasadené na UAT — over a akceptuj."` + a `system→director` notification; failure ⇒
  `status="blocked"` with the deploy error (surfaced, never hidden).
- **`uat_slug` NULL / compose missing** → graceful, **HONEST** skip: `status="awaiting_director"`,
  `next_action="Žiadny UAT nakonfigurovaný — dokončíš bez UAT testu."` + a `system→director` note
  (`payload={"uat_deploy": {"skipped": True, "reason": …}}`). **No false UAT claim.**
- Sequencing: in the full-flow release settle, chain `publish (v0.8.0) → on publish-ok → this UAT-deploy →
  settle`. (A failed publish still blocks at the publish step as today.)

**Do NOT modify the fast-fix path.** Reuse `_run_uat_deploy` (shared low-level), but leave
`_fast_fix_auto_deploy` and the fast_fix release block untouched. If a shared helper is extracted, the fast-fix
behaviour MUST stay byte-identical (verified by the fast-fix tests). The new full-flow deploy is gated to
`flow_type == "new_version"`.

## CR-2 — honest `uat_accept` completion message
The `uat_accept` handler currently records **"UAT akceptované zákazníkom — pipeline dokončená."** unconditionally.
Make it HONEST based on whether a UAT was actually deployed (i.e. `project.uat_slug` set):
- **UAT deployed** (uat_slug set) → keep "UAT akceptované zákazníkom — pipeline dokončená."
- **No UAT** (uat_slug NULL) → e.g. **"Verzia akceptovaná a dokončená — bez UAT testu (projekt nemá
  nakonfigurovaný UAT)."** Never claim a customer UAT acceptance that did not happen.

## Scope / safety
- Full-flow (`new_version`) only. Fast-fix lane untouched (`_fast_fix_auto_deploy` + fast_fix release block
  unchanged; reuse only the shared `_run_uat_deploy`). `claude_agent.py` untouched.
- The deeper "NEX Studio auto-PROVISIONS a UAT for a brand-new project" is OUT OF SCOPE (separate autonomy
  feature). For nex-asistent (no uat_slug, not yet deployable — needs the Slovak dictionary + a running Ollama)
  this CR makes the behaviour HONEST (no-UAT path); it does not itself deploy nex-asistent.

## Self-verify (Implementer, before DONE)
1. `poetry run pytest` (FULL) — baseline-verify the env-only `test_default_claude_config_dir`.
2. `ruff format --check . && ruff check .`; `cd frontend && npm run build && npm run lint` (if any FE touched).
3. New tests: full-flow release with uat_slug set → deploy ran → awaiting_director ("nasadené na UAT");
   deploy fail → blocked; uat_slug NULL → awaiting_director + honest "žiadny UAT" (NO "UAT akceptované");
   `uat_accept` completion message honest per uat_slug. Fast-fix deploy tests still green (byte-identical).
4. Confirm the fast_fix release path/tests are unchanged.

Report exact outputs. STOP + report any gap (§2.4). Do NOT commit — Dedo commits + verifies.
