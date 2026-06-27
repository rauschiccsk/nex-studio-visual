"""Integration tests for the v2 Fast-Fix Lane short path (CR-V2-028).

OQ-3 resolved: fast-fix is autonomous ONLY through the build to "verified" — it does NOT
auto-deploy. The patch then flows through the normal MANUAL per-customer Nasadiť (UAT/PROD tabs,
CR-V2-027). These tests pin the load-bearing rules of that resolution against the real v2 DB:

  * **Entry** — a fast-fix directive creates a PATCH version (``vX.Y.Z+1``) and starts a
    ``fast_fix`` pipeline whose first phase is Príprava (the directive rides in as the kickoff content).
  * **Short path** — ``ensure_build_task`` is re-targeted onto the v2 short path: the single Task is
    materialized at the START of the Programovanie round (Návrh is skipped) from the kickoff directive.
  * **Lightweight Príprava** — fast-fix Príprava AUTO-CONTINUES to Programovanie (no ``approve_spec``
    stop — there is no Špecifikácia to approve; zero mid-flight approvals), while a ``new_version``
    Príprava ALWAYS stops at ``approve_spec`` (D3, dial-independent).
  * **Light Auditor check** — the Verifikácia brief is the focused fix-works + no-regression check for
    fast-fix, the full adversarial release oracle for a ``new_version``.
  * **Stops at verified, never auto-deploys** — the lane reaches the verified/Hotovo boundary WITHOUT
    any in-pipeline deploy: the retired ``_fast_fix_auto_deploy`` is gone and the lane makes no deploy
    call; the verified patch appears in the deploy matrix for the manual Nasadiť.
"""

from __future__ import annotations

import uuid as _uuid

import pytest
from sqlalchemy import select

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version
from backend.services import fast_fix, orchestrator
from backend.services.pipeline_status import PipelineStatusBlock

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _seed_user(db) -> User:
    u = User(
        username=f"ff_{_uuid.uuid4().hex[:8]}",
        email=f"ff_{_uuid.uuid4().hex[:8]}@test.local",
        password_hash="x",
        role="ri",
    )
    db.add(u)
    db.flush()
    return u


def _seed_project(db, *, creator: User) -> Project:
    suffix = _uuid.uuid4().hex[:8]
    project = Project(
        name=f"Fast-Fix Proj {suffix}",
        slug=f"fast-fix-{suffix}",
        type="standard",
        auth_mode="password",
        description="CR-V2-028 fast-fix short-path test project.",
        created_by=creator.id,
    )
    db.add(project)
    db.flush()
    return project


def _seed_base_version(db, project: Project, version_number: str = "v1.2.3") -> Version:
    """A pre-existing version so ``create_patch_version`` has a semver base to bump."""
    version = Version(project_id=project.id, version_number=version_number, status="active")
    db.add(version)
    db.flush()
    return version


def _fast_fix_block(*, kind: str = "done", commits=None, deliverables=None, verdict=None) -> PipelineStatusBlock:
    return PipelineStatusBlock(
        stage="programovanie",
        kind=kind,
        summary="ok",
        awaiting="manazer",
        commits=commits or [],
        deliverables=deliverables or [],
        verdict=verdict,
    )


# ---------------------------------------------------------------------------
# 1. Entry: directive → patch version → fast_fix pipeline at Príprava
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fast_fix_start_creates_patch_version_at_priprava(db_session) -> None:
    """A fast-fix directive bumps the PATCH version and starts a ``fast_fix`` pipeline whose first phase
    is Príprava, carrying the directive in the kickoff message payload."""
    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator)
    _seed_base_version(db_session, project, "v0.4.9")

    patch = fast_fix.create_patch_version(db_session, project_id=project.id, user_id=creator.id)
    assert patch.version_number == "v0.4.10"  # semver bump, NOT lexicographic
    assert patch.name == "Rýchla oprava"

    state = await orchestrator.apply_action(
        db_session,
        version_id=patch.id,
        action="start",
        payload={"flow_type": "fast_fix", "directive": "Rename label Firmy → Dodávatelia"},
    )
    assert state.flow_type == "fast_fix"
    assert state.current_stage == "priprava"
    assert state.status == "agent_working"

    kickoff = db_session.execute(
        select(PipelineMessage).where(
            PipelineMessage.version_id == patch.id,
            PipelineMessage.kind == "kickoff",
        )
    ).scalar_one()
    assert kickoff.stage == "priprava" and kickoff.author == "manazer"
    assert kickoff.payload["directive"] == "Rename label Firmy → Dodávatelia"


