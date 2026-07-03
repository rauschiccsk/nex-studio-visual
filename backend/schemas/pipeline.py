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
    """The Programovanie task currently in focus, for the who's-up status (WS-C2, CR-NS-035; v2 §4.4.2)."""

    number: int
    title: str


class AgentSession(BaseModel):
    """Per-agent liveness for the who's-up status — ``idle`` / ``active`` / ``stale`` from R1's
    ``OrchestratorSession.last_input_at`` heartbeat. v2: only the two agents (AI Agent / Auditor)."""

    role: str
    status: Literal["idle", "active", "stale"]


class PipelineBoardRead(BaseModel):
    """Vývoj board snapshot: current 4-phase state + the most recent messages (CR-V2-021).

    The v2 Vývoj board (design §4.4.2) renders a horizontal 4-phase bar (Príprava → Návrh → Programovanie
    → Verifikácia → Hotovo) whose chips ARE the tabs; each phase's durable artifact (Špecifikácia .md /
    design doc / coding log / Auditor verdict) is read by the FE from ``recent_messages`` (the phase
    gate_report / verdict carries it in ``payload['report']``). ``state`` is ``None`` until ``start``.

    The v1 Gate-E / gate_g / Coordinator board fields (``gate_e_open_findings`` / ``release_acceptance_
    satisfied`` / ``regate_proposal`` / ``coordinator_triage`` / ``autonomous_decisions_summary``) are
    DROPPED — there is no Gate E, no gate_g release-gate, and no Coordinator hub in the 4-phase model.
    """

    state: Optional[PipelineStateRead] = None
    recent_messages: list[PipelineMessageRead] = Field(default_factory=list)
    #: Backend-authoritative set of schvaľovacie-body actions valid to OFFER right now (WS-C1, CR-NS-030;
    #: rebuilt to the dial-governed v2 verbs in CR-V2-009 — ``approve_spec`` / ``schvalit`` / ``uprav`` /
    #: ``pokracovat`` / ``ask`` / ``answer`` / ``verdict`` / ``pause``). The FE renders only these; empty when
    #: the pipeline hasn't started.
    available_actions: list[str] = Field(default_factory=list)
    #: Build-readiness facts (WS-C1, CR-NS-030) the FE uses to DISABLE the Programovanie sign-off button when
    #: not satisfiable: ``all_tasks_done`` False → a task is still ``todo``; ``build_open_findings`` > 0 → a
    #: failed/unverified task. Defaults are the permissive "ready" values so an absent field never disables.
    #: Also feeds the Programovanie split-view task progress (design §4.5).
    all_tasks_done: bool = True
    build_open_findings: int = 0
    #: The Programovanie task currently in focus (WS-C2, CR-NS-035) — in_progress while coding, else the held
    #: failed task at a fix-loop HALT; the who's-up status shows "#N: title". ``None`` outside Programovanie.
    current_task: Optional[BoardTask] = None
    #: Per-agent liveness (idle / active / stale) for the who's-up status — one entry per v2 agent role
    #: (AI Agent / Auditor). An absent field on an older board → the FE renders no staleness indicator.
    agent_sessions: list[AgentSession] = Field(default_factory=list)
    #: CR-V2-056 (reality-anchoring): is this version VERIFIED *right now*, COMPUTED from the live repo (the
    #: Verifikácia PASS verdict's bound commit SHA vs the current HEAD) — NOT a stored 'done' snapshot. Lets
    #: the board reflect reality: a version whose HEAD drifted past its verified commit shows a stale-PASS
    #: warning instead of a frozen green. ``verified_provenance`` is the reason: ``sha_match`` (verified) /
    #: ``sha_drift`` (was verified, HEAD moved past it — the frozen PASS the FE flags) / ``unbound`` /
    #: ``legacy`` / ``repo_unreadable`` / ``no_pass`` (never passed / re-judge pending).
    verified: bool = False
    verified_provenance: str = "no_pass"


class PipelineActionRequest(BaseModel):
    """Director action body for ``POST /pipeline/{version_id}/action``.

    ``action`` and ``payload`` shape are validated by the orchestrator
    (F-007 §5.2); invalid values surface as HTTP 4xx via the router.
    """

    action: str
    payload: Optional[dict[str, Any]] = None


class PipelineRelayRequest(BaseModel):
    """Body for ``POST /pipeline/{version_id}/relay`` (CR-V2-015) — a Manažér message typed in the
    read-only AI Agent tab.

    SPIKE-IO Model B: the message is RELAYED by the engine as the next ``--resume`` turn (the engine is the
    sole writer to the warm ``claude`` session) — it is NEVER keystroked into the PTY. When a turn is in
    flight the message is enqueued behind it; when settled it dispatches immediately as an ``ask``/``answer``.
    """

    text: str = Field(..., min_length=1, description="The Manažér's message to relay to the AI Agent.")


class PipelineRelayResponse(BaseModel):
    """Result of a relay: whether the message was ENQUEUED behind an in-flight turn (``deferred``) or
    dispatched immediately, plus the current board snapshot."""

    deferred: bool
    board: PipelineBoardRead


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
