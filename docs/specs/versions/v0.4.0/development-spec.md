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
