# Build robustness — persist agent logs + auto-retry on crash + honest timeout-vs-crash message

Follow-on to `cockpit-timeout-and-activity-fix.md` (Bug 1/2). Director-approved 2026-07-10 after the
nex-payables v1.1.0 build "timed out" at ~8 min — which is FAR below the 40-min programovanie budget, so
it was almost certainly a CRASH / envelope-loss (the claude subprocess failed / lost its result), NOT a
real timeout. The engine reports both with the same "Vypršal čas agenta" message, and no agent log was
persisted (the terminal-logs volume is empty), so the crash cause is undiagnosable. Fix all three. Branch
`v2.0.0-dev`. Build ON TOP of the Bug 1/2 changes (same timeout/envelope-loss area — do this AFTER them to
avoid a conflict). Self-verify: FULL `.venv/bin/python -m pytest -q` from root + ruff; FE unaffected.

Grounding: `invoke_agent`/`invoke_claude` (orchestrator.py ~2561/claude_agent.py) run the agent turn.
`ClaudeAgentTimeout` (real 40-min wall) and `ClaudeAgentError` (crash / non-zero exit / decode/connection
failure — the "envelope loss") are DISTINCT exceptions, caught separately (timeout ~3019, error ~3044).
Both currently ride the same lost-work / "review & continue" path (~2697-2719) with the
`_PLAN_PASS_ENVELOPE_LOSS_PREFIX = "claude invocation failed:"` message. `STAGE_TIMEOUT[programovanie]=2400`.

## Fix 1 — persist the agent turn's log (so a crash/timeout is diagnosable)
Today the claude subprocess stdout/stderr + streaming events are not persisted anywhere durable (the
`v3prod_terminal_logs` volume is empty), so a crash leaves no trace. Persist each agent turn's output +
error to a durable per-turn log file (under the terminal-logs volume / a known dir), so the next crash is
diagnosable.
- In `invoke_claude` (claude_agent.py), on BOTH the error/timeout path AND normal completion, write the
  subprocess stderr (and a bounded tail of stdout / the last streaming events) to a per-turn log file
  (e.g. `terminal-logs/<version_id>/<stage>-<ts-from-caller>.log`). NO timestamp via Date.now in a place
  that breaks resume — the caller can pass a label; keep it simple (a monotonic counter / the session id).
- **§4: redact** — never write credentials/OAuth tokens to the log (the claude CLI shouldn't emit them,
  but scrub any `Authorization`/`token`/`Bearer`/`sk-`/OAuth patterns defensively before writing).
- On a crash/timeout, the honest message (Fix 3) should reference where the log is, so the operator/Dedo
  can read it.

## Fix 2 — auto-retry ONCE on a CRASH (ClaudeAgentError), NOT on a real timeout
Currently the envelope-loss path is conservative — it does NOT re-invoke (correct for a REAL timeout: a
re-run just risks another 40-min wall). But a CRASH / connection-loss (`ClaudeAgentError`, e.g. an
overloaded/529 or a dropped connection at ~8 min) is usually TRANSIENT and should be retried automatically
once, not left for a manual resume.
- Distinguish: `ClaudeAgentTimeout` (hit the budget) → keep the current conservative behaviour (settle to
  awaiting_manazer, manager resumes; NO auto-retry). `ClaudeAgentError` (crash/connection/decode — envelope
  loss NOT from exhausting the budget) → **auto-retry the agent turn ONCE** before settling to
  awaiting_manazer. The build round is resume-safe (reclaims an orphaned `in_progress` task), so a retry
  re-runs from the next todo task cleanly.
- Bound it: retry ONCE per dispatch (a second consecutive crash → settle to awaiting_manazer + honest
  message + the persisted log, so a persistent problem still surfaces, not an infinite retry loop).
- Keep it inside the existing dispatch/round machinery (don't spin a new loop). Reuse the transient-retry
  spirit of `invoke_claude`'s existing bounded transient-error retry (CR-NS-018) if that already covers
  connection blips — verify whether the ~8-min crash was already past those retries (it reached the
  build-round handler, so it was); the NEW retry is at the build-round/turn level.

## Fix 3 — honest message: timeout vs crash (not the misleading "Vypršal čas agenta" for both)
The user-facing notification must state the ACTUAL reason:
- Real `ClaudeAgentTimeout` → e.g. "Agent vyčerpal časový limit (Xmin) — hotové zmeny sú zapísané, môžeš
  pokračovať v stavbe."
- `ClaudeAgentError` / crash / connection-loss (after the auto-retry also failed) → e.g. "Agent stratil
  spojenie / spadol (nie časový limit) — skúsil som to raz znova, opäť zlyhalo. Hotové zmeny sú zapísané,
  môžeš pokračovať. (log: <path>)".
- Route the correct message from the exception TYPE (timeout vs error), not a single shared string. Keep
  it plain-language for the manager.

## Tests (RED→GREEN)
- Fix 2: a `ClaudeAgentError` in the build round triggers exactly ONE auto-retry, then (if it fails again)
  settles to awaiting_manazer; a `ClaudeAgentTimeout` does NOT auto-retry (settles immediately). Mock the
  agent invocation to raise each.
- Fix 3: the settled notification text differs for timeout vs crash (assert the two distinct messages).
- Fix 1: a per-turn log file is written on completion AND on crash/timeout, and it contains NO credential
  patterns (assert redaction on a stderr fixture that includes a fake token).
- Full `pytest` from root + ruff.
