"""Per-phase project COST backend — the *Náklady* screen (E5; CR-V2-029, reshaped in CR-V2-063).

Covers: the Manažér-wait accumulation listener (enter/exit/total, wait→wait keep, no-accum on a
non-wait set); the per-PHASE aggregation + phase-stamp attribution; per-model pricing (incl. an
unpriced model surfacing as ``None`` + the model key); the hand-entered ``external_cost`` rows and the
measured/entered split they must never be merged into; the honest ``None`` propagation when a price /
the coefficient / a wage is unset; the agent-only ``system`` row; the ``rows`` ORDER contract; the
``share_pct`` footing; the endpoint + 404; the external-cost CRUD 422 gate; and migration 086
up→down→up.

The v1 ROI headline (N× faster, M× cheaper, EUR saved) is retired with CR-V2-063 — the Manažér needs
what it cost, not whether we beat a human — so nothing here asserts on it.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from backend.api.routes.external_cost import router as external_cost_router
from backend.api.routes.metrics import router as metrics_router
from backend.core.security import get_current_user
from backend.db.models.external_cost import ExternalCost
from backend.db.models.foundation import User
from backend.db.models.model_price import ModelPrice
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.system_settings import SystemSetting
from backend.db.models.versions import Version
from backend.db.session import _ensure_pg8000_driver, get_db
from backend.services import metrics as metrics_service
from backend.services import system_setting
from backend.services.metrics import (
    COEFFICIENT_KEY,
    COMPARISON_PHASES,
    EXTERNAL_ROW_KEY,
    SYSTEM_ROW_KEY,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

# ── helpers ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_settings_cache():
    """``system_setting._cache`` is process-global and SURVIVES the per-test rollback — clear it on
    both sides so a value stored by one case can never leak into the next."""
    system_setting.invalidate_cache()
    yield
    system_setting.invalidate_cache()


def _make_user(db_session, role="ri"):
    user = User(
        username=f"u_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
        role=role,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _make_project(db_session, owner):
    project = Project(
        name=f"P {uuid.uuid4().hex[:8]}",
        slug=f"p-{uuid.uuid4().hex[:8]}",
        type="standard",
        auth_mode="password",
        description="d",
        created_by=owner.id,
    )
    db_session.add(project)
    db_session.flush()
    return project


def _make_version(db_session, project, version_number="1.0.0"):
    v = Version(project_id=project.id, version_number=version_number)
    db_session.add(v)
    db_session.flush()
    return v


def _msg(db_session, version_id, author, stage, *, in_tok, out_tok, dur, model="m", phase=None, recorded=True):
    """One metered turn. ``recorded`` (ICCINT-168): the turn carries its per-model parts — as every turn does
    since v4.43.0; ``False`` is a turn from before, input/output only, which cannot be priced."""
    usage: dict[str, Any] = {"input_tokens": in_tok, "output_tokens": out_tok, "model": model}
    if recorded:
        usage["parts"] = [
            {
                "model": model,
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "cost_usd": None,
            }
        ]
    payload: dict[str, Any] = {"usage": usage, "timing": {"duration_seconds": dur, "parse_attempts": 1}}
    if phase is not None:
        payload["phase"] = phase
    m = PipelineMessage(
        version_id=version_id,
        stage=stage,
        author=author,
        recipient="manazer",
        kind="gate_report",
        content="x",
        payload=payload,
    )
    db_session.add(m)
    db_session.flush()
    return m


def _ext(db_session, project, *, version=None, in_tok=2000, out_tok=1000, model="m", day=None, description="Dedo"):
    """One hand-entered ``external_cost`` entry. ``version=None`` = a project-level entry (it belongs
    to the project total and to NO version)."""
    entry = ExternalCost(
        project_id=project.id,
        version_id=version.id if version is not None else None,
        occurred_on=day or date(2026, 7, 1),
        description=description,
        model=model,
        input_tokens=in_tok,
        output_tokens=out_tok,
    )
    db_session.add(entry)
    db_session.flush()
    return entry


def _set(db_session, key, value):
    """Store (or overwrite) one float setting. ``merge`` rather than ``add`` — migration 086 seeds
    ``metrics_minutes_per_mtok`` / ``metrics_hourly_wage_externe``, so an ``add`` would collide on the
    primary key."""
    db_session.merge(SystemSetting(key=key, value=str(value), value_type="float"))
    db_session.flush()
    system_setting.invalidate_cache(key)


def _price_list(db_session, *, model="m", input_usd=3000.0, output_usd=15000.0, eur_usd=1.0):
    """A price list for ``model`` (ICCINT-168 — lists are read from Claude-Code-paid turns, a test plants one).
    The prices are deliberately huge and the rate 1 : 1 so ordinary token counts give euro figures whose
    round-UP is visible: (1000×3000 + 500×15000)/1e6 = 10,5 € → 11 €. Checked "until" far ahead, so the
    refresh at the start of every computation leaves it alone."""
    row = ModelPrice(
        model=model,
        valid_from=datetime(2020, 1, 1, tzinfo=timezone.utc),
        input_usd=input_usd,
        output_usd=output_usd,
        cache_read_usd=input_usd / 10,
        cache_write_usd=input_usd * 2,
        eur_usd=eur_usd,
        rate_date=date(2026, 10, 5) if eur_usd else None,
        rate_source="test" if eur_usd else None,
        observations=10,
        max_deviation=0.0,
        checked_until=datetime(2099, 1, 1, tzinfo=timezone.utc),
    )
    db_session.add(row)
    db_session.flush()
    return row


def _rows(scope) -> dict[str, Any]:
    """A scope's rows keyed by ``key`` (phase key / ``externe`` / ``system``)."""
    return {r.key: r for r in scope.rows}


def _client(db_session, current):
    app = FastAPI()
    app.include_router(metrics_router, prefix="/api/v1")
    app.include_router(external_cost_router, prefix="/api/v1")

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = lambda: current
    return TestClient(app)


# ── settings keys ────────────────────────────────────────────────────────────


def test_pricing_settings_keys_present(db_session):
    """The registry AFTER ICCINT-168: NO hand-entered model price at all (the list is read from turns Claude
    Code paid for — Director: "je zmetkujúce ak niečo tam je uvedené a v skutočnosti sa používa niečo iné"),
    ONE token→minutes coefficient and SIX wages (5 phases + externe). The agent-only ``system`` row has no
    wage key at all."""
    assert not [k for k in system_setting.DEFAULT_SETTINGS if k.startswith("api_price")]
    wages = [f"metrics_hourly_wage_{p}" for p in COMPARISON_PHASES] + [f"metrics_hourly_wage_{EXTERNAL_ROW_KEY}"]
    for key in (COEFFICIENT_KEY, *wages):
        assert key in system_setting.DEFAULT_SETTINGS, key
        assert system_setting.DEFAULT_SETTINGS[key].value_type == "float"
    for key in wages:
        assert system_setting.DEFAULT_SETTINGS[key].value == "0.0", key  # unset by default → null, never faked
    assert len(wages) == 6  # 5 phases + externe — one wage per row that HAS a human side
    # ONE coefficient now, carrying the Manažér-calibrated 600 as its default.
    assert system_setting.DEFAULT_SETTINGS[COEFFICIENT_KEY].value == "600"
    # The five per-phase coefficients collapsed into it; developer_hourly_rate was dead code.
    for key in [f"metrics_minutes_per_mtok_{p}" for p in COMPARISON_PHASES] + ["developer_hourly_rate"]:
        assert key not in system_setting.DEFAULT_SETTINGS, key
    # The system row is agent-only — no wage key exists (nor will one be added).
    assert f"metrics_hourly_wage_{SYSTEM_ROW_KEY}" not in system_setting.DEFAULT_SETTINGS


