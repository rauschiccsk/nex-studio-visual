"""Dedo unblocks a build stuck on a NEX Studio bug (ICCINT-13) — the way OUT of ``framework_issue``.

The escalation path was complete in both directions and still ended in a dead end. The AI Agent could
escalate a bug it cannot fix because the fix belongs in NEX Studio itself (``blocked`` /
``block_reason='framework_issue'``, delivered by :mod:`backend.services.dedo_escalation`); ICCINT-12 gave
Dedo a voice back (:mod:`backend.services.dedo_message`). What no one had was a way to say "fixed — go on".
The Manažér's only offered action, ``nahlasit_znova``, re-sends the report and changes nothing, so the build
hung on that screen forever and Dedo's answer sat ``pending`` behind a turn that would never run.

WHO MAY DO IT — DEDO, NOT THE MANAŽÉR (Director decision). The Manažér cannot judge whether NEX Studio was
actually fixed. Letting him release the build would send it straight back into the same broken version: one
more burnt round, one more identical block, and a screen that now lies about who cleared it. So this is not
an action verb, has no HTTP endpoint and appears in no menu — the Manažér keeps ``nahlasit_znova``. What he
DOES get is the decision that follows: once Dedo unblocks, the build waits for the Manažér to press
"Pokračovať". Dedo reports the repair, the Manažér decides to go on — the human keeps the last word.

WHAT IT DOES, in one transaction:

  * REFUSES anything that is not ``blocked`` ∧ ``framework_issue`` — there is no such thing as "unblocking"
    a healthy build, and a typo'd project slug looks exactly like one.
  * REFUSES an empty reason. The reason is not paperwork: it is the only record of who let the build go on
    and why, and it is what the agent reads before its next move.
  * WRITES the reason as an ordinary Dedo message through
    :func:`backend.services.dedo_message.record_dedo_message` — the SAME single write path ICCINT-12
    established (marked ``dedo_unblock``), so the reason lands in the thread permanently AND rides into the
    resumed turn like any other answer.
  * SETTLES the build ``awaiting_manazer`` with ``block_reason`` cleared and
    ``resume_after_framework_fix`` set, which is what makes
    :func:`~backend.services.orchestrator.determine_available_actions` offer the single ``pokracovat``
    button — in whichever phase the escalation happened. Today that is Príprava, Vizuál or Programovanie
    (the three sites that settle ``framework_issue``), but the guard below is deliberately drawn wider and
    on a different axis: it asks whether the resumed turn is the AI AGENT's, which is the property that
    actually has to hold, instead of hard-coding a list of phases that would silently go stale.
  * REFUSES a phase whose turn is not the AI Agent's — see the guard.

The transport is a host-side CLI (:mod:`backend.cli.dedo_unblock`), not HTTP: charter §4.5 forbids Dedo the
API until he has his own machine identity (ICCINT-14), and that boundary is meant to be enforced by a token
rather than by the caller's good behaviour. When the endpoint arrives it must call THIS function — one way
to unblock a build, not two that drift.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.pipeline import PipelineState
from backend.services.dedo_message import DedoMessageError, record_dedo_message, version_awaiting_dedo

logger = logging.getLogger(__name__)

__all__ = ["DedoMessageError", "unblock_framework_issue", "version_awaiting_dedo"]

#: What the board tells the Manažér once the build is released. Plain Slovak, no "framework"/"Dedo" jargon
#: (the cockpit's reader is a non-expert): what happened, and the one thing to do about it.
UNBLOCK_NEXT_ACTION = "Chybu v NEX Studiu sme opravili. Stlač „Pokračovať“ — AI Agent bude pokračovať tam, kde prestal."


def unblock_framework_issue(
    db: Session,
    *,
    version_id: uuid.UUID,
    reason: str,
    payload_extra: dict | None = None,
) -> PipelineState:
    """Release ``version_id`` from its ``framework_issue`` block; return the settled state.

    ``reason`` is MANDATORY (see the module docstring) and is recorded as a Dedo message before the state
    moves, so a build can never be found unblocked with no explanation of why. The caller commits — this
    only flushes, the same contract every other writer in this codebase follows.

    ``payload_extra`` is merged into that message's payload alongside the ``dedo_unblock`` marker. ICCINT-14
    uses it to stamp the TRANSPORT (``dedo_transport='api'``) so the thread records that Dedo released the
    build over the network rather than from the host — the same one-writer discipline as everything else
    here: the HTTP route adds a label, not a second way to unblock.

    Raises :class:`~backend.services.dedo_message.DedoMessageError` when the reason is empty, the version has
    no build, or the build is not blocked on ``framework_issue``.
    """
    text = (reason or "").strip()
    if not text:
        raise DedoMessageError("Unblocking needs a reason — it is the build's only record of why it went on")

    state = db.execute(select(PipelineState).where(PipelineState.version_id == version_id)).scalar_one_or_none()
    if state is None:
        raise DedoMessageError(f"No pipeline started for version {version_id} — there is no build to unblock")

    # THE GUARD. Only a build actually stuck on a NEX Studio bug can be released this way. Anything else —
    # a healthy build, an agent question, a paused loop — is either not stuck or stuck for a reason Dedo is
    # not the one to clear, and forcing it would hand the Manažér a resume button for a state he already has
    # the right actions for.
    if not (state.status == "blocked" and state.block_reason == "framework_issue"):
        raise DedoMessageError(
            f"Version {version_id} is not blocked on a NEX Studio bug "
            f"(status={state.status!r}, block_reason={state.block_reason!r}) — nothing to unblock"
        )

    # Local import: :mod:`backend.services.orchestrator` imports ``dedo_message``, which this module imports;
    # importing the orchestrator at module scope would drag that whole chain into every CLI start for one
    # lookup table. STAGE_ACTOR answers "WHO does the resumed turn run" — and the answer must be the AI
    # AGENT, for two reasons that are really one: the terminal ``done`` stage has no actor at all
    # (``_begin_dispatch`` silently no-ops → a button that does nothing), and ``verifikacia`` runs the
    # independent AUDITOR, whose turn deliberately does NOT eat a message addressed to the AI Agent
    # (:func:`invoke_agent_with_parse_retry` folds pending Dedo messages only for ``role == AI_AGENT_ROLE``)
    # — so the press would run a turn that leaves Dedo's answer ``pending`` forever. Neither combination is
    # reachable today: ``framework_issue`` is settled only from Príprava, Vizuál and Programovanie, and a
    # blocked build's phase cannot change. Refusing keeps it that way LOUDLY instead of turning it into a
    # second dead end.
    from backend.services.orchestrator import AI_AGENT_ROLE, STAGE_ACTOR

    if STAGE_ACTOR.get(state.current_stage) != AI_AGENT_ROLE:
        raise DedoMessageError(
            f"Version {version_id} is in phase {state.current_stage!r}, which has no AI Agent turn to resume "
            f"(actor={STAGE_ACTOR.get(state.current_stage)!r}) — unblocking it would leave the Manažér with a "
            "button that does nothing for Dedo's answer"
        )

    msg = record_dedo_message(
        db,
        version_id=version_id,
        content=text,
        # ``dedo_unblock`` is written LAST so a caller's extras can label the write but never disown it.
        payload_extra={**(payload_extra or {}), "dedo_unblock": True},
    )

    # Order matters only for legibility: the ``status`` set-listener (``_clear_block_reason_on_unblock``)
    # already drops ``block_reason`` on the way out of ``blocked``. It is written explicitly anyway so the
    # transition reads completely at this site and does not depend on a listener the next reader may not know.
    state.status = "awaiting_manazer"
    state.block_reason = None
    state.resume_after_framework_fix = True
    state.next_action = UNBLOCK_NEXT_ACTION
    db.flush()
    logger.info("Dedo unblocked version %s (framework_issue cleared, message %s)", version_id, msg.id)
    return state
