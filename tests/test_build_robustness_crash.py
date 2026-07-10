"""build-robustness-crash-handling.md — persist agent logs + auto-retry a CRASH (not a timeout) + honest
timeout-vs-crash message.

Three fixes on the build-round envelope-loss path (built on top of the Bug 1/2 cockpit-timeout fix):

* **Fix 1** — every agent turn's subprocess output is persisted to a durable per-turn log
  (``TURN_LOG_DIR/<version>/<stage>-<session>.log``) on completion AND on crash/timeout, with
  credential/OAuth patterns REDACTED before the write (§4). The raising ``ClaudeAgentError`` carries the
  written path so the honest message can reference it.
* **Fix 2** — a build turn that CRASHES (``ClaudeAgentError`` — connection/decode, NOT the wall-clock
  budget) is auto-retried ONCE per dispatch; a REAL timeout (``ClaudeAgentTimeout``) is never retried.
* **Fix 3** — the settled notification states the ACTUAL reason: a timeout message vs a distinct crash
  message (which also cites the auto-retry + the log path).

Fix 1 exercises the real ``_invoke_once`` with a faked subprocess; Fix 2/3 drive the real
``_run_build_round`` with ``invoke_agent_with_parse_retry`` stubbed to return the two distinct
envelope-loss shapes. Runs against the real v2 DB (SAVEPOINT-isolated ``db_session``).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from uuid import uuid4

import pytest
from sqlalchemy import select

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineState
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version
from backend.services import claude_agent, orchestrator
from backend.services.claude_agent import ClaudeAgentError, ClaudeAgentTimeout
from backend.services.pipeline_status import ParseFailure, PipelineStatusBlock

# (pytest ``asyncio_mode = auto`` — async tests run without an explicit mark.)


# ── Fix 1: redaction (pure function) ────────────────────────────────────────────

_FAKE_TOKEN = "sk-ant-oat01-FAKEsecret1234567890abcdEFGH"
_STDERR_WITH_SECRETS = (
    "Traceback (most recent call last):\n"
    f"Authorization: Bearer {_FAKE_TOKEN}\n"
    "OAuth refresh_token=REFRESHfakeSECRETvalue987\n"
    "api_key=SKKEYfake000abc\n"
    f"raw sk token {_FAKE_TOKEN}\n"
    "some ordinary log line about a task\n"
)
#: The exact secret substrings that must NEVER survive redaction.
_SECRET_SUBSTRINGS = ("FAKEsecret", "REFRESHfakeSECRET", "SKKEYfake", _FAKE_TOKEN)


def test_redact_secrets_scrubs_every_token_shape():
    out = claude_agent._redact_secrets(_STDERR_WITH_SECRETS)
    for secret in _SECRET_SUBSTRINGS:
        assert secret not in out, f"{secret!r} leaked through redaction"
    assert "[REDACTED]" in out
    # non-secret prose survives — redaction must not nuke the whole log
    assert "some ordinary log line about a task" in out


def test_redact_secrets_is_idempotent_and_noop_on_clean_text():
    clean = "just a normal traceback\nline two\n"
    assert claude_agent._redact_secrets(clean) == clean
    once = claude_agent._redact_secrets(_STDERR_WITH_SECRETS)
    assert claude_agent._redact_secrets(once) == once


# ── Fix 1: per-turn log written on completion / crash / timeout (real _invoke_once) ──


class _FakeProc:
    """Non-streaming ``claude`` subprocess stand-in: ``communicate()`` returns the canned stdout/stderr
    (or hangs, to force a wall-clock timeout)."""

    def __init__(self, *, stdout=b"", stderr=b"", returncode=0, hang=False):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self._hang = hang
        self.pid = 987654

    async def communicate(self):
        if self._hang:
            await asyncio.sleep(30)
        return self._stdout, self._stderr

    def kill(self):
        pass

    async def wait(self):
        return self.returncode


def _patch_exec(monkeypatch, proc):
    async def _fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)


async def test_turn_log_written_on_completion_redacted(monkeypatch, tmp_path):
    envelope = json.dumps({"result": "done ok", "usage": {"input_tokens": 1, "output_tokens": 2}}).encode()
    _patch_exec(monkeypatch, _FakeProc(stdout=envelope, stderr=_STDERR_WITH_SECRETS.encode(), returncode=0))

    text, _usage, _structured = await claude_agent._invoke_once(
        project_slug="p",
        claude_session_id=uuid4(),
        prompt="go",
        timeout=5,
        log_dir=tmp_path,
        log_label="programovanie-sess",
    )
    assert text == "done ok"
    log_file = tmp_path / "programovanie-sess.log"
    assert log_file.is_file()  # persisted on NORMAL completion (Fix 1)
    body = log_file.read_text()
    assert "ok" in body  # the outcome marker
    for secret in _SECRET_SUBSTRINGS:  # §4 — no credential survives
        assert secret not in body


async def test_turn_log_written_on_crash_with_path_on_exception(monkeypatch, tmp_path):
    _patch_exec(monkeypatch, _FakeProc(stdout=b"", stderr=_STDERR_WITH_SECRETS.encode(), returncode=1))

    with pytest.raises(ClaudeAgentError) as ei:
        await claude_agent._invoke_once(
            project_slug="p",
            claude_session_id=uuid4(),
            prompt="go",
            timeout=5,
            log_dir=tmp_path,
            log_label="programovanie-sess",
        )
    log_file = tmp_path / "programovanie-sess.log"
    assert log_file.is_file()  # persisted on CRASH (Fix 1)
    assert ei.value.log_path == str(log_file)  # the exception carries the path (Fix 3 references it)
    body = log_file.read_text()
    assert "crash" in body
    for secret in _SECRET_SUBSTRINGS:
        assert secret not in body


async def test_turn_log_written_on_timeout_with_path(monkeypatch, tmp_path):
    _patch_exec(monkeypatch, _FakeProc(hang=True))

    async def _no_kill(_proc):  # avoid os.killpg on a fake pid
        return None

    monkeypatch.setattr(claude_agent, "_kill_process_tree", _no_kill)

    with pytest.raises(ClaudeAgentTimeout) as ei:
        await claude_agent._invoke_once(
            project_slug="p",
            claude_session_id=uuid4(),
            prompt="go",
            timeout=0.05,
            log_dir=tmp_path,
            log_label="programovanie-sess",
        )
    log_file = tmp_path / "programovanie-sess.log"
    assert log_file.is_file()  # persisted on TIMEOUT (Fix 1)
    assert ei.value.log_path == str(log_file)
    assert "timeout" in log_file.read_text()


async def test_turn_log_is_noop_without_log_dir(monkeypatch, tmp_path):
    # No log_dir → today's byte-identical behaviour (no file, no crash). Guards the default path.
    envelope = json.dumps({"result": "x"}).encode()
    _patch_exec(monkeypatch, _FakeProc(stdout=envelope, returncode=0))
    text, _u, _s = await claude_agent._invoke_once(project_slug="p", claude_session_id=uuid4(), prompt="go")
    assert text == "x"
    assert not list(tmp_path.iterdir())  # nothing written anywhere under tmp


# ── Fix 2/3: build-round auto-retry + honest message (real _run_build_round) ─────


def _make_version(db_session):
    user = User(
        username=f"u_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
        role="ri",
    )
    db_session.add(user)
    db_session.flush()
    project = Project(
        name=f"P {uuid.uuid4().hex[:8]}",
        slug=f"p-{uuid.uuid4().hex[:8]}",
        type="standard",
        auth_mode="password",
        description="d",
        created_by=user.id,
        miera_autonomie="po_kazdej_faze",
    )
    db_session.add(project)
    db_session.flush()
    version = Version(project_id=project.id, version_number=f"1.{uuid.uuid4().hex[:4]}.0")
    db_session.add(version)
    db_session.flush()
    return version, project


def _seed_programovanie(db_session, version_id):
    state = PipelineState(
        version_id=version_id,
        flow_type="new_version",
        current_stage="programovanie",
        current_actor="ai_agent",
        status="agent_working",
        next_action="working",
    )
    db_session.add(state)
    db_session.flush()
    return state


def _seed_one_task(db_session, version, project):
    epic = Epic(project_id=project.id, version_id=version.id, number=1, title="Foundation", status="planned")
    db_session.add(epic)
    db_session.flush()
    feat = Feat(epic_id=epic.id, number=1, title="Schema", status="todo")
    db_session.add(feat)
    db_session.flush()
    task = Task(feat_id=feat.id, number=1, title="T1", task_type="backend", status="todo")
    db_session.add(task)
    db_session.flush()
    return task


def _tasks(db_session, version_id):
    return (
        db_session.execute(
            select(Task)
            .join(Feat, Feat.id == Task.feat_id)
            .join(Epic, Epic.id == Feat.epic_id)
            .where(Epic.version_id == version_id)
        )
        .scalars()
        .all()
    )


def _stub_turns(monkeypatch, blocks):
    """Drive ``invoke_agent_with_parse_retry`` from a scripted list (one per dispatched turn); count calls."""
    calls = []
    seq = list(blocks)

    async def _fake(db, *, version_id, role, stage, prompt, **_kw):
        calls.append({"role": role, "stage": stage, "prompt": prompt})
        return seq[len(calls) - 1] if len(calls) <= len(seq) else seq[-1]

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _fake)
    return calls


def _stub_mech(monkeypatch, reason=None):
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block, baseline_sha=None: reason)


def _no_baseline_git(monkeypatch):
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "b" * 40)


def _crash(log_path="/var/lib/nex-studio/terminal-logs/v/programovanie-sess.log"):
    return ParseFailure(
        "claude invocation failed: claude exited with code 1: boom",
        lost_work={"next_action": "AUDIT-FALLBACK — over git log a pokračuj."},
        envelope_loss_kind="crash",
        log_path=log_path,
    )


def _timeout():
    return ParseFailure(
        "claude invocation failed: claude invocation timed out after 2400s",
        lost_work={"next_action": "AUDIT-FALLBACK — over git log a pokračuj."},
        envelope_loss_kind="timeout",
        log_path=None,
    )


def _done_block():
    return PipelineStatusBlock(
        stage="programovanie", kind="gate_report", summary="hotovo", awaiting="manazer", commits=["a" * 40]
    )


async def test_crash_auto_retries_once_then_settles(db_session, monkeypatch):
    # Fix 2: a CRASH is re-invoked ONCE; a second consecutive crash settles awaiting_manazer (no infinite loop).
    version, project = _make_version(db_session)
    _seed_programovanie(db_session, version.id)
    _seed_one_task(db_session, version, project)
    _no_baseline_git(monkeypatch)
    calls = _stub_turns(monkeypatch, [_crash(), _crash()])  # crashes both times

    state = await orchestrator.run_dispatch(db_session, version.id)

    assert len(calls) == 2  # exactly ONE auto-retry (initial + 1 retry)
    assert state.status == "awaiting_manazer"
    assert state.current_stage == "programovanie"
    # the task is NOT marked failed — committed-but-lost work may have landed (stays in_progress for reclaim)
    assert [t.status for t in _tasks(db_session, version.id)] == ["in_progress"]


async def test_second_crash_after_retry_does_not_loop(db_session, monkeypatch):
    # Bound: even if EVERY turn crashes, the retry fires only once per dispatch (2 calls, not 5/∞).
    version, project = _make_version(db_session)
    _seed_programovanie(db_session, version.id)
    _seed_one_task(db_session, version, project)
    _no_baseline_git(monkeypatch)
    calls = _stub_turns(monkeypatch, [_crash()])  # last entry repeats → every call crashes

    await orchestrator.run_dispatch(db_session, version.id)
    assert len(calls) == 2  # initial + exactly one retry, then settle


async def test_timeout_does_not_auto_retry(db_session, monkeypatch):
    # Fix 2: a REAL timeout is conservative — settle at once, NO re-invoke (a re-run risks another long wall).
    version, project = _make_version(db_session)
    _seed_programovanie(db_session, version.id)
    _seed_one_task(db_session, version, project)
    _no_baseline_git(monkeypatch)
    calls = _stub_turns(monkeypatch, [_timeout(), _done_block()])  # would recover IF (wrongly) retried

    state = await orchestrator.run_dispatch(db_session, version.id)

    assert len(calls) == 1  # a timeout is NOT retried
    assert state.status == "awaiting_manazer"
    assert [t.status for t in _tasks(db_session, version.id)] == ["in_progress"]


async def test_crash_retry_recovers_when_second_turn_succeeds(db_session, monkeypatch):
    # The retry actually RE-RUNS the turn: a crash then a clean gate_report completes the task.
    version, project = _make_version(db_session)
    _seed_programovanie(db_session, version.id)
    _seed_one_task(db_session, version, project)
    _no_baseline_git(monkeypatch)
    _stub_mech(monkeypatch, None)  # mechanical verify passes
    calls = _stub_turns(monkeypatch, [_crash(), _done_block()])

    state = await orchestrator.run_dispatch(db_session, version.id)

    assert len(calls) == 2  # crash + one recovering retry
    assert state.status == "awaiting_manazer" and state.current_stage == "programovanie"
    assert [t.status for t in _tasks(db_session, version.id)] == ["done"]


async def test_timeout_and_crash_settle_messages_are_distinct(db_session, monkeypatch):
    # Fix 3: the settled next_action states the ACTUAL reason — a timeout message vs a distinct crash message.
    v1, p1 = _make_version(db_session)
    _seed_programovanie(db_session, v1.id)
    _seed_one_task(db_session, v1, p1)
    _no_baseline_git(monkeypatch)
    _stub_turns(monkeypatch, [_timeout()])
    st_timeout = await orchestrator.run_dispatch(db_session, v1.id)
    timeout_msg = st_timeout.next_action

    v2, p2 = _make_version(db_session)
    _seed_programovanie(db_session, v2.id)
    _seed_one_task(db_session, v2, p2)
    _stub_turns(monkeypatch, [_crash(log_path="/logs/prog-x.log"), _crash(log_path="/logs/prog-x.log")])
    st_crash = await orchestrator.run_dispatch(db_session, v2.id)
    crash_msg = st_crash.next_action

    assert timeout_msg != crash_msg  # NOT one shared "Vypršal čas agenta" string
    assert "časový limit" in timeout_msg and "40 min" in timeout_msg
    assert "stratil spojenie" in crash_msg and "raz znova" in crash_msg
    assert "log: /logs/prog-x.log" in crash_msg  # references the persisted log (Fix 1 ↔ Fix 3)


async def test_legacy_envelope_loss_without_kind_uses_audit_next_action(db_session, monkeypatch):
    # Backward-compat: an envelope-loss ParseFailure with no ``envelope_loss_kind`` (older shape) keeps using
    # the audit's own next_action verbatim — the honest-message override is scoped to timeout/crash only.
    version, project = _make_version(db_session)
    _seed_programovanie(db_session, version.id)
    _seed_one_task(db_session, version, project)
    _no_baseline_git(monkeypatch)
    legacy = ParseFailure(
        "claude invocation failed: timeout",
        lost_work={"next_action": "Práca možno commitnutá — skontroluj git log a pokračuj."},
    )
    calls = _stub_turns(monkeypatch, [legacy])
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert len(calls) == 1  # no kind → not treated as a crash → no retry
    assert state.next_action == "Práca možno commitnutá — skontroluj git log a pokračuj."