# ---------------------------------------------------------------------------
# 2. ensure_build_task re-targeted onto the v2 short path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_build_task_reads_v2_kickoff_directive(db_session) -> None:
    """``ensure_build_task`` materializes ONE Task whose brief is the v2 kickoff directive (re-keyed to
    the ``stage=priprava``/``author=manazer`` tokens ``apply_action('start')`` records — NOT the v1
    ``kickoff``/``director`` tokens the v2 DB CHECK rejects)."""
    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator)
    _seed_base_version(db_session, project, "v1.0.0")
    patch = fast_fix.create_patch_version(db_session, project_id=project.id, user_id=creator.id)
    await orchestrator.apply_action(
        db_session,
        version_id=patch.id,
        action="start",
        payload={"flow_type": "fast_fix", "directive": "Fix the IBAN validator off-by-one"},
    )

    # The re-keyed reader sees the v2 kickoff directive.
    assert fast_fix.kickoff_directive(db_session, patch.id) == "Fix the IBAN validator off-by-one"

    task = fast_fix.ensure_build_task(db_session, patch.id)
    assert task.description == "Fix the IBAN validator off-by-one"
    assert task.title == "Fix the IBAN validator off-by-one"

    # Idempotent: a second call reuses the same Task (a re-entry into build), never a duplicate.
    again = fast_fix.ensure_build_task(db_session, patch.id)
    assert again.id == task.id
    count = (
        db_session.execute(
            select(Task)
            .join(Feat, Feat.id == Task.feat_id)
            .join(Epic, Epic.id == Feat.epic_id)
            .where(Epic.version_id == patch.id)
        )
        .scalars()
        .all()
    )
    assert len(count) == 1


# ---------------------------------------------------------------------------
# 3. Lightweight Príprava auto-continues (no approve_spec) for fast-fix only
# ---------------------------------------------------------------------------


def _settled_priprava_state(db, *, flow_type: str) -> PipelineState:
    creator = _seed_user(db)
    project = _seed_project(db, creator=creator)
    version = Version(project_id=project.id, version_number="v2.0.0", status="active")
    db.add(version)
    db.flush()
    state = PipelineState(
        version_id=version.id,
        flow_type=flow_type,
        current_stage="priprava",
        current_actor="ai_agent",
        status="agent_working",
    )
    db.add(state)
    db.flush()
    return state


def test_priprava_boundary_fast_fix_auto_continues_to_programovanie(db_session) -> None:
    """A fast-fix produces no Špecifikácia, so its Príprava AUTO-CONTINUES to Programovanie (zero
    mid-flight approvals, design §2.4/§2.5) — ``_settle_phase_boundary`` returns True and advances."""
    state = _settled_priprava_state(db_session, flow_type="fast_fix")
    advanced = orchestrator._settle_phase_boundary(db_session, state)
    assert advanced is True
    assert state.current_stage == "programovanie"
    assert state.status == "agent_working"


def test_priprava_boundary_new_version_always_stops_at_approve_spec(db_session) -> None:
    """A ``new_version`` Príprava ALWAYS stops for the mandatory Špecifikácia approval (D3,
    dial-independent) — ``_settle_phase_boundary`` returns False and does NOT advance."""
    state = _settled_priprava_state(db_session, flow_type="new_version")
    advanced = orchestrator._settle_phase_boundary(db_session, state)
    assert advanced is False
    assert state.current_stage == "priprava"  # held for approve_spec


