# Metrics v3 — three honest phases (Návrh / Programovanie / Verifikácia)

Director decision 2026-07-07 (andros/nex-payables crash-test). The v3 conversational
pipeline collapsed the old 5-agent roles into one partner, but the per-phase cost metric
still assumes the legacy 4-phase model — so v3 projects show real work only under
`priprava`+`programovanie`, leaving `navrh` and `verifikacia` permanently empty. Two of
those "phases" DO happen in v3, they are just mis-stamped. Fix = stamp v3 work into the
three phases that reflect reality, and stop rendering phantom empty rows.

Target phases for v3 (map to EXISTING keys — no label changes needed):
- **navrh** ("Návrh")         = alignment + specification + DB schema + task-plan (everything pre-build)
- **programovanie** ("Programovanie") = the build (unchanged)
- **verifikacia** ("Verifikácia")     = the agent checking its own work + release smoke

Branch: `v2.0.0-dev`. Self-verify BOTH domains (BE: `ruff format --check` + `ruff check` +
FULL `pytest`; FE: build + lint + test). Two parts, one shared goal.

Diagnosis is DONE (live DB + code read via two Explore agents). Key facts you can rely on:
- Token-bearing agent turns go through `invoke_agent` (orchestrator.py ~2506) and are
  persisted with `stage = current_stage` and **`payload.phase` NOT set (null)**.
- Metrics attribute each usage-bearing message by `payload.phase` **if it is a string,
  ELSE `msg.stage`** (`backend/services/pipeline_metrics.py:161`, `aggregate_usage_by_phase`).
- So today v3 tokens attribute purely by `msg.stage` → all pre-build + self-check land on
  `priprava`, build on `programovanie`.
- `current_stage` / `msg.stage` drive pipeline control flow and the deploy/release gate.
  The self-check (kontrola) round is DELIBERATELY kept at `stage='priprava'` so it stays
  invisible to the deploy path. **Do NOT change `msg.stage` or `current_stage` anywhere.**

---

## Part 1 — Pipeline: stamp `payload.phase` per round (backend, orchestrator.py)

Goal: on the v3 **conversation flow** (`mode='conversation'`) ONLY, set `payload['phase']`
on the agent's usage-bearing LLM turns to the metrics phase for the round that produced them.
Leave `stage` / `current_stage` untouched. Legacy automaton (`mode` NULL) must be UNAFFECTED.

Round → phase mapping:
| Round / code path | phase to stamp |
|---|---|
| pre-build conversation turns — `run_conversation_turn` (4373) / `_conversation_directive` (4302) | `navrh` |
| task-plan round — `_run_conversation_plan_round` (4520) | `navrh` |
| self-check / kontrola round — `_run_conversation_kontrola_round` (4559), incl. the smoke via `_run_release_smoke` (4040) run inside it | `verifikacia` |
| build round — `_run_build_round` (6698) under `stage='programovanie'` | `programovanie` (already correct via stage fallback; stamp explicitly for robustness) |

Suggested mechanism (you decide the cleanest wiring):
- Add an optional param to `invoke_agent` (e.g. `metrics_phase: str | None = None`,
  orchestrator.py ~2506). When provided, write it into the persisted message payload as
  `payload['phase']`. Default `None` → current behaviour (fall back to `stage`), so every
  legacy caller is unchanged.
- Each conversation-flow round passes its phase from the table above.
- The value MUST be one of `STAGE_VALUES` (`navrh` / `programovanie` / `verifikacia`) so it
  passes the metrics phase set and existing labels.

Guardrails:
- Only the conversation flow sets `metrics_phase`. Confirm the legacy `run_dispatch` path
  still produces its historical stamping (its own `_next_stage` walk is authoritative there).
- No change to `msg.stage`, `current_stage`, predicates (`spec_approved`, `kontrola_done`,
  `programming_complete`, …), or the deploy/release gate. This is a metrics-only stamp.

## Part 2 — Metrics: data-driven phase list, no phantom empty rows (backend, metrics.py)

Today `_build_phases` (metrics.py 247–254) force-emits all 4 `COMPARISON_PHASES`, so a
zero-token phase renders an empty row. Change it to emit **only phases that actually did
work** (input_tokens + output_tokens > 0), preserving `COMPARISON_PHASES` canonical order.

- Apply the same filter to BOTH the per-version `by_phase` (in the version loop) and the
  cumulative `by_phase` (compute_project_metrics 454–464).
- Keep `COMPARISON_PHASES` itself unchanged. Do NOT touch `_overhead_totals` (257–265),
  `_config_flags` (300–317) or `_cost_totals` (278–297) — they must still iterate the full
  set. Footing is preserved because dropped phases contributed 0 tokens.
- Result: v3 project → 3 rows (Návrh / Programovanie / Verifikácia); legacy v1/v2 project →
  whatever phases it really used; never a permanent empty row.
- Frontend needs NO change (MetricsPage maps over the backend array; labels via
  PHASE_LABELS already cover all keys). Still run the FE build/lint/test to confirm the
  metrics page renders an arbitrary-length phase list with no hardcoded assumption of 4.

---

## Tests (mandatory, RED→GREEN where the bug is reproducible)

- **Pipeline (Part 1):** a test that a conversation-flow self-check turn persists
  `payload['phase'] == 'verifikacia'` while `msg.stage == 'priprava'` (control flow
  unchanged); a pre-build conversation turn persists `payload['phase'] == 'navrh'`; a build
  turn attributes to `programovanie`. If full-flow simulation is too heavy, unit-test the
  `invoke_agent` record path with `metrics_phase` set/unset and assert the payload + stage.
  Also assert the legacy path (no `metrics_phase`) is byte-for-byte unchanged.
- **Metrics (Part 2):** given a `by_phase` with only `navrh`/`programovanie`/`verifikacia`
  non-zero, `_build_phases` returns exactly those three in canonical order (no `priprava`
  row); and the sum of phase tokens still equals the grand total (footing).
- Full `pytest` (orchestrator + metrics are shared spine — run everything, not one file).

## Out of scope (Dedo handles directly)

- Seeding the two missing settings (`metrics_hourly_wage_navrh`,
  `metrics_minutes_per_mtok_verifikacia`) — ALREADY DONE in prod.
- Retroactive re-stamp of nex-payables' existing messages — Dedo does this with a targeted
  SQL migration after your change is verified.
