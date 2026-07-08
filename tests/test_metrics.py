"""Per-phase project metrics / ROI backend (E5; v2 metrics per-phase basis, CR-V2-029).

Covers: the Manažér-wait accumulation listener (enter/exit/total, wait→wait keep, no-accum on a
non-wait set); the per-PHASE aggregation + phase-stamp attribution; per-model pricing; the agent-vs-
human cost + ROI when prices/rates/wages are unset (→ null, never fabricated) and when configured; the
endpoint + 404; the per-phase settings keys (+ the v1 per-role keys retired).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes.metrics import router as metrics_router
from backend.core.security import get_current_user
from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.system_settings import SystemSetting
from backend.db.models.versions import Version
from backend.db.session import get_db
from backend.services import metrics as metrics_service
from backend.services import system_setting
from backend.services.metrics import COMPARISON_PHASES

# ── helpers ─────────────────────────────────────────────────────────────────


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


def _msg(db_session, version_id, author, stage, *, in_tok, out_tok, dur, model="m", phase=None):
    payload: dict[str, Any] = {
        "usage": {"input_tokens": in_tok, "output_tokens": out_tok, "model": model},
        "timing": {"duration_seconds": dur, "parse_attempts": 1},
    }
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


def _set(db_session, key, value):
    db_session.add(SystemSetting(key=key, value=str(value), value_type="float"))
    db_session.flush()


def _client(db_session, current):
    app = FastAPI()
    app.include_router(metrics_router, prefix="/api/v1")

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = lambda: current
    return TestClient(app)


# ── settings keys ────────────────────────────────────────────────────────────


def test_pricing_settings_keys_present(db_session):
    flat = ("developer_hourly_rate", "api_price_input_per_mtok", "api_price_output_per_mtok")
    per_phase = [f"metrics_minutes_per_mtok_{p}" for p in COMPARISON_PHASES]
    per_phase += [f"metrics_hourly_wage_{p}" for p in COMPARISON_PHASES]
    families = tuple(
        f"api_price_{d}_per_mtok_{fam}" for d in ("input", "output") for fam in ("opus", "sonnet", "haiku")
    )
    for key in (*flat, *per_phase, *families):
        assert key in system_setting.DEFAULT_SETTINGS, key
        assert system_setting.DEFAULT_SETTINGS[key].value_type == "float"
        assert system_setting.DEFAULT_SETTINGS[key].value == "0.0" or key.startswith("developer") or key in flat
    # the 4 per-phase rate + 4 per-phase wage keys exist (8 total)
    assert len(per_phase) == 8
    system_setting._cache.clear()
    # every new per-phase metrics key resolves to 0.0 (unset) without a seed row
    assert system_setting.get_float(db_session, "metrics_minutes_per_mtok_navrh") == 0.0
    assert system_setting.get_float(db_session, "metrics_hourly_wage_verifikacia") == 0.0


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
    system_setting._cache.clear()
    assert system_setting.get_float_or_none(db_session, "metrics_hourly_wage_programovanie") is None  # no row
    _set(db_session, "metrics_hourly_wage_programovanie", "0.0")  # explicit 0
    assert system_setting.get_float_or_none(db_session, "metrics_hourly_wage_programovanie") == 0.0
    system_setting._cache.clear()


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
    # zero-token phases (priprava / verifikacia here) are DROPPED (no phantom empty rows).
    assert [r.phase for r in v.by_phase] == ["navrh", "programovanie"]
    rows = {r.phase: r for r in v.by_phase}
    assert rows["programovanie"].input_tokens == 1000
    assert rows["navrh"].input_tokens == 2000
    assert "priprava" not in rows  # zero-token phase → no row
    assert "verifikacia" not in rows
    # footing preserved: the emitted phase rows still sum to the version grand total
    assert sum(r.input_tokens for r in v.by_phase) == v.usage.input_tokens
    # no un-phased system-authored messages → the engine-overhead row foots at 0
    assert v.system_overhead.input_tokens == 0


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
    rows = {r.phase: r for r in m.by_version[0].by_phase}
    assert rows["programovanie"].input_tokens == 300  # stamp wins over stage
    assert rows["priprava"].input_tokens == 10  # un-stamped system note → its stage bucket
    # no message carried the "system" bucket key (no system-stamped, un-staged record) → system row 0
    assert m.by_version[0].system_overhead.input_tokens == 0


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


# ── cost + ROI: unconfigured → null (never fabricated) ───────────────────────


def test_metrics_unconfigured_returns_nulls(db_session, monkeypatch):
    monkeypatch.setattr(metrics_service.settings, "api_price_input_per_mtok", 0.0)
    monkeypatch.setattr(metrics_service.settings, "api_price_output_per_mtok", 0.0)
    monkeypatch.setattr(metrics_service.settings, "developer_hourly_rate", 0.0)
    system_setting._cache.clear()

    user = _make_user(db_session)
    project = _make_project(db_session, user)
    version = _make_version(db_session, project)
    _msg(db_session, version.id, "ai_agent", "programovanie", in_tok=1000, out_tok=500, dur=10.0, phase="programovanie")

    m = metrics_service.compute_project_metrics(db_session, project)
    prog = {r.phase: r for r in m.by_version[0].by_phase}["programovanie"]
    assert prog.agent_cost is None  # "m" → _unknown, flat unset → unpriced
    assert prog.human_cost is None
    assert prog.x_faster is None and prog.m_cheaper is None and prog.eur_saved is None
    roi = m.roi
    assert roi.agent_cost_total is None
    assert roi.human_cost_total is None
    assert roi.x_faster is None and roi.m_cheaper is None and roi.eur_saved is None
    assert roi.configured is False
    assert roi.pricing_configured is False
    assert roi.rates_configured is False
    assert roi.wages_configured is False


# ── cost + ROI: configured → computed ────────────────────────────────────────


def test_metrics_configured_computes_cost_and_roi(db_session, monkeypatch):
    monkeypatch.setattr(metrics_service.settings, "api_price_input_per_mtok", 0.0)
    monkeypatch.setattr(metrics_service.settings, "api_price_output_per_mtok", 0.0)
    monkeypatch.setattr(metrics_service.settings, "developer_hourly_rate", 0.0)
    _set(db_session, "api_price_input_per_mtok", "3.0")
    _set(db_session, "api_price_output_per_mtok", "15.0")
    _set(db_session, "metrics_minutes_per_mtok_programovanie", "240")
    _set(db_session, "metrics_minutes_per_mtok_navrh", "520")
    _set(db_session, "metrics_hourly_wage_programovanie", "60")
    _set(db_session, "metrics_hourly_wage_navrh", "100")
    system_setting._cache.clear()

    user = _make_user(db_session)
    project = _make_project(db_session, user)
    version = _make_version(db_session, project)
    _msg(db_session, version.id, "ai_agent", "programovanie", in_tok=1000, out_tok=500, dur=10.0, phase="programovanie")
    _msg(db_session, version.id, "ai_agent", "navrh", in_tok=2000, out_tok=800, dur=20.0, phase="navrh")

    m = metrics_service.compute_project_metrics(db_session, project)
    rows = {r.phase: r for r in m.by_version[0].by_phase}

    # programovanie: agent (1000×3 + 500×15)/1e6 = 0.0105; human 1500/1e6×240 = 0.36 min → /60×60 = 0.36
    assert rows["programovanie"].agent_cost == pytest.approx(0.0105)
    assert rows["programovanie"].human_minutes == pytest.approx(0.36)
    assert rows["programovanie"].human_cost == pytest.approx(0.36)
    # navrh: agent (2000×3 + 800×15)/1e6 = 0.018
    assert rows["navrh"].agent_cost == pytest.approx(0.018)

    roi = m.roi
    assert roi.agent_cost_total == pytest.approx(0.0285)  # 0.0105 + 0.018 (other phases 0 tokens)
    assert roi.human_cost_total == pytest.approx(0.36 + 1.456 / 60 * 100)
    # speed: human-minutes (0.36 + 1.456) vs agent ACTIVE minutes (30/60 = 0.5)
    assert roi.x_faster == pytest.approx(1.816 / 0.5)
    assert roi.m_cheaper == pytest.approx(roi.human_cost_total / 0.0285)
    assert roi.eur_saved == pytest.approx(roi.human_cost_total - 0.0285)
    assert roi.configured is True
    assert roi.covered_versions == 1 and roi.total_versions == 1
    # model "m" is unrecognized → priced flat, surfaced as 100% unknown-model tokens
    assert roi.unknown_model_token_pct == pytest.approx(100.0)

    system_setting._cache.clear()  # the cache is process-global + survives rollback — leave it clean


def test_metrics_per_model_family_pricing(db_session, monkeypatch):
    """Per-family price prices a named family; an un-named/unkeyed model with no flat fallback is left
    unpriced (agent_cost None + the key surfaced for the per-row badge)."""
    monkeypatch.setattr(metrics_service.settings, "api_price_input_per_mtok", 0.0)
    monkeypatch.setattr(metrics_service.settings, "api_price_output_per_mtok", 0.0)
    _set(db_session, "api_price_input_per_mtok_opus", "5.0")
    _set(db_session, "api_price_output_per_mtok_opus", "25.0")
    system_setting._cache.clear()

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
    rows = {r.phase: r for r in m.by_version[0].by_phase}
    assert rows["programovanie"].agent_cost == pytest.approx((1000 * 5 + 400 * 25) / 1e6)  # 0.015
    assert rows["navrh"].agent_cost is None  # _unknown, no flat fallback → unpriced
    assert "claude-zeta-9" in rows["navrh"].unpriced_model_keys
    # headline agent cost is None — the comparison set is not fully priced (honest, not a partial)
    assert m.roi.agent_cost_total is None
    system_setting._cache.clear()


# ── endpoint ─────────────────────────────────────────────────────────────────


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
    # priprava turn, so exactly ONE phase row (no phantom empty rows for the other three).
    assert [p["phase"] for p in body["by_phase"]] == ["priprava"]
    assert "system_overhead" in body and "manager" in body and "roi" in body
    assert body["roi"]["total_versions"] == 1

    assert client.get("/api/v1/projects/does-not-exist/metrics").status_code == 404
