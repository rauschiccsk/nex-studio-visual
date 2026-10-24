"""DEV-214 — the build stops once its task plan is written; the Manažér sees the plan and starts the coding himself.

23.10.2026 NEX Manager 1.4.0: the build round wrote „Plán úloh zapísaný: 6 epicov, 12 featov, 20 taskov“ at 20:29:31
and task #21.1.1 began in the same second — how big the plan was and how long the agent would work on it (DEV-210)
showed only while it was being coded. Director: „Áno, súhlasím, založ všetkých päť tiketov.“, „Áno, súhlasím, začni
DEV-214“.

Pinned: the round that writes the plan stops before any task, whatever the Miera autonómie; „Spustiť programovanie“
(``pokracovat``) starts the first task without planning again; „Uprav“ at that stop sends the PLAN back to the agent
and stops again; a question is answered and the build stays at the plan; a fast fix and a build whose first task
already started are as before.
"""

from __future__ import annotations

import pytest

from backend.api.routes.pipeline import _board
from backend.services import build_forecast, orchestrator
from backend.services.build_forecast import BuildForecast
from tests.test_orchestrator_v2_programovanie import (
    _done_block,
    _make_version,
    _msgs,
    _no_baseline_git,
    _seed_programovanie,
    _seed_tasks,
    _stub_mech,
    _stub_plan_passes,
    _stub_turns,
    _tasks,
)

PLAN = [
    ("Foundation", [("Schema", [("users table", "migration", 60), ("audit_log", "migration", 30)])]),
    ("Calc", [("Hlavná kniha", [("GL výpočet", "backend", 120)])]),
]
REWORKED = [("Foundation", [("Schema", [("users table", "migration", 60)])])]
STOP_TEXT = (
    "Plán úloh je zostavený: 2 epicy, 2 featy, 3 úlohy. Programovanie sa začne, až keď ho spustíš: pozri si plán "
    "v Pláne úloh a klikni „Spustiť programovanie“, alebo plán vráť agentovi na úpravu tlačidlom „Uprav“ "
    "s pripomienkou."
)


@pytest.fixture(autouse=True)
def _no_forecast(monkeypatch):
    monkeypatch.setattr(build_forecast, "forecast", lambda db, version_id, **kw: None)


def _record(db_session, version_id, kind, content):
    orchestrator._record_message(
        db_session,
        version_id=version_id,
        stage="programovanie",
        author="manazer",
        recipient="ai_agent",
        kind=kind,
        content=content,
        payload={"phase": "programovanie"},
    )
    db_session.flush()


def _press(db_session, state):
    """What a button press does before the next turn (``_begin_dispatch``): the agent is working again."""
    state.status = "agent_working"
    db_session.flush()
    return state


def _no_more_planning(monkeypatch):
    async def _planned_again(**_kw):
        raise AssertionError("the plan was generated again")

    monkeypatch.setattr(orchestrator, "invoke_claude", _planned_again)


def _capture_plan_prompts(monkeypatch, plan):
    _stub_plan_passes(monkeypatch, plan)
    planned = orchestrator.invoke_claude
    prompts: list[str] = []

    async def _capture(*, prompt, **kw):
        prompts.append(prompt)
        return await planned(prompt=prompt, **kw)

    monkeypatch.setattr(orchestrator, "invoke_claude", _capture)
    return prompts


async def _at_plan_stop(db_session, monkeypatch, tmp_path, dial="plna"):
    version, _project = _make_version(db_session, source_path=str(tmp_path), project_dial=dial)
    state = _seed_programovanie(db_session, version.id)
    _no_baseline_git(monkeypatch)
    _stub_plan_passes(monkeypatch, PLAN)
    turns = _stub_turns(monkeypatch, [_done_block()])
    _stub_mech(monkeypatch, [None])
    settled = await orchestrator._run_build_round(db_session, state)
    return version, settled, turns


@pytest.mark.parametrize("dial", ["plna", "po_kazdej_faze"])
async def test_the_round_that_writes_the_plan_stops_before_any_task(db_session, monkeypatch, tmp_path, dial):
    version, settled, turns = await _at_plan_stop(db_session, monkeypatch, tmp_path, dial)

    assert (settled.current_stage, settled.status, settled.block_reason) == ("programovanie", "awaiting_manazer", None)
    assert settled.next_action == orchestrator.PLAN_STOP_NEXT_ACTION
    assert turns == [], "no task turn ran"
    assert [(t.status, t.baseline_sha) for t in _tasks(db_session, version.id)] == [("todo", None)] * 3
    assert _msgs(db_session, version.id)[-1].content == STOP_TEXT
    assert orchestrator.plan_awaits_start(db_session, version.id)


async def test_the_stop_says_how_long_the_agent_will_work(db_session, monkeypatch, tmp_path):
    monkeypatch.setattr(
        build_forecast,
        "forecast",
        lambda db, version_id, **kw: BuildForecast(minutes=72, spent=0, error=0.32, tasks_done=0, tasks_total=3),
    )

    version, _settled, _turns = await _at_plan_stop(db_session, monkeypatch, tmp_path)

    assert _msgs(db_session, version.id)[-1].content.startswith(
        "Plán úloh je zostavený: 2 epicy, 2 featy, 3 úlohy; práca agenta asi 1 h 10 min ± 30 %. Programovanie"
    )


async def test_the_board_offers_to_start_the_coding_not_to_finish_the_phase(db_session, monkeypatch, tmp_path):
    version, _settled, _turns = await _at_plan_stop(db_session, monkeypatch, tmp_path)

    actions = _board(db_session, version.id).available_actions

    assert "pokracovat" in actions and "uprav" in actions
    assert "schvalit" not in actions, "„Schváliť“ would skip the whole coding to Verifikácia"


