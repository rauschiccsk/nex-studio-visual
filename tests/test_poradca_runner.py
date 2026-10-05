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

from backend.db.models.foundation import User
from backend.db.models.poradca import PoradcaMessage
from backend.db.models.projects import Project
from backend.services import build_db, build_sandbox
from backend.services.poradca import runner, sandbox, tools
from backend.services.poradca.mcp_server import Tool

FAKE_SECRET = "fake-oauth-token-for-tests-0001"

FAKE_CLAUDE = textwrap.dedent(
    """
    import json, socket, sys, time
    sock_path, mode = sys.argv[1], sys.argv[2]
    def out(o): print(json.dumps(o), flush=True)
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
        return [sys.executable, str(script), sock, mode["value"]]

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
    assert stored.usage == {"input_tokens": 120, "output_tokens": 30, "model": "claude-opus-test"}
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
    assert db.get(PoradcaMessage, answer.id).status == "stopped"
    assert await runner.stop(answer.id) is False


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
