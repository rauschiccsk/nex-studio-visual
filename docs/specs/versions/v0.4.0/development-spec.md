# NEX Studio v0.4.0 — Phase 2: Cockpit polish (E6 / E3 / E2)

> Development spec (waterfall). Built by **Dedo (design) + nex-implementer** — NEX Studio develops
> cross-project, NOT through its own cockpit pipeline.
> Phase 1 (v0.3.0) shipped the cockpit hardening + Coordinator-operator + observability (CR-NS-029..037).
> Phase 2 = the Director's polish features. Each feature is grounded by a discovery sweep before its
> design (every file:line below is a real extension point, verified 2026-06-12).

---

## Phase 2 scope
- **E6 — Telegram presence toggle** (this spec; designed + approved 2026-06-12). The Director can mark
  "away" while the cockpit is open → agent-needs-Director events ping Telegram even with the board open.
- **E3 — Sidebar agent-terminal cleanup + per-role model/effort in Settings** (to be designed).
- **E2 — Backlog feature (JIRA-like deferred-requirements store)** (to be designed).

Sequence: **E6 → E3 → E2** (Director-approved order).

---

## E6 — Telegram presence toggle

### Goal
Today the cockpit pings the Director on Telegram only when **no Director has a live board WebSocket** for
the version (presence = an open board WS). So if the Director keeps the cockpit open but steps away from
the computer, an `awaiting_director`/`blocked` event does NOT ping them. E6 adds an explicit **"away"**
toggle: when away, Director-needed events ping Telegram **even with the board open**.

### Current state (grounded, verified 2026-06-12)
- **Notify gate** — `backend/services/pipeline_runner.py:_maybe_notify` (183-208): sends iff
  `state.status in _NOTIFY_STATUSES = ("awaiting_director","blocked")` (line 36/190) **AND**
  `registry.present_director_ids(version_id)` is empty (line 192-193:
  `if registry.present_director_ids(version_id): return  # a Director is already on the board — no out-of-band nudge`)
  **AND** the project owner has a `telegram_chat_id` (`_owner_chat_id`, 173-180).
- **Send path** — `notify.send_telegram` (`backend/services/notify.py:23`) → subprocess
  `scripts/notify_telegram.sh` (sources the bot token from `/opt/infra/telegram/icc-agents.env` ro;
  **Python never touches the token** — CR-NS-011/012 security pattern). Fire-and-forget, never blocks.
- **Presence** — `backend/services/pipeline_ws.py:PipelineWsRegistry._conns:
  dict[version_id, set[(WebSocket, user_id)]]` (line 30); `present_director_ids(version_id)` (line 63)
  returns the connected user_ids. The board WS endpoint `backend/api/routes/pipeline.py:251-282` registers
  on connect (272), loops on `receive_text()` to detect disconnect (277-278), unregisters on disconnect
  (282). FE holds the persistent WS via `frontend/src/hooks/usePipelineWs.ts`.
- **FE** — sidebar user section `frontend/src/components/layout/Sidebar.tsx:319-337` (name, "Director · Ri",
  logout); Zustand stores with persist (`store/activeContextStore.ts`, `authStore.ts`); role check
  `role === "ri"`.

### Design (approved 2026-06-12)
E6 is an **"away" annotation on the EXISTING WS-connection presence** — NOT a new presence system, and
**NO DB migration** (presence is ephemeral).

- **Backend — registry away flag.** Extend `PipelineWsRegistry` so each connection carries `away: bool`
  (e.g. `_conns: dict[version_id, dict[WebSocket, _Conn(user_id, away)]]`, or a parallel away map). Add
  `set_away(version_id, websocket, away)` and `active_director_ids(version_id)` = user_ids with ≥1
  **non-away** connection. Keep `present_director_ids` (raw presence) for any other consumer.
- **Backend — WS inbound presence message.** The board WS receive loop (`pipeline.py:277-278`, today only
  detecting disconnect) parses inbound `{"type":"presence","away":<bool>}` → `registry.set_away(...)`.
  Malformed / other inbound text is ignored **silently** (matching today's ignored-inbound-frame
  behavior; disconnect detection — the `WebSocketDisconnect` except — UNCHANGED).
