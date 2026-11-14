"""DEV-376 — the cockpit's sentences name the buttons the way the screen draws them.

14.11.2026, measured on kokpit 4.139.6: 55 places of the backend wrote a button's name into a sentence by hand. Two of
them already sent the Manažér to a button the screen did not draw — „Manažér: posúdiť výsledok Programovania (Schváliť /
Uprav).“ next to „Prejsť na overenie“ and „Upraviť“, „Pozastavené Manažérom — pokračuj cez 'Pokračovať'.“ next to
„Pokračovať v stavbe“. DEV-373 had put the names in one place (``backend/services/action_labels.py``) for the screen and
Poradca; the sentences still kept their own copies. Director, on all the buttons in all 55 places: „Súhlasím“.

A sentence names a button by its action (``action_labels.press``); the name is filled in for the state it is shown with.
Two rules, not lists: no string of the backend writes a button's name itself, and the state's sentence never leaves
the cockpit without its names filled in.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from backend.services import action_labels
from tests.test_a_button_is_named_in_one_place import _every_name

BACKEND = Path(__file__).resolve().parents[1] / "backend"
_LOWER = "a-záäčďéíĺľňóôŕšťúýž"


def _first_words() -> set[str]:
    """„Schváliť“ of „Schváliť na Hotovo“ — the short form a hand-written sentence uses for a button."""
    return {name.split()[0] for name in _every_name() if " " in name}


def _button_pattern(*, of_the_build: bool = True) -> re.Pattern[str]:
    """A name standing as a button: quoted, in brackets, bold, either side of „/“ — or a one-word name written with its
    capital in the middle of a sentence („skús Uprav“). The short form („Schváliť“) quoted on its own, or a quoted name
    that only starts like a button (a stale copy, „Vrátiť agentovi na opravu“ of „Vrátiť Staviteľovi na opravu“), is a
    build button only where the build is spoken of — NEX Manager's own „Spustiť“ (launch an application) is not."""
    whole = "|".join(sorted(map(re.escape, _every_name()), key=len, reverse=True))
    short = "|".join(sorted(map(re.escape, _first_words()), key=len, reverse=True))
    single = "|".join(sorted(map(re.escape, (n for n in _every_name() if " " not in n)), key=len, reverse=True))
    quoted = f"{whole}|{short}" if of_the_build else whole
    # a quoted name that only starts like a button („Vrátiť agentovi na opravu“) is a stale copy of one
    stale = rf"|„(?:{short})\b[^“]*“" if of_the_build else ""
    return re.compile(
        rf"[„'\"](?:{quoted})[“'\"]{stale}"
        rf"|[(*](?:{whole}|{short})[)*]"
        rf"|(?:/ )(?:{whole}|{short})\b|(?:{whole}|{short}) /"
        rf"|[(„'\"*](?:{whole})(?=[)“'\"*,.])"
        rf"|(?<=[{_LOWER}] )(?:{single})(?![\w])"
    )


#: A module that speaks of the build — it reads or writes the build's state or its messages.
_OF_THE_BUILD = re.compile(r"\bPipeline(?:State|Message)\b")


def _docstrings(tree: ast.AST) -> set[int]:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                found.add(id(body[0].value))
    return found


