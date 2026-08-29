"""A fix round closes on a MEASUREMENT, not on the agent's word (ICCINT-42).

29.08.2026, nex-productcatalogs. The Manažér's screen read 125/125 úloh, 100 %, nine green epics and
"SYSTÉM: Úloha #9.3.1 „Oprava po Verifikácii" — hotovo (1 pokus)", with *Prejsť na overenie* as the
recommended button. Underneath, the app was dead: a fresh clone + ``docker compose up`` brought up the db and
the frontend, ran the migrations clean — and the backend died on boot with ``python-multipart`` missing.

Nothing on that screen lied on purpose. The fix task closed because :func:`verify_mechanical` passed, and all
that check proves is that the commits exist and the promised files are on disk; whether the app RUNS was
never asked. The task's own brief ended *"…potom over znova"*. Nobody checked that anybody had.

Director: *"Ak by som ťa nespýtal, z tejto obrazovky ja môžem posúdiť, že všetko je v poriadku."*

The engine owns the instrument — it is what produced the finding in the first place. These pin that it now
points it back at the fix before a human is asked to approve one.
"""

from __future__ import annotations

import contextlib
import uuid as _uuid

import pytest
from sqlalchemy import select

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineState
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version
from backend.schemas.epic import EpicCreate
from backend.schemas.feat import FeatCreate
from backend.schemas.task import TaskCreate
from backend.services import claude_agent, orchestrator
from backend.services import epic as epic_service
from backend.services import feat as feat_service
from backend.services import task as task_service

BOOT_FAIL_BRIEF = (
    "## Verifikácia FAIL — oprav podľa nálezov Auditora\n"
    "Konkrétny dôvod zlyhania: container nex-productcatalogs-smoke-migrate-1 exited (1)\n\n"
    "Spusti appku tak ako engine (`docker compose up`), zreprodukuj zlyhanú skúšku po spustení a oprav jej "
    "príčinu, potom over znova."
)
# What the clean clone actually said on 29.08.2026, once ICCINT-37 made the log visible at all.
REAL_REASON = (
    "up exit 1: container backend-1 exited (1)\n"
    '--- backend ---\nRuntimeError: Form data requires "python-multipart" to be installed.'
)


def _seed(db) -> tuple[Version, PipelineState]:
    suffix = _uuid.uuid4().hex[:8]
    user = User(username=f"cc_{suffix}", email=f"cc_{suffix}@test.local", password_hash="x", role="ri")
    db.add(user)
    db.flush()
    project = Project(
        name=f"Recheck {suffix}",
        slug=f"recheck-{suffix}",
        type="standard",
        auth_mode="password",
        description="Fix-round re-measurement test.",
        created_by=user.id,
        source_path=None,
    )
    db.add(project)
    db.flush()
    version = Version(project_id=project.id, version_number="0.1.0", status="active")
    db.add(version)
    db.flush()
    state = PipelineState(
        version_id=version.id,
        flow_type="new_version",
        current_stage="programovanie",
        current_actor="ai_agent",
        status="agent_working",
        mode=None,
    )
    db.add(state)
    db.flush()
    return version, state


def _record_fail_verdict(db, version_id) -> None:
    """What puts a Programovanie round here: the Manažér's fix directive off a Verifikácia FAIL."""
    orchestrator._record_message(
        db,
        version_id=version_id,
        stage="verifikacia",
        author="manazer",
        recipient="ai_agent",
        kind="return",
        content=BOOT_FAIL_BRIEF,
        payload={"phase": "verifikacia"},
    )
    db.flush()


def _tasks_of(db, version_id) -> list[Task]:
    return list(
        db.execute(
            select(Task)
            .join(Feat, Feat.id == Task.feat_id)
            .join(Epic, Epic.id == Feat.epic_id)
            .where(Epic.version_id == version_id)
        )
        .scalars()
        .all()
    )


