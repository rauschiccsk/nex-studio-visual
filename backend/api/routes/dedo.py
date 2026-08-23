"""Dedo's door (ICCINT-14) — the technical team reaches a stuck build over the network, as himself.

ICCINT-12 gave Dedo a voice and ICCINT-13 a way to release a build stuck on a NEX Studio bug, but both
lived exclusively in host-side commands (``backend/cli/*``): to answer an escalation Dedo had to be sitting
at the server. This router is the same two capabilities plus the reads they need, reachable over HTTP.

WHAT MAKES IT SAFE IS THE SHAPE, NOT THE CARE OF THE CALLER. Charter §4.5 (Director, 2026-08-22) allows
Dedo his own machine identity and draws the line by hand: read any build, write ONE kind of message,
change state ONE way. Every other thing — approving a gate, starting or stopping a build, answering for the
Manažér, deciding anything, touching ``/api/v1/credentials/*`` — stays forbidden. The charter is explicit
that this must be enforced by the TOKEN and not by the caller's discipline, so:

* the router hangs off :func:`~backend.core.dedo_auth.require_dedo_identity` (a router-level dependency, so
  it cannot be forgotten on a route added later) and off NOTHING else — no ``get_current_user``, no role;
* Dedo's abilities ARE the endpoints below. "He cannot approve a gate" is not a rule enforced somewhere;
  there is no endpoint to call. Adding one is the only way to widen him, and
  ``tests/test_dedo_api.py::TestNothingBeyondTheCharter`` fails the moment anyone does — which is exactly
  what happened when ICCINT-24 added the sixth (``POST /builds/{id}/proposals``).

  THAT SIXTH ENDPOINT IS AWAITING THE DIRECTOR'S DECISION AT THE TIME OF WRITING (23.08.2026). It was
  added by the ICCINT-24 implementation and the enumeration test was widened for it deliberately, by the
  implementer, not by anyone with the authority to grant a machine identity a new capability; the earlier
  version of this note asserted a Director decision that does not exist in the repository or in the
  knowledge base, and asserting authority one cannot produce is worse than the widening itself. The
  argument FOR it is that it grants no new reach — it delivers nothing to anybody, it only puts a finding
  on the MANAŽÉR's desk (see :func:`propose_build_message`) — but the argument is the implementer's, and
  the decision is the Director's. ``tests/test_dedo_api.py`` states the same, in the one place that fails
  if the door changes shape again.

EVERY WRITE GOES THROUGH AN EXISTING SERVICE — :func:`~backend.services.dedo_message.record_dedo_message`,
:func:`~backend.services.dedo_message.record_dedo_proposal` and
:func:`~backend.services.dedo_unblock.unblock_framework_issue`, the very functions the CLIs call. One way
for Dedo to speak, one to propose, one to release a build — never a second writer per transport that drifts
from the first. The CLIs stay exactly as they were: same services, different transport.

BOTH WRITES THAT REACH AN AGENT ARE CONFINED TO A BUILD THAT ASKED. Reading is the wide half of the grant
and covers every build; writing is the narrow half and reaches only a build stuck on a NEX Studio bug (or
one Dedo has just released). The unblock has always refused the rest inside its service; the message does
too, at :func:`_require_a_build_that_asked_for_dedo` — because a Dedo message is not a comment, it is the
directive that opens the agent's next prompt, and a build that never escalated must not receive one. The
ICCINT-24 PROPOSAL is the deliberate exception and proves the rule from the other side: it may name any
build precisely BECAUSE it reaches no agent at all until a human presses a button.

EVERY WRITE SAYS IT CAME OVER THE WIRE. Both writes stamp ``dedo_transport='api'`` into the recorded
message payload, so the thread distinguishes "Dedo typed this on the host" from "Dedo sent this over the
network" months later, when it matters and nobody remembers.

There is no frontend for THIS DOOR, on purpose: the Manažér has no use for Dedo's door, and a screen for it
would only invite someone to hand a human the machine's token. What ICCINT-24 gives the cockpit is a screen
for the PROPOSAL — on the Manažér's own routes, with his own login (``/pipeline/{id}/dedo-proposal/…``),
reading the row this door wrote. Dedo writes the finding; the Manažér, and only the Manažér, sends it.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.dedo_auth import require_dedo_identity
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.db.session import get_db
from backend.schemas.dedo import DedoBuildRead, DedoMessageCreate, DedoProposalCreate, DedoUnblockRequest
from backend.schemas.pipeline import PipelineMessageRead
from backend.services import dedo_message as dedo_message_service
from backend.services import dedo_unblock as dedo_unblock_service
from backend.services.dedo_message import DedoMessageError

logger = logging.getLogger(__name__)

#: Router-level dependency: EVERY route here is Dedo's machine identity or nothing. Declared once, on the
#: router, so a route added later cannot ship ungated by omission.
router = APIRouter(tags=["Dedo"], dependencies=[Depends(require_dedo_identity)])

#: Stamped into every message this router writes, so the audit trail records the transport, not just the
#: author. Absence of the marker means the host-side CLI wrote it.
API_TRANSPORT = {"dedo_transport": "api"}

_DEFAULT_MESSAGE_LIMIT = 200


def _read(version: Version, project: Project, state: PipelineState) -> DedoBuildRead:
    return DedoBuildRead(
        version_id=version.id,
        version_number=version.version_number,
        project_id=project.id,
        project_slug=project.slug,
        project_name=project.name,
        current_stage=state.current_stage,
        current_actor=state.current_actor,
        status=state.status,
        block_reason=state.block_reason,
        next_action=state.next_action,
        resume_after_framework_fix=state.resume_after_framework_fix,
        waiting_since=state.awaiting_director_since,
        updated_at=state.updated_at,
    )


def _load(db: Session, version_id: uuid.UUID) -> tuple[Version, Project, PipelineState]:
    """The build behind ``version_id``, or HTTP 404 saying which part is missing.

    A version with no pipeline row is a 404 on purpose and not an empty 200: Dedo asking about a build that
    was never started is a mistake worth surfacing (a copy-pasted id from the wrong project looks exactly
    like it), and a hollow answer would read as "nothing wrong here".
    """
    row = db.execute(
        select(Version, Project, PipelineState)
        .join(Project, Project.id == Version.project_id)
        .outerjoin(PipelineState, PipelineState.version_id == Version.id)
        .where(Version.id == version_id)
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Neznáma verzia {version_id}.")
    version, project, state = row
    if state is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Verzia {version_id} ({project.slug}) nemá spustenú stavbu — niet čo čítať ani odblokovať.",
        )
    return version, project, state


def _require_a_build_that_asked_for_dedo(version_id: uuid.UUID, state: PipelineState) -> None:
    """Refuse to write into a build that never escalated — HTTP 409, before anything is recorded.

    A message on this door is NOT a note in a thread. ``dedo_message.pending_for_prompt`` folds it into the
    TOP of the AI Agent's next prompt under "Odpoveď od Deda" with "Riaď sa pokynom nižšie a pokračuj v
    práci" — a top-priority directive the agent has no reason to question. Without this guard, a caller
    holding the machine token could steer the agent of ANY running build in ANY project, none of which ever
    asked Dedo anything: charter §4.5 grants him a reply to an escalation, not a channel into every
    customer's build.

    The host CLI has had this rule from the start (:func:`~backend.services.dedo_message.version_awaiting_dedo`
    refuses a project with no blocked build, saying a typo'd slug looks exactly like one). This is the same
    rule on the HTTP path, applied where the version is named rather than resolved.

    WHAT IS ALLOWED is the escalation itself (``blocked``/``framework_issue``) and the short window right
    after Dedo released it (``resume_after_framework_fix``, i.e. waiting for the Manažér's "Pokračovať"):
    correcting or completing one's own answer before the turn runs is part of answering, and the message
    still reaches the agent of the build that asked. Anything else is refused. Widening this is a Director
    decision, not a default.
    """
    if state.status == "blocked" and state.block_reason == "framework_issue":
        return
    if state.resume_after_framework_fix:
        return
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            f"Verzia {version_id} nečaká na Deda (stav={state.status!r}, dôvod bloku={state.block_reason!r}) — "
            "písať do stavby, ktorá o nič nežiadala, by bol pokyn pre cudzieho AI Agenta."
        ),
    )


@router.get("/waiting", response_model=list[DedoBuildRead])
def list_waiting_builds(db: Session = Depends(get_db)) -> list[DedoBuildRead]:
    """Every build, in every project, currently stuck on a NEX Studio bug — Dedo's queue.

    Cross-project by design: an escalation is a NEX Studio problem, and which customer's build tripped over
    it is incidental. Oldest wait first, because that is the one that has been costing the longest.
    """
    rows = db.execute(
        select(Version, Project, PipelineState)
        .join(Project, Project.id == Version.project_id)
        .join(PipelineState, PipelineState.version_id == Version.id)
        .where(
            PipelineState.status == "blocked",
            PipelineState.block_reason == "framework_issue",
        )
        .order_by(PipelineState.updated_at.asc())
    ).all()
    return [_read(version, project, state) for version, project, state in rows]


@router.get("/builds/{version_id}", response_model=DedoBuildRead)
def get_build(version_id: uuid.UUID, db: Session = Depends(get_db)) -> DedoBuildRead:
    """Where one build stands: phase, status, and why it is blocked.

    ANY build, not only a blocked one — Dedo diagnoses NEX Studio, and "the build that did NOT get stuck"
    is half of every such diagnosis. Reading is the wide half of the charter's grant; writing is the narrow
    one.
    """
    return _read(*_load(db, version_id))


@router.get("/builds/{version_id}/messages", response_model=list[PipelineMessageRead])
def list_build_messages(
    version_id: uuid.UUID,
    limit: int = Query(_DEFAULT_MESSAGE_LIMIT, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[PipelineMessage]:
    """The build's message log — the escalation in the agent's own words, and everything around it.

    Returns the LAST ``limit`` entries in chronological order: a long build's thread runs to thousands of
    rows and the end is what an escalation is about.
    """
    _load(db, version_id)  # 404 before an empty list can be mistaken for "no messages"
    rows = (
        db.execute(
            select(PipelineMessage)
            .where(PipelineMessage.version_id == version_id)
            .order_by(PipelineMessage.seq.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return list(reversed(rows))


@router.post("/builds/{version_id}/messages", response_model=PipelineMessageRead, status_code=status.HTTP_201_CREATED)
def post_build_message(
    version_id: uuid.UUID,
    body: DedoMessageCreate,
    db: Session = Depends(get_db),
) -> PipelineMessage:
    """Write one message into the build's thread AS DEDO — the single thing Dedo may say.

    Straight through :func:`~backend.services.dedo_message.record_dedo_message`, the same writer the host
    CLI uses. The message lands ``pending`` and reaches the agent on its next turn; on a build blocked on a
    NEX Studio bug that turn only comes after the unblock below, which is the honest behaviour and not a
    bug — nothing runs while the framework is broken.

    ONLY into a build that escalated: see :func:`_require_a_build_that_asked_for_dedo`.
    """
    _, _, state = _load(db, version_id)
    _require_a_build_that_asked_for_dedo(version_id, state)
    try:
        msg = dedo_message_service.record_dedo_message(
            db,
            version_id=version_id,
            content=body.content,
            payload_extra=dict(API_TRANSPORT),
        )
    except DedoMessageError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    db.commit()
    logger.info("Dedo (API) recorded a message for version %s (message %s)", version_id, msg.id)
    return msg


@router.post(
    "/builds/{version_id}/proposals",
    response_model=PipelineMessageRead,
    status_code=status.HTTP_201_CREATED,
)
def propose_build_message(
    version_id: uuid.UUID,
    body: DedoProposalCreate,
    db: Session = Depends(get_db),
) -> PipelineMessage:
    """Propose a finding for the Manažér to send to the agent — the ordinary case (ICCINT-24).

    THE SIXTH ENDPOINT — MOUNTED, AND AWAITING THE DIRECTOR'S DECISION (23.08.2026; see the module
    docstring). What follows is the argument for keeping it, made by the implementer; it is not a
    ratification, and nobody should read it as one.

    The reason it may exist is the reason it is not a widening: unlike
    :func:`post_build_message` above, this one delivers NOTHING. The row lands ``status='proposed'``,
    addressed to the Manažér; no prompt ever carries it (delivery keys on ``pending``), and the agent is
    not told it exists. It appears in the cockpit as an editable proposal behind one button, and the text
    reaches the agent only if the Manažér presses it — as HIS message, through the ordinary ``uprav`` /
    ``answer`` / ``ask`` action named in ``proposed_action``, with every guard that action already has.

    THAT is why it takes ANY build while the message endpoint takes only a build that escalated. The
    ICCINT-14 boundary is untouched: what it forbids is Dedo putting a directive at the top of a stranger's
    agent prompt, and a proposal cannot do that from any angle. What it never forbade — and what the product
    was missing — is Dedo telling the MANAŽÉR what he found, without the Director acting as a typist.

    Refuses an unknown ``proposed_action`` and a version with no build (409): a proposal about a build that
    was never started has no recipient and no meaning.
    """
    _load(db, version_id)  # 404 for an unknown version / a version with no build
    try:
        msg = dedo_message_service.record_dedo_proposal(
            db,
            version_id=version_id,
            content=body.content,
            proposed_action=body.proposed_action,
            payload_extra=dict(API_TRANSPORT),
        )
    except DedoMessageError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    db.commit()
    logger.info("Dedo (API) proposed a message for version %s (message %s)", version_id, msg.id)
    return msg


@router.post("/builds/{version_id}/unblock", response_model=DedoBuildRead)
def unblock_build(
    version_id: uuid.UUID,
    body: DedoUnblockRequest,
    db: Session = Depends(get_db),
) -> DedoBuildRead:
    """Release a build stuck on ``framework_issue`` — the ONLY state change on this door.

    Straight through :func:`~backend.services.dedo_unblock.unblock_framework_issue`, so every guard that
    protects the host command protects this one too: a build that is not stuck on a NEX Studio bug is
    refused (409), an empty reason is refused, and the reason is recorded as Dedo's own message before the
    state moves. What the release does NOT do is resume the build — it lands back on the Manažér's desk
    with a single "Pokračovať". The human keeps the last word; Dedo only reports the repair.
    """
    version, project, _ = _load(db, version_id)
    try:
        dedo_unblock_service.unblock_framework_issue(
            db,
            version_id=version_id,
            reason=body.reason,
            payload_extra=dict(API_TRANSPORT),
        )
    except DedoMessageError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    db.commit()
    logger.info("Dedo (API) unblocked version %s (%s)", version_id, project.slug)
    state = db.execute(select(PipelineState).where(PipelineState.version_id == version_id)).scalar_one()
    return _read(version, project, state)
