"""Embedded agent terminal service — PTY-backed claude CLI processes.

Spawns ``claude --append-system-prompt …`` inside the backend container
under a PTY, broadcasts stdout to all attached WebSocket listeners,
forwards user input to the PTY master fd, and updates the audit row in
``agent_terminal_sessions`` on lifecycle events (spawn / end / idle / crash).

Persistence (2026-05-19 rework — Director directive)
----------------------------------------------------
Every PTY output chunk is **appended to a durable log file on disk**
at :data:`TERMINAL_LOG_DIR`/<session-uuid>.log in addition to the RAM
ring buffer. On attach, history is replayed from disk (not RAM), so:

* Re-login after long idle preserves the full visual history
* Cross-BE-restart sessions are resumed via ``claude --resume <uuid>``
  on first attach — both visual log + AI conversation memory continue

Session lifecycle:

* **spawn** assigns ``claude_session_id``, persists it in DB, and starts
  claude with ``--session-id <uuid>``. Log file is created (0600).
* PTY output → RAM deque (fast replay for fresh attachers) + disk file
  (durable replay for cross-restart attachers).
* On BE startup, sessions WITH ``claude_session_id`` are left as-is
  (resumable). Legacy sessions without UUID are finalized as
  ``server_restart``.
* On attach to a row whose runtime is absent, we **auto-respawn** via
  ``claude --resume <uuid>`` and continue appending to the same log.
* On explicit end / idle / crash, the log file remains for audit; the
  retention task cleans it up after :data:`LOG_RETENTION_DAYS` days.

Log rotation: when the file exceeds :data:`LOG_FILE_MAX_BYTES`, the
oldest half is truncated in-place (keeps the newer half intact). The
rotation marker ``\\n[...log rotated, older history truncated...]\\n``
is prepended so the user knows.

Thread-safety
-------------
All mutation goes through ``asyncio.Lock`` per session.
"""

from __future__ import annotations

import asyncio
import collections
import datetime as dt
import errno
import logging
import os
import re
import signal
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator, Optional

import ptyprocess
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.agent_terminal import AgentTerminalSession

logger = logging.getLogger(__name__)

PROJECTS_ROOT = Path("/opt/projects")
_SLUG_RE = re.compile(r"^[a-z][a-z0-9-]*[a-z0-9]$|^[a-z]$")
_VALID_ROLES = frozenset({"designer", "implementer", "auditor", "coordinator"})

# Output ring buffer (RAM) — fast replay for rapid re-attach. Disk log
# is authoritative for long-term history.
_BUFFER_MAX_CHUNKS = 512

#: Idle TTL — sessions with no IO from user (input) for this many seconds
#: get auto-killed by :func:`idle_cleanup`. 24h matches the policy
#: approved 2026-05-13.
IDLE_TTL_SECONDS = 24 * 3600

#: Grace period between SIGTERM and SIGKILL when ending a session.
SIGTERM_GRACE_SECONDS = 5

#: Directory for durable PTY output logs. Mounted as a Docker volume
#: in production (docker-compose.yml backend service).
TERMINAL_LOG_DIR = Path("/var/lib/nex-studio/terminal-logs")

#: Hard cap on per-session log file size. When exceeded, the oldest
#: half is truncated in-place (see :func:`_rotate_log_if_needed`).
LOG_FILE_MAX_BYTES = 100 * 1024 * 1024  # 100 MB

#: How long to keep log files for ended sessions. Background task in
#: lifespan periodically removes older files.
LOG_RETENTION_DAYS = 30

#: How often to scan for stale log files (retention cleanup interval).
LOG_CLEANUP_INTERVAL_SECONDS = 24 * 3600  # daily


class AgentTerminalError(ValueError):
    """Raised on invalid input (bad slug/role, missing project, etc.)."""


class SessionConflictError(AgentTerminalError):
    """Active session for (user, role) already exists."""


class SessionNotFoundError(AgentTerminalError):
    """No in-memory runtime AND no resumable DB row for the given id."""