def _finish_all_tasks(db, version_id) -> list[Task]:
    """The agent reports done — every task green, which is exactly the state that fooled the screen."""
    tasks = _tasks_of(db, version_id)
    for task in tasks:
        task.status = "done"
    db.flush()
    for feat_id in {t.feat_id for t in tasks}:
        task_service.recompute_feat_status(db, feat_id)
    return tasks


def _plain_plan(db, version) -> None:
    """An ordinary plan row — no fix epic anywhere near it."""
    epic = epic_service.create(
        db,
        EpicCreate(
            project_id=version.project_id,
            version_id=version.id,
            title="Základ appky",
            plain_description="Kostra aplikácie.",
        ),
    )
    feat = feat_service.create(
        db, FeatCreate(epic_id=epic.id, title="Prihlásenie", description="…", plain_description="Prihlásenie.")
    )
    task_service.create(
        db,
        TaskCreate(
            feat_id=feat.id, title="Formulár", description="…", task_type="backend", plain_description="Formulár."
        ),
    )
    db.flush()


def _collect(messages):
    async def _on_message(msg):
        messages.append(msg)

    return _on_message


# ── the defect itself ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_fix_that_did_not_fix_it_never_reaches_the_manazer_as_done(db_session, monkeypatch) -> None:
    version, state = _seed(db_session)
    _record_fail_verdict(db_session, version.id)
    orchestrator._ensure_verifikacia_fix_task(db_session, version.id, scope=BOOT_FAIL_BRIEF, findings=None)
    _finish_all_tasks(db_session, version.id)

    async def _still_broken(slug):
        return False, REAL_REASON

    monkeypatch.setattr(orchestrator, "_app_starts_after_fix", _still_broken)
    messages: list = []
    await orchestrator._run_build_round(db_session, state, on_message=_collect(messages))

    # NOT awaiting_manazer with a green plan — the whole point.
    assert state.status == "blocked", "the Manažér was still asked to approve a build that does not start"
    assert state.block_reason == "agent_error"
    assert "nespustí" in state.next_action

    # The plan must stop reading 100 % / Hotovo, or the screen tells the same untruth by another route.
    fix_tasks = _tasks_of(db_session, version.id)
    assert any(t.status == "todo" for t in fix_tasks), "the fix stayed 'done' over an app that will not boot"
    epic = db_session.execute(select(Epic).where(Epic.version_id == version.id)).scalars().first()
    assert epic.status != "done", "the epic stayed green"

    # And the reason is ON SCREEN, with the failing container's log behind the disclosure (ICCINT-37).
    said = [m for m in messages if (m.payload or {}).get("fix_boot_recheck")]
    assert said, "the re-check happened silently — the Manažér has no way to know why he is blocked"
    assert "python-multipart" in (said[-1].payload or {}).get("technical_detail", "")
    assert "nespustí" in said[-1].content


@pytest.mark.asyncio
async def test_a_fix_that_works_says_so_and_hands_over_exactly_as_before(db_session, monkeypatch) -> None:
    """The control. A working fix must not be made slower or scarier — one plain sentence, then the same
    approval point the Manažér already knows."""
    version, state = _seed(db_session)
    _record_fail_verdict(db_session, version.id)
    orchestrator._ensure_verifikacia_fix_task(db_session, version.id, scope=BOOT_FAIL_BRIEF, findings=None)
    _finish_all_tasks(db_session, version.id)

    async def _boots(slug):
        return True, "backend responded 200 /health; frontend served index.html"

    monkeypatch.setattr(orchestrator, "_app_starts_after_fix", _boots)
    messages: list = []
    await orchestrator._run_build_round(db_session, state, on_message=_collect(messages))

    assert state.status == "awaiting_manazer"
    assert state.next_action == "Manažér: posúdiť výsledok Programovania (Schváliť / Uprav)."
    said = [m for m in messages if (m.payload or {}).get("fix_boot_recheck")]
    assert said and said[-1].payload["smoke"]["pass"] is True
    assert "spustila" in said[-1].content


