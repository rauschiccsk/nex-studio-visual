"""DEV-36 — after the Vizuál is drawn, the Manažér gets a short list of what to check in it, before the link.

Director 09.10.2026, approving the Vizuál of NEX Inbox 1.7.0: „Po vyhotovení vizuálu pred tým linkom na
samotný vizual pomohlo by mi krátky popis čo všetko treba prekontrolovať vo vizuáli." Until now the cockpit said
only „Vizuál je pripravený — otvor si ho: <link>"; what to look at was left to whatever the agent happened to
write in its report. Approved together with it (all of 3–7): a direct link to each screen, what the Vizuál
cannot show, a list after every change, ticking with a count at „Schváliť vizuál", the same list for Poradca.

Pinned here (the backend half — the ticking lives in the frontend tests):
* the agent is asked for the list in every Vizuál turn and the first draft's link message carries it, each item
  with a link straight to its screen in the preview (only a path inside the preview becomes a link);
* a turn without the list is asked for it once more; still nothing → the Manažér is told so next to the link;
* a change round gets its own list — without re-announcing the preview link;
* the board carries every list of the version from the database, not from the 50-message tail;
* Poradca's view of the build shows the same list.
"""

from backend.api.routes.pipeline import _board
from backend.services import orchestrator
from backend.services.pipeline_status import PipelineStatusBlock, VizualCheckItem, VizualChecklist
from backend.services.poradca import tools as poradca_tools
from backend.services.poradca.tools import PoradcaTools
from tests.test_orchestrator_v2_vizual import (
    _make_version,
    _msgs,
    _patch_spin_up,
    _seed_drawn,
    _seed_state,
    _stub_invoke_capture,
)

CHECKLIST = VizualChecklist(
    items=[
        VizualCheckItem(
            screen="Prehľad",
            path="/",
            action="Pozri kartu „Čaká na doručenie do Genesisu“.",
            expected="Karta ukazuje 2 faktúry a text „zdieľanie neodpovedá od …“.",
        ),
        VizualCheckItem(
            screen="Detail faktúry INB-I-000114",
            path="/invoices/INB-I-000114",
            action="Otvor faktúru, ktorá čaká na doručenie.",
            expected="Pokojný modrý pruh „Čaká na doručenie do Genesisu“.",
        ),
        VizualCheckItem(
            screen="Zoznam faktúr",
            path="https://iny-server.example/invoices",
            action="Nastav filter „Doručenie do Genesisu“.",
            expected="Ostanú len čakajúce a zastavené faktúry.",
        ),
    ],
    not_verifiable=["E-mail o výpadku zdieľania príde až s naprogramovaným doručovateľom."],
)


def _done(stage, checklist=CHECKLIST):
    return PipelineStatusBlock(
        stage=stage, kind="gate_report", summary="ok", awaiting="manazer", vizual_checklist=checklist
    )


def _checklist_notes(db_session, version_id):
    return [m for m in _msgs(db_session, version_id) if m.payload and m.payload.get("vizual_checklist")]


async def _first_draft(db_session, monkeypatch, blocks):
    version, project = _make_version(db_session)
    state = _seed_state(db_session, version.id, stage="vizual", actor="ai_agent")
    _patch_spin_up(monkeypatch)
    queue = list(blocks)
    calls = _stub_invoke_capture(monkeypatch, lambda s: queue.pop(0)(s) if len(queue) > 1 else queue[0](s))
    settled = await orchestrator._run_vizual_round(db_session, state)
    return version, project, settled, calls


async def test_every_vizual_turn_asks_for_the_list(db_session, monkeypatch):
    version, _, _, calls = await _first_draft(db_session, monkeypatch, [_done])

    assert "`vizual_checklist`" in calls[0]["prompt"]
    assert "`not_verifiable`" in calls[0]["prompt"]
    change = orchestrator._vizual_directive(db_session, version.id, "Zväčši súčet.")
    assert "`vizual_checklist`" in change and "len to, čoho sa zmena dotkla" in change


async def test_the_link_message_carries_the_list_with_a_link_to_each_screen(db_session, monkeypatch):
    version, project, settled, _ = await _first_draft(db_session, monkeypatch, [_done])

    base = f"https://vizual-{project.slug}.isnex.eu"
    [note] = _checklist_notes(db_session, version.id)
    assert note.payload["vizual_url"] == base and base in note.content
    assert note.payload["vizual_checklist"] == {
        "round": "first",
        "items": [
            {
                "screen": "Prehľad",
                "action": "Pozri kartu „Čaká na doručenie do Genesisu“.",
                "expected": "Karta ukazuje 2 faktúry a text „zdieľanie neodpovedá od …“.",
                "url": f"{base}/",
            },
            {
                "screen": "Detail faktúry INB-I-000114",
                "action": "Otvor faktúru, ktorá čaká na doručenie.",
                "expected": "Pokojný modrý pruh „Čaká na doručenie do Genesisu“.",
                "url": f"{base}/invoices/INB-I-000114",
            },
            {
                # A path that leaves the preview is never turned into a link.
                "screen": "Zoznam faktúr",
                "action": "Nastav filter „Doručenie do Genesisu“.",
                "expected": "Ostanú len čakajúce a zastavené faktúry.",
                "url": None,
            },
        ],
        "not_verifiable": ["E-mail o výpadku zdieľania príde až s naprogramovaným doručovateľom."],
    }
    # The list sits right before the link: the link message comes after the agent's own report.
    assert [m.author for m in _msgs(db_session, version.id)][-1] == "system"
    assert settled.status == "awaiting_manazer"