- **Backend — gate change (the one-line core).** `_maybe_notify` (pipeline_runner.py:192):
  `present_director_ids` → `active_director_ids`. An away Director no longer suppresses the ping;
  everything else in `_maybe_notify` is UNCHANGED.
- **FE — presence store.** New `usePresenceStore` (Zustand + persist, key `"nex-presence"`):
  `{ isAway: boolean, setIsAway }`. Persists across reload (away survives a refresh — the safe direction);
  **reset to `false` on login** (the authStore login handler sets `isAway=false`) so "away" never silently
  carries into a fresh session.
- **FE — sidebar toggle.** In `Sidebar.tsx` user section, a Director-only (`role==="ri"`) toggle
  "🟢 Pri počítači / 🌙 Preč" bound to `usePresenceStore`. Collapsed sidebar → icon only.
- **FE — WS send.** `usePipelineWs.ts`: on WS open AND whenever `isAway` changes, send
  `{"type":"presence","away":isAway}` so a fresh connection inherits the current state and toggling updates
  live (no reconnect).

### Decision (Director-approved 2026-06-12)
- **Manual revert.** `isAway` defaults `false` ("at computer"). The Director toggles back manually
  ("Pri počítači"). Persists in-session / across-reload; resets on a new login. **NO** auto-clear on board
  interaction (that would risk silencing pings while still away).

### Seams to preserve
- `notify.py` / `notify_telegram.sh` security path UNCHANGED (token never in Python).
- `_NOTIFY_STATUSES` UNCHANGED (away changes WHEN a ping fires, not WHICH events).
- `_owner_chat_id` resolution UNCHANGED.
- WS disconnect detection UNCHANGED (presence-message parsing is additive in the same loop).
- NO DB migration (away is in-memory in the registry + FE-persisted).
- Single-Director scope (no multi-Director "who's away" board indicator — future).

### Acceptance
- Director on the board, `isAway=false`, event `awaiting_director`/`blocked` → **NO** Telegram (unchanged).
- Director on the board, `isAway=true`, same event → **Telegram SENT** (the new behavior).
- Director with no board WS open → Telegram sent (unchanged).
- Toggling away / at-computer takes effect on the **next** event without a reconnect.
- New login → `isAway` resets to `false`.
- Tests: backend registry away + `active_director_ids` + the gate (present-but-away → notify;
  present-not-away → suppress); the WS presence-message handler (sets away; malformed ignored; disconnect
  still detected); FE toggle + store + WS-send + login-reset.

### Build order
1. **Backend:** registry away + `active_director_ids` + WS presence-message handling + the `_maybe_notify`
   gate change + tests.
2. **FE:** `usePresenceStore` + the Sidebar toggle + `usePipelineWs` send + the login-reset + tests.

**End of E6.**

---

## E3 — Sidebar single-terminal cleanup + per-user model/effort config

> Designed + Director-approved 2026-06-12 (3 discovery sweeps). Two parts: (a) trim the sidebar to one
> agent terminal; (b/c) per-USER per-role model/effort config the cockpit applies at dispatch.
> **Out of scope (deferred future epic):** per-user Claude **subscription/auth** — running each Director's
> dispatches on their own Claude MAX (per-user credentials / `.claude` dirs / docker mounts / encrypted
> token storage). E3 is per-user *config* on the existing single shared login. Split into **CR-NS-039 =
> E3(a)** and **CR-NS-040 = E3(b/c)**.

### E3(a) — Sidebar: single Coordinator terminal (CR-NS-039)

**Goal:** the Director's one ad-hoc consult channel is the Coordinator (hub-and-spoke: Director↔Coordinator;
the Coordinator has READ `docs/specs/**`+`schemas/**` [CR-033] → answers all project questions). Remove the
other four terminal links.