@dataclass
class _RuntimeSession:
    """In-memory runtime state for one active session."""

    id: uuid.UUID
    user_id: uuid.UUID
    role: str
    project_slug: str
    process: ptyprocess.PtyProcess
    claude_session_id: Optional[uuid.UUID] = None
    output_buffer: collections.deque[bytes] = field(
        default_factory=lambda: collections.deque(maxlen=_BUFFER_MAX_CHUNKS),
    )
    listeners: set[asyncio.Queue[Optional[bytes]]] = field(default_factory=set)
    reader_task: Optional[asyncio.Task] = None
    last_input_at: float = field(default_factory=time.time)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_sessions: dict[uuid.UUID, _RuntimeSession] = {}
_registry_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _validate_role(role: str) -> None:
    if role not in _VALID_ROLES:
        raise AgentTerminalError(f"Invalid role: {role!r}")


def _validate_slug(slug: str) -> None:
    if not _SLUG_RE.match(slug):
        raise AgentTerminalError(f"Invalid slug: {slug!r}")


def _agent_spec_path(project_root: Path, role: str) -> Path:
    """Charter path for a role within a project: ``.claude/agents/<role>/CLAUDE.md``."""
    return project_root / ".claude" / "agents" / role / "CLAUDE.md"


def _resolve_agent_spec(slug: str, role: str) -> Path:
    """Return the validated path to ``.claude/agents/<role>/CLAUDE.md``."""
    _validate_slug(slug)
    _validate_role(role)
    project_root = PROJECTS_ROOT / slug
    if not project_root.is_dir():
        raise AgentTerminalError(f"Project not found: {slug}")
    spec = _agent_spec_path(project_root, role)
    if not spec.is_file():
        raise AgentTerminalError(
            f"Agent spec missing for {slug}/{role}: expected {spec}",
        )
    return spec


def available_roles(slug: str) -> dict[str, bool]:
    """Return charter availability per valid role for ``slug``.

    Non-raising spec-path check (mirrors :func:`_resolve_agent_spec`):
    ``{role: <CLAUDE.md exists>}`` for every role in :data:`_VALID_ROLES`.
    Raises :class:`AgentTerminalError` on an invalid slug or missing project
    directory (the router maps that to a 404).
    """
    _validate_slug(slug)
    project_root = PROJECTS_ROOT / slug
    if not project_root.is_dir():
        raise AgentTerminalError(f"Project not found: {slug}")
    return {role: _agent_spec_path(project_root, role).is_file() for role in _VALID_ROLES}


# ---------------------------------------------------------------------------
# Disk log helpers
# ---------------------------------------------------------------------------


def _log_path(session_id: uuid.UUID) -> Path:
    """Compute the per-session log file path."""
    return TERMINAL_LOG_DIR / f"{session_id}.log"


def _ensure_log_dir() -> None:
    """Create TERMINAL_LOG_DIR if missing. Idempotent. Mode 0700."""
    TERMINAL_LOG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)


def _create_log_file(session_id: uuid.UUID) -> Path:
    """Create an empty log file with mode 0600. Returns the path."""
    _ensure_log_dir()
    path = _log_path(session_id)
    # Open with O_CREAT|O_TRUNC|O_WRONLY mode 0600 — overwrites if exists
    # (shouldn't happen for a freshly assigned UUID).
    fd = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    os.close(fd)
    return path


def _append_chunk_to_log(session_id: uuid.UUID, chunk: bytes) -> None:
    """Append a PTY output chunk to the session's disk log.

    Performs rotation if the file exceeds :data:`LOG_FILE_MAX_BYTES`
    after the append: truncates the oldest half in-place, prepending a
    visible marker so the user knows.

    Tolerant of missing dir / file (recreates). Errors are logged but
    not raised — disk persistence must not crash the reader task.
    """
    path = _log_path(session_id)
    try:
        _ensure_log_dir()
        # Append (file may not exist yet — created here lazily for safety).
        with open(path, "ab") as fh:
            fh.write(chunk)
        _rotate_log_if_needed(path)
    except OSError as exc:
        logger.warning(
            "agent_terminal: failed to append to %s: %s",
            path,
            exc,
        )


