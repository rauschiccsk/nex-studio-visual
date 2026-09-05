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


@pytest.mark.asyncio
async def test_ensure_build_task_multiline_directive_titles_from_first_line(db_session) -> None:
    """A MULTI-line directive → the Task title is the trimmed FIRST line while the description keeps the
    full multi-line text, and ``task_type`` defaults to ``backend`` (the neutral default).

    Covers the v1 ``test_ensure_build_task_materializes_from_directive`` nuance the single-line short-path
    test never exercised (single-line → title==description so the first-line trim was untested)."""
    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator)
    _seed_base_version(db_session, project, "v1.0.0")
    patch = fast_fix.create_patch_version(db_session, project_id=project.id, user_id=creator.id)
    directive = (
        "  Fix the IBAN validator off-by-one  \nIt drops the last check digit on 24-char IBANs.\nAdd a regression test."
    )
    await orchestrator.apply_action(
        db_session,
        version_id=patch.id,
        action="start",
        payload={"flow_type": "fast_fix", "directive": directive},
    )

    task = fast_fix.ensure_build_task(db_session, patch.id)
    # Title = the trimmed FIRST non-empty line; description keeps the FULL multi-line directive.
    assert task.title == "Fix the IBAN validator off-by-one"
    assert task.description == directive
    assert "regression test" in task.description  # later lines survive in the description, not the title
    assert task.task_type == "backend"


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


def _record_kickoff(db, version_id, directive: str) -> PipelineMessage:
    """The kickoff row ``apply_action('start')`` writes for a fast-fix (stage/author/kind + payload.directive)."""
    msg = PipelineMessage(
        version_id=version_id,
        stage="priprava",
        author="manazer",
        recipient="ai_agent",
        kind="kickoff",
        content=directive,
        payload={"flow_type": "fast_fix", "phase": "priprava", "directive": directive},
    )
    db.add(msg)
    db.flush()
    return msg


def test_priprava_directive_is_lightweight_for_fast_fix(db_session) -> None:
    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator)
    version = Version(project_id=project.id, version_number="v3.0.0", status="active")
    db_session.add(version)
    db_session.flush()
    # A real fast-fix ALWAYS has the Manažér's kickoff directive recorded (apply_action 'start'); without it
    # there is no brief to be lightweight ABOUT, so seed it — otherwise this asserts an unreachable state.
    _record_kickoff(db_session, version.id, "Oprav zaokrúhlenie DPH na položkách")

    ff = orchestrator._priprava_directive(db_session, version.id, flow_type="fast_fix")
    assert "RÝCHLA OPRAVA" in ff and "ĽAHKÁ" in ff
    assert "NEZAPISUJ Špecifikáciu" in ff  # no heavy spec dialogue / no spec artifact

    full = orchestrator._priprava_directive(db_session, version.id, flow_type="new_version")
    # CR-V2-032: the full path is the step-by-step interactive dialogue (one question at a time).
    assert "Špecifikáci" in full and "PO JEDNEJ" in full


@pytest.mark.asyncio
async def test_fast_fix_priprava_brief_carries_the_manager_directive(db_session) -> None:
    """ICCINT-55: the fast-fix Príprava brief must CARRY the Manažér's directive, not point at it.

    The AI Agent runs as its own process and never reads the ``PipelineMessage`` table — that table is the
    cockpit's Manažér↔agent thread. The retired brief told the agent the directive was "VYŠŠIE v tomto vlákne";
    on a new patch version its session is fresh besides, so there was nothing above at all. The agent reported
    having no brief and the lane stalled on its first live use (nex-productcatalogs 0.1.1, 03.09.2026).

    This drives the REAL wiring: ``apply_action('start')`` records the kickoff, the brief must read it back.
    """
    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator)
    _seed_base_version(db_session, project, "v1.0.0")
    patch = fast_fix.create_patch_version(db_session, project_id=project.id, user_id=creator.id)
    directive = "Appka sa nedá otvoriť z NEX Managera — spúšťací token sa neoveruje lokálne."
    await orchestrator.apply_action(
        db_session,
        version_id=patch.id,
        action="start",
        payload={"flow_type": "fast_fix", "directive": directive},
    )

    brief = orchestrator._priprava_directive(db_session, patch.id, flow_type="fast_fix")

    assert directive in brief, "smernica Manažéra sa k agentovi nedostala"
    assert "VYŠŠIE v tomto vlákne" not in brief, "brief ukazuje na vlákno, ktoré agent nevidí"
    # ...and it reads the SAME row the build Task's brief reads — one source, no drift.
    assert fast_fix.kickoff_directive(db_session, patch.id) == directive