async def test_starting_the_coding_runs_the_tasks_without_planning_again(db_session, monkeypatch, tmp_path):
    version, settled, turns = await _at_plan_stop(db_session, monkeypatch, tmp_path)
    _no_more_planning(monkeypatch)

    after = await orchestrator._run_build_round(db_session, _press(db_session, settled))  # no directive

    assert len(turns) == 3
    assert [t.status for t in _tasks(db_session, version.id)] == ["done"] * 3
    assert (after.status, after.next_action) != ("awaiting_manazer", orchestrator.PLAN_STOP_NEXT_ACTION)


async def test_uprav_at_the_stop_sends_the_plan_back_and_stops_again(db_session, monkeypatch, tmp_path):
    version, settled, turns = await _at_plan_stop(db_session, monkeypatch, tmp_path)
    comment = "Úloha audit_log je zbytočná, vypusti ju."
    _record(db_session, version.id, "return", comment)
    prompts = _capture_plan_prompts(monkeypatch, REWORKED)

    after = await orchestrator._run_build_round(
        db_session,
        _press(db_session, settled),
        directive=f"Manažér ťa vrátil na úpravu fázy 'programovanie': {comment}",
    )

    assert orchestrator.PLAN_REWORK_DIRECTIVE.format(comment=comment) in prompts[0], "the skeleton pass gets it"
    assert [t.title for t in _tasks(db_session, version.id)] == ["users table"], "the plan is replaced"
    assert turns == [], "no task turn ran"
    assert (after.status, after.next_action) == ("awaiting_manazer", orchestrator.PLAN_STOP_NEXT_ACTION)
    assert _msgs(db_session, version.id)[-1].content.startswith("Plán úloh je zostavený: 1 epic, 1 feat, 1 úloha.")


async def test_a_question_at_the_stop_is_answered_and_the_build_stays_at_the_plan(db_session, monkeypatch, tmp_path):
    version, settled, turns = await _at_plan_stop(db_session, monkeypatch, tmp_path)
    question = "Prečo je GL výpočet samostatná úloha?"
    _record(db_session, version.id, "question", question)

    after = await orchestrator._run_build_round(
        db_session, _press(db_session, settled), directive=f"Manažér sa pýta: {question}"
    )

    assert len(turns) == 1 and turns[0]["prompt"] == orchestrator.PLAN_QUESTION_DIRECTIVE.format(text=question)
    assert [(t.status, t.baseline_sha) for t in _tasks(db_session, version.id)] == [("todo", None)] * 3
    assert (after.status, after.next_action) == ("awaiting_manazer", orchestrator.PLAN_STOP_NEXT_ACTION)
    assert [m.content for m in _msgs(db_session, version.id)].count(STOP_TEXT) == 1, "announced once, not again"


async def test_a_fast_fix_runs_its_task_as_before(db_session, monkeypatch, tmp_path):
    version, project = _make_version(db_session, source_path=str(tmp_path), project_dial="plna")
    state = _seed_programovanie(db_session, version.id, flow_type="fast_fix")
    _seed_tasks(db_session, version, project, ["Oprava importu"])
    _no_baseline_git(monkeypatch)
    turns = _stub_turns(monkeypatch, [_done_block()])
    _stub_mech(monkeypatch, [None])
    _record(db_session, version.id, "return", "Oprav import podľa nálezu.")

    await orchestrator._run_build_round(db_session, state, directive="Oprav import podľa nálezu.")

    assert len(turns) == 1 and "Oprav import podľa nálezu." in turns[0]["prompt"]


async def test_after_the_first_task_started_uprav_goes_to_the_task_as_before(db_session, monkeypatch, tmp_path):
    version, project = _make_version(db_session, source_path=str(tmp_path), project_dial="plna")
    state = _seed_programovanie(db_session, version.id)
    _epic, _feat, tasks = _seed_tasks(db_session, version, project, ["users table", "audit_log"])
    tasks[0].baseline_sha = "a" * 40  # it started, then a cut-off dispatch reclaimed it to todo
    db_session.flush()
    _no_baseline_git(monkeypatch)
    _no_more_planning(monkeypatch)
    turns = _stub_turns(monkeypatch, [_done_block()])
    _stub_mech(monkeypatch, [None])
    directive = "Manažér ťa vrátil na úpravu fázy 'programovanie': Použi UUID ako kľúč."
    _record(db_session, version.id, "return", "Použi UUID ako kľúč.")

    await orchestrator._run_build_round(db_session, state, directive=directive)

    assert turns and directive in turns[0]["prompt"]
    assert not orchestrator.plan_awaits_start(db_session, version.id)


def test_the_counts_read_as_slovak():
    assert orchestrator._count_words(1, "úloha", "úlohy", "úloh") == "1 úloha"
    assert orchestrator._count_words(4, "úloha", "úlohy", "úloh") == "4 úlohy"
    assert orchestrator._count_words(20, "úloha", "úlohy", "úloh") == "20 úloh"


async def test_a_version_without_a_plan_is_not_awaiting_its_start(db_session):
    version, _project = _make_version(db_session)

    assert orchestrator.plan_awaits_start(db_session, version.id) is False


async def test_poradca_names_the_button_as_the_screen_does(db_session, monkeypatch, tmp_path):
    from backend.services.poradca import tools
    from tests.test_poradca_tools_db import _Ctx

    version, _settled, _turns = await _at_plan_stop(db_session, monkeypatch, tmp_path)
    monkeypatch.setattr(tools, "SessionLocal", lambda: _Ctx(db_session))
    t = tools.PoradcaTools(project_id=version.project_id, version_id=version.id, user_id=version.project_id)

    text = await t.stavba({})

    assert "„Spustiť programovanie“ (pokracovat)" in text
    assert "„Pokračovať“ (pokracovat)" not in text
