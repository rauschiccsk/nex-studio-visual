"""Beh otázky Poradcu od uloženia po odpoveď (ICCINT-167, návrh §4.1).

Namiesto ``docker run … claude`` beží náhradný skript, ktorý sa správa ako Claude Code: zavolá nástroj
Poradcu cez socket (ako prostredník v kontajneri), vypíše priebeh ``stream-json`` a na konci výsledok so
spotrebou. Všetko ostatné — uloženie, priebeh, filter, súbežnosť, zastavenie — je skutočné.
"""

from __future__ import annotations

import asyncio
import json
import sys
import textwrap
import uuid
from pathlib import Path

import pytest
from sqlalchemy import event

from backend.db.models.foundation import User
from backend.db.models.poradca import PoradcaConversation, PoradcaMessage
from backend.db.models.projects import Project
from backend.services import build_db, build_sandbox
from backend.services.poradca import runner, sandbox, tools
from backend.services.poradca.mcp_server import Tool

FAKE_SECRET = "fake-oauth-token-for-tests-0001"

FAKE_CLAUDE = textwrap.dedent(
    """
    import datetime, json, os, socket, sys, time
    sock_path, mode, transcript = sys.argv[1], sys.argv[2], sys.argv[3]
    def out(o): print(json.dumps(o), flush=True)
    # Ako Claude Code: dokončená správa ide do záznamu sedenia s konečnou spotrebou, súčet sedenia
    # (cost-state) až na konci DOKONČENÉHO behu — zastavený beh ho nezapíše.
    USAGE = {"input_tokens": 120, "output_tokens": 30,
             "cache_read_input_tokens": 5000, "cache_creation_input_tokens": 200}
    def note(o):
        with open(transcript, "a") as t:
            t.write(json.dumps(o) + "\\n")
    note({"type": "assistant", "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
          "message": {"id": "msg-%f" % time.time(), "model": "claude-opus-test", "usage": USAGE}})
    s = socket.socket(socket.AF_UNIX); s.connect(sock_path); f = s.makefile("rw")
    f.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": "stavba", "arguments": {"verzia": "1.0.0"}}}) + "\\n"); f.flush()
    reply = json.loads(f.readline())
    out({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "a", "name": "Read", "input": {"file_path": "/opt/projects/demo/app.py"}},
        {"type": "tool_use", "id": "b", "name": "mcp__poradca__stavba", "input": {}}]}})
    if mode == "slow":
        time.sleep(60)
    text = "Agent stojí na teste. " + reply["result"]["content"][0]["text"]
    total = {"inputTokens": 0, "outputTokens": 0, "cacheReadInputTokens": 0,
             "cacheCreationInputTokens": 0, "costUSD": 0.0}
    if os.path.exists(transcript):
        for line in open(transcript):
            row = json.loads(line)
            if row.get("type") == "cost-state":
                total = row["modelUsage"]["claude-opus-test"]
    total = {"inputTokens": total["inputTokens"] + 120, "outputTokens": total["outputTokens"] + 30,
             "cacheReadInputTokens": total["cacheReadInputTokens"] + 5000,
             "cacheCreationInputTokens": total["cacheCreationInputTokens"] + 200,
             "costUSD": round(total["costUSD"] + 0.0123, 9)}
    note({"type": "cost-state", "totalCostUSD": total["costUSD"], "modelUsage": {"claude-opus-test": total}})
    out({"type": "result", "result": text, "is_error": False,
         "usage": {"input_tokens": 120, "output_tokens": 30},
         "modelUsage": {"claude-opus-test": {"inputTokens": 120, "outputTokens": 30}}})
    """
)


class _Ctx:
    """``SessionLocal()`` pre skúšku: vráti tú istú izolovanú reláciu a nezatvorí ju."""

    def __init__(self, session):
        self._s = session

    def __enter__(self):
        return self._s

    def __exit__(self, *exc):
        return False