@pytest.mark.asyncio
async def test_run_dispatch_fast_fix_priprava_auto_advances_without_spec(db_session, monkeypatch) -> None:
    """Through the real ``run_dispatch``: a fast-fix Príprava that closes with a gate_report (and writes
    NO Špecifikácia) does NOT trip the spec-artifact gate and AUTO-ADVANCES to Programovanie — zero
    mid-flight approvals, no ``specification.md`` required (CR-V2-028)."""
    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator)
    _seed_base_version(db_session, project, "v1.0.0")
    patch = fast_fix.create_patch_version(db_session, project_id=project.id, user_id=creator.id)
    state = await orchestrator.apply_action(
        db_session,
        version_id=patch.id,
        action="start",
        payload={"flow_type": "fast_fix", "directive": "Tighten the rate-limit window"},
    )
    assert state.current_stage == "priprava"

    async def _fake_invoke(db, **kw):
        # A Príprava gate_report with NO deliverables (no specification.md written) — the fast-fix case.
        return PipelineStatusBlock(stage="priprava", kind="gate_report", summary="ack", awaiting="manazer")

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _fake_invoke)
    monkeypatch.setattr(orchestrator, "_repo_head", lambda _root: "deadbeef")

    settled = await orchestrator.run_dispatch(db_session, patch.id)
    # The spec-artifact gate did NOT fire (no blocked); the lane advanced straight to Programovanie.
    assert settled.status == "agent_working"
    assert settled.current_stage == "programovanie"


# ---------------------------------------------------------------------------
# 4. Flow-type-aware briefs: lightweight Príprava + light Auditor verifikácia
# ---------------------------------------------------------------------------


def test_priprava_directive_is_lightweight_for_fast_fix(db_session) -> None:
    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator)
    version = Version(project_id=project.id, version_number="v3.0.0", status="active")
    db_session.add(version)
    db_session.flush()

    ff = orchestrator._priprava_directive(db_session, version.id, flow_type="fast_fix")
    assert "RÝCHLA OPRAVA" in ff and "ĽAHKÁ" in ff
    assert "NEZAPISUJ Špecifikáciu" in ff  # no heavy spec dialogue / no spec artifact

    full = orchestrator._priprava_directive(db_session, version.id, flow_type="new_version")
    assert "Špecifikáci" in full and "objasňujúce otázky" in full  # the full interactive dialogue


def test_verifikacia_directive_is_light_for_fast_fix(db_session) -> None:
    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator)
    version = Version(project_id=project.id, version_number="v4.0.0", status="active")
    db_session.add(version)
    db_session.flush()

    light = orchestrator._verifikacia_directive(db_session, version.id, flow_type="fast_fix")
    assert "ĽAHKÁ" in light and "NIE plný release oracle" in light
    assert "OPRAVA FUNGUJE" in light and "ŽIADNA REGRESIA" in light
    # The light check STILL emits a verdict + STILL verifies §4 hard-security (just lighter).
    assert "kind=verdict" in light and "§4 HARD-SECURITY" in light

    full = orchestrator._verifikacia_directive(db_session, version.id, flow_type="new_version")
    assert "ADVERZARIÁLNE SPOT-CHECKY" in full  # the full release oracle