def _written_names(root: Path) -> list[str]:
    patterns = {flag: _button_pattern(of_the_build=flag) for flag in (True, False)}
    found = []
    for path in sorted(root.rglob("*.py")):
        if "tests" in path.parts or path.name == "action_labels.py":
            continue
        source = path.read_text(encoding="utf-8")
        pattern = patterns[bool(_OF_THE_BUILD.search(source))]
        tree = ast.parse(source)
        docs = _docstrings(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
                for match in pattern.finditer(node.value):
                    found.append(f"{path.relative_to(root.parent)}:{node.lineno}: {match.group(0)}")
    return found


def test_the_rule_recognises_every_way_a_name_was_written():
    """The shapes found on 14.11.2026 — the guard below is only as good as this pattern."""
    pattern = _button_pattern()
    for text in (
        "Usmerni (Uprav) alebo odpovedz.",
        "posúdiť výsledok (Schváliť / Uprav).",
        "pokračuj cez 'Pokračovať'.",
        "dokonči („Pokračovať“) alebo usmerni („Uprav“)",
        "(**Schváliť** / **Uprav**)",
        "daj mu chvíľu a skús Uprav.",
        "sa ponúkne „Schváliť na Hotovo“, keď",
        "klikni „Spustiť programovanie“, alebo",
    ):
        assert pattern.search(text), text
    for text in ("Schválil si plán.", "Upravené súbory: 3", "Pokračovanie stavby", "posúdiť výsledok Programovania"):
        assert not pattern.search(text), text
    assert pattern.search("vráť ho tlačidlom „Schváliť“")
    assert pattern.search("vráť prácu agentovi na opravu („Vrátiť agentovi na opravu“).")
    assert not _button_pattern(of_the_build=False).search("(tlačidlo „Spustiť“) zatiaľ nefunguje")
    assert _button_pattern(of_the_build=False).search("stlač „Pokračovať v stavbe“")


def test_no_string_of_the_backend_writes_a_buttons_name_itself():
    written = _written_names(BACKEND)
    assert written == [], (
        "a sentence names the button by its action — action_labels.press('…') — and the cockpit fills in the name the "
        "screen draws:\n  " + "\n  ".join(written)
    )


def test_a_pressed_button_reads_as_the_screen_draws_it():
    labels = {"uprav": "Skús znova", "schvalit": "Prejsť na overenie"}
    text = f"Napíš, čo inak, a stlač {action_labels.press('uprav')}; inak {action_labels.press('schvalit')}."
    named = action_labels.name_buttons(text, labels)
    assert named == "Napíš, čo inak, a stlač „Skús znova“; inak „Prejsť na overenie“."
    # a button the state draws under no name keeps its plain name — never the marker
    assert action_labels.name_buttons(action_labels.press("pause"), {}) == "„Pozastaviť“"


def test_an_action_without_a_button_cannot_be_pressed():
    with pytest.raises(ValueError):
        action_labels.press("decide")


# ─── the sentence leaves the cockpit only with its names filled in ─────────────────────────────────────────────────

_MARKER = "⟦"
_QUOTED = re.compile(r"„([^“]+)“")


def test_the_state_goes_out_only_named():
    """Rule: the state is turned into what goes out (``PipelineStateRead``) only by ``action_labels.read_state``, and
    its sentence is read only there — or off a board, which named it. The engine carries the sentence between its own
    fields (the question asked during the Decision Cards keeps it aside) and is the one other reader."""
    raw = []
    for path in sorted(BACKEND.rglob("*.py")):
        if "tests" in path.parts or path.name == "action_labels.py":
            continue
        source = path.read_text(encoding="utf-8")
        if "PipelineStateRead.model_validate(" in source:
            raw.append(f"{path.relative_to(BACKEND.parent)}: PipelineStateRead.model_validate(")
        if path.name == "orchestrator.py":
            continue
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Attribute) and node.attr == "next_action" and isinstance(node.ctx, ast.Load):
                owner = ast.unparse(node.value)
                if not owner.endswith("board.state"):
                    raw.append(f"{path.relative_to(BACKEND.parent)}:{node.lineno}: {owner}.next_action")
    assert raw == [], (
        "the state's sentence goes out through action_labels (read_state / state_sentence):\n  " + "\n  ".join(raw)
    )


@pytest.fixture
def built(db_session, monkeypatch, tmp_path):
    from backend.services import orchestrator
    from tests.test_orchestrator_v2_verifikacia import _make_version, _seed_verifikacia

    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", tmp_path)
    version, project = _make_version(db_session, project_dial="plna")
    return version, project, _seed_verifikacia(db_session, version.id)


def _names_only_what_the_board_draws(text: str, labels: dict[str, str]) -> None:
    assert _MARKER not in text, text
    for name in _QUOTED.findall(text):
        assert name in labels.values(), f"„{name}“ is no button of this state: {sorted(labels.values())}"