def test_v1_per_role_keys_retired(db_session):
    """CR-V2-029 OWNS retiring the 11 v1 per-role keys (+ the dead director-rate) from DEFAULT_SETTINGS."""
    retired = [
        f"metrics_minutes_per_mtok_{r}" for r in ("coordinator", "designer", "customer", "implementer", "auditor")
    ]
    retired += [
        f"metrics_hourly_wage_{r}"
        for r in ("coordinator", "designer", "customer", "implementer", "auditor", "director")
    ]
    assert len(retired) == 11
    retired.append("metrics_director_minutes_per_human_role_hour")  # dead with the priced Director overhead
    for key in retired:
        assert key not in system_setting.DEFAULT_SETTINGS, key


def test_get_float_or_none_distinguishes_unset_from_explicit_zero(db_session):
    assert system_setting.get_float_or_none(db_session, "metrics_hourly_wage_programovanie") is None  # no row
    _set(db_session, "metrics_hourly_wage_programovanie", "0.0")  # explicit 0
    assert system_setting.get_float_or_none(db_session, "metrics_hourly_wage_programovanie") == 0.0


# ── Manažér-wait accumulation listener ───────────────────────────────────────


def _state(db_session, version):
    st = PipelineState(
        version_id=version.id,
        flow_type="new_version",
        current_stage="priprava",
        current_actor="ai_agent",
        status="agent_working",
        next_action="x",
    )
    db_session.add(st)
    db_session.flush()
    return st


def test_manager_wait_accumulates_on_exit(db_session):
    user = _make_user(db_session)
    project = _make_project(db_session, user)
    version = _make_version(db_session, project)
    st = _state(db_session, version)
    assert (st.total_director_wait_seconds or 0.0) == 0.0

    st.status = "awaiting_manazer"  # ENTER → stamp
    db_session.flush()
    assert st.awaiting_director_since is not None
    st.awaiting_director_since = datetime.now(timezone.utc) - timedelta(seconds=60)  # backdate 60s

    st.status = "agent_working"  # LEAVE → accumulate + clear
    db_session.flush()
    assert st.awaiting_director_since is None
    assert st.total_director_wait_seconds >= 60


def test_manager_wait_wait_to_wait_keeps_clock_no_accum(db_session):
    user = _make_user(db_session)
    project = _make_project(db_session, user)
    version = _make_version(db_session, project)
    st = _state(db_session, version)

    st.status = "awaiting_manazer"
    db_session.flush()
    stamped = st.awaiting_director_since
    st.status = "blocked"  # wait → wait: keep clock, do NOT accumulate
    db_session.flush()
    assert st.awaiting_director_since == stamped
    assert (st.total_director_wait_seconds or 0.0) == 0.0


def test_manager_wait_two_intervals_sum(db_session):
    user = _make_user(db_session)
    project = _make_project(db_session, user)
    version = _make_version(db_session, project)
    st = _state(db_session, version)

    for _ in range(2):
        st.status = "awaiting_manazer"
        db_session.flush()
        st.awaiting_director_since = datetime.now(timezone.utc) - timedelta(seconds=30)
        st.status = "agent_working"
        db_session.flush()
    assert st.total_director_wait_seconds >= 60  # two 30s intervals


# ── aggregation + per-phase breakdown ────────────────────────────────────────


def test_metrics_aggregation_and_breakdown(db_session):
    user = _make_user(db_session)
    project = _make_project(db_session, user)
    version = _make_version(db_session, project)
    _msg(db_session, version.id, "ai_agent", "programovanie", in_tok=1000, out_tok=500, dur=10.0, phase="programovanie")
    _msg(db_session, version.id, "ai_agent", "navrh", in_tok=2000, out_tok=800, dur=20.0, phase="navrh")

    m = metrics_service.compute_project_metrics(db_session, project)

    assert m.usage.input_tokens == 3000
    assert m.usage.output_tokens == 1300
    assert m.usage.duration_seconds == 30.0
    assert m.usage.messages == 2
    v = m.by_version[0]
    # metrics-v3-three-phases.md Part 2: only phases that DID work are emitted, in canonical order — the
    # zero-token phases (priprava / vizual / verifikacia here) are DROPPED (no phantom empty rows).
    assert [r.key for r in v.rows if r.kind == "phase"] == ["navrh", "programovanie"]
    rows = _rows(v)
    assert rows["programovanie"].input_tokens == 1000
    assert rows["navrh"].input_tokens == 2000
    assert "priprava" not in rows  # zero-token phase → no row
    assert "verifikacia" not in rows
    # footing preserved: the emitted phase rows still sum to the version grand total
    assert sum(r.input_tokens for r in v.rows if r.kind == "phase") == v.usage.input_tokens
    # no un-phased system-authored messages → no engine-overhead row at all
    assert SYSTEM_ROW_KEY not in rows


def test_metrics_phase_stamp_attribution(db_session):
    """A turn recorded under one DB stage but carrying a ``phase`` stamp lands in the STAMPED phase; a
    genuinely-system, un-phased message foots the system-overhead row (CR-V2-029)."""
    user = _make_user(db_session)
    project = _make_project(db_session, user)
    version = _make_version(db_session, project)
    # a helper turn recorded under stage=verifikacia but stamped phase=programovanie (its spawning phase)
    _msg(db_session, version.id, "ai_agent", "verifikacia", in_tok=300, out_tok=120, dur=4.0, phase="programovanie")
    # a genuinely-system message with NO phase stamp → falls back to its stage (priprava) bucket
    _msg(db_session, version.id, "system", "priprava", in_tok=10, out_tok=5, dur=1.0)

    m = metrics_service.compute_project_metrics(db_session, project)
    rows = _rows(m.by_version[0])
    assert rows["programovanie"].input_tokens == 300  # stamp wins over stage
    assert rows["priprava"].input_tokens == 10  # un-stamped system note → its stage bucket
    # no message carried the "system" bucket key (no system-stamped, un-staged record) → no system row
    assert SYSTEM_ROW_KEY not in rows


def test_metrics_manager_overhead_measured_only(db_session):
    """The Manažér overhead is measured (wait + interventions) only — no priced cost in v2."""
    user = _make_user(db_session)
    project = _make_project(db_session, user)
    version = _make_version(db_session, project)
    st = _state(db_session, version)
    st.status = "awaiting_manazer"
    db_session.flush()
    st.awaiting_director_since = datetime.now(timezone.utc) - timedelta(seconds=45)
    st.status = "agent_working"
    db_session.flush()
    # a Manažér-authored message counts as one intervention
    db_session.add(
        PipelineMessage(
            version_id=version.id,
            stage="navrh",
            author="manazer",
            recipient="ai_agent",
            kind="approval",
            content="ok",
            payload={"phase": "navrh"},
        )
    )
    db_session.flush()

    v = metrics_service.compute_project_metrics(db_session, project).by_version[0]
    assert v.manager.interventions == 1
    assert v.manager.wait_seconds >= 45
    assert v.manager_wait_seconds >= 45


