"""DEV-384 — what the cockpit tells the Manažér in the conversation and in the state's sentence is Slovak.

14.11.2026, found while DEV-376 renamed the buttons of every sentence: when an agent's turn crashed in a way the engine
does not recognise, the runner's last-resort settle (``pipeline_runner._mark_blocked``) wrote the Manažér „Agent
dispatch failed: <the exception> — pipeline blocked.“ — English, with the raw exception as its sentence, and no word on
what to press. The state's sentence beside it was Slovak. Nothing checked the language of the texts written for the
Manažér. Director, on the ticket and on doing it: „Áno“.

DEV-385 — the same for the error texts the screen shows: an action the engine refuses (``Orchestrator*`` errors) and an
error a route answers with (``HTTPException(detail=…)``). 14.11.2026 there were 13 English refusals („Pipeline already
started for this version“) and 20 English route errors — the login's „Invalid username or password“ among them; the
routes decided 404 / 409 by English words in the text, so the kind now goes by the error's type
(``backend/core/errors.py``). Director: „Áno založ samostatný tiket a implementuj.“

A rule, not a list: every message the backend writes to the Manažér, every state sentence it sets, every refusal of the
engine and every route's error text is read for English — the cockpit's own sentence rule
(``block_language.english_sentences``, Poradca's check) and, for a short text it cannot judge, a Slovak sentence carries
a diacritic or a common Slovak word.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from backend.services.poradca.block_language import _SLOVAK, _words, english_sentences

BACKEND = Path(__file__).resolve().parents[1] / "backend"
_DIACRITICS = re.compile(r"[áäčďéíĺľňóôŕšťúýž]", re.I)
#: Common Slovak words without a diacritic that Poradca's list does not need („Pripravujem rozhodnutia znova…“,
#: „Namiesto mazania ho archivuj“).
_ALSO_SLOVAK = frozenset({"znova", "ho"})


def _text(node: ast.AST) -> str | None:
    """The words of a string expression — an f-string's values stand as ``X``, a conditional gives both branches."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else " X " for v in node.values)
    if isinstance(node, ast.BinOp):
        return (_text(node.left) or "") + (_text(node.right) or "")
    if isinstance(node, ast.IfExp):
        return (_text(node.body) or "") + " | " + (_text(node.orelse) or "")
    if isinstance(node, ast.Call):  # a sentence handed through the naming helpers — action_labels.say(db, state, "…")
        name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
        sentence = {"say": 2, "name_buttons": 0}.get(name or "")
        if sentence is not None and len(node.args) > sentence:
            return _text(node.args[sentence])
    return None


#: Where one sentence ends — and the two branches of a conditional text (``_text`` joins them with „ | “).
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+|\n+| \| ")


def _english_sentence(sentence: str) -> bool:
    """The words of speech (Poradca's split: no path, file, flag or number; ``X`` stands for an f-string's value)."""
    words = [w for w in _words(sentence) if w != "x"]
    return len(words) >= 3 and not _DIACRITICS.search(sentence) and not any(w in _SLOVAK | _ALSO_SLOVAK for w in words)


def is_english(text: str) -> bool:
    """Any sentence of it English — a Slovak sentence beside it does not cover an English one."""
    return bool(english_sentences(text)) or any(_english_sentence(s) for s in _SENTENCE_END.split(text))


def _told_the_manazer(root: Path) -> list[tuple[str, str, str]]:
    """(kind, place, text) of every message written to the Manažér, every state sentence the backend sets, every refusal
    of the engine and every error text of a route."""
    found = []
    for path in sorted(root.rglob("*.py")):
        if "tests" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            expr, kind = None, ""
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Attribute) and t.attr == "next_action" for t in node.targets
            ):
                expr, kind = node.value, "sentence"
            elif isinstance(node, ast.Call):
                name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
                kw = {k.arg: k.value for k in node.keywords}
                to = kw.get("recipient")
                if (
                    name in ("_record_message", "PipelineMessage")
                    and isinstance(to, ast.Constant)
                    and to.value == "manazer"
                ):
                    expr, kind = kw.get("content"), "message"
                elif (name or "").startswith("Orchestrator") and node.args:  # the engine refuses an action
                    expr, kind = node.args[0], "refusal"
                elif name == "HTTPException" and "detail" in kw:
                    expr, kind = kw["detail"], "route error"
            text = _text(expr) if expr is not None else None
            if text:
                found.append((kind, f"{path.relative_to(root.parent)}:{node.lineno}", " ".join(text.split())))
    return found


