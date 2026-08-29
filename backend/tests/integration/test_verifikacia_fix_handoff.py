"""Four defects one build surfaced in a day (ICCINT-31/32/35/36), nex-productcatalogs, 28.–29.08.2026.

They are together because they were found together, walking ONE build from "122 tasks green" to a Verifikácia
that refused it. Each is small; the pattern is not. In every one of them the engine told the Manažér — or the
agent — something that was not so:

  * **36** the one-click "Nechaj to opraviť" handed the agent no finding and told it to rewrite an APPROVED
    Špecifikácia. The agent refused and asked; a less careful turn would have rewritten it off a card label.
  * **32** a deliverable written as "path — description" crashed the dispatch AFTER the work was committed,
    and the screen offered "Skús znova", which could not help: same input, same crash.
  * **35** the fix rows were the only rows in the plan with no plain sentence — on the one line that says
    something broke.
  * **31** feat and epic stayed "Čaká" over work that was running.
"""

from __future__ import annotations

import uuid as _uuid

import pytest
from sqlalchemy import select

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineState
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version
from backend.services import orchestrator
from backend.services import task as task_service
from backend.services.pipeline_status import PipelineStatusBlock

BOOT_FAIL = (
    "## Verifikácia FAIL — oprav podľa nálezov Auditora\n"
    "Konkrétny dôvod zlyhania: container nex-productcatalogs-smoke-migrate-1 exited (1)\n\n"
    "Spusti appku tak ako engine (`docker compose up`), zreprodukuj zlyhanú skúšku a oprav jej príčinu."
)


def _seed(db, *, stage: str = "verifikacia") -> tuple[Version, PipelineState]:
    suffix = _uuid.uuid4().hex[:8]
    user = User(username=f"cc_{suffix}", email=f"cc_{suffix}@test.local", password_hash="x", role="ri")
    db.add(user)
    db.flush()
    project = Project(
        name=f"Fix {suffix}",
        slug=f"fix-{suffix}",
        type="standard",
        auth_mode="password",
        description="Verifikácia fix handoff test.",
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
        current_stage=stage,
        current_actor="ai_agent",
        status="agent_working",
        mode=None,
    )
    db.add(state)
    db.flush()
    return version, state


# ── ICCINT-36: the fix brief must survive the trip to the agent ───────────────


def _seed_fix_card(db, version_id) -> None:
    """A Verifikácia FIX card, the Manažér's one-click answer, and the brief apply_action recorded."""
    orchestrator._record_message(
        db,
        version_id=version_id,
        stage="verifikacia",
        author="system",
        recipient="manazer",
        kind="consultation",
        content="Verifikácia našla chybu.",
        payload={
            "consultation": {
                "id": "vfix-1",
                "source": "verifikacia_fix",
                "decisions": [
                    {
                        "key": "verifikacia_fix_next",
                        "question": "Verifikácia našla blokujúcu chybu. Ako chceš pokračovať?",
                        "options": [
                            {"id": "fix_it", "label": "Nechaj to opraviť", "recommended": True},
                            {"id": "hold", "label": "Zatiaľ podržať"},
                        ],
                    }
                ],
            }
        },
    )
    db.flush()
    orchestrator._record_message(
        db,
        version_id=version_id,
        stage="programovanie",
        author="manazer",
        recipient="ai_agent",
        kind="answer",
        content="Ako chceš pokračovať? → Nechaj to opraviť",
        payload={
            "consultation_decision": {
                "consultation_id": "vfix-1",
                "key": "verifikacia_fix_next",
                "option_id": "fix_it",
                "label": "Nechaj to opraviť",
            }
        },
    )
    # What ``_route_manazer_fix_to_ai_agent`` records: the CONCRETE brief.
    orchestrator._record_message(
        db,
        version_id=version_id,
        stage="verifikacia",
        author="manazer",
        recipient="ai_agent",
        kind="return",
        content=BOOT_FAIL,
        payload={"phase": "verifikacia"},
    )
    db.flush()


def test_the_one_click_fix_carries_the_finding_to_the_agent(db_session) -> None:
    version, _state = _seed(db_session)
    _seed_fix_card(db_session, version.id)

    directive = orchestrator.dispatch_directive(db_session, version.id, "decide", {}, "programovanie")

    assert directive, "the agent was dispatched with no directive at all"
    # THE defect: the agent must receive the REASON, not just the label of the button that was pressed.
    assert "migrate-1 exited (1)" in directive, "the fix brief was thrown away and rebuilt from the card label"
    assert "docker compose up" in directive


