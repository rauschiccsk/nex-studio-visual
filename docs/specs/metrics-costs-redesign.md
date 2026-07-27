# CR-V2-063 — Metriky → **Náklady**: cost per phase / version / project + manually entered external cost

Branch `v2.0.0-dev`. Self-verify BOTH domains:

* **BE:** `poetry run ruff format --check .` + `poetry run ruff check .` + `poetry run pytest`
  (from the repo root — `pyproject.toml:51` sets `testpaths = ["tests", "backend/tests"]`, so BOTH
  packages are collected; see the Tests section for what that means here).
* **FE:** `npm run build` + `npm run lint` + `npm run type-check` + **`npx vitest run`**.
  There is **no `test` script** in `frontend/package.json` — `vitest` is installed but has no npm
  alias, so invoke it directly. (Noted for the Director, out of scope here: CI runs neither
  `vitest` nor `type-check`, so all 44 existing FE test files are currently executed by nobody.)

Run `npm run codegen` after the schema changes. Note the page does **not** read the generated
contract: `MetricsPage.tsx` imports from the hand-written `frontend/src/types/metrics.ts`, which must
be edited by hand to match the new schema.

## Why

The page today answers *"are we better than a human?"* — four ROI headline cards (`N× rýchlejšie`,
`M× lacnejšie`, `Ušetrené €` ×2). The Manager does not need that question answered. He needs
**what it cost**: token cost per phase, per version, and per project, plus the same figures
converted to human work with his coefficient.

Two facts from the pre-spec analysis drive the design:

1. **The human side is not measured.** It is `tokens × minutes-per-Mtok × wage`, all three
   hand-entered. Today that coefficient exists **five times** (once per phase); the two live
   cockpits drifted to `50–70` and `600`, producing opposite verdicts from the same engine. The
   Director has calibrated **600** from a real project — it becomes a single setting.
2. **The cockpit only meters its own builds.** Work done outside it (Dedo in the terminal, a
   developer working directly) contributes zero to a project's cost today. Hence a manual
   **external cost** entry.

Honesty rule that governs every part below: **measured and hand-entered figures are summed but
never merged.** Every total carries its `z toho ručne zadané` split. A missing input stays `None`
and renders `—`; nothing is ever fabricated as `0`.

---

## Part 1 — Settings registry (`backend/services/system_setting.py`)

**Remove** these keys from `DEFAULT_SETTINGS` (and delete their rows in the data migration, Part 2):

- `metrics_minutes_per_mtok_priprava`, `_navrh`, `_vizual`, `_programovanie`, `_verifikacia` (5 keys,
  anchor: the block starting at `:301`)
- `developer_hourly_rate` — dead: no code reads it, yet its description still claims it drives the
  metrics page. Verified by repo-wide grep.

**Add:**

```python
"metrics_minutes_per_mtok": _Default(
    value="600",
    value_type="float",
    label="Ľudský čas — koeficient",
    unit="min / mil. tokenov",
    description=(
        "Koľko minút ľudskej práce zodpovedá 1 miliónu tokenov. Platí rovnako pre všetky fázy aj "
        "pre externé náklady. 0 = nenastavené → ľudské stĺpce sa nezobrazia."
    ),
),
"metrics_hourly_wage_externe": _Default(
    value="0.0",
    value_type="float",
    label="Hodinová sadzba — Externé náklady",
    unit="€ / hod",
    description="Sadzba pre ručne zadané externé náklady. 0 = nenastavené → nezobrazí sa.",
),
```

**Keep** the five `metrics_hourly_wage_{phase}` keys unchanged — wages genuinely differ per phase.

**Currency (Part 6 of the analysis).** Every `api_price_*` key (`api_price_input_per_mtok`,
`api_price_output_per_mtok`, and the six per-family variants at `:382-425`) currently declares
`unit="$ / mil. tokenov"` while wages declare `unit="€ / hod"`, and the result is rendered `€`.
Change the price `unit` to `"€ / mil. tokenov"` and append to each description:
`"Zadaj v eurách — všetky sumy na obrazovke Náklady sú v eurách."`