# ── cost: unconfigured → null (never fabricated) ─────────────────────────────


def test_metrics_unconfigured_returns_nulls(db_session, monkeypatch):
    _set(db_session, COEFFICIENT_KEY, "0")  # migration 086 seeds 600 — this scope is deliberately unset

    user = _make_user(db_session)
    project = _make_project(db_session, user)
    version = _make_version(db_session, project)
    _msg(db_session, version.id, "ai_agent", "programovanie", in_tok=1000, out_tok=500, dur=10.0, phase="programovanie")

    m = metrics_service.compute_project_metrics(db_session, project)
    prog = _rows(m)["programovanie"]
    assert prog.agent_cost is None  # "m" has no price list yet → nothing priced
    assert [u.reason for u in prog.unpriced] == [metrics_service.NO_PRICE_REASON.format(model="m")]
    assert prog.human_minutes is None
    assert prog.human_cost is None
    # nothing priced: unpriced, never a fabricated 0 € — even next to the external half's REAL zero
    assert m.totals.agent_cost_measured is None and m.totals.agent_cost_total is None
    assert m.totals.agent_cost_external == 0
    assert m.totals.agent_cost_complete is False
    assert m.totals.human_minutes_measured is None and m.totals.human_minutes_total is None
    assert m.totals.human_cost_measured is None and m.totals.human_cost_total is None
    assert m.coefficient_minutes_per_mtok is None
    assert m.price_list == []  # no list was used — the screen shows none
    # `coefficient_configured` was removed: the screen derives the same fact from the coefficient
    # itself, and one fact with two sources is how they drift apart. Assert the source.
    assert m.coefficient_minutes_per_mtok is None
    assert m.wages_configured is False


# ── cost: configured → computed ──────────────────────────────────────────────


def test_metrics_configured_computes_costs(db_session, monkeypatch):
    _price_list(db_session)
    _set(db_session, COEFFICIENT_KEY, "240")
    _set(db_session, "metrics_hourly_wage_programovanie", "60")
    _set(db_session, "metrics_hourly_wage_navrh", "100")

    user = _make_user(db_session)
    project = _make_project(db_session, user)
    version = _make_version(db_session, project)
    _msg(db_session, version.id, "ai_agent", "programovanie", in_tok=1000, out_tok=500, dur=10.0, phase="programovanie")
    _msg(db_session, version.id, "ai_agent", "navrh", in_tok=2000, out_tok=800, dur=20.0, phase="navrh")

    m = metrics_service.compute_project_metrics(db_session, project)
    rows = _rows(m)

    # Every euro figure is WHOLE euros rounded UP (ICCINT-168 — Director: rather a little more than less).
    # programovanie: agent (1000×3000 + 500×15000)/1e6 = 10,5 € → 11; human 1500/1e6×240 = 0.36 min → 0,36 € → 1
    assert rows["programovanie"].agent_cost == 11
    assert rows["programovanie"].human_minutes == pytest.approx(0.36)
    assert rows["programovanie"].human_cost == 1
    # navrh: agent (2000×3000 + 800×15000)/1e6 = 18 € → 18 (an exact euro is NOT rounded up to 19);
    # human 2800/1e6×240 = 0.672 min → /60×100 = 1,12 € → 2
    assert rows["navrh"].agent_cost == 18
    assert rows["navrh"].human_minutes == pytest.approx(0.672)
    assert rows["navrh"].human_cost == 2

    t = m.totals
    assert t.agent_cost_measured == 29  # 11 + 18 — the SUM of what the rows show, so the table adds up
    assert t.agent_cost_external == 0  # nothing was entered → a REAL zero, not None
    assert t.agent_cost_total == 29
    assert t.agent_cost_complete is True
    assert t.human_minutes_measured == pytest.approx(1.032)
    assert t.human_cost_measured == 3  # 1 + 2, not ⌈1,48⌉ = 2
    assert t.human_cost_total == 3

    # the assumption block the screen must display
    assert m.coefficient_minutes_per_mtok == pytest.approx(240)
    assert m.wages["programovanie"] == pytest.approx(60)
    assert m.wages["navrh"] == pytest.approx(100)
    assert m.wages["priprava"] is None  # unset → None, never 0
    assert m.currency == "EUR"
    # the price list the figures were computed with — shown on the screen with its rate
    assert [(p.model, p.input_usd, p.output_usd, p.eur_usd) for p in m.price_list] == [("m", 3000.0, 15000.0, 1.0)]
    assert m.by_version[0].price_list == m.price_list
    assert m.coefficient_minutes_per_mtok is not None
    assert m.wages_configured is True


def test_metrics_per_model_price_list_and_unpriced_row(db_session, monkeypatch):
    """Each model is priced by ITS list; a model with no list yet is NAMED as unpriced and the priced part
    still shows (ICCINT-168: the old rule — a ``None`` total for any gap — hid the whole known spend), with
    ``agent_cost_complete`` False so the screen says the figure is partial."""
    _price_list(db_session, model="claude-opus-4-8", input_usd=5000.0, output_usd=25000.0)

    user = _make_user(db_session)
    project = _make_project(db_session, user)
    version = _make_version(db_session, project)
    _msg(
        db_session,
        version.id,
        "ai_agent",
        "programovanie",
        in_tok=1000,
        out_tok=400,
        dur=5.0,
        model="claude-opus-4-8",
        phase="programovanie",
    )
    _msg(
        db_session,
        version.id,
        "ai_agent",
        "navrh",
        in_tok=1000,
        out_tok=400,
        dur=5.0,
        model="claude-zeta-9",
        phase="navrh",
    )

    m = metrics_service.compute_project_metrics(db_session, project)
    rows = _rows(m)
    assert rows["programovanie"].agent_cost == 15  # (1000×5000 + 400×25000)/1e6 = 15 €
    assert rows["programovanie"].unpriced == []
    assert rows["navrh"].agent_cost is None  # no list for claude-zeta-9 → nothing in the row priced
    assert [(u.reason, u.tokens) for u in rows["navrh"].unpriced] == [
        (metrics_service.NO_PRICE_REASON.format(model="claude-zeta-9"), 1400)
    ]
    assert m.totals.agent_cost_measured == 15  # the priced part …
    assert m.totals.agent_cost_total == 15
    assert m.totals.agent_cost_complete is False  # … flagged as partial
    assert [p.model for p in m.price_list] == ["claude-opus-4-8"]  # only lists actually used


def test_turn_recorded_before_v4_43_is_named_unrecorded_not_priced(db_session):
    """A turn stored before ICCINT-168 carries input/output only — the cache it read was never seen — so it
    cannot be priced and must not be under-priced from what it does carry. The row says why."""
    _price_list(db_session)
    user = _make_user(db_session)
    project = _make_project(db_session, user)
    version = _make_version(db_session, project)
    _msg(db_session, version.id, "ai_agent", "programovanie", in_tok=1000, out_tok=500, dur=10.0, phase="programovanie")
    _msg(
        db_session,
        version.id,
        "ai_agent",
        "programovanie",
        in_tok=2000,
        out_tok=800,
        dur=10.0,
        phase="programovanie",
        recorded=False,
    )

    row = _rows(metrics_service.compute_project_metrics(db_session, project))["programovanie"]
    assert row.agent_cost == 11  # the recorded turn alone (10,5 € → 11)
    assert [(u.reason, u.turns, u.tokens) for u in row.unpriced] == [(metrics_service.UNRECORDED_REASON, 1, 2800)]
    assert row.input_tokens == 3000 and row.output_tokens == 1300  # its tokens still count everywhere else