def test_the_rule_tells_english_from_slovak():
    for text in (
        "Agent dispatch failed X — pipeline blocked. Dôvod je nižšie v technickom detaile.",
        "Pipeline not started for this version",
        "The build must not run without a plan and it is not ready.",
    ):
        assert is_english(text), text
    for text in (
        "Pripravujem rozhodnutia znova…",
        "Ťah agenta sa nečakane zrútil a stavba stojí.",
        "Manažér: rozhodni X/X (konzultácia).",
        "Pozastavujem — Staviteľ dokončí rozrobenú úlohu a potom zastane.",
    ):
        assert not is_english(text), text


def test_the_scan_finds_what_the_manazer_is_told():
    """Both kinds, each on its own — a scan that reads only the state sentences would still count to forty."""
    found = _told_the_manazer(BACKEND)
    kinds = {
        kind: [place for k, place, _ in found if k == kind]
        for kind in ("message", "sentence", "refusal", "route error")
    }
    floor = {"message": 30, "sentence": 40, "refusal": 60, "route error": 80}  # 14.11.2026: 44, 52, 74, 100
    assert all(len(kinds[k]) >= n for k, n in floor.items()), {k: len(v) for k, v in kinds.items()}
    assert any("pipeline_runner.py" in place for place in kinds["message"]), (
        "the runner's last-resort message is not seen"
    )
    assert any("auth.py" in place for place in kinds["route error"]), "the login's error is not seen"


def test_nothing_the_manazer_is_told_is_english():
    english = [f"{place}: {text[:120]}" for _kind, place, text in _told_the_manazer(BACKEND) if is_english(text)]
    assert english == [], "the Manažér reads the cockpit in Slovak:\n  " + "\n  ".join(english)


def test_a_crashed_turn_tells_the_manazer_what_happened_and_what_to_press(db_session, monkeypatch, tmp_path):
    """The runner's last resort: a Slovak sentence with the error bar's button; the raw reason is the technical
    detail the conversation shows under it, never the sentence."""
    from backend.api.routes.pipeline import _board
    from backend.services import orchestrator, pipeline_runner
    from tests.test_orchestrator_v2_verifikacia import _make_version, _seed_verifikacia

    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", tmp_path)
    version, _project = _make_version(db_session, project_dial="plna")
    _seed_verifikacia(db_session, version.id)

    state, msg = pipeline_runner._mark_blocked(db_session, version.id, reason="KeyError: 'phase'")

    assert (state.status, state.block_reason) == ("blocked", "system_error")
    assert msg.content == (
        "Ťah agenta sa nečakane zrútil a stavba sa zastavila. Môžeš napísať, čo má agent urobiť inak, a stlač "
        "„Skús znova“. Dôvod je nižšie v technickom detaile."
    )
    assert msg.payload == {"technical_detail": "KeyError: 'phase'"}
    shown = _board(db_session, version.id).state.next_action
    assert shown == (
        "Ťah agenta sa nečakane zrútil a stavba stojí. Môžeš napísať, čo má agent urobiť inak, a stlač „Skús znova“."
    )
    assert "KeyError" not in msg.content + shown


def test_the_kind_of_a_refusal_goes_by_its_type_not_by_its_words(db_session):
    """404 / 409 / 400 by the error's class — the old English words in a plain refusal decide nothing any more."""
    from backend.api.routes.pipeline import _map_orch_error
    from backend.services import orchestrator

    def status(exc):
        return _map_orch_error(db_session, None, exc).status_code

    assert status(orchestrator.OrchestratorNotFound("Verzia neexistuje.")) == 404
    assert status(orchestrator.OrchestratorConflict("Stavba už bola spustená.")) == 409
    assert status(orchestrator.OrchestratorError("Version not found — Pipeline already started")) == 400


async def test_an_unknown_version_is_404_and_a_second_start_is_409_in_slovak(db_session, monkeypatch, tmp_path):
    """The engine's own refusals reach the route as the kinds they are — and read Slovak."""
    import uuid

    import pytest

    from backend.api.routes.pipeline import _map_orch_error
    from backend.services import orchestrator
    from tests.test_orchestrator_v2_verifikacia import _make_version, _seed_verifikacia

    unknown = uuid.uuid4()
    with pytest.raises(orchestrator.OrchestratorError) as missing:  # a version gone from under a turn
        orchestrator._project_slug_for_version(db_session, unknown)
    gone = _map_orch_error(db_session, unknown, missing.value)
    assert (gone.status_code, gone.detail) == (404, f"Verzia {unknown} v kokpite neexistuje.")

    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", tmp_path)
    version, _project = _make_version(db_session, project_dial="plna")
    _seed_verifikacia(db_session, version.id)
    with pytest.raises(orchestrator.OrchestratorError) as again:
        await orchestrator.apply_action(db_session, version_id=version.id, action="start", payload={})
    twice = _map_orch_error(db_session, version.id, again.value)
    assert (twice.status_code, twice.detail) == (409, "Stavba tejto verzie už bola spustená.")