def test_the_one_click_fix_never_tells_the_agent_to_rewrite_the_specification(db_session) -> None:
    """The second half of ICCINT-36, and the dangerous half. The generic consultation directive ends with
    "Teraz PREPRACUJ Špecifikáciu/Návrh…" — correct for a card about DOCUMENTS, catastrophic for a card about
    a boot failure. The agent that met it refused, saying it would have to invent the changes."""
    version, _state = _seed(db_session)
    _seed_fix_card(db_session, version.id)

    directive = orchestrator.dispatch_directive(db_session, version.id, "decide", {}, "programovanie")

    assert "PREPRACUJ" not in (directive or "")
    assert "Špecifikáciu/Návrh" not in (directive or "")


def test_a_document_consultation_still_gets_the_rework_directive(db_session) -> None:
    """The control: the generic directive is RIGHT where it belongs — a consultation about the documents."""
    version, _state = _seed(db_session, stage="navrh")
    orchestrator._record_message(
        db_session,
        version_id=version.id,
        stage="navrh",
        author="system",
        recipient="manazer",
        kind="consultation",
        content="Previerka našla diery.",
        payload={
            "consultation": {
                "id": "doc-1",
                "source": "auditor_upfront",
                "decisions": [
                    {
                        "key": "dph",
                        "question": "Čo s prázdnou sadzbou DPH?",
                        "options": [
                            {"id": "priznak", "label": "Prijať s príznakom", "recommended": True},
                            {"id": "zahodit", "label": "Zahodiť"},
                        ],
                    }
                ],
            }
        },
    )
    db_session.flush()
    orchestrator._record_message(
        db_session,
        version_id=version.id,
        stage="navrh",
        author="manazer",
        recipient="ai_agent",
        kind="answer",
        content="→ Prijať s príznakom",
        payload={
            "consultation_decision": {
                "consultation_id": "doc-1",
                "key": "dph",
                "option_id": "priznak",
                "label": "Prijať s príznakom",
            }
        },
    )
    db_session.flush()

    directive = orchestrator.dispatch_directive(db_session, version.id, "decide", {}, "navrh")

    assert "PREPRACUJ" in directive
    assert "Prijať s príznakom" in directive


# ── ICCINT-32: a deterministic verify returns a reason, it does not crash ─────


def test_a_deliverable_that_is_not_a_path_fails_the_verify_instead_of_killing_the_turn(tmp_path, monkeypatch) -> None:
    """347 bytes of description where a path was expected. ``Path.exists()`` raises ENAMETOOLONG (pathlib
    ignores only ENOENT/ENOTDIR/EBADF/ELOOP), and that exception used to escape the whole dispatch — AFTER
    the task's work was already committed — as an unrecoverable "Systémová chyba"."""
    block = PipelineStatusBlock(
        stage="programovanie",
        kind="done",
        summary="hotovo",
        awaiting="manazer",
        deliverables=[
            "backend/infra/card_prefill.py — zloženie podkladu pre formulár z jedného dodávateľského riadku "
            "podľa mapovania §12.6 (názov, váha ako TEXT, sadzba DPH z AppSetting.pricing.default_vat_prc, "
            "čiarové kódy s hlavným na prvom mieste, kód výrobcu so značkou, dodávateľský kód s dodávateľom, "
            "celé supParameters, zdrojová ponuka s príznakmi)"
        ],
    )

    # The project root and the leading directories must EXIST — with a missing parent the kernel answers
    # ENOENT before it ever measures the component, so the crash would not reproduce and the test would pass
    # against the unfixed code. (Found by running it: the first cut used a nonexistent slug.)
    from backend.services import claude_agent

    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", tmp_path)
    (tmp_path / "acme" / "backend" / "infra").mkdir(parents=True)

    reason = orchestrator.verify_mechanical("acme", block)

    assert reason is not None, "a malformed deliverable passed the verify"
    assert "PATHS only" in reason, "the reason must tell the agent what to fix"


# ── ICCINT-35: the fix rows speak plain Slovak ───────────────────────────────


def test_the_fix_sentence_comes_from_the_finding_not_a_template() -> None:
    plain = orchestrator._fix_plain_description(BOOT_FAIL)
    assert "migrate-1 exited (1)" in plain
    assert plain.startswith("Verifikácia našla chybu")


