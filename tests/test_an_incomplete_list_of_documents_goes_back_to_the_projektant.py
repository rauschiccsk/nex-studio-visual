"""DEV-511 — a brief whose list of documents from the internet is not complete goes back to the Projektant once, in
the same session, before anyone sees it (the round DEV-356 made for a block that is not Slovak).

25.11.2026 the brief of NEX DualBook listed two documents without an exact address and left them to the Manažér.
"""

from __future__ import annotations

from backend.db.models.poradca import PoradcaMessage
from backend.services.poradca import references, runner
from backend.tests.test_the_cockpit_fetches_what_a_project_needs_from_the_internet import GOOD, _brief
from tests.test_poradca_runner import _capture_calls, _finish, world  # noqa: F401


async def test_a_brief_with_an_incomplete_list_goes_back_once_and_the_complete_one_is_kept(
    world,  # noqa: F811
    monkeypatch,
):
    calls = _capture_calls(monkeypatch)
    incomplete = _brief("docs/reference/kv.pdf | poučenie MF SR | Poučenie\n")
    complete = _brief(GOOD)
    left = [incomplete, complete]

    async def _stream(argv, entry, message_id, project_dir):
        return left.pop(0), None

    monkeypatch.setattr(runner, "_stream", _stream)
    db = world["db"]

    _, answer = runner.ask(db, world["conversation"], "Doplň podklady z internetu.", world["user"])
    await _finish(answer.id)
    db.expire_all()
    stored = db.get(PoradcaMessage, answer.id)

    assert stored.status == "done", stored.error
    assert stored.content == complete and left == []
    first, rewrite = calls
    assert rewrite.claude_session_id == first.claude_session_id and rewrite.charter_text is None
    assert rewrite.prompt == references.rewrite_directive(references.problems_in_answer(incomplete))
    assert {"tool": runner.REFERENCES_STEP, "target": "zoznam podkladov z internetu"} in stored.steps


async def test_a_brief_with_a_complete_list_is_asked_once(world, monkeypatch):  # noqa: F811
    calls = _capture_calls(monkeypatch)

    async def _stream(argv, entry, message_id, project_dir):
        return _brief(GOOD), None

    monkeypatch.setattr(runner, "_stream", _stream)
    db = world["db"]

    _, answer = runner.ask(db, world["conversation"], "Doplň podklady z internetu.", world["user"])
    await _finish(answer.id)

    assert len(calls) == 1
