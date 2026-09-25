"""Schemas for Dedo's own door (ICCINT-14) — what the technical team may see and say.

Deliberately a SMALL surface. Dedo reads a build's position and its thread, and he says two things: an
answer to the agent, and "fixed, go on". Nothing here describes an approval, a start, a stop or a
decision — those are the Manažér's, and they are absent from this module for the same reason they are
absent from the router: what does not exist cannot be called.

The message log reuses :class:`~backend.schemas.pipeline.PipelineMessageRead` rather than defining a
second shape of the same row — Dedo reads the SAME thread the cockpit shows, not a private rendering of it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DedoBuildRead(BaseModel):
    """One build as Dedo needs to see it: which project, where it stands, and why it is stuck.

    Flat on purpose — Dedo's client is a terminal, not the cockpit, so the project a version belongs to is
    inlined instead of being a second lookup he has no endpoint for.
    """

    version_id: UUID
    version_number: str
    project_id: UUID
    project_slug: str
    project_name: str
    #: Phase the build is sitting in (``priprava`` / ``vizual`` / ``programovanie`` / …).
    current_stage: str
    current_actor: str
    status: str
    #: WHY it is blocked. ``framework_issue`` is the only reason Dedo can clear; the others are shown so he
    #: can see at a glance that a build is stuck on something that is not his to unstick.
    block_reason: Optional[str] = None
    next_action: str
    #: ICCINT-13: set once Dedo released the build and it is waiting for the Manažér to press "Pokračovať".
    resume_after_framework_fix: bool = False
    #: When the build entered its current wait — how long it has been sitting on Dedo's desk.
    waiting_since: Optional[datetime] = None
    updated_at: datetime


class DedoMessageCreate(BaseModel):
    """Dedo's answer to the build's AI Agent."""

    #: Whitespace-only text is refused by
    #: :func:`~backend.services.dedo_message.record_dedo_message` (409) — a message that says nothing is
    #: not a message. ``min_length`` catches only the literally empty body, which is the common typo.
    content: str = Field(min_length=1)


class DedoProposalCreate(BaseModel):
    """A finding Dedo PROPOSES the Manažér send to the agent (ICCINT-24) — not a message to the agent.

    The extra field over :class:`DedoMessageCreate` is the whole point: a proposal carries the ACTION it
    should be sent with, because the Manažér must not be asked to pick a verb out of the engine's
    vocabulary. It is one the cockpit already offers him — ``uprav`` / ``answer`` / ``ask`` continue the build
    in front of him, ``fast_fix`` starts a patch version (ICCINT-54), ``decide`` answers an open decision card
    (ICCINT-56); the send goes through that action with all of its guards, so there is no second way into the
    agent's prompt.
    """

    content: str = Field(min_length=1)
    #: Validated against :data:`~backend.services.dedo_message.PROPOSAL_ACTIONS` in the service (409), so
    #: the allowed set lives in ONE place next to the writer instead of being restated as a Literal here.
    proposed_action: str = Field(min_length=1, description="uprav | answer | ask | fast_fix | decide")


class DedoProjectProposalCreate(BaseModel):
    """Zadanie, ktoré Dedo pripravil pre prácu, ktorá sa ešte NEZAČALA (ICCINT-152).

    Dvojička k :class:`DedoProposalCreate`. Rozdiel je jediný a je to celý dôvod jej existencie: tamtá sa
    pripína na bežiacu stavbu, táto na projekt — lebo keď stavba nebeží, niet sa na čo pripnúť, a práve
    vtedy zadanie treba.
    """

    content: str = Field(min_length=1)
    #: Overuje sa proti :data:`~backend.db.models.dedo_proposal.PROJECT_PROPOSAL_ACTIONS` v službe (409),
    #: aby povolená množina žila na JEDNOM mieste vedľa zapisovateľa.
    proposed_action: str = Field(min_length=1, description="fast_fix | new_version")


class DedoProjectProposalRead(BaseModel):
    """Otvorené zadanie tak, ako ho Manažér uvidí na stránke projektu."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    content: str
    proposed_action: str
    status: str
    created_at: datetime


class DedoProjectProposalSendRequest(BaseModel):
    """Manažér posiela zadanie ďalej — text je TEN, ktorý mal na obrazovke, aj s jeho úpravami.

    ``proposal_id`` nie je ozdoba: koná sa nad návrhom, ktorý mal pred očami, nie nad „tým, čo je
    otvorené teraz". Inak by Dedov novší návrh podaný medzi zobrazením a kliknutím odišiel namiesto neho.
    """

    proposal_id: UUID
    text: str = Field(min_length=1)


class DedoProjectProposalRejectRequest(BaseModel):
    """Manažér zadanie zamieta — menuje ten, ktorý videl, z rovnakého dôvodu."""

    proposal_id: UUID


class DedoProjectProposalSendResponse(BaseModel):
    """Čo z kliknutia vzniklo. Verzia je jediná odpoveď, ktorá Manažéra zaujíma — vezme ho tam."""

    version_id: UUID
    #: ``True`` pri ``fast_fix``; pri ``new_version`` je verzia koncept a nič nebeží.
    started: bool


class DedoUnblockRequest(BaseModel):
    """Release a build stuck on a NEX Studio bug. The reason is mandatory, not paperwork.

    It becomes the build's only permanent record of who let it go on and why, and the agent reads it as the
    answer to its escalation on the very next turn.
    """

    reason: str = Field(min_length=1)