def _rotate_log_if_needed(path: Path) -> None:
    """If file > LOG_FILE_MAX_BYTES, keep the newer half + marker prefix.

    In-place: read tail bytes into memory (max half of limit), then
    rewrite the file with a marker + tail. Brief blocking IO; acceptable
    because rotation is rare (only when a session's log doubles).
    """
    try:
        size = path.stat().st_size
    except OSError:
        return
    if size <= LOG_FILE_MAX_BYTES:
        return
    keep_bytes = LOG_FILE_MAX_BYTES // 2
    try:
        with open(path, "rb") as fh:
            fh.seek(size - keep_bytes)
            tail = fh.read(keep_bytes)
        marker = (
            b"\r\n\x1b[33m[...log rotated, older history truncated to keep size <= "
            + str(LOG_FILE_MAX_BYTES).encode()
            + b" bytes...]\x1b[0m\r\n"
        )
        with open(path, "wb") as fh:
            fh.write(marker)
            fh.write(tail)
    except OSError as exc:
        logger.warning("agent_terminal: log rotation failed for %s: %s", path, exc)


def _replay_log(session_id: uuid.UUID, chunk_size: int = 65536) -> list[bytes]:
    """Read the full log file and return a list of byte chunks.

    Returns empty list if file does not exist (legacy session or new
    session before first chunk).
    """
    path = _log_path(session_id)
    if not path.is_file():
        return []
    chunks: list[bytes] = []
    try:
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(chunk_size)
                if not chunk:
                    break
                chunks.append(chunk)
    except OSError as exc:
        logger.warning("agent_terminal: log replay failed for %s: %s", path, exc)
    return chunks


# ---------------------------------------------------------------------------
# PTY spawn helpers (initial + resume)
# ---------------------------------------------------------------------------


