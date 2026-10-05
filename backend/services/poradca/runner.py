"""Beh jednej otázky Poradcu (ICCINT-167, návrh §3 a §4.1).

Otázka sa uloží hneď; odpoveď vzniká ako ``running`` a dopĺňa sa na pozadí:

  1. čaká na voľné miesto — naraz beží najviac ``poradca_max_concurrent`` otázok v celom kokpite (všetky
     čerpajú z jedného predplatného s limitom ako stavby); kým čaká, človek vidí prečo;
  2. pripraví priečinky, prekrytia tajomstiev a server nástrojov na sockete tejto otázky;
  3. spustí kontajner (:mod:`.sandbox`) na vlastnej oplotenej sieti a číta priebeh ``stream-json``;
     z neho do databázy a prehliadača ide len KROK (nástroj a cieľ), nikdy obsah;
  4. odpoveď prejde filtrom tajomstiev a uloží sa so spotrebou a trvaním.

Zastavenie zabije klienta ``docker run`` aj kontajner. Strop jednej otázky je
``settings.poradca_question_timeout``. Zlyhanie sa vždy zapíše vetou pre človeka — otázka nikdy neostane
visieť v ``running``: pri štarte backendu :func:`fail_orphans` uzavrie tie, ktoré prerušil reštart.
"""

from __future__ import annotations

import asyncio
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
from typing import Any, Optional
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
from backend.services import build_db, build_sandbox, system_setting
from backend.services.claude_agent import _usage_from
from backend.services.poradca import sandbox
from backend.services.poradca.context import known_secret_values
from backend.services.poradca.mcp_server import McpServer
from backend.services.poradca.secrets_filter import SecretFilter

logger = logging.getLogger(__name__)

#: Rola v ``user_agent_settings`` — model a úsilie Poradcu si nastaví každý sám (Nastavenia → Agenti).
PORADCA_ROLE = "poradca"
DEFAULT_MODEL = "opus"
DEFAULT_EFFORT = "high"
#: Kľúč v ``system_settings``: koľko otázok smie bežať naraz v celom kokpite.
MAX_CONCURRENT_KEY = "poradca_max_concurrent"

_CHARTER = Path(__file__).resolve().parents[3] / "templates" / "poradca-charter.md"
_STREAM_LINE_LIMIT = 16 * 1024 * 1024


class PoradcaBusy(Exception):
    """V rozhovore už jedna otázka beží — ďalšia počká, kým dobehne alebo ju človek zastaví."""


# ── priebeh pre prehliadač ────────────────────────────────────────────────────


class _Hub:
    """Odberatelia priebehu po rozhovoroch. Udalosť je malý slovník; obsah nástrojov v nej nikdy nie je."""

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
                pass  # pomalý odberateľ nesmie zdržať otázku; po obnovení si stav prečíta z API


hub = _Hub()


# ── strop súbežnosti ──────────────────────────────────────────────────────────


class _Slots:
    """Počítadlo bežiacich otázok so stropom čítaným pri každom vstupe (zmena v Nastaveniach platí hneď)."""

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


#: Bežiace odpovede podľa id správy — pre zastavenie.
_running: dict[UUID, _Running] = {}


def max_concurrent(db: Session) -> int:
    return system_setting.get_int(db, MAX_CONCURRENT_KEY)


# ── verejné vstupy ────────────────────────────────────────────────────────────