@pytest.mark.asyncio
async def test_an_ordinary_round_is_not_slowed_down_by_a_boot_check(db_session, monkeypatch) -> None:
    """A first pass through Programovanie has no failed measurement behind it, and Verifikácia boots the app
    next anyway. Paying for a second up/down cycle here would buy nothing."""
    version, state = _seed(db_session)
    _plain_plan(db_session, version)
    _finish_all_tasks(db_session, version.id)

    async def _must_not_run(slug):
        raise AssertionError("an ordinary round paid for a boot check it had no reason to run")

    monkeypatch.setattr(orchestrator, "_app_starts_after_fix", _must_not_run)
    await orchestrator._run_build_round(db_session, state, on_message=_collect([]))

    assert state.status in ("awaiting_manazer", "agent_working")


# ── the pieces it is built from ───────────────────────────────────────────────


def test_reopening_a_round_leaves_the_earlier_rounds_alone(db_session) -> None:
    """Rounds 1 and 2 were settled against their own findings and are none of round 3's business — walking
    them back would rewrite history the Manažér already read."""
    version, _state = _seed(db_session)
    for scope in ("prvý nález", "druhý nález", "tretí nález"):
        orchestrator._ensure_verifikacia_fix_task(db_session, version.id, scope=scope, findings=None)
        _finish_all_tasks(db_session, version.id)

    moved = orchestrator._reopen_verifikacia_fix_round(db_session, version.id)

    assert moved == 1, "reopened something other than exactly the last round"
    feats = (
        db_session.execute(
            select(Feat).join(Epic, Epic.id == Feat.epic_id).where(Epic.version_id == version.id).order_by(Feat.number)
        )
        .scalars()
        .all()
    )
    assert [f.status for f in feats] == ["done", "done", "todo"]


def test_reopening_when_there_is_no_fix_round_is_not_an_error(db_session) -> None:
    """A diagnostic that can crash the thing it diagnoses is worse than no diagnostic."""
    version, _state = _seed(db_session)
    assert orchestrator._reopen_verifikacia_fix_round(db_session, version.id) == 0


@pytest.mark.asyncio
async def test_the_recheck_measures_with_the_same_instrument_as_verifikacia(db_session, monkeypatch, tmp_path) -> None:
    """The re-check is only worth anything if "it starts" means the SAME thing here and at Verifikácia — so
    both go through :func:`_boot_leg`, including ICCINT-37's log capture. A second, parallel definition of
    "it starts" would drift, and the drift would show up as a build that passes here and fails there."""
    slug = "recheck-instrument"
    root = tmp_path / slug
    root.mkdir()
    (root / "docker-compose.yml").write_text(
        "services:\n"
        "  backend:\n    image: x\n    ports: ['8000:8000']\n"
        "  frontend:\n    image: y\n    ports: ['80:80']\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", tmp_path)

    @contextlib.asynccontextmanager
    async def _dead_stack(project_slug, compose, roles):
        yield orchestrator._SmokeStack(
            base=["docker", "compose", "-p", f"{project_slug}-smoke"],
            compose=compose,
            override=compose,
            project=f"{project_slug}-smoke",
            roles=roles,
            up_rc=1,
            up_detail="container backend-1 exited (1)",
        )

    monkeypatch.setattr(orchestrator, "_boot_smoke_stack", _dead_stack)

    async def _logs(base):
        return '\n--- backend ---\nRuntimeError: Form data requires "python-multipart" to be installed.'

    monkeypatch.setattr(orchestrator, "_smoke_failure_logs", _logs)

    ok, detail = await orchestrator._app_starts_after_fix(slug)

    assert ok is False
    assert "up exit 1" in detail
    assert "python-multipart" in detail, "the re-check dropped the reason ICCINT-37 exists to carry"


@pytest.mark.asyncio
async def test_a_project_with_no_compose_is_not_reported_as_broken(db_session, monkeypatch, tmp_path) -> None:
    """A boot check needs a compose to boot. A legit non-web project must not be blocked by the absence of
    one — that would turn this safeguard into a wall."""
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", tmp_path)
    (tmp_path / "no-compose").mkdir()
    ok, detail = await orchestrator._app_starts_after_fix("no-compose")
    assert ok is True and "SKIPPED" in detail
