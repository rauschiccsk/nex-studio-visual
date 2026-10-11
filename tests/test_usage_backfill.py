"""Backfilling the spend of older turns from session logs (ICCINT-168, :mod:`usage_backfill`).

The logs have the shape Claude Code really writes: a run starts with the prompt (``user`` with text), a closed run ends
with the session total ``cost-state``, an interrupted one has nothing; helpers are in ``<session>/subagents``.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage
from backend.db.models.poradca import PoradcaConversation, PoradcaMessage
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import usage_backfill
from backend.services.usage_ledger import UsagePart

T0 = datetime(2026, 9, 20, 10, 0, 0, tzinfo=timezone.utc)
OPUS = "claude-opus-5"


def _iso(t: datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


def _prompt(at: datetime) -> dict:
    return {"type": "user", "timestamp": _iso(at), "promptId": "p", "message": {"role": "user", "content": "Úloha"}}


def _tool_result(at: datetime) -> dict:
    return {"type": "user", "timestamp": _iso(at), "message": {"content": [{"type": "tool_result", "content": "x"}]}}


def _msg(mid: str, at: datetime, out: int, model: str = OPUS) -> dict:
    usage = {
        "input_tokens": 1,
        "output_tokens": out,
        "cache_read_input_tokens": 1000,
        "cache_creation_input_tokens": 10,
    }
    return {"type": "assistant", "timestamp": _iso(at), "message": {"id": mid, "model": model, "usage": usage}}


def _state(out: int, cost: float, read: int) -> dict:
    usage = {
        "inputTokens": 3,
        "outputTokens": out,
        "cacheReadInputTokens": read,
        "cacheCreationInputTokens": 30,
        "costUSD": cost,
    }
    return {"type": "cost-state", "modelUsage": {OPUS: usage}}


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def _three_runs(path: Path) -> None:
    """Closed run → interrupted run → closed run (the third total excludes the interrupted run, as in Claude Code)."""
    _write(
        path,
        [
            _prompt(T0),
            _msg("a1", T0 + timedelta(seconds=5), 100),
            _tool_result(T0 + timedelta(seconds=6)),  # a tool result is not a new prompt
            _msg("a2", T0 + timedelta(seconds=9), 200),
            _state(out=300, cost=0.10, read=3000),
            _prompt(T0 + timedelta(minutes=5)),
            _msg("b1", T0 + timedelta(minutes=5, seconds=3), 40),
            _msg("b1", T0 + timedelta(minutes=5, seconds=8), 400),  # the last write of a message is final
            _prompt(T0 + timedelta(minutes=10)),
            _msg("c1", T0 + timedelta(minutes=10, seconds=4), 50),
            _state(out=350, cost=0.12, read=4000),
        ],
    )


def test_a_session_record_splits_into_runs_closed_with_a_price_or_cut_off_without(tmp_path):
    path = tmp_path / "s.jsonl"
    _three_runs(path)
    first, cut, last = usage_backfill.read_invocations(path)
    assert first.closed and first.parts == [UsagePart(OPUS, 3, 300, 3000, 30, 0.10)]
    assert first.end == T0 + timedelta(seconds=9)
    assert not cut.closed and cut.parts == [UsagePart(OPUS, 1, 400, 1000, 10, None)]
    # the total after the third run is subtracted from the one after the FIRST — the interrupted run between has none
    assert last.closed and last.parts == [UsagePart(OPUS, 0, 50, 1000, 0, pytest.approx(0.02))]


def test_a_cut_off_runs_helpers_count_with_it(tmp_path):
    path = tmp_path / "s.jsonl"
    _three_runs(path)
    helper = tmp_path / "s" / "subagents" / "agent-1.jsonl"
    _write(helper, [_msg("h1", T0 + timedelta(minutes=5, seconds=5), 70, model="claude-haiku-4-5")])
    _first, cut, _last = usage_backfill.read_invocations(path)
    assert sorted((p.model, p.output_tokens) for p in cut.parts) == [("claude-haiku-4-5", 70), (OPUS, 400)]


@pytest.fixture()
def world(db_session, tmp_path):
    user = User(
        username=f"u_{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:8]}@x.sk", password_hash="x", role="ri"
    )
    db_session.add(user)
    db_session.flush()
    slug = f"p-{uuid.uuid4().hex[:8]}"
    project = Project(name="P", slug=slug, type="standard", auth_mode="password", description="d", created_by=user.id)
    db_session.add(project)
    db_session.flush()
    version = Version(project_id=project.id, version_number="1.5.0")
    db_session.add(version)
    db_session.flush()
    home = tmp_path / "claude"
    _three_runs(home / "projects" / f"-opt-projects-{slug}" / f"{uuid.uuid4()}.jsonl")
    return {"db": db_session, "version": version, "home": home, "user": user, "project": project, "tmp": tmp_path}


def _turn(db, version, *, at: datetime, out: int, duration: float = 15.0) -> PipelineMessage:
    """A turn as the cockpit wrote it. ``at`` is the WRITE time = transaction start, i.e. BEFORE the turn ran (measured:
    a turn written 13:49:59 with a duration of 73 s, its run ended 13:51:11)."""
    msg = PipelineMessage(
        version_id=version.id,
        stage="programovanie",
        author="ai_agent",
        recipient="manazer",
        kind="gate_report",
        content="x",
        payload={
            "usage": {"input_tokens": 3, "output_tokens": out, "model": OPUS},
            "timing": {"duration_seconds": duration, "parse_attempts": 1},
        },
        created_at=at,
    )
    db.add(msg)
    db.flush()
    return msg


#: Starts of the turns of the three runs from :func:`_three_runs` (runs start at T0, T0+5 min, T0+10 min).
STARTS = (T0 - timedelta(seconds=1), T0 + timedelta(minutes=4, seconds=59), T0 + timedelta(minutes=9, seconds=59))


def _three_turns(db, version, outs=(300, 400, 50)) -> list[PipelineMessage]:
    return [_turn(db, version, at=at, out=out) for at, out in zip(STARTS, outs)]


def test_old_turns_get_their_runs_and_keep_what_they_recorded(world):
    """Each run belongs to the last turn that started before it; the turn gets parts and a mark, its input and output
    (from which human time is computed) stay. A turn to which no run belongs stays unfilled."""
    db, version = world["db"], world["version"]
    first, cut, _third = _three_turns(db, version)
    nothing = _turn(db, version, at=T0 + timedelta(hours=3), out=999)  # no run — the log was not preserved

    the_plan = usage_backfill.plan(db, claude_home=world["home"], poradca_sessions=world["tmp"] / "none")
    assert usage_backfill.apply(db, the_plan) == 3
    db.expire_all()
    assert db.get(PipelineMessage, first.id).payload["usage"]["parts"][0]["cost_usd"] == 0.10
    stored = db.get(PipelineMessage, cut.id).payload["usage"]
    assert stored["parts"][0]["cost_usd"] is None and stored["backfill"] == usage_backfill.BACKFILL_MARK
    assert (stored["input_tokens"], stored["output_tokens"]) == (3, 400)  # unchanged
    assert "parts" not in db.get(PipelineMessage, nothing.id).payload["usage"]
    assert the_plan.unmatched_runs[world["project"].slug] == 0
    assert any("1.5.0: ťahov 4, doplní sa 3" in line for line in usage_backfill.report(the_plan))


def test_a_run_long_after_the_last_turn_belongs_to_no_turn(world):
    """A run ending long after the duration of the turn that started before it is not its — its own turn was lost."""
    db, version = world["db"], world["version"]
    early = _turn(db, version, at=T0 - timedelta(hours=1), out=300)
    the_plan = usage_backfill.plan(db, claude_home=world["home"], poradca_sessions=world["tmp"] / "none")
    assert usage_backfill.apply(db, the_plan) == 0
    db.expire_all()
    assert "parts" not in db.get(PipelineMessage, early.id).payload["usage"]
    assert the_plan.unmatched_runs[world["project"].slug] == 3


def test_a_run_that_does_not_cover_the_turn_is_not_forced_onto_it(world):
    """The turn wrote 5 000 output tokens, the run at its time has 300 — that is not its run (or the log is incomplete):
    better „nevyčíslené" than someone else's spend."""
    db, version = world["db"], world["version"]
    first, *_rest = _three_turns(db, version, outs=(5_000, 400, 50))
    the_plan = usage_backfill.plan(db, claude_home=world["home"], poradca_sessions=world["tmp"] / "none")
    assert usage_backfill.apply(db, the_plan) == 2
    db.expire_all()
    assert "parts" not in db.get(PipelineMessage, first.id).payload["usage"]


