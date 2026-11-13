"""DEV-373 — every build button is named in one place: the screen draws what Poradca says.

13.11.2026, NEX Legal Watch 0.1.0 after its Verifikácia: Poradca told the Manažér to press „Schváliť“, the screen drew
„Schváliť na Hotovo“. The screen's bars wrote their own names (the same ``schvalit`` reads differently in each phase),
Poradca kept a hand-made copy, and its test checked only that every action was in the copy. Nine names had drifted
apart (also „Vrátiť agentovi na opravu“ against „Vrátiť Staviteľovi na opravu“, „Hotovo“ against „Označiť ako
hotové“). Director, on naming the buttons in one place: „Áno oprav.“

The cockpit works the names out for the build's state (``backend/services/action_labels.py``) and hands them out on
the board; the screen draws them, Poradca reads them. A rule, not a list: no component of the screen writes the
name of a build button itself.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from backend.api.routes.pipeline import _board
from backend.services import action_labels
from tests.test_orchestrator_v2_verifikacia import _make_version, _seed_verifikacia

ROOT = Path(__file__).resolve().parents[1]
SCREEN = ROOT / "frontend" / "src"
GENERATED = SCREEN / "services" / "api" / "actionLabels.generated.json"

#: A component that sends a build action draws its button — its name must come from the board.
_SENDS_A_BUILD_ACTION = re.compile(r"postPipelineActionApi|runTrigger\(")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_LINE_COMMENT = re.compile(r"(?m)(^|[\s;{}(),])//.*$")


def _code_of(path: Path) -> str:
    """The component without its comments — a comment may name a button, the code may not."""
    return _LINE_COMMENT.sub(r"\1", _BLOCK_COMMENT.sub(" ", path.read_text(encoding="utf-8")))


def _every_name() -> set[str]:
    """Every name a build button can carry — read off the module's constants, not listed again here."""
    names: set[str] = set()
    for key, value in vars(action_labels).items():
        if key.isupper() and isinstance(value, str):
            names.add(value)
        elif key.isupper() and isinstance(value, dict):
            names.update(v for v in value.values() if isinstance(v, str))
    return names


def test_every_name_counts_the_variants_too():
    assert {"Schváliť na Hotovo", "Skús znova", "Spustiť programovanie", "Označiť ako hotové"} <= _every_name()


def _screen_files() -> list[Path]:
    """The components that send a build action — the word „Upraviť“ on a project's settings is no build button."""
    return [
        p
        for p in sorted(SCREEN.rglob("*.ts*"))
        if "__tests__" not in p.parts
        and ".test." not in p.name
        and not p.name.endswith(".generated.ts")
        and p.name != "pipeline.ts"
        and _SENDS_A_BUILD_ACTION.search(p.read_text(encoding="utf-8"))
    ]


def test_no_screen_component_writes_a_buttons_name_itself():
    files = _screen_files()
    assert len(files) >= 15, "the screen's build bars were not found — the guard would watch nothing"
    written = []
    for path in files:
        code = _code_of(path)
        for text in _every_name():
            quoted = re.escape(text)
            if re.search(rf"[\"'`]{quoted}[\"'`]|>\s*{quoted}\s*<", code):
                written.append(f"{path.relative_to(ROOT)}: {text}")
    assert written == []


def test_the_screens_plain_names_are_the_cockpits():
    assert json.loads(GENERATED.read_text(encoding="utf-8")) == action_labels.BASE


@pytest.fixture
def built(db_session, monkeypatch, tmp_path):
    from backend.services import orchestrator

    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", tmp_path)
    version, project = _make_version(db_session, project_dial="plna")
    return version, _seed_verifikacia(db_session, version.id)


@pytest.fixture
def state(built):
    return built[1]


@pytest.mark.parametrize(
    ("stage", "name"),
    [
        ("navrh", "Schváliť plán"),
        ("vizual", "Schváliť vizuál"),
        ("programovanie", "Prejsť na overenie"),
        ("verifikacia", "Schváliť na Hotovo"),
    ],
)
def test_approve_is_named_by_the_gate_it_passes(db_session, state, stage, name):
    state.current_stage, state.status, state.block_reason = stage, "awaiting_manazer", None
    db_session.flush()

    assert action_labels.action_labels(db_session, state)["schvalit"] == name