def test_after_programovanie_the_sentence_names_the_buttons_on_screen(db_session, built, monkeypatch):
    """The first of the two that lied on 14.11.2026: „(Schváliť / Uprav)“ beside „Prejsť na overenie“ / „Upraviť“."""
    from backend.api.routes.pipeline import _board
    from backend.services import orchestrator

    _version, _project, state = built
    monkeypatch.setattr(orchestrator, "build_readiness", lambda _db, _vid: (True, 0))
    state.current_stage, state.status, state.block_reason = "programovanie", "awaiting_manazer", None
    state.next_action = orchestrator.PROGRAMOVANIE_NEXT_ACTION
    db_session.flush()

    board = _board(db_session, state.version_id)

    assert board.state.next_action == "Manažér: posúdiť výsledok Programovania („Prejsť na overenie“ / „Upraviť“)."
    _names_only_what_the_board_draws(board.state.next_action, board.action_labels)


def test_a_pause_by_the_manazer_names_the_resume_as_drawn(db_session, built):
    """The second: „pokračuj cez 'Pokračovať'“ beside „Pokračovať v stavbe“."""
    from backend.api.routes.pipeline import _board
    from backend.services import orchestrator

    _version, _project, state = built
    state.current_stage, state.status, state.pause_reason = "programovanie", "paused", "manazer"
    state.next_action = orchestrator.PAUSED_BY_MANAZER_NEXT_ACTION
    db_session.flush()

    board = _board(db_session, state.version_id)

    assert board.state.next_action == "Pozastavené Manažérom — pokračuj tlačidlom „Pokračovať v stavbe“."


def test_a_live_state_frame_names_the_buttons_as_the_board_does(db_session, built, monkeypatch):
    """Tasks remain → the board holds the approval back and the edit button is the conversation box's „Uprav“; the state
    alone would have said the approve bar's „Upraviť“. A live frame names it as the board will."""
    from backend.api.routes.pipeline import _board
    from backend.services import orchestrator

    _version, _project, state = built
    monkeypatch.setattr(orchestrator, "build_readiness", lambda _db, _vid: (False, 0))
    state.current_stage, state.status, state.block_reason = "programovanie", "awaiting_manazer", None
    state.next_action = f"Vráť prácu tlačidlom {action_labels.press('uprav')}."
    db_session.flush()

    frame = action_labels.read_state(db_session, state)

    assert frame.next_action == "Vráť prácu tlačidlom „Uprav“."
    assert frame.next_action == _board(db_session, state.version_id).state.next_action


def test_a_message_names_its_button_as_the_stop_it_announces(db_session, built):
    """A message goes out at once — written after the stop it announces is settled, it names that stop's button, in its
    text and in what its payload shows (a Decision Card's option)."""
    from backend.services import orchestrator

    _version, _project, state = built
    state.current_stage, state.status, state.block_reason = "programovanie", "blocked", "check_failed"
    db_session.flush()

    msg = orchestrator._record_message(
        db_session,
        version_id=state.version_id,
        stage="programovanie",
        author="system",
        recipient="manazer",
        kind="notification",
        content=f"Kontrola neprešla. Stlač {action_labels.press('uprav')}.",
        payload={"options": [{"detail": f"neskôr tlačidlom {action_labels.press('uprav')}"}]},
    )

    assert msg.content == "Kontrola neprešla. Stlač „Skús znova“."
    assert msg.payload == {"options": [{"detail": "neskôr tlačidlom „Skús znova“"}]}


async def test_a_refused_action_names_the_buttons_of_the_screen_it_came_from(db_session, built):
    from backend.api.routes.pipeline import _map_orch_error
    from backend.services import orchestrator

    _version, _project, state = built
    state.current_stage, state.status, state.pause_reason = "programovanie", "paused", "manazer"
    db_session.flush()

    with pytest.raises(orchestrator.OrchestratorError) as refused:
        await orchestrator.apply_action(db_session, version_id=state.version_id, action="ask", payload={"text": "?"})
    detail = _map_orch_error(db_session, state.version_id, refused.value).detail

    assert (
        detail == "Build je pozastavený — pokračuj tlačidlom „Pokračovať v stavbe“, alebo ho usmerni tlačidlom „Uprav“."
    )