def test_cache_tokens_are_priced_and_shown_but_never_human_minutes(db_session):
    """ICCINT-168: cache reads/writes are on the agent's bill (each at its own price) and on the screen, but
    a person does not re-read the whole conversation at every step — the human minutes stay input+output."""
    _price_list(db_session)  # cache read 300 $, cache write 6000 $ per million (from the helper)
    _set(db_session, COEFFICIENT_KEY, "600")
    user = _make_user(db_session)
    project = _make_project(db_session, user)
    version = _make_version(db_session, project)
    m = _msg(
        db_session, version.id, "ai_agent", "programovanie", in_tok=1000, out_tok=500, dur=1.0, phase="programovanie"
    )
    part = m.payload["usage"]["parts"][0]
    m.payload = {
        **m.payload,
        "usage": {**m.payload["usage"], "parts": [{**part, "cache_read_tokens": 10_000, "cache_write_tokens": 500}]},
    }
    db_session.flush()

    row = _rows(metrics_service.compute_project_metrics(db_session, project))["programovanie"]
    assert (row.cache_read_tokens, row.cache_write_tokens) == (10_000, 500)
    # 10,5 € (input+output) + 10 000×300/1e6 = 3 € + 500×6000/1e6 = 3 € → 16,5 € → 17
    assert row.agent_cost == 17
    assert row.human_minutes == pytest.approx(0.9)  # 1500/1e6×600 — the cache is not human work


def test_price_list_without_rate_is_named_not_converted(db_session):
    """A list whose ECB rate could not be fetched yet cannot give euros — named, never a guessed rate."""
    _price_list(db_session, eur_usd=None)
    user = _make_user(db_session)
    project = _make_project(db_session, user)
    version = _make_version(db_session, project)
    _msg(db_session, version.id, "ai_agent", "programovanie", in_tok=1000, out_tok=500, dur=1.0, phase="programovanie")

    row = _rows(metrics_service.compute_project_metrics(db_session, project))["programovanie"]
    assert row.agent_cost is None
    assert [u.reason for u in row.unpriced] == [metrics_service.NO_RATE_REASON]


def test_euros_follow_the_lists_rate(db_session):
    """The list's ECB rate converts: 1 € = 2 $ halves the dollar figure (21 $ → 10,5 € → 11)."""
    _price_list(db_session, eur_usd=2.0)
    user = _make_user(db_session)
    project = _make_project(db_session, user)
    version = _make_version(db_session, project)
    _msg(db_session, version.id, "ai_agent", "programovanie", in_tok=2000, out_tok=1000, dur=1.0, phase="programovanie")

    m = metrics_service.compute_project_metrics(db_session, project)
    assert _rows(m)["programovanie"].agent_cost == 11
    listed = m.price_list[0]
    assert (listed.input_eur, listed.output_eur) == (1500.0, 7500.0)  # the list shown in euros at that rate


# ── external cost: measured and entered are summed but NEVER merged ───────────


def _configured_scope(db_session, monkeypatch):
    """Prices + coefficient + the two wages the external cases compare against."""
    _price_list(db_session)
    _set(db_session, COEFFICIENT_KEY, "600")
    _set(db_session, "metrics_hourly_wage_programovanie", "60")
    _set(db_session, f"metrics_hourly_wage_{EXTERNAL_ROW_KEY}", "50")


def test_external_entry_on_version_counts_in_that_version_and_the_project(db_session, monkeypatch):
    """Case 1: a version-bound entry lands in that version's totals AND in the project totals, always in
    ``…_external`` and NEVER inside ``…_measured``."""
    _configured_scope(db_session, monkeypatch)
    user = _make_user(db_session)
    project = _make_project(db_session, user)
    version = _make_version(db_session, project)
    _msg(db_session, version.id, "ai_agent", "programovanie", in_tok=1000, out_tok=500, dur=10.0, phase="programovanie")
    _ext(db_session, project, version=version, in_tok=2000, out_tok=1000)

    m = metrics_service.compute_project_metrics(db_session, project)
    v = m.by_version[0]

    ext_row = _rows(v)[EXTERNAL_ROW_KEY]
    assert ext_row.kind == "external"
    assert ext_row.turns == 1  # one entry
    assert ext_row.active_seconds == 0.0  # nothing was metered here
    assert ext_row.agent_cost == 21  # (2000×3000 + 1000×15000)/1e6 — priced by the SAME list as measured work

    for totals in (v.totals, m.totals):
        # measured stays the metered row alone — the entered figure never merges into it
        assert totals.agent_cost_measured == 11
        assert totals.agent_cost_external == 21
        assert totals.agent_cost_total == 32
        assert totals.human_minutes_measured == pytest.approx(0.9)  # 1500/1e6×600
        assert totals.human_minutes_external == pytest.approx(1.8)  # 3000/1e6×600
        assert totals.human_minutes_total == pytest.approx(2.7)
        assert totals.human_cost_measured == 1  # 0.9/60×60 = 0,90 € → 1
        assert totals.human_cost_external == 2  # 1.8/60×50 = 1,50 € → 2
        assert totals.human_cost_total == 3


def test_version_less_external_entry_counts_in_the_project_only(db_session, monkeypatch):
    """Case 2: a version-less entry belongs to the project total and to NO version."""
    _configured_scope(db_session, monkeypatch)
    user = _make_user(db_session)
    project = _make_project(db_session, user)
    version = _make_version(db_session, project)
    _msg(db_session, version.id, "ai_agent", "programovanie", in_tok=1000, out_tok=500, dur=10.0, phase="programovanie")
    _ext(db_session, project, version=None, in_tok=2000, out_tok=1000)

    m = metrics_service.compute_project_metrics(db_session, project)
    v = m.by_version[0]

    assert EXTERNAL_ROW_KEY not in _rows(v)  # absent from the version scope entirely
    assert v.totals.agent_cost_external == 0  # a REAL zero — nothing was entered for this version
    assert v.totals.human_minutes_external == 0.0
    assert v.totals.human_cost_external == 0
    assert v.totals.agent_cost_total == 11

    assert EXTERNAL_ROW_KEY in _rows(m)
    assert m.totals.agent_cost_external == 21
    assert m.totals.agent_cost_total == 32


def test_deleting_a_version_keeps_its_external_entries_at_project_level(db_session, monkeypatch):
    """``external_cost.version_id`` is ON DELETE **SET NULL**, never CASCADE: the money was really spent,
    so deleting a version must not erase a hand-entered figure from the project total. The entry survives
    and degrades to a project-level one — which is exactly what a NULL ``version_id`` already means."""
    _configured_scope(db_session, monkeypatch)
    user = _make_user(db_session)
    project = _make_project(db_session, user)
    version = _make_version(db_session, project)
    entry_id = _ext(db_session, project, version=version, in_tok=2000, out_tok=1000).id

    db_session.delete(version)
    db_session.flush()
    db_session.expire_all()

    survivor = db_session.get(ExternalCost, entry_id)
    assert survivor is not None  # NOT cascaded away with the version
    assert survivor.version_id is None  # degraded to a project-level entry
    assert survivor.input_tokens == 2000 and survivor.output_tokens == 1000

    m = metrics_service.compute_project_metrics(db_session, project)
    assert m.by_version == []  # the version really is gone
    assert EXTERNAL_ROW_KEY in _rows(m)  # …and its spend still counts in the project total
    assert m.totals.agent_cost_external == 21