**Do NOT convert stored values** — a silent × rate would be worse than the current mismatch. The
Director re-enters them.

Also fix the stale `"4 phases"` prose the analysis found in the registry (`:283`, `:285`) — with the
collapse there is now **one** coefficient and **six** wages (5 phases + externe).

---

## Part 2 — External cost: model + migration

New file `backend/db/models/external_cost.py`, exported from `backend/db/models/__init__.py`:

```python
class ExternalCost(Base):
    """Manually entered token spend the cockpit cannot meter (work done outside a build:
    Dedo in the terminal, a developer working directly). Priced with the SAME per-model prices
    and the SAME human coefficient as measured work, but always reported as a separate row and a
    separate `…_external` total — entered figures are never merged into measured ones."""

    __tablename__ = "external_cost"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: NULL = a project-level entry (counts in the project total, in no version).
    version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("versions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    #: full model id (e.g. "claude-opus-5") — priced through the SAME `_model_family` chain.
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at / updated_at  # follow the convention in backend/db/models/backlog.py

    __table_args__ = (
        CheckConstraint("input_tokens >= 0 AND output_tokens >= 0", name="ck_external_cost_tokens_nonneg"),
    )
```

**Migration `migrations/versions/086_external_cost_and_metrics_settings.py`** (`down_revision = "085"`):

1. `create_table("external_cost", …)` + the two indexes + the check constraint.
2. Data: `INSERT` `metrics_minutes_per_mtok` = `'600'` and `metrics_hourly_wage_externe` = `'0.0'`
   (skip if present).
3. Data: `DELETE FROM system_settings WHERE key IN (the 5 old per-phase coefficient keys,
   'developer_hourly_rate')`.
4. `downgrade()` reverses all of it (drop table; re-insert the 6 deleted keys with value `'0.0'`;
   delete the 2 added keys).

`version_id` belonging to `project_id` is **not** a DB constraint — validate in the service and
return 422 (Part 4).

---

## Part 3 — Computation (`backend/services/metrics.py`, `backend/schemas/metrics.py`)

The module keeps its data source (`pipeline_metrics.aggregate_usage_by_phase`) and its per-model
pricing chain (`_model_family` / `_resolve_price` / `_agent_cost_split`, `:82-131`) **unchanged** —
that half is measured and correct. What goes is the ROI half.

### Schemas — replace the ROI shape

**Delete** `RoiHeadlineRead` and the `x_faster` / `m_cheaper` / `eur_saved` fields from
`PhaseMetricRead`. **Rename** `PhaseMetricRead` → `CostRowRead` and reshape:

```python
class CostRowRead(BaseModel):
    """One cost row within a scope. `kind` keeps measured and entered figures distinguishable all
    the way to the screen — a renderer must never present them as the same class of number."""

    key: str                       # phase key, or "externe", or "system"
    kind: Literal["phase", "external", "system"]
    turns: int                     # metered messages (UsageTotals.messages) — NOT parse_attempts
    input_tokens: int
    output_tokens: int
    #: this row's tokens ÷ the scope's total tokens × 100. Always computable (never None), which is
    #: why it is token share and not cost share — cost is None whenever a model is unpriced.
    share_pct: float
    agent_cost: Optional[float]
    unpriced_model_keys: list[str]
    human_minutes: Optional[float]
    human_cost: Optional[float]
    active_seconds: float          # kept: real measured compute time (0.0 for external rows)


class CostTotalsRead(BaseModel):
    """Scope totals with the mandatory measured/entered split."""

    turns: int
    input_tokens: int
    output_tokens: int
    agent_cost_measured: Optional[float]
    agent_cost_external: Optional[float]
    agent_cost_total: Optional[float]
    human_minutes_measured: Optional[float]
    human_minutes_external: Optional[float]
    human_minutes_total: Optional[float]
    human_cost_measured: Optional[float]
    human_cost_external: Optional[float]
    human_cost_total: Optional[float]
```

