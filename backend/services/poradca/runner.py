"""Run of a single Poradca question (ICCINT-167, design §3 and §4.1).

The question is saved immediately; the answer is created as ``running`` and filled in in the background:

  1. waits for a free slot — at most ``poradca_max_concurrent`` questions run at once in the whole cockpit (all
     draw on one subscription with a limit, like builds); while it waits, the person sees why;
  2. prepares the directories, the secret overlays and the tools server on this question's socket;
  3. starts the container (:mod:`.sandbox`) on its own fenced network and reads the ``stream-json`` progress;
     from it only the STEP (tool and target) goes to the database and the browser, never the content;
  4. the answer passes the secrets filter and is saved with its usage and duration.

Stopping kills the ``docker run`` client and the container. The limit for a single question is
``settings.poradca_question_timeout``. A failure is always recorded as a sentence for the human — a question never
stays hanging in ``running``: at backend start :func:`fail_orphans` closes those interrupted by a restart.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import signal
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.config.settings import settings
from backend.core.agent_env import agent_env
from backend.db.models.foundation import User, UserAgentSettings
from backend.db.models.pipeline import PipelineState
from backend.db.models.poradca import (
    AUTHOR_HUMAN,
    AUTHOR_PORADCA,
    DONE,
    FAILED,
    RUNNING,
    STOPPED,
    PoradcaConversation,
    PoradcaMessage,
)
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.db.session import SessionLocal
from backend.services import build_db, build_sandbox, system_setting, usage_ledger
from backend.services.claude_agent import UsageMetadata, _usage_from
from backend.services.poradca import attachments, sandbox
from backend.services.poradca.context import known_secret_values
from backend.services.poradca.mcp_server import McpServer
from backend.services.poradca.secrets_filter import SecretFilter

logger = logging.getLogger(__name__)

#: Role in ``user_agent_settings`` — everyone sets Poradca's model and effort for themselves (Nastavenia → Agenti).
PORADCA_ROLE = "poradca"
DEFAULT_MODEL = "opus"
DEFAULT_EFFORT = "high"
#: Key in ``system_settings``: how many questions may run at once in the whole cockpit.
MAX_CONCURRENT_KEY = "poradca_max_concurrent"

_CHARTER = Path(__file__).resolve().parents[3] / "templates" / "poradca-charter.md"
_STREAM_LINE_LIMIT = 16 * 1024 * 1024


class PoradcaBusy(Exception):
    """A question is already running in this conversation — the next one waits until it finishes or is stopped."""


class ConversationGone(LookupError):
    """The conversation was deleted in the meantime — the question no longer belongs in it."""


#: Title of a deleted conversation. The original title is the first question, so it goes away with the text.
DELETED_TITLE = "Vymazaný rozhovor"


# ── progress for the browser ──────────────────────────────────────────────────


class _Hub:
    """Subscribers to the progress, per conversation. An event is a small dict; tool content is never in it."""

    def __init__(self) -> None:
        self._queues: dict[UUID, set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, conversation_id: UUID) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._queues[conversation_id].add(queue)
        return queue

    def unsubscribe(self, conversation_id: UUID, queue: asyncio.Queue) -> None:
        self._queues[conversation_id].discard(queue)
        if not self._queues[conversation_id]:
            self._queues.pop(conversation_id, None)

    def publish(self, conversation_id: UUID, event: dict) -> None:
        for queue in list(self._queues.get(conversation_id, ())):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass  # a slow subscriber must not delay the question; after a refresh it reads the state from the API


hub = _Hub()


# ── concurrency limit ─────────────────────────────────────────────────────────


class _Slots:
    """Counter of running questions with a limit read on every entry (a change in Nastavenia applies immediately)."""

    def __init__(self) -> None:
        self._active = 0
        self._cond: Optional[asyncio.Condition] = None

    def _condition(self) -> asyncio.Condition:
        if self._cond is None:
            self._cond = asyncio.Condition()
        return self._cond

    @property
    def active(self) -> int:
        return self._active

    async def acquire(self, limit: int) -> None:
        cond = self._condition()
        async with cond:
            await cond.wait_for(lambda: self._active < max(1, limit))
            self._active += 1

    async def release(self) -> None:
        cond = self._condition()
        async with cond:
            self._active = max(0, self._active - 1)
            cond.notify_all()


_slots = _Slots()


@dataclass
class _Running:
    conversation_id: UUID
    container: Optional[str] = None
    process: Any = None
    stop_requested: bool = False
    task: Optional[asyncio.Task] = None
    steps: list[dict] = field(default_factory=list)
    #: Secrets filter of this question — every step and log passes through it (finding of the 05.10.2026 review: steps
    #: of built-in tools and Claude's error output previously went out unfiltered).
    secret_filter: SecretFilter = field(default_factory=SecretFilter)


#: Running answers by message id — for stopping.
_running: dict[UUID, _Running] = {}


def max_concurrent(db: Session) -> int:
    return system_setting.get_int(db, MAX_CONCURRENT_KEY)


# ── public entry points ───────────────────────────────────────────────────────


def ask(
    db: Session,
    conversation: PoradcaConversation,
    question: str,
    user: User,
    images: Sequence[attachments.Incoming] = (),
) -> tuple[PoradcaMessage, PoradcaMessage]:
    """Saves the question and starts the answer in the background. The caller has verified access to the conversation.

    DEV-52: ``images`` sú snímky, ktoré Manažér priložil (už prijaté :func:`attachments.accept`). Uložia sa k
    rozhovoru pred zápisom otázky; keď sa otázka nezapíše, zmažú sa — nikdy neostane snímka bez otázky.

    Raises:
        PoradcaBusy: an answer is already running in this conversation.
        ConversationGone: someone deleted the conversation in the meantime.
    """
    _lock_live(db, conversation.id)
    running = db.execute(
        select(PoradcaMessage.id).where(
            PoradcaMessage.conversation_id == conversation.id, PoradcaMessage.status == RUNNING
        )
    ).first()
    if running is not None:
        raise PoradcaBusy("V tomto rozhovore už Poradca odpovedá — počkaj, alebo ho zastav.")
    first = (
        db.execute(select(PoradcaMessage.id).where(PoradcaMessage.conversation_id == conversation.id)).first() is None
    )
    stored = attachments.store(conversation.id, list(images))
    human = PoradcaMessage(
        conversation_id=conversation.id,
        version_id=conversation.version_id,
        author=AUTHOR_HUMAN,
        content=question,
        attachments=stored,
        status=DONE,
    )
    answer = PoradcaMessage(
        conversation_id=conversation.id,
        version_id=conversation.version_id,
        author=AUTHOR_PORADCA,
        content="",
        status=RUNNING,
    )
    db.add_all([human, answer])
    conversation.updated_at = datetime.now(timezone.utc)
    try:
        db.commit()
    except Exception:
        db.rollback()
        attachments.discard(conversation.id, stored)
        raise
    db.refresh(human)
    db.refresh(answer)
    entry = _Running(conversation_id=conversation.id)
    _running[answer.id] = entry
    entry.task = asyncio.get_running_loop().create_task(
        _run(answer.id, conversation.id, question + attachments.prompt_note(stored), user.id, first),
        name=f"poradca-{answer.id}",
    )
    return human, answer


async def stop(message_id: UUID) -> bool:
    """Stops a running answer. ``False`` when it is no longer running (it finished, or a restart interrupted it)."""
    entry = _running.get(message_id)
    if entry is None:
        return False
    entry.stop_requested = True
    await _kill(entry)
    if entry.task is not None and entry.container is None:
        entry.task.cancel()  # still waiting in the queue — the container does not exist
    return True


def fail_orphans(db: Session) -> int:
    """At backend start: nobody will finish ``running`` answers from the last run — they are closed with a reason."""
    result = db.execute(
        update(PoradcaMessage)
        .where(PoradcaMessage.status == RUNNING)
        .values(
            status=FAILED,
            error="Odpoveď prerušil reštart kokpitu. Opýtaj sa znova.",
            finished_at=datetime.now(timezone.utc),
        )
    )
    db.commit()
    return result.rowcount or 0


# ── beh ───────────────────────────────────────────────────────────────────────


async def _kill(entry: _Running) -> None:
    proc = entry.process
    if proc is not None and proc.returncode is None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            try:
                proc.kill()
            except ProcessLookupError:
                pass
    if entry.container:
        await build_sandbox.reap_container(entry.container)


def _charter_text() -> str:
    return _CHARTER.read_text(encoding="utf-8")


def _charter_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _model_and_effort(db: Session, user_id: UUID) -> tuple[str, str]:
    row = db.execute(
        select(UserAgentSettings.model, UserAgentSettings.effort).where(
            UserAgentSettings.user_id == user_id, UserAgentSettings.agent_role == PORADCA_ROLE
        )
    ).first()
    model = (row.model if row is not None else None) or DEFAULT_MODEL
    effort = (row.effort if row is not None else None) or DEFAULT_EFFORT
    return model, effort


def scope_line(db: Session, project: Project, version_id: Optional[UUID]) -> str:
    """What we are talking about — the context line Poradca receives with every question."""
    if version_id is None:
        return f"Projekt {project.name} ({project.slug}) — otázka sa týka celého projektu."
    version = db.get(Version, version_id)
    if version is None:
        return f"Projekt {project.name} ({project.slug}) — zvolená verzia už neexistuje; ber celý projekt."
    state = db.execute(select(PipelineState).where(PipelineState.version_id == version_id)).scalar_one_or_none()
    stage = f", fáza {state.current_stage}, stav {state.status}" if state is not None else ", stavba nezačala"
    return f"Projekt {project.name} ({project.slug}), verzia {version.version_number}{stage}."


#: DEV-30: how a changed charter is handed to a conversation that started under an older one.
_CHARTER_REFRESH = (
    "[Pravidlá Poradcu sa od začiatku tohto rozhovoru zmenili. Odteraz platia tieto aktuálne pravidlá — "
    "nahrádzajú tie zo začiatku rozhovoru:]\n{charter}\n[Koniec aktuálnych pravidiel]\n\n"
)


def _prompt(scope: str, asker: str, question: str, charter_refresh: Optional[str] = None) -> str:
    refresh = _CHARTER_REFRESH.format(charter=charter_refresh) if charter_refresh else ""
    return f"{refresh}[Kontext z kokpitu]\n{scope}\nPýta sa: {asker}.\n\n{question}"


def _builtin_target(name: str, args: dict, project_dir: str) -> str:
    def rel(path: object) -> str:
        text = str(path or "")
        return os.path.relpath(text, project_dir) if text.startswith(project_dir) else text

    if name == "Read":
        path = str(args.get("file_path") or "")
        if path.startswith(sandbox.CONTAINER_ATTACHMENTS_DIR + "/"):
            return "priložená snímka obrazovky"  # DEV-52 — the Manažér's own image, not a file of the project
        return rel(path)
    if name == "Grep":
        where = rel(args.get("path")) if args.get("path") else "celý projekt"
        return f"„{args.get('pattern', '')}“ v {where}"
    if name == "Glob":
        return str(args.get("pattern", ""))
    return ""


async def _record_step(entry: _Running, message_id: UUID, tool: str, target: str) -> None:
    step = {"tool": tool, "target": entry.secret_filter(target)[:300]}
    entry.steps.append(step)
    hub.publish(entry.conversation_id, {"type": "step", "message_id": str(message_id), **step})
    with SessionLocal() as db:
        db.execute(update(PoradcaMessage).where(PoradcaMessage.id == message_id).values(steps=list(entry.steps)))
        db.commit()


async def _run(message_id: UUID, conversation_id: UUID, question: str, user_id: UUID, first: bool) -> None:
    entry = _running[message_id]
    started = time.monotonic()
    status, content, error, usage = FAILED, "", None, None
    acquired = False
    transcript, before, launched_at = None, None, None
    secret_filter = SecretFilter()
    charter = _charter_text()
    charter_sha = _charter_sha(charter)
    charter_refresh = False
    try:
        with SessionLocal() as db:
            conversation = db.get(PoradcaConversation, conversation_id)
            project = db.get(Project, conversation.project_id)
            user = db.get(User, user_id)
            limit = max_concurrent(db)
            scope = scope_line(db, project, conversation.version_id)
            model, effort = _model_and_effort(db, user_id)
            secret_filter = SecretFilter(known_secret_values(db, project))
            entry.secret_filter = secret_filter
            asker = " ".join(p for p in (user.first_name, user.last_name) if p) or user.username
            slug, session_id = project.slug, conversation.claude_session_id
            # DEV-30: Claude Code ignores a new charter on --resume — a changed one goes into the question itself.
            charter_refresh = not first and conversation.charter_sha != charter_sha
            from backend.services.poradca.tools import build_tools

            tools = build_tools(project_id=project.id, version_id=conversation.version_id, user_id=user_id)

        if _slots.active >= max(1, limit):
            hub.publish(
                conversation_id,
                {"type": "queued", "message_id": str(message_id), "limit": limit},
            )
            await _record_step(entry, message_id, "rad", f"čakám — naraz bežia najviac {limit} otázky")
        await _slots.acquire(limit)
        acquired = True
        if entry.stop_requested:
            raise asyncio.CancelledError

        token = build_sandbox.turn_token()
        _sessions, run_path = await asyncio.to_thread(sandbox.prepare, conversation_id, token)
        # ICCINT-168: what an answer cost is read from the session record — the sum before the answer and after it (also
        # someone stops it or it times out). The run result carries the sum for the whole conversation, not the answer.
        transcript = usage_ledger.build_transcript(sandbox.session_dir(conversation_id), session_id)
        before = await asyncio.to_thread(usage_ledger.last_cost_state, transcript)
        launched_at = datetime.now(timezone.utc)
        container_dir, host_dir = sandbox.project_dirs(slug)
        overlays = await asyncio.to_thread(sandbox.overlay_paths, host_dir)

        async def on_step(tool: str, target: str) -> None:
            await _record_step(entry, message_id, tool, target)

        server = McpServer(str(run_path / sandbox.SOCKET_NAME), tools, secret_filter, on_step)
        await server.start()
        network = sandbox.network_name(slug, token)
        try:
            await build_db.create_fenced_network(network, f"poradca-{slug}", token)
            argv = sandbox.run_argv(
                project_slug=slug,
                conversation_id=conversation_id,
                token=token,
                network=network,
                call=sandbox.ClaudeCall(
                    prompt=_prompt(scope, asker, question, charter if charter_refresh else None),
                    claude_session_id=session_id,
                    charter_text=charter if first else None,
                    model=model,
                    effort=effort,
                    # DEV-52: images of any question of this conversation stay readable for the follow-ups too.
                    attachments=await asyncio.to_thread(sandbox.attachments_dir(conversation_id).is_dir),
                ),
                overlays=overlays,
            )
            entry.container = sandbox.container_name(slug, token)
            content, usage = await asyncio.wait_for(
                _stream(argv, entry, message_id, container_dir), timeout=settings.poradca_question_timeout
            )
            status = DONE
        finally:
            await server.close()
            if entry.container:
                await build_sandbox.reap_container(entry.container)
            await build_db.remove_network(network)
            shutil.rmtree(run_path, ignore_errors=True)
    except asyncio.TimeoutError:
        await _kill(entry)
        status = FAILED
        error = f"Odpoveď prekročila strop {settings.poradca_question_timeout // 60} minút a bola zastavená."
    except asyncio.CancelledError:
        status = STOPPED
        error = None
    except sandbox.PoradcaUnavailable as exc:
        logger.error("poradca: sandbox unavailable: %s", exc)
        status, error = FAILED, f"Poradcu sa nepodarilo spustiť: {exc}"
    except _QuestionFailed as exc:
        status, error = (STOPPED, None) if entry.stop_requested else (FAILED, str(exc))
    except Exception:  # noqa: BLE001 — the question must not stay hanging
        logger.exception("poradca: question %s failed", message_id)
        status, error = FAILED, "Poradca zlyhal na strane kokpitu. Podrobnosti sú v logu backendu."
    finally:
        if acquired:
            await _slots.release()
        if entry.stop_requested and status != DONE:
            status = STOPPED
        if transcript is not None and launched_at is not None:
            parts = await asyncio.to_thread(usage_ledger.settle, transcript, before, launched_at)
            if parts:
                usage = _usage_payload(UsageMetadata.from_parts(parts))
        content = secret_filter(content or "")
        error = secret_filter(error) if error else None
        with SessionLocal() as db:
            db.execute(
                update(PoradcaMessage)
                .where(PoradcaMessage.id == message_id)
                .values(
                    status=status,
                    content=content,
                    error=error,
                    usage=usage,
                    steps=list(entry.steps),
                    duration_seconds=round(time.monotonic() - started, 1),
                    finished_at=datetime.now(timezone.utc),
                )
            )
            if status == DONE and (first or charter_refresh):
                # The conversation now carries this charter — only an answered question proves it arrived.
                db.execute(
                    update(PoradcaConversation)
                    .where(PoradcaConversation.id == conversation_id)
                    .values(charter_sha=charter_sha)
                )
            db.commit()
        _running.pop(message_id, None)
        hub.publish(conversation_id, {"type": "finished", "message_id": str(message_id), "status": status})
        if usage and usage.get("parts"):
            # ICCINT-168: the answer may have been the last one missing from the model's price list — add it right away.
            await asyncio.to_thread(_refresh_prices)


def _refresh_prices() -> None:
    from backend.services import model_pricing

    try:
        with SessionLocal() as db:
            model_pricing.refresh(db)
    except Exception:  # noqa: BLE001 — the price list is completed next time, the answer is already written
        logger.warning("poradca: cenník sa po odpovedi nepodarilo obnoviť", exc_info=True)


def _usage_payload(meta: UsageMetadata) -> dict:
    """Usage of an answer in the shape a build turn carries too — Náklady price it by the same path."""
    payload: dict = {"input_tokens": meta.input_tokens, "output_tokens": meta.output_tokens, "model": meta.model}
    if meta.parts:
        payload["parts"] = [p.to_payload() for p in meta.parts]
    return payload


class _QuestionFailed(Exception):
    """Claude Code finished without an answer — a sentence for the human."""


async def _stream(argv: list[str], entry: _Running, message_id: UUID, project_dir: str) -> tuple[str, Optional[dict]]:
    """Starts the container and reads ``stream-json``. Returns ``(answer text, usage)``."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=agent_env(),
            limit=_STREAM_LINE_LIMIT,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        raise sandbox.PoradcaUnavailable(f"docker CLI nie je k dispozícii ({exc})") from exc
    entry.process = proc
    result_text: Optional[str] = None
    usage: Optional[dict] = None
    is_error = False
    assert proc.stdout is not None and proc.stderr is not None
    stderr_task = asyncio.ensure_future(proc.stderr.read())
    async for raw in proc.stdout:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(evt, dict):
            continue
        if evt.get("type") == "assistant":
            for block in (evt.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = str(block.get("name") or "")
                    if name.startswith("mcp__"):
                        continue  # a Poradca tool step is reported by the tools server (from what it actually executed)
                    target = _builtin_target(name, block.get("input") or {}, project_dir)
                    await _record_step(entry, message_id, name, target)
        elif evt.get("type") == "result":
            result_text = evt.get("result")
            is_error = bool(evt.get("is_error"))
            meta = _usage_from(evt)
            if meta is not None:
                usage = _usage_payload(meta)
    await proc.wait()
    stderr_text = (await stderr_task).decode("utf-8", errors="replace").strip()
    if entry.stop_requested:
        raise _QuestionFailed("zastavené")
    if result_text is None or is_error or proc.returncode not in (0, None):
        if build_sandbox.looks_unavailable(stderr_text):
            raise sandbox.PoradcaUnavailable(stderr_text[:300])
        logger.warning(
            "poradca: claude ended without an answer rc=%s stderr=%s",
            proc.returncode,
            entry.secret_filter(stderr_text)[:500],
        )
        detail = (result_text or "").strip()[:300] if is_error else ""
        raise _QuestionFailed("Poradca skončil bez odpovede." + (f" ({detail})" if detail else ""))
    return str(result_text).strip(), usage


def new_conversation(
    db: Session, *, project: Project, author: User, version_id: Optional[UUID], title: str
) -> PoradcaConversation:
    conversation = PoradcaConversation(
        project_id=project.id,
        version_id=version_id,
        author_id=author.id,
        title=title[:200] or "Rozhovor",
        claude_session_id=uuid4(),
    )
    db.add(conversation)
    db.flush()
    return conversation


def edit_conversation(db: Session, conversation: PoradcaConversation, **values: Any) -> None:
    """Changes the title or „o čom sa rozprávame" — the conversation does not move in the list meanwhile.

    The order of conversations is set by the last question (``updated_at`` is set by :func:`ask`), not by an edit. So
    ``updated_at`` explicitly stays as it was — otherwise ``onupdate`` would move it to now.

    Raises:
        ConversationGone: the conversation was deleted in the meantime. The ``deleted_at IS NULL`` condition is in the
            write itself: an edit that waited for the deletion lock is re-evaluated after it and writes nothing —
            the new title would otherwise put text back into a deleted conversation (finding of the 05.10.2026 review).
    """
    result = db.execute(
        update(PoradcaConversation)
        .where(PoradcaConversation.id == conversation.id, PoradcaConversation.deleted_at.is_(None))
        .values(**values, updated_at=PoradcaConversation.updated_at)
    )
    if result.rowcount == 0:
        db.rollback()
        raise ConversationGone("Rozhovor bol vymazaný.")
    db.commit()
    db.refresh(conversation)


async def delete_conversation(db: Session, conversation: PoradcaConversation) -> None:
    """Deletes what was said in the conversation; leaves the usage to Náklady.

    Discards the questions, answers, steps, errors, the title and the whole Claude Code session record on disk — it also
    carries what the tools returned to Poradca. Of the messages only usage and time remain: the project's Náklady sum
    (``metrics._poradca_rows``) and without them they would show less than was actually spent. The conversation gets
    ``deleted_at`` and the interface no longer sees it from then on.

    Order: under the lock the record is moved to the trash (one indivisible step) and the database is changed; if the
    database fails → the record is returned. Trash files are deleted only afterwards, outside the lock and event loop.

    Raises:
        PoradcaBusy: an answer is still running — the container would write into a record being deleted; stop it first.
        ConversationGone: already deleted.
        OSError: the record on disk could not be moved — the database and the record stayed as they were.
    """
    trashed = _wipe(db, conversation)
    hub.publish(conversation.id, {"type": "deleted"})
    if trashed is not None:
        await asyncio.to_thread(sandbox.discard, trashed)


def _wipe(db: Session, conversation: PoradcaConversation) -> Optional[Path]:
    _lock_live(db, conversation.id)
    running = db.execute(
        select(PoradcaMessage.id).where(
            PoradcaMessage.conversation_id == conversation.id, PoradcaMessage.status == RUNNING
        )
    ).first()
    if running is not None:
        db.rollback()
        raise PoradcaBusy("Kým Poradca odpovedá, rozhovor sa nedá vymazať — najprv odpoveď zastav.")
    try:
        trashed = sandbox.move_to_trash(conversation.id)
    except OSError:
        db.rollback()
        raise
    try:
        db.execute(
            update(PoradcaMessage)
            .where(PoradcaMessage.conversation_id == conversation.id)
            .values(content="", steps=[], attachments=[], error=None, captured_backlog_item_id=None)
        )
        db.execute(
            update(PoradcaConversation)
            .where(PoradcaConversation.id == conversation.id)
            .values(
                title=DELETED_TITLE,
                deleted_at=datetime.now(timezone.utc),
                updated_at=PoradcaConversation.updated_at,
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        if trashed is not None:
            try:
                sandbox.restore_from_trash(trashed, conversation.id)
            except OSError:
                logger.exception("poradca: transcript of %s stays in %s — restore failed", conversation.id, trashed)
        raise
    db.refresh(conversation)
    return trashed


def _lock_live(db: Session, conversation_id: UUID) -> None:
    """Locks the conversation row until the end of the transaction; deleted or non-existent → :class:`ConversationGone`.

    Both asking and deleting go through this lock, so they do not cross: a question into a conversation being deleted
    does not get through and the deletion does not start while a question is being saved (it then sees it as running).
    """
    row = db.execute(
        select(PoradcaConversation.deleted_at).where(PoradcaConversation.id == conversation_id).with_for_update()
    ).one_or_none()
    if row is None or row.deleted_at is not None:
        db.rollback()
        raise ConversationGone("Rozhovor bol vymazaný.")