async def test_a_turn_without_the_list_is_asked_for_it_once_more(db_session, monkeypatch):
    version, _, settled, calls = await _first_draft(
        db_session, monkeypatch, [lambda s: _done(s, checklist=None), _done]
    )

    assert len(calls) == 2
    assert "kontrolný zoznam" in calls[1]["prompt"] and "`vizual_checklist`" in calls[1]["prompt"]
    [note] = _checklist_notes(db_session, version.id)
    assert len(note.payload["vizual_checklist"]["items"]) == 3
    assert settled.status == "awaiting_manazer"


async def test_still_no_list_the_manager_is_told_next_to_the_link(db_session, monkeypatch):
    version, project, settled, calls = await _first_draft(db_session, monkeypatch, [lambda s: _done(s, checklist=None)])

    assert len(calls) == 2  # asked once more, not forever
    assert _checklist_notes(db_session, version.id) == []
    [note] = [m for m in _msgs(db_session, version.id) if m.payload and m.payload.get("vizual_url")]
    assert note.payload["vizual_checklist_missing"] is True
    assert note.content == (
        f"Vizuál je pripravený — otvor si ho: https://vizual-{project.slug}.isnex.eu\n\n"
        "AI partner nedodal zoznam, čo vo Vizuáli skontrolovať. Prezri si Vizuál podľa jeho hlásenia vyššie."
    )
    assert settled.status == "awaiting_manazer"


async def test_a_change_round_gets_its_own_list_without_a_second_link_announcement(db_session, monkeypatch):
    version, project = _make_version(db_session)
    state = _seed_state(db_session, version.id, stage="vizual", actor="ai_agent")
    _seed_drawn(db_session, version.id)
    _patch_spin_up(monkeypatch)
    after_change = VizualChecklist(
        items=[VizualCheckItem(screen="Prehľad", path="/", action="Pozri súčet.", expected="Súčet je väčší.")]
    )
    _stub_invoke_capture(monkeypatch, lambda s: _done(s, checklist=after_change))

    await orchestrator._run_vizual_round(db_session, state)  # fresh entry: announces the link
    await orchestrator._run_vizual_round(db_session, state, directive="Zväčši súčet.")

    links = [m for m in _msgs(db_session, version.id) if m.payload and m.payload.get("vizual_url")]
    assert len(links) == 1
    [change] = _checklist_notes(db_session, version.id)
    assert change.content == "Zmena je vo Vizuáli — čo po nej skontrolovať:"
    assert change.payload["vizual_checklist"]["round"] == "change"
    assert change.payload["vizual_checklist"]["items"][0]["url"] == f"https://vizual-{project.slug}.isnex.eu/"


async def test_the_board_carries_every_list_even_past_the_message_tail(db_session, monkeypatch):
    version, _, _, _ = await _first_draft(db_session, monkeypatch, [_done])
    for i in range(60):  # push the link message far out of the 50-message tail
        orchestrator._record_message(
            db_session,
            version_id=version.id,
            stage="vizual",
            author="manazer",
            recipient="ai_agent",
            kind="answer",
            content=f"správa {i}",
        )
    db_session.flush()

    board = _board(db_session, version.id)

    assert not [m for m in board.recent_messages if m.payload and m.payload.get("vizual_checklist")]
    [listed] = board.vizual_checklists
    assert listed.round == "first" and len(listed.items) == 3
    assert listed.items[1].url.endswith("/invoices/INB-I-000114")
    assert listed.seq == _checklist_notes(db_session, version.id)[0].seq


class _SameSession:
    """``SessionLocal()`` for Poradca's tool: hand back the test's own session and leave it open."""

    def __init__(self, session):
        self._s = session

    def __enter__(self):
        return self._s

    def __exit__(self, *exc):
        return False


async def test_poradca_sees_the_same_list(db_session, monkeypatch):
    version, project, _, _ = await _first_draft(db_session, monkeypatch, [_done])
    monkeypatch.setattr(poradca_tools, "SessionLocal", lambda: _SameSession(db_session))

    out = await PoradcaTools(project_id=project.id, version_id=version.id, user_id=project.created_by).stavba({})

    assert "Čo má Manažér vo Vizuáli skontrolovať (prvý návrh):" in out
    assert (
        "2. Detail faktúry INB-I-000114 — Otvor faktúru, ktorá čaká na doručenie. → Pokojný modrý pruh "
        f"„Čaká na doručenie do Genesisu“. (https://vizual-{project.slug}.isnex.eu/invoices/INB-I-000114)"
    ) in out
    assert "Vo Vizuáli sa overiť nedá: E-mail o výpadku zdieľania príde až s naprogramovaným doručovateľom." in out


async def test_once_the_vizual_is_approved_poradca_no_longer_lists_it(db_session, monkeypatch):
    version, project, settled, _ = await _first_draft(db_session, monkeypatch, [_done])
    monkeypatch.setattr(poradca_tools, "SessionLocal", lambda: _SameSession(db_session))
    settled.current_stage = "programovanie"
    db_session.flush()

    out = await PoradcaTools(project_id=project.id, version_id=version.id, user_id=project.created_by).stavba({})

    assert "Fáza: programovanie" in out
    assert "vo Vizuáli skontrolovať" not in out
