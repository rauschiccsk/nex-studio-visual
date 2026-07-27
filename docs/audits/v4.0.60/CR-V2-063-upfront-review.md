# Upfront review — CR-V2-063 (Metriky → Náklady)

**Pillar 1 of §2.5** — the mandatory review of the Specification and Design *before* implementation.
Subject: `docs/specs/metrics-costs-redesign.md`. Date: 2026-07-27.

**Verdict: PASS — no open findings. Implementation may start.**

## Method

Four independent lenses over the spec against the real codebase, then an adversarial verification
pass over every finding (each verifier was instructed to REFUTE, and to default to *does not hold*
when it could not positively confirm against real code).

| Lens | Question |
|---|---|
| determinizmus | Where would a deterministic Implementer be forced to invent? |
| dopady | What does the rename/delete break that the spec does not mention? |
| správnosť | Is the arithmetic and the data model actually coherent? |
| manažér | Does the screen answer what the Manager asked for? |

74 raw findings → **6 confirmed** after verification (68 refuted, mostly claims that the spec already
answered elsewhere). All 6 were fixed in the spec before hand-off; the list below is the record.

## Findings (all resolved)

| # | Sev | Finding | Resolution in spec |
|---|---|---|---|
| 1 | **blocking** | The existing metrics test suite lives in the ROOT `tests/` package (`tests/test_metrics.py`, 438 lines) and asserts on symbols this CR deletes/renames; `pyproject.toml:51` collects both packages, so the mandated `pytest` gate could not go green. The spec named only `backend/tests/`. | New "Existing coverage — disposition" subsection: rewrite `tests/test_metrics.py` in place, explicit DELETE list, explicit KEEP-and-re-point list (Manažér-wait, phase-stamp, 404 — the only coverage of each), plus the mechanical `r.phase` → `r.key` rename in `backend/tests/test_metrics_phase_stamp.py`. |
| 2 | major | `SystemOverheadRead` folded into `CostRowRead`, which carries `human_*` — undefined for a system row. With the totals rule as written ("any row that carries tokens"), `human_cost_measured` would be permanently `None` on any project with un-phased engine tokens, since no `metrics_hourly_wage_system` key exists. | The `None` rule is now explicitly scoped to `kind="phase"` rows; the system row is declared agent-only (`human_* = None`, no wage entry) while its `agent_cost` still counts in `agent_cost_measured`. |
| 3 | major | The mandated FE gate `npm run test` does not exist — `frontend/package.json` has no `test` script. | Gate changed to `npx vitest run` (+ `type-check`). Recorded for the Director that CI runs neither, so 44 FE test files are executed by nobody — flagged, out of scope for this CR. |
| 4 | minor | `Sidebar.tsx:260-267` is the **UAT** NavItem, not Metriky — the Implementer would have relabelled the wrong entry. | Anchor corrected to `:281`, with an explicit "do not touch `:261-268`". |
| 5 | minor | `test_Sidebar_metrics_link.test.tsx` queries the nav by the literal label `Metriky`; the rename breaks it and the spec did not list it. | Added to the disposition subsection. |
| 6 | minor | `rows` order never specified, although the screen renders "rows in `rows` order". | Backend contract added: phases (canonical order) → external → system last. Test 10 (BE) + test 14 (FE) pin it. |

## Notable refutations

Recorded because they show where the spec was already sufficient and should not be "fixed":

- `share_pct` denominator, `_external_rows` scoping, `CostTotalsRead` token semantics, and the
  `None` rule for `human_minutes_measured` were all challenged as under-specified and all **refuted**
  — the spec pins each in prose plus a test.
- The claim that write access should be `ha_or_above` was refuted against `backend/core/authz.py:41-51`:
  the v4.0.35 model uses `assert_*_access(..., ri_only=False)` for reads *and* ordinary writes.
- The claim that `developer_hourly_rate` is still read was refuted — no production read exists.
