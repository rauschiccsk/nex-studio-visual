"""Pydantic schemas for the pipeline cockpit API (F-007 §6, CR-NS-018 Phase 3)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from backend.db.models.pipeline import (
    ACTOR_VALUES,
    BLOCK_REASON_VALUES,
    FLOW_TYPE_VALUES,
    MESSAGE_KIND_VALUES,
    STAGE_VALUES,
    STATUS_VALUES,
)

# Literal aliases sourced from the DB CHECK value tuples (v0.7.0 R2, D2) — the response-schema enums
# and the DB constraints share ONE source so they cannot drift. FastAPI introspects each ``Literal``
# into an OpenAPI ``enum``, which drives the generated FE pipeline types (R2-b). ``Literal[<tuple>]``
# is equivalent at runtime to spelling the members out (``Literal["a", "b"]`` already passes a tuple),
# and Pydantic validates them; the backend uses no static type-checker (intentional), so the dynamic
# construction needs no ``# type: ignore``.
FlowType = Literal[FLOW_TYPE_VALUES]
PipelineStage = Literal[STAGE_VALUES]
PipelineActor = Literal[ACTOR_VALUES]
PipelineStatus = Literal[STATUS_VALUES]
BlockReason = Literal[BLOCK_REASON_VALUES]
MessageKind = Literal[MESSAGE_KIND_VALUES]


class PipelineStateRead(BaseModel):
    """Serialised ``pipeline_state`` row — "who is on turn and what's next"."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    version_id: UUID
    flow_type: FlowType
    current_stage: PipelineStage
    current_actor: PipelineActor
    status: PipelineStatus
    next_action: str
    is_regate: bool
    iteration: int
    #: R4 (D1): why the pipeline is ``blocked`` (authoritative; replaces the FE ``isErrorBlock`` heuristic).
    #: ``None`` whenever ``status != 'blocked'`` (and on legacy blocked rows pre-067 → FE heuristic fallback).
    block_reason: Optional[BlockReason] = None
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
    kind: MessageKind
    content: str
    status: str
    #: Free-form per-message structured data (deliverables / commits / findings / usage / timing / the
    #: synthesis & autonomous markers …). Legible-cockpit-output fix adds ``report``: the agent's full
    #: human-readable markdown report body — the rich source the cockpit bubble renders, while ``content``
    #: stays the one-line summary. Additive within the existing freeform dict — no schema / codegen change.
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


class RegateProposal(BaseModel):
    """gate_g FAIL re-gate proposal (CR-NS-057 §F2.4): the Coordinator's inferred re-gate target for a FAIL
    verdict + a short Slovak rationale, computed FRESH for the board. The Director one-click-confirms it or
    overrides to any gate_a..build stage; the verdict stays the Director's."""

    entry_stage: str
    reason: Optional[str] = None


class CoordinatorTriage(BaseModel):
    """R4 (D3): the LATEST Coordinator relay/escalation triage in front of the Director NOW — its
    ``triage_class`` + ``confidence`` + ``proposed_action``. Surfaced even for a NON-executable relay
    (``director_decision`` / low-confidence), unlike the executable proposal WhosTurnBoard already shows.
    All optional — a directive may omit any field."""

    triage_class: Optional[str] = None
    confidence: Optional[float] = None
    proposed_action: Optional[str] = None


class AutonomousDecision(BaseModel):
    """R4 (D4): one ``is_autonomous`` Coordinator decision in the board roll-up (task #, action, why)."""

    task: Optional[int] = None
    action: Optional[str] = None
    rationale: Optional[str] = None
    confidence: Optional[float] = None


class AutonomousDecisionsSummary(BaseModel):
    """R4 (D4): board-level roll-up of the ``is_autonomous`` Coordinator notes (CR-055 recoveries +
    CR-103 fast_fix answers) — the total ``count`` + the ``recent`` few (newest first)."""

    count: int = 0
    recent: list[AutonomousDecision] = Field(default_factory=list)


class AgentSession(BaseModel):
    """R4 (D5): per-role agent liveness for the rail — ``idle`` / ``active`` / ``stale`` from R1's
    ``OrchestratorSession.last_input_at`` heartbeat."""

    role: str
    status: Literal["idle", "active", "stale"]


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
    #: gate_g FAIL re-gate proposal (CR-NS-057 §F2.4) — the inferred target + rationale, computed only at
    #: gate_g / awaiting_director|blocked. ``None`` elsewhere; the FE renders the FAIL→target button + chips.
    regate_proposal: Optional[RegateProposal] = None
    #: R4 (D3): the latest Coordinator relay/escalation triage in front of the Director — present only at a
    #: settled (awaiting_director / blocked) state with such a directive; ``None`` otherwise. The FE renders
    #: "Koordinátor klasifikoval: X (istota Y %), navrhuje Z" even for a non-executable relay.
    coordinator_triage: Optional[CoordinatorTriage] = None
    #: R4 (D4): board roll-up of the autonomous Coordinator decisions for this version (count + recent few).
    #: Always computed; the FE renders the line only when ``count > 0`` (absent / 0 → render nothing).
    autonomous_decisions_summary: Optional[AutonomousDecisionsSummary] = None
    #: R4 (D5): per-role agent liveness (idle / active / stale) for the rail's staleness chips. Always present
    #: (one entry per agent role); an absent field on an older board → the FE renders no staleness indicator.
    agent_sessions: list[AgentSession] = Field(default_factory=list)


class PipelineActionRequest(BaseModel):
    """Director action body for ``POST /pipeline/{version_id}/action``.

    ``action`` and ``payload`` shape are validated by the orchestrator
    (F-007 §5.2); invalid values surface as HTTP 4xx via the router.
    """

    action: str
    payload: Optional[dict[str, Any]] = None


class FastFixStartRequest(BaseModel):
    """Body for ``POST /pipeline/fast-fix`` (F-009, CR-NS-094) — the "Rýchla oprava" entry.

    One prompt: ``project_id`` + the Director ``directive`` (the whole fix brief). The backend
    auto-creates the next PATCH version and starts a ``fast_fix`` pipeline carrying the directive.
    """

    project_id: UUID
    directive: str = Field(..., min_length=1, description="The Director's fast-fix directive (the task brief).")


class FastFixStartResponse(BaseModel):
    """Result of starting a Fast-Fix: the new PATCH ``version_id`` + the initial board snapshot."""

    version_id: UUID
    board: PipelineBoardRead
