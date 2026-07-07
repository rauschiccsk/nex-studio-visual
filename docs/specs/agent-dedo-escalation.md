# Spec: Agent → Dedo escalation (framework_issue) — Director observation #6

**Author:** Dedo · **Date:** 2026-07-07 · **For:** Implementer.
The biggest of the 6 observations: backend + agent prompt + frontend. Director-approved delivery = **A+B** (`.dedo-channel` inbox + Telegram to the Director).

## The gap
When the AI Agent hits an error it CANNOT fix because it needs a change to **NEX Studio itself** (the framework/tooling, §15 "fix NEX Studio, not the project"), today it has no way to say so. Its failures land as `agent_error`/`system_error` → the Manažér is told to "Uprav", but the Manažér objectively cannot fix a NEX Studio bug. There is NO escalation path to Dedo (the meta-developer).

## Required behaviour
1. **Agent-initiated** (per Director): when the Agent recognises "this needs a NEX Studio change", it **prepares a message for Dedo** — a clear description of the problem, what's blocking, and what NEX Studio change seems needed. It does NOT keep retrying or push it onto the Manažér.
2. The version settles **blocked** with a NEW `block_reason = "framework_issue"`.
3. **Manažér view:** a clear status *"NEX Studio potrebuje opravu — Dedo dostal správu, počkaj"* and the escalation message, with **NO recovery actions** (no Uprav/answer/decide — nothing the Manažér can do). `determine_available_actions` returns an EMPTY set for `framework_issue`.
4. **Delivery to Dedo (A+B):**
   - **(A)** a message file into `.dedo-channel/inbox/` (audit trail — the channel Dedo monitors), format per `.dedo-channel/README.md`: `system-to-dedo-YYYY-MM-DD-HHMM-framework-issue-<projectslug>.md` with YAML frontmatter (`from: system`, `to: dedo`, `type: flag`) + the agent's message + context (project, version, the error/agent output excerpt).
   - **(B)** a Telegram ping to the Director via `notify.send_telegram` (reuse the #5-fixed notify — recipient = the project owner / admin chat_id).
5. When Dedo fixes NEX Studio and clears the block, the Manažér resumes (retry). A `framework_issue` is cleared by a Dedo/admin action (or reuse an existing reset path) → back to `awaiting_manazer`.

## Implementation

### Backend
- `backend/db/models/pipeline.py` (BLOCK_REASON_VALUES ~:75): add `"framework_issue"`.
- **Agent signal + parse:** MIRROR the existing `decision_needed`/`agent_question` mechanism (orchestrator.py ~:1208 decision parse, and how `agent_question` is detected). Add a structured escalation the agent can emit (e.g. a `FRAMEWORK_ISSUE:` sentinel block / a parseable section with the Dedo message). In the agent-output settle path, when that escalation is present → `state.status="blocked"`, `state.block_reason="framework_issue"`, capture the agent's message text.
- **Settle + record:** record a `system → manazer` pipeline message (kind notification) with a readable summary + `payload.framework_issue=true`; capture the agent's full Dedo-message (for delivery A). Set `state.next_action` to a "čaká na Deda" hint.
- **Delivery helper** (NEW, e.g. `backend/services/dedo_escalation.py`): (A) write the `.dedo-channel/inbox/` file — resolve the channel dir robustly (SAME class of bug as #5: `/opt/projects/nex-studio/.dedo-channel` is NOT reachable in v3 where /opt/projects→/opt/projects-v3; use an env/mounted path — see control-plane note). (B) `await notify.send_telegram(<short escalation>, owner_chat_id)`.
- `determine_available_actions` (orchestrator.py ~:3486): for `status=="blocked" and block_reason=="framework_issue"` → return EMPTY set.

### Agent prompt
Find where the v3 Agent's system prompt is built (`backend/services/claude_agent.py` / the prompt assembly in orchestrator) and add a §15 escalation instruction: *"If you hit a problem you cannot fix because it requires a change to NEX Studio ITSELF (the tooling/framework, not the customer project), do NOT keep retrying or ask the Manažér to fix it — they can't. Escalate to Dedo: emit a FRAMEWORK_ISSUE with a clear message (the error, the context, what NEX Studio change is needed)."* Match the exact sentinel/format the parse expects.

### Frontend
- `components/cockpit/labels.ts` (BLOCK_REASON_LABELS ~:100): add `framework_issue: "NEX Studio potrebuje opravu (Dedo)"` + red/system tone.
- `HonestStatusStrip.tsx`: framework_issue → red/blocked tone.
- `ConversationComposer.tsx`: when `block_reason==="framework_issue"` → disable the composer + show a banner *"Toto musí opraviť Dedo — už dostal správu. Manažér to nevie cez Uprav. Počkaj na Deda."*
- `ConversationThread.tsx`: render the framework_issue system message with an amber/red accent.

## Control-plane (Dedo handles — note it, don't do it)
The `.dedo-channel` dir must be reachable + writable by the v3 backend (uid 1000). Dedo will mount `/opt/projects/nex-studio/.dedo-channel` into the v3 backend + chown. Make the channel path env-configurable (e.g. `DEDO_CHANNEL_DIR`, default the legacy path) so the mount can point it.

## Tests
Backend: framework_issue settle from a parsed escalation; determine_available_actions empty for it; the delivery helper writes a well-formed channel file + calls send_telegram (mock). Frontend: composer disabled + banner on framework_issue; label/tone. Run backend `pytest` + frontend `npm run test`. Report diff + results. **STOP + ask** if the agent-signal format or the prompt-assembly site is ambiguous — this touches the agent contract, do not invent silently.