def test_totals_split_turns_and_tokens_measured_vs_entered(db_session, monkeypatch):
    """The volume figures carry the SAME measured/entered split as the money ones — a merged turn/token
    count would be a merge of measured and entered figures, which the screen must never present."""
    _configured_scope(db_session, monkeypatch)
    user = _make_user(db_session)
    project = _make_project(db_session, user)
    version = _make_version(db_session, project)
    _msg(db_session, version.id, "ai_agent", "programovanie", in_tok=1000, out_tok=500, dur=10.0, phase="programovanie")
    _msg(db_session, version.id, "system", "priprava", in_tok=100, out_tok=50, dur=1.0, phase=SYSTEM_ROW_KEY)
    _ext(db_session, project, version=version, in_tok=2000, out_tok=1000)

    m = metrics_service.compute_project_metrics(db_session, project)
    for scope in (m.by_version[0], m):  # the entry is version-bound → both scopes hold the same figures
        t = scope.totals
        # measured = the phase + system rows; external = the single entered row
        assert (t.turns_measured, t.turns_external, t.turns) == (2, 1, 3)
        assert (t.input_tokens_measured, t.input_tokens_external, t.input_tokens) == (1100, 2000, 3100)
        assert (t.output_tokens_measured, t.output_tokens_external, t.output_tokens) == (550, 1000, 1550)
        # the combined figures stay the sum of both halves (they are the totals)
        assert t.turns_measured + t.turns_external == t.turns
        assert t.input_tokens_measured + t.input_tokens_external == t.input_tokens
        assert t.output_tokens_measured + t.output_tokens_external == t.output_tokens


def test_totals_volume_split_is_a_real_zero_without_entries(db_session, monkeypatch):
    """A scope with nothing entered reports ``0`` entered volume — a REAL zero, matching the
    ``agent_cost_external`` rule (nothing was entered is knowledge, not a gap)."""
    _configured_scope(db_session, monkeypatch)
    user = _make_user(db_session)
    project = _make_project(db_session, user)
    version = _make_version(db_session, project)
    _msg(db_session, version.id, "ai_agent", "programovanie", in_tok=1000, out_tok=500, dur=10.0, phase="programovanie")

    t = metrics_service.compute_project_metrics(db_session, project).totals
    assert (t.turns_external, t.input_tokens_external, t.output_tokens_external) == (0, 0, 0)
    assert (t.turns_measured, t.input_tokens_measured, t.output_tokens_measured) == (1, 1000, 500)


# ── honest None propagation ──────────────────────────────────────────────────


def test_coefficient_unset_nulls_every_human_figure_but_no_cost(db_session, monkeypatch):
    """Case 3: coefficient 0 → every ``human_*`` is ``None`` while every ``agent_cost`` is still a number
    (the human side is a conversion; without its coefficient there is nothing to convert)."""
    _configured_scope(db_session, monkeypatch)
    _set(db_session, COEFFICIENT_KEY, "0")

    user = _make_user(db_session)
    project = _make_project(db_session, user)
    version = _make_version(db_session, project)
    _msg(db_session, version.id, "ai_agent", "programovanie", in_tok=1000, out_tok=500, dur=10.0, phase="programovanie")
    _ext(db_session, project, version=version, in_tok=2000, out_tok=1000)

    m = metrics_service.compute_project_metrics(db_session, project)
    assert len(m.rows) == 2  # one phase row + the external row
    for row in m.rows:
        assert row.human_minutes is None, row.key
        assert row.human_cost is None, row.key
        assert row.agent_cost is not None, row.key

    t = m.totals
    assert t.human_minutes_measured is None and t.human_minutes_external is None and t.human_minutes_total is None
    assert t.human_cost_measured is None and t.human_cost_external is None and t.human_cost_total is None
    assert t.agent_cost_measured == 11
    assert t.agent_cost_external == 21
    assert t.agent_cost_total == 32
    assert m.coefficient_minutes_per_mtok is None
    # `coefficient_configured` was removed: the screen derives the same fact from the coefficient
    # itself, and one fact with two sources is how they drift apart. Assert the source.
    assert m.coefficient_minutes_per_mtok is None


def test_wage_missing_on_one_phase_nulls_the_human_cost_totals_only(db_session, monkeypatch):
    """Case 4: a wage set for 4 of the 5 phases → the human COST totals are ``None`` (a partial sum would
    read as a total), while the coefficient-only minutes and the whole agent side are unaffected."""
    _price_list(db_session)
    _set(db_session, COEFFICIENT_KEY, "600")
    unpaid_phase = "vizual"
    for phase in COMPARISON_PHASES:
        if phase != unpaid_phase:
            _set(db_session, f"metrics_hourly_wage_{phase}", "60")

    user = _make_user(db_session)
    project = _make_project(db_session, user)
    version = _make_version(db_session, project)
    for phase in COMPARISON_PHASES:
        _msg(db_session, version.id, "ai_agent", phase, in_tok=1000, out_tok=500, dur=10.0, phase=phase)

    m = metrics_service.compute_project_metrics(db_session, project)
    rows = _rows(m)
    assert rows[unpaid_phase].human_minutes == pytest.approx(0.9)  # minutes need the coefficient only
    assert rows[unpaid_phase].human_cost is None  # …but no wage → no cost
    assert rows["programovanie"].human_cost == 1  # 0,90 € → 1

    t = m.totals
    assert t.human_cost_measured is None
    assert t.human_cost_total is None
    assert t.human_minutes_measured == pytest.approx(4.5)  # 5 × 0.9 — wage-independent
    assert t.agent_cost_measured == 55  # 5 × 11 — unaffected
    assert t.agent_cost_total == 55


def test_system_row_is_agent_only_and_counts_in_agent_measured(db_session, monkeypatch):
    """Case 9: the ``kind="system"`` row carries NO human figures and has no wage key, yet its
    ``agent_cost`` IS metered spend — it belongs in ``agent_cost_measured`` and must never drag the human
    totals to ``None``."""
    _price_list(db_session)
    _set(db_session, COEFFICIENT_KEY, "600")
    _set(db_session, "metrics_hourly_wage_programovanie", "60")

    user = _make_user(db_session)
    project = _make_project(db_session, user)
    version = _make_version(db_session, project)
    _msg(db_session, version.id, "ai_agent", "programovanie", in_tok=1000, out_tok=500, dur=10.0, phase="programovanie")
    # an engine turn stamped with the non-comparison "system" bucket → folded into the system row
    _msg(db_session, version.id, "system", "priprava", in_tok=200, out_tok=100, dur=2.0, phase=SYSTEM_ROW_KEY)

    m = metrics_service.compute_project_metrics(db_session, project)
    sys_row = _rows(m)[SYSTEM_ROW_KEY]
    assert sys_row.kind == "system"
    assert sys_row.input_tokens == 200 and sys_row.output_tokens == 100
    assert sys_row.human_minutes is None
    assert sys_row.human_cost is None
    assert SYSTEM_ROW_KEY not in m.wages  # no metrics_hourly_wage_system key exists or will be added
    assert sys_row.agent_cost == 3  # (200×3000 + 100×15000)/1e6 = 2,1 € → 3

    t = m.totals
    assert t.agent_cost_measured == 14  # 11 (phase) + 3 (system)
    assert t.human_cost_measured == 1  # a NUMBER — the system row did not drag it to None
    assert t.human_minutes_measured == pytest.approx(0.9)