@pytest.mark.parametrize("reason", sorted(action_labels.RETRY_REASONS))
def test_rework_on_an_error_or_a_failed_check_is_a_retry(db_session, state, reason):
    state.status, state.block_reason = "blocked", reason
    db_session.flush()

    assert action_labels.action_labels(db_session, state)["uprav"] == "Skús znova"


def test_rework_of_a_phase_is_named_as_before(db_session, state):
    state.status, state.block_reason = "awaiting_manazer", None
    db_session.flush()

    assert action_labels.action_labels(db_session, state)["uprav"] == "Upraviť"


@pytest.mark.parametrize(
    "released",
    [
        {"resume_after_framework_fix": True},
        {"resume_after_restart": True},
        {"status": "paused", "pause_reason": "fix_ready"},
    ],
)
def test_continue_after_a_release_is_one_word(db_session, state, released):
    state.current_stage = "programovanie"
    for key, value in released.items():
        setattr(state, key, value)
    db_session.flush()

    assert action_labels.action_labels(db_session, state)["pokracovat"] == "Pokračovať"


def test_continue_at_the_stop_after_the_plan_starts_the_coding(db_session, state, monkeypatch):
    from backend.services import orchestrator

    state.current_stage, state.status, state.pause_reason = "programovanie", "paused", None
    db_session.flush()
    monkeypatch.setattr(orchestrator, "plan_awaits_start", lambda db, vid: vid == state.version_id)
    assert action_labels.action_labels(db_session, state)["pokracovat"] == "Spustiť programovanie"

    monkeypatch.setattr(orchestrator, "plan_awaits_start", lambda db, vid: False)
    assert action_labels.action_labels(db_session, state)["pokracovat"] == "Pokračovať v stavbe"


@pytest.mark.parametrize(
    ("stage", "reason", "name"),
    [
        ("verifikacia", None, "Znova spustiť overenie"),
        ("verifikacia", "decision_needed", "Znova overiť bez opravy"),
        ("programovanie", None, "Znova overiť bez opravy"),
    ],
)
def test_verify_without_a_fix_says_whether_it_reruns_a_stalled_verification(db_session, state, stage, reason, name):
    state.current_stage, state.status, state.block_reason = stage, "blocked", reason
    db_session.flush()

    assert action_labels.action_labels(db_session, state)["overit_bez_opravy"] == name


def test_the_board_hands_out_every_buttons_name_for_its_state(db_session, state):
    state.current_stage, state.status, state.block_reason = "verifikacia", "awaiting_manazer", None
    db_session.flush()

    labels = _board(db_session, state.version_id).action_labels

    assert set(labels) == set(action_labels.BASE)
    assert labels["schvalit"] == "Schváliť na Hotovo"


async def test_poradca_names_the_offered_buttons_with_the_boards_words(db_session, built, monkeypatch):
    """The same words reach Poradca — read from the board; the agents' own actions are no button of the Manažér's."""
    from backend.api.routes import pipeline as pipeline_routes
    from backend.services.poradca import tools

    version, state = built
    state.status, state.block_reason = "awaiting_manazer", None
    db_session.flush()
    board = _board(db_session, version.id)
    board.available_actions = ["ask", "schvalit", "uprav", "verdict"]

    class _Session:
        def __enter__(self):
            return db_session

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(tools, "SessionLocal", _Session)
    monkeypatch.setattr(pipeline_routes, "_board", lambda db, vid, limit=None: board)
    text = await tools.PoradcaTools(project_id=version.project_id, version_id=version.id, user_id=None).stavba({})

    line = next(x for x in text.splitlines() if x.startswith("Tlačidlá, ktoré Manažér"))
    assert (
        line
        == "Tlačidlá, ktoré Manažér v Riadiacom centre práve vidí: „Schváliť na Hotovo“ (schvalit); „Upraviť“ (uprav)."
    )
