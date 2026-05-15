"""Dialogue orchestration service — Customer ↔ Designer via Director gate.

Director directive 2026-05-15: 4th ICC agent (Customer) systematically
asks Designer about application functionality; Director mediates every
message (plný-gate mode). This service holds:

* Session lifecycle (create / pause / end / startup orphan cleanup)
* Message lifecycle (add pending → Director approves → mark delivered
  → forward to recipient agent via its PTY stdin)
* PTY orchestration: spawns ``claude --append-system-prompt`` for both
  Customer and Designer per session, owns their lifetimes, routes
  messages between them via DB

Architecture notes
------------------
Dialogue lives in **its own PTY namespace**, distinct from
:mod:`backend.services.agent_terminal`. Reason: agent_terminal enforces
"single active session per (user, role)" — that conflicts with a
Director who may have a standalone Designer terminal open AND a Gate E
dialogue (where Designer is one of the two dialogue agents). Keeping
dialogue PTYs separate eliminates the constraint clash.

The shared low-level PTY mechanics (spawn + read pump + write +
SIGTERM) are deliberately copy-pasted from ``agent_terminal`` rather
than extracted to a helper module — the two services have subtly
different invariants (agent_terminal exposes a live terminal to the
user; dialogue uses agents headlessly and writes their stdout to DB
messages). A future refactor can extract once both shapes stabilise.
"""

from __future__ import annotations

import asyncio
import collections
import errno
import logging
import os
import re
import signal
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import ptyprocess
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.dialogue import DialogueMessage, DialogueSession

logger = logging.getLogger(__name__)

PROJECTS_ROOT = Path("/opt/projects")
_SLUG_RE = re.compile(r"^[a-z][a-z0-9-]*[a-z0-9]$|^[a-z]$")
_DIALOGUE_ROLES = frozenset({"customer", "designer"})

#: PTY output ring buffer per agent (used to collect agent's stdout
#: into a single message that gets persisted when the agent stops
#: producing output for SETTLE_SECONDS).
_BUFFER_MAX_BYTES = 64 * 1024

#: Settle window after which the accumulated agent output is treated
#: as one complete message and persisted into ``dialogue_messages``.
#: Agents typically write their response in one burst; this delay
#: catches stragglers (e.g. token streaming) without waiting forever.
SETTLE_SECONDS = 2.5

#: SIGTERM grace before SIGKILL when ending a session.
SIGTERM_GRACE_SECONDS = 5


class DialogueError(ValueError):
    """Raised on invalid input (bad slug, missing project, missing
    agent spec, malformed message state transition)."""


class DialogueSessionNotFoundError(DialogueError):
    """No active in-memory session for the given id."""


@dataclass
class _AgentChannel:
    """One side (Customer or Designer) of a dialogue session.

    Holds the PTY process + a collector for in-flight stdout (so
    multi-chunk agent responses can be assembled into a single
    DialogueMessage row).
    """

    role: str
    process: ptyprocess.PtyProcess
    output_buffer: bytearray = field(default_factory=bytearray)
    last_output_at: float = field(default=0.0)
    reader_task: Optional[asyncio.Task] = None
    settle_task: Optional[asyncio.Task] = None


@dataclass
class _RuntimeSession:
    """In-memory runtime state for one active dialogue session."""

    id: uuid.UUID
    user_id: uuid.UUID
    project_slug: str
    version_id: Optional[uuid.UUID]
    customer: _AgentChannel
    designer: _AgentChannel
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    listeners: set[asyncio.Queue] = field(default_factory=set)


_sessions: dict[uuid.UUID, _RuntimeSession] = {}
_registry_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_slug(slug: str) -> None:
    if not _SLUG_RE.match(slug):
        raise DialogueError(f"Invalid slug: {slug!r}")