def test_fast_fix_priprava_brief_asks_when_the_directive_is_missing(db_session) -> None:
    """No kickoff directive → the brief must ASK for it, never fabricate or imply one (honest-by-construction)."""
    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator)
    version = Version(project_id=project.id, version_number="v4.0.0", status="active")
    db_session.add(version)
    db_session.flush()

    brief = orchestrator._priprava_directive(db_session, version.id, flow_type="fast_fix")

    assert "NENAŠLA" in brief and "kind=question" in brief
    assert "NIŽŠIE" not in brief, "sľubuje smernicu, ktorá tam nie je"


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
    # CR-V2-053: the full release oracle refutes-don't-confirms + runs an unconditional negative/safety test
    assert "REFUTUJ, NEPOTVRDZUJ" in full and "NEGATÍVNE / BEZPEČNOSTNÉ OVERENIE" in full


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


# ---------------------------------------------------------------------------
# 5. ICCINT-53: the customer's sentence lives in the DATA, not in a file we overwrite
# ---------------------------------------------------------------------------


def _fix_epic(db, project, version):
    from backend.db.models.tasks import Epic

    epic = Epic(
        project_id=project.id,
        version_id=version.id,
        number=1,
        title=fast_fix.FAST_FIX_EPIC_TITLE,
        status="done",
    )
    db.add(epic)
    db.flush()
    return epic


def _block_with_note(db, version_id, note):
    msg = PipelineMessage(
        version_id=version_id,
        stage="programovanie",
        author="ai_agent",
        recipient="manazer",
        kind="gate_report",
        content="hotovo",
        payload={"phase": "programovanie", "customer_note": note},
    )
    db.add(msg)
    db.flush()
    return msg


def test_customer_note_reaches_the_changelog(db_session, tmp_path) -> None:
    """The agent's sentence must render as the changelog bullet — and survive REGENERATION.

    Before ICCINT-53 the agent wrote its prose into RELEASE_NOTES.md and NEX Studio, which owns that file,
    regenerated it away — six rounds running on nex-productcatalogs, with the fallback printing the machine
    Epic title instead ("Rýchla oprava — Rýchla oprava"). Held in ``plain_description`` it cannot be lost:
    the assertion below renders TWICE and demands the same text both times.
    """
    from backend.services import orchestrator, release_note_writer

    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator)
    version = Version(project_id=project.id, version_number="0.1.1", status="active", name="Rýchla oprava")
    db_session.add(version)
    db_session.flush()
    db_session.add(
        PipelineState(
            version_id=version.id,
            flow_type="fast_fix",
            current_stage="verifikacia",
            current_actor="auditor",
            status="agent_working",
        )
    )
    _fix_epic(db_session, project, version)
    note = "Appku otvoríte jedným kliknutím z NEX Managera — bez zadávania mena a hesla."
    _block_with_note(db_session, version.id, note)

    orchestrator._apply_customer_note_to_fix_epic(db_session, version.id)
    body = release_note_writer.write_release_note(db_session, version.id, tmp_path).read_text(encoding="utf-8")

    assert f"- {note}" in body
    assert "- Rýchla oprava" not in body, "zákazník stále číta strojový názov epiky"

    # Regenerate: the whole point is that this cannot be overwritten any more.
    orchestrator._apply_customer_note_to_fix_epic(db_session, version.id)
    again = release_note_writer.write_release_note(db_session, version.id, tmp_path).read_text(encoding="utf-8")
    assert again == body


def test_customer_note_never_overwrites_a_plan_authored_description(db_session) -> None:
    """A ``plain_description`` the agent's own task plan wrote is HIS text — never clobbered."""
    from backend.services import orchestrator

    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator)
    version = Version(project_id=project.id, version_number="0.2.0", status="active")
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
    epic = _fix_epic(db_session, project, version)
    epic.plain_description = "Text z plánu úloh."
    db_session.flush()
    _block_with_note(db_session, version.id, "Neskorší text, ktorý nesmie prepísať ten z plánu.")

    orchestrator._apply_customer_note_to_fix_epic(db_session, version.id)

    assert epic.plain_description == "Text z plánu úloh."


def test_fast_fix_build_brief_asks_for_the_customer_sentence(db_session) -> None:
    """Nobody can supply what nobody asked for — the fast-fix build brief must request ``customer_note``."""
    from backend.db.models.tasks import Feat, Task
    from backend.services import orchestrator

    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator)
    version = Version(project_id=project.id, version_number="0.3.0", status="active")
    db_session.add(version)
    db_session.flush()
    epic = _fix_epic(db_session, project, version)
    feat = Feat(epic_id=epic.id, number=1, title=fast_fix.FAST_FIX_EPIC_TITLE, status="done")
    db_session.add(feat)
    db_session.flush()
    task = Task(feat_id=feat.id, number=1, title="Oprav vstup", description="…", task_type="backend", status="todo")
    db_session.add(task)
    db_session.flush()

    brief = orchestrator._directive_for_build_task(task, None, [], flow_type="fast_fix")
    assert "customer_note" in brief
    assert "RELEASE_NOTES.md" in brief  # ...and says explicitly not to write there

    plain = orchestrator._directive_for_build_task(task, None, [], flow_type="new_version")
    assert "customer_note" not in plain  # a normal build carries it in the task plan instead