def test_system_row_alone_nulls_the_human_totals_instead_of_fabricating_zero(db_session, monkeypatch):
    """The mirror image of case 9: with the whole metered spend in the agent-only ``system`` row there is
    NO ``kind="phase"`` row carrying tokens, so the human measured/total figures are ``None`` — a Σ over
    an empty phase set would render "Cena ľudskej práce 0,00 €" under a table showing nothing but ``—``.
    The agent side is unaffected (system spend IS metered) and the untouched external side keeps its REAL
    zero (nothing was entered)."""
    _configured_scope(db_session, monkeypatch)
    user = _make_user(db_session)
    project = _make_project(db_session, user)
    version = _make_version(db_session, project)
    _msg(db_session, version.id, "system", "priprava", in_tok=200, out_tok=100, dur=2.0, phase=SYSTEM_ROW_KEY)

    m = metrics_service.compute_project_metrics(db_session, project)
    for scope in (m, m.by_version[0]):
        assert [r.key for r in scope.rows] == [SYSTEM_ROW_KEY]  # no phase row at all
        t = scope.totals
        assert t.human_minutes_measured is None
        assert t.human_cost_measured is None
        assert t.human_minutes_total is None
        assert t.human_cost_total is None
        # the agent side is metered spend and stays a number; nothing was entered → a REAL 0.0 external
        assert t.agent_cost_measured == 3  # (200×3000 + 100×15000)/1e6 = 2,1 € → 3
        assert t.agent_cost_external == 0
        assert t.agent_cost_total == 3


def test_externe_wage_alone_marks_wages_configured(db_session, monkeypatch):
    """``wages_configured`` covers EVERY wage-bearing row, ``externe`` included: its wage puts a real
    human-cost number on screen, so a scope priced through it alone must not be labelled
    "Mzdy nenastavené"."""
    _price_list(db_session)
    _set(db_session, COEFFICIENT_KEY, "600")
    for phase in COMPARISON_PHASES:
        _set(db_session, f"metrics_hourly_wage_{phase}", "0")
    _set(db_session, f"metrics_hourly_wage_{EXTERNAL_ROW_KEY}", "50")

    user = _make_user(db_session)
    project = _make_project(db_session, user)
    _ext(db_session, project, in_tok=2000, out_tok=1000)

    m = metrics_service.compute_project_metrics(db_session, project)
    assert m.wages[EXTERNAL_ROW_KEY] == pytest.approx(50)
    assert all(m.wages[phase] is None for phase in COMPARISON_PHASES)
    assert m.wages_configured is True  # the externe wage alone satisfies the flag
    assert m.totals.human_cost_external == 2  # 3000/1e6×600 = 1.8 min → /60×50 = 1,50 € → 2


# ── share_pct + row order ────────────────────────────────────────────────────


def test_share_pct_sums_to_100_over_a_scope_with_tokens(db_session, monkeypatch):
    """Case 6 (a): every row's token share of the scope, summing to 100 ± 0.1 across ALL rows."""
    _configured_scope(db_session, monkeypatch)
    user = _make_user(db_session)
    project = _make_project(db_session, user)
    version = _make_version(db_session, project)
    _msg(db_session, version.id, "ai_agent", "navrh", in_tok=1000, out_tok=500, dur=10.0, phase="navrh")
    _msg(db_session, version.id, "ai_agent", "programovanie", in_tok=2000, out_tok=800, dur=20.0, phase="programovanie")
    _msg(db_session, version.id, "system", "priprava", in_tok=100, out_tok=50, dur=1.0, phase=SYSTEM_ROW_KEY)
    _ext(db_session, project, version=version, in_tok=500, out_tok=250)

    v = metrics_service.compute_project_metrics(db_session, project).by_version[0]
    assert len(v.rows) == 4
    assert sum(r.share_pct for r in v.rows) == pytest.approx(100.0, abs=0.1)
    assert _rows(v)["navrh"].share_pct == pytest.approx(1500 / 5200 * 100.0)


def test_share_pct_is_zero_for_a_scope_without_tokens(db_session):
    """Case 6 (b): a scope with no tokens leaves every row at ``0.0`` — nothing to divide, and a
    fabricated share would be worse than none."""
    user = _make_user(db_session)
    project = _make_project(db_session, user)
    version = _make_version(db_session, project)
    # a failed turn: real wall-clock, zero tokens — the row is KEPT, its share is a real 0.0
    _msg(db_session, version.id, "ai_agent", "programovanie", in_tok=0, out_tok=0, dur=7.0, phase="programovanie")

    v = metrics_service.compute_project_metrics(db_session, project).by_version[0]
    assert [r.key for r in v.rows] == ["programovanie"]
    assert all(r.share_pct == 0.0 for r in v.rows)


def test_rows_order_is_phases_then_external_then_system(db_session, monkeypatch):
    """Case 10: the payload order is a backend contract — phase rows in canonical ``COMPARISON_PHASES``
    order, then the ``external`` row, then the ``system`` row last (it foots the table). A row absent
    from a scope is simply not emitted."""
    _configured_scope(db_session, monkeypatch)
    user = _make_user(db_session)
    project = _make_project(db_session, user)
    version = _make_version(db_session, project)
    # deliberately seeded OUT of canonical order (programovanie before navrh) — the payload re-orders
    _msg(db_session, version.id, "ai_agent", "programovanie", in_tok=2000, out_tok=800, dur=20.0, phase="programovanie")
    _msg(db_session, version.id, "ai_agent", "navrh", in_tok=1000, out_tok=500, dur=10.0, phase="navrh")
    _msg(db_session, version.id, "system", "priprava", in_tok=100, out_tok=50, dur=1.0, phase=SYSTEM_ROW_KEY)
    _ext(db_session, project, version=version)

    m = metrics_service.compute_project_metrics(db_session, project)
    expected_keys = ["navrh", "programovanie", EXTERNAL_ROW_KEY, SYSTEM_ROW_KEY]
    expected_kinds = ["phase", "phase", "external", "system"]
    assert [r.key for r in m.by_version[0].rows] == expected_keys
    assert [r.kind for r in m.by_version[0].rows] == expected_kinds
    assert [r.key for r in m.rows] == expected_keys  # same contract at project scope
    assert [r.kind for r in m.rows] == expected_kinds

    # a scope with neither entered nor un-phased spend emits phase rows ONLY
    other = _make_project(db_session, user)
    other_version = _make_version(db_session, other)
    _msg(db_session, other_version.id, "ai_agent", "navrh", in_tok=10, out_tok=5, dur=1.0, phase="navrh")
    other_metrics = metrics_service.compute_project_metrics(db_session, other)
    assert [r.key for r in other_metrics.rows] == ["navrh"]