def _resolve_agent_spec(slug: str, role: str) -> Path:
    """Return the validated path to ``.claude/agents/<role>/CLAUDE.md``."""
    _validate_slug(slug)
    if role not in _DIALOGUE_ROLES:
        raise DialogueError(f"Invalid dialogue role: {role!r}")
    project_root = PROJECTS_ROOT / slug
    if not project_root.is_dir():
        raise DialogueError(f"Project not found: {slug}")
    spec = project_root / ".claude" / "agents" / role / "CLAUDE.md"
    if not spec.is_file():
        raise DialogueError(
            f"Agent spec missing for {slug}/{role}: expected {spec}",
        )
    return spec


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def create_session(
    *,
    user_id: uuid.UUID,
    project_slug: str,
    version_id: Optional[uuid.UUID],
    db: Session,
) -> DialogueSession:
    """Spawn both agents under PTY and create the DB session row.

    Raises:
        DialogueError: invalid slug, missing project, missing charter
            for customer or designer (both must exist).
    """
    customer_spec = _resolve_agent_spec(project_slug, "customer")
    designer_spec = _resolve_agent_spec(project_slug, "designer")

    row = DialogueSession(
        user_id=user_id,
        project_slug=project_slug,
        version_id=version_id,
        status="active",
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    customer_proc = _spawn_agent("customer", project_slug, customer_spec)
    designer_proc = _spawn_agent("designer", project_slug, designer_spec)

    runtime = _RuntimeSession(
        id=row.id,
        user_id=user_id,
        project_slug=project_slug,
        version_id=version_id,
        customer=_AgentChannel(role="customer", process=customer_proc),
        designer=_AgentChannel(role="designer", process=designer_proc),
    )

    async with _registry_lock:
        _sessions[row.id] = runtime

    # Spawn reader tasks for both agents.
    runtime.customer.reader_task = asyncio.create_task(
        _pump_agent_output(runtime, "customer"),
        name=f"dialogue-reader-customer-{row.id}",
    )
    runtime.designer.reader_task = asyncio.create_task(
        _pump_agent_output(runtime, "designer"),
        name=f"dialogue-reader-designer-{row.id}",
    )

    logger.info(
        "Created dialogue session: id=%s user=%s project=%s customer_pid=%s designer_pid=%s",
        row.id,
        user_id,
        project_slug,
        customer_proc.pid,
        designer_proc.pid,
    )
    return row


def _spawn_agent(role: str, project_slug: str, spec_path: Path) -> ptyprocess.PtyProcess:
    """Spawn ``claude --append-system-prompt $(cat spec)`` for the given role."""
    append_prompt = spec_path.read_text(encoding="utf-8")
    env = {**os.environ, "TERM": "xterm-256color", "FORCE_COLOR": "0"}
    project_root = PROJECTS_ROOT / project_slug
    return ptyprocess.PtyProcess.spawn(
        ["claude", "--append-system-prompt", append_prompt],
        cwd=str(project_root),
        env=env,
        dimensions=(40, 120),
    )


async def send_to_agent(
    *,
    session_id: uuid.UUID,
    recipient: str,
    content: str,
) -> None:
    """Forward an approved message into the recipient agent's stdin."""
    runtime = _sessions.get(session_id)
    if runtime is None:
        raise DialogueSessionNotFoundError(f"No active session: {session_id}")
    channel = runtime.customer if recipient == "customer" else runtime.designer
    loop = asyncio.get_running_loop()
    # Append newline so the agent's input loop sees a complete prompt.
    await loop.run_in_executor(None, channel.process.write, content.encode("utf-8") + b"\n")


async def end_session(
    *,
    session_id: uuid.UUID,
    terminated_by: str,
    db: Session,
) -> None:
    """SIGTERM both agents → grace → SIGKILL. Finalize DB row."""
    runtime = _sessions.get(session_id)
    if runtime is not None:
        for channel in (runtime.customer, runtime.designer):
            try:
                channel.process.kill(signal.SIGTERM)
            except Exception:  # noqa: BLE001
                pass

        for _ in range(SIGTERM_GRACE_SECONDS * 10):
            both_dead = all(not c.process.isalive() for c in (runtime.customer, runtime.designer))
            if both_dead:
                break
            await asyncio.sleep(0.1)

        for channel in (runtime.customer, runtime.designer):
            if channel.process.isalive():
                try:
                    channel.process.kill(signal.SIGKILL)
                except Exception:  # noqa: BLE001
                    pass

        async with _registry_lock:
            _sessions.pop(session_id, None)

    # Finalize DB row regardless of runtime presence.
    from sqlalchemy import func as sql_func

    row = db.get(DialogueSession, session_id)
    if row is not None and row.ended_at is None:
        row.status = "ended"
        row.ended_at = sql_func.now()
        row.terminated_by = terminated_by
        db.commit()


def mark_orphaned_on_startup(db: Session) -> int:
    """On BE startup, mark all ``status='active'`` rows from previous
    boot as ``terminated_by='server_restart'``.

    Dialogue PTY processes don't survive container restart — every
    pre-existing active row is an orphan.
    """
    from sqlalchemy import func as sql_func

    rows = (
        db.execute(
            select(DialogueSession).where(DialogueSession.status == "active"),
        )
        .scalars()
        .all()
    )
    for row in rows:
        row.status = "ended"
        row.ended_at = sql_func.now()
        row.terminated_by = "server_restart"
    db.commit()
    if rows:
        logger.info("Marked %d orphan dialogue sessions as server_restart", len(rows))
    return len(rows)


# ---------------------------------------------------------------------------
# Message lifecycle
# ---------------------------------------------------------------------------


def add_message(
    *,
    session_id: uuid.UUID,
    author: str,
    content: str,
    status: str,
    db: Session,
) -> DialogueMessage:
    """Persist a message row + bump the session's ``message_count``."""
    msg = DialogueMessage(
        session_id=session_id,
        author=author,
        content=content,
        status=status,
    )
    db.add(msg)
    sess = db.get(DialogueSession, session_id)
    if sess is not None:
        sess.message_count = (sess.message_count or 0) + 1
    db.commit()
    db.refresh(msg)
    return msg


def approve_message(message_id: uuid.UUID, db: Session) -> DialogueMessage:
    """``pending → approved``. Caller then forwards content to the
    recipient agent via :func:`send_to_agent` and marks ``delivered``."""
    msg = db.get(DialogueMessage, message_id)
    if msg is None:
        raise DialogueError(f"Message not found: {message_id}")
    if msg.status != "pending":
        raise DialogueError(
            f"Cannot approve message in status {msg.status!r} (must be 'pending')",
        )
    msg.status = "approved"
    db.commit()
    db.refresh(msg)
    return msg


def mark_delivered(message_id: uuid.UUID, db: Session) -> DialogueMessage:
    """``approved → delivered``. Called after :func:`send_to_agent`
    succeeds in writing to the recipient agent's stdin."""
    msg = db.get(DialogueMessage, message_id)
    if msg is None:
        raise DialogueError(f"Message not found: {message_id}")
    if msg.status != "approved":
        raise DialogueError(
            f"Cannot deliver message in status {msg.status!r} (must be 'approved')",
        )
    msg.status = "delivered"
    db.commit()
    db.refresh(msg)
    return msg


def reject_message(message_id: uuid.UUID, db: Session) -> DialogueMessage:
    """``pending → rejected``. Director rejects Customer's question
    (e.g. ask Customer to reformulate). Caller may then trigger a
    fresh Customer question."""
    msg = db.get(DialogueMessage, message_id)
    if msg is None:
        raise DialogueError(f"Message not found: {message_id}")
    if msg.status != "pending":
        raise DialogueError(
            f"Cannot reject message in status {msg.status!r} (must be 'pending')",
        )
    msg.status = "rejected"
    db.commit()
    db.refresh(msg)
    return msg


# ---------------------------------------------------------------------------
# Agent output collection — pump stdout, settle window, persist as message
# ---------------------------------------------------------------------------


async def _pump_agent_output(runtime: _RuntimeSession, role: str) -> None:
    """Reader task: collect PTY stdout into a settling buffer; when the
    buffer is quiet for :data:`SETTLE_SECONDS`, flush as a pending
    DialogueMessage authored by this role."""
    loop = asyncio.get_running_loop()
    channel = runtime.customer if role == "customer" else runtime.designer
    try:
        while True:
            chunk = await loop.run_in_executor(None, _safe_read, channel.process)
            if chunk is None:
                break
            async with runtime.lock:
                channel.output_buffer.extend(chunk)
                channel.last_output_at = time.time()
                # Schedule / restart settle task — when no new chunk
                # for SETTLE_SECONDS, flush.
                if channel.settle_task is None or channel.settle_task.done():
                    channel.settle_task = asyncio.create_task(
                        _settle_and_flush(runtime, role),
                        name=f"dialogue-settle-{role}-{runtime.id}",
                    )
    except Exception:
        logger.exception("dialogue reader crashed: session=%s role=%s", runtime.id, role)


async def _settle_and_flush(runtime: _RuntimeSession, role: str) -> None:
    """After SETTLE_SECONDS of no new chunks, persist the buffered
    bytes as one DialogueMessage (status=pending) and broadcast to
    WS listeners."""
    channel = runtime.customer if role == "customer" else runtime.designer
    while True:
        await asyncio.sleep(SETTLE_SECONDS)
        async with runtime.lock:
            now = time.time()
            if now - channel.last_output_at >= SETTLE_SECONDS and channel.output_buffer:
                content = channel.output_buffer.decode("utf-8", errors="replace").strip()
                channel.output_buffer.clear()
                if content:
                    # Persist as pending message + broadcast.
                    from backend.db.session import SessionLocal

                    db = SessionLocal()
                    try:
                        msg = add_message(
                            session_id=runtime.id,
                            author=role,
                            content=content,
                            status="pending",
                            db=db,
                        )
                    finally:
                        db.close()
                    await _broadcast(runtime, {"type": "message", "message_id": str(msg.id)})
                return
            # Otherwise keep waiting (new chunks arrived in the meantime).


async def _broadcast(runtime: _RuntimeSession, payload: dict) -> None:
    """Send a JSON-serializable payload to every WS listener of this session."""
    async with runtime.lock:
        for q in list(runtime.listeners):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass


async def subscribe(session_id: uuid.UUID) -> asyncio.Queue:
    """WebSocket endpoint subscribes here to receive broadcast events
    (new messages, status updates, session end). Caller must call
    :func:`unsubscribe` when the WS disconnects."""
    runtime = _sessions.get(session_id)
    if runtime is None:
        raise DialogueSessionNotFoundError(f"No active session: {session_id}")
    q: asyncio.Queue = asyncio.Queue(maxsize=256)
    async with runtime.lock:
        runtime.listeners.add(q)
    return q


async def unsubscribe(session_id: uuid.UUID, q: asyncio.Queue) -> None:
    runtime = _sessions.get(session_id)
    if runtime is None:
        return
    async with runtime.lock:
        runtime.listeners.discard(q)


# ---------------------------------------------------------------------------
# Internal PTY helpers
# ---------------------------------------------------------------------------


def _safe_read(proc: ptyprocess.PtyProcess) -> Optional[bytes]:
    """Blocking PTY read. Returns ``None`` on EOF / EIO."""
    try:
        return proc.read(4096)
    except EOFError:
        return None
    except OSError as exc:
        if exc.errno == errno.EIO:
            return None
        raise


# Test-only access.
def _get_runtime_for_test(session_id: uuid.UUID) -> Optional[_RuntimeSession]:
    return _sessions.get(session_id)


# Test-only — reset registry between tests.
def _clear_registry_for_test() -> None:
    _sessions.clear()


# Unused-import suppression — collections + bytes are used in
# placeholder branches; keep for future extension.
_ = collections