def test_a_turn_already_recorded_with_parts_is_left_alone(world):
    db, version = world["db"], world["version"]
    msg = _turn(db, version, at=STARTS[0], out=300)
    msg.payload = {**msg.payload, "usage": {**msg.payload["usage"], "parts": []}}
    db.flush()
    the_plan = usage_backfill.plan(db, claude_home=world["home"], poradca_sessions=world["tmp"] / "none")
    assert the_plan.turns == []


def test_a_compaction_summary_does_not_split_a_run(tmp_path):
    """On compacting a long conversation Claude Code inserts the summary as a ``user`` message with text — not a prompt.
    If it were, the first half of the run would count from messages and the second from a total that contains it (review
    finding: Dedo Home +169 thousand extra output tokens)."""
    path = tmp_path / "s.jsonl"
    summary = {"type": "user", "isCompactSummary": True, "timestamp": _iso(T0 + timedelta(seconds=4))}
    summary["message"] = {"role": "user", "content": "Zhrnutie doterajšieho rozhovoru…"}
    _write(
        path,
        [
            _prompt(T0),
            _msg("a1", T0 + timedelta(seconds=2), 100),
            summary,
            _msg("a2", T0 + timedelta(seconds=6), 200),
            _state(out=300, cost=0.10, read=3000),
        ],
    )
    [run] = usage_backfill.read_invocations(path)
    assert run.closed and run.output_tokens == 300