# ── endpoints ────────────────────────────────────────────────────────────────


def test_endpoint_returns_shape_and_404(db_session):
    user = _make_user(db_session)
    project = _make_project(db_session, user)
    version = _make_version(db_session, project)
    _msg(db_session, version.id, "ai_agent", "priprava", in_tok=100, out_tok=50, dur=5.0, phase="priprava")
    client = _client(db_session, user)

    r = client.get(f"/api/v1/projects/{project.slug}/metrics")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["slug"] == project.slug
    assert body["usage"]["input_tokens"] == 100
    assert len(body["by_version"]) == 1
    # metrics-v3-three-phases.md Part 2: only phases that did work are emitted — this project ran a single
    # priprava turn, so exactly ONE row (no phantom empty rows for the other four).
    assert [row["key"] for row in body["rows"]] == ["priprava"]
    assert "totals" in body and "manager" in body
    # the assumption block the screen must display
    assert body["currency"] == "EUR"
    assert "coefficient_minutes_per_mtok" in body and "wages" in body

    assert client.get("/api/v1/projects/does-not-exist/metrics").status_code == 404


def test_external_cost_post_rejects_foreign_version_and_negative_tokens(db_session):
    """Case 7: the two 422 gates — a ``version_id`` from ANOTHER project (validated in the router, it is
    deliberately not a DB constraint) and a negative token count."""
    user = _make_user(db_session)
    project = _make_project(db_session, user)
    other_project = _make_project(db_session, user)
    other_version = _make_version(db_session, other_project)
    client = _client(db_session, user)
    url = f"/api/v1/projects/{project.slug}/external-costs"
    payload = {
        "occurred_on": "2026-07-01",
        "description": "Dedo v termináli",
        "model": "claude-opus-5",
        "input_tokens": 2000,
        "output_tokens": 1000,
    }

    assert client.post(url, json=payload).status_code == 201

    foreign = {**payload, "version_id": str(other_version.id)}
    assert client.post(url, json=foreign).status_code == 422

    negative = {**payload, "input_tokens": -1}
    assert client.post(url, json=negative).status_code == 422


def test_external_cost_patch_rejects_explicit_null_on_a_required_field(db_session):
    """``ExternalCostUpdate`` types every field ``Optional`` to express "not supplied", so an explicit
    JSON ``null`` for a NOT NULL column would reach the DB and 500 on an IntegrityError. It is a bad
    request → 422. ``version_id`` is the deliberate exception: ``null`` there is a real instruction
    (move the entry back to the project level)."""
    user = _make_user(db_session)
    project = _make_project(db_session, user)
    version = _make_version(db_session, project)
    client = _client(db_session, user)
    url = f"/api/v1/projects/{project.slug}/external-costs"
    created = client.post(
        url,
        json={
            "occurred_on": "2026-07-01",
            "description": "Dedo v termináli",
            "model": "claude-opus-5",
            "input_tokens": 2000,
            "output_tokens": 1000,
            "version_id": str(version.id),
        },
    )
    assert created.status_code == 201, created.text
    entry_url = f"{url}/{created.json()['id']}"

    for field in ("occurred_on", "description", "model", "input_tokens", "output_tokens"):
        r = client.patch(entry_url, json={field: None})
        assert r.status_code == 422, f"{field}: {r.status_code} {r.text}"

    # the entry is untouched by the rejected calls…
    assert client.patch(entry_url, json={}).json()["description"] == "Dedo v termináli"
    # …and an explicit null version_id still works — it moves the entry to the project level
    moved = client.patch(entry_url, json={"version_id": None})
    assert moved.status_code == 200, moved.text
    assert moved.json()["version_id"] is None


# ── migration 086 ────────────────────────────────────────────────────────────

#: The five per-phase coefficients migration 086 collapses — deleted on upgrade, re-inserted on
#: downgrade carrying the value the collapsed key still holds (the calibration is never destroyed).
_MIGRATION_086_RETIRED_COEFFICIENTS = tuple(f"metrics_minutes_per_mtok_{p}" for p in COMPARISON_PHASES)
#: Dead on arrival — nothing reads it, so a downgrade restores it merely UNSET.
_MIGRATION_086_DEAD = ("developer_hourly_rate",)
#: Everything the upgrade DELETES.
_MIGRATION_086_RETIRED = _MIGRATION_086_RETIRED_COEFFICIENTS + _MIGRATION_086_DEAD
#: The keys migration 086 INSERTS on upgrade and deletes on downgrade — (key, seeded value).
_MIGRATION_086_ADDED = (("metrics_minutes_per_mtok", "600"), ("metrics_hourly_wage_externe", "0.0"))
#: A value the Manažér could have calibrated AFTER the upgrade — the downgrade must hand exactly this
#: back to the five per-phase keys, proving it reads the live row instead of re-seeding a constant.
_MIGRATION_086_CALIBRATED = "777"


def _test_database_url() -> str:
    """The RUN-SCOPED test database URL (``tests.conftest``) — the throwaway ``…_mig086`` below is derived
    from it, and a base name here would give two concurrent runs the same throwaway database (audit
    2026-08-23, finding 6)."""
    from tests.conftest import _get_test_database_url

    return _ensure_pg8000_driver(_get_test_database_url())


def _drop_database_if_exists(admin_url: str, db_name: str) -> None:
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": db_name},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))  # noqa: S608
    finally:
        engine.dispose()


def _create_clean_database(admin_url: str, db_name: str) -> None:
    _drop_database_if_exists(admin_url, db_name)
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))  # noqa: S608
    finally:
        engine.dispose()


def _setting_rows(engine, key: str) -> list[str]:
    with engine.connect() as conn:
        return [row[0] for row in conn.execute(text("SELECT value FROM system_settings WHERE key = :k"), {"k": key})]


def _table_exists(engine, table: str) -> bool:
    with engine.connect() as conn:
        return conn.execute(text("SELECT to_regclass(:t)"), {"t": f"public.{table}"}).scalar() is not None


def _external_cost_version_fk_action(engine) -> str:
    """``pg_constraint.confdeltype`` of ``external_cost.version_id`` — ``'n'`` = SET NULL, ``'c'`` =
    CASCADE. Read from the DB, not from the model: the schema the app runs on is the migrated one."""
    with engine.connect() as conn:
        return str(
            conn.execute(
                text(
                    "SELECT c.confdeltype FROM pg_constraint c "
                    "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1] "
                    "WHERE c.conrelid = 'public.external_cost'::regclass AND c.contype = 'f' "
                    "AND a.attname = 'version_id'"
                )
            ).scalar()
        )


def _assert_086_applied(engine) -> None:
    """After an upgrade: the table exists, each added key has EXACTLY one row, no retired key survives."""
    assert _table_exists(engine, "external_cost")
    for key, value in _MIGRATION_086_ADDED:
        assert _setting_rows(engine, key) == [value], key  # exactly one row, seeded value — no duplicates
    for key in _MIGRATION_086_RETIRED:
        assert _setting_rows(engine, key) == [], key  # no orphan left behind