**Current state (grounded):** `Sidebar.tsx:273-277` has 5 agent NavItems — Coordinator(/coordinator),
Designer(/designer), Customer(/dialogue), Implementer(/implementer), Auditor(/auditor). Routes
`App.tsx:53-57`. Persistent terminals (CR-NS-004 `PersistentTerminalsLayer`) for
designer/implementer/auditor/coordinator (xterm+WS, kept alive across nav). **AG Customer→/dialogue is a
SEPARATE system** (`DialoguePage`, the gate_e Customer dialogue), NOT a persistent terminal.
`agentTerminalStore` slots + `ROLES`; backend `agent_terminal` `AgentRole`/`_VALID_ROLES`/CHECK
{designer,implementer,auditor,coordinator}. Role gating CR-NS-014 (`/agent-terminal/available-roles`).

**Changes:**
- **FE — remove the 4 NavItems** (Designer, Customer, Implementer, Auditor) from `Sidebar.tsx`; keep
  Coordinator. Remove the orphaned routes (`App.tsx`) for /designer, /implementer, /auditor. Remove the
  `PersistentTerminalsLayer` slots + `agentTerminalStore` slots/`ROLES` for designer/implementer/auditor →
  `ROLES = ["coordinator"]`. Remove the now-unused icon helpers.
- **AG Customer / /dialogue — remove the SIDEBAR LINK ONLY; KEEP the /dialogue route + `DialoguePage`.**
  VERIFIED 2026-06-12: gate_e is entirely cockpit-based (CockpitPage→ExchangePanel, `ExchangePanel.tsx:97-119`);
  /dialogue is a STANDALONE dialogue feature reachable ONLY via this sidebar link, NOT used by the cockpit
  gate_e. So dropping the link declutters the sidebar without touching gate_e. The standalone /dialogue is
  superseded by in-cockpit gate_e, but **retiring the feature entirely is a SEPARATE decision, not this
  sidebar-declutter CR** — keep the route + `DialoguePage`.
- **Backend — service-level trim:** narrow `agent_terminal` `AgentRole` literal + `_VALID_ROLES` to
  `{"coordinator"}` so the spawn API rejects the other roles; `/agent-terminal/available-roles` narrows
  accordingly. **KEEP the DB CHECK constraint permissive (NO migration)** — once the API + FE no longer
  offer the old roles, the permissive constraint is harmless, and a constraint-narrowing migration over
  existing `agent_terminal_sessions` rows is not worth the risk.
