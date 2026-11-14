"""DEV-384 — what the cockpit tells the Manažér in the conversation and in the state's sentence is Slovak.

14.11.2026, found while DEV-376 renamed the buttons of every sentence: when an agent's turn crashed in a way the engine
does not recognise, the runner's last-resort settle (``pipeline_runner._mark_blocked``) wrote the Manažér „Agent
dispatch failed: <the exception> — pipeline blocked.“ — English, with the raw exception as its sentence, and no word on
what to press. The state's sentence beside it was Slovak. Nothing checked the language of the texts written for the
Manažér. Director, on the ticket and on doing it: „Áno“.

A rule, not a list: every message the backend writes to the Manažér and every state sentence it sets is read for
English — the cockpit's own sentence rule (``block_language.english_sentences``, Poradca's check) and, for a short text
it cannot judge, a Slovak sentence carries a diacritic or a common Slovak word.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from backend.services.poradca.block_language import _SLOVAK, _words, english_sentences

BACKEND = Path(__file__).resolve().parents[1] / "backend"
_DIACRITICS = re.compile(r"[áäčďéíĺľňóôŕšťúýž]", re.I)
#: Common Slovak words without a diacritic that Poradca's list does not need („Pripravujem rozhodnutia znova…“).
_ALSO_SLOVAK = frozenset({"znova"})


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
    """(kind, place, text) of every message written to the Manažér and every state sentence the backend sets."""
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
    messages = [place for kind, place, _ in found if kind == "message"]
    sentences = [place for kind, place, _ in found if kind == "sentence"]
    assert len(messages) >= 20 and len(sentences) >= 40, "the scan sees almost nothing — it would pass a house on fire"
    assert any("pipeline_runner.py" in place for place in messages), "the runner's last-resort message is not seen"


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