@pytest.fixture()
def world(db_session, tmp_path, monkeypatch):
    user = User(
        username=f"u_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
        role="ri",
        first_name="Tibor",
    )
    db_session.add(user)
    db_session.flush()
    project = Project(
        name=f"Demo {uuid.uuid4().hex[:8]}",
        slug=f"demo-{uuid.uuid4().hex[:6]}",
        type="standard",
        auth_mode="password",
        description="d",
        created_by=user.id,
    )
    db_session.add(project)
    db_session.flush()

    monkeypatch.setattr(runner, "SessionLocal", lambda: _Ctx(db_session))
    monkeypatch.setattr(tools, "SessionLocal", lambda: _Ctx(db_session))
    monkeypatch.setattr(runner, "known_secret_values", lambda db, p: [FAKE_SECRET])
    monkeypatch.setattr(runner, "max_concurrent", lambda db: 3)

    data = tmp_path / "poradca"
    data.mkdir()
    monkeypatch.setattr(sandbox.settings, "poradca_data_dir", str(data))
    monkeypatch.setattr(sandbox, "project_dirs", lambda slug: ("/opt/projects/demo", str(tmp_path)))
    monkeypatch.setattr(sandbox, "prepare", lambda cid, tok: (data / "s", _mk(data / "run" / tok)))
    monkeypatch.setattr(sandbox, "overlay_paths", lambda d: [])

    async def _noop(*a, **k):
        return "10.77.0.0/24"

    monkeypatch.setattr(build_db, "create_fenced_network", _noop)
    monkeypatch.setattr(build_db, "remove_network", _noop)
    monkeypatch.setattr(build_sandbox, "reap_container", _noop)
    monkeypatch.setattr(runner, "_charter_text", lambda: "charta")

    script = tmp_path / "fake_claude.py"
    script.write_text(FAKE_CLAUDE)
    mode = {"value": "fast"}

    def _argv(**kw):
        sock = str(sandbox.run_dir(kw["token"]) / sandbox.SOCKET_NAME)
        sessions = _mk(sandbox.session_dir(kw["conversation_id"]))
        transcript = sessions / f"{kw['call'].claude_session_id}.jsonl"
        return [sys.executable, str(script), sock, mode["value"], str(transcript)]

    monkeypatch.setattr(sandbox, "run_argv", _argv)

    async def _stavba(args):
        return f"fáza programovanie; token {FAKE_SECRET}"

    monkeypatch.setattr(
        tools,
        "build_tools",
        lambda **kw: [Tool("stavba", "Stav", {"type": "object"}, _stavba, lambda a: "verzia 1.0.0")],
    )
    conversation = runner.new_conversation(db_session, project=project, author=user, version_id=None, title="t")
    db_session.commit()
    return {"user": user, "conversation": conversation, "db": db_session, "mode": mode}