def test_an_unusable_scope_still_yields_a_sentence() -> None:
    """Better a generic sentence than the "(bez ľudského vysvetlenia)" placeholder that started this."""
    plain = orchestrator._fix_plain_description("")
    assert plain and "bez ľudského vysvetlenia" not in plain


# ── ICCINT-31: the plan shows work as running while it runs ──────────────────


def test_starting_a_task_moves_its_feat_and_epic_to_running(db_session) -> None:
    version, _state = _seed(db_session, stage="programovanie")
    epic = Epic(project_id=version.project_id, version_id=version.id, number=1, title="E", status="planned")
    db_session.add(epic)
    db_session.flush()
    feat = Feat(epic_id=epic.id, number=1, title="F", status="todo")
    db_session.add(feat)
    db_session.flush()
    done = Task(feat_id=feat.id, number=1, title="T1", status="done", task_type="backend")
    running = Task(feat_id=feat.id, number=2, title="T2", status="todo", task_type="backend")
    db_session.add_all([done, running])
    db_session.flush()

    running.status = "in_progress"
    db_session.flush()
    task_service.recompute_feat_status(db_session, feat.id)

    db_session.refresh(feat)
    db_session.refresh(epic)
    assert feat.status == "in_progress", "the plan showed 'Čaká' over work that was running"
    assert epic.status == "in_progress"


def test_the_epic_is_recomputed_even_when_this_feat_did_not_move(db_session) -> None:
    """The propagation used to sit INSIDE ``if feat.status != new_status`` — a rollup that skips the very
    case it exists for. A sibling feat can leave the epic stale while this one stands still."""
    version, _state = _seed(db_session, stage="programovanie")
    epic = Epic(project_id=version.project_id, version_id=version.id, number=1, title="E", status="planned")
    db_session.add(epic)
    db_session.flush()
    quiet = Feat(epic_id=epic.id, number=1, title="F1", status="in_progress")
    db_session.add(quiet)
    db_session.flush()
    db_session.add(Task(feat_id=quiet.id, number=1, title="T", status="in_progress", task_type="backend"))
    db_session.flush()

    # This feat is ALREADY in_progress, so its own status does not change — the epic still must catch up.
    task_service.recompute_feat_status(db_session, quiet.id)

    db_session.refresh(epic)
    assert epic.status == "in_progress"


@pytest.mark.asyncio
async def test_nothing_here_touches_a_finished_plan(db_session) -> None:
    """A guard against over-reach: an all-done feat stays done, and so does its epic."""
    version, _state = _seed(db_session, stage="programovanie")
    epic = Epic(project_id=version.project_id, version_id=version.id, number=1, title="E", status="planned")
    db_session.add(epic)
    db_session.flush()
    feat = Feat(epic_id=epic.id, number=1, title="F", status="todo")
    db_session.add(feat)
    db_session.flush()
    db_session.add(Task(feat_id=feat.id, number=1, title="T", status="done", task_type="backend"))
    db_session.flush()

    task_service.recompute_feat_status(db_session, feat.id)

    db_session.refresh(feat)
    db_session.refresh(epic)
    assert feat.status == "done" and epic.status == "done"
    assert db_session.execute(select(Task).where(Task.feat_id == feat.id)).scalar_one().status == "done"


# ── ICCINT-38/39: an instruction with nowhere to land, and a plan that stays a map ──


@pytest.mark.asyncio
async def test_an_instruction_with_no_open_task_becomes_work(db_session) -> None:
    """The Manažér's only lever must not be spent in silence (ICCINT-38).

    Found 30.08.2026: the agent closed the last task believing it had fixed the boot, it had not, and from
    that state ``uprav`` reached nobody. The message was recorded ``delivered``, the build loop asked for the
    next todo task, got None, and settled straight back — so the one thing he could do did nothing, quietly.
    """
    version, state = _seed(db_session, stage="programovanie")
    state.status = "awaiting_manazer"
    epic = Epic(project_id=version.project_id, version_id=version.id, number=1, title="E", status="done")
    db_session.add(epic)
    db_session.flush()
    feat = Feat(epic_id=epic.id, number=1, title="F", status="done")
    db_session.add(feat)
    db_session.flush()
    db_session.add(Task(feat_id=feat.id, number=1, title="T", status="done", task_type="backend"))
    db_session.flush()
    assert task_service.get_next_todo_task(db_session, version.id) is None  # nothing left to run

    await orchestrator.apply_action(
        db_session, version_id=version.id, action="uprav", payload={"comment": "Migrácie padajú na 0022."}
    )

    todo = task_service.get_next_todo_task(db_session, version.id)
    assert todo is not None, "the instruction was accepted and reached nobody"
    assert "0022" in (todo.description or ""), "the task must carry HIS words, not a template"
    assert state.status == "agent_working"


