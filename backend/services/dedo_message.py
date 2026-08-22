"""Dedo → AI Agent messages (ICCINT-12) — the return leg of the escalation channel.

The outbound leg already existed: when the AI Agent hits an error it CANNOT fix because the fix requires a
change to NEX Studio ITSELF, it settles the build ``blocked``/``block_reason='framework_issue'`` and
:mod:`backend.services.dedo_escalation` delivers the agent's message to Dedo (an audit file in the channel
Dedo watches + a Telegram ping). There was no way back: ``pipeline_message`` accepted only ``ai_agent`` /
``auditor`` / ``manazer`` / ``system`` as author, so Dedo's answer had to be retyped by a human into the
Manažér's box — two windows, and an audit trail that claimed the Manažér said something he never said.

This module is that return leg, and it is deliberately ONE function deep:

  * :func:`record_dedo_message` WRITES the message (``author='dedo'``, ``recipient='ai_agent'``,
    ``status='pending'``) through the orchestrator's existing ``_record_message`` helper — the same writer
    every other pipeline message goes through, not a second one.
  * :func:`pending_for_prompt` / :func:`mark_delivered` are the DELIVERY half: the orchestrator folds any
    pending Dedo message into the TOP of the next AI-Agent prompt and marks it delivered once it has been
    handed to the agent. A message the agent never sees would be a failed task, not a half-success, so
    delivery is **durable and at-least-once**: the pending rows live in the database (not in the in-process
    relay queue, which a host-side CLI in a different process could not reach and a restart would lose),
    and they stay ``pending`` until a turn has actually carried them. "Actually carried" means an ENVELOPE
    came back from the headless CLI — a crash / timeout returns a ``ParseFailure`` with
    ``envelope_loss_kind`` set and does NOT consume the message, because on that path the prompt may never
    have reached the model at all (and the build round's crash auto-retry would re-run without the block).

``record_dedo_message`` is the single write path ON PURPOSE. The thin host CLI
(``python -m backend.cli.dedo_message``) calls it today; the authenticated HTTP endpoint (ICCINT-14, once
Dedo has his own machine identity — charter §4.5) must call the SAME function rather than grow a parallel
one, so there is one way for Dedo to speak, not two that drift.

Out of scope here (by decision): this does NOT unblock a ``framework_issue`` build (ICCINT-13) and does NOT
expose any HTTP surface (ICCINT-14).

RELEASE CONDITION — ICCINT-12 MUST SHIP TOGETHER WITH ICCINT-13. On its own this delivers nothing. A build
blocked on ``framework_issue`` offers the Manažér exactly ONE action —
:func:`~backend.services.orchestrator.determine_available_actions` returns ``{"nahlasit_znova"}`` — and that
action only re-sends the escalation to Dedo; it dispatches no agent turn. Nothing else on that screen starts
one (the FE renders buttons strictly from ``board.available_actions``). So the write path below works, the
message is visible in the thread, and it stays ``pending`` forever, because no turn ever runs to carry it.
The trigger is ICCINT-13's job. Pinned by ``TestTheMissingTrigger`` in ``tests/test_dedo_message.py``: that
test goes RED the moment the action set changes, which is the signal to revisit this note.
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version

logger = logging.getLogger(__name__)

#: Machine participant token for the NEX Studio technical team (``PARTICIPANT_VALUES``, migration 088).
DEDO_PARTICIPANT = "dedo"

#: Who a Dedo message is addressed to. Dedo answers the AI Agent's escalation — never the Manažér, never
#: the Auditor (the Auditor verifies the project, it is not his build to unstick).
DEDO_RECIPIENT = "ai_agent"

#: ``kind`` of a Dedo message. Deliberately an EXISTING kind (``answer``) — Dedo's message is by
#: construction a reply to the agent's escalation, so no new ``MESSAGE_KIND_VALUES`` member and no CHECK
#: widening on ``kind`` is needed. The ``author`` column already says who spoke.
DEDO_MESSAGE_KIND = "answer"

#: Heading the folded prompt block carries. It names Dedo explicitly so the agent cannot mistake the
#: instruction for the Manažér's (a Manažér cannot answer a framework issue — that is the whole point).
_PROMPT_HEADING = "## Odpoveď od Deda (technický tím NEX Studia)"


class DedoMessageError(Exception):
    """Raised when a Dedo message cannot be recorded (unknown version, empty text, no build to answer)."""


def record_dedo_message(db: Session, *, version_id: uuid.UUID, content: str) -> PipelineMessage:
    """Record a message from Dedo to the AI Agent of ``version_id``; return the persisted row.

    The SINGLE write path for Dedo's voice (the CLI today, the ICCINT-14 endpoint tomorrow). The row is
    written ``status='pending'`` — :func:`pending_for_prompt` is what turns it into something the agent
    actually reads, and it stays pending until a turn has carried it, so a crash between the write and the
    next dispatch re-delivers instead of swallowing.

    ``stage`` is stamped from the build's CURRENT phase so the message sits in the thread where the
    escalation happened. The caller commits (this only flushes) — same contract as every other writer.
    """
    text = (content or "").strip()
    if not text:
        raise DedoMessageError("Dedo message is empty — nothing to deliver to the agent")

    state = db.execute(select(PipelineState).where(PipelineState.version_id == version_id)).scalar_one_or_none()
    if state is None:
        raise DedoMessageError(f"No pipeline started for version {version_id} — there is no agent to answer")

    # Local import: :mod:`backend.services.orchestrator` imports THIS module (it folds pending messages into
    # the prompt), so importing it at module scope would be a cycle. Reusing ``_record_message`` rather than
    # constructing a PipelineMessage here is the point — one writer, one place where the columns are set.
    from backend.services.orchestrator import _record_message

    msg = _record_message(
        db,
        version_id=version_id,
        stage=state.current_stage,
        author=DEDO_PARTICIPANT,
        recipient=DEDO_RECIPIENT,
        kind=DEDO_MESSAGE_KIND,
        content=text,
        status="pending",
        payload={"phase": state.current_stage, "dedo_reply": True},
    )
    logger.info("Dedo message recorded for version %s (message %s)", version_id, msg.id)
    return msg


def pending_messages(db: Session, version_id: uuid.UUID) -> list[PipelineMessage]:
    """Dedo messages for ``version_id`` that no agent turn has carried yet, oldest first."""
    return list(
        db.execute(
            select(PipelineMessage)
            .where(
                PipelineMessage.version_id == version_id,
                PipelineMessage.author == DEDO_PARTICIPANT,
                PipelineMessage.status == "pending",
            )
            .order_by(PipelineMessage.seq.asc())
        )
        .scalars()
        .all()
    )


def pending_for_prompt(db: Session, version_id: uuid.UUID) -> tuple[Optional[str], list[PipelineMessage]]:
    """``(prompt_block, rows)`` for the pending Dedo messages of ``version_id``; ``(None, [])`` if none.

    ``prompt_block`` is what the orchestrator PREPENDS to the next AI-Agent prompt — prepended, not
    substituted, so the phase's own brief is never clobbered by an incoming answer. ``rows`` is handed back
    so the caller can :func:`mark_delivered` them AFTER the turn has actually been dispatched (see the
    at-least-once note in the module docstring).
    """
    rows = pending_messages(db, version_id)
    if not rows:
        return None, []
    lines = [
        _PROMPT_HEADING,
        "",
        "Toto je odpoveď od Deda — vývojára NEX Studia — na tvoju eskaláciu (`framework_issue`). Nie je to "
        "správa od Manažéra: Manažér chybu v samotnom NEX Studiu opraviť nevie, preto si ju eskaloval. "
        "Riaď sa pokynom nižšie a pokračuj v práci.",
        "",
    ]
    for i, row in enumerate(rows, start=1):
        prefix = f"{i}. " if len(rows) > 1 else ""
        lines.append(f"{prefix}{(row.content or '').strip()}")
        lines.append("")
    return "\n".join(lines).rstrip(), rows


def mark_delivered(db: Session, messages: list[PipelineMessage]) -> None:
    """Flip carried Dedo messages ``pending`` → ``delivered``.

    Called ONLY once the turn has come back with an envelope (the agent provably read the prompt). A turn
    that lost its envelope — crash or timeout — must leave the rows ``pending``; see the receipt in
    :func:`backend.services.orchestrator.invoke_agent_with_parse_retry`.
    """
    for msg in messages:
        msg.status = "delivered"
    if messages:
        db.flush()


def version_awaiting_dedo(db: Session, project_slug: str) -> Version:
    """The project's ONE build currently blocked on ``framework_issue`` — what Dedo is answering.

    Shared resolution so the CLI and the future ICCINT-14 endpoint agree on what "reply to project X" means
    instead of each guessing. Raises when the answer is not unambiguous:

    * unknown slug → there is nothing to answer;
    * no blocked build → Dedo would be answering a build that never asked (a typo'd slug looks exactly like
      this, and silently writing into a healthy build is worse than refusing);
    * more than one → the caller must name the version explicitly.
    """
    project_id = db.execute(select(Project.id).where(Project.slug == project_slug)).scalar_one_or_none()
    if project_id is None:
        raise DedoMessageError(f"Unknown project slug: {project_slug}")
    versions = list(
        db.execute(
            select(Version)
            .join(PipelineState, PipelineState.version_id == Version.id)
            .where(
                Version.project_id == project_id,
                PipelineState.status == "blocked",
                PipelineState.block_reason == "framework_issue",
            )
            .order_by(Version.created_at.asc())
        )
        .scalars()
        .all()
    )
    if not versions:
        raise DedoMessageError(
            f"Project {project_slug} has no build blocked on framework_issue — "
            "pass --version-id explicitly if you really mean to write into another build"
        )
    if len(versions) > 1:
        numbers = ", ".join(v.version_number for v in versions)
        raise DedoMessageError(
            f"Project {project_slug} has several builds waiting on Dedo ({numbers}) — pass --version-id"
        )
    return versions[0]
