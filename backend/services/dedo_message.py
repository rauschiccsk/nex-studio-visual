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

ICCINT-24 ADDS A SECOND TRACK, and it is the common one. The above is Dedo ANSWERING an escalation — a case
that has not occurred once in the product's life. What happens constantly is the opposite direction: the
Director asks Dedo to review an agent's work, Dedo measures and finds mistakes, and those findings reached
the agent only because the Director read Dedo's text and retyped it into the cockpit (four times in one
day). :func:`record_dedo_proposal` records such a finding as ``status='proposed'`` — recorded, shown to the
Manažér, delivered to NOBODY — and the cockpit turns it into one click. The decision stays exactly where
ICCINT-14 put it (the Manažér's); only the retyping goes away. "The Manažér decides" and "the Manažér
transcribes" are not the same thing.

``record_dedo_message`` is the single write path ON PURPOSE. The thin host CLI
(``python -m backend.cli.dedo_message``) calls it today; the authenticated HTTP endpoint (ICCINT-14, once
Dedo has his own machine identity — charter §4.5) must call the SAME function rather than grow a parallel
one, so there is one way for Dedo to speak, not two that drift.

Out of scope here (by decision): unblocking a ``framework_issue`` build is :mod:`backend.services.dedo_unblock`
(ICCINT-13), and no HTTP surface exists for either (ICCINT-14).

RELEASE CONDITION — SATISFIED BY ICCINT-13 (was: "must ship together with it"). On its own this module still
delivers nothing, and that has not changed: a build blocked on ``framework_issue`` offers the Manažér only
``nahlasit_znova``, which re-sends the escalation and dispatches no turn — and, since the audit of
2026-08-22, it also EXECUTES nothing else: ``apply_action``'s framework-block gate refuses every other verb
and :func:`~backend.services.orchestrator.relay_manazer_message` refuses a typed message, so the Manažér
cannot start the turn by a side door either. A message written here therefore sits ``pending`` until Dedo
acts. What ICCINT-13 added is the way OUT of that state:
:func:`backend.services.dedo_unblock.unblock_framework_issue` — Dedo's, not the Manažér's — settles the build
to ``awaiting_manazer`` and sets ``pipeline_state.resume_after_framework_fix``, whereupon
:func:`~backend.services.orchestrator.determine_available_actions` offers exactly ONE action, ``pokracovat``,
in whichever phase the escalation happened. That click runs a turn — the resume carries a directive so that
every phase DISPATCHES it (at Vizuál a directive-less turn only re-shows the preview and calls nobody; that
was the dead end the 2026-08-22 audit found) — and the turn carries whatever is pending here. Pinned
end-to-end by ``TestTheTriggerThatNowExists`` in ``tests/test_dedo_message.py`` and by the per-phase
routed-press test in ``tests/test_dedo_unblock.py``.
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

#: The ONE status a message must be in to be delivered to the agent (see :func:`pending_messages`). Named
#: rather than inlined so the delivery filter and the proposal writer below cannot drift apart silently.
PENDING_STATUS = "pending"

#: ICCINT-24: a finding that is NOT delivery. See :data:`~backend.db.models.pipeline.MESSAGE_STATUS_VALUES`.
PROPOSED_STATUS = "proposed"

#: Terminal status of a handled proposal — sent or rejected, either way never offered again and never
#: ``pending``. The row stays in the log, so the original wording remains findable afterwards.
ARCHIVED_STATUS = "archived"

#: Who a PROPOSAL is addressed to. The Manažér, not the agent — a second, structural reason a proposal
#: cannot leak into a prompt: even a delivery query that forgot the status filter would be looking for
#: ``recipient='ai_agent'`` rows and would not find one.
PROPOSAL_RECIPIENT = "manazer"

#: ``kind`` of a proposal: a notice to the Manažér, which is what it is until he acts on it.
PROPOSAL_KIND = "notification"

#: Payload marker every proposal carries — what the cockpit keys its bar on, and what keeps a proposal out
#: of the conversation transcript (it was never said to the agent).
PROPOSAL_MARKER = "dedo_proposal"

#: The verbs a proposal may be sent WITH — exactly the ones the Manažér already uses to speak to the agent
#: (:func:`~backend.services.orchestrator.apply_action`): ``uprav`` (send it back for rework), ``answer``
#: (answer the agent's question), ``ask`` (ask him something). ICCINT-24 deliberately adds NO fourth verb
#: and no delivery path of its own: the proposal is sent by the very action the Manažér would have clicked
#: himself, so every guard that action has (a non-empty comment, ``answer`` only on a blocked build, the
#: framework-block gate, the single-flight dispatch guard) applies unchanged.
#: ``fast_fix`` (ICCINT-54) je štvrté sloveso a jediné, ktoré NEPREDPOKLADÁ bežiacu stavbu: rýchla oprava
#: verziu vytvára až odoslaním formulára, takže dovtedy niet stavby, do ktorej by sa text dal podať. Návrh
#: sa preto zavesí na NAJNOVŠIU verziu projektu (tam, kam sa Manažér pozerá) a jeho odoslanie nespustí
#: ``apply_action``, ale štart rýchlej opravy — pod účtom Manažéra, presne ako keby formulár vyplnil sám.
#: Bez neho by Dedo musel Directorovi dávať blok na skopírovanie, a to pri KAŽDEJ rýchlej oprave.
PROPOSAL_ACTIONS = ("uprav", "answer", "ask", "fast_fix", "decide")

#: Why a proposal was archived without the Manažér deciding on it: Dedo wrote a NEWER one. Kept distinct
#: from ``sent`` / ``rejected`` so the log never claims he handled something he never saw.
SUPERSEDED_RESOLUTION = "superseded"

#: Human wording per resolution, for the refusal the Manažér reads when he acts on a proposal that is no
#: longer open. Says WHAT happened to it — "už nie je aktuálny" alone would leave him guessing.
_GONE_REASON_SK = {
    "sent": "Tento návrh už bol odoslaný agentovi.",
    "rejected": "Tento návrh už bol odmietnutý.",
    SUPERSEDED_RESOLUTION: "Technický tím medzitým poslal novší návrh — na obrazovke máš starý.",
}
_GONE_FALLBACK_SK = "Tento návrh už nie je otvorený."


class DedoMessageError(Exception):
    """Raised when a Dedo message cannot be recorded (unknown version, empty text, no build to answer)."""


class ProposalNotFound(Exception):
    """The named proposal does not exist on this build at all (a wrong id, a wrong version → 404)."""


class ProposalGone(Exception):
    """The named proposal exists but is no longer open — sent, rejected, or superseded (→ 409).

    Carries the row and its resolution so the caller can tell the Manažér WHICH of those it was, in his own
    words. Never resolved by substituting whatever is open instead: that substitution is the whole defect
    this class exists to prevent (:func:`proposal_for_decision`).
    """

    def __init__(self, proposal: PipelineMessage, resolution: Optional[str]) -> None:
        self.proposal = proposal
        self.resolution = resolution
        super().__init__(f"Proposal {proposal.id} is no longer open ({resolution or proposal.status})")

    @property
    def message_sk(self) -> str:
        """What the Manažér is told — what happened to his finding, and what he does now.

        Deliberately does not promise anything about the screen (the cockpit refreshes itself on this
        refusal, but that is the cockpit's business): it states the fact and points at the decision that
        is actually his to make.
        """
        why = _GONE_REASON_SK.get(self.resolution or "", _GONE_FALLBACK_SK)
        return f"{why} Nič sa neodoslalo — rozhodni o tom, ktorý návrh je na obrazovke teraz."


def record_dedo_message(
    db: Session,
    *,
    version_id: uuid.UUID,
    content: str,
    payload_extra: Optional[dict] = None,
) -> PipelineMessage:
    """Record a message from Dedo to the AI Agent of ``version_id``; return the persisted row.

    The SINGLE write path for Dedo's voice (the two host CLIs today, the ICCINT-14 endpoint tomorrow). The row
    is written ``status='pending'`` — :func:`pending_for_prompt` is what turns it into something the agent
    actually reads, and it stays pending until a turn has carried it, so a crash between the write and the
    next dispatch re-delivers instead of swallowing.

    ``stage`` is stamped from the build's CURRENT phase so the message sits in the thread where the
    escalation happened. The caller commits (this only flushes) — same contract as every other writer.

    ``payload_extra`` merges extra keys into the recorded payload. ICCINT-13's unblock uses it to mark its
    reason ``dedo_unblock=True`` — the same row, written the same way, just labelled so the cockpit can say
    "Dedo let the build go on, and here is why" instead of showing an unattributed green bubble. A second
    writer for that one flag would be exactly the drift this module exists to prevent.
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
        status=PENDING_STATUS,
        payload={"phase": state.current_stage, "dedo_reply": True, **(payload_extra or {})},
    )
    logger.info("Dedo message recorded for version %s (message %s)", version_id, msg.id)
    return msg


def record_dedo_proposal(
    db: Session,
    *,
    version_id: uuid.UUID,
    content: str,
    proposed_action: str,
    payload_extra: Optional[dict] = None,
) -> PipelineMessage:
    """Record a finding Dedo PROPOSES the Manažér send to the agent; return the persisted row (ICCINT-24).

    The ordinary case the product was missing. :func:`record_dedo_message` is Dedo ANSWERING an escalation:
    it writes ``pending``, so the next turn carries it, which is why ICCINT-14 confines it to a build that
    asked for Dedo. But most of Dedo's findings are about a build that never escalated — the Director asks
    him to review the agent's work, he measures, he finds mistakes — and the only way those reached the
    agent was the Director reading Dedo's text and retyping it into the cockpit.

    This is the same text on a different track. The row is written ``status='proposed'`` and addressed to
    the ``manazer``, so:

    * NOTHING delivers it. Delivery keys on ``pending`` (:func:`pending_messages` → :func:`pending_for_prompt`),
      and a proposal is not pending — not "not yet", but never: its only transitions are to ``archived``.
    * The agent does not learn it exists. It is not addressed to him and it is not in his prompt.
    * The decision stays with the Manažér, exactly as before. What changes is only that he no longer has to
      retype the text to make it — the cockpit offers Dedo's wording, editable, behind one button that
      sends it as HIS message through ``proposed_action`` (see :data:`PROPOSAL_ACTIONS`).

    Because it delivers nothing, this write is allowed into ANY build — that is the whole difference from
    :func:`record_dedo_message`, whose HTTP door refuses a build that never escalated. A proposal into a
    healthy build is a suggestion on the Manažér's desk, not an instruction in the agent's prompt.

    AT MOST ONE PROPOSAL IS EVER OPEN PER BUILD, and this writer is what makes that true: a new finding
    ARCHIVES every still-open one first (``resolution='superseded'``). Dedo re-measuring and proposing again
    is the normal case, and the older row is by then a stale reading of a build that has moved on. Before
    this (audit 2026-08-23, finding 2) the rows simply piled up and only the newest was OFFERED — so the
    moment the Manažér sent or declined it, the bar came back carrying an obsolete finding that looked
    exactly like a new one, and one click would have sent it to the agent. "Handled" has to be a property of
    the BUILD's desk, not of one row; the superseded rows stay in the log, marked, so what was proposed and
    when is still answerable.

    Refuses an empty text, an unknown ``proposed_action``, and a version with no build (there is no agent
    to propose anything to). The caller commits (this only flushes) — same contract as every other writer.
    """
    text = (content or "").strip()
    if not text:
        raise DedoMessageError("Dedo proposal is empty — there is nothing to propose")
    if proposed_action not in PROPOSAL_ACTIONS:
        raise DedoMessageError(
            f"Unknown proposed action {proposed_action!r} — Dedo may propose only: {', '.join(PROPOSAL_ACTIONS)}"
        )

    state = db.execute(select(PipelineState).where(PipelineState.version_id == version_id)).scalar_one_or_none()
    if state is None:
        raise DedoMessageError(f"No pipeline started for version {version_id} — there is no agent to write to")

    # ICCINT-56: ``decide`` is the fifth verb and the only one aimed at a DECISION CARD rather than at the
    # agent. A card blocked on ``decision_needed`` offers the Manažér nothing but the card — no ``uprav``, no
    # ``answer`` — so before this the only way to steer a stalled build was to TYPE the brief into the card's
    # free-text box. That is the one thing the proposal door exists to remove (nex-productcatalogs 0.1.1,
    # 04.09.2026: after five failed rounds the engine withdraws the one-click option ON PURPOSE, leaving the
    # keyboard as the only way forward).
    #
    # The key is resolved and PINNED here, at proposal time, for the same reason the verb is
    # (audit 2026-08-23, finding 1): the send path must run against the card the Manažér actually read, never
    # against whatever is open at click time.
    extra = dict(payload_extra or {})
    if proposed_action == "decide":
        from backend.services.orchestrator import _latest_consultation  # local import: see record_dedo_message

        lc = _latest_consultation(db, version_id)
        decisions = (lc[0].get("decisions") or []) if lc is not None else []
        if len(decisions) != 1:
            # Honest-by-construction: with no card there is nothing to answer, and with several open decisions
            # Dedo would be GUESSING which one his text belongs to. Refuse rather than pick.
            raise DedoMessageError(
                "A 'decide' proposal needs exactly ONE open decision on this build — "
                f"found {len(decisions)}. Nothing was proposed."
            )
        extra["decision_key"] = decisions[0].get("key")

    from backend.services.orchestrator import _record_message  # local import: see record_dedo_message

    # ONE open proposal per build (see the docstring): whatever was still waiting is now stale, so it is
    # archived rather than left to resurface behind the finding that replaced it.
    for stale in _open_proposals(db, version_id):
        resolve_proposal(db, stale, resolution=SUPERSEDED_RESOLUTION)

    msg = _record_message(
        db,
        version_id=version_id,
        stage=state.current_stage,
        author=DEDO_PARTICIPANT,
        recipient=PROPOSAL_RECIPIENT,
        kind=PROPOSAL_KIND,
        content=text,
        status=PROPOSED_STATUS,
        payload={
            "phase": state.current_stage,
            PROPOSAL_MARKER: True,
            "proposed_action": proposed_action,
            **extra,
        },
    )
    logger.info(
        "Dedo proposal recorded for version %s (message %s, action %s)",
        version_id,
        msg.id,
        proposed_action,
    )
    return msg


def _open_proposals(db: Session, version_id: uuid.UUID) -> list[PipelineMessage]:
    """Every still-open proposal of a build, newest first. Normally 0 or 1 (see :func:`record_dedo_proposal`)."""
    return list(
        db.execute(
            select(PipelineMessage)
            .where(
                PipelineMessage.version_id == version_id,
                PipelineMessage.author == DEDO_PARTICIPANT,
                PipelineMessage.status == PROPOSED_STATUS,
            )
            .order_by(PipelineMessage.seq.desc())
        )
        .scalars()
        .all()
    )


def open_proposal(db: Session, version_id: uuid.UUID) -> Optional[PipelineMessage]:
    """The build's proposal still waiting for the Manažér; ``None`` when there is none.

    There is AT MOST ONE, and that is enforced at the write: :func:`record_dedo_proposal` archives any
    still-open proposal as ``superseded`` before recording a new one, because Dedo re-measuring and
    proposing again is the normal case and the older reading is stale by then. The newest-first ordering is
    therefore belt-and-braces (a row written before that rule existed, or by a future second writer), not
    the mechanism — "handled" is a property of the build's desk, not of a single row.

    NOT the authority for acting on a proposal: :func:`proposal_for_decision` is, because the send/reject
    the Manažér clicked names the row he was LOOKING at, and this function answers a different question
    ("what is open now?"). Using this one to resolve a click is the TOCTOU the 2026-08-23 audit found.
    """
    rows = _open_proposals(db, version_id)
    return rows[0] if rows else None


def proposal_for_decision(db: Session, version_id: uuid.UUID, message_id: uuid.UUID) -> PipelineMessage:
    """The proposal the Manažér was LOOKING AT when he pressed a button; raise when it is not actionable.

    This exists because "what is open now" is not the same question as "what did he decide about", and the
    difference is not theoretical (audit 2026-08-23, finding 1): the cockpit reconciles every 25 s, Dedo
    re-measures and re-proposes constantly, and both send and reject used to look the CURRENT open proposal
    up again. So a finding written in the gap between his reading and his click would be executed with ITS
    verb instead of the one on the button he pressed — he presses "Spýtať sa agenta" (a question) and the
    engine runs ``uprav`` (work handed back, tasks reset, the loop re-dispatched) — while the finding he
    actually read was archived as "sent" without ever going anywhere. Rejecting mirrored it: the wrong one
    declined, the one on his screen still open.

    Naming the row closes it: it is either the one he saw and still open, or the call is refused and he is
    told why. Raises :class:`ProposalGone` — the caller maps it to a 409, never a silent substitution.
    """
    row = db.execute(
        select(PipelineMessage).where(
            PipelineMessage.id == message_id,
            PipelineMessage.version_id == version_id,
            PipelineMessage.author == DEDO_PARTICIPANT,
        )
    ).scalar_one_or_none()
    if row is None or not (row.payload or {}).get(PROPOSAL_MARKER):
        raise ProposalNotFound(f"No proposal {message_id} on version {version_id}")
    if row.status != PROPOSED_STATUS:
        raise ProposalGone(row, (row.payload or {}).get("dedo_proposal_resolution"))
    return row


def resolve_proposal(
    db: Session,
    proposal: PipelineMessage,
    *,
    resolution: str,
    sent_text: Optional[str] = None,
    sent_action: Optional[str] = None,
    sent_message_id: Optional[uuid.UUID] = None,
) -> PipelineMessage:
    """Mark a proposal handled — ``sent``, ``rejected`` or ``superseded`` — so it is never offered again.

    The first two are the Manažér's decision. ``superseded`` is NOT a decision and is deliberately spelled
    differently: it means Dedo replaced the finding before anyone acted on it (:func:`record_dedo_proposal`),
    and the log must not read as though the Manažér handled something he never saw.

    The row moves to ``archived`` (never ``pending``: a handled proposal is not a queued delivery) and
    keeps its ORIGINAL ``content`` untouched. What actually went to the agent is a separate ``manazer``
    message written by the action; the link between the two is recorded here (``sent_message_id``) and
    stamped on that message by the route, so both directions are traceable — including the case that
    matters most, where the Manažér rewrote Dedo's wording before sending it.
    """
    payload = dict(proposal.payload or {})
    payload["dedo_proposal_resolution"] = resolution
    if sent_text is not None:
        payload["sent_text"] = sent_text
        payload["edited"] = sent_text.strip() != (proposal.content or "").strip()
    if sent_action is not None:
        payload["sent_action"] = sent_action
    if sent_message_id is not None:
        payload["sent_message_id"] = str(sent_message_id)
    proposal.payload = payload  # reassign: JSONB change tracking is by identity
    proposal.status = ARCHIVED_STATUS
    db.flush()
    logger.info("Dedo proposal %s resolved: %s", proposal.id, resolution)
    return proposal


def pending_messages(db: Session, version_id: uuid.UUID) -> list[PipelineMessage]:
    """Dedo messages for ``version_id`` that no agent turn has carried yet, oldest first.

    ``status == PENDING_STATUS`` is the load-bearing clause, not a formality: it is what keeps an ICCINT-24
    ``proposed`` finding — one the Manažér has NOT clicked — out of every prompt the agent is ever handed.
    ``tests/test_dedo_message.py::TestAProposalNeverReachesTheAgent`` fails the moment it is widened.
    """
    return list(
        db.execute(
            select(PipelineMessage)
            .where(
                PipelineMessage.version_id == version_id,
                PipelineMessage.author == DEDO_PARTICIPANT,
                PipelineMessage.status == PENDING_STATUS,
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