# ---------------------------------------------------------------------------
# 5. Build round materializes the fast-fix task + reaches verified WITHOUT deploy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_round_materializes_fast_fix_task_and_runs_it(db_session, monkeypatch) -> None:
    """The Programovanie round materializes the single fast-fix Task (Návrh was skipped) and runs the
    self-checking loop on it. With the agent + mechanical verify faked (no git/docker), the one task is
    marked done and the (plna) dial auto-signs the boundary forward to Verifikácia."""
    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator)
    _seed_base_version(db_session, project, "v1.0.0")
    patch = fast_fix.create_patch_version(db_session, project_id=project.id, user_id=creator.id)
    state = await orchestrator.apply_action(
        db_session,
        version_id=patch.id,
        action="start",
        payload={"flow_type": "fast_fix", "directive": "Bump retry timeout to 30s"},
    )
    # Move the (already-started, agent_working) state into Programovanie as the lane would after Príprava.
    state.current_stage = "programovanie"
    state.current_actor = "ai_agent"
    state.status = "agent_working"
    db_session.flush()

    # Fake the agent turn (a clean done block) + the deterministic gates so no git/docker is spawned.
    async def _fake_invoke(db, **kw):
        return _fast_fix_block(kind="done")

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _fake_invoke)
    monkeypatch.setattr(orchestrator, "_repo_head", lambda _root: "deadbeef")
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda *a, **k: None)  # task passes

    settled = await orchestrator._run_build_round(db_session, state)

    # The single fast-fix task was materialized AND marked done.
    tasks = (
        db_session.execute(
            select(Task)
            .join(Feat, Feat.id == Task.feat_id)
            .join(Epic, Epic.id == Feat.epic_id)
            .where(Epic.version_id == patch.id)
        )
        .scalars()
        .all()
    )
    assert len(tasks) == 1
    assert tasks[0].status == "done"
    # fast-fix dial = plna → the Programovanie boundary auto-continues to Verifikácia (no Manažér stop).
    assert settled.current_stage == "verifikacia"
    assert settled.status == "agent_working"


# ---------------------------------------------------------------------------
# 6. The retired auto-deploy + the verified-not-deployed boundary
# ---------------------------------------------------------------------------


def test_fast_fix_auto_deploy_is_retired() -> None:
    """OQ-3: the legacy in-lane ``_fast_fix_auto_deploy`` is RETIRED — it no longer exists as a function
    on the orchestrator (the patch deploys via the normal manual per-customer Nasadiť, CR-V2-027)."""
    assert not hasattr(orchestrator, "_fast_fix_auto_deploy")


def test_fast_fix_dial_is_full_auto_through_verification(db_session) -> None:
    """The fast-fix lane runs full-auto (``plna``) regardless of any per-project / global dial — so it
    reaches verified with zero mid-flight approvals (the carve-out in ``resolve_miera_autonomie``)."""
    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator)
    project.miera_autonomie = "po_kazdej_faze"  # the MOST-stops setting
    version = Version(project_id=project.id, version_number="v5.0.0", status="active")
    db_session.add(version)
    db_session.flush()
    db_session.add(
        PipelineState(
            version_id=version.id,
            flow_type="fast_fix",
            current_stage="programovanie",
            current_actor="ai_agent",
            status="agent_working",
        )
    )
    db_session.flush()
    # Even with the project pinned to "stop after every phase", fast-fix forces plna.
    assert orchestrator.resolve_miera_autonomie(db_session, version.id) == "plna"
    # And no dial-governed boundary stops at plna.
    assert orchestrator.dial_stops_at("plna", "programovanie") is False
    assert orchestrator.dial_stops_at("plna", "verifikacia") is False


def test_fast_fix_stage_order_stops_at_done_no_deploy() -> None:
    """The lane STOPS at the verified/``done`` boundary — there is no deploy phase in the lane's stage
    order (deploy is OUT of the pipeline, OQ-3/D6)."""
    assert orchestrator.FAST_FIX_STAGE_ORDER[-1] == "done"
    assert "deploy" not in orchestrator.FAST_FIX_STAGE_ORDER
    # ``deploy`` is an always-stop carve-out, but it lives in the per-customer deploy subsystem, never
    # in the lane's phase path — so the lane can never auto-advance INTO a deploy.
    assert "deploy" in orchestrator.ALWAYS_STOP_BOUNDARIES
    assert "deploy" not in orchestrator.DIAL_GOVERNED_BOUNDARIES