def _mk(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


async def _finish(answer_id: uuid.UUID) -> None:
    entry = runner._running.get(answer_id)
    if entry is not None and entry.task is not None:
        await asyncio.wait_for(entry.task, timeout=30)


async def test_question_runs_to_a_filtered_answer_with_steps_and_usage(world):
    db = world["db"]
    queue = runner.hub.subscribe(world["conversation"].id)
    human, answer = runner.ask(db, world["conversation"], "Prečo agent stojí?", world["user"])
    await _finish(answer.id)
    db.expire_all()
    stored = db.get(PoradcaMessage, answer.id)
    assert stored.status == "done", stored.error
    # Výstup nástroja aj odpoveď prešli filtrom: známe tajomstvo sa nikde neuloží.
    assert FAKE_SECRET not in stored.content and "‹skryté›" in stored.content
    assert "Agent stojí na teste" in stored.content
    # Kroky: vstavaný nástroj z priebehu, nástroj Poradcu zo servera (raz, nie dvakrát), bez obsahu.
    assert stored.steps == [{"tool": "stavba", "target": "verzia 1.0.0"}, {"tool": "Read", "target": "app.py"}]
    # ICCINT-168: spotreba zo záznamu sedenia — rozdiel súčtov, aj s vyrovnávacou pamäťou a cenou Claude Code.
    assert stored.usage == {
        "input_tokens": 120,
        "output_tokens": 30,
        "model": "claude-opus-test",
        "parts": [
            {
                "model": "claude-opus-test",
                "input_tokens": 120,
                "output_tokens": 30,
                "cache_read_tokens": 5000,
                "cache_write_tokens": 200,
                "cost_usd": 0.0123,
                "web_search_requests": 0,
            }
        ],
    }
    assert stored.duration_seconds is not None and stored.finished_at is not None
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    assert [e["type"] for e in events] == ["step", "step", "finished"]
    assert all(FAKE_SECRET not in json.dumps(e) for e in events)
    runner.hub.unsubscribe(world["conversation"].id, queue)


async def test_second_question_waits_for_the_first(world):
    db = world["db"]
    _human, answer = runner.ask(db, world["conversation"], "Prvá", world["user"])
    with pytest.raises(runner.PoradcaBusy):
        runner.ask(db, world["conversation"], "Druhá", world["user"])
    await _finish(answer.id)


async def test_stop_kills_the_run_and_records_stopped(world):
    db = world["db"]
    world["mode"]["value"] = "slow"
    _human, answer = runner.ask(db, world["conversation"], "Pomaly", world["user"])
    for _ in range(100):
        entry = runner._running.get(answer.id)
        if entry is not None and entry.process is not None and entry.steps:
            break
        await asyncio.sleep(0.05)
    assert await runner.stop(answer.id) is True
    await _finish(answer.id)
    db.expire_all()
    stopped = db.get(PoradcaMessage, answer.id)
    assert stopped.status == "stopped"
    # ICCINT-168: zastavená odpoveď nie je zadarmo — jej dokončené správy zo záznamu sedenia, bez ceny Claude Code
    # (ten ju do súčtu nezapíše); ocení ju cenník.
    assert stopped.usage["parts"] == [
        {
            "model": "claude-opus-test",
            "input_tokens": 120,
            "output_tokens": 30,
            "cache_read_tokens": 5000,
            "cache_write_tokens": 200,
            "cost_usd": None,
            "web_search_requests": 0,
        }
    ]
    assert await runner.stop(answer.id) is False


async def test_a_second_answer_costs_only_itself_not_the_whole_conversation(world):
    """ICCINT-168: Claude Code hlási pri pokračovaní sedenia súčet ZA CELÝ ROZHOVOR. Druhá odpoveď nesmie
    zaplatiť aj prvú — jej spotreba je rozdiel súčtov pred ňou a po nej."""
    db = world["db"]
    for question in ("Prvá", "Druhá"):
        _human, answer = runner.ask(db, world["conversation"], question, world["user"])
        await _finish(answer.id)
    db.expire_all()
    second = db.get(PoradcaMessage, answer.id)
    assert second.status == "done", second.error
    assert [(p["output_tokens"], p["cost_usd"]) for p in second.usage["parts"]] == [(30, 0.0123)]


async def test_timeout_records_a_sentence_not_a_hang(world, monkeypatch):
    db = world["db"]
    world["mode"]["value"] = "slow"
    monkeypatch.setattr(runner.settings, "poradca_question_timeout", 1)
    _human, answer = runner.ask(db, world["conversation"], "Pomaly", world["user"])
    await _finish(answer.id)
    db.expire_all()
    stored = db.get(PoradcaMessage, answer.id)
    assert stored.status == "failed" and "strop" in stored.error


async def test_unavailable_sandbox_fails_loudly_and_never_runs_without_it(world, monkeypatch):
    db = world["db"]

    def _boom(cid, tok):
        raise sandbox.PoradcaUnavailable("chýba priečinok Poradcu")

    monkeypatch.setattr(sandbox, "prepare", _boom)
    _human, answer = runner.ask(db, world["conversation"], "Ahoj", world["user"])
    await _finish(answer.id)
    db.expire_all()
    stored = db.get(PoradcaMessage, answer.id)
    assert stored.status == "failed" and "chýba priečinok Poradcu" in stored.error


async def test_concurrency_cap_queues_and_says_why(world, monkeypatch):
    db = world["db"]
    monkeypatch.setattr(runner, "max_concurrent", lambda db: 1)
    await runner._slots.acquire(1)  # niekto iný práve beží
    try:
        _human, answer = runner.ask(db, world["conversation"], "Čakám?", world["user"])
        for _ in range(100):
            entry = runner._running.get(answer.id)
            if entry is not None and entry.steps:
                break
            await asyncio.sleep(0.02)
        assert entry.steps[0]["tool"] == "rad" and "najviac 1" in entry.steps[0]["target"]
    finally:
        await runner._slots.release()
    await _finish(answer.id)
    db.expire_all()
    assert db.get(PoradcaMessage, answer.id).status == "done"


def test_fail_orphans_closes_answers_a_restart_interrupted(world):
    db = world["db"]
    msg = PoradcaMessage(conversation_id=world["conversation"].id, author="poradca", content="", status="running")
    db.add(msg)
    db.commit()
    assert runner.fail_orphans(db) >= 1
    db.refresh(msg)
    assert msg.status == "failed" and "reštart" in msg.error


async def test_every_step_passes_the_secret_filter_before_db_and_websocket(world):
    """Nález previerky 05.10.2026: kroky vstavaných nástrojov (Grep vzor, Read cesta) išli do databázy
    a prehliadača nefiltrované. Teraz filtruje ``_record_step`` pre všetky kroky na jednom mieste."""
    from backend.services.poradca.secrets_filter import SecretFilter

    db = world["db"]
    msg = PoradcaMessage(conversation_id=world["conversation"].id, author="poradca", content="", status="running")
    db.add(msg)
    db.commit()
    entry = runner._Running(conversation_id=world["conversation"].id, secret_filter=SecretFilter([FAKE_SECRET]))
    queue = runner.hub.subscribe(world["conversation"].id)
    await runner._record_step(entry, msg.id, "Grep", f"„{FAKE_SECRET}“ v celý projekt")
    db.refresh(msg)
    assert FAKE_SECRET not in json.dumps(msg.steps) and "‹skryté›" in msg.steps[0]["target"]
    assert FAKE_SECRET not in json.dumps(queue.get_nowait())
    runner.hub.unsubscribe(world["conversation"].id, queue)


async def test_a_question_into_a_deleted_conversation_never_starts(world):
    # Otázka aj vymazanie idú cez zámok riadku rozhovoru: do vymazaného sa nič neuloží ani nespustí.
    db = world["db"]
    await runner.delete_conversation(db, world["conversation"])
    with pytest.raises(runner.ConversationGone):
        runner.ask(db, world["conversation"], "Ešte jedna", world["user"])
    assert db.query(PoradcaMessage).filter(PoradcaMessage.conversation_id == world["conversation"].id).count() == 0
    assert not any(e.conversation_id == world["conversation"].id for e in runner._running.values())


async def test_delete_refuses_while_the_real_run_is_going_then_succeeds(world):
    db = world["db"]
    world["mode"]["value"] = "slow"
    _human, answer = runner.ask(db, world["conversation"], "Pomaly", world["user"])
    with pytest.raises(runner.PoradcaBusy):
        await runner.delete_conversation(db, world["conversation"])
    for _ in range(100):
        entry = runner._running.get(answer.id)
        if entry is not None and entry.process is not None and entry.steps:
            break
        await asyncio.sleep(0.05)
    assert await runner.stop(answer.id) is True
    await _finish(answer.id)
    db.expire_all()
    await runner.delete_conversation(db, world["conversation"])
    assert db.get(PoradcaMessage, answer.id).content == ""


def test_rename_after_a_delete_writes_nothing_back(world):
    # Úprava, ktorá prešla kontrolou prístupu pred vymazaním a zápis robí až po ňom (čakala na zámok).
    db = world["db"]
    conversation = world["conversation"]
    asyncio.run(runner.delete_conversation(db, conversation))
    with pytest.raises(runner.ConversationGone):
        runner.edit_conversation(db, conversation, title="Heslo do UAT je …")
    db.expire_all()
    assert db.get(PoradcaConversation, conversation.id).title == runner.DELETED_TITLE


async def test_ask_and_delete_take_the_row_lock(world):
    """Zámok riadku je to, čo otázku a vymazanie radí za seba; v jednej relácii skúšky ho nevidno inak než
    v poslanom príkaze."""
    db = world["db"]
    statements: list[str] = []

    def _seen(conn, cursor, statement, *a):
        statements.append(statement)

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", _seen)
    try:
        _human, answer = runner.ask(db, world["conversation"], "Prvá", world["user"])
        await _finish(answer.id)
        asked = [st for st in statements if "FOR UPDATE" in st and "poradca_conversations" in st]
        statements.clear()
        db.expire_all()
        await runner.delete_conversation(db, world["conversation"])
        deleted = [st for st in statements if "FOR UPDATE" in st and "poradca_conversations" in st]
    finally:
        event.remove(engine, "before_cursor_execute", _seen)
    assert asked and deleted


async def test_a_failed_database_write_puts_the_transcript_back(world, monkeypatch):
    db = world["db"]
    conversation = world["conversation"]
    record = sandbox.session_dir(conversation.id)
    record.mkdir(parents=True)
    (record / "t.jsonl").write_text("{}")

    def _boom():
        raise RuntimeError("databáza spadla")

    with monkeypatch.context() as m:  # nie ``undo()`` — ten by vrátil aj presmerovanie z prípravku ``world``
        m.setattr(db, "commit", _boom)
        with pytest.raises(RuntimeError):
            await runner.delete_conversation(db, conversation)
    assert (record / "t.jsonl").read_text() == "{}"
    assert not sandbox.trash_dir().exists() or not any(sandbox.trash_dir().iterdir())
    db.expire_all()
    assert db.get(PoradcaConversation, conversation.id).deleted_at is None


async def test_delete_tells_open_tabs(world):
    queue = runner.hub.subscribe(world["conversation"].id)
    try:
        await runner.delete_conversation(world["db"], world["conversation"])
        assert queue.get_nowait() == {"type": "deleted"}
    finally:
        runner.hub.unsubscribe(world["conversation"].id, queue)


# ── DEV-30: a conversation started under an older charter gets the current one ────────────────────────────────
#
# Claude Code takes the charter (--append-system-prompt) only on a conversation's first question; on --resume it
# ignores a new one (measured 08.10.2026: a rule appended on resume — "start every answer with GAMA" — was not
# followed). The Director asked in a conversation from the morning, so the new rule never reached it.


def _capture_calls(monkeypatch) -> list:
    calls: list = []
    patched = sandbox.run_argv

    def _capture(**kw):
        calls.append(kw["call"])
        return patched(**kw)

    monkeypatch.setattr(sandbox, "run_argv", _capture)
    return calls


async def _ask(world, question: str) -> None:
    _, answer = runner.ask(world["db"], world["conversation"], question, world["user"])
    await _finish(answer.id)
    world["db"].expire_all()
    assert world["db"].get(PoradcaMessage, answer.id).status == "done"


async def test_a_changed_charter_reaches_a_running_conversation_once(world, monkeypatch):
    calls = _capture_calls(monkeypatch)
    await _ask(world, "Prvá otázka")
    assert calls[0].charter_text == "charta"  # the first question starts the session with the charter
    await _ask(world, "Druhá otázka")
    assert calls[1].charter_text is None and "charta" not in calls[1].prompt  # unchanged → nothing repeated

    monkeypatch.setattr(runner, "_charter_text", lambda: "charta v2 — pokyn len keď niečo chýba")
    await _ask(world, "Tretia otázka")
    assert calls[2].charter_text is None  # on --resume an appended charter would be ignored
    assert "charta v2 — pokyn len keď niečo chýba" in calls[2].prompt
    assert calls[2].prompt.index("charta v2") < calls[2].prompt.index("Tretia otázka")
    await _ask(world, "Štvrtá otázka")
    assert "charta v2" not in calls[3].prompt  # once, not with every question


async def test_a_conversation_from_before_the_change_gets_the_charter_at_its_next_question(world, monkeypatch):
    calls = _capture_calls(monkeypatch)
    await _ask(world, "Prvá otázka")
    world["conversation"].charter_sha = None  # as every conversation started before DEV-30
    world["db"].commit()
    await _ask(world, "Druhá otázka")
    assert "charta" in calls[1].prompt