def test_every_round_lives_in_one_fix_epic(db_session) -> None:
    """ICCINT-39 — the plan grows downward, not sideways."""
    version, _state = _seed(db_session, stage="verifikacia")
    for round_no in range(3):
        orchestrator._ensure_verifikacia_fix_task(
            db_session, version.id, scope=f"nález {round_no}", findings=[f"nález {round_no}"]
        )
        open_task = db_session.execute(
            select(Task)
            .join(Feat, Feat.id == Task.feat_id)
            .join(Epic, Epic.id == Feat.epic_id)
            .where(Epic.version_id == version.id, Task.status == "todo")
        ).scalar_one()
        open_task.status = "done"
        db_session.flush()

    epics = (
        db_session.execute(
            select(Epic).where(Epic.version_id == version.id, Epic.title == orchestrator._VERIFIKACIA_FIX_EPIC_TITLE)
        )
        .scalars()
        .all()
    )
    assert len(epics) == 1, "three rounds grew three epics — the plan sprawls"
    feats = db_session.execute(select(Feat).where(Feat.epic_id == epics[0].id)).scalars().all()
    assert len(feats) == 3, "the rounds must still be tellable apart"


def test_three_findings_become_three_tasks_in_one_round(db_session) -> None:
    """The half that matters more: the plan must be able to say WHICH of the findings is done."""
    version, _state = _seed(db_session, stage="verifikacia")
    orchestrator._ensure_verifikacia_fix_task(
        db_session, version.id, scope="tri veci", findings=["migrácie padajú", "chýba index", "zlá sadzba"]
    )
    tasks = (
        db_session.execute(
            select(Task)
            .join(Feat, Feat.id == Task.feat_id)
            .join(Epic, Epic.id == Feat.epic_id)
            .where(Epic.version_id == version.id)
        )
        .scalars()
        .all()
    )
    assert len(tasks) == 3
    assert len({t.feat_id for t in tasks}) == 1  # one round, three tasks
    assert {t.description for t in tasks} == {"migrácie padajú", "chýba index", "zlá sadzba"}


def test_a_build_already_mid_flight_is_not_given_a_second_fix_task(db_session) -> None:
    """The migration hazard ICCINT-39 created and nearly shipped.

    A build that was ALREADY running when the one-epic shape landed carries an open fix task under the OLD
    per-round epic title. A lookup that only knew the new name would not find it, would stack a second open
    task, and the agent would do the same fix twice — exactly the failure the idempotency guard exists to
    prevent (nex-shopify 2026-07-20: three identical tasks, one fix done 3x). Caught on the live
    nex-productcatalogs build minutes after v4.2.3 went out, before the Manažér pressed the card.
    """
    version, _state = _seed(db_session, stage="verifikacia")
    legacy = Epic(
        project_id=version.project_id,
        version_id=version.id,
        number=9,
        title=f"{orchestrator._VERIFIKACIA_FIX_TITLE} (2. kolo)",  # the OLD shape
        status="planned",
    )
    db_session.add(legacy)
    db_session.flush()
    lf = Feat(epic_id=legacy.id, number=1, title=orchestrator._VERIFIKACIA_FIX_TITLE, status="todo")
    db_session.add(lf)
    db_session.flush()
    db_session.add(
        Task(
            feat_id=lf.id,
            number=1,
            title=orchestrator._VERIFIKACIA_FIX_TITLE,
            status="todo",
            task_type="backend",
            description="starý rozsah",
        )
    )
    db_session.flush()

    orchestrator._ensure_verifikacia_fix_task(db_session, version.id, scope="nový rozsah", findings=["nový rozsah"])

    open_tasks = (
        db_session.execute(
            select(Task)
            .join(Feat, Feat.id == Task.feat_id)
            .join(Epic, Epic.id == Feat.epic_id)
            .where(Epic.version_id == version.id, Task.status.in_(("todo", "in_progress")))
        )
        .scalars()
        .all()
    )
    assert len(open_tasks) == 1, "a second open fix task was stacked — the agent would fix the same thing twice"
    # The existing one was REFRESHED with the new brief, not left carrying a stale scope.
    assert open_tasks[0].description == "nový rozsah"