**`None` propagation — scoped to `kind="phase"` rows.** A `…_measured` figure is `None` iff some
`kind="phase"` row that carries tokens has that figure unconfigured (today's rule at `_cost_totals`,
`:297-316`, which iterates the comparison phases only — keep that scope). A `…_total` is `None` when
either half is `None`. A scope with no external entries has `agent_cost_external = 0.0` (a real zero
— nothing was entered), not `None`.

**The `kind="system"` row is agent-only.** It always carries `human_minutes = None` and
`human_cost = None`, and `wages` has no `"system"` entry — un-phased engine tokens have no human
equivalent by definition, and no `metrics_hourly_wage_system` key exists or will be added. Its
`agent_cost` **does** contribute to `agent_cost_measured` (it is metered spend); if the system row
itself is unpriced, `agent_cost_measured` is `None`. It must NOT drag the human totals to `None` —
that is why the rule above is scoped to phase rows.

**`rows` order** (the screen renders `rows` in payload order, so this is a backend contract):
phase rows first in canonical `COMPARISON_PHASES` order, then the `kind="external"` row, then the
`kind="system"` row last — it foots the table. A row absent from a scope is simply not emitted.

`SystemOverheadRead` is folded into `CostRowRead` with `kind="system"` (drop the separate schema).
`ManagerOverheadRead`, `UsageTotalsRead`, `ModelTokensRead` stay as they are.

`VersionMetricsRead` → `VersionCostsRead`: `rows: list[CostRowRead]` + `totals: CostTotalsRead`,
keeping `manager`, `manager_wait_seconds`, `internal_idle_seconds`, `total_time_seconds`; drop `roi`.

`ProjectMetricsRead` → `ProjectCostsRead`: `rows`, `totals`, `by_version`, `manager`, plus the
**assumption block** the screen must display:

```python
    coefficient_minutes_per_mtok: Optional[float]   # the single new setting (None when 0/unset)
    wages: dict[str, Optional[float]]               # row key -> hourly wage, None when unset
    currency: str = "EUR"
    pricing_configured: bool
    coefficient_configured: bool
    wages_configured: bool
```

### Service changes

- `COMPARISON_PHASES` (`:61`) and `_build_phases` (`:247-273`) keep their current derivation and drop
  predicate — correct as they are. Fix the stale `"4 build phases"` comments (`:54`, `:277`,
  `schemas/metrics.py:125`) to say the list derives from `STAGE_VALUES` (5 today).
- `_human_minutes_for_phase` (`:137`) reads the **single** `metrics_minutes_per_mtok` instead of the
  per-phase key at `:212`.