def ask(
    db: Session, conversation: PoradcaConversation, question: str, user: User
) -> tuple[PoradcaMessage, PoradcaMessage]:
    """Uloží otázku a začne odpoveď na pozadí. Volajúci už overil prístup k rozhovoru.

    Raises:
        PoradcaBusy: v tomto rozhovore už odpoveď beží.
    """
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
    human = PoradcaMessage(
        conversation_id=conversation.id,
        version_id=conversation.version_id,
        author=AUTHOR_HUMAN,
        content=question,
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
    db.commit()
    db.refresh(human)
    db.refresh(answer)
    entry = _Running(conversation_id=conversation.id)
    _running[answer.id] = entry
    entry.task = asyncio.get_running_loop().create_task(
        _run(answer.id, conversation.id, question, user.id, first), name=f"poradca-{answer.id}"
    )
    return human, answer


async def stop(message_id: UUID) -> bool:
    """Zastaví bežiacu odpoveď. ``False``, keď už nebeží (dobehla, alebo ju prerušil reštart)."""
    entry = _running.get(message_id)
    if entry is None:
        return False
    entry.stop_requested = True
    await _kill(entry)
    if entry.task is not None and entry.container is None:
        entry.task.cancel()  # ešte čaká v rade — kontajner neexistuje
    return True


def fail_orphans(db: Session) -> int:
    """Pri štarte backendu: odpovede ``running`` z minulého behu už nikto nedokončí — uzavrú sa s dôvodom."""
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
    """O čom sa rozprávame — riadok kontextu, ktorý dostane Poradca pri každej otázke."""
    if version_id is None:
        return f"Projekt {project.name} ({project.slug}) — otázka sa týka celého projektu."
    version = db.get(Version, version_id)
    if version is None:
        return f"Projekt {project.name} ({project.slug}) — zvolená verzia už neexistuje; ber celý projekt."
    state = db.execute(select(PipelineState).where(PipelineState.version_id == version_id)).scalar_one_or_none()
    stage = f", fáza {state.current_stage}, stav {state.status}" if state is not None else ", stavba nezačala"
    return f"Projekt {project.name} ({project.slug}), verzia {version.version_number}{stage}."


def _prompt(scope: str, asker: str, question: str) -> str:
    return f"[Kontext z kokpitu]\n{scope}\nPýta sa: {asker}.\n\n{question}"


def _builtin_target(name: str, args: dict, project_dir: str) -> str:
    def rel(path: object) -> str:
        text = str(path or "")
        return os.path.relpath(text, project_dir) if text.startswith(project_dir) else text

    if name == "Read":
        return rel(args.get("file_path"))
    if name == "Grep":
        where = rel(args.get("path")) if args.get("path") else "celý projekt"
        return f"„{args.get('pattern', '')}“ v {where}"
    if name == "Glob":
        return str(args.get("pattern", ""))
    return ""


async def _record_step(entry: _Running, message_id: UUID, tool: str, target: str) -> None:
    step = {"tool": tool, "target": target}
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
    secret_filter = SecretFilter()
    try:
        with SessionLocal() as db:
            conversation = db.get(PoradcaConversation, conversation_id)
            project = db.get(Project, conversation.project_id)
            user = db.get(User, user_id)
            limit = max_concurrent(db)
            scope = scope_line(db, project, conversation.version_id)
            model, effort = _model_and_effort(db, user_id)
            secret_filter = SecretFilter(known_secret_values(db, project))
            asker = " ".join(p for p in (user.first_name, user.last_name) if p) or user.username
            slug, session_id = project.slug, conversation.claude_session_id
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
                    prompt=_prompt(scope, asker, question),
                    claude_session_id=session_id,
                    charter_text=_charter_text() if first else None,
                    model=model,
                    effort=effort,
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
    except Exception:  # noqa: BLE001 — otázka nesmie ostať visieť
        logger.exception("poradca: question %s failed", message_id)
        status, error = FAILED, "Poradca zlyhal na strane kokpitu. Podrobnosti sú v logu backendu."
    finally:
        if acquired:
            await _slots.release()
        if entry.stop_requested and status != DONE:
            status = STOPPED
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
            db.commit()
        _running.pop(message_id, None)
        hub.publish(conversation_id, {"type": "finished", "message_id": str(message_id), "status": status})


class _QuestionFailed(Exception):
    """Claude Code skončil bez odpovede — veta pre človeka."""


async def _stream(argv: list[str], entry: _Running, message_id: UUID, project_dir: str) -> tuple[str, Optional[dict]]:
    """Spustí kontajner a číta ``stream-json``. Vráti ``(text odpovede, spotreba)``."""
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
                        continue  # krok nástroja Poradcu hlási server nástrojov (z toho, čo naozaj vykonal)
                    target = _builtin_target(name, block.get("input") or {}, project_dir)
                    await _record_step(entry, message_id, name, target)
        elif evt.get("type") == "result":
            result_text = evt.get("result")
            is_error = bool(evt.get("is_error"))
            meta = _usage_from(evt)
            if meta is not None:
                usage = {"input_tokens": meta.input_tokens, "output_tokens": meta.output_tokens, "model": meta.model}
    await proc.wait()
    stderr_text = (await stderr_task).decode("utf-8", errors="replace").strip()
    if entry.stop_requested:
        raise _QuestionFailed("zastavené")
    if result_text is None or is_error or proc.returncode not in (0, None):
        if build_sandbox.looks_unavailable(stderr_text):
            raise sandbox.PoradcaUnavailable(stderr_text[:300])
        logger.warning("poradca: claude ended without an answer rc=%s stderr=%s", proc.returncode, stderr_text[:500])
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
