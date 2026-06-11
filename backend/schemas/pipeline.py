"""Pydantic schemas for the pipeline cockpit API (F-007 §6, CR-NS-018 Phase 3)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PipelineStateRead(BaseModel):
    """Serialised ``pipeline_state`` row — "who is on turn and what's next"."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    version_id: UUID
    flow_type: str
    current_stage: str
    current_actor: str
    status: str
    next_action: str
    is_regate: bool
    iteration: int
    created_at: datetime
    updated_at: datetime


class PipelineMessageRead(BaseModel):
    """Serialised ``pipeline_message`` row (append-only log entry)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    version_id: UUID
    stage: str
    author: str
    recipient: str
    kind: str
    content: str
    status: str
    payload: Optional[dict[str, Any]] = None
    created_at: datetime
    #: Monotonic insertion order (CR-NS-018). Carried in the payload so both the REST
    #: board and the incremental WS broadcast expose the authoritative order — clients
    #: can sort by it instead of relying on arrival timing.
    seq: int


class BoardTask(BaseModel):
    """The build task currently in focus, for the "kto je na rade" board (WS-C2, CR-NS-035)."""

    number: int
    title: str


class PipelineBoardRead(BaseModel):
    """Board snapshot: current state + the most recent messages.

    ``state`` is ``None`` until the pipeline is ``start``ed (lazy creation).
    """

    state: Optional[PipelineStateRead] = None
    recent_messages: list[PipelineMessageRead] = Field(default_factory=list)
    #: Deterministic count of unresolved Gate E gaps (CR-NS-018 §5) — the authoritative
    #: open-finding value the FE close-gate reads, NOT the Customer's ``findings`` array.
    gate_e_open_findings: int = 0
    #: Backend-authoritative set of Director actions valid to OFFER right now (WS-C1, CR-NS-030).
    #: The FE renders only these (intersected with its finer message-derived conditions); empty when
    #: the pipeline hasn't started.
    available_actions: list[str] = Field(default_factory=list)
    #: Build-readiness facts (WS-C1, CR-NS-030) the FE uses to DISABLE the final-approve / end-build
    #: buttons (mirrors ``gate_e_open_findings``): ``all_tasks_done`` False → a task is still ``todo``
    #: (approve blocked); ``build_open_findings`` > 0 → a failed/unverified task (approve + end_build
    #: blocked). Defaults are the permissive "ready" values so an absent field never disables.
    all_tasks_done: bool = True
    build_open_findings: int = 0
    #: The build task currently in focus (WS-C2, CR-NS-035) — in_progress while building, else the held
    #: failed task at a HALT; the "kto je na rade" board shows "#N: title". ``None`` outside build.
    current_task: Optional[BoardTask] = None


class PipelineActionRequest(BaseModel):
    """Director action body for ``POST /pipeline/{version_id}/action``.

    ``action`` and ``payload`` shape are validated by the orchestrator
    (F-007 §5.2); invalid values surface as HTTP 4xx via the router.
    """

    action: str
    payload: Optional[dict[str, Any]] = None