def test_migration_086_up_down_up_leaves_no_orphan_rows(monkeypatch):
    """Case 8: 086 applied to a DB that still holds the five per-phase coefficients, then reversed, then
    re-applied — no orphan rows either way (the guards are ``WHERE NOT EXISTS`` / ``IF EXISTS``, so a
    re-run must be a no-op rather than a duplicate or an error), the ``version_id`` FK is SET NULL, and
    the downgrade returns the CALIBRATED coefficient to the five per-phase keys instead of wiping it."""
    from alembic import command
    from alembic.config import Config

    base_url = _test_database_url()
    prefix, name = base_url.rsplit("/", 1)
    admin_url = prefix + "/postgres"
    mig_db_name = name.split("?")[0] + "_mig086"
    mig_db_url = prefix + "/" + mig_db_name

    _create_clean_database(admin_url, mig_db_name)

    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    # ``migrations/env.py`` honours an explicit ``-x url=…`` over settings.database_url, so the whole
    # chain runs against the throwaway DB and never the developer's real test DB.
    config.cmd_opts = type("opts", (), {"x": [f"url={mig_db_url}"]})()

    engine = None
    try:
        command.upgrade(config, "085")
        engine = create_engine(mig_db_url)

        # The pre-CR state this CR has to clean up: the five per-phase coefficients + the dead rate.
        with engine.connect() as conn:
            for key in _MIGRATION_086_RETIRED:
                conn.execute(
                    text("INSERT INTO system_settings (key, value, value_type) VALUES (:k, '70', 'float')"),
                    {"k": key},
                )
            conn.commit()

        command.upgrade(config, "086")
        _assert_086_applied(engine)
        assert _external_cost_version_fk_action(engine) == "n"  # SET NULL — a deleted version never
        # erases hand-entered money from the project total (CASCADE would be silent data loss)

        # The Manažér re-calibrates the collapsed coefficient AFTER the upgrade — the downgrade has to
        # give THAT number back, not a hardcoded constant.
        with engine.connect() as conn:
            conn.execute(
                text("UPDATE system_settings SET value = :v WHERE key = 'metrics_minutes_per_mtok'"),
                {"v": _MIGRATION_086_CALIBRATED},
            )
            conn.commit()

        command.downgrade(config, "085")
        assert not _table_exists(engine, "external_cost")
        for key, _ in _MIGRATION_086_ADDED:
            assert _setting_rows(engine, key) == [], key
        for key in _MIGRATION_086_RETIRED_COEFFICIENTS:
            # the collapsed value comes BACK into each per-phase key — a downgrade must not destroy the
            # calibrated coefficient (re-seeding '0.0' would)
            assert _setting_rows(engine, key) == [_MIGRATION_086_CALIBRATED], key
        for key in _MIGRATION_086_DEAD:
            # nothing reads it and there is no value to restore → UNSET
            assert _setting_rows(engine, key) == ["0.0"], key

        command.upgrade(config, "086")
        _assert_086_applied(engine)
    finally:
        if engine is not None:
            engine.dispose()
        _drop_database_if_exists(admin_url, mig_db_name)


def test_a_turn_that_ran_without_any_recorded_spend_makes_the_total_incomplete(db_session):
    """Ťah bežal (má trvanie), ale jeho spotreba sa nezaznamenala vôbec (zlyhal pred v4.43.0, alebo sa záznam sedenia
    nedal prečítať) — jeho cena nie je nula, je neznáma; súčet nesmie vyzerať úplný (nález kontroly ICCINT-168)."""
    _price_list(db_session)
    user = _make_user(db_session)
    project = _make_project(db_session, user)
    version = _make_version(db_session, project)
    _msg(db_session, version.id, "ai_agent", "programovanie", in_tok=1000, out_tok=500, dur=10.0, phase="programovanie")
    failed = _msg(
        db_session, version.id, "system", "programovanie", in_tok=0, out_tok=0, dur=40.0, phase="programovanie"
    )
    failed.payload = {**failed.payload, "usage": None}
    db_session.flush()

    m = metrics_service.compute_project_metrics(db_session, project)
    row = _rows(m)["programovanie"]
    assert row.agent_cost == 11
    assert [(u.reason, u.turns) for u in row.unpriced] == [(metrics_service.UNKNOWN_SPEND_REASON, 1)]
    assert m.totals.agent_cost_complete is False


def test_the_project_never_shows_less_than_its_versions(db_session):
    """Každá verzia zaokrúhľuje nahor sama: 10,5 € → 11 v oboch. Projekt preto ukáže 22, nie ⌈21⌉ = 21 —
    a Poradca o celom projekte (žiadna verzia) pribudne k tomu zvlášť, tiež nahor."""
    from backend.db.models.poradca import PoradcaConversation, PoradcaMessage

    _price_list(db_session)
    user = _make_user(db_session)
    project = _make_project(db_session, user)
    for number in ("1.0.0", "1.1.0"):
        version = _make_version(db_session, project, version_number=number)
        _msg(
            db_session,
            version.id,
            "ai_agent",
            "programovanie",
            in_tok=1000,
            out_tok=500,
            dur=1.0,
            phase="programovanie",
        )
    conversation = PoradcaConversation(
        project_id=project.id, author_id=user.id, title="t", claude_session_id=uuid.uuid4()
    )
    db_session.add(conversation)
    db_session.flush()
    part = {"model": "m", "input_tokens": 100, "output_tokens": 10, "cache_read_tokens": 0, "cache_write_tokens": 0}
    db_session.add(
        PoradcaMessage(
            conversation_id=conversation.id,
            author="poradca",
            status="done",
            usage={"input_tokens": 100, "output_tokens": 10, "model": "m", "parts": [{**part, "cost_usd": None}]},
        )
    )
    db_session.flush()

    m = metrics_service.compute_project_metrics(db_session, project)
    assert [v.totals.agent_cost_total for v in m.by_version] == [11, 11]
    assert _rows(m)["programovanie"].agent_cost == 22
    assert _rows(m)["poradca"].agent_cost == 1  # (100×3000 + 10×15000)/1e6 = 0,45 € → 1, len na úrovni projektu
    assert m.totals.agent_cost_total == 23


def test_web_searches_are_priced_by_the_list_or_named(db_session):
    """Vyhľadávanie na webe Claude Code účtuje zvlášť od tokenov — cenník ho má ako vlastnú cenu; kým ju nepozná,
    vyhľadávania sú pomenované a tokeny ostávajú ocenené."""
    row = _price_list(db_session)
    user = _make_user(db_session)
    project = _make_project(db_session, user)
    version = _make_version(db_session, project)
    m = _msg(
        db_session, version.id, "ai_agent", "programovanie", in_tok=1000, out_tok=500, dur=1.0, phase="programovanie"
    )
    part = m.payload["usage"]["parts"][0]
    m.payload = {**m.payload, "usage": {**m.payload["usage"], "parts": [{**part, "web_search_requests": 3}]}}
    db_session.flush()

    unpriced = _rows(metrics_service.compute_project_metrics(db_session, project))["programovanie"]
    assert unpriced.agent_cost == 11  # tokeny 10,5 €
    assert [(u.reason, u.turns) for u in unpriced.unpriced] == [
        (metrics_service.NO_SEARCH_PRICE_REASON.format(model="m"), 3)
    ]
    row.web_search_usd = 0.5
    db_session.flush()
    priced = metrics_service.compute_project_metrics(db_session, project)
    assert _rows(priced)["programovanie"].agent_cost == 12  # 10,5 + 3 × 0,5 = 12 €
    assert priced.price_list[0].web_search_usd == 0.5
