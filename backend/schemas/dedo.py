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

from pydantic import BaseModel, Field


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


class DedoUnblockRequest(BaseModel):
    """Release a build stuck on a NEX Studio bug. The reason is mandatory, not paperwork.

    It becomes the build's only permanent record of who let it go on and why, and the agent reads it as the
    answer to its escalation on the very next turn.
    """

    reason: str = Field(min_length=1)