# ---------------------------------------------------------------------------
# 6. ICCINT-57: the lane must be VERIFIABLE — it has no Návrh to declare from
# ---------------------------------------------------------------------------


def _fast_fix_build(db, *, flow_type="fast_fix", stage="verifikacia"):
    creator = _seed_user(db)
    project = _seed_project(db, creator=creator)
    version = Version(project_id=project.id, version_number="9.9.9", status="active")
    db.add(version)
    db.flush()
    db.add(
        PipelineState(
            version_id=version.id,
            flow_type=flow_type,
            current_stage=stage,
            current_actor="auditor",
            status="agent_working",
        )
    )
    db.flush()
    return project, version


def _priprava_declaration(db, version_id, features):
    """The fast lane's Príprava close, carrying the release-coverage declaration."""
    msg = PipelineMessage(
        version_id=version_id,
        stage="priprava",
        author="ai_agent",
        recipient="manazer",
        kind="gate_report",
        content="Príprava uzavretá.",
        payload={"phase": "priprava", "plan": None, "flagship_features": features},
    )
    db.add(msg)
    db.flush()
    return msg


def test_fast_fix_floor_comes_from_priprava_not_from_a_navrh_it_never_runs(db_session) -> None:
    """The deadlock this ticket is about.

    The oracle floor was read ONLY from the design close, which is found by the ``plan`` payload a design
    carries. A fast fix materializes no plan and has no Návrh phase at all, so the floor was always ``(0, 0)``
    — and ``_evaluate_release_coverage`` refuses ``(0, 0)`` outright. Every fast fix that reached Verifikácia
    therefore failed forever, whatever the code did: nex-productcatalogs 0.1.1 burned 16 rounds and 22 hours on
    16 identical failures while the Auditor reported "Bez nálezu".
    """
    from backend.services import orchestrator

    _project, version = _fast_fix_build(db_session)
    _priprava_declaration(db_session, version.id, ["Katalóg sa otvorí z Managera bez prihlasovania"])

    assert orchestrator._declared_release_coverage(db_session, version.id) == (1, 0)


def test_a_declared_fast_fix_can_actually_pass_the_release_gate(db_session) -> None:
    """End of the dead end: with the declaration in place the gate is satisfiable by a real acceptance run."""
    from backend.services import orchestrator

    _project, version = _fast_fix_build(db_session)
    _priprava_declaration(db_session, version.id, ["Katalóg sa otvorí z Managera bez prihlasovania"])
    req = orchestrator._declared_release_coverage(db_session, version.id)

    ok, detail = orchestrator._evaluate_release_coverage(total=3, feature=1, negative=0, coverage_req=req)

    assert ok, detail


def test_an_undeclared_fast_fix_is_still_refused(db_session) -> None:
    """The floor is MOVED, not lowered. A fast fix that declares nothing stays unverifiable — otherwise this
    fix would have quietly turned the release gate off for the whole lane."""
    from backend.services import orchestrator

    _project, version = _fast_fix_build(db_session)  # no Príprava declaration

    req = orchestrator._declared_release_coverage(db_session, version.id)
    ok, detail = orchestrator._evaluate_release_coverage(total=3, feature=1, negative=0, coverage_req=req)

    assert req == (0, 0)
    assert not ok and "missing declaration" in detail


def test_a_new_version_still_reads_its_declaration_from_the_design(db_session) -> None:
    """The ordinary lane is untouched — a Príprava block must NOT become a back door around the design close."""
    from backend.services import orchestrator

    _project, version = _fast_fix_build(db_session, flow_type="new_version")
    _priprava_declaration(db_session, version.id, ["toto sa nesmie počítať"])

    assert orchestrator._declared_release_coverage(db_session, version.id) == (0, 0)


def test_the_fast_fix_priprava_brief_asks_for_the_declaration(db_session) -> None:
    """Nobody can declare what nobody asked for — and the ask must name the APP, not our own tooling: on the
    stalled build the agent's unprompted declaration read "Živý náhľad appky…", a NEX Studio feature."""
    from backend.services import orchestrator

    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator)
    _seed_base_version(db_session, project, "v1.0.0")
    patch = fast_fix.create_patch_version(db_session, project_id=project.id, user_id=creator.id)
    _record_kickoff(db_session, patch.id, "Oprav vstup z Managera")

    brief = orchestrator._priprava_directive(db_session, patch.id, flow_type="fast_fix")

    assert "flagship_features" in brief
    assert "NEX Studiu" in brief  # says explicitly not to declare our own tooling