def test_running_the_backfill_twice_counts_nothing_twice(world):
    """Second run: a run that already belongs to a filled-in turn stays with it — it does not fall to another unfilled
    turn that started earlier and whose own run is missing from the log (it would fit it exactly)."""
    db, version = world["db"], world["version"]
    earlier = _turn(db, version, at=T0 - timedelta(minutes=2), out=300, duration=200.0)
    first, *_rest = _three_turns(db, version)
    for _ in range(2):
        the_plan = usage_backfill.plan(db, claude_home=world["home"], poradca_sessions=world["tmp"] / "none")
        usage_backfill.apply(db, the_plan)
    db.expire_all()
    assert len(db.get(PipelineMessage, first.id).payload["usage"]["parts"]) == 1
    assert "parts" not in db.get(PipelineMessage, earlier.id).payload["usage"]


def test_a_failed_turns_runs_are_costed_but_not_turned_into_human_work(world):
    """A turn that failed before v4.43.0 has no spend recorded — its run is in the log. It is filled into the price;
    input and output (from which human time is computed) stay zero — a failed attempt is not work a human would do."""
    db, version = world["db"], world["version"]
    _first, failed, _third = _three_turns(db, version)
    failed.payload = {**failed.payload, "usage": None}
    db.flush()
    the_plan = usage_backfill.plan(db, claude_home=world["home"], poradca_sessions=world["tmp"] / "none")
    usage_backfill.apply(db, the_plan)
    db.expire_all()
    usage = db.get(PipelineMessage, failed.id).payload["usage"]
    assert (usage["input_tokens"], usage["output_tokens"]) == (0, 0)
    assert [p["output_tokens"] for p in usage["parts"]] == [400]


def test_a_short_run_stays_with_its_answer_even_when_the_next_question_follows_at_once(world):
    """Answers of one conversation are assigned together. A short run belongs to the answer that started BEFORE it — not
    to the next question asked a few seconds after its start."""
    db = world["db"]
    conversation = PoradcaConversation(
        project_id=world["project"].id, author_id=world["user"].id, title="t", claude_session_id=uuid.uuid4()
    )
    db.add(conversation)
    db.flush()

    def _answer(start: datetime, end: datetime, out: int) -> PoradcaMessage:
        answer = PoradcaMessage(
            conversation_id=conversation.id,
            author="poradca",
            status="done",
            usage={"input_tokens": 3, "output_tokens": out, "model": OPUS},
            created_at=start,
            finished_at=end,
        )
        db.add(answer)
        db.flush()
        return answer

    first = _answer(T0 - timedelta(seconds=2), T0 + timedelta(seconds=10), 300)
    second = _answer(T0 + timedelta(seconds=12), T0 + timedelta(minutes=5, seconds=9), 400)  # 12 s after the run start
    _answer(T0 + timedelta(minutes=9, seconds=58), T0 + timedelta(minutes=10, seconds=5), 50)
    sessions = world["tmp"] / "poradca"
    _three_runs(sessions / str(conversation.id) / f"{conversation.claude_session_id}.jsonl")
    the_plan = usage_backfill.plan(db, claude_home=world["tmp"] / "none", poradca_sessions=sessions)
    usage_backfill.apply(db, the_plan)
    db.expire_all()
    assert [p["output_tokens"] for p in db.get(PoradcaMessage, first.id).usage["parts"]] == [300]
    assert [p["output_tokens"] for p in db.get(PoradcaMessage, second.id).usage["parts"]] == [400]


def test_an_old_poradca_answer_is_filled_from_its_conversation_record(world):
    """An old Poradca answer is filled in from the session log of ITS conversation — with the price Claude Code paid."""
    db = world["db"]
    conversation = PoradcaConversation(
        project_id=world["project"].id, author_id=world["user"].id, title="t", claude_session_id=uuid.uuid4()
    )
    db.add(conversation)
    db.flush()
    answers = []
    for start, out in zip(STARTS, (300, 400, 50)):
        answer = PoradcaMessage(
            conversation_id=conversation.id,
            author="poradca",
            status="done",
            usage={"input_tokens": 3, "output_tokens": out, "model": OPUS},
            created_at=start,
            finished_at=start + timedelta(seconds=20),
        )
        db.add(answer)
        answers.append(answer)
    db.flush()
    sessions = world["tmp"] / "poradca"
    _three_runs(sessions / str(conversation.id) / f"{conversation.claude_session_id}.jsonl")

    the_plan = usage_backfill.plan(db, claude_home=world["tmp"] / "none", poradca_sessions=sessions)
    assert usage_backfill.apply(db, the_plan) == 3
    db.expire_all()
    assert db.get(PoradcaMessage, answers[0].id).usage["parts"][0]["cost_usd"] == 0.10