def test_the_dedo_door_reads_the_named_sentence(db_session, built):
    from backend.api.routes.dedo import _read

    version, project, state = built
    state.current_stage, state.status, state.block_reason = "programovanie", "blocked", "agent_error"
    state.next_action = f"Zlyhalo. Stlač {action_labels.press('uprav')}."
    db_session.flush()

    assert _read(db_session, version, project, state).next_action == "Zlyhalo. Stlač „Skús znova“."


async def test_what_the_agent_is_told_to_tell_the_manazer_names_the_buttons(db_session, built, monkeypatch):
    from backend.services import orchestrator

    _version, _project, state = built
    monkeypatch.setattr(orchestrator, "build_readiness", lambda _db, _vid: (False, 0))  # the plan stop: tasks remain
    state.current_stage, state.status, state.block_reason = "programovanie", "awaiting_manazer", None
    db_session.flush()
    prompts = []

    async def _fake(**kw):
        prompts.append(kw["prompt"])
        return ""

    monkeypatch.setattr(orchestrator, "invoke_claude", _fake)
    await orchestrator.invoke_agent(
        db_session,
        version_id=state.version_id,
        role=orchestrator.AI_AGENT_ROLE,
        stage="programovanie",
        prompt=orchestrator.PLAN_QUESTION_DIRECTIVE.format(text="Prečo je plán taký veľký?"),
    )

    assert prompts and _MARKER not in prompts[0]
    assert "nech plán vráti tlačidlom „Uprav“ s touto pripomienkou" in prompts[0]


def test_the_boards_ci_gate_names_the_held_step(db_session, built):
    from datetime import datetime, timezone

    from backend.api.routes.pipeline import _board

    _version, _project, state = built
    state.current_stage, state.status, state.block_reason = "programovanie", "awaiting_manazer", None
    state.ci_pending_since = datetime.now(timezone.utc)
    db_session.flush()

    gate = _board(db_session, state.version_id).ci_gate

    assert gate is not None and "„Prejsť na overenie“ sa ponúkne" in gate.message and _MARKER not in gate.message


def test_a_restart_names_the_resume_as_the_screen_draws_it_after_the_restart(db_session, monkeypatch):
    """Written after the state is settled: before it, the build still read as working and the resume as „Pokračovať v
    stavbe“; the screen after a restart draws „Pokračovať“."""
    from backend.api.routes.pipeline import _board
    from tests.test_a_build_cut_off_by_a_restart_can_go_on import _recovered
    from tests.test_orchestrator_v2_dispatch_lifecycle import _msgs

    version, _state = _recovered(db_session, monkeypatch, "programovanie", "ai_agent")

    assert _msgs(db_session, version.id)[-1].content.endswith("Pokračuj tlačidlom „Pokračovať“.")
    assert _board(db_session, version.id).state.next_action == (
        "Programovanie prerušené reštartom backendu — pokračuj tlačidlom „Pokračovať“."
    )


async def test_a_consultation_that_fell_back_names_the_approve_bars_buttons(db_session, built):
    """Written after the stop is settled: while the agent still worked, the edit button was the conversation box's
    „Uprav“; at the stop the approve bar carries „Upraviť“ beside the approval."""
    from backend.services import orchestrator
    from tests.test_orchestrator_v2_dispatch_lifecycle import _msgs

    _version, _project, state = built
    state.current_stage, state.status, state.block_reason = "navrh", "agent_working", None
    db_session.flush()

    await orchestrator._consult_fallback(
        db_session,
        state,
        note=f"Posúď návrh: {action_labels.press('schvalit')}, alebo {action_labels.press('uprav')}.",
        on_message=None,
    )

    assert _msgs(db_session, state.version_id)[-1].content == "Posúď návrh: „Schváliť plán“, alebo „Upraviť“."