- New `_external_rows(db, project_id, version_id)` → aggregates `ExternalCost` rows into one
  `CostRowRead` with `kind="external"`, priced through `_agent_cost_split` (build a `by_model` dict
  from the entries' `model` field) and converted with the same coefficient + the
  `metrics_hourly_wage_externe` wage. `active_seconds = 0.0`, `turns` = number of entries.
  Per version: `version_id == version.id`. At project level: **all** the project's entries
  (version-bound and version-less alike).
- **Delete** `_compute_headline`, `_config_flags`'s ROI usage (keep the three booleans), and
  `RoiHeadlineRead` construction. The three defects the analysis confirmed in the headline
  (partial-numerator `x_faster`, cumulative money/time population mismatch, `covered` counting a
  token-less version) disappear with it — do not port them.
- `share_pct`: computed per scope after all rows exist; `0.0` when the scope has no tokens.

`flat_subscription` / `marginal_cost_eur` (`:385-386`, hardcoded literals) are deleted from the
schema. The fact they encoded moves to a **sentence on screen** (Part 5) — it is a caveat, not data.

---

## Part 4 — External cost CRUD (`backend/api/routes/external_cost.py`)

New router, mounted in `backend/main.py` next to `metrics_router` (`:22`, `:237`), same prefix.

| Method | Path | Notes |
|---|---|---|
| GET | `/projects/{slug}/external-costs` | list, newest `occurred_on` first |
| POST | `/projects/{slug}/external-costs` | create |
| PATCH | `/projects/{slug}/external-costs/{id}` | partial update |
| DELETE | `/projects/{slug}/external-costs/{id}` | hard delete |

Every handler calls `authz.assert_project_slug_access(db, current_user, slug)` — identical access to
the metrics GET (`routes/metrics.py:37`). Schemas `ExternalCostRead` / `ExternalCostCreate` /
`ExternalCostUpdate` in `backend/schemas/external_cost.py`.

Validation → **422**: `input_tokens`/`output_tokens` negative; `description` empty or > 500;
`model` empty; `version_id` set but not a version of this project; `occurred_on` in the future.

---

## Part 5 — Screen (`frontend/src/pages/MetricsPage.tsx`)

Keep the route (`/projects/:slug/metrics`) — renaming paths is churn. Change the **label** to
`Náklady` in the page title and in the `NavItem` at **`Sidebar.tsx:281`** (`label="Metriky"` →
`"Náklady"`, `disabledTitle` → `"Vyber projekt pre prístup k nákladom"`). Do **not** touch
`:261-268` — that is the UAT entry.

Top to bottom:

1. **Title** `Náklady — <projekt>` + the existing Verzia/Kumulatívne toggle.
2. **Assumption strip** (small, always visible, never hidden behind a tooltip):
   > Ľudská práca je prepočet, nie meranie — pri {coefficient} min na mil. tokenov. · Ceny sú v eurách. ·
   > **Cena AI je hodnota spotrebovaného výpočtu v cenníku, nie minutá hotovosť** — platíme paušál.

   When `coefficient_minutes_per_mtok` is `None`, the first clause reads
   `Koeficient nie je nastavený — ľudské stĺpce sa nezobrazujú` and links to `/settings`.
3. **Two cards**: `Cena AI` and `Cena ľudskej práce`, each with a subline
   `z toho ručne zadané: X €`. No ratio cards. Delete `fmtRatio` (`:54-56`).
4. **Table**, columns in this order:
   `Fáza | ťahy | tokeny vstup/výstup | podiel tokenov | Cena AI (€) | Ľudský čas | Cena ľudskej práce (€)`
   Rows in `rows` order; the `kind="external"` row is labelled **`Externé (ručne zadané)`** and
   visually marked (italic + a `ručne` badge); `kind="system"` keeps today's italic
   `Systém (neporovnané)` treatment. Footer = totals with the measured/entered split.
   Delete the columns `AI čas`, `opravy`, `hodnota vstup/výstup`, `N×`, `M×`, `ušetrené €`
   (`:355-364`). Add `title` tooltips to `ťahy` and `podiel tokenov`.
5. **Per-version list** — same table shape per version (today's section, re-columned).
6. **Externé náklady** — new section: the entry list + an inline form (dátum, popis, model select,
   tokeny vstup, tokeny výstup, verzia select with a *"celý projekt"* option) + edit/delete.
   `PHASE_LABELS` in `labels.ts:31-38` gains no entry — the external and system row labels live in
   the page (they are not build phases).
7. **Keep** the Manažér réžia row and the three idle cards unchanged.

**Delete both charts.** The analysis confirmed they collapse a deliberate `null` into a zero-height
bar (`:213`, `:221-222`) — the exact fabrication the backend refuses to do. A cost chart may return
later; it is not needed to answer the Manager's question and it is not worth re-introducing the bug.

Formatting rules: `fmtCost` keeps `—` for `null` (`:50-52`) — **no `?? 0` anywhere in this file**.
Money to 2 decimals; a negative money value renders in the error colour, never success-green.

---

## Tests (mandatory, RED→GREEN where a defect is reproducible)

### Existing coverage — disposition (do this FIRST; `pytest` cannot go green otherwise)

Both test packages are collected (`pyproject.toml:51`). Two existing files assert on symbols this CR
deletes or renames — they are currently green, so they must be dealt with, not discovered:

- **`tests/test_metrics.py`** (438 lines — the metrics suite lives in the ROOT `tests/` package, not
  in `backend/tests/`). **Rewrite in place; the new cases 1–8 below go HERE.**
  - DELETE: `test_pricing_settings_keys_present` and `test_v1_per_role_keys_retired` assertions over
    `developer_hourly_rate` / `metrics_minutes_per_mtok_{phase}` (`:109-124`), and every ROI
    assertion (`:311-318`, `:360-364`, `:434-435` — `x_faster`, `m_cheaper`, `eur_saved`,
    `covered_versions`, `body["roi"]`, `body["system_overhead"]`).
  - **KEEP and re-point** (they are the ONLY coverage of these and nothing below replaces them):
    the Manažér-wait case (`:293`), the phase-stamp attribution cases, and the metrics **404** path
    (`:438`). `body["by_phase"]` → `body["rows"]`.
- **`backend/tests/test_metrics_phase_stamp.py`** — reads `r.phase` off `_build_phases()` rows at
  `:459, :471, :472, :491, :519, :520, :530, :537`. Mechanical rename `r.phase` → `r.key`.
  **Nothing else in that file changes.**
- **`frontend/src/__tests__/components/test_Sidebar_metrics_link.test.tsx`** — queries the nav button
  by the literal label `Metriky`. Update to `Náklady`.

### New cases

Backend (in `tests/test_metrics.py`, alongside the kept cases):

1. External entry bound to a version appears in that version's totals **and** in the project totals,
   in `…_external`, and never inside `…_measured`.
2. A version-less entry appears in the project totals only — absent from every version.
3. Coefficient unset (`0`) → every `human_*` is `None` while every `agent_cost` is still a number.
4. Wage set for 4 of 5 phases → the phase totals are `None`, `agent_cost_total` is unaffected.
5. An unpriced model in one row → that row's `agent_cost` is `None`, `agent_cost_total` is `None`,
   `unpriced_model_keys` names the model.
6. `share_pct` over all rows sums to 100 ± 0.1 for a scope with tokens; `0.0` for an empty scope.
7. `POST` with a `version_id` from another project → 422. Negative tokens → 422.
8. Migration 086 up→down→up on a DB holding the 5 old coefficient keys leaves no orphan rows.
9. A scope containing a `kind="system"` row with tokens: that row's `human_minutes`/`human_cost` are
   `None`, `wages` has no `"system"` key, `human_cost_measured` is a **number** (not dragged to
   `None` by the system row), and the system row's `agent_cost` IS inside `agent_cost_measured`.
10. `rows` order is phases (canonical order) → `external` → `system`, with absent rows simply missing.

Frontend (`frontend/src/__tests__/`): rewrite `test_MetricsPage.test.tsx` —

11. A `null` `human_cost` renders `—`, and no `0` appears in that cell.
12. The external row carries the `ručne` badge and is not counted inside the measured subline.
13. The assumption strip shows the coefficient value; with `coefficient_minutes_per_mtok: null` it
    shows the not-configured wording and the settings link.
14. The table renders rows in payload order — phases, then `Externé (ručne zadané)`, then
    `Systém (neporovnané)` last.

---

## Out of scope (Dedo handles directly, or deliberately deferred)

- **The v3 cockpit** (`/opt/projects/nex-studio`) does **not** get this screen. It keeps the old
  metrics page; Dedo only aligns its coefficient rows to `600` so the two cockpits stop reporting
  contradictory numbers. Confirm with the Director before the data change.
- **Subscription-share allocation** (this project's % of the monthly Claude MAX fee) — proposed and
  deliberately deferred. The cockpit meters only its own builds, so the share would be a share of
  cockpit traffic, not of real spend. The screen states the flat-subscription caveat in words
  instead (Part 5 §2).
- **Automatic import of Dedo's terminal tokens** from session transcripts — manual entry first; an
  importer is a separate CR.
- **Re-verifying the entered model prices.** `api_price_input_per_mtok_opus = 3` /
  `_output_ = 25` are low for Opus. Director action, not code.
