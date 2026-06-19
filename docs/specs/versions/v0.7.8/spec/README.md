# v0.7.8 — offer `rerun_release_audit` at gate_g/blocked (not only awaiting_director)

> **Status:** spec ready.
> **Owner:** Dedo (design) → nex-implementer (build) → independent verify → CI → deploy.
> **Why (LIVE):** nex-asistent is parked at `gate_g | auditor | blocked` — the Auditor asked a question after
> the v0.7.5 smoke FAIL. The v0.7.6 `rerun_release_audit` action is OFFERED only at `status ==
> "awaiting_director"`, so at `blocked` the **"Znova spustiť release audit" button is hidden** → the Director
> cannot re-run the audit after the v0.7.7 smoke fix. Gap in the v0.7.6 gating: the action should also be
> available when the Auditor is `blocked` on a question (the Director chooses to re-validate instead of
> answering). The `apply_action` handler ALREADY accepts `blocked` (rerun_release_audit is in
> `_ADVANCING_ACTIONS`, whose guard treats `awaiting_director`/`blocked`/`paused` as settled) — only the OFFER
> + the FE condition gate to `awaiting_director`.

---

## CR — extend the offer to `blocked`

### 1. Backend
- `determine_available_actions` (`backend/services/orchestrator.py`), the `elif stage == "gate_g":` branch
  (the v0.7.6 block, ~`:351-355`): currently adds `"rerun_release_audit"` only when `status ==
  "awaiting_director"`. Change the condition to **`status in ("awaiting_director", "blocked")`**. (`verdict`
  stays as-is.) No handler change — `apply_action` already accepts the action from a settled `blocked` state.

### 2. Frontend
- `frontend/src/components/cockpit/PipelineActionBar.tsx`: the "Znova spustiť release audit" button currently
  renders on `current_stage === "gate_g" && awaiting && allowed("rerun_release_audit")`. Make it render
  whenever the backend offers it — i.e. `current_stage === "gate_g" && allowed("rerun_release_audit")` (drop
  the `awaiting`-only sub-gate; `allowed(...)` is now the source of truth and already encodes
  awaiting_director-OR-blocked). Keep the SK label + hint.

### 3. Tests
- `tests/test_orchestrator.py`: extend the offer matrix — `rerun_release_audit` is offered at BOTH
  `gate_g + awaiting_director` AND `gate_g + blocked`; still absent off-`gate_g`, for `fast_fix`, and while
  `agent_working`. Keep the existing `_ADVANCING_ACTIONS` guard test (rejected while agent_working) green.

### 4. Scope / safety
- Two-line behaviour change (offer condition + FE condition) + tests. No handler/dispatch/smoke change.
  `claude_agent.py` untouched. Fast-fix unaffected (gate_g-only).

## Self-verify (Implementer, before DONE)
1. `poetry run pytest` (FULL) — baseline-verify the env-only `test_default_claude_config_dir`.
2. `ruff format --check . && ruff check .`.
3. `cd frontend && npm run build && npm run lint`.
4. New/updated tests: offered at gate_g/blocked AND gate_g/awaiting_director; absent off-gate_g + fast_fix + agent_working.

Report exact outputs. STOP + report any gap (§2.4). Do NOT commit — Dedo commits + verifies.
