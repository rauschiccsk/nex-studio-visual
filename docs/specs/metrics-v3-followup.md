# Follow-up — corrections from adversarial verification of metrics-v3-three-phases

Two independent adversarial verifiers checked the batch. The SUCCESS-path stamping,
control-flow safety, and legacy-untouched are all CONFIRMED correct — leave them. Two real
findings remain, both minimal, both with exact anchors. Branch `v2.0.0-dev`. Self-verify BOTH
domains (BE: ruff format --check + ruff check + FULL pytest; FE: build+lint+test).

Key fact both fixes rely on: `aggregate_usage_by_phase` (`pipeline_metrics.py:157`) counts a
message when it carries `usage` **OR** `timing` (not both). `_failure_metrics_payload` ALWAYS
supplies `timing`, so failed-turn messages ALWAYS count toward per-phase metrics.

---

## Correction 1 — failure-path stamping LEAK (backend, orchestrator.py)

`metrics_phase` was threaded only through the SUCCESS helpers. THREE failure-escalation routes
in the v3 conversation flow still stamp `'priprava'` on usage/timing-bearing messages, via two
SHARED failure helpers that were not modified (each currently does `payload["phase"] = stage`):

- `_record_parse_exhaustion` (~orchestrator.py:835) — called at:
  - `~4471` inside `run_conversation_turn` → should be **navrh** (currently leaks to priprava)
  - `~4718` inside `_run_conversation_kontrola_round` → should be **verifikacia** (leaks to priprava)
- `_settle_plan_pass_failure` (~orchestrator.py:4775) — called at `~4891` and `~4943` inside
  `_generate_incremental_plan` (which already has `metrics_phase="navrh"` in scope) → should be
  **navrh** (currently leaks to priprava; the call passes only `stage`, not the in-scope `metrics_phase`).

Fix (same decoupling the batch already uses): add `metrics_phase: Optional[str] = None` to BOTH
helpers; set `phase = metrics_phase if metrics_phase is not None else stage` for the payload phase
stamp ONLY. Then:
- pass `metrics_phase="navrh"` at the `~4471` call,
- pass `metrics_phase="verifikacia"` at the `~4718` call,
- pass `metrics_phase=metrics_phase` at the `~4891` and `~4943` calls.
- Legacy callers of `_record_parse_exhaustion` (~`4271`, ~`5225`) pass nothing → `phase == stage`,
  byte-identical.

Guardrail: `msg.stage` / `current_stage` UNCHANGED (deploy gate + `_latest_navrh_gate_report_payload`
key on `msg.stage`, verified). This is a metrics-only stamp, exactly like the batch.

Why it matters: without this, a parse-exhaustion / envelope-loss / plan-pass failure in a v3 build
renders a phantom **"Príprava"** row, contradicting the Návrh/Programovanie/Verifikácia promise.

## Correction 2 — data-driven drop predicate ignores time (backend, metrics.py)

`_build_phases` (~metrics.py:262) drops a phase on `(input_tokens + output_tokens) == 0`. A
comparison phase with 0 tokens but **non-zero `duration_seconds`** (a failed turn whose envelope
carried no usage but real wall-clock) is then dropped, which (a) inflates the headline
`x_faster` (its `active_seconds` vanish from the denominator — a one-sided bias flattering the
agent) and (b) shows its duration nowhere (`_overhead_totals` excludes COMPARISON_PHASES → time
footing breaks).

Fix: drop a phase only when it has NO metered activity at all:
```python
if t is None or not (t.input_tokens or t.output_tokens or t.duration_seconds or t.parse_attempts):
    continue
```
Still removes genuine phantom rows; preserves BOTH token and time footing. (Interacts with
Correction 1: after C1, a usage=None failed turn lands in the right phase with 0 tokens + real
time — C2 keeps that phase visible instead of silently dropping it.)

---

## Tests (mandatory, RED→GREEN)

- **Stamping (C1):** a parse-exhaustion turn recorded in a conversation `run_conversation_turn`
  round stamps `payload['phase']=='navrh'` (NOT 'priprava') while `msg.stage=='priprava'`; in a
  kontrola round stamps `'verifikacia'`; a plan-pass failure under
  `_generate_incremental_plan(metrics_phase='navrh')` stamps `'navrh'`. Assert legacy callers
  (no `metrics_phase`) still stamp `stage`. Confirm RED on current code, GREEN after.
- **Filter (C2):** `_build_phases` KEEPS a phase with 0 tokens but `duration_seconds>0` (and
  separately one with `parse_attempts>0`); still DROPS a fully-empty phase; assert both token and
  duration footing vs the grand total.
- Full `pytest` (shared spine); FE build+lint+test.