def _spawn_pty(
    *,
    spec_path: Path,
    project_root: Path,
    claude_session_id: uuid.UUID,
    resume: bool,
) -> ptyprocess.PtyProcess:
    """Spawn the claude CLI under PTY.

    Args:
        spec_path: per-role charter (`.claude/agents/<role>/CLAUDE.md`)
        project_root: cwd for claude (project source root)
        claude_session_id: UUID for ``--session-id`` (fresh) or ``--resume``
        resume: ``True`` → resume existing session by uuid; ``False`` →
            create new session with charter injection.
    """
    env = {**os.environ, "TERM": "xterm-256color", "FORCE_COLOR": "1"}
    if resume:
        argv = ["claude", "--resume", str(claude_session_id)]
    else:
        append_prompt = spec_path.read_text(encoding="utf-8")
        argv = [
            "claude",
            "--session-id",
            str(claude_session_id),
            "--append-system-prompt",
            append_prompt,
        ]
    return ptyprocess.PtyProcess.spawn(
        argv,
        cwd=str(project_root),
        env=env,
        dimensions=(40, 120),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def spawn(
    *,
    user_id: uuid.UUID,
    role: str,
    project_slug: str,
    db: Session,
    claude_session_id: Optional[uuid.UUID] = None,
) -> AgentTerminalSession:
    """Spawn a new claude CLI process under PTY for ``(user, role, project)``.

    Assigns a fresh ``claude_session_id`` (UUID4) and creates a durable
    log file on disk. Both are required for cross-restart auto-resume.

    When ``claude_session_id`` is provided (CR-NS-018 §10 debug attach), the
    PTY ``--resume``s that existing session instead of minting a fresh one —
    so a Director can attach an interactive terminal to the orchestrator's
    headless agent conversation.

    Raises:
        AgentTerminalError: invalid role/slug or missing agent spec.
        SessionConflictError: an active session already exists for this
            ``(user_id, role)`` pair.
    """
    spec_path = _resolve_agent_spec(project_slug, role)

    existing = db.execute(
        select(AgentTerminalSession).where(
            AgentTerminalSession.user_id == user_id,
            AgentTerminalSession.role == role,
            AgentTerminalSession.ended_at.is_(None),
        ),
    ).scalar_one_or_none()
    if existing is not None:
        raise SessionConflictError(
            f"Active {role} session already running for user {user_id} (session_id={existing.id})",
        )

    project_root = PROJECTS_ROOT / project_slug
    if claude_session_id is None:
        claude_session_id = uuid.uuid4()
        resume = False
    else:
        resume = True
    proc = _spawn_pty(
        spec_path=spec_path,
        project_root=project_root,
        claude_session_id=claude_session_id,
        resume=resume,
    )

    row = AgentTerminalSession(
        user_id=user_id,
        role=role,
        project_slug=project_slug,
        pid=proc.pid,
        claude_session_id=claude_session_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    # Create durable log file (mode 0600). The reader task appends chunks
    # to this file in parallel with the RAM ring buffer.
    _create_log_file(row.id)

    runtime = _RuntimeSession(
        id=row.id,
        user_id=user_id,
        role=role,
        project_slug=project_slug,
        process=proc,
        claude_session_id=claude_session_id,
    )

    async with _registry_lock:
        _sessions[row.id] = runtime

    runtime.reader_task = asyncio.create_task(
        _pump_output(runtime),
        name=f"agent-terminal-reader-{row.id}",
    )

    logger.info(
        "Spawned agent terminal session: id=%s user=%s role=%s project=%s pid=%s claude_session_id=%s",
        row.id,
        user_id,
        role,
        project_slug,
        proc.pid,
        claude_session_id,
    )
    return row


async def _respawn_for_resume(
    *,
    row: AgentTerminalSession,
    db: Session,
) -> _RuntimeSession:
    """Spawn a new PTY for an existing session via ``claude --resume <uuid>``.

    Called from :func:`attach` when the row exists in DB (ended_at IS NULL,
    has claude_session_id) but no runtime entry exists — typically after
    a BE restart. Continues appending to the **same** disk log so the
    user sees pre-restart history + new content seamlessly.

    Updates the row's ``pid`` to the new PTY process id. The
    ``claude_session_id`` is unchanged (--resume keeps AI memory).
    """
    if row.claude_session_id is None:
        raise SessionNotFoundError(
            f"Session {row.id} cannot be resumed: no claude_session_id (legacy session from before migration 046)",
        )
    spec_path = _resolve_agent_spec(row.project_slug, row.role)
    project_root = PROJECTS_ROOT / row.project_slug

    proc = _spawn_pty(
        spec_path=spec_path,
        project_root=project_root,
        claude_session_id=row.claude_session_id,
        resume=True,
    )

    # Update pid in DB row (new PTY process).
    row.pid = proc.pid
    db.commit()

    runtime = _RuntimeSession(
        id=row.id,
        user_id=row.user_id,
        role=row.role,
        project_slug=row.project_slug,
        process=proc,
        claude_session_id=row.claude_session_id,
    )

    async with _registry_lock:
        _sessions[row.id] = runtime

    runtime.reader_task = asyncio.create_task(
        _pump_output(runtime),
        name=f"agent-terminal-reader-{row.id}",
    )

    logger.info(
        "Resumed agent terminal session: id=%s role=%s new_pid=%s claude_session_id=%s",
        row.id,
        row.role,
        proc.pid,
        row.claude_session_id,
    )
    return runtime


async def attach(session_id: uuid.UUID) -> AsyncIterator[bytes]:
    """Async iterator yielding bytes: full disk-log history first, then live.

    If no runtime entry exists but a resumable row is present in DB,
    auto-respawns the PTY via ``claude --resume <uuid>`` first.

    Caller (the WebSocket endpoint) consumes this iterator until it
    returns (session ended) or the WS disconnects. The ``finally`` block
    removes this listener queue.
    """
    runtime = _sessions.get(session_id)
    if runtime is None:
        # Try auto-respawn from DB row (cross-restart scenario).
        from backend.db.session import SessionLocal

        db = SessionLocal()
        try:
            row = db.get(AgentTerminalSession, session_id)
            if row is None or row.ended_at is not None:
                raise SessionNotFoundError(f"No active session: {session_id}")
            if row.claude_session_id is None:
                raise SessionNotFoundError(
                    f"Session {session_id} cannot be resumed (legacy, no claude_session_id)",
                )
            runtime = await _respawn_for_resume(row=row, db=db)
        finally:
            db.close()

    q: asyncio.Queue[Optional[bytes]] = asyncio.Queue(maxsize=256)
    # Snapshot disk log BEFORE registering listener — once we register,
    # the reader task may append new chunks to ``q``. Disk log already
    # contains everything up to ~now; the listener gets everything from
    # ~now onward. The deque overlap (last 64 KB) is fine — duplicate
    # bytes don't break the terminal renderer (ANSI is idempotent for
    # repeated escape sequences, plain text is harmless).
    history = _replay_log(session_id)
    async with runtime.lock:
        runtime.listeners.add(q)

    try:
        for chunk in history:
            yield chunk
        while True:
            chunk = await q.get()
            if chunk is None:
                return  # process ended (sentinel)
            yield chunk
    finally:
        async with runtime.lock:
            runtime.listeners.discard(q)


async def write_input(session_id: uuid.UUID, data: bytes) -> None:
    """Forward keystrokes from the WS client to the PTY master fd."""
    runtime = _sessions.get(session_id)
    if runtime is None:
        raise SessionNotFoundError(f"No active session: {session_id}")
    runtime.last_input_at = time.time()
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, runtime.process.write, data)


async def resize(session_id: uuid.UUID, rows: int, cols: int) -> None:
    """Update PTY winsize after browser resize."""
    runtime = _sessions.get(session_id)
    if runtime is None:
        raise SessionNotFoundError(f"No active session: {session_id}")
    runtime.process.setwinsize(rows, cols)


async def end_session(
    session_id: uuid.UUID,
    *,
    terminated_by: str,
    db: Session,
) -> None:
    """Stop a running session. SIGTERM, grace, SIGKILL. DB row finalized.

    Idempotent. Note: the disk log file is **kept** after end_session;
    the retention task (:func:`cleanup_old_logs`) removes it after
    :data:`LOG_RETENTION_DAYS` days.
    """
    runtime = _sessions.get(session_id)
    if runtime is None:
        _finalize_db_row(
            session_id,
            terminated_by=terminated_by,
            exit_code=None,
            db=db,
        )
        return

    try:
        runtime.process.kill(signal.SIGTERM)
    except Exception:  # noqa: BLE001
        pass

    for _ in range(SIGTERM_GRACE_SECONDS * 10):
        if not runtime.process.isalive():
            break
        await asyncio.sleep(0.1)
    if runtime.process.isalive():
        try:
            runtime.process.kill(signal.SIGKILL)
        except Exception:  # noqa: BLE001
            pass

    await _cleanup_after_exit(runtime, terminated_by=terminated_by, db=db)


async def idle_cleanup(db: Session) -> int:
    """Kill sessions idle for > :data:`IDLE_TTL_SECONDS`. Returns count."""
    now = time.time()
    to_kill: list[uuid.UUID] = [
        sid for sid, runtime in _sessions.items() if now - runtime.last_input_at > IDLE_TTL_SECONDS
    ]
    for sid in to_kill:
        try:
            await end_session(sid, terminated_by="idle", db=db)
        except Exception:
            logger.exception("idle_cleanup: failed to end session %s", sid)
    if to_kill:
        logger.info("idle_cleanup killed %d sessions", len(to_kill))
    return len(to_kill)


def mark_orphaned_on_startup(db: Session) -> int:
    """Finalize legacy rows on BE startup. Resumable rows survive.

    With migration 046, sessions with ``claude_session_id`` are
    **resumable** via ``claude --resume <uuid>`` on next attach. Their
    DB row stays as-is (ended_at remains NULL); first attach triggers
    auto-respawn.

    Legacy rows (claude_session_id IS NULL — spawned before migration
    046) cannot be resumed and are finalized as ``server_restart``.

    Returns the count of legacy rows finalized.
    """
    legacy_rows = (
        db.execute(
            select(AgentTerminalSession).where(
                AgentTerminalSession.ended_at.is_(None),
                AgentTerminalSession.claude_session_id.is_(None),
            ),
        )
        .scalars()
        .all()
    )
    from sqlalchemy import func

    for row in legacy_rows:
        row.ended_at = func.now()
        row.terminated_by = "server_restart"
    db.commit()
    if legacy_rows:
        logger.info(
            "Marked %d legacy (pre-046) sessions as server_restart",
            len(legacy_rows),
        )

    resumable_count = (
        db.execute(
            select(AgentTerminalSession).where(
                AgentTerminalSession.ended_at.is_(None),
                AgentTerminalSession.claude_session_id.isnot(None),
            ),
        )
        .scalars()
        .all()
    )
    if resumable_count:
        logger.info(
            "Found %d resumable sessions (will respawn on first attach)",
            len(resumable_count),
        )
    return len(legacy_rows)


def cleanup_old_logs(db: Session) -> int:
    """Delete log files for sessions ended more than LOG_RETENTION_DAYS ago.

    Two-pass:

    1. For each ``.log`` file in :data:`TERMINAL_LOG_DIR`, look up the
       session row. If row exists and ``ended_at`` is older than
       retention threshold → delete file.
    2. Orphan files (no matching row in DB) older than retention by
       file mtime → delete.

    Returns the count of files deleted.
    """
    if not TERMINAL_LOG_DIR.is_dir():
        return 0

    threshold_dt = dt.datetime.now(dt.timezone.utc) - dt.timedelta(
        days=LOG_RETENTION_DAYS,
    )
    threshold_ts = threshold_dt.timestamp()
    deleted = 0
    for path in TERMINAL_LOG_DIR.glob("*.log"):
        try:
            session_id = uuid.UUID(path.stem)
        except ValueError:
            continue  # unexpected filename
        row = db.get(AgentTerminalSession, session_id)
        should_delete = False
        if row is not None and row.ended_at is not None:
            if row.ended_at < threshold_dt:
                should_delete = True
        elif row is None:
            # Orphan file (no DB row). Use mtime as fallback.
            try:
                if path.stat().st_mtime < threshold_ts:
                    should_delete = True
            except OSError:
                continue
        if should_delete:
            try:
                path.unlink()
                deleted += 1
            except OSError as exc:
                logger.warning(
                    "agent_terminal: failed to delete stale log %s: %s",
                    path,
                    exc,
                )
    if deleted:
        logger.info("agent_terminal: deleted %d stale log files (>%dd)", deleted, LOG_RETENTION_DAYS)
    return deleted


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _pump_output(runtime: _RuntimeSession) -> None:
    """Reader task: PTY → RAM deque + disk log + WS listeners.

    Terminates when the process exits. On exit, finalizes the DB row
    via :func:`_cleanup_after_exit`.
    """
    loop = asyncio.get_running_loop()
    try:
        while True:
            chunk = await loop.run_in_executor(None, _safe_read, runtime.process)
            if chunk is None:
                break
            # Disk log append (outside the lock — file IO is independent
            # from in-memory broadcast; failures are logged but tolerated).
            _append_chunk_to_log(runtime.id, chunk)
            async with runtime.lock:
                runtime.output_buffer.append(chunk)
                for q in runtime.listeners:
                    try:
                        q.put_nowait(chunk)
                    except asyncio.QueueFull:
                        try:
                            q.get_nowait()
                            q.put_nowait(chunk)
                        except Exception:
                            pass
    except Exception:
        logger.exception("agent_terminal reader crashed for session %s", runtime.id)

    from backend.db.session import SessionLocal

    exit_code = runtime.process.exitstatus
    terminated_by = "user" if exit_code == 0 else "crash"
    db = SessionLocal()
    try:
        await _cleanup_after_exit(runtime, terminated_by=terminated_by, db=db)
    finally:
        db.close()


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


async def _cleanup_after_exit(
    runtime: _RuntimeSession,
    *,
    terminated_by: str,
    db: Session,
) -> None:
    """Send sentinel to listeners, finalize DB row, drop runtime entry."""
    async with runtime.lock:
        for q in list(runtime.listeners):
            try:
                q.put_nowait(None)
            except Exception:  # noqa: BLE001
                pass
        runtime.listeners.clear()

    _finalize_db_row(
        runtime.id,
        terminated_by=terminated_by,
        exit_code=runtime.process.exitstatus,
        db=db,
    )

    async with _registry_lock:
        _sessions.pop(runtime.id, None)


def _finalize_db_row(
    session_id: uuid.UUID,
    *,
    terminated_by: str,
    exit_code: Optional[int],
    db: Session,
) -> None:
    """Update the audit row with ``ended_at`` + ``exit_code`` + ``terminated_by``."""
    from sqlalchemy import func

    row = db.get(AgentTerminalSession, session_id)
    if row is None:
        logger.warning("finalize: row not found for session %s", session_id)
        return
    if row.ended_at is not None:
        return
    row.ended_at = func.now()
    row.exit_code = exit_code
    row.terminated_by = terminated_by
    db.commit()


# Exposed for tests — direct access to the registry for assertion.
def _get_runtime_for_test(session_id: uuid.UUID) -> Optional[_RuntimeSession]:
    return _sessions.get(session_id)