- The `PersistentTerminalsLayer` (built for MULTIPLE terminals) now hosts one — keep minimal or simplify
  (Implementer's call), no behavior regression.
- **`AgentRole` is OVERLOADED — decouple (Option A; re-verified 2026-06-12, the earlier cascade was
  INCOMPLETE — it missed the debug-attach seam).** The FE `AgentRole` (`agentTerminal.ts:15`) types BOTH
  spawn-terminal roles AND the CR-NS-018 §10 **debug-attach** targets (`DebugTerminalDrawer.tsx` +
  `pipeline.ts`), which MUST keep all 4 (you debug-attach to a failed Implementer/Auditor session). Full
  cascade (tsc+lint+tests green):
  - **Decouple debug-attach:** add `DebugAttachRole = "coordinator"|"designer"|"implementer"|"auditor"` in
    `pipeline.ts`; retype `DebugTerminalSession.role`, `openDebugTerminalApi(role)`, and
    `DebugTerminalDrawer` (`TERMINAL_ROLES`, `asTerminalRole(...)→DebugAttachRole`, the `role`
    state/`attach`/`changeRole`) to it. (`PipelineActor` is unsuitable — it has customer/director.)
  - **Narrow the SPAWN type (FE):** `AgentRole` → `Literal["coordinator"]` in `agentTerminal.ts:15`;
    `SpawnRequest.role`/`AvailableRoles` auto-narrow to coordinator-only — CONSISTENT (the other NavItems are
    GONE, so gating/available-roles is coordinator-only). **BE: the spawn path narrows to coordinator too,
    BUT the BE carries the SYMMETRIC debug-attach overload — see the BE decouple below (my earlier "BE
    narrowing is safe" claim was WRONG).**
  - Remove the now-unused `Sidebar.tsx` `agentDisabled()`/`agentTitle()` + trim/remove `AG_ROLE_LABEL`;
    `PersistentTerminalsLayer` `matchActiveRole()`/`entries` + `agentTerminalStore.ROLES` → coordinator-only;
    `AgentTerminalPage` `Record<AgentRole>`/role-prop auto-narrow.
  - **Update the obsolete test** `test_Sidebar_agent_gating.test.tsx` (it mocks 4-role gating — now
    coordinator-only).
  - **BE debug-attach decouple (re-verified 2026-06-12 — the BE has the SYMMETRIC overload; the prior "BE
    safe" claim was WRONG; a completeness sweep found 4 affected sites — the single failing test
    `test_debug_terminal_resumes_orchestrator_session` covered only 2):**
    - **Spawn = coordinator-only:** `SpawnRequest.role` = `Literal["coordinator"]`; `_VALID_ROLES` =
      `{"coordinator"}`; the spawn-API path validates against it; `available_roles` stays coordinator-only.
    - **Debug-attach = 4 roles:** add `_DEBUG_ATTACH_ROLES = {coordinator,designer,implementer,auditor}` +
      a `_validate_debug_attach_role`; the debug-terminal endpoint (`pipeline.py:206`) validates against IT
      (not `_VALID_ROLES`); **move the coordinator-only gate OUT of `_resolve_agent_spec`** to the spawn-API
      entry, so debug-attach + auto-resume can use 4 roles.
    - **Reads serialize 4 roles (the key fix a 2-schema split would miss):** the session-row READ schema
      `AgentTerminalSessionRead.role` must accept the 4 roles (e.g. `DebugAttachRole`/`str`) — the SAME table
      holds debug-attach (non-coordinator) rows, so this fixes BOTH `list_sessions` (`agent_terminal.py:130`,
      `response_model=list[AgentTerminalSessionRead]`) AND the debug-terminal response in ONE schema. The
      coordinator-only constraint lives on `SpawnRequest` + spawn validation, NOT the read schema.
    - **Auto-resume:** `_respawn_for_resume` (`agent_terminal.py:~447/514`, WS reconnect) must resume a
      non-coordinator debug-attach session — with the gate moved out of `_resolve_agent_spec`, it works.
    - DB CHECK constraint stays permissive (no migration). Blast radius confirmed bounded to debug-attach +
      auto-resume; the spawn API stays coordinator-only.

**Seams to preserve:** the orchestrator pipeline still dispatches ALL roles
(coordinator/designer/implementer/auditor/customer) — E3(a) removes only the interactive SIDEBAR terminals,
NOT the pipeline agents; gate_e / `DialoguePage` must not break (verify); the debug-attach (CR-NS-018 §10)
to the orchestrator session is unaffected.

**Acceptance:** sidebar shows one agent terminal (Coordinator) + the non-agent nav; /coordinator works; the
removed links/routes are gone; the spawn API rejects non-coordinator roles; **gate_e still works**;
`npm run build` + `npm run lint` + backend tests green.

**Plus (carried E6 hardening nits, fold into CR-NS-039):** (1) a code-comment on
`pipeline_ws.py` `present_director_ids`/`active_director_ids` documenting the deliberate sync/lock-free
read invariant (if ever made async, the dict-iteration race appears); (2) a `test_pipeline_ws` case for the
same-user-multiple-sockets `active_director_ids` (active iff ≥1 non-away connection).

### E3(b/c) — Per-user per-role model/effort config (CR-NS-040)

**Goal:** move model/effort out of "hidden in `.claude/agents/<role>/settings.json` + interactive `/model`
`/effort`" into an explicit **per-USER** Settings UI the cockpit applies at dispatch. Per-user because each
team member will run on their own Claude MAX (the *auth* for that = the deferred future epic; this is the
*config*).

**Current state (grounded):** orchestrator `invoke_claude` (`claude_agent.py`) does NOT pass
`--model`/`--effort`; model resolved by the CLI from `.claude/agents/<role>/settings.json`
("claude-opus-4-8"); no effort in dispatch. `claude --model` + `claude --effort` flags BOTH exist (verified
via `claude --help`). ONE shared login (`CLAUDE_CONFIG_DIR=/home/andros/.claude`); orchestrator session
keyed (project_slug, role), shared. User model `foundation.py` (id, username, role ri/ha/shu, …); only
`admin` seeded; NO per-user settings store (only global `system_settings`). The triggering Director's
user_id is at the route (`current_user`) but **NOT threaded** into dispatch; the project owner IS
resolvable (version→project→owner_id, as in `_owner_chat_id`, `pipeline_runner.py:173-180`).

**Changes:**
- **New table `user_agent_settings`** (`foundation.py` + migration): `(user_id FK→users ON DELETE CASCADE,
  agent_role, model, effort)`, unique `(user_id, agent_role)`. `agent_role` ∈
  {coordinator, designer, implementer, auditor, customer} — the **PIPELINE agent role**, NOT the user's
  ri/ha/shu. CHECK on agent_role + effort.
- **Config API (per-user — each edits their OWN):** `GET /api/v1/user-agent-settings` (the caller's rows) +
  `PUT /api/v1/user-agent-settings/{agent_role}` (upsert model+effort), scoped to the authenticated user.
- **Settings UI — new "Agenti" tab** (`SettingsPage.tsx`): for each of the 5 pipeline agent-roles, a
  **Model** dropdown (Opus 4.8 `claude-opus-4-8` / Sonnet 4.6 `claude-sonnet-4-6` / Haiku 4.5
  `claude-haiku-4-5-20251001`) + an **Effort** dropdown (low / medium / high / xhigh / max / ultracode).
  Shows + edits the CURRENT user's config; draft/save pattern like the System tab.
- **Dispatch threading + application:** at `invoke_agent` (`orchestrator.py:784`), resolve the **project
  owner** (version→project→owner_id; reuse the `_owner_chat_id` join) → look up
  `user_agent_settings(owner_id, role)` → pass `model` + `effort` to `invoke_claude`, which appends
  `--model <m>` + `--effort <e>` to the `claude -p` args. **Fallback:** owner's config → (unset) → today's
  default (no flags → CLI uses `.claude/agents/<role>/settings.json`). **Unset = exactly today's behavior
  (no regression).** Attribution = **project owner** (stable, reuses existing resolution, aligns with the
  future per-user subscription); threading the *triggering* user is unnecessary.
- **Effort enum:** low/medium/high/xhigh/max/ultracode — confirm these are exactly what `claude --effort`
  accepts (Implementer checks `claude --effort`); if the CLI's accepted set differs, STOP+ask.

**Out of scope (future epic — NOT this CR):** per-user Claude **subscription/auth** (per-user credentials /
`.claude` config dirs / docker mounts / encrypted token storage). E3 is config-only on the shared login.

**Seams to preserve:** orchestrator session keying (project_slug, role) UNCHANGED (model/effort vary per
dispatch turn; the conversation thread stays shared); unset config = today's exact behavior; the single
shared login UNCHANGED; no credentials handled (the config stores model names + effort levels only, never
secrets).

**Acceptance:** a user sets e.g. Designer=Sonnet/high in "Agenti" → a build of THEIR project dispatches the
Designer with `--model claude-sonnet-4-6 --effort high`; unset roles dispatch exactly as today; another
user's config is independent; the API rejects editing another user's config; WS-D metrics still record the
(now-varied) model. Tests: table + API (upsert, per-user isolation, reject-other-user), dispatch resolution
(owner config → flags; unset → no flags), the Settings tab.

**Build order (CR-NS-040, after E3(a)):** table+migration → config API → dispatch threading +
`invoke_claude` flags → Settings "Agenti" tab → tests.

**End of E3.**
