"""Pipeline orchestrator engine (F-007 §5, CR-NS-018 Phase 2).

Backend-owned state machine that drives the multi-agent pipeline. Director
actions (``apply_action``) transition ``pipeline_state``, write typed
``pipeline_message`` rows, and dispatch the next agent headless via
``claude -p --resume`` (``invoke_agent``). Agent output is parsed
deterministically (``pipeline_status``); a parse failure or a verify failure
escalates to ``status=blocked`` — never a guess (F-007 §5.3, §5.4).

State ownership: ``apply_action`` / ``_dispatch`` are the **sole** mutators of
``pipeline_state``. ``invoke_agent`` only records the agent's message and
returns the parsed block.

Phase 2 = engine + tests only. Live agents are exercised in tests via a
monkeypatched ``invoke_claude``; real wiring lands with the charter §5.3
convention (Phase 3).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Optional

import yaml
from pydantic import ValidationError
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.db.models.backlog import BacklogItem
from backend.db.models.foundation import UserAgentSettings
from backend.db.models.orchestrator import OrchestratorSession
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version
from backend.schemas.backlog import BacklogItemCreate
from backend.schemas.epic import EpicCreate
from backend.schemas.feat import FeatCreate
from backend.schemas.task import TaskCreate
from backend.services import backlog as backlog_service
from backend.services import claude_agent, fast_fix, template_bootstrap, uat_provisioner
from backend.services import epic as epic_service
from backend.services import feat as feat_service
from backend.services import project as project_service
from backend.services import system_setting as system_setting_service
from backend.services import task as task_service
from backend.services.claude_agent import ClaudeAgentError, invoke_claude
from backend.services.pipeline_status import (
    PIPELINE_STATUS_JSON_SCHEMA,
    TASK_PLAN_FEAT_TASKS_JSON_SCHEMA,
    TASK_PLAN_SKELETON_JSON_SCHEMA,
    ParseFailure,
    PipelineStatusBlock,
    TaskPlan,
    TaskPlanEpic,
    TaskPlanFeat,
    extract_report_body,
    extract_task_plan_json,
    parse_status_block,
    parse_structured_output,
    parse_task_plan_feat_tasks,
    parse_task_plan_skeleton,
)

# NOTE (CR-V2-006): the v1 ``CoordinatorDirective`` / ``CoordinatorTarget`` / ``task_pass`` status-block
# shapes were DROPPED from ``pipeline_status``. The Coordinator-relay + per-task-audit + Gate-E engine
# code below still REFERENCES the removed ``parsed.coordinator_directive`` / ``parsed.task_pass`` /
# Gate-E signals; those code paths are dead in v2 and are removed wholesale by CR-V2-009 (apply_action
# rebuild) / CR-V2-013 (Gate-E → Auditor upfront review). They are intentionally left in place here
# (writer-deferral, per the build plan §2.1/§3 ordering + R-BLAST "don't re-author orchestrator.py
# wholesale") and would only raise if actually invoked — the engine-integration tests that exercise them
# are deferred to Milestones C/D. This CR (Milestone B) only lands the status-block CONTRACT.

logger = logging.getLogger(__name__)

# ── v2.0.0 two-agent role identity (CR-V2-007) ─────────────────────────────────────────────────────
# The build engine drives exactly two agents: the AI Agent (the doer) + the independent Auditor (the
# verifier). The DB enum/CHECK values (``OrchestratorSession.role``, ``PipelineState.current_actor``,
# ``UserAgentSettings.agent_role`` — all landed by CR-V2-001) use UNDERSCORE spelling, matching the
# snake_case DB convention; the charter filesystem path uses HYPHEN
# (``.claude/agents/ai-agent/CLAUDE.md``). The two spellings MUST map explicitly and never diverge
# (R-SWEEP). :func:`_charter_slug_for_role` is the single bridge — every charter-path build goes through
# it so a DB value can never silently become a filesystem path.
AI_AGENT_ROLE = "ai_agent"
AUDITOR_ROLE = "auditor"
#: DB role value → charter-path slug (underscore → hyphen). Identity for ``auditor``; explicit for the
#: AI Agent (``ai_agent`` → ``ai-agent``). The ONLY place the two spellings are reconciled.
_CHARTER_PATH_SLUG: dict[str, str] = {
    AI_AGENT_ROLE: "ai-agent",
    AUDITOR_ROLE: "auditor",
}


#: Charter-path slug (hyphen) → DB role value (underscore) — the inverse of :data:`_CHARTER_PATH_SLUG`,
#: built from the same single source so the two spellings can never drift apart.
_DB_ROLE_FROM_SLUG: dict[str, str] = {slug: role for role, slug in _CHARTER_PATH_SLUG.items()}


def _charter_slug_for_role(role: str) -> str:
    """Map a DB role value (underscore) to its charter-path slug (hyphen).

    ``ai_agent`` → ``ai-agent``; ``auditor`` → ``auditor``. Unknown roles fall back to the value
    unchanged (defensive — a mis-keyed dispatch would then miss its charter file rather than crash,
    surfacing as a 'spec missing' error instead of a silent path divergence)."""
    return _CHARTER_PATH_SLUG.get(role, role)


def db_role_for_charter_slug(slug: str) -> str:
    """Map a charter-path slug (hyphen) to its DB role value (underscore) — inverse of
    :func:`_charter_slug_for_role`. ``ai-agent`` → ``ai_agent``; ``auditor`` → ``auditor``. Used at the
    debug-attach boundary, which speaks charter-path slugs but looks up the underscore-keyed
    ``OrchestratorSession.role`` (CR-V2-007). Unknown slugs pass through unchanged."""
    return _DB_ROLE_FROM_SLUG.get(slug, slug)


#: Per-message hook for incremental broadcast (CR-NS-018): the orchestrator calls it
#: right after recording a dispatch-path message; the runner commits + broadcasts that
#: one message (the engine stays WS-free). Defined here so ``claude_agent`` stays model-free.
MessageCallback = Callable[[PipelineMessage], Awaitable[None]]


@dataclass
class _DispatchMetrics:
    """Accumulates token usage + wall-clock across one logical agent turn (WS-D, CR-NS-036).

    A turn may span several ``invoke_agent`` calls (parse-retry re-emits — each burns tokens
    even when its block doesn't parse), so the metrics live in a single object threaded through
    :func:`invoke_agent_with_parse_retry` and folded into the FINAL recorded message's payload.
    ``saw_usage`` stays ``False`` until a real :class:`claude_agent.UsageMetadata` is seen, so a
    run with no usage (test doubles / a usage-less envelope) records ``usage: None`` rather than
    fabricated zeros."""

    input_tokens: int = 0
    output_tokens: int = 0
    duration_seconds: float = 0.0
    attempts: int = 0
    model: Optional[str] = None
    saw_usage: bool = False

    def record(self, usage: Optional["claude_agent.UsageMetadata"], duration: float) -> None:
        """Fold one invocation's outcome in: always count the attempt + its wall-clock; add tokens
        only when the envelope actually carried usage."""
        self.attempts += 1
        self.duration_seconds += duration
        if usage is not None:
            self.saw_usage = True
            self.input_tokens += usage.input_tokens
            self.output_tokens += usage.output_tokens
            if usage.model:
                self.model = usage.model

    def usage_payload(self) -> Optional[dict[str, Any]]:
        """The ``payload.usage`` block, or ``None`` when no usage was ever captured (never fabricate)."""
        if not self.saw_usage:
            return None
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens, "model": self.model}

    def timing_payload(self) -> dict[str, Any]:
        """The ``payload.timing`` block — duration + how many invocations the turn took (parse-retries)."""
        return {"duration_seconds": round(self.duration_seconds, 3), "parse_attempts": self.attempts}


def _split_claude_result(
    result: "tuple | str",
) -> "tuple[str, Optional[claude_agent.UsageMetadata], Optional[dict]]":
    """Normalise :func:`invoke_claude`'s return to ``(text, usage, structured_output)``.

    Since R3 (v0.7.0) ``invoke_claude`` returns the 3-tuple ``(text, usage, structured_output)``
    (was ``(text, usage)`` at WS-D, CR-NS-036). Unit-test doubles that monkeypatch
    ``orchestrator.invoke_claude`` may still return a bare ``str`` or a 2-tuple — tolerate every
    arity (missing elements default to ``None``) so the engine works under test without forcing every
    fake to mint usage / structured output. ``structured_output`` is ``None`` for a test double that
    doesn't model it; the fence fallback then parses the result text exactly as today."""
    if isinstance(result, tuple):
        text = result[0]
        usage = result[1] if len(result) > 1 else None
        structured = result[2] if len(result) > 2 else None
        return text, usage, structured
    return result, None, None


def _failure_metrics_payload(result: object) -> dict[str, Any]:
    """The WS-D ``usage``/``timing`` to fold onto an escalation message for a turn that produced NO
    message of its own — a terminal :class:`ParseFailure` (CR-NS-036). The SINGLE source of the carry
    keys, so the attachment can't drift across the escalation sites.

    Includes ``usage`` and/or ``timing`` independently — ``usage`` is ``None`` (omitted) when no
    envelope was received (e.g. a ClaudeAgentError exhaustion), but ``timing`` is still present and
    MUST be carried (WS-E, CR-NS-037): ``aggregate_pipeline_usage`` counts a payload with timing alone
    (0 tokens, real wall-clock). Empty only for a non-``ParseFailure`` (a successful block already
    carries its own metrics) — so attaching it is always a safe no-op."""
    if not isinstance(result, ParseFailure):
        return {}
    out: dict[str, Any] = {}
    if result.usage is not None:
        out["usage"] = result.usage
    if result.timing is not None:
        out["timing"] = result.timing
    return out


def _relay_fallback_payload(result: object, metrics_role: Optional[str]) -> Optional[dict[str, Any]]:
    """The fallback ``system→director`` note's payload when a Coordinator relay itself parse-fails:
    the failed worker's usage/timing (:func:`_failure_metrics_payload`) plus a ``metrics_role``
    role-of-origin tag (metrics redesign §1.1) so those tokens are attributed to the worker rather than
    the excluded ``system`` bucket. ``None`` when there is nothing to carry (keeps the note's payload
    NULL, unchanged from before)."""
    payload = _failure_metrics_payload(result)
    if not payload:
        return None
    if metrics_role is not None:
        payload = {**payload, "metrics_role": metrics_role}
    return payload


def _seed_metrics_from_failure(result: object) -> Optional["_DispatchMetrics"]:
    """A :class:`_DispatchMetrics` pre-loaded with a failed worker turn's captured usage/timing (WS-D),
    so a Coordinator relay invoked to escalate that failure accumulates ON TOP and its recorded relay
    message carries worker + coordinator tokens (no extra notification, no undercount). ``None`` when
    there's nothing to carry (not a ParseFailure / no usage)."""
    if not isinstance(result, ParseFailure) or result.usage is None:
        return None
    seed = _DispatchMetrics()
    seed.saw_usage = True
    seed.input_tokens = int(result.usage.get("input_tokens") or 0)
    seed.output_tokens = int(result.usage.get("output_tokens") or 0)
    model = result.usage.get("model")
    seed.model = model if isinstance(model, str) else None
    if result.timing:
        seed.duration_seconds = float(result.timing.get("duration_seconds") or 0.0)
        seed.attempts = int(result.timing.get("parse_attempts") or 0)
    return seed


# Ordered phases and the agent responsible for each (v2.0.0 design §2.1; CR-V2-009).
# The v1 11-stage 5-role serial waterfall (kickoff/gate_a..gate_e/task_plan/build/gate_g/release)
# collapses to the FOUR v2 phases the AI Agent walks with one warm context, plus the terminal ``done``
# (= "Hotovo"). Single source of truth shared with the DB ``STAGE_VALUES`` tuple
# (``backend/db/models/pipeline.py``) and ``pipeline_status.STAGES``:
#   * ``priprava``      — Príprava: interactive Zadanie→Špecifikácia dialogue (CR-V2-010); ends at the
#                         ALWAYS-mandatory ``approve_spec`` stop (dial-independent).
#   * ``navrh``         — Návrh: one design doc + the EPIC→FEAT→TASK task plan (CR-V2-011); the Auditor's
#                         upfront review (CR-V2-013) surfaces at the post-Návrh schvaľovací bod.
#   * ``programovanie`` — Programovanie: the AI Agent's self-checking coding loop (CR-V2-012).
#   * ``verifikacia``   — Verifikácia: the Auditor's end verification — release-acceptance + adversarial
#                         spot-checks (CR-V2-014); a FAIL loops the fix back to the AI Agent.
#   * ``done``          — Hotovo (terminal; no actor). Deploy is OUT of the pipeline (per-customer, D6).
STAGE_ORDER: tuple[str, ...] = (
    "priprava",
    "navrh",
    "programovanie",
    "verifikacia",
    "done",
)
# Fast-Fix Lane phase path (design §2.4 "Fast-fix = dial at full-auto"): the lightweight lane skips the
# heavy Návrh + per-task work — the Manažér's directive IS the brief, so Príprava advances straight to
# Programovanie, and a settled Programovanie advances to a LIGHT Verifikácia (fix-works + no-regression,
# not the full release oracle). A subset of :data:`STAGE_ORDER`, so every member reuses the same
# :data:`STAGE_ACTOR` mapping below. (OQ-1: ``cr``/``bug`` flow_types dropped — only ``new_version`` +
# ``fast_fix`` survive.)
FAST_FIX_STAGE_ORDER: tuple[str, ...] = (
    "priprava",
    "programovanie",
    "verifikacia",
    "done",
)
# The AGENT on turn for each phase (design §2.1/§2.2). The AI Agent (doer) owns Príprava/Návrh/
# Programovanie with one warm context; the Auditor (independent verifier) owns Verifikácia. ``done`` has
# no actor (terminal). DB enum values use underscore (``ai_agent``/``auditor`` — CR-V2-001 ACTOR_VALUES);
# the charter filesystem slug uses a hyphen (``ai-agent``) — mapped in CR-V2-007, kept distinct here.
STAGE_ACTOR: dict[str, str] = {
    "priprava": "ai_agent",
    "navrh": "ai_agent",
    "programovanie": "ai_agent",
    "verifikacia": "auditor",
}
_VERIFY_RETRIES = 2
# Auditor fix-loop bound (v2 design §2.2 "Division of labour"; CR-V2-009). At Verifikácia, an Auditor FAIL
# verdict loops the fix back to the AI Agent (the Auditor only finds; the AI Agent fixes), the Auditor
# re-verifies, bounded to this many fix↔re-verify rounds; on the (n+1)-th still-failing round the build
# STOPS and escalates to the Manažér (design §2.2 (i)). PROVISIONAL home of the named constant
# CR-V2-014 wires into the runner's auto-chain backstop (R-AUTOCHAIN) once the Verifikácia loop exists.
AUDITOR_LOOP_MAX = 5
# (The v1 per-task ``_AUTO_FIX_RETRIES`` is RETIRED — CR-V2-012 replaced the per-task-audited build loop with
# the AI-Agent self-checking loop, whose own bound is :data:`_SELF_CHECK_RETRIES` defined beside it.)
# gate_g FAIL scope-escalation cap (CR-NS-056 §F1.5) — kept for the deferred-RED gate_g/Verifikácia
# round-runner (rebuilt in CR-V2-014). DISTINCT from the loop bounds above.
_MAX_SCOPE_ESCALATIONS_PER_ITERATION = 1
# Bounded re-invokes when the agent emits an unparseable <<<PIPELINE_STATUS>>>
# block (CR-NS-018). A single LLM JSON typo must not halt the pipeline; the
# agent runs ``--resume`` so a retry is a cheap re-emit, not a redo of the work.
# Distinct from ``_VERIFY_RETRIES`` (which retries a *valid* report that failed
# verification).
_PARSE_RETRIES = 2
# Upper bound on the total feats in an incrementally-generated task plan (v0.7.3, CR-1; v2 the plan folds
# into the Návrh phase — CR-V2-011). Each feat costs one bounded ``--resume`` per-feat pass, so this caps
# the multi-pass loop. A coarse-grained plan (module ≈ task) is well under this even for a large app;
# exceeding it signals an over-fine decomposition → fail-closed HALT (``blocked``), never a runaway loop.
MAX_PLAN_FEATS = 40
# The Manažér actions ``apply_action`` accepts (v2 design §4.4; CR-V2-009). The v1 11-stage/5-role verb
# set (approve / fix / leave / end_gate_e / end_build / continue_build / apply_coordinator_recommendation
# / rerun_release_audit / surgical_fix / uat_accept / retry_publish / accept_merged) collapses to the
# 4-phase schvaľovacie body:
#   * ``start``        — "Spustiť tvorbu špecifikácie": create the pipeline + begin Príprava.
#   * ``approve_spec`` — the ALWAYS-mandatory end-Príprava Špecifikácia approval (dial-independent; design
#                        §2.3, D3). Advances Príprava → Návrh.
#   * ``schvalit``     — "Schváliť": approve the current phase's output at a dial-governed schvaľovací bod
#                        (after Návrh / Programovanie / Verifikácia) → advance to the next phase / Hotovo.
#   * ``uprav``        — "Uprav": send the Manažér's correction back to the AI Agent at a schvaľovací bod
#                        (re-work the current phase); the phase does NOT advance.
#   * ``pokracovat``   — "Pokračovať": resume a build the Manažér paused (cooperative pause boundary).
#   * ``verdict``      — the Auditor's Verifikácia verdict (PASS → Hotovo; FAIL → loop fix to the AI Agent,
#                        bounded by :data:`AUDITOR_LOOP_MAX`, then escalate to the Manažér).
#   * ``ask``/``answer`` — direct Manažér↔AI Agent comms (the Coordinator relay is retired; design §2.2).
#   * ``pause``        — cooperatively pause the Programovanie loop at a task boundary.
# (Deploy is OUT of the pipeline — per-customer UAT/PROD actions live in the deploy subsystem, D6.)
_ACTIONS = frozenset(
    {
        "start",
        "approve_spec",
        "schvalit",
        "uprav",
        "pokracovat",
        "verdict",
        "ask",
        "answer",
        "pause",
    }
)
# Actions that act on / advance past an agent's output — only valid once the agent has SETTLED
# (CR-NS-018). Guarding these stops a stale board / double-click from advancing while the agent is
# mid-work (which would skip a mandatory schvaľovací bod). ``ask``/``answer``/``pause`` are NOT advancing
# (ask/answer thread input without advancing; pause is only meaningful while the agent works).
_ADVANCING_ACTIONS = frozenset(
    {
        "approve_spec",
        "schvalit",
        "uprav",
        "verdict",
        "pokracovat",
    }
)

# Per-phase backstop timeouts (seconds) for a single headless agent turn (CR-NS-018 fix-round; v2 4-phase
# CR-V2-009). Dispatch is async, so these only guard a *hung* agent. Programovanie is the heaviest single
# turn; Príprava/Návrh are read+produce; Verifikácia runs the release-acceptance smoke. Unknown phases
# fall back to the env-tunable ``claude_agent.CLAUDE_INVOKE_TIMEOUT``.
STAGE_TIMEOUT: dict[str, int] = {
    "priprava": 900,
    "navrh": 1200,
    "programovanie": 2400,
    "verifikacia": 1200,
}


def _timeout_for(stage: str) -> int:
    return STAGE_TIMEOUT.get(stage, claude_agent.CLAUDE_INVOKE_TIMEOUT)


def determine_available_actions(state: PipelineState) -> set[str]:
    """The Manažér actions valid to OFFER right now, derived from (current_stage, status) — WS-C1
    (CR-NS-030); rebuilt to the 4-phase model in CR-V2-009. The single backend source of truth for
    button presence, so the FE can't drift into no-op buttons.

    This is the (phase, status)-level offerable set — a subset of what :func:`apply_action` accepts.
    Finer payload/DB preconditions stay in apply_action (a non-empty comment for ``uprav``; a settled
    Auditor verdict). This set only removes the GROSS (phase, status) mismatches; the FE intersects it
    with finer message-derived conditions and falls back to its own logic when a field is absent.

    The schvaľovacie body the dial GOVERNS (``schvalit`` after Návrh / Programovanie / Verifikácia) are
    always OFFERED here at a settled phase — whether the build actually STOPS at one is the dial's call
    (:func:`dial_stops_at`, applied in the dispatch path), but once it has stopped the Manažér can act."""
    stage, status = state.current_stage, state.status

    if status == "agent_working":
        # Nothing to ratify while the agent works; only the Programovanie loop has a cooperative pause boundary.
        return {"pause"} if stage == "programovanie" else set()
    if status == "done":
        return set()
    if status == "paused":
        # A paused Programovanie loop: only the resume verb (CR-V2-009 collapses end_build away — a
        # paused build resumes via ``pokracovat`` or the Manažér steers it with ``uprav``).
        return {"pokracovat", "uprav"}

    # Settled (awaiting_manazer / blocked): ask + uprav are universally valid — ``uprav`` doubles as the
    # error-block "Skús znova" / re-work recovery at any phase, and ``ask`` opens a direct AI-Agent
    # consult. A blocked state is an agent QUESTION → the Manažér can ``answer`` it.
    actions: set[str] = {"ask", "uprav"}
    if status == "blocked":
        actions.add("answer")

    if stage == "priprava":
        # End-Príprava: the ALWAYS-mandatory Špecifikácia approval (dial-independent, design §2.3/D3).
        actions.add("approve_spec")
    elif stage in ("navrh", "programovanie"):
        # The dial-governed schvaľovacie body after Návrh / Programovanie — ``schvalit`` advances to the
        # next phase. (Whether the build HALTED here at all is the dial's call; once settled, it's offered.)
        actions.add("schvalit")
    elif stage == "verifikacia":
        # Verifikácia is the Auditor's phase: the Manažér ratifies the Auditor's verdict (``verdict``) and,
        # at the dial-governed end stop, signs off with ``schvalit`` → Hotovo.
        actions.update({"verdict", "schvalit"})

    return actions


def build_readiness(db: Session, version_id: uuid.UUID) -> tuple[bool, int]:
    """``(all_tasks_done, open_findings)`` for the build stage (WS-C1, CR-NS-030).

    ``determine_available_actions`` is state-only, so it cannot gate the DB-dependent build
    preconditions: approve@build is rejected while any task is ``todo`` (build not finished) or any is
    ``failed``/unverified (open finding); end_build is rejected while a finding is open. The board
    exposes these two facts so the FE can DISABLE "Schváliť build → Audit" / "Ukončiť build" when not
    satisfiable — mirroring the existing Gate E ``gate_e_open_findings`` gate — instead of offering a
    button that 400s. Cheap counts; the board computes them each fetch like ``_gate_e_open_findings``."""
    all_tasks_done = task_service.get_next_todo_task(db, version_id) is None
    return all_tasks_done, _build_open_findings(db, version_id)


class OrchestratorError(ValueError):
    """Invalid orchestration request (unknown version/action, missing payload)."""


# ---------------------------------------------------------------------------
# Resolution helpers
# ---------------------------------------------------------------------------


def _project_slug_for_version(db: Session, version_id: uuid.UUID) -> str:
    slug = db.execute(
        select(Project.slug).join(Version, Version.project_id == Project.id).where(Version.id == version_id)
    ).scalar_one_or_none()
    if slug is None:
        raise OrchestratorError(f"Version not found: {version_id}")
    return slug


def _resolve_dispatch_overrides(db: Session, version_id: uuid.UUID, role: str) -> tuple[Optional[str], Optional[str]]:
    """Resolve ``(model, effort)`` dispatch flags for ``role`` from the project owner's config (CR-NS-040).

    The version's project owner's ``user_agent_settings(role)`` row drives ``--model`` / ``--effort``
    (attribution = project owner: stable, reuses the existing owner join, aligns with the future
    per-user subscription). Graceful fallback — no owner / no row / unset field → no flag (today's
    exact behavior, ``scalar``-safe, never crashes) — EXCEPT the **Auditor effort, which scales with the
    Miera autonómie dial** (CR-V2-008 / AUTON-5 / OQ-9): when no explicit per-user effort is set, the
    Auditor's effort is :func:`auditor_effort_for_level` of the resolved dial (higher autonomy → deeper
    Auditor; the independent verifier is the safety net that compensates for fewer human stops). An
    explicit per-user Auditor effort still wins (the Manažér's deliberate choice overrides the coupling).
    Re-resolved on every :func:`invoke_agent` call, so parse-retries keep it.
    """
    row = db.execute(
        select(UserAgentSettings.model, UserAgentSettings.effort)
        .join(Project, Project.owner_id == UserAgentSettings.user_id)
        .join(Version, Version.project_id == Project.id)
        .where(Version.id == version_id, UserAgentSettings.agent_role == role)
    ).first()
    model = row.model if row is not None else None
    effort = row.effort if row is not None else None
    if effort is None and role == AUDITOR_ROLE:
        # OQ-9: no explicit per-user Auditor effort → derive it from the autonomy dial (inverse to human
        # oversight). Falls back to the dial default's effort (``max``) when the dial itself is unset.
        effort = auditor_effort_for_level(resolve_miera_autonomie(db, version_id))
    return model, effort


def _resolve_orch_session(db: Session, project_slug: str, role: str) -> tuple[uuid.UUID, bool]:
    """Return ``(claude_session_id, is_first)`` for ``(project_slug, role)``.

    Lazily creates the orchestrator_session row + a fresh claude UUID the first
    time a role is driven for a project (the UUID is shared across versions and
    Directors of that project).
    """
    row = db.execute(
        select(OrchestratorSession).where(
            OrchestratorSession.project_slug == project_slug,
            OrchestratorSession.role == role,
        )
    ).scalar_one_or_none()
    if row is not None:
        return row.claude_session_id, False
    new_uuid = uuid.uuid4()
    db.add(OrchestratorSession(project_slug=project_slug, role=role, claude_session_id=new_uuid))
    db.flush()
    return new_uuid, True


def _get_state(db: Session, version_id: uuid.UUID) -> Optional[PipelineState]:
    return db.execute(select(PipelineState).where(PipelineState.version_id == version_id)).scalar_one_or_none()


def _record_message(
    db: Session,
    *,
    version_id: uuid.UUID,
    stage: str,
    author: str,
    recipient: str,
    kind: str,
    content: str,
    status: str = "delivered",
    payload: Optional[dict[str, Any]] = None,
) -> PipelineMessage:
    msg = PipelineMessage(
        version_id=version_id,
        stage=stage,
        author=author,
        recipient=recipient,
        kind=kind,
        content=content,
        status=status,
        payload=payload,
    )
    db.add(msg)
    db.flush()
    return msg


def _directive_for(stage: str, flow_type: str = "new_version") -> str:
    """Minimal orchestrator directive for a stage. The agent reads its charter."""
    # Fast-Fix Lane kickoff (F-009 §3, CR-NS-094): the Coordinator's escalation guard. The Director's
    # directive rides in the kickoff message payload; the Coordinator triages it FIRST — small & obvious
    # (single concern, no multi-module / schema / new-dep, no requirement ambiguity) → confirm it's
    # fast-lane-suitable and await the Director's go to build (NO Designer, NO task_plan). Non-trivial →
    # STOP (kind=blocked) + a structured `coordinator_directive` proposing convert-to-full-version, never
    # proceeding on its own (reuse the flag-the-gap-and-STOP pattern).
    if stage == "kickoff" and flow_type == "fast_fix":
        return (
            "RÝCHLA OPRAVA (fast-fix lane, F-009): pokyn Directora (smernica) je VYŠŠIE v tomto brífe — je "
            "to TVOJ celý zadanie. Najprv ho zatrieď (escalation guard §3): je malý a jednoznačný (jeden "
            "koncept, žiadna multi-modul / schéma / nová závislosť zmena, žiadna nejasnosť požiadavky)?\n"
            "- ÁNO → potvrď, že je vhodný pre rýchlu opravu (NEnastavuj kind=blocked). Engine ťa "
            "AUTOMATICKY posunie do buildu — submission Directora JE autorizácia, NEčakaj na ďalšie "
            "schválenie. NEDISPATCHUJ Návrhára ani task_plan.\n"
            "- NIE (netriviálny: nejednoznačný, multi-modul, mení špecifikované správanie vyžadujúce návrh, "
            "schéma/dependency zmena) → ZASTAV: nepokračuj, nastav kind=blocked a pripoj štruktúrovaný "
            "`coordinator_directive` (triage_class=director_decision, proposed_action="
            "convert_to_full_version, rationale=prečo) navrhujúci konverziu na plnú verziu/pipeline.\n"
            "Ukonči odpoveď štruktúrovaným stavovým výstupom (F-007-orchestration-cockpit.md §5.3)."
        )
    # The task plan no longer flows through this generic directive — run_dispatch early-returns into the
    # Návrh round (CR-V2-011 _run_navrh_round), which folds the narrowed skeleton / per-feat passes
    # (_task_plan_skeleton_directive / _task_plan_feat_directive below) in after the design-doc turn.
    base = (
        f"Pokračuj fázou '{stage}' podľa autoritatívneho spec balíka a svojho charteru. "
        "Ukonči odpoveď štruktúrovaným stavovým výstupom (F-007-orchestration-cockpit.md §5.3)."
    )
    return base


def _version_spec_rel(version_number: str) -> str:
    """Relative repo path of a version's spec directory (``docs/specs/versions/v<N>``).

    Single source for the version-scoped spec-tree location the build artifacts live under (the
    ``customer-requirements.md`` Zadanie, the Príprava ``specification.md`` Špecifikácia, and the
    Návrh design doc + task plan). Mirrors the convention the metrics/footprint reads already use
    (:func:`_gate_e_question_budget`, ``_write_task_plan_doc``)."""
    return f"docs/specs/versions/v{version_number}"


#: Relative repo path of the Špecifikácia artifact the Príprava phase produces (CR-V2-010, PREP-3).
#: The AI Agent (which has Write tools in its warm ``claude`` session) writes the Markdown spec here at
#: the end of the Príprava dialogue and lists it in ``deliverables[]``; the engine verifies it exists +
#: records it as the durable Príprava artifact (the manager's reading view in the Vývoj → Príprava tab).
def _priprava_spec_rel(version_number: str) -> str:
    return f"{_version_spec_rel(version_number)}/specification.md"


#: Relative repo path of the Návrh design document the Návrh phase produces (CR-V2-011, NAVRH-1/NAVRH-2).
#: The AI Agent writes ONE coherent design `.md` here (overview / data model / API / BE+FE, sized to the
#: project) and lists it in ``deliverables[]``; the EPIC→FEAT→TASK task plan is the design doc's LAST part
#: (folded in via the incremental skeleton/per-feat passes — design §2.1(2)). The engine verifies the doc
#: exists + records it as the durable Návrh artifact (the Vývoj → Návrh tab reading view). Mirrors the
#: ``specification.md`` convention the Príprava phase uses.
def _navrh_design_doc_rel(version_number: str) -> str:
    return f"{_version_spec_rel(version_number)}/design.md"


def _priprava_directive(db: Session, version_id: uuid.UUID) -> str:
    """The Príprava phase brief (CR-V2-010; PREP-1..PREP-4, RULES-3 read-first/ask-until-understood).

    DESIGN-BEARING (flagged for the Manažér): this prompt DEFINES the AI Agent's Príprava behaviour —
    the interactive Zadanie→Špecifikácia dialogue. Drafted from ``nex-studio-v2-design.md`` §2.1 / §5.1(1).
    The agent's own ``Pravidlá agenta`` charter (templates/ai-agent-charter.md §2) carries the matching
    rules; this is the per-turn orchestrator injection that names the concrete Zadanie + Špecifikácia paths.

    The init prompt ("Načítaj zadanie a začni prípravu špecifikácie" — design §2.1) tells the AI Agent to:
      1. READ the Zadanie (``customer-requirements.md``) + existing code / specs / KB (read-before-you-think);
      2. systematize the requirements and ASK the Manažér clarifying questions on EVERY unclear /
         under-thought point — NO design until every detail is understood (set ``kind=question`` and STOP);
      3. proactively PROPOSE improvements (features / UX / quality), the professional taking responsibility;
      4. when (and only when) every detail is understood, WRITE the Špecifikácia as Markdown to the version
         spec path and list it in ``deliverables[]``, closing the phase with ``kind=gate_report``. The
         end-Príprava ``Schváliť špecifikáciu`` stop is ALWAYS mandatory (dial-independent) — Návrh cannot
         begin until the Manažér approves the Špecifikácia.
    """
    version_number = db.execute(select(Version.version_number).where(Version.id == version_id)).scalar_one()
    zadanie_rel = f"{_version_spec_rel(version_number)}/customer-requirements.md"
    spec_rel = _priprava_spec_rel(version_number)
    return (
        "Načítaj zadanie a začni prípravu špecifikácie (fáza Príprava).\n"
        f"1. NAČÍTAJ Zadanie (`{zadanie_rel}`) + existujúci kód, špecifikácie a KB — read before you think.\n"
        "2. SYSTEMATIZUJ požiadavky a pýtaj sa Manažéra objasňujúce otázky na KAŽDÝ nejasný / nedomyslený "
        "bod. ŽIADNY návrh, kým nie je každý detail pochopený — keď niečo nie je jasné, nastav "
        "`kind=question`, polož otázku (`question`) a ZASTAV (neprodukuj špecifikáciu naslepo).\n"
        "3. PROAKTÍVNE navrhni vylepšenia (features / UX / kvalita) — profesionál preberá zodpovednosť za "
        "výsledok, amatérsky vstup (Zadanie) je len východisko (waterfall filozofia).\n"
        "4. Až keď je KAŽDÝ detail pochopený: zapíš Špecifikáciu ako Markdown do "
        f"`{spec_rel}` (vytvor adresár ak treba) a uveď ju v `deliverables[]`. Špecifikácia je profesionálny "
        "dokument (prehľad, funkcie/riešenia, dátový model, API, BE+FE, hraničné prípady) nadimenzovaný "
        "podľa projektu. Ukonči kolo `kind=gate_report`.\n"
        "Schválenie Špecifikácie Manažérom (`Schváliť špecifikáciu`) je VŽDY povinné a nezávislé od Miery "
        "autonómie — Návrh sa nezačne, kým ju Manažér neschváli.\n"
        "Ukonči odpoveď štruktúrovaným stavovým výstupom (F-007-orchestration-cockpit.md §5.3)."
    )


def _navrh_directive(db: Session, version_id: uuid.UUID) -> str:
    """The Návrh phase design-doc brief (CR-V2-011; NAVRH-1..NAVRH-4, ARCH-2).

    DESIGN-BEARING (flagged for the Manažér): this prompt DEFINES the AI Agent's Návrh behaviour — produce
    ONE coherent design document, "like Dedo", NOT a multi-doc tree. Drafted from ``nex-studio-v2-design.md``
    §2.1(2) / §5.1(2). The agent's ``Pravidlá agenta`` charter (templates/ai-agent-charter.md) carries the
    matching rules; this is the per-turn orchestrator injection naming the concrete Špecifikácia + design-doc
    paths.

    Drives the DESIGN-DOC turn only — the EPIC→FEAT→TASK task plan (the design doc's LAST part) is generated
    SEPARATELY via the folded incremental skeleton/per-feat passes (:func:`_run_navrh_round`), so a large plan
    never overflows one turn (no parse exhaustion). The brief therefore tells the AI Agent to:
      1. READ the approved Špecifikácia (``specification.md``) + the Zadanie + existing code / KB;
      2. WRITE ONE coherent design ``.md`` to the version spec path — sections SIZED to the project (overview/
         goal · data model · API/interfaces · BE+FE design — only as much as needed; depth is the agent's
         judgment), list it in ``deliverables[]``;
      3. close the design-doc turn with ``kind=done`` — the engine then folds the task plan in (the agent does
         NOT cram the whole EPIC→FEAT→TASK tree into this status block);
      4. if any design detail is still ambiguous, ASK the Manažér (``kind=question``) and STOP — the post-Návrh
         schvaľovací bod surfaces these clarification questions (the Auditor's upfront review hooks here in
         CR-V2-013).
    """
    version_number = db.execute(select(Version.version_number).where(Version.id == version_id)).scalar_one()
    spec_rel = _priprava_spec_rel(version_number)
    design_rel = _navrh_design_doc_rel(version_number)
    return (
        "Pokračuj fázou Návrh: vytvor JEDEN koherentný návrhový dokument (ako Dedo), NIE strom viacerých "
        "dokumentov.\n"
        f"1. NAČÍTAJ schválenú Špecifikáciu (`{spec_rel}`) + Zadanie + existujúci kód a KB.\n"
        f"2. ZAPÍŠ jeden návrhový dokument ako Markdown do `{design_rel}` (vytvor adresár ak treba) a uveď ho "
        "v `deliverables[]`. Sekcie NADIMENZUJ podľa projektu (prehľad/cieľ · dátový model · API/rozhrania · "
        "BE+FE návrh — len toľko, koľko treba; hĺbka je tvoj profesionálny úsudok: malé → ľahké, zložité → "
        "dôkladné).\n"
        "3. Plán úloh (EPIC → FEAT → TASK) je POSLEDNÁ časť návrhu, ale NEVkladaj ho do tohto stavového "
        "bloku — engine ho doplní samostatnými prechodmi (kostra + úlohy po funkciách), aby sa veľký plán "
        "nezlomil. Tento ťah UZAVRI `kind=done` (návrhový dokument je hotový).\n"
        "4. Ak je akýkoľvek detail návrhu ešte nejednoznačný, nastav `kind=question`, polož otázku Manažérovi "
        "a ZASTAV — schvaľovací bod po Návrhu tvoje otázky vynesie.\n"
        "Ukonči odpoveď štruktúrovaným stavovým výstupom (F-007-orchestration-cockpit.md §5.3)."
    )


# E5 (CR-NS-045): the per-task human-effort estimate is the metrics page's human-baseline source — kept
# in BOTH task_plan prompts below (skeleton → feat-level Σ; per-feat → per-task), advisory, never blocking.
_TASK_PLAN_ESTIMATE_NOTE = (
    "`estimated_minutes` = realistický odhad práce pre schopného ĽUDSKÉHO vývojára v minútach "
    "(NIE čas AI výpočtu); ADVISORY pole — chýbajúci odhad je povolený a NIKDY neblokuje build."
)
# TEXT/FENCE EXTRACTION (CR-1, live root-cause 2026-06-18): ``--json-schema`` does NOT yield a
# ``structured_output`` field in this CLI — the model emits TEXT. So the narrowed passes carry their JSON
# in a DEDICATED ``<<<TASK_PLAN_JSON>>>`` sentinel fence (extracted by ``extract_task_plan_json``). The
# directive must pin the EXACT field names (the live model drifted to ``features``/``id``/``project``) and
# forbid extras, or the tolerant parser would have nothing valid to map.
_TASK_PLAN_FENCE_RULE = (
    "Výstup vráť VÝHRADNE ako jeden JSON objekt vnútri tohto sentinel bloku (nič iné okolo, žiaden "
    "markdown, žiaden komentár):\n<<<TASK_PLAN_JSON>>>\n{…}\n<<<END_TASK_PLAN_JSON>>>\n"
    "Použi PRESNE tieto názvy polí a ŽIADNE iné — nikdy nie `project`/`version`/`level`/`id`/`features`."
)
# Concrete minimal examples (exact field names) — the model copies the SHAPE, not the content.
_SKELETON_EXAMPLE = (
    "Príklad tvaru:\n<<<TASK_PLAN_JSON>>>\n"
    '{"epics":[{"title":"Foundation","feats":['
    '{"title":"Schéma a migrácie","description":"DB schéma + audit log","estimated_minutes":120}]}],'
    '"cross_cutting_rules":"Spoločná transakčná hranica; immutable audit; scoping na firmu."}\n'
    "<<<END_TASK_PLAN_JSON>>>"
)
_FEAT_TASKS_EXAMPLE = (
    "Príklad tvaru:\n<<<TASK_PLAN_JSON>>>\n"
    '{"tasks":[{"title":"GL tabuľky","task_type":"migration","description":"hlavná kniha + saldokonto",'
    '"checklist_type":null,"priority":"normal","estimated_minutes":90}]}\n'
    "<<<END_TASK_PLAN_JSON>>>"
)


def _task_plan_skeleton_directive(director_note: Optional[str] = None) -> str:
    """Pass 1 prompt (v0.7.3, CR-1; v2 CR-V2-011 — folds into Návrh): the AI Agent emits the EPIC + FEAT
    **skeleton** only — NO tasks, in a ``<<<TASK_PLAN_JSON>>>`` sentinel fence (``structured_output`` is dead
    in this CLI — see the fence rule).

    Bounded so a large design's tree never overflows one turn (the per-feat tasks come in their own
    passes). On a Manažér ``uprav`` (re-plan) the framed comment is prepended so the AI Agent applies the
    edit on the resumed warm session, not a blind re-plan.
    """
    base = (
        "Doplň POSLEDNÚ časť návrhu — plán úloh. Najprv vytvor jeho KOSTRU: emituj IBA epiky a funkcie "
        "(EPIC + FEAT), BEZ úloh. "
        "Objekt má pole `epics` (zoznam): KAŽDÝ epik má `title` a pole "
        "`feats` (zoznam, ≥1) — KAŽDÁ funkcia má `title`, `description` a `estimated_minutes` (Σ odhadov "
        "jej úloh). Navrch objektu pole `cross_cutting_rules` (markdown, regulované invarianty knihy, "
        "kodifikované RAZ). Úlohy NEemituj — doplnia sa v ďalších prechodoch po jednej funkcii. "
        + _TASK_PLAN_ESTIMATE_NOTE
        + "\n\n"
        + _TASK_PLAN_FENCE_RULE
        + "\n\n"
        + _SKELETON_EXAMPLE
    )
    if director_note:
        return f"{director_note}\n\n{base}"
    return base


def _task_plan_feat_directive(feat_title: str) -> str:
    """Passes 2..N prompt (v0.7.3, CR-1; v2 CR-V2-011): the AI Agent emits ONLY one feat's tasks, in a
    ``<<<TASK_PLAN_JSON>>>`` sentinel fence.

    Runs on the resumed warm AI-Agent session, so the full design doc + the just-emitted skeleton stay in
    context; the orchestrator grafts the returned tasks onto the matching skeleton feat.
    """
    return (
        f"Pre funkciu „{feat_title}“ z kostry plánu emituj IBA jej úlohy. Objekt má jedno pole `tasks` "
        "(zoznam, ≥1): KAŽDÁ úloha má `title`, `task_type` (jedno z: backend, frontend, migration, test, "
        "docs), `description`, `checklist_type` (text alebo null), `priority` (normal | high | urgent) a "
        "`estimated_minutes`. Granularita HRUBOZRNNÁ — modul ≈ úloha (F-007 §4); nedeľ koherentný modul. "
        + _TASK_PLAN_ESTIMATE_NOTE
        + "\n\n"
        + _TASK_PLAN_FENCE_RULE
        + "\n\n"
        + _FEAT_TASKS_EXAMPLE
    )


def _prepend_fast_fix_directive(db: Session, version_id: uuid.UUID, prompt: str) -> str:
    """Prepend the Director's fast-fix directive onto the Coordinator's **kickoff** brief (F-009 §1,
    CR-NS-097). The kickoff agent runs a FRESH session (start deletes the project's sessions, so there is
    no thread to ``--resume``) — the brief is its ONLY context. Without the directive in the brief the
    escalation-guard triage is blind (the live run asked "chýba samotný popis toho, čo mám opraviť"). A
    no-op when no directive is recorded (the brief's generic triage instruction still stands)."""
    directive = fast_fix.kickoff_directive(db, version_id)
    if not directive:
        return prompt
    return f"## Pokyn Directora (smernica na rýchlu opravu)\n\n{directive}\n\n---\n\n{prompt}"


def _augment_brief_with_backlog(db: Session, version_id: uuid.UUID, stage: str, prompt: str) -> str:
    """Prepend the version's ``included`` backlog items to the Designer's **gate_a** brief (E2, CR-NS-042).

    Orchestrator-side only — NO agent API call. gate_a is the Designer's FIRST dispatch (where it authors
    the version's customer-requirements); injecting once here makes the Designer design the assigned backlog
    items as the version's requirements. Once-only by design — gate_b/c/d read what gate_a wrote, so there is
    no re-injection → no drift. A no-op for any other stage, or a version with no ``included`` items.
    """
    if stage != "gate_a":
        return prompt
    items = (
        db.execute(
            select(BacklogItem)
            .where(BacklogItem.version_id == version_id, BacklogItem.status == "included")
            .order_by(BacklogItem.number.asc())
        )
        .scalars()
        .all()
    )
    if not items:
        return prompt
    lines = [
        "## Zákaznícke požiadavky (z backlogu)",
        "",
        "Tieto požiadavky boli priradené k tejto verzii — navrhni ich ako jej zákaznícke požiadavky:",
        "",
    ]
    for it in items:
        line = f"- **REQ-{it.number}: {it.title}**"
        if it.description:
            line += f" — {it.description}"
        lines.append(line)
    return "\n".join(lines) + "\n\n---\n\n" + prompt


def directive_for_action(action: str, payload: dict[str, Any], stage: str) -> Optional[str]:
    """Frame the Manažér's interactive message for the re-dispatch prompt, else ``None`` (CR-V2-009).

    For ``uprav`` / ``ask`` / ``answer`` the Manažér's content MUST reach the agent (CR-NS-018) —
    otherwise the re-dispatched agent re-runs blind on the generic phase directive ("nič sa nezmenilo").
    For a fresh-phase dispatch (``start`` / ``approve_spec`` / ``schvalit`` / ``verdict`` / ``pokracovat``)
    there is no Manažér-specific instruction → ``None``, and the caller falls back to
    :func:`_directive_for`. The agent runs ``--resume`` (full thread), so the framed line lands in context.
    """
    if action == "uprav":
        comment = str(payload.get("comment", "")).strip()
        return f"Manažér ťa vrátil na úpravu fázy '{stage}': {comment}" if comment else None
    if action == "ask":
        text = str(payload.get("text", "")).strip()
        return f"Manažér sa pýta: {text}" if text else None
    if action == "answer":
        text = str(payload.get("text", "")).strip()
        return f"Manažér odpovedal na tvoju otázku: {text}" if text else None
    return None


def latest_coordinator_report(db: Session, version_id: uuid.UUID) -> Optional[str]:
    """RETIRED in v2 (CR-V2-009): always ``None`` for a 4-phase build. Queries for an ``author=coordinator``
    message — a participant value that no longer exists (CR-V2-001 collapsed to ai_agent/auditor/manazer/
    system), so it never matches a v2 message. The v1 ``apply_coordinator_recommendation`` action it fed is
    removed; this symbol is kept only so the deferred-RED v1 tests still collect (C/D drops both)."""
    return db.execute(
        select(PipelineMessage.content)
        .where(
            PipelineMessage.version_id == version_id,
            PipelineMessage.author == "coordinator",
            PipelineMessage.kind == "gate_report",
        )
        .order_by(PipelineMessage.seq.desc())
        .limit(1)
    ).scalar_one_or_none()


def _latest_customer_gate_report(db: Session, version_id: uuid.UUID) -> Optional[PipelineMessage]:
    """Most recent Customer ``gate_report`` for a version's Gate E (or ``None``).

    Author + stage filtered, ordered by the monotonic ``seq``. Its payload carries
    the Gate E boundary signals (``coverage_complete``, ``findings``, ``topic_done``)
    that drive the boundary actions (F-007-gate-e §3/§4): topic boundary vs final
    sign-off, and the open-finding gate that blocks closing.
    """
    return db.execute(
        select(PipelineMessage)
        .where(
            PipelineMessage.version_id == version_id,
            PipelineMessage.author == "customer",
            PipelineMessage.stage == "gate_e",
            PipelineMessage.kind == "gate_report",
        )
        .order_by(PipelineMessage.seq.desc())
        .limit(1)
    ).scalar_one_or_none()


def _latest_uat_deploy(db: Session, version_id: uuid.UUID) -> Optional[dict[str, Any]]:
    """The most recent ``uat_deploy`` notification payload for a version, or ``None`` if no UAT deploy was
    ever attempted (v0.8.1 CR-2).

    Both lanes record ``{"uat_deploy": {...}}`` ``system→director`` notifications — full flow:
    :func:`_release_auto_uat_deploy`; fast-fix: :func:`_fast_fix_auto_deploy` — as a real success
    (``{ok: True}``), a failure (``{ok: False}``), or a skip (``{skipped: True}``). ``uat_accept`` reads
    this to report HONESTLY whether a UAT was ACTUALLY deployed, instead of the ``uat_slug`` proxy (which
    lies when a configured slug's compose is gone — CR-1 honest-skips, yet the slug stays set). Ordered by
    the monotonic ``seq`` so the latest deploy outcome wins; ``None`` when no deploy was ever recorded
    (e.g. a ``cr``/``bug`` release, which never deploys to UAT)."""
    rows = (
        db.execute(
            select(PipelineMessage.payload)
            .where(
                PipelineMessage.version_id == version_id,
                PipelineMessage.author == "system",
                PipelineMessage.kind == "notification",
            )
            .order_by(PipelineMessage.seq.desc())
        )
        .scalars()
        .all()
    )
    for payload in rows:
        if isinstance(payload, dict) and isinstance(payload.get("uat_deploy"), dict):
            return payload["uat_deploy"]
    return None


def _uat_render_needs_reprovision(db: Session, version_id: uuid.UUID) -> bool:
    """Whether the engine must RE-PROVISION (not just re-``up``) the UAT render before redeploying (H2, CR-2).

    Today an EXISTING-but-broken render is re-``up``-ed verbatim on every retry → identical failure (the
    nex-manager dogfood case). This self-heals it WITHOUT clobbering a working UAT. Reads the LATEST
    ``uat_deploy`` notification (the same one :func:`_latest_uat_deploy` surfaces) plus its ``seq``:

    * ``ok is False`` (the deploy FAILED — the proven broken-render case) → ``True`` (NARROW core).
    * ``ok is True`` → ``True`` **iff** the deploy note's seq is BEFORE the current iteration boundary
      (:func:`_iteration_boundary_seq`, the latest ``verdict`` seq — the SAME anchor
      :func:`_release_acceptance_satisfied` uses). A current-iteration successful deploy is recorded AFTER
      that boundary verdict, so its seq > boundary → ``False`` (the working UAT is preserved); a successful
      deploy from a PRIOR iteration has a newer verdict past it → seq < boundary → ``True`` (the render is
      stale w.r.t. the new code → re-render, idempotent, secrets preserved). For the fast-fix lane (no
      gate_g verdict) the boundary is 0, so any ``ok is True`` note is treated as current-iteration → preserved.
    * no deploy ever recorded / a ``skipped`` / an indeterminate note → the note says nothing about the
      on-disk render, so the **3rd trigger** (CR-R2-2, :func:`_existing_render_fails_h1`) self-heals the
      nex-manager orphan: an EXISTING render whose on-disk ``.env`` fails the H1 driver↔URL pair → ``True``;
      a render that PASSES H1 (or no render on disk) → ``False`` (nothing to heal).
    """
    rows = db.execute(
        select(PipelineMessage.seq, PipelineMessage.payload)
        .where(
            PipelineMessage.version_id == version_id,
            PipelineMessage.author == "system",
            PipelineMessage.kind == "notification",
        )
        .order_by(PipelineMessage.seq.desc())
    ).all()
    deploy_seq: Optional[int] = None
    deploy: Optional[dict[str, Any]] = None
    for seq, payload in rows:
        if isinstance(payload, dict) and isinstance(payload.get("uat_deploy"), dict):
            deploy_seq, deploy = seq, payload["uat_deploy"]
            break
    if deploy is not None and not deploy.get("skipped"):
        if deploy.get("ok") is False:
            return True  # the deploy FAILED — re-render the broken render (NARROW core).
        if deploy.get("ok") is True:
            # A current-iteration success is recorded AFTER the boundary verdict (seq > boundary) → preserved
            # (working UAT); a prior-iteration success has a newer verdict past it (seq < boundary) → stale.
            return deploy_seq < _iteration_boundary_seq(db, version_id)
    # 3rd trigger (CR-R2-2): no deploy note / a skip note / an indeterminate note — the note tells us nothing
    # about the on-disk render. Self-heal the nex-manager orphan: an EXISTING render whose on-disk .env FAILS
    # the H1 driver↔URL pair (a skip note but a non-importable DATABASE_URL that would otherwise be re-`up`-ed
    # verbatim). Reuses H1 verbatim; a render that PASSES H1 stays untouched (predicate stays False).
    return _existing_render_fails_h1(db, version_id)


def _existing_render_fails_h1(db: Session, version_id: uuid.UUID) -> bool:
    """Whether an EXISTING UAT render's on-disk ``.env`` FAILS the H1 driver↔URL validator pair (CR-R2-2).

    The 3rd :func:`_uat_render_needs_reprovision` trigger — the nex-manager orphan signature: a skip / no
    deploy note, yet ``/opt/uat/<uat_slug>/.env`` carries a non-importable ``DATABASE_URL`` (bare
    ``postgresql://`` while the source ships pg8000) that :func:`_run_uat_deploy` would re-``up`` verbatim →
    identical failure. Reuses H1 VERBATIM (``detect_sqlalchemy_pg_drivers`` on the source project +
    ``validate_rendered_db_drivers`` on the rendered ``.env``) — no new validation logic. ``False`` when the
    project is unresolvable, the UAT compose / ``.env`` is absent or unreadable, or the render PASSES H1
    (preserve-working-UAT). NEVER raises."""
    project = db.execute(
        select(Project).join(Version, Version.project_id == Project.id).where(Version.id == version_id)
    ).scalar_one_or_none()
    if project is None:
        return False
    try:
        uat_slug = project.uat_slug or uat_provisioner.derive_uat_slug(project)
    except (ValueError, TypeError):
        return False
    if not _uat_compose_exists(uat_slug):
        return False
    env_path = UAT_ROOT / uat_slug / ".env"
    if not env_path.is_file():
        return False
    try:
        env_text = env_path.read_text(encoding="utf-8")
    except OSError:
        return False
    declared = uat_provisioner.detect_sqlalchemy_pg_drivers(claude_agent.PROJECTS_ROOT / project.slug)
    fail_msgs, _ = uat_provisioner.validate_rendered_db_drivers(env_text, declared, project_slug=project.slug)
    return bool(fail_msgs)


# CR-R2-1 (#1b): the flows that actually deploy a UAT (record a ``uat_deploy`` note) — full flow
# :func:`_release_auto_uat_deploy` (``new_version``) + fast-fix :func:`_fast_fix_auto_deploy` (``fast_fix``).
# ``cr`` / ``bug`` releases NEVER deploy a UAT, so the no-silent-done-without-UAT guard must NOT fire for them
# — it would be unremediable (``retry_publish`` is ``new_version``-only), leaving the version impossible to
# finish. The guard is therefore gated on this set.
_UAT_DEPLOYING_FLOWS: frozenset[str] = frozenset({"new_version", "fast_fix"})


def _project_is_deployable(db: Session, version_id: uuid.UUID) -> bool:
    """Whether the version's project is STRUCTURALLY deployable — its source compose ships BOTH a backend
    and a db service (CR-R2-1 #1b).

    Deployability is keyed on the actual compose structure, NOT the ``uat_slug`` proxy: after #1a every
    project carries a ``uat_slug``, so the proxy would over-block a pure-CLI/lib project. A backend+db stack
    is the signature of an app that MUST have a live UAT before it can be marked done; a pure-lib project
    (no backend+db) returns ``False`` → it completes normally (the honest "bez UAT testu" branch). Any
    resolution / parse failure (no project, no ``source_path``, missing or unparseable compose) → ``False``
    (never block on an indeterminate structure)."""
    project = db.execute(
        select(Project).join(Version, Version.project_id == Project.id).where(Version.id == version_id)
    ).scalar_one_or_none()
    if project is None or not project.source_path:
        return False
    try:
        compose = uat_provisioner.load_source_compose(Path(project.source_path))
        roles = uat_provisioner.identify_service_roles(compose["services"])
    except Exception:  # noqa: BLE001 — an indeterminate compose must never block completion.
        return False
    return roles["backend"] is not None and roles["db"] is not None


def _gate_e_open_findings(db: Session, version_id: uuid.UUID) -> int:
    """Count of unresolved Gate E gaps — DETERMINISTIC from the orchestrator's own log,
    NOT the Customer's self-reported ``findings`` array (F-007-gate-e §5).

    A gap is RAISED by a Designer answer with ``payload.gap_found`` and RESOLVED by a
    Director ``fix`` / ``leave`` decision (tagged ``payload.resolves_gap``). open =
    ``max(0, raised − resolved)``. Consults (Coordinator revise) set neither marker, so
    they never perturb the count; content strings are never matched. A non-zero count
    blocks closing Gate E (final approve or early-end) — the gate no longer depends on
    how the Customer phrases its summary."""
    rows = (
        db.execute(
            select(PipelineMessage).where(PipelineMessage.version_id == version_id, PipelineMessage.stage == "gate_e")
        )
        .scalars()
        .all()
    )
    # A gap is raised only by a Designer's REVIEW answer (Q&A loop) — never by the fix
    # EDIT turn (``is_fix_edit``), which merely applies an approved fix. This makes the
    # count robust even if the edit turn's status block erroneously carries gap_found (§5).
    raised = sum(
        1
        for m in rows
        if m.author == "designer"
        and m.kind == "answer"
        and m.payload
        and m.payload.get("gap_found")
        and not m.payload.get("is_fix_edit")
    )
    resolved = sum(1 for m in rows if m.author == "director" and m.payload and m.payload.get("resolves_gap"))
    return max(0, raised - resolved)


# PIPELINE-AUTONOMY Phase 3 (design docs/architecture/pipeline-autonomy.md §2.1): the Gate E question
# budget scales with the version's SPEC footprint — the only artifact that exists at gate_e (task_plan is
# the NEXT stage, so it CANNOT drive the depth — adversarial Issue 8). A small tweak → a few questions to
# the touched spots; a greenfield → a full walk, capped by the ceiling. floor = the MINIMUM review depth
# (Gate E exists to catch spec gaps — under-review is the opposite failure); ceiling = the upper bound on
# the AUTONOMOUS Branch-A run — reaching it ESCALATES to the Director (extend or close), NEVER silent-closes
# (§2.1, the threshold-downgrade anti-pattern). Both clamp to absolute floors/caps, so a missing/unreadable
# spec tree (tests / no repo) degrades to a sane small budget — never 0, never unbounded.
_GATE_E_SPEC_LINES_PER_FLOOR_Q = 500  # one floor question per ~500 lines of spec footprint
_GATE_E_FLOOR_MIN = 3
_GATE_E_FLOOR_MAX = 10
_GATE_E_CEILING_MULTIPLE = 3  # ceiling = floor × 3 — headroom for legitimate deep review before escalating
_GATE_E_CEILING_MIN = 6
_GATE_E_CEILING_MAX = 30
# Topic-boundary slack for the runner's auto-chain backstop (:func:`auto_chain_limit`): the Customer also
# auto-continues across clean topic boundaries (not just questions), and a boundary is not a question, so it
# does not consume the question budget. This bounds how many boundary continues the backstop tolerates above
# the question ceiling before it trips — a degenerate boundary-only loop (no questions, no coverage_complete)
# is an agent bug, caught here exactly as a runaway chain is today.
_GATE_E_TOPIC_SLACK = 12


def _gate_e_spec_footprint_lines(db: Session, version_id: uuid.UUID) -> int:
    """Total line count of the version's spec markdown tree — a DETERMINISTIC scope proxy for the Gate E
    question budget (§2.1). Reads ``docs/specs/versions/v<X>/**/*.md`` in the orchestrated repo (the Gate A
    ``development-spec.md`` + the BE/FE spec package + ``customer-requirements.md``). Returns 0 when the repo
    or the spec dir is absent (tests / fresh project) — the caller clamps that to the floor, never crashes."""
    slug = _project_slug_for_version(db, version_id)
    version_number = db.execute(select(Version.version_number).where(Version.id == version_id)).scalar_one_or_none()
    if version_number is None:
        return 0
    spec_dir = claude_agent.PROJECTS_ROOT / slug / "docs" / "specs" / "versions" / f"v{version_number}"
    if not spec_dir.exists():
        return 0
    total = 0
    for path in sorted(spec_dir.rglob("*.md")):
        try:
            with path.open("r", encoding="utf-8", errors="ignore") as fh:
                total += sum(1 for _ in fh)
        except OSError:  # an unreadable file degrades to skip, never crashes the budget
            continue
    return total


def _gate_e_question_budget(db: Session, version_id: uuid.UUID) -> tuple[int, int]:
    """``(floor, ceiling)`` for the Gate E question budget, scaled to the version's spec footprint (§2.1).

    floor = the minimum questions a healthy review asks (clamped to ``[_GATE_E_FLOOR_MIN, _GATE_E_FLOOR_MAX]``);
    ceiling = the upper bound on the autonomous Branch-A run (``floor × _GATE_E_CEILING_MULTIPLE``, clamped to
    ``[_GATE_E_CEILING_MIN, _GATE_E_CEILING_MAX]`` and never below ``floor``). A small spec → small floor + small
    ceiling (few questions); a large spec → larger budget, still bounded. Reaching the ceiling ESCALATES to the
    Director, it never silent-closes (the floor/ceiling-with-escalation semantics, design §2.1)."""
    lines = _gate_e_spec_footprint_lines(db, version_id)
    floor = min(_GATE_E_FLOOR_MAX, max(_GATE_E_FLOOR_MIN, lines // _GATE_E_SPEC_LINES_PER_FLOOR_Q))
    ceiling = min(_GATE_E_CEILING_MAX, max(_GATE_E_CEILING_MIN, floor * _GATE_E_CEILING_MULTIPLE))
    return floor, max(ceiling, floor)


def _gate_e_question_count(db: Session, version_id: uuid.UUID) -> int:
    """How many Customer questions Gate E has asked so far — the budget unit (§2.1). A Customer ``question``
    turn is recorded ``author='customer'`` ∧ ``kind='question'`` (a ``blocked`` Customer block maps to
    ``question`` too, :func:`invoke_agent`); topic boundaries (``gate_report``) are NOT questions and never
    count. Deterministic from the message log, like :func:`_gate_e_open_findings`."""
    return db.execute(
        select(func.count())
        .select_from(PipelineMessage)
        .where(
            PipelineMessage.version_id == version_id,
            PipelineMessage.stage == "gate_e",
            PipelineMessage.author == "customer",
            PipelineMessage.kind == "question",
        )
    ).scalar_one()


def auto_chain_limit(db: Session, version_id: uuid.UUID) -> int:
    """Upper bound for the runner's auto-chain backstop (:mod:`backend.services.pipeline_runner`).

    PROVISIONAL 4-phase bound (CR-V2-009, R-AUTOCHAIN). The v1 bound budgeted the full 11-stage waterfall
    PLUS the Gate-E self-loop question ceiling PLUS topic slack — but the 4-phase model has NO Gate-E
    self-loop, so that slack is dropped. The new non-monotonic loop is the Auditor's bounded fix↔re-verify
    cycle (Verifikácia FAIL → Programovanie → Verifikácia, up to :data:`AUDITOR_LOOP_MAX` rounds), which
    only fully exists after CR-V2-014. So this CR sets a provisional bound = ``len(STAGE_ORDER)`` (the
    monotonic phase advance) + an Auditor-loop margin; **CR-V2-014 finalizes it** by wiring the named
    ``AUDITOR_LOOP_MAX`` term once the Verifikácia loop is implemented, so a legitimately deep (but bounded)
    Auditor loop does not mis-trip the backstop. A pure runaway backstop — every real path settles well
    before it. fast_fix is unaffected (its chain is ≤3, far under any bound). The ``db``/``version_id`` args
    are kept (the runner calls it per-version) for CR-V2-014, which may scale the margin per build."""
    # Each Auditor FAIL round re-enters Programovanie then Verifikácia → 2 phase steps per round; budget
    # AUDITOR_LOOP_MAX such rounds on top of the monotonic phase advance.
    return len(STAGE_ORDER) + 2 * AUDITOR_LOOP_MAX


def _verifikacia_passed(db: Session, version_id: uuid.UUID) -> bool:
    """Whether the Auditor's LATEST Verifikácia verdict is PASS (CR-V2-009 — no-silent-done invariant).

    Hotovo is reachable ONLY through a recorded Auditor PASS verdict at Verifikácia: ``schvalit`` at the
    Verifikácia end-stop is gated on this, never a silent sign-off. Deterministic from the message log —
    the most recent ``stage=verifikacia`` ∧ ``kind=verdict`` message whose ``payload.verdict == 'PASS'``.
    (v2 form of the v1 ``no-silent-done-without-UAT`` safeguard: deploy is OUT of the pipeline — D6/OQ-3 —
    so the gate becomes ``no-silent-done-without-VERIFICATION``.)"""
    latest = db.execute(
        select(PipelineMessage)
        .where(
            PipelineMessage.version_id == version_id,
            PipelineMessage.stage == "verifikacia",
            PipelineMessage.kind == "verdict",
        )
        .order_by(PipelineMessage.seq.desc())
        .limit(1)
    ).scalar_one_or_none()
    return bool(latest and latest.payload and latest.payload.get("verdict") == "PASS")


def _gate_e_coverage_complete(report: Optional[PipelineMessage]) -> bool:
    """Whether the latest Customer boundary signalled all 7 okruhy covered (§4)."""
    return bool(report and report.payload and report.payload.get("coverage_complete"))


def _latest_designer_answer(db: Session, version_id: uuid.UUID) -> Optional[PipelineMessage]:
    """Most recent Designer answer in Gate E (or ``None``) — carries ``gap_found`` /
    ``proposed_fix`` in its payload, which gate the Branch B ``fix`` / ``leave`` actions."""
    return db.execute(
        select(PipelineMessage)
        .where(
            PipelineMessage.version_id == version_id,
            PipelineMessage.author == "designer",
            PipelineMessage.stage == "gate_e",
            PipelineMessage.kind == "answer",
        )
        .order_by(PipelineMessage.seq.desc())
        .limit(1)
    ).scalar_one_or_none()


def _latest_gate_e_milestone(db: Session, version_id: uuid.UUID) -> Optional[PipelineMessage]:
    """Latest gate_e milestone — a Designer ``answer`` or a Customer ``gate_report`` (by ``seq``).

    Distinguishes a per-question continue (latest = Designer answer → relay the answer
    back to the Customer) from a topic-boundary continue (latest = Customer gate_report
    → generic, no stale answer leaked into the next okruh). Symmetric relay (§5)."""
    return db.execute(
        select(PipelineMessage)
        .where(
            PipelineMessage.version_id == version_id,
            PipelineMessage.stage == "gate_e",
            or_(
                and_(PipelineMessage.author == "designer", PipelineMessage.kind == "answer"),
                and_(PipelineMessage.author == "customer", PipelineMessage.kind == "gate_report"),
            ),
        )
        .order_by(PipelineMessage.seq.desc())
        .limit(1)
    ).scalar_one_or_none()


def _latest_coordinator_message_content(db: Session, version_id: uuid.UUID) -> Optional[str]:
    """Content of the most recent Coordinator message (any kind) for a version.

    In Gate E Branch B this is the Coordinator's recommendation on a proposed fix —
    composed into the Coordinator-relayed ``fix`` directive so the decision travels
    Director→Coordinator→Designer (the Coordinator never drops out, §2)."""
    return db.execute(
        select(PipelineMessage.content)
        .where(PipelineMessage.version_id == version_id, PipelineMessage.author == "coordinator")
        .order_by(PipelineMessage.seq.desc())
        .limit(1)
    ).scalar_one_or_none()


def _gate_e_gap_open(db: Session, version_id: uuid.UUID) -> bool:
    """Whether the latest Designer answer flagged a gap (Branch B) — gates ``fix``/``leave``."""
    ans = _latest_designer_answer(db, version_id)
    return bool(ans and ans.payload and ans.payload.get("gap_found"))


_GATE_E_ROLE_SK = {
    "customer": "Zákazník",
    "designer": "Návrhár",
    "director": "Director",
    "coordinator": "Koordinátor",
    "system": "Systém",
}


def gate_e_audit_markdown(messages: list[PipelineMessage], version_number: str) -> str:
    """Assemble the Gate E audit record (F-007-gate-e §4) from the stage=gate_e thread.

    Pure (no DB/FS): covered okruhy + findings recorded during the review + the
    full Customer↔Designer↔Director transcript (seq-ordered). Written on final
    sign-off — by then the open-finding gate has passed, so closure is clean.
    """
    topics: list[str] = []
    findings: list[str] = []
    for m in messages:
        if not m.payload:
            continue
        if m.author == "customer" and m.kind == "gate_report" and m.payload.get("topic_done"):
            topic = m.payload.get("topic")
            if topic and topic not in topics:
                topics.append(topic)
        for finding in m.payload.get("findings") or []:
            if finding not in findings:
                findings.append(finding)

    lines = [f"# Gate E — zákaznícka previerka (audit) — v{version_number}", ""]
    lines += ["## Pokryté okruhy", ""]
    lines += ([f"- {t}" for t in topics] if topics else ["(žiadne zaznamenané)"]) + [""]
    lines += ["## Nálezy zaznamenané počas previerky", ""]
    lines += ([f"- {f}" for f in findings] if findings else ["Žiadne otvorené nálezy."]) + [""]
    lines += ["## Priebeh previerky (riešenia v poradí)", ""]
    for m in messages:
        who = _GATE_E_ROLE_SK.get(m.author, m.author)
        lines.append(f"**{who}:** {m.content}")
    lines.append("")
    return "\n".join(lines)


def _write_gate_e_audit(db: Session, version_id: uuid.UUID) -> str:
    """Persist the Gate E audit at final sign-off (F-007-gate-e §4) → returns the rel path.

    Records the summary as a ``pipeline_message`` (FS-independent audit trail) and
    best-effort writes ``docs/specs/versions/v<X>/customer-dialogue.md`` into the
    orchestrated project's repo (only when that repo exists — tests/no-repo skip).
    """
    slug = _project_slug_for_version(db, version_id)
    version_number = db.execute(select(Version.version_number).where(Version.id == version_id)).scalar_one()
    messages = (
        db.execute(
            select(PipelineMessage)
            .where(PipelineMessage.version_id == version_id, PipelineMessage.stage == "gate_e")
            .order_by(PipelineMessage.seq.asc())
        )
        .scalars()
        .all()
    )
    md = gate_e_audit_markdown(messages, version_number)
    rel = f"docs/specs/versions/v{version_number}/customer-dialogue.md"
    _record_message(
        db,
        version_id=version_id,
        stage="gate_e",
        author="system",
        recipient="director",
        kind="notification",
        content=f"Gate E audit uložený: {rel}",
        payload={"path": rel, "gate_e_audit": md},
    )
    project_root = claude_agent.PROJECTS_ROOT / slug
    if project_root.exists():  # real orchestrated repo — write the spec-tree artifact
        out = project_root / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")
    return rel


def _render_task_plan_md(db: Session, version: Version, project: Project) -> str:
    """Render the version's materialized Epic/Feat/Task rows to a reviewable markdown plan — the LAST
    part of the Návrh design doc (CR-V2-011). The plan otherwise lives ONLY as cockpit DB rows; the
    Manažér (+ the independent Auditor) need this doc to review the plan against the design at the
    post-Návrh schvaľovací bod. Re-queried from the DB rows so the displayed hierarchical numbers match
    the cockpit."""
    epics = db.execute(select(Epic).where(Epic.version_id == version.id).order_by(Epic.number)).scalars().all()
    n_epics = n_feats = n_tasks = 0
    total_min = 0
    body: list[str] = []
    for epic in epics:
        n_epics += 1
        body.append(f"## Epic {epic.number}: {epic.title}")
        body.append("")
        feats = db.execute(select(Feat).where(Feat.epic_id == epic.id).order_by(Feat.number)).scalars().all()
        for feat in feats:
            n_feats += 1
            fest = f" — ~{feat.estimated_minutes} min" if feat.estimated_minutes else ""
            body.append(f"### Feat {epic.number}.{feat.number}: {feat.title}{fest}")
            if feat.description:
                body.append(feat.description)
            tasks = db.execute(select(Task).where(Task.feat_id == feat.id).order_by(Task.number)).scalars().all()
            for task in tasks:
                n_tasks += 1
                total_min += task.estimated_minutes or 0
                test = f" — ~{task.estimated_minutes} min" if task.estimated_minutes else ""
                body.append(f"- **{epic.number}.{feat.number}.{task.number}** `[{task.task_type}]` {task.title}{test}")
            body.append("")
    hours = round(total_min / 60, 1)
    header = [
        f"# {project.slug} — Plán úloh v{version.version_number}",
        "",
        "> Generované automaticky z plánu úloh fázy Návrh (zdroj pravdy = cockpit DB rows). Slúži Manažérovi "
        "(a nezávislému Auditorovi) na overenie plánu proti návrhu pred stavbou. Needituj ručne — pri ďalšom "
        "behu Návrhu sa prepíše.",
        "",
        f"**Súhrn:** {n_epics} epicov · {n_feats} featov · {n_tasks} úloh · odhad ~{total_min} min (~{hours} h).",
        "",
    ]
    return "\n".join(header + body).rstrip() + "\n"


def _write_task_plan_doc(db: Session, version: Version) -> Optional[str]:
    """Write the materialized task plan to ``spec/task-plan.md`` in the project repo
    so it is a reviewable artefact (not DB-only). Skips cleanly (``None``) when the
    project has no ``source_path`` (no checkout to write into — tests / library
    projects). Returns a failure reason (→ caller records ``blocked``) only when a
    checkout exists but the write fails — a checked-out project's plan is not "done"
    without its reviewable doc (2026-06-22 process-gap fix)."""
    project = db.get(Project, version.project_id)
    if project is None or not project.source_path:
        return None
    doc_path = (
        Path(project.source_path)
        / "docs"
        / "specs"
        / "versions"
        / f"v{version.version_number}"
        / "spec"
        / "task-plan.md"
    )
    try:
        md = _render_task_plan_md(db, version, project)
        doc_path.parent.mkdir(parents=True, exist_ok=True)
        doc_path.write_text(md, encoding="utf-8")
    except OSError as exc:
        return f"task-plan doc write failed: {exc}"
    return None


def _persist_priprava_spec(db: Session, state: PipelineState, block: PipelineStatusBlock) -> Optional[str]:
    """Persist + verify the Príprava Špecifikácia artifact at the end of the Príprava dialogue (CR-V2-010,
    PREP-3). Returns a failure reason (→ caller settles ``blocked``, the phase does NOT close) or ``None``.

    The AI Agent writes the Špecifikácia Markdown to disk itself (it has Write tools in its warm session)
    and lists it in ``deliverables[]``; this is the deterministic mechanical gate that the artifact is real
    + readable (the Vývoj → Príprava tab reads this record), the Príprava analogue of ``_write_task_plan``
    for Návrh. The on-disk verify reuses the spec-tree convention (:func:`_priprava_spec_rel`).

    No-op pass (``None``) when the project has no checkout to write into (tests / library projects) — the
    spec then lives only as the recorded ``report`` payload of the gate_report message (DB audit trail),
    which is still readable. A checkout that EXISTS but is missing the spec file is a real failure: the
    Špecifikácia phase is not "done" without its reviewable artifact.
    """
    version = db.get(Version, state.version_id)
    if version is None:
        return "version not found for Špecifikácia write"
    rel = _priprava_spec_rel(version.version_number)
    project = db.get(Project, version.project_id)
    if project is None or not project.source_path:
        # No checkout — the spec is captured in the gate_report ``report`` payload (DB audit trail); record
        # the (DB-only) artifact note so the Príprava tab + audit trail still surface it.
        _record_message(
            db,
            version_id=state.version_id,
            stage="priprava",
            author="system",
            recipient="manazer",
            kind="notification",
            content="Špecifikácia pripravená (záznam v priebehu — projekt nemá checkout na zápis súboru).",
            payload={"phase": "priprava", "priprava_spec": True, "path": rel},
        )
        return None
    spec_path = Path(project.source_path) / rel
    if not spec_path.exists():
        return f"Špecifikácia artifact missing on disk: {rel}"
    _record_message(
        db,
        version_id=state.version_id,
        stage="priprava",
        author="system",
        recipient="manazer",
        kind="notification",
        content=f"Špecifikácia uložená: {rel}. Schváľ ju v Vývoj → Príprava (Schváliť špecifikáciu).",
        payload={"phase": "priprava", "priprava_spec": True, "path": rel},
    )
    return None


def _persist_navrh_design_doc(db: Session, state: PipelineState, block: PipelineStatusBlock) -> Optional[str]:
    """Persist + verify the Návrh design document at the end of the design-doc turn (CR-V2-011, NAVRH-1).
    Returns a failure reason (→ caller settles ``blocked``, the phase does NOT close) or ``None``.

    The AI Agent writes the design Markdown to disk itself (it has Write tools in its warm session) and
    lists it in ``deliverables[]``; this is the deterministic mechanical gate that the artifact is real +
    readable (the Vývoj → Návrh tab reads this record) — the Návrh analogue of :func:`_persist_priprava_spec`.
    The on-disk verify reuses the spec-tree convention (:func:`_navrh_design_doc_rel`).

    No-op pass (``None``) when the project has no checkout to write into (tests / library projects) — the
    design then lives only as the recorded ``report`` payload of the gate_report message (DB audit trail),
    which is still readable. A checkout that EXISTS but is missing the doc is a real failure: the Návrh
    phase is not "done" without its reviewable design artifact."""
    version = db.get(Version, state.version_id)
    if version is None:
        return "version not found for design-doc write"
    rel = _navrh_design_doc_rel(version.version_number)
    project = db.get(Project, version.project_id)
    if project is None or not project.source_path:
        _record_message(
            db,
            version_id=state.version_id,
            stage="navrh",
            author="system",
            recipient="manazer",
            kind="notification",
            content="Návrhový dokument pripravený (záznam v priebehu — projekt nemá checkout na zápis súboru).",
            payload={"phase": "navrh", "navrh_design_doc": True, "path": rel},
        )
        return None
    design_path = Path(project.source_path) / rel
    if not design_path.exists():
        return f"design-doc artifact missing on disk: {rel}"
    _record_message(
        db,
        version_id=state.version_id,
        stage="navrh",
        author="system",
        recipient="manazer",
        kind="notification",
        content=f"Návrhový dokument uložený: {rel}. Posúď ho v Vývoj → Návrh.",
        payload={"phase": "navrh", "navrh_design_doc": True, "path": rel},
    )
    return None


def _write_task_plan(db: Session, state: PipelineState, block: PipelineStatusBlock) -> Optional[str]:
    """Materialize the AI Agent's Návrh task-plan decomposition into Epic/Feat/Task rows.

    F-007 §5 / CR-NS-020 CR-2; v2 CR-V2-011 (the plan folds into the Návrh design doc). The deterministic
    mechanical gate for the task plan (replaces the disk-deliverable ``verify_mechanical`` — the plan's
    deliverable is DB rows, not files). Returns a failure reason (→ ``status=blocked``, nothing written)
    or ``None`` on success.

    **Idempotent replace + atomic:** a Manažér ``uprav`` re-dispatches the AI Agent, which re-runs this;
    we drop the version's existing epics first (FK cascade → feats/tasks) so a re-plan never duplicates.
    The whole replace runs in a SAVEPOINT — any failure rolls back the rows while the caller still records
    ``blocked`` (never a half-written plan). Numbers are service-assigned (MAX+1); status is forced
    (planned/todo — the AI Agent never pre-marks done); ``baseline_sha`` / ``task_count`` /
    ``auto_fix_count`` stay untouched (CR-3 owns them).
    """
    plan = block.plan
    if plan is None or not plan.epics:  # defensive — parse_status_block already guards this
        return "task_plan gate_report carried no plan"
    version = db.get(Version, state.version_id)
    if version is None:
        return "version not found for task_plan write"

    n_epics = n_feats = n_tasks = 0
    try:
        with db.begin_nested():  # SAVEPOINT — atomic replace, no half-written plan
            db.execute(delete(Epic).where(Epic.version_id == state.version_id))
            db.flush()
            for epic_in in plan.epics:
                epic_row = epic_service.create(
                    db,
                    EpicCreate(
                        project_id=version.project_id,
                        version_id=state.version_id,
                        title=epic_in.title,
                    ),
                )
                n_epics += 1
                for feat_in in epic_in.feats:
                    feat_row = feat_service.create(
                        db,
                        FeatCreate(
                            epic_id=epic_row.id,
                            title=feat_in.title,
                            description=feat_in.description,
                            estimated_minutes=feat_in.estimated_minutes,
                        ),
                    )
                    n_feats += 1
                    for task_in in feat_in.tasks:
                        task_service.create(
                            db,
                            TaskCreate(
                                feat_id=feat_row.id,
                                title=task_in.title,
                                task_type=task_in.task_type,
                                description=task_in.description,
                                checklist_type=task_in.checklist_type,
                                priority=task_in.priority,
                                estimated_minutes=task_in.estimated_minutes,
                            ),
                        )
                        n_tasks += 1
    except (ValueError, ValidationError, IntegrityError) as exc:
        return f"plan write failed: {exc}"

    # Materialize the plan as a reviewable doc (spec/task-plan.md) — not DB-only —
    # so the Coordinator (separate session) can verify it before the build.
    doc_err = _write_task_plan_doc(db, version)
    if doc_err is not None:
        return doc_err

    _record_message(
        db,
        version_id=state.version_id,
        stage="navrh",  # CR-V2-011: the task plan is the last part of the Návrh design doc
        author="system",
        recipient="manazer",
        kind="notification",
        content=f"Plán úloh zapísaný: {n_epics} epicov, {n_feats} featov, {n_tasks} taskov. Doc: spec/task-plan.md.",
        payload={"task_plan_summary": {"epics": n_epics, "feats": n_feats, "tasks": n_tasks}, "phase": "navrh"},
    )
    return None


def dispatch_directive(
    db: Session, version_id: uuid.UUID, action: str, payload: dict[str, Any], stage: str
) -> Optional[str]:
    """Resolve the re-dispatch prompt for an ``agent_working`` transition, else ``None`` (CR-V2-009).

    Single entry point for the route (CR-NS-018): payload-framed for ``uprav`` / ``ask`` / ``answer``
    (delegates to :func:`directive_for_action`), ``None`` for a fresh-phase dispatch (``start`` /
    ``approve_spec`` / ``schvalit`` / ``verdict`` / ``pokracovat``). The v1 ``apply_coordinator_recommendation``
    DB-fetch + the Gate-E sub-flow relay branches are REMOVED — the Coordinator hub-and-spoke is retired
    (design §2.2) and the 4-phase model has no Gate E (the Auditor's upfront review replaces it, CR-V2-013).
    ``db`` / ``version_id`` are kept (route call signature) for forward use; currently unused here.
    """
    del db, version_id  # route-call signature parity; the v1 DB-fetch relay paths are retired (CR-V2-009)
    return directive_for_action(action, payload, stage)


# ---------------------------------------------------------------------------
# Agent invocation (records message, no state mutation)
# ---------------------------------------------------------------------------


async def invoke_agent(
    db: Session,
    *,
    version_id: uuid.UUID,
    role: str,
    stage: str,
    prompt: str,
    timeout: Optional[int] = None,
    on_event: Optional[claude_agent.EventCallback] = None,
    recipient: str = "manazer",
    on_message: Optional[MessageCallback] = None,
    extra_payload: Optional[dict[str, Any]] = None,
    metrics: Optional["_DispatchMetrics"] = None,
) -> PipelineStatusBlock | ParseFailure:
    """Drive one agent turn headless and record its message.

    Resolves the ``(project, role)`` claude session, invokes claude, parses the
    status block, and appends a ``pipeline_message``. On a claude error or a
    parse failure, records a ``system`` escalation message and returns the
    ``ParseFailure``. Does **not** mutate ``pipeline_state`` (the caller owns it).

    ``timeout`` overrides the per-invocation backstop; ``None`` → the per-stage
    default (:func:`_timeout_for`).

    ``recipient`` (F-007-gate-e §5) is who the agent's message is addressed to —
    the next in the chain. CR-V2-004 renamed the operator participant token
    ``director`` → ``manazer`` (migration 071 ``ck_pipeline_message_recipient``), so the
    default is now ``"manazer"``; ``director`` is no longer a valid DB participant. (The
    v1 gate_e round's ``designer`` / ``coordinator`` recipients live on dead Coordinator/
    Gate-E paths removed wholesale by CR-V2-009 / CR-V2-013.)

    When ``on_event`` is set, each streamed event (and a one-shot ``active_role``
    signal at the start) is tagged with ``_role=role`` so the cockpit rail shows the
    **real** working agent per turn, not the nominal stage actor.

    ``metrics`` (WS-D, CR-NS-036): an optional :class:`_DispatchMetrics` accumulator. When given
    (by :func:`invoke_agent_with_parse_retry`) the turn's token usage + wall-clock fold into it
    across parse-retries, and the recorded message's ``payload.usage`` / ``payload.timing`` reflect
    the accumulated total. When ``None`` a fresh per-call accumulator is used (single-shot direct
    callers still get accurate per-message metrics).
    """
    slug = _project_slug_for_version(db, version_id)
    session_id, is_first = _resolve_orch_session(db, slug, role)
    # R1-d (D3): bump the session's last activity for the TTL retention task. One UPDATE per turn (covers
    # the just-created row too — a harmless re-stamp to ≈now); the retention loop prunes rows untouched 7d.
    db.execute(
        update(OrchestratorSession)
        .where(OrchestratorSession.project_slug == slug, OrchestratorSession.role == role)
        .values(last_input_at=datetime.now(timezone.utc))
    )
    # CR-NS-040 (E3(b/c)): per-dispatch model/effort from the project owner's config. Resolved here (not
    # in the parse-retry wrapper) so EVERY dispatch — including each parse-retry, which re-enters
    # invoke_agent — applies the owner's config; unset → no flags (today's behavior).
    model_override, effort_override = _resolve_dispatch_overrides(db, version_id, role)
    charter_path: Optional[Path] = None
    if is_first:
        # CR-V2-007: DB role value (underscore) → charter-path slug (hyphen) via the single bridge, so
        # the on-disk ``Pravidlá agenta`` path (``.claude/agents/ai-agent/CLAUDE.md``) never diverges
        # from the DB ``ai_agent``.
        charter_path = (
            claude_agent.PROJECTS_ROOT / slug / ".claude" / "agents" / _charter_slug_for_role(role) / "CLAUDE.md"
        )

    tagged_on_event: Optional[claude_agent.EventCallback] = None
    if on_event is not None:

        async def tagged_on_event(evt: dict) -> None:
            await on_event({**evt, "_role": role} if isinstance(evt, dict) else evt)

        await tagged_on_event({"type": "active_role"})  # per-turn rail signal (steps Z→N→K)

    # WS-D (CR-NS-036): time + meter this dispatch into the turn accumulator. A fresh local one for
    # single-shot direct callers; the shared one when threaded through the parse-retry loop.
    turn_metrics = metrics if metrics is not None else _DispatchMetrics()
    _started = perf_counter()
    try:
        text, usage, structured_output = _split_claude_result(
            await invoke_claude(
                project_slug=slug,
                claude_session_id=session_id,
                prompt=prompt,
                charter_path=charter_path,
                timeout=timeout if timeout is not None else _timeout_for(stage),
                on_event=tagged_on_event,
                model=model_override,
                effort=effort_override,
                # R3 (v0.7.0): grammar-constrain the agent's status block to the schema so a malformed
                # block is impossible at the source; the validated object lands in structured_output.
                json_schema=PIPELINE_STATUS_JSON_SCHEMA,
            )
        )
    except ClaudeAgentError as exc:
        # A failed invocation still burned wall-clock (and counts as an attempt) — record it so the
        # turn's timing/parse_attempts reflect retries; no usage (no envelope was returned) (WS-D).
        turn_metrics.record(None, perf_counter() - _started)
        # R1-c (D1): an envelope-loss (timeout/crash) may have left real commits behind even though the
        # JSON envelope was lost. Audit ``baseline..HEAD`` and record ONE system→director notification so
        # the Director can review & continue — never silently re-do or lose the work. The audit dict rides
        # on the returned ParseFailure so ``run_dispatch`` settles to ``awaiting_director`` (not a bare
        # ``blocked``). A no-op (returns None) when no dispatch baseline was armed (Seam #1/#3).
        lost_work = await _audit_lost_work(
            db,
            version_id=version_id,
            slug=slug,
            stage=stage,
            timeout_seconds=timeout if timeout is not None else _timeout_for(stage),
            on_message=on_message,
        )
        # Return the failure SILENTLY otherwise (CR-NS-022 §2 — no raw system→director dump here). The
        # caller decides if/how it reaches the Director: invoke_agent_with_parse_retry relays the
        # FINAL unrecovered failure via the Coordinator in plain Slovak; internal direct callers
        # (auditor / coordinator-judge) fold it into their own handling. Suppresses the leak where
        # an intermediate parse-retry later succeeds.
        return ParseFailure(
            f"claude invocation failed: {exc}",
            usage=turn_metrics.usage_payload(),
            timing=turn_metrics.timing_payload(),
            lost_work=lost_work,
        )
    turn_metrics.record(usage, perf_counter() - _started)
    stdout = text

    # R3 (v0.7.0): the grammar-constrained structured_output is PRIMARY. When the agent produced one,
    # validate it through the same content contract; on its ParseFailure (a schema the model couldn't
    # satisfy) OR when none was produced (no schema / older CLI), fall back to the fence parse of the
    # result text (D2 defense-in-depth). The fence path stays byte-for-byte as the rollout-safe net.
    parsed: ParseFailure | PipelineStatusBlock
    if structured_output is not None:
        parsed = parse_structured_output(structured_output)
        if isinstance(parsed, ParseFailure):
            parsed = parse_status_block(stdout)
    else:
        parsed = parse_status_block(stdout)
    if isinstance(parsed, ParseFailure):
        # WS-D (CR-NS-036): carry this turn's accumulated metrics on the ParseFailure so a terminal
        # escalation (which records the only message for this no-message turn) can fold them in.
        return replace(parsed, usage=turn_metrics.usage_payload(), timing=turn_metrics.timing_payload())

    # Map the agent block.kind → message kind (question/blocked → question).
    msg_kind = "question" if parsed.kind in ("question", "blocked") else parsed.kind
    if msg_kind not in (
        "kickoff",
        "question",
        "answer",
        "gate_report",
        "notification",
    ):
        msg_kind = "gate_report"
    msg = _record_message(
        db,
        version_id=version_id,
        stage=stage,
        author=role,
        recipient=recipient,
        kind=msg_kind,
        content=parsed.summary,
        payload={
            # Legible-cockpit-output fix: the agent's FULL human-readable markdown report — the text
            # BEFORE the machine status fence (## headings, lists, code, ✅). ``content`` stays the
            # one-line ``summary`` (deriveBrief / previews / every existing consumer); this ADDITIVE key
            # lets the cockpit bubble render the rich report instead of the discarded monolithic block.
            # ``None`` when the agent emitted nothing but the fence (FE falls back to content/summary).
            "report": extract_report_body(stdout) or None,
            "deliverables": parsed.deliverables,
            "commits": parsed.commits,
            "question": parsed.question,
            "awaiting": parsed.awaiting,
            "block_kind": parsed.kind,
            # CR-V2-006 dropped the v1 Gate-E / Coordinator / per-task-audit status-block fields
            # (topic / topic_done / coverage_complete / gap_found / task_pass / coordinator_directive)
            # from ``PipelineStatusBlock``. ``invoke_agent``'s per-turn payload write runs on EVERY
            # turn (incl. the AI-Agent units this CR re-keys), so a bare ``parsed.<removed>`` raises
            # AttributeError here. Read them defensively via ``getattr(..., None)`` so they degrade to
            # the same ``None`` they will become once the writers/payload keys are removed wholesale by
            # CR-V2-009 (apply_action rebuild) / CR-V2-013 (Gate-E → Auditor upfront review). This is the
            # minimal CR-V2-007-local unblock — NOT the Coordinator-relay removal those CRs own. The
            # repurposed-and-kept ``findings`` / ``proposed_fix`` (CR-V2-006 Auditor verdict) stay direct.
            "topic": getattr(parsed, "topic", None),
            "topic_done": getattr(parsed, "topic_done", None),
            "coverage_complete": getattr(parsed, "coverage_complete", None),
            "findings": parsed.findings,
            "gap_found": getattr(parsed, "gap_found", None),
            "proposed_fix": parsed.proposed_fix,
            # task_plan decomposition (F-007 §4/§5, CR-NS-020 CR-2; v2: folds into Návrh — CR-V2-011).
            # Persisted so the audit trail / TaskPlanPanel can show the plan and CR-3 can re-read the
            # cross-cutting rules from this gate_report payload.
            # mode="json" so any UUID in the plan serializes to a str for JSONB.
            "plan": parsed.plan.model_dump(mode="json") if parsed.plan is not None else None,
            "cross_cutting_rules": parsed.cross_cutting_rules,
            # v1 per-task Auditor verdict (removed by CR-V2-006; defensive read — see note above).
            "task_pass": getattr(parsed, "task_pass", None),
            # v1 structured Coordinator proposal (removed by CR-V2-006; defensive read — see note above).
            # The relay executor (apply_coordinator_recommendation) is removed wholesale by CR-V2-009.
            "coordinator_directive": (
                _cd.model_dump(mode="json")
                if (_cd := getattr(parsed, "coordinator_directive", None)) is not None
                else None
            ),
            # Caller-supplied structural markers (e.g. is_fix_edit) for the deterministic
            # open-finding count — orchestrator record, not agent self-report (§5).
            **(extra_payload or {}),
            # WS-D (CR-NS-036) token usage + dispatch timing for this turn — placed AFTER the
            # extra_payload spread so these orchestrator-owned metrics are never clobbered. usage is
            # None when no envelope carried it (never fabricated); timing accumulates parse-retries.
            "usage": turn_metrics.usage_payload(),
            "timing": turn_metrics.timing_payload(),
        },
    )
    if on_message is not None:  # incremental broadcast (CR-NS-018) — stream this turn now
        await on_message(msg)
    return parsed


async def invoke_agent_with_parse_retry(
    db: Session,
    *,
    version_id: uuid.UUID,
    role: str,
    stage: str,
    prompt: str,
    timeout: Optional[int] = None,
    on_event: Optional[claude_agent.EventCallback] = None,
    recipient: str = "manazer",
    on_message: Optional[MessageCallback] = None,
    extra_payload: Optional[dict[str, Any]] = None,
    metrics: Optional["_DispatchMetrics"] = None,
) -> PipelineStatusBlock | ParseFailure:
    """Invoke the actor; on a status-block ``ParseFailure``, re-invoke (bounded).

    A single LLM JSON typo in the ``<<<PIPELINE_STATUS>>>`` block must not halt
    the pipeline (CR-NS-018). On a parse failure we feed the error back and ask
    the agent to re-emit **only** a corrected, valid block — same content, valid
    JSON. The agent runs ``--resume`` so each retry is a cheap re-emit, not a
    redo of the work. After ``_PARSE_RETRIES`` still-invalid attempts we return
    the last :class:`ParseFailure` and the caller escalates to ``blocked``
    (endpoint unchanged). No guessing — we never fabricate a block.

    Distinct from :func:`_verify_with_retries`, which retries a *valid* report
    that failed verification. Only the first (primary) invocation streams via
    ``on_event``; the cheap re-emit retries don't stream.
    """
    # WS-D (CR-NS-036): one accumulator for the whole turn — failed re-emits burn tokens too, so the
    # surviving (successful) message's payload reflects the SUM across the primary + every retry. A
    # caller may pre-seed it (the Coordinator relay carries a failed worker's lost tokens into its
    # relay message — see _coordinator_relay_engine_failure).
    turn_metrics = metrics if metrics is not None else _DispatchMetrics()
    result = await invoke_agent(
        db,
        version_id=version_id,
        role=role,
        stage=stage,
        prompt=prompt,
        timeout=timeout,
        on_event=on_event,
        recipient=recipient,
        on_message=on_message,
        extra_payload=extra_payload,
        metrics=turn_metrics,
    )
    attempts = 0
    while isinstance(result, ParseFailure) and attempts < _PARSE_RETRIES:
        attempts += 1
        result = await invoke_agent(
            db,
            version_id=version_id,
            role=role,
            stage=stage,
            timeout=timeout,
            # R3 (v0.7.0): transport-agnostic — the status block may arrive as grammar-constrained
            # structured_output (--json-schema) OR the <<<PIPELINE_STATUS>>> fence fallback, so the
            # re-prompt names neither; it cites the validation reason and asks for a conforming object.
            prompt=(
                f"Tvoj štruktúrovaný stavový výstup sa nepodarilo spracovať: {result.reason}. "
                "Pošli LEN platný stavový objekt podľa schémy "
                "(F-007-orchestration-cockpit.md §5.3) — rovnaký obsah, správne polia a hodnoty."
            ),
            recipient=recipient,
            on_message=on_message,
            extra_payload=extra_payload,
            metrics=turn_metrics,
        )
    return result


# Marks a task_plan-pass ParseFailure that originated from a ``ClaudeAgentError`` (timeout/crash) rather
# than an unparseable structured output — lets _settle_plan_pass_failure pick the accurate block_reason
# (agent_error vs parse_exhaustion) without a new ParseFailure field. Same wording invoke_agent uses.
_PLAN_PASS_ENVELOPE_LOSS_PREFIX = "claude invocation failed:"


async def _plan_pass_once(
    db: Session,
    state: PipelineState,
    *,
    prompt: str,
    json_schema: dict,
    parser: Callable[[dict], Any],
    on_event: Optional[claude_agent.EventCallback],
    on_message: Optional[MessageCallback],
    metrics: "_DispatchMetrics",
) -> Any:
    """One ``claude`` invocation for a task_plan generation pass (v0.7.3, CR-1).

    Resumes the SAME ``(project, ai_agent)`` claude session the design phase used (so the full design
    + the just-emitted skeleton stay in context), grammar-constrains the output to the **narrowed**
    ``json_schema``, meters the turn into ``metrics``, and parses the ``structured_output`` envelope
    field with ``parser``. Returns the parsed narrowed model or a :class:`ParseFailure` — it records
    **no** message of its own on the parse path (the caller :func:`_invoke_plan_pass` records ONE
    synthetic note on overall success). Mirrors :func:`invoke_agent`'s session/metrics handling — incl.
    the **R1 envelope-loss path** (a ``ClaudeAgentError`` runs :func:`_audit_lost_work` and rides its
    audit dict on ``ParseFailure.lost_work`` so the caller settles to ``awaiting_director``, not a
    ``blocked`` dead-end) — but never assumes a :class:`PipelineStatusBlock` (the narrowed passes do
    not emit one — that is why they cannot use ``invoke_agent``, which stays byte-identical)."""
    version_id = state.version_id
    slug = _project_slug_for_version(db, version_id)
    # CR-V2-007: the task_plan generation passes run inside the AI Agent's warm session (they fold into
    # the Návrh phase in CR-V2-011); re-keyed off the retired ``designer`` role to ``ai_agent`` (DB value).
    session_id, is_first = _resolve_orch_session(db, slug, AI_AGENT_ROLE)
    db.execute(
        update(OrchestratorSession)
        .where(OrchestratorSession.project_slug == slug, OrchestratorSession.role == AI_AGENT_ROLE)
        .values(last_input_at=datetime.now(timezone.utc))
    )
    model_override, effort_override = _resolve_dispatch_overrides(db, version_id, AI_AGENT_ROLE)
    charter_path: Optional[Path] = None
    if is_first:  # task_plan normally runs after the design phase (session exists → resume); defensive.
        charter_path = (
            claude_agent.PROJECTS_ROOT
            / slug
            / ".claude"
            / "agents"
            / _charter_slug_for_role(AI_AGENT_ROLE)
            / "CLAUDE.md"
        )

    tagged_on_event: Optional[claude_agent.EventCallback] = None
    if on_event is not None:

        async def tagged_on_event(evt: dict) -> None:
            await on_event({**evt, "_role": AI_AGENT_ROLE} if isinstance(evt, dict) else evt)

        await tagged_on_event({"type": "active_role"})

    _started = perf_counter()
    try:
        text, usage, structured = _split_claude_result(
            await invoke_claude(
                project_slug=slug,
                claude_session_id=session_id,
                prompt=prompt,
                charter_path=charter_path,
                timeout=_timeout_for("navrh"),
                on_event=tagged_on_event,
                model=model_override,
                effort=effort_override,
                json_schema=json_schema,
            )
        )
    except ClaudeAgentError as exc:
        # A failed invocation still burned wall-clock (no usage envelope) — count it (WS-D).
        metrics.record(None, perf_counter() - _started)
        # R1 envelope-loss parity (CR-1, audit 2026-06-18): a timeout/crash may have left real commits
        # behind even though the JSON envelope was lost — audit baseline..HEAD and ride the audit dict on
        # ParseFailure.lost_work so the round settles to awaiting_director ("review & continue"), exactly
        # like invoke_agent. A no-op (None) when no dispatch baseline was armed; the prefix below then lets
        # the round set block_reason=agent_error (a ClaudeAgentError), never the parse_exhaustion mislabel.
        lost_work = await _audit_lost_work(
            db,
            version_id=version_id,
            slug=slug,
            stage="navrh",  # CR-V2-011: the plan passes fold into Návrh — the lost-work note is a navrh-phase turn
            timeout_seconds=_timeout_for("navrh"),
            on_message=on_message,
        )
        return ParseFailure(
            f"{_PLAN_PASS_ENVELOPE_LOSS_PREFIX} {exc}",
            usage=metrics.usage_payload(),
            timing=metrics.timing_payload(),
            lost_work=lost_work,
        )
    metrics.record(usage, perf_counter() - _started)
    # TEXT/FENCE EXTRACTION (CR-1, live root-cause 2026-06-18): ``--json-schema`` does NOT return a
    # ``structured_output`` field in this CLI — the model emits the narrowed JSON as TEXT in a
    # ``<<<TASK_PLAN_JSON>>>`` sentinel fence (the directives instruct it). Prefer ``structured_output``
    # (forward-compat if a future CLI populates it), else fall back to extracting the fenced JSON from
    # stdout — the SAME text/fence survival path ``invoke_agent`` uses (``parse_status_block``).
    if structured is not None:
        obj: Any = structured
    else:
        obj = extract_task_plan_json(text)
        if isinstance(obj, ParseFailure):
            return obj
    return parser(obj)


async def _invoke_plan_pass(
    db: Session,
    state: PipelineState,
    *,
    prompt: str,
    json_schema: dict,
    parser: Callable[[dict], Any],
    label_fn: Callable[[Any], str],
    on_event: Optional[claude_agent.EventCallback] = None,
    on_message: Optional[MessageCallback] = None,
) -> Any:
    """One bounded task_plan generation pass with per-pass parse-retry (v0.7.3, CR-1; v2 CR-V2-011).

    The narrowed-schema sibling of :func:`invoke_agent_with_parse_retry`, used by the folded task-plan
    passes inside :func:`_run_navrh_round` (the standalone ``_run_task_plan_round`` is removed — the plan
    is the last part of the Návrh design doc). The passes emit a ``TaskPlanSkeleton`` /
    ``TaskPlanFeatTasks`` object (NOT a status block), so they bypass ``invoke_agent`` /
    ``invoke_agent_with_parse_retry`` / :data:`PIPELINE_STATUS_JSON_SCHEMA` entirely — those stay
    byte-identical. The same parse-retry policy applies **per pass** (``_PARSE_RETRIES``): a single-feat
    JSON typo re-emits just that pass, never the whole tree. On success it records ONE concise synthetic
    audit ``pipeline_message`` (author=``ai_agent``, stage=``navrh``, kind=``notification`` — these are
    not status blocks, so ``note``-style) with the turn's accumulated usage/timing, so the ``on_message``
    broadcast + WS-D metrics are preserved. Returns the parsed narrowed model, or a :class:`ParseFailure`
    on retry-exhaustion (carrying the accumulated metrics → the round's fail-closed HALT)."""
    metrics = _DispatchMetrics()
    result = await _plan_pass_once(
        db,
        state,
        prompt=prompt,
        json_schema=json_schema,
        parser=parser,
        on_event=on_event,
        on_message=on_message,
        metrics=metrics,
    )
    attempts = 0
    while isinstance(result, ParseFailure) and result.lost_work is None and attempts < _PARSE_RETRIES:
        # Retry only a genuine PARSE failure (re-emit the block). An envelope-loss (ClaudeAgentError →
        # lost_work set) is NOT a re-emittable typo — stop and let the R1 path settle to awaiting_director
        # (re-invoking would just risk another long timeout and could drop the lost_work signal).
        attempts += 1
        result = await _plan_pass_once(
            db,
            state,
            prompt=(
                f"Tvoj výstup sa nepodarilo spracovať: {result.reason}. Pošli ho ZNOVA — rovnaký obsah, "
                "ale VÝHRADNE ako jeden JSON objekt vnútri bloku <<<TASK_PLAN_JSON>>> … "
                "<<<END_TASK_PLAN_JSON>>>, s presnými názvami polí a bez čohokoľvek navyše."
            ),
            json_schema=json_schema,
            parser=parser,
            on_event=None,  # cheap re-emit retries don't stream (mirror invoke_agent_with_parse_retry)
            on_message=on_message,
            metrics=metrics,
        )
    if isinstance(result, ParseFailure):
        # Attach the accumulated turn metrics so the fail-closed Coordinator relay can carry the lost tokens.
        return replace(result, usage=metrics.usage_payload(), timing=metrics.timing_payload())
    msg = _record_message(
        db,
        version_id=state.version_id,
        stage="navrh",  # CR-V2-011: the plan passes are Návrh-phase turns (the plan folds into Návrh)
        author="ai_agent",
        recipient="manazer",
        kind="notification",
        content=label_fn(result),
        payload={"usage": metrics.usage_payload(), "timing": metrics.timing_payload(), "phase": "navrh"},
    )
    if on_message is not None:
        await on_message(msg)
    return result


async def _coordinator_relay_engine_failure(
    db: Session,
    version_id: uuid.UUID,
    stage: str,
    reason: str,
    on_message: Optional[MessageCallback] = None,
    *,
    failed: Optional[ParseFailure] = None,
) -> None:
    """Relay an engine-level hard failure to the Director via the Coordinator, in plain Slovak
    (F-007 §6/§7, CR-NS-022 §2). Called from the orchestration layer at the point it decides to
    block, so a worker parse-exhaustion / a plan write failure reaches the Director as a plain
    Coordinator explanation — never a raw technical dump. The Coordinator's turn
    (``recipient=director``) IS that message. If the Coordinator itself can't run, fall back to a
    plain ``system→director`` note (the Coordinator's own failure is handled here — no re-relay).

    ``failed`` (WS-D, CR-NS-036): the worker's terminal :class:`ParseFailure` when this relay escalates
    a parse-exhaustion (vs an engine error like a plan-write fail, where the worker DID produce a
    message). When it carries usage, the relay's metric accumulator is pre-seeded with the worker's
    lost tokens, so the recorded relay message counts worker + Coordinator (no extra notification, no
    undercount); the fallback note carries them too."""
    seed = _seed_metrics_from_failure(failed)
    # Metrics redesign §1.1: the seeded worker tokens (and the timing carried on the fallback note)
    # belong to the FAILED WORKER's role-of-origin, not the relaying Coordinator. The worker's role is
    # the actor of the stage the failure happened in (STAGE_ACTOR). Tag the recorded message with
    # ``metrics_role`` (top-level payload) so aggregate_usage_by_role attributes those tokens to the
    # worker, not coordinator/system. Only when there is actually a worker turn to carry.
    failed_role = STAGE_ACTOR.get(stage)
    relay_extra: dict[str, Any] = {"is_director_brief": True}
    if seed is not None and failed_role is not None:
        relay_extra["metrics_role"] = failed_role
    relay = await invoke_agent_with_parse_retry(
        db,
        version_id=version_id,
        role="coordinator",
        stage=stage,
        metrics=seed,
        prompt=(
            f"Vo fáze '{stage}' nastalo technické zlyhanie, ktoré treba oznámiť Directorovi: {reason}. "
            "Vysvetli mu to po slovensky, zrozumiteľne — čo sa stalo a čo môže urobiť — bez technického "
            "žargónu a kódov. "
            # E7 (F-008 §3, CR-NS-033): triage the failure (typically nex_studio_bug or director_decision)
            # + append a structured directive in the PAYLOAD — the human relay text stays plain (CR-NS-022).
            "Klasifikuj zlyhanie (triage §7.1 — zvyčajne nex_studio_bug alebo director_decision) a pripoj "
            "štruktúrovaný `coordinator_directive` popri vysvetlení (technické detaily nech ostanú v "
            "payloade, nie v slovenskom texte)."
            + _DIRECTOR_FORMAT_BRIEF
            + "Ukonči <<<PIPELINE_STATUS>>> blokom (F-007-orchestration-cockpit.md §5.3)."
        ),
        on_message=on_message,
        # CR-2: an engine-failure/HALT escalation the Director reads at a block → Director-facing by
        # construction → always the prominent rail. Metrics redesign §1.1: + role-of-origin tag.
        extra_payload=relay_extra,
    )
    if isinstance(relay, ParseFailure):
        # Even the fallback must NOT leak the raw reason to the Director (CR-NS-022 §2) — keep it
        # plain Slovak and log the raw detail instead (mirrors _block_failed).
        logger.warning("engine-failure relay fallback (%s): %s", stage, reason)
        msg = _record_message(
            db,
            version_id=version_id,
            stage=stage,
            author="system",
            recipient="director",
            kind="notification",
            content=(
                f"Vo fáze '{stage}' nastal problém, ktorý si vyžaduje tvoju pozornosť — "
                "skús akciu zopakovať; podrobnosti sú v zázname."
            ),
            # WS-D (CR-NS-036): even when the Coordinator relay itself fails to parse, the failed
            # worker's lost tokens ride on this fallback note so aggregate_pipeline_usage counts them.
            # Metrics redesign §1.1: tag the same role-of-origin so they don't fall into the excluded
            # ``system`` bucket (this note is author="system").
            payload=_relay_fallback_payload(failed, failed_role),
        )
        if on_message is not None:
            await on_message(msg)


async def _record_internal_turn_parse_failure(
    db: Session,
    version_id: uuid.UUID,
    stage: str,
    *,
    turn_label: str,
    failed: ParseFailure,
    on_message: Optional[MessageCallback] = None,
) -> None:
    """Make a silent INTERNAL-turn parse-exhaustion visible + counted (WS-E, CR-NS-037, Class F).

    When an internal Coordinator / verify-judge turn (NOT a build worker) exhausts its parse-retries,
    the orchestrator otherwise discards the terminal :class:`ParseFailure` → its tokens leak and the
    failure is invisible to the Director. The SINGLE drift-proof recorder used by all five Class-F
    sites: records ONE plain-Slovak ``system→director`` note (CR-NS-022 §2 — no raw technical dump)
    naming the failed turn, and attaches its accumulated usage/timing when present
    (:func:`_failure_metrics_payload`) so :func:`pipeline_metrics.aggregate_pipeline_usage` counts it.

    Pure observability: the note is recorded ALWAYS (visibility ≠ metrics — unlike ``_block_failed``'s
    usage-gating); the metrics payload rides along when present. The caller KEEPS its existing settled
    state + fallback — this adds no control-flow branch, no offerable action, no status/stage change
    (WS-E HARD constraint)."""
    msg = _record_message(
        db,
        version_id=version_id,
        stage=stage,
        author="system",
        recipient="director",
        kind="notification",
        content=(
            f"{turn_label} sa nepodarilo dokončiť ani po opakovaných pokusoch — pokračuje sa "
            "náhradným postupom (nie pôvodný zámer Koordinátora). Pozri priebeh a rozhodni."
        ),
        # Metrics when present (else NULL payload — the note still records, for visibility).
        payload=_failure_metrics_payload(failed) or None,
    )
    if on_message is not None:
        await on_message(msg)


# CR-2 (v0.7.3) → simplified v0.7.4: the shared Director-facing formatting nudge — appended to ALL three
# Director-facing Coordinator prompts (_coordinator_synthesis, verify_done judge, _coordinator_relay). The
# headline is now GUARANTEED by the FE (PipelineMessageBubble.deriveBrief), independent of model compliance
# (the model systematically ignored the prior `## ` heading instruction — verified live v0.7.4), so this is a
# best-effort nudge for a scannable body only. The <<<PIPELINE_STATUS>>> contract / R3 grammar is UNCHANGED.
_DIRECTOR_FORMAT_BRIEF = (
    " Prvý riadok = krátke **jednovetové zhrnutie** (čo sa stalo / čo treba rozhodnúť). "
    "Potom detaily; možnosti, kroky a riziká dávaj do **odrážkových zoznamov**. Slovensky. "
)


async def _coordinator_synthesis(
    db: Session,
    state: PipelineState,
    *,
    trigger: str,
    completed: bool = False,
    on_message: Optional[MessageCallback] = None,
) -> Optional[str]:
    """§A.1 (CR-NS-053, Pillar A) — emit ONE Director-facing synthesis at a decision point.

    At every Director decision point the Coordinator (the sole Director-facing voice) analyzes the
    outcome like a senior dev and explains it in plain, STRUCTURED Slovak (markdown). Recorded as a
    ``coordinator→director`` message marked ``payload.is_synthesis=true`` (the FE distinguishes it from
    a raw worker report — mirrors the established ``is_fix_edit`` marker), so the raw worker report
    stays recorded for drill-down while the synthesis is the primary Director-facing message.

    Returns the synthesis ``summary`` for the caller to use as ``next_action``, or ``None`` on a
    ``ParseFailure`` — on which the WS-E recorder makes the failed turn visible + metered and the caller
    keeps its EXISTING settled state + ``next_action`` unchanged. **Additive observability only: never a
    new control-flow branch, never a dead-end (WS-E HARD constraint).**

    Synthesis fires ONLY for WORKER-authored decision points: the Coordinator never synthesizes its OWN
    output (CR-NS-053 fix-round 1). ``kickoff`` and ``release`` are coordinator-authored (STAGE_ACTOR), so
    a synthesis there would be a redundant second Coordinator turn that demotes its own Director-facing
    message — the guard (one place, all 5 sites) returns ``None`` and the caller settles exactly as today.
    """
    if state.current_actor == "coordinator":
        return None
    verb = "je dokončená" if completed else "prešla overením"
    result = await invoke_agent_with_parse_retry(
        db,
        version_id=state.version_id,
        role="coordinator",
        stage=state.current_stage,
        prompt=(
            f"Fáza/udalosť '{trigger}' {verb}. Pre Directora to ZHRŇ — analyzuj ako senior vývojár a "
            "vysvetli zrozumiteľnou rečou, ŠTRUKTÚROVANE: (1) čo sa stalo, (2) čo je ďalší krok / čo "
            "od Directora treba, (3) riziká alebo poznámky."
            + _DIRECTOR_FORMAT_BRIEF
            + "Ukonči <<<PIPELINE_STATUS>>> blokom (F-007-orchestration-cockpit.md §5.3)."
        ),
        recipient="director",
        on_message=on_message,
        # Structural marker (orchestrator record, not agent self-report) so the FE renders this as the
        # PRIMARY Director-facing message and keeps the raw worker report as secondary drill-down.
        extra_payload={"is_synthesis": True},
    )
    if isinstance(result, ParseFailure):
        # WS-E graceful fallback (non-negotiable): visible + metered, NO control-flow / next_action
        # change — the caller settles EXACTLY as before (keeps the raw report + the pre-existing
        # next_action). The synthesis is additive observability, never a new dead-end.
        await _record_internal_turn_parse_failure(
            db,
            state.version_id,
            state.current_stage,
            turn_label="Zhrnutie Koordinátora",
            failed=result,
            on_message=on_message,
        )
        return None
    return result.summary or None


# ---------------------------------------------------------------------------
# Verify hooks (F-007 §5.4)
# ---------------------------------------------------------------------------


def verify_mechanical(slug: str, block: PipelineStatusBlock, baseline_sha: Optional[str] = None) -> Optional[str]:
    """Deterministic backend checks. Returns a failure reason or ``None`` (pass).

    Every ``commits[]`` hash must exist in the project repo (``git show``) and
    every ``deliverables[]`` path must exist on disk. No agent involved.

    When ``baseline_sha`` is given (per-task build loop, F-007 §6 / CR-NS-020 CR-3),
    additionally require the work to sit in ``baseline_sha..HEAD``: the baseline must
    exist + be an ancestor of HEAD, and every reported commit must be new since the
    baseline (reachable from HEAD, NOT from the baseline). This enforces "never build
    on an unverified base" — a task's commits are scoped to its own baseline, never an
    earlier task's. ``baseline_sha=None`` (gates / release) keeps existence-only checks.
    """
    project_root = claude_agent.PROJECTS_ROOT / slug
    for commit in block.commits:
        if not _commit_exists(project_root, commit):
            return f"commit {commit!r} not found in {slug}"
    for rel in block.deliverables:
        if not (project_root / rel).exists():
            return f"deliverable {rel!r} missing on disk"
    if baseline_sha is not None:
        if not _commit_exists(project_root, baseline_sha):
            return f"task baseline {baseline_sha!r} not found in {slug}"
        if not _git_ok(project_root, ["merge-base", "--is-ancestor", baseline_sha, "HEAD"]):
            return f"task baseline {baseline_sha!r} is not an ancestor of HEAD (history diverged)"
        for commit in block.commits:
            if not _git_ok(project_root, ["merge-base", "--is-ancestor", commit, "HEAD"]):
                return f"commit {commit!r} is not reachable from HEAD"
            if _git_ok(project_root, ["merge-base", "--is-ancestor", commit, baseline_sha]):
                return f"commit {commit!r} predates the task baseline (not in baseline..HEAD)"
    return None


def _commit_exists(project_root: Path, commit_hash: str) -> bool:
    import subprocess

    try:
        result = subprocess.run(
            ["git", "-C", str(project_root), "cat-file", "-e", f"{commit_hash}^{{commit}}"],
            capture_output=True,
            timeout=15,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _git_ok(project_root: Path, args: list[str]) -> bool:
    """Run a git command in *project_root*; True iff it exits 0 (no output captured)."""
    import subprocess

    try:
        return (
            subprocess.run(
                ["git", "-C", str(project_root), *args], capture_output=True, timeout=15, check=False
            ).returncode
            == 0
        )
    except (OSError, subprocess.SubprocessError):
        return False


def _repo_head(project_root: Path) -> Optional[str]:
    """Return the project repo's current HEAD SHA, or ``None`` if it can't be read."""
    import subprocess

    try:
        r = subprocess.run(
            ["git", "-C", str(project_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _repo_parent(project_root: Path, commit: str) -> Optional[str]:
    """Return the SHA of ``commit``'s first parent (``<commit>^``), or ``None`` if unreadable / a root
    commit. Used by accept_merged (WS-B2, CR-NS-031): moving a merged task's baseline to the reported
    commit's parent puts that commit back inside ``baseline..HEAD`` so it passes verify_mechanical."""
    import subprocess

    try:
        r = subprocess.run(
            ["git", "-C", str(project_root), "rev-parse", "--verify", f"{commit}^"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _rev_list_count(project_root: Path, baseline: Optional[str]) -> int:
    """Number of commits in ``baseline..HEAD`` — work that landed since the dispatch baseline (R1-c).

    0 on any git error, a missing/unparseable count, or a NULL baseline. The audit is advisory (Seam #1:
    a mid-dispatch history rewrite is out of scope — the Director reviews ``git log``), so it must never
    raise; a 0 simply reads as "no change detected"."""
    if not baseline:
        return 0
    try:
        r = subprocess.run(
            ["git", "-C", str(project_root), "rev-list", "--count", f"{baseline}..HEAD"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return 0
    out = r.stdout.strip()
    return int(out) if r.returncode == 0 and out.isdigit() else 0


def _lost_work_audit_recorded(db: Session, version_id: uuid.UUID, baseline: str) -> bool:
    """True if a lost-work audit notification for THIS dispatch baseline already exists (R1-c idempotency).

    The timeout catch is re-entered once per parse-retry (the parse-retry machinery is untouched — §5), so
    without this guard a single timed-out dispatch would record N identical notifications. Keyed on the
    frozen ``dispatch_baseline_sha`` → exactly one notification per dispatch (Seam #4)."""
    return (
        db.execute(
            select(PipelineMessage.id)
            .where(
                PipelineMessage.version_id == version_id,
                PipelineMessage.author == "system",
                PipelineMessage.kind == "notification",
                PipelineMessage.payload["lost_work_audit"].astext == "true",
                PipelineMessage.payload["dispatch_baseline_sha"].astext == baseline,
            )
            .limit(1)
        ).first()
        is not None
    )


async def _audit_lost_work(
    db: Session,
    *,
    version_id: uuid.UUID,
    slug: str,
    stage: str,
    timeout_seconds: int,
    on_message: Optional[MessageCallback] = None,
) -> Optional[dict[str, Any]]:
    """R1-c (D1): on an agent envelope-loss (timeout/crash), audit ``baseline..HEAD`` and surface any
    committed-but-lost work to the Director — *review & continue*, never silently lost, never auto-merged.

    Reads the dispatch's frozen ``dispatch_baseline_sha``, compares it to the current HEAD, and records ONE
    ``system→director`` ``notification`` carrying ``{dispatch_baseline_sha, post_timeout_head_sha,
    timeout_seconds, detected_commit_count}`` (idempotent per baseline). Returns the audit dict (with the
    Slovak ``next_action`` the caller settles on), or ``None`` when there is no dispatch baseline to audit
    against (e.g. an internal sub-turn before ``_begin_dispatch`` armed one, or an unreadable repo) — in which
    case the caller keeps its existing escalation. Status is NOT mutated here (the caller owns it)."""
    state = _get_state(db, version_id)
    if state is None or not state.dispatch_baseline_sha:
        return None
    baseline = state.dispatch_baseline_sha
    project_root = claude_agent.PROJECTS_ROOT / slug
    head = _repo_head(project_root)
    count = _rev_list_count(project_root, baseline)
    if count >= 1:
        next_action = f"Vypršal čas agenta — môžu byť zapísané zmeny ({count} commitov). Over 'git log' a pokračuj."
    else:
        next_action = "Vypršal čas agenta — žiadna zmena nezistená. Pokračuj."
    if not _lost_work_audit_recorded(db, version_id, baseline):
        msg = _record_message(
            db,
            version_id=version_id,
            stage=stage,
            author="system",
            recipient="manazer",  # CR-V2-009: lost-work audit (safeguard #3) re-pointed to the Manažér
            kind="notification",
            content=next_action,
            payload={
                "lost_work_audit": True,
                "phase": stage,  # per-turn phase stamp (CR-V2-009)
                "dispatch_baseline_sha": baseline,
                "post_timeout_head_sha": head,
                "timeout_seconds": timeout_seconds,
                "detected_commit_count": count,
            },
        )
        if on_message is not None:
            await on_message(msg)
    return {
        "dispatch_baseline_sha": baseline,
        "post_timeout_head_sha": head,
        "timeout_seconds": timeout_seconds,
        "detected_commit_count": count,
        "next_action": next_action,
    }


def _iteration_boundary_seq(db: Session, version_id: uuid.UUID) -> int:
    """The seq of the latest ``verdict`` message — the current gate_g iteration boundary (a verdict is what
    increments ``state.iteration``); 0 on the first iteration. Lets the scope-escalation cap (§F1.5) + the
    prior-Q&A derivation (§F1.6) scope to the CURRENT iteration without an ``iteration`` column on messages."""
    seq = db.execute(
        select(func.max(PipelineMessage.seq)).where(
            PipelineMessage.version_id == version_id,
            PipelineMessage.kind == "verdict",
        )
    ).scalar_one_or_none()
    return int(seq or 0)


def _prior_scope_qa(db: Session, version_id: uuid.UUID) -> list[tuple[str, str]]:
    """gate_g scope questions already answered by the Director THIS iteration (CR-NS-056 §F1.6) — prompt
    CONTEXT so the verify-judge does not re-raise them. Each coordinator scope-question (kind=question, a
    scope-class directive: triage_class=director_decision OR proposed_action=route_to_designer) paired with the
    FIRST Director-authored message of greater seq in any answer channel (kind in {answer, return, question}).
    Empty ⇒ the verify prompt stays byte-identical to today (this only reduces how often the §F1.5 cap is hit)."""
    boundary = _iteration_boundary_seq(db, version_id)
    msgs = (
        db.execute(
            select(PipelineMessage)
            .where(
                PipelineMessage.version_id == version_id,
                PipelineMessage.stage == "gate_g",
                PipelineMessage.seq > boundary,
            )
            .order_by(PipelineMessage.seq.asc())
        )
        .scalars()
        .all()
    )
    pairs: list[tuple[str, str]] = []
    for i, m in enumerate(msgs):
        if m.author != "coordinator" or m.kind != "question":
            continue
        directive = (m.payload or {}).get("coordinator_directive") or {}
        if not (
            directive.get("triage_class") == "director_decision"
            or directive.get("proposed_action") == "coordinator_route_to_designer"
        ):
            continue
        answer = next(
            (n.content for n in msgs[i + 1 :] if n.author == "director" and n.kind in ("answer", "return", "question")),
            None,
        )
        if answer is not None:
            pairs.append((m.content, answer))
    return pairs


async def verify_done(
    db: Session,
    version_id: uuid.UUID,
    block: PipelineStatusBlock,
    on_message: Optional[MessageCallback] = None,
) -> tuple[Optional[str], Optional[dict[str, Any]], bool]:
    """Verify a gate_report before awaiting the Director. ``(reason, directive, is_coordinator_error)``:
    reason on FAIL else None; the judge's ``coordinator_directive`` (dict) on a blocked verdict so the caller
    can classify scope vs mechanical (CR-NS-056 §F1.1); ``is_coordinator_error`` True ONLY when the FAIL is a
    Coordinator SYSTEM error — its OWN verify output stayed unparseable after parse-retries (v0.7.2 R-B). The
    caller (``_verify_with_retries``) uses that flag to escalate a Coordinator-system-error to the Director
    instead of auto-returning the Designer (whose work is fine — re-running it can't fix the Coordinator's
    parse problem). Mirrors ``_coordinator_relay``'s ``(text, directive)`` contract, plus the flag.

    Mechanical checks first (deterministic); then a judgment check by invoking the coordinator agent through
    ``invoke_agent_with_parse_retry`` (v0.7.2 R-A — an unparseable Coordinator verify now self-corrects on a
    bounded re-emit instead of failing immediately, like every other invocation site). The coordinator's block
    must report ``kind != blocked`` and ``awaiting='director'`` to count as a PASS. The Coordinator's judgment
    is a real dispatch-path message → ``on_message`` streams it live (CR-NS-018).
    """
    slug = _project_slug_for_version(db, version_id)
    mech = verify_mechanical(slug, block)
    if mech is not None:
        return mech, None, False

    # CR-1 (v0.7.5; narrowed v0.7.9): App-starts smoke — a deterministic HARD gate, the sibling of
    # ``verify_mechanical``, invoked ONLY at full-flow ``gate_g``. full-flow only: fast_fix never reaches
    # gate_g (FAST_FIX_STAGE_ORDER has no gate_g), so this block is unreachable for a fast_fix version.
    # It runs BEFORE the Coordinator judgment turn, so a smoke FAIL short-circuits (returns a non-None
    # reason) and prevents the judgment — exactly like a ``verify_mechanical`` fail. v0.7.9: the smoke is
    # a BOOT check (app boots + responds), NOT a runtime acceptance run (prod images carry no pytest).
    smoke_verdict_block = ""
    if block.stage == "gate_g":
        version_label = db.execute(select(Version.version_number).where(Version.id == version_id)).scalar_one()
        # gate-g-hardening GAP 1 (A2): boot leg + release-acceptance leg in ONE up/down cycle.
        (smoke_ok, smoke_detail), acceptance = await _run_release_smoke(slug, version_label)
        smoke_msg = _record_message(
            db,
            version_id=version_id,
            stage="gate_g",
            author="system",
            recipient="director",
            kind="notification",
            content=(
                f"App-starts smoke PASS — {smoke_detail}." if smoke_ok else f"App-starts smoke FAIL — {smoke_detail}"
            ),
            payload={"smoke": {"pass": smoke_ok, "detail": smoke_detail}},
        )
        if on_message is not None:
            await on_message(smoke_msg)
        if not smoke_ok:
            # HARD gate: the non-None reason renders as a ``gate_g`` FAIL via the mechanical-block settle.
            return f"App-starts smoke FAIL: {smoke_detail}", None, False
        # gate-g-hardening GAP 1 (A1/A3): record the release-acceptance outcome as its OWN system→director
        # notification (``release_acceptance`` payload) — the boundary-anchored ``_release_acceptance_satisfied``
        # reads it to GATE the PASS verdict. Acceptance does NOT short-circuit verify_done like the boot check:
        # the gate_report still completes so the Director sees the result, but a non-pass/non-skip blocks the
        # PASS (the verdict guard + the disabled FE button) — the §6 "acceptance fails 2/28 → PASS blocked" flow.
        acc_ok, acc_detail, acc_skipped = acceptance  # never None here — the boot leg passed
        acc_msg = _record_message(
            db,
            version_id=version_id,
            stage="gate_g",
            author="system",
            recipient="director",
            kind="notification",
            content=(
                f"Release acceptance PASS — {acc_detail}."
                if acc_ok
                else (
                    f"Release acceptance SKIP — {acc_detail}."
                    if acc_skipped
                    else f"Release acceptance FAIL — {acc_detail}."
                )
            ),
            payload={"release_acceptance": {"pass": acc_ok, "detail": acc_detail, "skipped": acc_skipped}},
        )
        if on_message is not None:
            await on_message(acc_msg)
        # PASS/SKIP → feed both engine verdicts into the Auditor's prompt so the synthesis reflects the
        # deterministic runtime floor (app boots + responds) AND the behavioural acceptance result — not only
        # spec-compliance. An acceptance FAIL is fed HONESTLY (the engine guard, not the Auditor, blocks PASS).
        acc_line = (
            f"release acceptance PASS ({acc_detail})"
            if acc_ok
            else (
                f"release acceptance SKIP ({acc_detail})" if acc_skipped else f"release acceptance FAIL ({acc_detail})"
            )
        )
        smoke_verdict_block = (
            f"Engine-overený app-starts smoke (deterministický boot check, pred týmto verdiktom): {smoke_detail}. "
            f"Engine-overená release acceptance (release_smoke_test.sh proti bežiacej stacke): {acc_line}. "
            "Zohľadni oboje v synthéze — aplikácia reálne nabootovala a odpovedá na HTTP a engine spustil "
            "behaviorálnu acceptance sadu; ak acceptance neprešla do exit-0, PASS je engine-om zablokovaný. "
        )

    # §F1.6 (CR-NS-056): feed the Director's already-answered scope Q&A this iteration into the prompt so the
    # judge does not re-raise them. Empty ⇒ ``prior_scope_block`` is "" → the prompt is byte-identical to today.
    prior = _prior_scope_qa(db, version_id)
    prior_scope_block = ""
    if prior:
        pairs = "\n".join(f"{i + 1}. Q: {q} / Director: {a}" for i, (q, a) in enumerate(prior))
        prior_scope_block = (
            pairs + " Na tieto otázky rozsahu už Director reagoval — NEoznačuj ich znova ako blocker, ak "
            "nepribudol NOVÝ problém alebo mechanická chyba (chýbajúca citácia / P-2). "
        )

    judgment = await invoke_agent_with_parse_retry(
        db,
        version_id=version_id,
        role="coordinator",
        stage=block.stage,
        prompt=(
            f"Verifikuj DONE report fázy '{block.stage}': spec compliance + žiadny "
            "claim bez authoritative source (P-2). "
            + smoke_verdict_block
            + prior_scope_block
            # E7 (F-008 §3, CR-NS-033): if you flag a problem, triage it + append a structured directive.
            + "Ak nájdeš problém, klasifikuj ho (triage podľa charteru §7.1) a popri slovenskom relayi "
            "pripoj štruktúrovaný `coordinator_directive` (triage_class, proposed_action, target, params, "
            "rationale, úprimná confidence) — pričom `target` musí byť OBJEKT {task_id?, role?, commit?} "
            "alebo úplne vynechaný, NIKDY nie voľný text."
            + _DIRECTOR_FORMAT_BRIEF
            + "Ukonči <<<PIPELINE_STATUS>>> blokom (F-007-orchestration-cockpit.md §5.3)."
        ),
        on_message=on_message,
        # CR-2 GATING (audit 2026-06-18): the verify turn carries the headline-first brief (above) but is
        # NOT unconditionally a Director-facing prominent-rail message — on a gate_report PASS the synthesis
        # is the Director-facing turn, and in the auto-return loop the worker is re-dispatched (agent_working,
        # not the Director's turn). So `is_director_brief` is tagged by the CALLER's settle (only when the
        # verify actually settles to awaiting_director/blocked) via `_mark_latest_coordinator_brief` — never
        # here on every turn.
    )
    if isinstance(judgment, ParseFailure):
        # WS-E (CR-NS-037): the verify-judge turn exhausted parse-retries (v0.7.2 R-A: it now actually
        # retries before landing here) → no message recorded. Make it visible + count its tokens; the
        # caller still treats the non-None reason as a verify FAIL (control flow unchanged). The
        # ``is_coordinator_error=True`` flag (3rd tuple slot, v0.7.2 R-B) tells the caller this FAIL is the
        # Coordinator's OWN parse problem — escalate to the Director, never auto-return the Designer.
        await _record_internal_turn_parse_failure(
            db,
            version_id,
            block.stage,
            turn_label="Overenie DONE reportu Koordinátorom",
            failed=judgment,
            on_message=on_message,
        )
        return f"coordinator verify unparseable: {judgment.reason}", None, True
    if judgment.kind == "blocked":
        # §F1.1 (CR-NS-056): plumb the judge's directive out so the caller classifies scope vs mechanical.
        # NOT a Coordinator-system-error (R-B): this is a real Coordinator block carrying a directive — keep
        # the existing scope-vs-mechanical path (a genuine Designer-report defect still auto-returns).
        directive = (
            judgment.coordinator_directive.model_dump(mode="json")
            if judgment.coordinator_directive is not None
            else None
        )
        return f"coordinator flagged: {judgment.question or judgment.summary}", directive, False
    return None, None, False


def _mark_latest_coordinator_brief(db: Session, version_id: uuid.UUID, stage: str) -> None:
    """CR-2 (v0.7.3) — tag the most recent Coordinator turn at ``stage`` as a Director-facing brief
    (``payload.is_director_brief=true`` → the FE prominent rail).

    Called from a settle that puts the Director directly on the Coordinator's verify turn (a mechanical /
    scope block that records NO synthesis). Because it tags only the LATEST Coordinator turn at the settle
    point, it never touches a gate_report PASS (the synthesis fired after it is the Director-facing turn) nor
    an auto-return-loop intermediate verify (older Coordinator turns stay untagged) — exactly the audit gate.
    """
    msg = db.execute(
        select(PipelineMessage)
        .where(
            PipelineMessage.version_id == version_id,
            PipelineMessage.stage == stage,
            PipelineMessage.author == "coordinator",
        )
        .order_by(PipelineMessage.seq.desc())
        .limit(1)
    ).scalar_one_or_none()
    if msg is not None:
        # Reassign (not in-place mutate) so SQLAlchemy flags the JSONB column dirty.
        msg.payload = {**(msg.payload or {}), "is_director_brief": True}
        db.flush()


async def _coordinator_relay(
    db: Session,
    state: PipelineState,
    worker_block: PipelineStatusBlock,
    on_message: Optional[MessageCallback] = None,
) -> tuple[Optional[str], Optional[dict[str, Any]]]:
    """Coordinator review of a worker's question/blocked turn → a relay for the Director.

    Hub-and-spoke (CR-NS-018): no worker output reaches the Director unreviewed.
    Only gate_reports went through the Coordinator (:func:`verify_done`); a worker
    ``question`` / ``blocked`` used to bypass it. This invokes the Coordinator
    (parse-retry like the verify path) to check the work done + assess the
    question, and returns its relay text. The Coordinator's turn is recorded as
    its own thread message by :func:`invoke_agent`. Returns ``None`` if the relay
    is unparseable after retries — the caller then surfaces the worker's original
    question (never a dead-end). The worker stays ``current_actor``, so the
    Director's answer routes back to the worker via :func:`dispatch_directive`.

    Returns ``(relay_text, directive)`` — the directive (the block's ``coordinator_directive`` as a dict, or
    ``None``) lets the build loop consider an autonomous recovery (Pillar B, CR-NS-055); non-build callers
    ignore it. ``(None, None)`` on an unparseable relay (the caller falls back to the worker's question).
    """
    kind_label = "je blokovaný" if worker_block.kind == "blocked" else "položil otázku"
    asked = worker_block.question or worker_block.summary
    # Fast-Fix Lane (F-009 §3 D5, CR-NS-103): append the operator brief on fast_fix only — at build a routine
    # question → autonomous `coordinator_answer_question`; at release never ask about the engine-owned deploy.
    fast_fix_relay = _FAST_FIX_RELAY_BRIEF if state.flow_type == "fast_fix" else ""
    relay = await invoke_agent_with_parse_retry(
        db,
        version_id=state.version_id,
        role="coordinator",
        stage=state.current_stage,
        prompt=(
            f"Worker '{state.current_actor}' vo fáze '{state.current_stage}' {kind_label}: {asked}. "
            "Over jeho doterajšiu prácu (deliverables/commits) a posúď otázku; priprav pre Directora "
            "relay — čo treba rozhodnúť. " + _FIRST_PRINCIPLES_TRIAGE +
            # Pillar B (CR-NS-055 §B.2): first-principles triage. In the build loop a clear bounded recovery
            # with honest high confidence auto-executes; at design gates the build-recovery actions don't
            # apply, so this is harmless guidance there.
            # E7 (F-008 §3, CR-NS-033): triage the surfaced problem + append a structured directive.
            "Klasifikuj problém (triage podľa charteru §7.1 — spec_problem / programmer_guidance / "
            "nex_studio_bug / director_decision) a popri relayi pripoj štruktúrovaný `coordinator_directive` "
            "(proposed_action + úprimná confidence); Director ho schváli a engine vykoná."
            + _DIRECTOR_FORMAT_BRIEF
            + "Ukonči <<<PIPELINE_STATUS>>> blokom (F-007-orchestration-cockpit.md §5.3)."
            + fast_fix_relay
        ),
        on_message=on_message,
        # CR-2: a Director-facing brief → the FE gives it the prominent rail (mirrors is_synthesis).
        extra_payload={"is_director_brief": True},
    )
    if isinstance(relay, ParseFailure):
        # WS-E (CR-NS-037): the relay turn exhausted parse-retries → no message recorded. Make it
        # visible + count its tokens, then KEEP the existing fallback (caller surfaces the raw worker
        # question). No control-flow change.
        await _record_internal_turn_parse_failure(
            db,
            state.version_id,
            state.current_stage,
            turn_label="Posúdenie otázky workera Koordinátorom",
            failed=relay,
            on_message=on_message,
        )
        return None, None
    directive = relay.coordinator_directive.model_dump(mode="json") if relay.coordinator_directive is not None else None
    return (relay.question or relay.summary), directive


# ---------------------------------------------------------------------------
# Dispatch + actions
# ---------------------------------------------------------------------------


def _begin_dispatch(db: Session, state: PipelineState) -> None:
    """Mark the actor for ``current_stage`` as working — synchronous, instant.

    First half of the old ``_dispatch``: sets ``agent_working`` and flushes so
    ``POST /action`` can return immediately. The actual agent run is deferred to
    the background task (:func:`run_dispatch`). A terminal/``done`` stage (no
    actor) is a no-op, leaving the caller's terminal state intact.
    """
    stage = state.current_stage
    actor = STAGE_ACTOR.get(stage)
    if actor is None:  # ``done`` or unknown — nothing to dispatch.
        return
    # R1-b (D1/D2): capture the dispatch baseline ONCE per dispatch and arm the durable single-flight flag.
    # The ``if not`` guard freezes the baseline across parse-retries (a retry re-enters here without
    # overwriting it — Seam #4); a fresh dispatch (after the settle listener reset it to NULL) re-captures
    # from a clean repo HEAD. ``_repo_head`` returns None when the repo is unreadable → no baseline, so the
    # lost-work audit degrades to a no-op rather than crashing (advisory, Seam #1).
    if not state.dispatch_baseline_sha:
        project_root = claude_agent.PROJECTS_ROOT / _project_slug_for_version(db, state.version_id)
        state.dispatch_baseline_sha = _repo_head(project_root)
    state.dispatch_in_flight = True
    state.current_actor = actor
    state.status = "agent_working"
    state.next_action = f"Agent '{actor}' pracuje na fáze '{stage}'."
    db.flush()


# Fast-Fix UAT auto-deploy (F-009, CR-NS-098/-101). The lane REDEPLOYS an existing UAT — it does NOT
# re-provision it. We run a plain ``docker compose up -d --build --force-recreate`` against the UAT's OWN
# ``/opt/uat/<slug>/docker-compose.yml`` (hand-authored like NEX Ledger OR uat-deploy.py-provisioned like
# NEX Inbox), so there is no template re-render, no port reallocation, no nginx rewrite — the working UAT
# is preserved (uat-deploy.py is a PROVISIONER and would overwrite all three). ``/opt/uat`` +
# /var/run/docker.sock are mounted into the backend image, so the compose is reachable. The FE build-arg
# is stamped via ``VITE_APP_VERSION`` (post-commit version scheme). Module-level so tests can monkeypatch
# the path/existence; the timeout backstops the docker build (~1–2 min).
UAT_ROOT: Path = Path("/opt/uat")
UAT_DEPLOY_TIMEOUT = 900


def _uat_compose_path(uat_slug: str) -> Path:
    """The UAT's existing compose file — ``/opt/uat/<uat_slug>/docker-compose.yml``."""
    return UAT_ROOT / uat_slug / "docker-compose.yml"


def _uat_compose_exists(uat_slug: str) -> bool:
    """True if the UAT has a redeployable compose (hand-authored or provisioned)."""
    return _uat_compose_path(uat_slug).is_file()


def _fe_app_version(project_slug: str) -> str:
    """``0.1.<commit-count>`` for the project repo — the post-commit version the FE build-arg stamps.

    ``<commit-count>`` = ``git -C /opt/projects/<slug> rev-list --count HEAD``. Falls back to ``0.1.0`` if
    git / the repo is unavailable — the redeploy still runs, only the FE version label is generic (never a
    hard failure over a missing counter).
    """
    project_root = claude_agent.PROJECTS_ROOT / project_slug
    try:
        result = subprocess.run(
            ["git", "-C", str(project_root), "rev-list", "--count", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return "0.1.0"
    count = result.stdout.strip()
    return f"0.1.{count}" if result.returncode == 0 and count.isdigit() else "0.1.0"


async def _run_uat_deploy(project_slug: str, uat_slug: str) -> tuple[bool, str]:
    """Plain redeploy of the UAT's EXISTING compose (``docker compose -f … up -d --build --force-recreate``).

    Respects ``/opt/uat/<uat_slug>/docker-compose.yml`` as-is — no re-render, no port reallocation, no
    nginx rewrite (unlike the uat-deploy.py provisioner) — and stamps the FE build-arg via
    ``VITE_APP_VERSION`` (post-commit version scheme).

    Returns ``(ok, detail)``: ``ok`` is True only when ``up`` exits 0 AND the deployed app actually
    SERVES (icc-deploy §5.6 #2 — "exit 0" is not "serves"); ``detail`` is ``"OK"`` on success, else a
    short tail of the deploy error / the serve-verify reason. Never raises — a spawn failure / timeout /
    serve-verify failure becomes ``(False, reason)`` so the caller settles to ``blocked`` rather than a
    false success. Async (``create_subprocess_exec`` + ``await``) so the ~1–2 min docker build never
    blocks the event loop.
    """
    compose = _uat_compose_path(uat_slug)
    cmd = ["docker", "compose", "-f", str(compose), "up", "-d", "--build", "--force-recreate"]
    env = {**os.environ, "VITE_APP_VERSION": _fe_app_version(project_slug)}
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=env
        )
    except OSError as exc:
        return False, f"deploy sa nepodarilo spustiť: {exc}"
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=UAT_DEPLOY_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        return False, f"deploy prekročil časový limit ({UAT_DEPLOY_TIMEOUT}s)"
    if proc.returncode != 0:
        tail = (stdout or b"").decode("utf-8", "replace").strip()[-300:]
        return False, (f"exit {proc.returncode}: {tail}" if tail else f"exit {proc.returncode}")
    # ``up`` exit 0 only means the containers were created — NOT that the app serves (the nex-asistent
    # false-success bug). Verify the app actually responds before reporting success.
    return await _verify_uat_serves(project_slug, uat_slug)


async def _verify_uat_serves(project_slug: str, uat_slug: str) -> tuple[bool, str]:
    """Post-``up`` readiness gate for a UAT deploy (icc-deploy §5.6 #2): confirm the deployed app actually
    SERVES before :func:`_run_uat_deploy` reports success — every backend ``/api`` responds AND every
    frontend serves (HTTP ``< 500``). Returns ``(True, "OK")`` once verified, else ``(False, reason)`` so
    the caller settles to ``blocked`` rather than a false success.

    The UAT compose strips host ports (Traefik routes by network), so this probes IN-network via
    ``docker compose exec``: the backend probes itself at ``localhost`` and probes the frontend (nginx, no
    Python) over the network by its unique UAT container name. Service keys + container ports are read from
    the SOURCE compose (the UAT compose's stripped ports can't reveal the container port); ``up --build``
    rebuilds the UAT from that same source, so the ports match the live containers.

    Defensive skips return ``(True, "OK")`` (the app deployed; we just can't probe it) — NEVER a new false
    FAIL: no UAT compose (the caller already guards existence), an unreadable source compose, or no backend
    service (no Python container to probe from). The real serve check runs whenever a backend exists."""
    uat_compose = _uat_compose_path(uat_slug)
    if not uat_compose.is_file():
        logger.warning("UAT serve-verify skipped (uat=%s) — no UAT compose to probe", uat_slug)
        return True, "OK"
    src_compose = claude_agent.PROJECTS_ROOT / project_slug / "docker-compose.yml"
    try:
        services = (yaml.safe_load(src_compose.read_text()) or {}).get("services") or {}
    except (OSError, yaml.YAMLError):
        logger.warning("UAT serve-verify skipped (slug=%s) — source compose unreadable", project_slug)
        return True, "OK"
    roles = uat_provisioner.identify_service_roles(services)
    be_role = roles["backend"]
    if be_role is None:
        logger.warning("UAT serve-verify skipped (slug=%s) — no backend service to probe from", project_slug)
        return True, "OK"

    base = ["docker", "compose", "-f", str(uat_compose)]
    # Backend: probe /api on localhost inside the backend container (any <500 = "responds").
    be_port = uat_provisioner.detect_internal_port(services[be_role], 8000)
    be_ready, be_last = await _await_http_ready(base, be_role, be_port, host="localhost", path="/api")
    if not be_ready:
        return False, f"backend '{be_role}' /api not responding within {ACCEPTANCE_SMOKE_READY_TIMEOUT}s: {be_last}"
    # Frontend: probe / on the frontend nginx FROM the backend, addressing it by its unique UAT container
    # name (the service-name alias collides across UATs on the shared nex-proxy-net; the container name
    # does not). nginx ships no Python, so it cannot probe itself.
    fe_role = roles["frontend"]
    if fe_role is not None:
        fe_port = uat_provisioner.detect_internal_port(services[fe_role], 80)
        fe_host = f"uat-{uat_slug}-{fe_role}"
        fe_ready, fe_last = await _await_http_ready(base, be_role, fe_port, host=fe_host, path="/")
        if not fe_ready:
            return False, f"frontend '{fe_role}' not serving within {ACCEPTANCE_SMOKE_READY_TIMEOUT}s: {fe_last}"
    return True, "OK"


# Engine-owned GitHub release publish (v0.8.0 CR-1). ``RELEASE_PUBLISH_TIMEOUT`` bounds the CI WATCH —
# ``≈ STAGE_TIMEOUT["release"]`` (900s); a slower CI is NOT a false block (the push already succeeded →
# "still running"). ``RELEASE_PUBLISH_STEP_TIMEOUT`` is the per-subprocess backstop for the quick
# git/gh steps (setup-git / push / rev-parse / run list); ``RELEASE_PUBLISH_PUSH_RETRIES`` mirrors the
# template_bootstrap push retry (354-377). The run REGISTERS a few seconds after the push (≈ a CI
# trigger lag) — poll ``gh run list`` for the pushed HEAD up to ATTEMPTS×INTERVAL before watching.
RELEASE_PUBLISH_TIMEOUT = 900
RELEASE_PUBLISH_STEP_TIMEOUT = 180
RELEASE_PUBLISH_PUSH_RETRIES = 1
RELEASE_PUBLISH_RUN_RESOLVE_ATTEMPTS = 6
RELEASE_PUBLISH_RUN_RESOLVE_INTERVAL = 5  # seconds between run-resolve polls (≈30s budget for CI to register)


async def _run_publish_step(cmd: list[str], timeout: int) -> tuple[int, str]:
    """Run ONE git/gh subprocess for the release publish; never raises. Returns ``(returncode,
    combined_output)``.

    The single subprocess seam for :func:`_run_release_publish` (the unit tests fake THIS, never
    ``git``/``gh`` themselves) — mirrors :func:`_compose_smoke_step` (``create_subprocess_exec`` +
    ``wait_for``, stderr folded into stdout, async so a network round-trip never blocks the event loop).
    Inherits the backend's runtime env — the SAME ``GH_TOKEN`` + ``gh auth setup-git`` credential helper
    create-project uses — which is NEVER read, logged, or returned here. A spawn failure → ``(127,
    reason)``; a timeout → ``(124, reason)`` (sentinel non-zero codes the caller treats as that step's
    failure, like a real non-zero exit)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
    except OSError as exc:
        return 127, f"spawn failed: {exc}"
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, f"timeout ({timeout}s)"
    return proc.returncode, (stdout or b"").decode("utf-8", "replace")


async def _resolve_pushed_ci_run(repo_full_name: str, head_sha: str) -> Optional[str]:
    """The GitHub Actions run id whose ``headSha`` is exactly the pushed HEAD, or ``None`` when
    undeterminable (gh error / not yet registered / unparseable).

    Matching on the SHA (not "the latest run") ties the watch to the commit the publish just pushed —
    a stale already-green run can never be mistaken for this release's CI (a false PASS). The caller
    polls this (the run registers a few seconds after the push). ``None`` → the caller keeps polling,
    then treats CI as "still running" (the push already succeeded — never a false block)."""
    rc, out = await _run_publish_step(
        ["gh", "run", "list", "-R", repo_full_name, "--limit", "20", "--json", "databaseId,headSha"],
        RELEASE_PUBLISH_STEP_TIMEOUT,
    )
    if rc != 0:
        return None
    try:
        runs = json.loads(out or "[]")
    except (ValueError, TypeError):
        return None
    for run in runs if isinstance(runs, list) else []:
        if isinstance(run, dict) and run.get("headSha") == head_sha:
            run_id = run.get("databaseId")
            return str(run_id) if run_id is not None else None
    return None


async def _run_release_publish(project_slug: str, repo_full_name: str) -> tuple[bool, str]:
    """Engine-owned GitHub publish of a finalized release (v0.8.0 CR-1): push the project's local
    commits to GitHub and verify CI, using the backend's EXISTING ``GH_TOKEN`` + ``gh auth setup-git``
    credential helper — the SAME path create-project uses (no new credential; nothing token-valued is
    read/logged/returned).

    Returns ``(ok, detail)`` and NEVER raises (modelled on :func:`_run_uat_deploy`): a spawn failure /
    timeout becomes a settled outcome, never a hang. Steps:

    1. ``gh auth setup-git`` — idempotent; wires the HTTPS credential helper (template_bootstrap pattern,
       339-348). A non-zero exit is NON-fatal — the push below surfaces the real credential error.
    2. ``git push origin main`` in ``/opt/projects/<slug>`` with a retry on a transient failure (mirror
       template_bootstrap 354-377). Push failure after retries → ``(False, "git push failed: <err>")``.
    3. Verify CI for the pushed HEAD: resolve the run whose ``headSha`` is the pushed HEAD (poll
       ``gh run list``, since the run registers a few seconds after the push), then ``gh run watch
       <id> --exit-status`` bounded by :data:`RELEASE_PUBLISH_TIMEOUT`. CI green → ``(True, "published +
       CI green (<id>)")``; CI red → ``(False, "CI failed (<id>): <tail>")``; can't determine / watch
       times out → ``(True, "pushed; CI still running (<id>) — monitor")`` (the push SUCCEEDED — do NOT
       false-block on a slow/undeterminable CI)."""
    project_root = claude_agent.PROJECTS_ROOT / project_slug

    # 1. Wire creds — idempotent; non-zero is non-fatal (the push surfaces any real credential error).
    await _run_publish_step(["gh", "auth", "setup-git"], RELEASE_PUBLISH_STEP_TIMEOUT)

    # 2. Push (with one retry on a transient failure) — mirror template_bootstrap 354-377.
    push_cmd = ["git", "-C", str(project_root), "push", "origin", "main"]
    last_err = ""
    for _attempt in range(RELEASE_PUBLISH_PUSH_RETRIES + 1):
        rc, out = await _run_publish_step(push_cmd, RELEASE_PUBLISH_STEP_TIMEOUT)
        if rc == 0:
            break
        last_err = out.strip()[-400:]
    else:
        return False, f"git push failed: {last_err}"

    # 3. Verify CI for the pushed HEAD. Resolve the local HEAD, then poll for ITS run (registration lag).
    rc, out = await _run_publish_step(
        ["git", "-C", str(project_root), "rev-parse", "HEAD"], RELEASE_PUBLISH_STEP_TIMEOUT
    )
    head_sha = out.strip() if rc == 0 else ""
    if not head_sha:
        return True, "pushed; CI still running (HEAD nezistený) — monitor"

    run_id: Optional[str] = None
    for attempt in range(RELEASE_PUBLISH_RUN_RESOLVE_ATTEMPTS):
        run_id = await _resolve_pushed_ci_run(repo_full_name, head_sha)
        if run_id is not None:
            break
        if attempt < RELEASE_PUBLISH_RUN_RESOLVE_ATTEMPTS - 1:
            await asyncio.sleep(RELEASE_PUBLISH_RUN_RESOLVE_INTERVAL)
    if run_id is None:
        return True, "pushed; CI still running (run zatiaľ nezaregistrovaný) — monitor"

    rc, out = await _run_publish_step(
        ["gh", "run", "watch", run_id, "--exit-status", "-R", repo_full_name], RELEASE_PUBLISH_TIMEOUT
    )
    if rc == 0:
        return True, f"published + CI green ({run_id})"
    if rc in (124, 127):  # our watch timed out / could not spawn — push already succeeded; never false-block CI.
        return True, f"pushed; CI still running ({run_id}) — monitor"
    return False, f"CI failed ({run_id}): {out.strip()[-300:]}"


# App-starts acceptance smoke (v0.7.5 CR-1) — the deterministic HARD gate behind full-flow ``gate_g``.
ACCEPTANCE_SMOKE_TIMEOUT = 900  # matches UAT_DEPLOY_TIMEOUT — covers ``up --build`` + the acceptance suite.
# gate-g-hardening GAP 1 (A1): bounds the host-run ``release_smoke_test.sh`` against the already-booted
# isolated stack — a SEPARATE budget from the build/boot above (the script's own assertions, no rebuild).
RELEASE_ACCEPTANCE_TIMEOUT = 900
# Readiness gate (v0.7.5 CR-1 robustness, Director Obs-2): ``up --wait`` only guarantees the container is
# RUNNING — a backend WITHOUT a healthcheck may still be booting/migrating. Poll ``/health`` up to this
# budget BEFORE the suite so the first acceptance request never races the boot into a false FAIL.
ACCEPTANCE_SMOKE_READY_TIMEOUT = 120  # bounded wait for the app to answer /health after ``up``.
ACCEPTANCE_SMOKE_READY_INTERVAL = 3  # seconds between readiness polls.


async def _compose_smoke_step(cmd: list[str], timeout: int) -> tuple[int, str]:
    """Run ONE ``docker compose`` subprocess for the acceptance smoke; never raises.

    Returns ``(returncode, combined_output)``. Mirrors :func:`_run_uat_deploy`'s subprocess dance
    (``create_subprocess_exec`` + ``wait_for``, stderr folded into stdout) — async so the docker
    build never blocks the event loop. A spawn failure → ``(127, reason)``; a timeout → ``(124,
    reason)`` (sentinel non-zero codes so the caller treats both as a FAIL, same as a real non-zero
    exit).
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
    except OSError as exc:
        return 127, f"spawn failed: {exc}"
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, f"timeout ({timeout}s)"
    return proc.returncode, (stdout or b"").decode("utf-8", "replace")


def _acceptance_smoke_override(compose_path: Path) -> str:
    """Build an ephemeral compose override that strips ``container_name`` + host ``ports`` from
    every service of *compose_path*.

    Under the isolated compose project ``-p <slug>-smoke`` the only remaining collision sources with
    a concurrently-running live UAT of the same project are the project's FIXED ``container_name``
    values and its published HOST ports (asistent binds ``nex-asistent-backend`` + ``10180/10182/
    10183``). Resetting both lets compose auto-name the containers per the smoke project and skip host
    publishing entirely; networks/volumes are already project-name-prefixed, so they isolate for free.

    Uses the Compose-Spec ``!reset`` tag (validated on the backend's compose plugin): an additive
    override CONCATENATES ``ports``, so a plain ``ports: []`` cannot remove a base binding — ``!reset``
    does. Acceptance tests run INSIDE the container (``exec``), so host ports are never needed.
    """
    data = yaml.safe_load(compose_path.read_text()) or {}
    services = data.get("services") or {}
    lines = ["services:"]
    for name in services:
        lines.append(f"  {name}:")
        lines.append("    container_name: !reset null")
        lines.append("    ports: !reset []")
    return "\n".join(lines) + "\n"


def _compose_backend_port(compose_path: Path) -> Optional[int]:
    """The CONTAINER port the ``backend`` service listens on, from its first ``ports`` entry — the
    target for the in-container ``/health`` readiness poll. Handles the short forms (``"port"`` /
    ``"host:port"`` / ``"ip:host:port"``, optional ``/proto``) and the long form (``{target: …}``).
    Returns ``None`` when undeterminable (no ``backend`` service / no ``ports`` / unparseable) → the
    caller SKIPS the readiness poll rather than guess (never invents a NEW false FAIL)."""
    data = yaml.safe_load(compose_path.read_text()) or {}
    backend = (data.get("services") or {}).get("backend") or {}
    ports = backend.get("ports") or []
    if not ports:
        return None
    entry = ports[0]
    if isinstance(entry, dict):  # long syntax: {target: 10180, published: …}
        target = entry.get("target")
        return int(target) if isinstance(target, int) or (isinstance(target, str) and target.isdigit()) else None
    container = str(entry).split("/", 1)[0].split(":")[-1]  # short syntax: container port is last colon segment
    return int(container) if container.isdigit() else None


def _readiness_probe_src(port: int, *, host: str = "localhost", path: str = "/health") -> str:
    """In-container stdlib Python probe (the same interpreter that runs the app — no curl dependency;
    slim Python images like asistent's ``python:3.12-slim`` ship no curl). Probes
    ``http://{host}:{port}{path}`` — ``host`` defaults to ``localhost`` (probe the own container) but can
    target a SIBLING service over the compose network (e.g. the frontend nginx, which has no Python of its
    own) by passing its service/container name.

    Exit ``0`` = **READY**: the server returned an HTTP response with status ``< 500`` — a 2xx/3xx success
    OR a 4xx (e.g. 404, where the probe path simply isn't a declared route — irrelevant; the app's real
    routes are exercised separately). Exit ``1`` = **keep polling**: status ``>= 500`` (server up but
    signalling starting/unavailable) OR no HTTP response at all (connection refused / reset / DNS /
    timeout). Path-agnostic (v0.7.7) — a 404 at the probe path now means "up", so a versioned health route
    (nex-asistent's ``/api/v1/health``) no longer false-FAILs the gate. Prints ``status <code>`` / ``err
    <e>`` so the caller can surface the last observation."""
    url = f"http://{host}:{port}{path}"
    return (
        "import sys, urllib.request, urllib.error\n"
        "try:\n"
        f"    r = urllib.request.urlopen('{url}', timeout=5)\n"
        "    print('status', getattr(r, 'status', 200)); sys.exit(0)\n"
        "except urllib.error.HTTPError as e:\n"
        "    print('status', e.code); sys.exit(0 if e.code < 500 else 1)\n"
        "except Exception as e:\n"
        "    print('err', e); sys.exit(1)\n"
    )


async def _await_http_ready(
    base: list[str],
    exec_service: str,
    port: int,
    *,
    host: str = "localhost",
    path: str = "/health",
    timeout: int = ACCEPTANCE_SMOKE_READY_TIMEOUT,
    interval: int = ACCEPTANCE_SMOKE_READY_INTERVAL,
) -> tuple[bool, str]:
    """Poll an in-container HTTP endpoint (run the stdlib probe inside ``exec_service`` via
    ``docker compose exec``) until the server RESPONDS (any status ``< 500``) or ``timeout`` elapses.

    ``exec_service`` is the compose service whose container runs the probe (it must have Python — a
    backend); ``host``/``port``/``path`` are the probe TARGET. With ``host=localhost`` the service probes
    itself; with ``host=<sibling>`` it probes another service over the compose network (used to reach the
    frontend nginx, which ships no Python). Returns ``(True, last)`` once the server responds, else
    ``(False, last)`` on timeout. The status ``< 500`` classification lives in :func:`_readiness_probe_src`
    (it runs in-container); here exit 0 is READY and a non-zero exit keeps polling."""
    cmd = base + ["exec", "-T", exec_service, "python", "-c", _readiness_probe_src(port, host=host, path=path)]
    attempts = max(1, timeout // interval)
    last = "no response"
    for i in range(attempts):
        rc, out = await _compose_smoke_step(cmd, 30)
        if rc == 0:
            return True, out.strip()[-200:] or "ready"
        last = out.strip()[-200:] or f"exit {rc}"
        if i < attempts - 1:
            await asyncio.sleep(interval)
    return False, last


async def _await_acceptance_app_ready(base: list[str], port: int) -> tuple[bool, str]:
    """Poll the ``backend`` service's in-container health endpoint until the SERVER RESPONDS (any HTTP
    status ``< 500``) or the budget (:data:`ACCEPTANCE_SMOKE_READY_TIMEOUT`) elapses. ``up --wait`` only
    guarantees the container is RUNNING — a backend WITHOUT a healthcheck may still be booting/migrating,
    so without this gate the first acceptance request races the boot into a confusing connection-refused
    mid-suite (a FALSE FAIL on a HARD gate). Returns ``(True, last)`` once the server responds, else
    ``(False, last)`` on timeout.

    Readiness = "the server is accepting + handling HTTP requests", NOT "this exact path returns 2xx"
    (v0.7.7, LIVE-confirmed: nex-asistent serves health at the versioned ``/api/v1/health``, so a probe to
    ``/health`` gets 404 — which now correctly means "up"). Thin wrapper over :func:`_await_http_ready`
    (the ``backend``-probes-itself case): probe ``http://localhost:<port>/health`` from the backend."""
    return await _await_http_ready(base, "backend", port, host="localhost", path="/health")


def _compose_frontend_port(compose_path: Path) -> Optional[int]:
    """The CONTAINER port the ``frontend`` service listens on, from its first ``ports`` entry — the
    target for the in-network frontend reachability probe (the nginx analog of
    :func:`_compose_backend_port`). Handles the short forms (``"port"`` / ``"host:port"`` /
    ``"ip:host:port"``, optional ``/proto``) and the long form (``{target: …}``). Returns ``None`` when
    undeterminable (no ``frontend`` service / no ``ports`` / unparseable) → the caller falls back to the
    nginx default (80) rather than guess a wrong port."""
    data = yaml.safe_load(compose_path.read_text()) or {}
    frontend = (data.get("services") or {}).get("frontend") or {}
    ports = frontend.get("ports") or []
    if not ports:
        return None
    entry = ports[0]
    if isinstance(entry, dict):  # long syntax: {target: 80, published: …}
        target = entry.get("target")
        return int(target) if isinstance(target, int) or (isinstance(target, str) and target.isdigit()) else None
    container = str(entry).split("/", 1)[0].split(":")[-1]  # short syntax: container port is last colon segment
    return int(container) if container.isdigit() else None


@dataclass
class _SmokeStack:
    """A live, isolated smoke stack shared by the boot leg + the release-acceptance leg of ONE up/down
    cycle (gate-g-hardening GAP 1 A2). ``base`` is the ``docker compose -p <slug>-smoke -f … -f …``
    prefix; ``compose``/``override`` are the file paths (the override path is handed to the host
    acceptance script so it can ``docker compose exec`` into the running stack — host ports were stripped,
    so there is no host-published port to curl); ``roles`` is the FE/BE/DB role→service map; ``up_rc`` /
    ``up_detail`` carry the ``up --build --wait`` outcome so the driver settles a build failure."""

    base: list[str]
    compose: Path
    override: Path
    project: str
    roles: dict[str, Optional[str]]
    up_rc: int
    up_detail: str

    @property
    def up_ok(self) -> bool:
        return self.up_rc == 0


@contextlib.asynccontextmanager
async def _boot_smoke_stack(project_slug: str, compose: Path, roles: dict[str, Optional[str]]):
    """gate-g-hardening GAP 1 (A2): bring the project's compose UP ONCE under an isolated ``-p
    <slug>-smoke`` project, yield a :class:`_SmokeStack` for the boot + release-acceptance legs to SHARE,
    then tear it down ONCE (``down -v`` + drop the temp override). Was two functions each with its own
    ``up``/``down`` — a double build + a teardown race; this is the single cycle. Never raises; the
    ``finally`` always tears down (modelled on the old ``_run_app_starts_smoke`` try/finally)."""
    logger.info("smoke stack starting (slug=%s)", project_slug)
    project = f"{project_slug}-smoke"
    tmpdir = Path(tempfile.mkdtemp(prefix=f"{project_slug}-smoke-"))
    override = tmpdir / "smoke.override.yml"
    base = ["docker", "compose", "-p", project, "-f", str(compose), "-f", str(override)]
    stack = _SmokeStack(
        base=base, compose=compose, override=override, project=project, roles=roles, up_rc=-1, up_detail=""
    )
    try:
        # Isolate — ephemeral override stripping container_name + host ports — then up (build + boot;
        # ``--wait`` blocks until healthchecks pass; Ollama reached via the app's own extra_hosts).
        override.write_text(_acceptance_smoke_override(compose))
        stack.up_rc, stack.up_detail = await _compose_smoke_step(
            base + ["up", "-d", "--build", "--wait"], ACCEPTANCE_SMOKE_TIMEOUT
        )
        yield stack
    finally:
        # Teardown — ALWAYS: tear the isolated stack (+ its volumes) down and drop the temp override.
        await _compose_smoke_step(base + ["down", "-v"], 120)
        shutil.rmtree(tmpdir, ignore_errors=True)


async def _run_app_starts_smoke(stack: _SmokeStack) -> tuple[bool, str]:
    """Boot leg (v0.7.5 CR-1, narrowed v0.7.9): against the already-UP isolated stack, verify the deployed
    app actually BOOTS and RESPONDS to HTTP (the v0.7.7 path-agnostic readiness poll) — the deterministic
    runtime floor behind full-flow ``gate_g`` (unfakeable, no test env needed).

    It does NOT run the acceptance suite IN the prod image (v0.7.9: ``python:3.12-slim`` carries no pytest);
    behavioural depth is the host-run ``release_smoke_test.sh`` (:func:`_run_release_acceptance`), a sibling
    leg of the SAME up/down cycle. Returns ``(ok, detail)`` and never raises: backend-not-responding /
    frontend-not-serving → ``(False, reason)``. The compose-structure pre-checks (no compose / a backend web
    app with no frontend) and the ``up`` itself are the driver's job (:func:`_run_release_smoke`); this leg
    only probes the running stack."""
    base, compose, roles = stack.base, stack.compose, stack.roles
    # Backend ready (the boot check) — ``up --wait`` returns once the container RUNS; a backend without a
    # healthcheck may still be booting/migrating. Poll /health until the server RESPONDS (status <500;
    # v0.7.7 path-agnostic). Undeterminable port → skip the poll (no NEW false FAIL — ``up`` succeeded).
    port = _compose_backend_port(compose)
    if port is not None:
        ready, last = await _await_acceptance_app_ready(base, port)
        if not ready:
            return False, f"app did not boot / not responding within {ACCEPTANCE_SMOKE_READY_TIMEOUT}s: {last}"
    # Frontend reachable — the frontend nginx has no Python, so probe it FROM the backend over the isolated
    # project network by service name (no host ports; the override stripped them). A 404 at ``/`` still
    # means "serving" (<500). This catches a frontend that built but never serves.
    fe_role = roles["frontend"]
    if fe_role is not None and roles["backend"] is not None:
        fe_port = _compose_frontend_port(compose) or 80
        fe_ready, fe_last = await _await_http_ready(base, roles["backend"], fe_port, host=fe_role, path="/")
        if not fe_ready:
            return False, (f"frontend '{fe_role}' not serving within {ACCEPTANCE_SMOKE_READY_TIMEOUT}s: {fe_last}")
    return True, "app booted + responds"


# gate-g-hardening GAP 1 (B): the anti-empty-floor sentinel ``release_smoke_test.sh`` MUST print —
# ``ASSERTIONS_RUN=<n>``. An empty ``set -e`` script that exit-0's without running anything is a FALSE
# green; the absence of the sentinel (or ``n==0``) is a FAIL, not a pass (parsed by the engine, below).
_ASSERTIONS_RUN_RE = re.compile(r"ASSERTIONS_RUN=(\d+)")


def _parse_assertions_run(output: str) -> Optional[int]:
    """The LAST ``ASSERTIONS_RUN=<n>`` count printed by ``release_smoke_test.sh`` (anti-empty floor), or
    ``None`` when the script printed no sentinel at all. ``None`` / ``0`` ⇒ the script asserted nothing
    (a false exit-0) → the caller FAILs it."""
    matches = _ASSERTIONS_RUN_RE.findall(output)
    return int(matches[-1]) if matches else None


async def _run_acceptance_script(script: Path, env: dict[str, str]) -> tuple[int, str]:
    """Run the host-executable ``release_smoke_test.sh`` (against the already-booted isolated stack) with
    the smoke-stack addressing env, bounded by :data:`RELEASE_ACCEPTANCE_TIMEOUT`; never raises. Mirrors
    :func:`_compose_smoke_step`: a spawn failure → ``(127, reason)``, a timeout → ``(124, reason)``
    (sentinel non-zero codes the caller treats as a FAIL). The script reaches the app via ``docker compose
    exec`` (host ports are stripped), so the compose project/files are passed through ``env``."""
    full_env = {**os.environ, **env}
    try:
        proc = await asyncio.create_subprocess_exec(
            "bash", str(script), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=full_env
        )
    except OSError as exc:
        return 127, f"spawn failed: {exc}"
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=RELEASE_ACCEPTANCE_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, f"timeout ({RELEASE_ACCEPTANCE_TIMEOUT}s)"
    return proc.returncode, (stdout or b"").decode("utf-8", "replace")


async def _run_release_acceptance(stack: _SmokeStack, project_slug: str) -> tuple[bool, str, bool]:
    """Release-acceptance leg (gate-g-hardening GAP 1 A1): run the project's black-box host-executable
    ``release_smoke_test.sh`` against the ALREADY-BOOTED isolated *stack* (NOT pytest in the prod image),
    requiring exit-0 AND a non-zero ``ASSERTIONS_RUN`` (anti-empty floor). Returns ``(ok, detail,
    skipped)``.

    **Archetype-conditional** (the key honesty fix): a web app (a ``backend`` service is present in the
    compose) with NO ``release_smoke_test.sh`` is a **FAIL** ("required but missing") — never a silent SKIP
    that would let the PASS through unchecked. A SKIP is legit ONLY for a pure lib/worker stack (no
    ``backend`` role); the no-compose case is SKIPped one level up in the driver."""
    script = claude_agent.PROJECTS_ROOT / project_slug / "release_smoke_test.sh"
    is_web_app = stack.roles["backend"] is not None
    if not script.is_file():
        if is_web_app:
            logger.warning(
                "release acceptance FAIL (slug=%s) — release_smoke_test.sh required but missing", project_slug
            )
            return False, "release_smoke_test.sh required but missing (web app — acceptance is mandatory)", False
        return True, "SKIPPED — no release_smoke_test.sh (pure lib/worker, no backend service)", True
    env = {
        "SMOKE_PROJECT": stack.project,
        "SMOKE_COMPOSE": str(stack.compose),
        "SMOKE_OVERRIDE": str(stack.override),
        "SMOKE_BACKEND": stack.roles["backend"] or "",
        "SMOKE_FRONTEND": stack.roles["frontend"] or "",
        "SMOKE_BACKEND_PORT": str(_compose_backend_port(stack.compose) or ""),
    }
    rc, out = await _run_acceptance_script(script, env)
    if rc != 0:
        return False, f"release_smoke_test.sh exit {rc}: {out.strip()[-400:]}", False
    assertions = _parse_assertions_run(out)
    if not assertions:  # None (no sentinel) or 0 — a false exit-0 that asserted nothing.
        return False, f"anti-empty floor: ASSERTIONS_RUN={assertions} — the acceptance script ran no assertions", False
    return True, f"release acceptance PASS — {assertions} assertions", False


async def _run_release_smoke(
    project_slug: str, version_label: str
) -> tuple[tuple[bool, str], Optional[tuple[bool, str, bool]]]:
    """gate-g-hardening GAP 1: the boot leg + the release-acceptance leg in ONE up/down cycle (A2). Returns
    ``((boot_ok, boot_detail), acceptance)`` where ``acceptance`` is ``(ok, detail, skipped)`` — or ``None``
    when the boot leg failed/short-circuited so acceptance never ran (the caller settles on the boot FAIL).

    Graceful SKIP when the project has no ``docker-compose.yml`` (a boot check needs a compose to boot): both
    legs SKIP (legit non-web). A backend web app with NO frontend service short-circuits to a structural FAIL
    BEFORE any build (icc-deploy §5.6 #1 — the nex-asistent "no FE emitted" bug; no point building a broken
    compose). Never raises."""
    root = claude_agent.PROJECTS_ROOT / project_slug
    compose = root / "docker-compose.yml"
    if not compose.is_file():
        logger.info("smoke SKIPPED (slug=%s, version=%s) — no docker-compose.yml", project_slug, version_label)
        skip = "SKIPPED — no docker-compose.yml"
        return (True, skip), (True, skip, True)
    services = (yaml.safe_load(compose.read_text()) or {}).get("services") or {}
    roles = uat_provisioner.identify_service_roles(services)
    if roles["backend"] is not None and roles["frontend"] is None:
        logger.warning("smoke FAIL (slug=%s) — backend web app has no frontend service", project_slug)
        return (False, "compose has a backend web app but no frontend service"), None
    async with _boot_smoke_stack(project_slug, compose, roles) as stack:
        if not stack.up_ok:
            return (False, f"up exit {stack.up_rc}: {stack.up_detail.strip()[-400:]}"), None
        boot_ok, boot_detail = await _run_app_starts_smoke(stack)
        if not boot_ok:
            return (boot_ok, boot_detail), None
        acceptance = await _run_release_acceptance(stack, project_slug)
        return (boot_ok, boot_detail), acceptance


def _release_acceptance_satisfied(db: Session, version_id: uuid.UUID) -> bool:
    """gate-g-hardening GAP 1 (A3): the engine release-acceptance gate behind a gate_g PASS.

    ``True`` only when the LATEST ``release_acceptance`` notification of THIS iteration reports a real
    exit-0 (``pass==True``) or a legit non-web SKIP (``skipped==True`` — a pure lib/worker / no-compose
    project with no running app to assert against). A recorded FAIL (``pass==False`` and not skipped) or NO
    acceptance notification at all ⇒ ``False`` ⇒ the verdict handler refuses the PASS.

    Freshness is anchored on the iteration boundary — :func:`_iteration_boundary_seq` (the latest
    ``verdict`` seq) — NOT the gate_report: the acceptance notification is recorded BEFORE the Auditor's
    gate_report, so an "after gate_report" anchor would never see it and a PASS could never unlock."""
    boundary = _iteration_boundary_seq(db, version_id)
    rows = (
        db.execute(
            select(PipelineMessage.payload)
            .where(
                PipelineMessage.version_id == version_id,
                PipelineMessage.author == "system",
                PipelineMessage.kind == "notification",
                PipelineMessage.seq > boundary,
            )
            .order_by(PipelineMessage.seq.desc())
        )
        .scalars()
        .all()
    )
    for payload in rows:
        if isinstance(payload, dict) and isinstance(payload.get("release_acceptance"), dict):
            acc = payload["release_acceptance"]
            return acc.get("pass") is True or acc.get("skipped") is True
    return False


async def _fast_fix_auto_deploy(
    db: Session, state: PipelineState, *, on_message: Optional[MessageCallback] = None
) -> None:
    """Redeploy the project's UAT after a fast_fix release-verify PASS (F-009, CR-NS-098).

    The fast-fix lane is end-to-end ("zadáš → vidíš na UAT → akceptuješ") only if the Director SEES the
    fix running on UAT before the single ``uat_accept`` touch. Resolves the version's ``project.uat_slug``:

    * **NULL** (no UAT configured) → skip gracefully with a ``system→director`` note and settle to
      ``awaiting_director`` (the Director still accepts; nothing was deployed — never silently blocked).
    * **set** → run :func:`_run_uat_deploy`. Success → ``awaiting_director`` (the Director's ``uat_accept``).
      Failure (non-zero / spawn error / timeout) → ``blocked`` with the deploy error in ``next_action`` —
      surfaced to the Director, never hidden, never silently marked done.

    Mutates ``state.status`` / ``state.next_action`` and records the outcome message; the caller flushes.
    """
    version_id = state.version_id
    project = db.execute(
        select(Project).join(Version, Version.project_id == Project.id).where(Version.id == version_id)
    ).scalar_one_or_none()
    uat_slug = project.uat_slug if project is not None else None
    project_slug = project.slug if project is not None else _project_slug_for_version(db, version_id)

    if not uat_slug:
        msg = _record_message(
            db,
            version_id=version_id,
            stage="release",
            author="system",
            recipient="director",
            kind="notification",
            content="UAT nie je pre projekt nakonfigurované — preskakujem deploy.",
            payload={"uat_deploy": {"skipped": True}},
        )
        if on_message is not None:
            await on_message(msg)
        state.status = "awaiting_director"
        state.next_action = "Director: over a akceptuj (UAT deploy preskočený — projekt nemá UAT)."
        return

    if not _uat_compose_exists(uat_slug):
        msg = _record_message(
            db,
            version_id=version_id,
            stage="release",
            author="system",
            recipient="director",
            kind="notification",
            content=f"UAT compose pre '{uat_slug}' nenájdený — preskakujem deploy.",
            payload={"uat_deploy": {"uat_slug": uat_slug, "skipped": True, "reason": "compose_missing"}},
        )
        if on_message is not None:
            await on_message(msg)
        state.status = "awaiting_director"
        state.next_action = f"Director: over a akceptuj (UAT deploy preskočený — compose pre '{uat_slug}' chýba)."
        return

    # H2 (CR-2): self-heal a stale/broken existing render before redeploy — the fast-fix lane had no
    # provisioning path, so a failed/stale render was re-`up`-ed verbatim. Re-PROVISION (rotate_secrets
    # default False → secrets + extra_hosts preserved) only when the render needs it; a working
    # current-iteration render is left untouched. A provision failure → blocked, never a silent re-`up`.
    if _uat_compose_exists(uat_slug) and _uat_render_needs_reprovision(db, version_id):
        version_label = db.execute(select(Version.version_number).where(Version.id == version_id)).scalar_one()
        try:
            await asyncio.to_thread(uat_provisioner.provision_uat, project_slug, uat_slug, version=version_label)
        except Exception as exc:  # noqa: BLE001 — any provision failure must surface as blocked, never re-`up`.
            detail = str(exc)
            msg = _record_message(
                db,
                version_id=version_id,
                stage="release",
                author="system",
                recipient="director",
                kind="notification",
                content=f"UAT provisioning zlyhal ({uat_slug}): {detail}",
                payload={"uat_deploy": {"uat_slug": uat_slug, "ok": False, "provisioned": False, "detail": detail}},
            )
            if on_message is not None:
                await on_message(msg)
            state.status = "blocked"
            state.block_reason = "system_error"  # R4 (D1): engine-side fast-fix UAT provisioning failure
            state.next_action = f"UAT provisioning zlyhal: {detail}. Skús znova alebo vráť."
            return

    ok, detail = await _run_uat_deploy(project_slug, uat_slug)
    content = f"UAT nasadené ({uat_slug}) — over a akceptuj." if ok else f"UAT deploy zlyhal ({uat_slug}): {detail}"
    msg = _record_message(
        db,
        version_id=version_id,
        stage="release",
        author="system",
        recipient="director",
        kind="notification",
        content=content,
        payload={"uat_deploy": {"uat_slug": uat_slug, "ok": ok, "detail": detail}},
    )
    if on_message is not None:
        await on_message(msg)
    if ok:
        state.status = "awaiting_director"
        state.next_action = "Nasadené na UAT — over a akceptuj."
    else:
        state.status = "blocked"
        state.block_reason = "system_error"  # R4 (D1): engine-side UAT deploy failure
        state.next_action = f"UAT deploy zlyhal: {detail}. Skús znova alebo vráť."


async def _release_auto_publish(
    db: Session, state: PipelineState, *, on_message: Optional[MessageCallback] = None
) -> None:
    """Engine-owned GitHub publish of a finalized FULL-FLOW release (v0.8.0 CR-2).

    The Coordinator finalizes the release LOCALLY (clean + secure) but the agent's headless environment
    has NO GitHub credentials — so the ENGINE (which has ``GH_TOKEN``) publishes here. Modelled EXACTLY
    on :func:`_fast_fix_auto_deploy`: resolves the version's ``project.repo_url`` and the repo full name
    (``{github_org}/{slug}`` — the SAME :func:`template_bootstrap._repo_from_url` create-project uses):

    * ``repo_url`` NULL (no GitHub repo) → skip gracefully with a ``system→director`` note and settle to
      ``awaiting_director`` (the Director still accepts; nothing was published — never silently blocked),
      mirroring the ``_fast_fix_auto_deploy`` NULL-slug skip.
    * set → run :func:`_run_release_publish` (push + CI verify). Success → chain the engine UAT-deploy
      (:func:`_release_auto_uat_deploy`, v0.8.1 CR-1), which settles the state (UAT deployed → the
      Director's ``uat_accept``; no UAT configured → an HONEST no-UAT completion). Failure (push/CI
      failed) → ``blocked`` with the publish error in ``next_action`` — surfaced, never hidden.

    Records the outcome as a ``system→director`` notification (payload ``{"release_publish": {ok,
    detail}}``); mutates ``state.status`` / ``state.next_action``; the caller flushes."""
    version_id = state.version_id
    project = db.execute(
        select(Project).join(Version, Version.project_id == Project.id).where(Version.id == version_id)
    ).scalar_one_or_none()
    repo_url = project.repo_url if project is not None else None
    project_slug = project.slug if project is not None else _project_slug_for_version(db, version_id)

    if not repo_url:
        msg = _record_message(
            db,
            version_id=version_id,
            stage="release",
            author="system",
            recipient="director",
            kind="notification",
            content="Projekt nemá nakonfigurovaný GitHub repozitár (repo_url) — preskakujem publikovanie.",
            payload={"release_publish": {"skipped": True, "reason": "no_repo_url"}},
        )
        if on_message is not None:
            await on_message(msg)
        state.status = "awaiting_director"
        state.next_action = "Director: over a akceptuj (GitHub publish preskočený — projekt nemá repo_url)."
        return

    repo_full_name = template_bootstrap._repo_from_url(repo_url, project_slug)
    ok, detail = await _run_release_publish(project_slug, repo_full_name)
    content = "Publikované na GitHub + CI zelené — over a akceptuj." if ok else f"GitHub publish/CI zlyhal: {detail}"
    msg = _record_message(
        db,
        version_id=version_id,
        stage="release",
        author="system",
        recipient="director",
        kind="notification",
        content=content,
        payload={"release_publish": {"repo": repo_full_name, "ok": ok, "detail": detail}},
    )
    if on_message is not None:
        await on_message(msg)
    if ok:
        # v0.8.1 CR-1: publish succeeded → chain the engine UAT-deploy so the full flow reaches the
        # Director's uat_accept with the release actually RUNNING on UAT (parity with the fast-fix lane),
        # or an HONEST "no UAT configured" completion — never a hollow ~1s accept. It sets status +
        # next_action (UAT deployed → awaiting_director; deploy failed → blocked; no UAT → awaiting_director
        # honest). The publish-ok notification above is already on the board, so the Director sees both steps.
        await _release_auto_uat_deploy(db, state, on_message=on_message)
    else:
        state.status = "blocked"
        state.block_reason = "system_error"  # R4 (D1): engine-side GitHub publish / CI failure
        state.next_action = f"GitHub publish/CI zlyhal: {detail}"


async def _release_auto_uat_deploy(
    db: Session, state: PipelineState, *, on_message: Optional[MessageCallback] = None
) -> None:
    """Engine UAT-deploy after a full-flow (new_version) release publish SUCCEEDS (v0.8.1 CR-1).

    Brings the full flow in line with the fast-fix lane: the Director SEES the release running on UAT
    before the single ``uat_accept`` (instead of a hollow ~1s accept that falsely claimed "UAT
    akceptované" though no UAT existed). Reuses the SAME low-level :func:`_run_uat_deploy` the fast-fix
    lane uses — :func:`_fast_fix_auto_deploy` itself is left UNTOUCHED (byte-identical). Resolves the
    version's ``project.uat_slug``:

    * **NULL / compose missing** → graceful, HONEST skip: a ``system→director`` note (``payload={"uat_deploy":
      {"skipped": True, "reason": …}}``) + settle to ``awaiting_director`` with ``"Žiadny UAT
      nakonfigurovaný — dokončíš bez UAT testu."`` — NO false "UAT akceptované" claim (the v0.8.1 honesty fix).
    * **set + compose** → run :func:`_run_uat_deploy`. Success → ``awaiting_director`` (``"Nasadené na UAT
      — over a akceptuj."``). Failure (non-zero / spawn error / timeout) → ``blocked`` with the deploy error
      in ``next_action`` — surfaced to the Director, never hidden.

    v0.9.0 Phase 3 (CR-1): the missing-UAT honest-skip is REPLACED with autonomous provisioning — a
    full-flow release whose project has no UAT yet now derives + persists ``uat_slug``, provisions
    ``/opt/uat/<uat_slug>`` from the source compose (Phase-2 ``uat_provisioner.provision_uat`` — Traefik
    labels baked in), then deploys, so the Director ALWAYS gets a real UAT to accept:

    * **uat_slug NULL** → ``derive_uat_slug(project)`` + ``project_service.set_uat_slug`` (persist; idempotent).
    * **compose missing** → ``provision_uat`` in a thread; a provision failure → ``blocked`` (never silent).
    * then the EXISTING-compose redeploy path: ``_run_uat_deploy`` (build + up). Success → ``awaiting_director``
      with the ``https://uat-<uat_slug>.isnex.eu`` URL in the notification; failure → ``blocked``.

    The ``{"uat_deploy": {...}}`` payload shape is preserved (``ok`` / no ``skipped`` on success) so the
    v0.8.1 honest ``uat_accept`` keys on a real deploy. Full-flow only; fast-fix's ``_fast_fix_auto_deploy``
    is untouched. Mutates ``state.status`` / ``state.next_action`` and records the outcome; the caller flushes."""
    version_id = state.version_id
    project = db.execute(
        select(Project).join(Version, Version.project_id == Project.id).where(Version.id == version_id)
    ).scalar_one_or_none()
    project_slug = project.slug if project is not None else _project_slug_for_version(db, version_id)
    version_label = db.execute(select(Version.version_number).where(Version.id == version_id)).scalar_one()

    # Defensive edge (version with no project — an FK should make this unreachable): cannot derive/provision
    # a UAT without a project → honest no-UAT finish, never a crash.
    if project is None:
        msg = _record_message(
            db,
            version_id=version_id,
            stage="release",
            author="system",
            recipient="director",
            kind="notification",
            content="Žiadny UAT nakonfigurovaný — verziu dokončíš bez UAT testu.",
            payload={"uat_deploy": {"skipped": True, "reason": "no_project"}},
        )
        if on_message is not None:
            await on_message(msg)
        state.status = "awaiting_director"
        state.next_action = "Žiadny UAT nakonfigurovaný — dokončíš bez UAT testu."
        return

    # 1. Derive + persist uat_slug when NULL (autonomous, idempotent — a manual non-null is kept).
    uat_slug = project.uat_slug
    if not uat_slug:
        uat_slug = uat_provisioner.derive_uat_slug(project)
        project_service.set_uat_slug(db, project, uat_slug)

    # 2. Provision the UAT if its compose does not exist yet (first release) OR the existing render is
    #    stale/broken (H2 CR-2 self-heal: a failed deploy or a prior-iteration success → re-render instead of
    #    re-`up`-ing a render the image can't import). A WORKING current-iteration render is preserved — the
    #    predicate returns False, so the redeploy goes straight to _run_uat_deploy (live instance untouched).
    #    rotate_secrets stays default False → secrets + extra_hosts are preserved across the re-provision.
    if not _uat_compose_exists(uat_slug) or _uat_render_needs_reprovision(db, version_id):
        try:
            await asyncio.to_thread(uat_provisioner.provision_uat, project_slug, uat_slug, version=version_label)
        except Exception as exc:  # noqa: BLE001 — any provision failure must surface as blocked, never crash.
            detail = str(exc)
            msg = _record_message(
                db,
                version_id=version_id,
                stage="release",
                author="system",
                recipient="director",
                kind="notification",
                content=f"UAT provisioning zlyhal ({uat_slug}): {detail}",
                payload={"uat_deploy": {"uat_slug": uat_slug, "ok": False, "provisioned": False, "detail": detail}},
            )
            if on_message is not None:
                await on_message(msg)
            state.status = "blocked"
            state.block_reason = "system_error"  # R4 (D1): engine-side full-flow UAT provisioning failure
            state.next_action = f"UAT provisioning zlyhal: {detail}. Skús znova alebo vráť."
            return

    # 3. Deploy (build + up). Traefik auto-routes via the labels the provisioner baked in (no host change).
    ok, detail = await _run_uat_deploy(project_slug, uat_slug)
    url = f"https://uat-{uat_slug}.isnex.eu"
    content = (
        f"UAT nasadené ({uat_slug}) — {url} — over a akceptuj." if ok else f"UAT deploy zlyhal ({uat_slug}): {detail}"
    )
    msg = _record_message(
        db,
        version_id=version_id,
        stage="release",
        author="system",
        recipient="director",
        kind="notification",
        content=content,
        payload={"uat_deploy": {"uat_slug": uat_slug, "ok": ok, "detail": detail}},
    )
    if on_message is not None:
        await on_message(msg)
    if ok:
        state.status = "awaiting_director"
        state.next_action = "Nasadené na UAT — over a akceptuj."
    else:
        state.status = "blocked"
        state.block_reason = "system_error"  # R4 (D1): engine-side full-flow UAT deploy failure
        state.next_action = f"UAT deploy zlyhal: {detail}. Skús znova alebo vráť."


async def run_dispatch(
    db: Session,
    version_id: uuid.UUID,
    on_event: Optional[claude_agent.EventCallback] = None,
    directive: Optional[str] = None,
    *,
    gate_e_dispatch: Optional[str] = None,
    on_message: Optional[MessageCallback] = None,
) -> Optional[PipelineState]:
    """Run the working agent for a phase and settle its status (background); CR-V2-009 4-phase rebuild.

    Reloads the (already ``agent_working``) state, invokes the phase's actor headless via the shared
    parse-retry invoke, and settles ``status`` to ``blocked`` / ``awaiting_manazer`` — OR, when the Miera
    autonómie dial does not stop at this phase boundary, AUTO-CONTINUES to the next phase (returns
    ``agent_working`` so the runner's auto-chain loop runs it; CR-V2-010 dial-settle wiring). Runs in
    :mod:`backend.services.pipeline_runner`'s background task against a fresh session — never inside the
    request. Returns the settled state (``None`` if the version/state vanished).

    ``on_message`` (CR-NS-018) is the incremental-broadcast hook: it fires right after each dispatch-path
    message is recorded so the runner commits + streams it live, instead of batching at round end.

    ``on_event`` (CR-NS-018) streams the agent's activity to the rail.

    ``directive`` (CR-NS-018) is the Manažér's framed message for an ``uprav`` / ``ask`` / ``answer``
    re-dispatch (see :func:`directive_for_action`). When present it IS the agent's prompt; otherwise the
    generic :func:`_directive_for` is used. Threading it here makes the Manažér↔agent loop two-way.

    ``gate_e_dispatch`` — DEFERRED no-op (CR-V2-009): the v1 Gate-E sub-flow selector. The 4-phase model
    has no Gate E (the Auditor's upfront review replaces it in CR-V2-013), so this is always ``None`` from
    the route now. The parameter is kept for signature stability with the runner/route until those FE
    contract CRs drop it.
    """
    state = _get_state(db, version_id)
    if state is None:
        return None
    stage = state.current_stage
    actor = state.current_actor
    if STAGE_ACTOR.get(stage) is None:  # terminal (``done``) — nothing to run.
        return state

    # Návrh round (CR-V2-011): one coherent design doc + the folded EPIC→FEAT→TASK task plan. Owns its own
    # multi-turn lifecycle (design-doc turn → fold the plan via incremental passes → SHARED dial-settle), so
    # it early-returns here instead of going through the single generic turn below. ``directive`` (an
    # uprav/ask/answer re-dispatch) is threaded as the design-turn prompt (two-way comms).
    if stage == "navrh":
        return await _run_navrh_round(db, state, on_event=on_event, directive=directive, on_message=on_message)

    # Programovanie round (CR-V2-012): the AI Agent's SELF-CHECKING coding loop executing the Návrh task plan
    # (implement + own tests/verification per task; NO per-task Auditor — the independent Auditor verifies once
    # at Verifikácia). Owns its own multi-task lifecycle + the SHARED dial-settle at the end, so it
    # early-returns here. ``directive`` (an uprav/answer/pokracovat re-dispatch) seeds attempt 1 of the resumed
    # task (two-way comms — the Coordinator relay is retired in v2).
    if stage == "programovanie":
        return await _run_build_round(db, state, on_event=on_event, directive=directive, on_message=on_message)

    # 4-phase dispatch. The v1 stage-specific routing (gate_e per-question round / build per-task loop /
    # task_plan incremental passes / kickoff triage / release publish) is collapsed: each phase runs as a
    # generic agent turn through the shared invoke path, with a per-phase BRIEF. Milestone C/D give each
    # phase its rich brief — Príprava (the interactive Zadanie→Špecifikácia dialogue, CR-V2-010) + Návrh
    # (the design doc + task plan, CR-V2-011) + Programovanie (the self-checking loop, CR-V2-012 above)
    # here; Verifikácia (CR-V2-014) next. The v1 ``_run_gate_e_round`` survives as a deferred-RED helper
    # CR-V2-013 re-points, but is NOT reachable from this 4-phase routing (``_run_task_plan_round`` folded
    # into Návrh, CR-V2-011; ``_run_build_round`` was rebuilt + re-homed to Programovanie, CR-V2-012).
    if directive is not None:
        prompt = directive  # the Manažér's framed uprav/ask/answer message IS the prompt (direct comms)
    elif stage == "priprava":
        # Príprava round (CR-V2-010): the init prompt + the interactive spec-dialogue brief (read Zadanie →
        # systematize → ask until understood → propose → write the Špecifikácia .md). DESIGN-BEARING prompt.
        prompt = _priprava_directive(db, state.version_id)
    else:
        prompt = _augment_brief_with_backlog(db, state.version_id, stage, _directive_for(stage, state.flow_type))
    result = await invoke_agent_with_parse_retry(
        db,
        version_id=state.version_id,
        role=actor,
        stage=stage,
        prompt=prompt,
        on_event=on_event,
        on_message=on_message,
    )

    if isinstance(result, ParseFailure):
        if result.lost_work is not None:
            # R1-c lost-work audit (R-BLAST safeguard #3): the agent's envelope was lost (timeout/crash) but
            # the commit audit ran. Surface "work may have landed — review & continue" instead of a bare
            # blocked: the audit notification is already recorded (by the timeout catch), so settle to
            # ``awaiting_manazer`` with the audit next_action. Never auto-proceeds (the phase does NOT
            # advance); the Manažér reviews ``git log`` and continues. Committed-but-lost work is surfaced,
            # never silently dropped.
            state.status = "awaiting_manazer"
            state.next_action = result.lost_work["next_action"]
            db.flush()
            return state
        # Parse-retries exhausted (CR-NS-022 §2): settle blocked directly (no Coordinator relay — retired in
        # v2; the AI Agent reports to the Manažér itself, design §2.2). The board shows a plain next_action,
        # never the raw parser error.
        state.status = "blocked"
        state.block_reason = "parse_exhaustion"  # R4 (D1): worker produced no parseable output after retries
        state.next_action = "Blokované — agent nevrátil platný výstup. Usmerni (Uprav) alebo odpovedz."
        db.flush()
        return state

    if result.kind in ("question", "blocked"):
        # The agent asked the Manažér something (direct comms — no Coordinator relay, design §2.2). Settle
        # blocked with an agent_question reason so the board offers ``answer``.
        state.status = "blocked"
        state.block_reason = "agent_question"  # R4 (D1): a worker question for the Manažér
        state.next_action = f"Agent '{actor}' sa pýta: {result.question}"
        db.flush()
        return state

    # gate_report / done / answer-class agent output → the phase produced its final output.
    # Príprava artifact persistence (CR-V2-010): on the Príprava gate_report that CLOSES the phase, persist
    # + verify the Špecifikácia .md artifact before settling. A missing artifact (checkout exists but the
    # spec file was not written) is a real failure → blocked, the phase does NOT advance to its approval.
    if stage == "priprava" and result.kind == "gate_report":
        spec_err = _persist_priprava_spec(db, state, result)
        if spec_err is not None:
            state.status = "blocked"
            state.block_reason = "agent_error"  # R4 (D1): the phase deliverable is missing on disk
            state.next_action = "Špecifikácia nebola zapísaná — usmerni agenta (Uprav) a zopakuj prípravu."
            db.flush()
            return state

    # Dial-settle wiring (Milestone-C SHARED — CR-V2-010, inherited by 011/012). At a settled phase
    # boundary the Miera autonómie dial governs auto-continue vs stop. ``_settle_phase_boundary`` returns
    # True when it AUTO-ADVANCED the phase (status is now ``agent_working`` at the next phase → the runner's
    # auto-chain loop runs it in this same single-flight task). The two always-stops (the end-Príprava
    # ``approve_spec`` Špecifikácia approval + deploy) are NEVER auto-continued (Príprava is not a
    # dial-governed boundary), and the Verifikácia end sign-off preserves the no-silent-done invariant.
    if _settle_phase_boundary(db, state):
        return state  # agent_working at the next phase — the auto-chain loop continues the build
    # The dial stopped here (or this is a non-boundary / always-stop phase, or Verifikácia auto-signed-off
    # to ``done``): settle for the Manažér's schvaľovací bod, unless already terminal (Hotovo).
    if state.status != "done":
        state.status = "awaiting_manazer"
        state.next_action = f"Manažér: posúdiť výstup fázy '{stage}'."
        db.flush()
    return state


_GATE_E_NO_EDIT = (
    "odpovedz — vysvetli, či je to pokryté; ak je to medzera, LEN navrhni riešenie "
    "(nastav gap_found=true + proposed_fix), NEUPRAVUJ žiadny súbor"
)


async def _block_failed(
    state: PipelineState,
    db: Session,
    reason: str,
    *,
    failed: Optional[ParseFailure] = None,
    on_message: Optional[MessageCallback] = None,
) -> PipelineState:
    # Plain next_action — no raw technical reason on the board (CR-NS-022 §2 refinement). The
    # ``reason`` is kept internal (logged); the Director acts via Vrátiť / Konzultovať.
    logger.info("pipeline %s blocked at %s: %s", state.version_id, state.current_stage, reason)
    state.status = "blocked"
    state.block_reason = "agent_error"  # R4 (D1): a worker turn failed (build-task / sub-flow agent error)
    state.next_action = "Blokované — pozri priebeh a rozhodni (Vrátiť / Konzultovať)."
    # WS-D (CR-NS-036): this block path records no relay message of its own, so a worker
    # parse-exhaustion's tokens would otherwise be lost. When the failed turn carried usage, record a
    # plain system→director note carrying it (the ONLY message on this path — not a duplicate) so
    # aggregate_pipeline_usage counts it; the note also gives the Director a reason this blocked.
    # Gated explicitly on usage (CR-036 behavior) — NOT on _failure_metrics_payload being non-empty,
    # which since WS-E (CR-NS-037) also returns timing-only; this preserves the original usage-gating.
    if failed is not None and failed.usage is not None:
        msg = _record_message(
            db,
            version_id=state.version_id,
            stage=state.current_stage,
            author="system",
            recipient="director",
            kind="notification",
            content="Fáza zablokovaná — agent nevrátil platný výstup ani po opravách; pozri priebeh a rozhodni.",
            payload=_failure_metrics_payload(failed),
        )
        if on_message is not None:
            await on_message(msg)
    db.flush()
    return state


async def _coordinator_review_gap(
    db: Session,
    state: PipelineState,
    designer_block: PipelineStatusBlock,
    on_message: Optional[MessageCallback] = None,
) -> None:
    """Branch B upward leg (§2): the Coordinator reviews the Designer's proposed fix and
    records a recommendation for the Director. Reuses the parse-retry; its message is the
    recommendation later composed into the Coordinator-relayed ``fix`` directive."""
    review = await invoke_agent_with_parse_retry(
        db,
        version_id=state.version_id,
        role="coordinator",
        stage="gate_e",
        prompt=(
            f"Návrhár našiel medzeru a navrhol opravu (bez editu): {designer_block.proposed_fix}. "
            "Prekontroluj návrh a daj Directorovi odporúčanie (opraviť / ponechať + prečo)."
            + _DIRECTOR_FORMAT_BRIEF
            + "Ukonči <<<PIPELINE_STATUS>>> blokom (F-007-orchestration-cockpit.md §5.3)."
        ),
        on_message=on_message,
        # CR-2: the Gate-E gap recommendation the Director reads at the per-question stop → Director-facing
        # by construction → always the prominent rail.
        extra_payload={"is_director_brief": True},
    )
    if isinstance(review, ParseFailure):
        # WS-E (CR-NS-037): a discarded gap-review parse-failure was a fully silent no-op → make it
        # visible + count its tokens. Still non-blocking advisory (the function returns None as before).
        await _record_internal_turn_parse_failure(
            db,
            state.version_id,
            "gate_e",
            turn_label="Revízia navrhovanej opravy Koordinátorom",
            failed=review,
            on_message=on_message,
        )


def _gate_e_scope_directive(db: Session, version_id: uuid.UUID) -> str:
    """The scope/budget prefix injected on EVERY Customer Gate E turn (design §2.1, Phase 3 — orchestrator
    side; the per-project Customer charter carries the matching depth rules). Tells the Customer to walk ONLY
    the okruhy/screens this version actually touches (small change → a few targeted questions, greenfield →
    a full walk) and the scope-scaled question budget (floor target + ceiling). Derived deterministically from
    the version's spec footprint (:func:`_gate_e_question_budget`)."""
    floor, ceiling = _gate_e_question_budget(db, version_id)
    asked = _gate_e_question_count(db, version_id)
    return (
        "ROZSAH PREVIERKY (Gate E, škálovaný podľa footprintu špecifikácie tejto verzie): choď LEN cez "
        "okruhy/obrazovky, ktoré táto verzia REÁLNE dotýka — malá zmena = pár cielených otázok k dotknutým "
        "miestam, greenfield = plná previerka. Rozpočet otázok: aspoň "
        f"{floor} (Gate E existuje na chytenie spec medzier — pod-previerka je opačné zlyhanie), strop {ceiling}. "
        f"Doteraz položených: {asked}. Keď je dotknutý rozsah pokrytý, signalizuj coverage_complete. "
    )


def _gate_e_continue_prompt(db: Session, version_id: uuid.UUID) -> str:
    """The Customer's next-turn base prompt when re-dispatched WITHOUT a Director directive — the FIRST gate_e
    turn OR an autonomous Branch-A / topic-boundary continue (Phase 3, §5.2). Mirrors the manual approve@gate_e
    relay (:func:`dispatch_directive`) so the Customer (a separate session) SEES the Designer's reply and never
    re-asks a covered point as a false finding — the relay the auto-chain loop cannot carry (it dispatches with
    ``directive=None``). The scope/budget prefix is added by the caller, so this returns only the relay base."""
    milestone = _latest_gate_e_milestone(db, version_id)
    if milestone is not None and milestone.author == "designer":  # auto-continued past a Branch-A answer
        return (
            f"Návrhár odpovedal na tvoju otázku: «{milestone.content}». Odpoveď je bez medzery "
            "(Koordinátor ju auto-ratifikoval). Pokračuj ďalšou otázkou previerky Gate E. "
            "Ukonči <<<PIPELINE_STATUS>>> blokom (F-007-orchestration-cockpit.md §5.3)."
        )
    if milestone is not None:  # auto-continued past a clean topic boundary (latest = Customer gate_report)
        return (
            "Okruh je uzavretý bez otvorených nálezov — pokračuj v previerke Gate E ďalším okruhom "
            "(alebo ďalšou otázkou). Ukonči <<<PIPELINE_STATUS>>> blokom (F-007-orchestration-cockpit.md §5.3)."
        )
    return _directive_for("gate_e")  # first gate_e turn — no prior milestone to relay


async def _run_gate_e_round(
    db: Session,
    state: PipelineState,
    *,
    on_event: Optional[claude_agent.EventCallback] = None,
    directive: Optional[str] = None,
    gate_e_dispatch: Optional[str] = None,
    on_message: Optional[MessageCallback] = None,
) -> PipelineState:
    """One Gate E per-question exchange (F-007-gate-e revised §2/§5): Director-gated.

    Hub-and-spoke, **one question at a time** — never chains the next question without
    the Director. Per re-dispatch (by ``gate_e_dispatch``):

    * ``"coordinator_consult"`` (``ask`` / ``return`` @ gate_e): invoke ONLY the
      **Coordinator** with the Director's input → it revises its recommendation →
      STOP (``awaiting_director``). The Director never addresses the worker directly.
    * ``"designer_edit"`` (Branch B ``fix``): the Designer first edits per the
      Coordinator-relayed directive, then the round continues to the next question.
    * ``None``: one Customer turn — ``gate_report``+``topic_done`` → round boundary;
      a ``question`` → one Designer answer (no-edit: explain / on a gap only PROPOSE)
      → if ``gap_found`` the Coordinator reviews the proposal → STOP.

    Each turn is a ``pipeline_message`` (stage=gate_e, ``seq``-ordered) with the chain
    ``recipient`` (Z→N→K→D, §5), and every turn streams with its real ``_role`` so the
    rail steps Customer→Designer→Coordinator. Parse failure → ``blocked`` (never guess).
    """
    if gate_e_dispatch == "coordinator_consult":  # ask/return @ gate_e — Coordinator revises
        revised = await invoke_agent_with_parse_retry(
            db,
            version_id=state.version_id,
            role="coordinator",
            stage="gate_e",
            prompt=directive,
            on_event=on_event,
            on_message=on_message,
        )
        if isinstance(revised, ParseFailure):
            return await _block_failed(state, db, revised.reason, failed=revised, on_message=on_message)
        state.status = "awaiting_director"
        state.next_action = "Director: posúď prepracované odporúčanie Koordinátora (Schváliť návrh / Ponechať)."
        db.flush()
        return state

    if gate_e_dispatch == "designer_edit":  # Branch B: the Designer applies the approved fix, then continue
        edit = await invoke_agent_with_parse_retry(
            db,
            version_id=state.version_id,
            role="designer",
            stage="gate_e",
            prompt=directive,
            on_event=on_event,
            recipient="coordinator",
            on_message=on_message,
            # Mark the edit turn so it can NEVER raise a gap in the deterministic count
            # (§5): it executes an approved fix; new gaps come only via the Q&A loop.
            extra_payload={"is_fix_edit": True},
        )
        if isinstance(edit, ParseFailure):
            return await _block_failed(state, db, edit.reason, failed=edit, on_message=on_message)
        # Symmetric relay (§5): tell the Customer what was fixed before its next question.
        customer_prompt = (
            f"Tvoj nález Návrhár opravil podľa schváleného riešenia: «{edit.summary}». "
            "Pokračuj ďalšou otázkou previerky Gate E. Ukonči <<<PIPELINE_STATUS>>> "
            "blokom (F-007-orchestration-cockpit.md §5.3)."
        )
    else:
        # directive set = a Director-framed relay (manual approve/leave); None = the first turn OR an
        # autonomous continue (Phase 3) — reconstruct the Designer-answer relay the auto-chain can't carry.
        customer_prompt = directive if directive is not None else _gate_e_continue_prompt(db, state.version_id)

    # §2.1 (Phase 3): every Customer turn carries the scope/budget context (touched okruhy + floor/ceiling).
    customer_prompt = _gate_e_scope_directive(db, state.version_id) + customer_prompt

    cust = await invoke_agent_with_parse_retry(
        db,
        version_id=state.version_id,
        role="customer",
        stage="gate_e",
        prompt=customer_prompt,
        on_event=on_event,
        recipient="designer",  # Z→N: the Customer's question is for the Designer
        on_message=on_message,
    )
    if isinstance(cust, ParseFailure):
        return await _block_failed(state, db, cust.reason, failed=cust, on_message=on_message)

    if cust.kind == "gate_report" and cust.topic_done:  # round boundary
        # §A.2 site 3 (Gate E topic boundary): Coordinator synthesis before settling. The per-topic report is
        # ALWAYS recorded (durable on the board, §2.3) — even when we auto-continue and don't use it as the
        # next_action.
        synthesis = await _coordinator_synthesis(
            db, state, trigger=f"okruh '{cust.topic or 'okruh'}'", on_message=on_message
        )
        # Phase 3 (§2.3): a CLEAN intermediate topic boundary (NOT coverage_complete) auto-continues to the
        # next okruh — the per-topic report stays durable above. coverage_complete is the FINAL close: the ONE
        # bounded Director sign-off (KEY), never auto-continued.
        if not cust.coverage_complete and await _maybe_autonomous_gate_e_continue(
            db, state, boundary="topic", on_message=on_message
        ):
            return state  # agent_working — the runner auto-chain runs the next okruh
        state.status = "awaiting_director"
        if cust.coverage_complete:
            state.next_action = synthesis or "Director: Gate E pokrytá — posúď a uzavri previerku (jeden podpis)."
        elif _gate_e_budget_reached(db, state.version_id):
            state.next_action = "Director: Gate E dosiahol strop otázok — predĺž previerku alebo ju uzavri."
        else:
            state.next_action = (
                synthesis or f"Director: posúď okruh '{cust.topic or 'okruh'}' (nálezy + riešenia Návrhára)."
            )
        db.flush()
        return state

    if cust.kind in ("question", "blocked"):  # one Customer question → one Designer answer
        designer = await invoke_agent_with_parse_retry(
            db,
            version_id=state.version_id,
            role="designer",
            stage="gate_e",
            prompt=(
                f"Zákazník vo fáze Gate E sa pýta: {cust.question}. {_GATE_E_NO_EDIT}. "
                "Ukonči <<<PIPELINE_STATUS>>> blokom (F-007-orchestration-cockpit.md §5.3)."
            ),
            on_event=on_event,
            recipient="coordinator",  # N→K: the Designer's answer is for the Coordinator
            on_message=on_message,
        )
        if isinstance(designer, ParseFailure):
            return await _block_failed(state, db, designer.reason, failed=designer, on_message=on_message)
        if designer.gap_found:  # Branch B upward leg — Coordinator reviews; a gap is ALWAYS the Director (§2.4)
            state.status = "awaiting_director"
            await _coordinator_review_gap(db, state, designer, on_message)
            state.next_action = "Director: Návrhár našiel medzeru a navrhol opravu — rozhodni Opraviť/Ponechať."
            db.flush()
            return state
        # Branch A — routine answer, no gap: AUTO-CONTINUE to the next question (Phase 3, §2.2) when
        # deterministically clean (0 open findings) and under the scope-scaled budget; else settle.
        if await _maybe_autonomous_gate_e_continue(db, state, boundary="question", on_message=on_message):
            return state  # agent_working — the runner auto-chain runs the next Customer question
        state.status = "awaiting_director"
        if _gate_e_budget_reached(db, state.version_id):
            state.next_action = (
                "Director: Gate E dosiahol strop otázok — predĺž previerku (schváliť → ďalšia otázka) alebo ju uzavri."
            )
        else:
            state.next_action = "Director: posúď odpoveď Návrhára (schváliť → ďalšia otázka)."
        db.flush()
        return state

    # Unexpected Customer output → let the Director judge.
    state.status = "awaiting_director"
    state.next_action = "Director: posúď výstup fázy gate_e."
    db.flush()
    return state


async def _settle_plan_pass_failure(
    db: Session,
    state: PipelineState,
    failed: ParseFailure,
    *,
    note: str,
    on_message: Optional[MessageCallback],
) -> PipelineState:
    """Settle a failed folded task-plan pass (skeleton or per-feat) — R1 envelope-loss parity (v0.7.3,
    CR-1; v2 CR-V2-011 — the plan folds into Návrh, the Coordinator relay is retired, design §2.2).

    Two distinct failure modes, two distinct settles:

    * **Envelope-loss (``ClaudeAgentError`` — timeout/crash) with an armed dispatch baseline**
      (``failed.lost_work`` is set): work may have committed even though the JSON envelope was lost.
      :func:`_plan_pass_once` already recorded the ``_audit_lost_work`` notification (safeguard #3), so
      settle to ``awaiting_manazer`` with its "review & continue" ``next_action`` — the SAME R1 path
      :func:`run_dispatch` takes; NOT a ``blocked`` dead-end.
    * **Hard failure** (``lost_work`` is ``None``): record ONE direct ``system→manazer`` notification (no
      Coordinator relay — the AI Agent reports to the Manažér itself) carrying the failed turn's metrics,
      and HALT ``blocked`` with an ACCURATE ``block_reason`` — ``agent_error`` when it was still a
      ``ClaudeAgentError`` (timeout/crash with no audit baseline), ``parse_exhaustion`` only for a
      genuinely unparseable structured output. Never mislabel a timeout as ``parse_exhaustion``.
    """
    if failed.lost_work is not None:
        state.status = "awaiting_manazer"
        state.next_action = failed.lost_work["next_action"]
        db.flush()
        return state
    msg = _record_message(
        db,
        version_id=state.version_id,
        stage="navrh",
        author="system",
        recipient="manazer",
        kind="notification",
        content=f"Plán úloh sa nepodarilo vygenerovať: {note}. Usmerni agenta (Uprav) a zopakuj Návrh.",
        payload={"phase": "navrh", **(_failure_metrics_payload(failed) or {})},
    )
    if on_message is not None:
        await on_message(msg)
    state.status = "blocked"
    state.block_reason = (
        "agent_error" if failed.reason.startswith(_PLAN_PASS_ENVELOPE_LOSS_PREFIX) else "parse_exhaustion"
    )
    state.next_action = "Blokované — plán úloh sa nepodarilo vygenerovať. Usmerni (Uprav) alebo odpovedz."
    db.flush()
    return state


async def _fold_task_plan_into_navrh(
    db: Session,
    state: PipelineState,
    *,
    on_event: Optional[claude_agent.EventCallback],
    directive: Optional[str],
    on_message: Optional[MessageCallback],
) -> Optional[PipelineState]:
    """Generate the EPIC→FEAT→TASK task plan INCREMENTALLY and fold it into the Návrh phase (CR-V2-011).

    The standalone ``task_plan`` stage/round is removed; the plan is the LAST part of the Návrh design doc
    (design §2.1(2)). This runs AFTER the design-doc turn, on the SAME warm AI-Agent session (so the full
    design doc + the just-emitted skeleton stay in context), then materializes the plan via the
    re-homed :func:`_write_task_plan`:

    * **Pass 1 — skeleton:** EPIC + FEAT (no tasks) + ``cross_cutting_rules``.
    * **Passes 2..N — per feat (skeleton order):** that feat's ``tasks[]``, accumulated in memory.
    * **Assemble** the full :class:`TaskPlan` in skeleton order (so ``_write_task_plan``'s MAX+1 numbering
      matches what the Manažér reviews), record the AI-Agent ``navrh`` ``gate_report`` (carries the plan +
      ``cross_cutting_rules`` the build loop re-reads via :func:`_fetch_cross_cutting_rules`), then call
      :func:`_write_task_plan` (re-homed to the ``navrh`` stage).

    Fail-closed (NO parse exhaustion on a large plan — that is the whole point of the incremental passes):
    a skeleton/per-feat exhaustion → ``blocked`` via :func:`_settle_plan_pass_failure` **naming the feat**,
    writing **nothing**; :data:`MAX_PLAN_FEATS` caps total feats; a defensive assemble/write failure →
    ``blocked``. Returns the SETTLED state on any failure (the caller returns it directly), or ``None`` on
    success (the caller then runs the SHARED dial-settle). The passes use the dedicated
    :func:`_invoke_plan_pass` — ``invoke_agent`` stays byte-identical."""
    version_id = state.version_id

    # Pass 1 — skeleton (EPIC + FEAT, no tasks) + cross_cutting_rules.
    skeleton = await _invoke_plan_pass(
        db,
        state,
        prompt=_task_plan_skeleton_directive(directive),
        json_schema=TASK_PLAN_SKELETON_JSON_SCHEMA,
        parser=parse_task_plan_skeleton,
        label_fn=lambda s: (
            f"Plán — kostra: {len(s.epics)} epík, "
            f"{sum(len(e.feats) for e in s.epics)} funkcií; úlohy sa dopĺňajú per funkcia."
        ),
        on_event=on_event,
        on_message=on_message,
    )
    if isinstance(skeleton, ParseFailure):
        # Skeleton failure: a genuine parse exhaustion → blocked; an envelope-loss (timeout) → R1
        # awaiting_manazer (never a blocked dead-end). See the helper.
        return await _settle_plan_pass_failure(
            db, state, skeleton, note="agent nevrátil platnú kostru plánu ani po opravách", on_message=on_message
        )

    # MAX_PLAN_FEATS cap (fail-closed) — a coarse-grained plan (module ≈ task) never needs this many.
    feat_refs = [(ei, fi, feat) for ei, epic in enumerate(skeleton.epics) for fi, feat in enumerate(epic.feats)]
    if len(feat_refs) > MAX_PLAN_FEATS:
        msg = _record_message(
            db,
            version_id=version_id,
            stage="navrh",
            author="system",
            recipient="manazer",
            kind="notification",
            content=(
                f"Plán má priveľa funkcií ({len(feat_refs)} > strop {MAX_PLAN_FEATS}) — rozklad je príliš "
                "jemnozrnný; treba hrubšiu granularitu (modul ≈ úloha, F-007 §4)."
            ),
            payload={"phase": "navrh"},
        )
        if on_message is not None:
            await on_message(msg)
        state.status = "blocked"
        state.block_reason = "system_error"
        state.next_action = "Plán úloh zamietnutý — rozklad je príliš jemnozrnný. Usmerni Návrh (Uprav)."
        db.flush()
        return state

    # Passes 2..N — per-feat tasks, accumulated in skeleton order.
    feat_tasks: dict[tuple[int, int], list] = {}
    for ei, fi, feat in feat_refs:
        pass_result = await _invoke_plan_pass(
            db,
            state,
            prompt=_task_plan_feat_directive(feat.title),
            json_schema=TASK_PLAN_FEAT_TASKS_JSON_SCHEMA,
            parser=parse_task_plan_feat_tasks,
            label_fn=lambda r, _t=feat.title: f"Plán — funkcia „{_t}“: {len(r.tasks)} úloh.",
            on_event=on_event,
            on_message=on_message,
        )
        if isinstance(pass_result, ParseFailure):
            # Fail-closed: one per-feat pass exhausting → HALT naming the feat, write NOTHING (no half-plan
            # — the write happens only after EVERY feat succeeds). An envelope-loss (timeout) instead
            # settles R1 awaiting_manazer ("review & continue"), never a blocked dead-end (see the helper).
            return await _settle_plan_pass_failure(
                db,
                state,
                pass_result,
                note=f"úlohy pre funkciu „{feat.title}“ sa nepodarilo vygenerovať ani po opravách",
                on_message=on_message,
            )
        feat_tasks[(ei, fi)] = pass_result.tasks

    # Assemble the FULL TaskPlan in skeleton order. TaskPlanFeat.tasks min_length=1 + the per-feat
    # passes' own ≥1 guarantee make this non-empty; a defensive ValidationError → fail-closed HALT
    # (nothing written).
    try:
        full_plan = TaskPlan(
            epics=[
                TaskPlanEpic(
                    title=epic.title,
                    feats=[
                        TaskPlanFeat(
                            title=feat.title,
                            description=feat.description,
                            estimated_minutes=feat.estimated_minutes,
                            tasks=feat_tasks[(ei, fi)],
                        )
                        for fi, feat in enumerate(epic.feats)
                    ],
                )
                for ei, epic in enumerate(skeleton.epics)
            ]
        )
    except ValidationError as exc:
        msg = _record_message(
            db,
            version_id=version_id,
            stage="navrh",
            author="system",
            recipient="manazer",
            kind="notification",
            content=f"Zostavený plán úloh je neúplný: {exc}.",
            payload={"phase": "navrh"},
        )
        if on_message is not None:
            await on_message(msg)
        state.status = "blocked"
        state.block_reason = "system_error"
        state.next_action = "Plán úloh zamietnutý — zostavený plán je neúplný. Usmerni Návrh (Uprav)."
        db.flush()
        return state

    assembled = PipelineStatusBlock(
        stage="navrh",
        kind="gate_report",
        summary="Návrh hotový — návrhový dokument + plán úloh (kostra + úlohy po funkciách).",
        awaiting="manazer",
        plan=full_plan,
        cross_cutting_rules=skeleton.cross_cutting_rules,
    )
    # Record the AI-Agent navrh gate_report carrying the assembled plan + cross_cutting_rules: the build
    # loop re-reads the rules from THIS message (_fetch_cross_cutting_rules), and it is the audit-trail
    # record of the plan the Manažér reviews at the post-Návrh schvaľovací bod. No usage of its own
    # (orchestrator-synthesized — the per-pass notes already accounted the agent tokens); mode="json" so
    # any UUID in the plan serializes for JSONB.
    plan_msg = _record_message(
        db,
        version_id=version_id,
        stage="navrh",
        author="ai_agent",
        recipient="manazer",
        kind="gate_report",
        content=assembled.summary,
        payload={
            "plan": full_plan.model_dump(mode="json"),
            "cross_cutting_rules": skeleton.cross_cutting_rules,
            "phase": "navrh",
        },
    )
    if on_message is not None:
        await on_message(plan_msg)

    reason = _write_task_plan(db, state, assembled)
    if reason is not None:
        # Plan write failed → blocked: a direct system→manazer note (no Coordinator relay, design §2.2).
        msg = _record_message(
            db,
            version_id=version_id,
            stage="navrh",
            author="system",
            recipient="manazer",
            kind="notification",
            content=f"Plán úloh sa nepodarilo zapísať: {reason}.",
            payload={"phase": "navrh"},
        )
        if on_message is not None:
            await on_message(msg)
        state.status = "blocked"
        state.block_reason = "system_error"  # R4 (D1): task-plan write failed (engine-side)
        state.next_action = "Plán úloh sa nepodarilo zapísať — usmerni Návrh (Uprav)."
        db.flush()
        return state
    return None  # success — the caller runs the SHARED dial-settle


async def _run_navrh_round(
    db: Session,
    state: PipelineState,
    *,
    on_event: Optional[claude_agent.EventCallback] = None,
    directive: Optional[str] = None,
    on_message: Optional[MessageCallback] = None,
) -> PipelineState:
    """The Návrh round (CR-V2-011; NAVRH-1..NAVRH-4, ARCH-2): ONE coherent design doc + the folded task plan.

    Replaces the v1 standalone design + ``_run_task_plan_round`` passes with a single Návrh phase:

    1. **Design-doc turn** — the AI Agent (warm session, resumed from Príprava) writes ONE coherent design
       ``.md`` (overview/data-model/API/BE+FE, sized to the project) per :func:`_navrh_directive`. A
       ``question``/``blocked`` turn settles ``blocked`` (the Manažér answers — the post-Návrh schvaľovací
       bod surfaces clarification questions; the Auditor's upfront review hooks here in CR-V2-013); a
       ``ParseFailure`` settles the R1 lost-work / parse-exhaustion path; a ``directive`` (uprav/ask/answer)
       IS the agent's prompt (two-way comms).
    2. **Persist + verify** the design-doc artifact (mirror of the Príprava spec gate). A checkout that
       exists but is missing the doc → ``blocked`` (the phase is not "done" without its artifact).
    3. **Fold the task plan in** (:func:`_fold_task_plan_into_navrh`) UNLESS the design turn already carried
       a non-empty inline plan (a small project — then it is materialized directly, no extra passes).
    4. **Settle via the SHARED dial** (:func:`_settle_phase_boundary`): the Návrh schvaľovací bod is
       dial-governed — auto-continue to Programovanie (``plna``) or stop ``awaiting_manazer`` (the Manažér
       reviews the design + plan + the AI Agent's clarification questions).

    The sole-mutator invariant holds: this runs inside the dispatch path, always a consequence of an action
    already routed through :func:`apply_action`.
    """
    actor = state.current_actor  # ai_agent
    # 1. The design-doc turn — directive (uprav/ask/answer) when the Manažér steered, else the Návrh brief.
    prompt = directive if directive is not None else _navrh_directive(db, state.version_id)
    result = await invoke_agent_with_parse_retry(
        db,
        version_id=state.version_id,
        role=actor,
        stage="navrh",
        prompt=prompt,
        on_event=on_event,
        on_message=on_message,
    )
    if isinstance(result, ParseFailure):
        if result.lost_work is not None:  # R1-c lost-work audit (safeguard #3) — never silently dropped
            state.status = "awaiting_manazer"
            state.next_action = result.lost_work["next_action"]
            db.flush()
            return state
        state.status = "blocked"
        state.block_reason = "parse_exhaustion"  # R4 (D1): no parseable design output after retries
        state.next_action = "Blokované — agent nevrátil platný návrh. Usmerni (Uprav) alebo odpovedz."
        db.flush()
        return state
    if result.kind in ("question", "blocked"):
        # A design ambiguity the AI Agent surfaces BEFORE finishing — direct comms (no Coordinator relay).
        state.status = "blocked"
        state.block_reason = "agent_question"
        state.next_action = f"Agent '{actor}' sa pýta: {result.question}"
        db.flush()
        return state

    # 2. Persist + verify the design-doc artifact (the Vývoj → Návrh tab reads this record).
    design_err = _persist_navrh_design_doc(db, state, result)
    if design_err is not None:
        state.status = "blocked"
        state.block_reason = "agent_error"  # R4 (D1): the phase deliverable (design doc) is missing on disk
        state.next_action = "Návrhový dokument nebol zapísaný — usmerni agenta (Uprav) a zopakuj Návrh."
        db.flush()
        return state

    # 3. Fold the task plan in. If the design turn already carried a non-empty inline plan (a small
    # project), materialize it directly; otherwise generate it via the incremental skeleton/per-feat passes
    # (no parse exhaustion on a large plan). Either path writes the navrh gate_report + Epic/Feat/Task rows.
    if result.plan is not None and result.plan.epics:
        settled = await _materialize_inline_navrh_plan(db, state, result, on_message=on_message)
    else:
        settled = await _fold_task_plan_into_navrh(db, state, on_event=on_event, directive=None, on_message=on_message)
    if settled is not None:
        return settled  # a fold/materialize failure already settled (blocked / awaiting_manazer)

    # 4. SHARED dial-settle (Milestone-C): auto-continue to Programovanie vs stop at the post-Návrh
    # schvaľovací bod (where the Manažér reviews the design + plan + the AI Agent's clarification questions).
    if _settle_phase_boundary(db, state):
        return state  # agent_working at Programovanie — the auto-chain loop continues the build
    if state.status != "done":
        state.status = "awaiting_manazer"
        state.next_action = "Manažér: posúdiť návrh + plán úloh (Schváliť / Uprav)."
        db.flush()
    return state


async def _materialize_inline_navrh_plan(
    db: Session,
    state: PipelineState,
    block: PipelineStatusBlock,
    *,
    on_message: Optional[MessageCallback],
) -> Optional[PipelineState]:
    """Materialize a SMALL project's inline Návrh plan (the design turn already carried a non-empty
    ``plan``) — CR-V2-011. Records the AI-Agent navrh gate_report (carries plan + cross_cutting_rules the
    build loop re-reads) + the Epic/Feat/Task rows via the re-homed :func:`_write_task_plan`. Returns the
    SETTLED state on a write failure (caller returns it), or ``None`` on success (caller runs the dial)."""
    # The gate_report message the design turn produced (recorded by invoke_agent) may not carry the plan in
    # its payload, so record the canonical navrh gate_report the build loop reads (_fetch_cross_cutting_rules
    # + the audit trail). mode="json" so any UUID in the plan serializes for JSONB.
    plan_msg = _record_message(
        db,
        version_id=state.version_id,
        stage="navrh",
        author="ai_agent",
        recipient="manazer",
        kind="gate_report",
        content="Návrh hotový — návrhový dokument + plán úloh (malý projekt, plán v jednom ťahu).",
        payload={
            "plan": block.plan.model_dump(mode="json"),
            "cross_cutting_rules": block.cross_cutting_rules,
            "phase": "navrh",
        },
    )
    if on_message is not None:
        await on_message(plan_msg)
    reason = _write_task_plan(db, state, block)
    if reason is not None:
        msg = _record_message(
            db,
            version_id=state.version_id,
            stage="navrh",
            author="system",
            recipient="manazer",
            kind="notification",
            content=f"Plán úloh sa nepodarilo zapísať: {reason}.",
            payload={"phase": "navrh"},
        )
        if on_message is not None:
            await on_message(msg)
        state.status = "blocked"
        state.block_reason = "system_error"
        state.next_action = "Plán úloh sa nepodarilo zapísať — usmerni Návrh (Uprav)."
        db.flush()
        return state
    return None


async def _verify_with_retries(
    db: Session,
    state: PipelineState,
    block: PipelineStatusBlock,
    on_message: Optional[MessageCallback] = None,
) -> tuple[Optional[str], bool]:
    """Verify; on failure auto-return to the agent up to ``_VERIFY_RETRIES`` times.

    Returns ``(reason, is_scope)`` (CR-NS-056 §F1.3): ``reason`` on FAIL else None; ``is_scope`` True when the
    judge's blocked verdict is a SCOPE/DESIGN class (``_verify_reason_is_scope``) — the caller escalates ONCE
    instead of looping. A scope flag (before OR after a re-verify) STOPS the loop immediately. The mechanical
    path is behaviorally unchanged (the auto-return loop fires up to ``_VERIFY_RETRIES``).

    v0.7.2 R-B: a Coordinator SYSTEM error (its OWN verify stayed unparseable after R-A's parse-retries —
    ``is_coordinator_error`` from :func:`verify_done`) NEVER enters the auto-return loop. The Designer's work
    is fine; re-dispatching it can't fix the Coordinator's parse problem (this caused the nex-asistent gate_b
    loop). We return ``(reason, False)`` immediately so the caller blocks with ``system_error`` instead of
    looping the Designer. A genuine Designer-report error (``is_coordinator_error`` False) keeps its
    auto-return — behaviour there is unchanged.

    Every recorded turn here is a dispatch-path message → ``on_message`` streams each
    live (the Coordinator judgment via :func:`verify_done`, the system auto-return, and
    the worker's corrected report) so none is lost once the end batch is dropped."""
    reason, directive, is_coordinator_error = await verify_done(db, state.version_id, block, on_message)
    if reason is not None and is_coordinator_error:
        return reason, False  # R-B: Coordinator-system-error → escalate (caller blocks), never loop the Designer
    if reason is not None and _verify_reason_is_scope(directive):
        return reason, True  # scope/design → break the loop (caller escalates once per iteration)
    attempts = 0
    while reason is not None and attempts < _VERIFY_RETRIES:
        attempts += 1
        msg = _record_message(
            db,
            version_id=state.version_id,
            stage=state.current_stage,
            author="system",
            recipient=state.current_actor,
            kind="return",
            content=f"Auto-return (verify {attempts}/{_VERIFY_RETRIES}): {reason}",
            payload={"verify_reason": reason},
        )
        if on_message is not None:
            await on_message(msg)
        retry = await invoke_agent_with_parse_retry(
            db,
            version_id=state.version_id,
            role=state.current_actor,
            stage=state.current_stage,
            prompt=(
                f"Verify zlyhal: {reason}. Oprav a znovu ukonči <<<PIPELINE_STATUS>>> "
                "blokom (F-007-orchestration-cockpit.md §5.3)."
            ),
            on_message=on_message,
        )
        if isinstance(retry, ParseFailure):
            # WS-E (CR-NS-037): the verify-retry re-emit exhausted parse-retries → its tokens would
            # leak. Record them + a visible note, then give up exactly as before (the caller blocks on
            # the non-None reason — control flow unchanged).
            await _record_internal_turn_parse_failure(
                db,
                state.version_id,
                state.current_stage,
                turn_label=f"Oprava po overení (agent „{state.current_actor}“)",
                failed=retry,
                on_message=on_message,
            )
            return reason, False
        if retry.kind != "gate_report":
            return reason, False  # give up on non-report → caller escalates
        block = retry
        reason, directive, is_coordinator_error = await verify_done(db, state.version_id, block, on_message)
        if reason is not None and is_coordinator_error:
            return reason, False  # R-B: Coordinator-system-error on re-verify → escalate, don't keep looping
        if reason is not None and _verify_reason_is_scope(directive):
            return reason, True  # scope flagged on re-verify → break the loop
    return reason, False


# ---------------------------------------------------------------------------
# Build per-task loop (F-007 §6, CR-NS-020 CR-3)
# ---------------------------------------------------------------------------


def _build_open_findings(db: Session, version_id: uuid.UUID) -> int:
    """Count of ``failed`` / ``in_progress`` (unverified) tasks for the version — the
    deterministic build gate (§6). The build loop sets ``Task.status`` (``done`` on a
    mechanical pass, ``failed`` after the auto-fix bound) — the Programmer never sets it —
    so ``Task.status`` IS the orchestrator's structural record, not agent self-report.

    A non-zero count blocks ``build → gate_g``, even on ``end_build``. ``todo`` tasks are NOT
    counted: ``end_build`` ("zvyšok do auditu") may legitimately advance with unstarted tasks
    remaining — only a failed (or stuck in_progress / unverified) task blocks the close."""
    return int(
        db.execute(
            select(func.count())
            .select_from(Task)
            .join(Feat, Feat.id == Task.feat_id)
            .join(Epic, Epic.id == Feat.epic_id)
            .where(Epic.version_id == version_id, Task.status.in_(("failed", "in_progress")))
        ).scalar_one()
    )


def _reset_failed_tasks_to_todo(db: Session, version_id: uuid.UUID) -> None:
    """Reset the version's ``failed`` tasks back to ``todo`` (F-007 §6/§7) so the build loop
    re-attempts them on a Director ``return`` — a fresh auto-fix budget; ``done`` stays done."""
    feat_ids = select(Feat.id).join(Epic, Epic.id == Feat.epic_id).where(Epic.version_id == version_id)
    db.execute(update(Task).where(Task.feat_id.in_(feat_ids), Task.status == "failed").values(status="todo"))
    db.flush()


def _reset_done_tasks_for_regate(db: Session, version_id: uuid.UUID) -> None:
    """gate_g FAIL Fix 2 (CR-NS-057 §F2.2): on a FAIL→build re-gate, flip the version's ``done`` tasks back to
    ``todo`` (existing ``todo`` untouched) so the WHOLE build re-runs against the corrected understanding.
    Re-run tasks keep their ``baseline_sha`` (a fresh anchor is a separate Director ``move_baseline``)."""
    feat_ids = select(Feat.id).join(Epic, Epic.id == Feat.epic_id).where(Epic.version_id == version_id)
    db.execute(update(Task).where(Task.feat_id.in_(feat_ids), Task.status == "done").values(status="todo"))
    db.flush()


def _resolve_surgical_targets(
    db: Session, version_id: uuid.UUID, identifiers: list[str]
) -> tuple[list[Task], list[str]]:
    """Resolve hierarchical ``<epic>.<feat>.<task>`` task ids (e.g. ``"1.3.1"`` — the exact format the Director
    reads from ``spec/task-plan.md``, :func:`_render_task_plan_md`) to their version-scoped ``Task`` rows.

    Returns ``(resolved_tasks, unresolved_identifiers)`` — an id is *unresolved* when malformed (not exactly
    three dot-separated positive integers) OR no matching Task exists under this version. The hierarchical id
    disambiguates ``Task.number`` (which is unique only WITHIN a feat — ``UNIQUE(feat_id, number)``), so a flat
    number can't be used to pinpoint one task across the version."""
    resolved: list[Task] = []
    unresolved: list[str] = []
    for ident in identifiers:
        parts = ident.strip().split(".")
        try:
            if len(parts) != 3:
                raise ValueError
            epic_num, feat_num, task_num = (int(p) for p in parts)
        except ValueError:
            unresolved.append(ident)
            continue
        task = db.execute(
            select(Task)
            .join(Feat, Feat.id == Task.feat_id)
            .join(Epic, Epic.id == Feat.epic_id)
            .where(
                Epic.version_id == version_id,
                Epic.number == epic_num,
                Feat.number == feat_num,
                Task.number == task_num,
            )
        ).scalar_one_or_none()
        if task is None:
            unresolved.append(ident)
        else:
            resolved.append(task)
    return resolved, unresolved


def _reset_tasks_for_surgical_fix(db: Session, version_id: uuid.UUID, target_task_numbers: list[str]) -> int:
    """gate-g-hardening GAP 2 (CR-D): the SELECTIVE reset behind a ``surgical_fix`` — flip ONLY the Director-
    scoped ``done`` tasks back to ``todo`` so :func:`get_next_todo_task` re-runs ONLY those (not the whole
    build — that is what a FAIL→build re-gate is for).

    Scope = ``target_task_numbers``, a REQUIRED list of hierarchical ``<epic>.<feat>.<task>`` ids (the handler
    rejects an empty scope upstream). Any id that does not resolve to an existing task → ``OrchestratorError``
    (clear feedback, never a silent partial scope). Mirrors :func:`_coordinator_reset_task`'s per-row pattern
    (NOT the bulk :func:`_reset_done_tasks_for_regate`, which skips the per-feat status recompute → board
    drift); each touched feat is recomputed ONCE. Returns the count actually reset (resolved tasks already in
    ``todo``/another state are left as-is) so the handler can reject a scope that matched no *resettable* task."""
    resolved, unresolved = _resolve_surgical_targets(db, version_id, target_task_numbers)
    if unresolved:
        raise OrchestratorError(
            "surgical_fix: neznáme čísla úloh (formát '<epic>.<feat>.<task>', napr. '1.3.1' — z spec/task-plan.md): "
            + ", ".join(unresolved)
        )
    touched_feats: set[uuid.UUID] = set()
    reset = 0
    for task in resolved:
        if task.status == "done":
            task.status = "todo"  # ORM assignment keeps the in-memory object in sync
            touched_feats.add(task.feat_id)
            reset += 1
    db.flush()
    for feat_id in touched_feats:
        task_service.recompute_feat_status(db, feat_id)
    return reset


def current_build_task(db: Session, version_id: uuid.UUID) -> Optional[Task]:
    """The build task currently in focus (WS-C2, CR-NS-035) for the "kto je na rade" board: the
    ``in_progress`` task while the Programmer works, else the ``failed`` (held) task at a HALT, else
    ``None``. Lowest number wins if several share a status."""
    feat_ids = select(Feat.id).join(Epic, Epic.id == Feat.epic_id).where(Epic.version_id == version_id)
    for status_ in ("in_progress", "failed"):
        task = db.execute(
            select(Task).where(Task.feat_id.in_(feat_ids), Task.status == status_).order_by(Task.number).limit(1)
        ).scalar_one_or_none()
        if task is not None:
            return task
    return None


def _failed_build_task(db: Session, version_id: uuid.UUID) -> Optional[Task]:
    """The version's failed build task (WS-B2, CR-NS-031) — the one the build loop HALTed on. The loop
    processes tasks in order and stops on the first failure, so there is at most one; the lowest number
    is the relevant one if several exist."""
    feat_ids = select(Feat.id).join(Epic, Epic.id == Feat.epic_id).where(Epic.version_id == version_id)
    return db.execute(
        select(Task).where(Task.feat_id.in_(feat_ids), Task.status == "failed").order_by(Task.number).limit(1)
    ).scalar_one_or_none()


def _latest_reported_commit(db: Session, version_id: uuid.UUID, task_id: uuid.UUID) -> Optional[str]:
    """The first commit the Programmer last reported for ``task_id`` (WS-B2, CR-NS-031), read from the
    build dispatch messages' ``payload.commits``. Newest-first; ``None`` if no commit was reported."""
    rows = (
        db.execute(
            select(PipelineMessage)
            .where(PipelineMessage.version_id == version_id, PipelineMessage.stage == "build")
            .order_by(PipelineMessage.seq.desc())
        )
        .scalars()
        .all()
    )
    for m in rows:
        payload = m.payload or {}
        if str(payload.get("task_id")) == str(task_id):
            commits = payload.get("commits") or []
            if commits:
                return commits[0]
    return None


# ---------------------------------------------------------------------------
# E7 — Coordinator as operator: structured directive + executable actions (F-008 §2/§4/§9, CR-NS-032)
# ---------------------------------------------------------------------------

_COORDINATOR_CONFIDENCE_FLOOR = 0.80
_EXECUTABLE_COORDINATOR_ACTIONS = frozenset(
    {
        "coordinator_reset_task",
        "coordinator_move_baseline",
        "coordinator_clear_session",
        "coordinator_escalate_dedo",
        "coordinator_route_to_designer",
        "capture_backlog_item",
        # Fast-Fix Lane (F-009 §3 D5, CR-NS-103): the Coordinator's autonomous answer to a routine build
        # Programmer question. The AUTONOMOUS path runs it via _maybe_autonomous_answer (stricter 0.85 floor);
        # listing it here also lets a Director-approved answer execute via apply_coordinator_recommendation.
        "coordinator_answer_question",
    }
)

# Pillar B (CR-NS-055): the bounded-recovery SUBSET the Coordinator may AUTO-EXECUTE without a Director click
# (the AUTO_SET — reversible, scoped). NOT route_to_designer (a DESIGN-QUALITY signal → escalate) / escalate_dedo
# / capture_backlog_item. Gated further by _coordinator_directive_executable (conf ≥ floor + not
# director_decision) and the per-task cap below.
_AUTONOMOUS_RECOVERY_ACTIONS = frozenset(
    {
        "coordinator_reset_task",
        "coordinator_move_baseline",
        "coordinator_clear_session",
    }
)

# Pillar B §B.4 cap: the Coordinator auto-intervenes at most ONCE per task. A 2nd HALT on the SAME task after
# an autonomous recovery → ESCALATE (a repeat failure after a clean first-principles fix is a design-quality
# signal, not an auto-loop).
_MAX_AUTONOMOUS_PER_TASK = 1

# Fast-Fix Lane autonomous ANSWER bounds (F-009 §3 D5, CR-NS-103). Distinct from the recovery floor/cap above:
# answering a routine Programmer question is LESS reversible than a task reset, so the confidence floor is
# HIGHER (0.85 > the 0.80 recovery floor) and the per-task cap is 2 — the 3rd routine question on one task
# signals the fix is not trivial after all → escalate → propose converting to a full version.
_FAST_FIX_ANSWER_CONFIDENCE_FLOOR = 0.85
_MAX_AUTONOMOUS_ANSWERS_PER_TASK = 2

# PIPELINE-AUTONOMY Phase 1 (design docs/architecture/pipeline-autonomy.md §1/§5.1): the routine full-flow
# gates the engine auto-ratifies on a deterministically-clean PASS (verify clean ∧ not scope) — a–d only.
# Each has its own deterministic FAIL→blocked pre-empt BEFORE the PASS site, so auto-ratify never sees a
# problem. NOT a confidence gate (there is no confidence on a PASS site — §0.1); purely deterministic.
_AUTO_RATIFY_GATES = frozenset({"gate_a", "gate_b", "gate_c", "gate_d"})
# Stages a routine-gate auto-ratify must NEVER advance (design §1.1 / Issue 10), even if a future edit
# widens _AUTO_RATIFY_GATES: ``release`` is the engine-owned _release_auto_publish path; ``gate_g`` is the
# KEY release verdict (auto DEFERRED to v2). Explicit exclusion = belt-and-suspenders.
_NEVER_AUTO_RATIFY_STAGES = frozenset({"release", "gate_g"})

# Pillar B §B.2: the first-principles triage framework appended to the Coordinator's build HALT / question
# prompt. Honest confidence is load-bearing — it gates auto-execution (bounded-recovery + conf ≥ floor + not
# director_decision → applied without a Director click; ambiguity / design-scope / destructive → escalate).
_FIRST_PRINCIPLES_TRIAGE = (
    "Rozhodni podľa PRVOTNÝCH PRINCÍPOV (profesionálne, kvalitné, spoľahlivé — NIKDY rýchle/dočasné). Ak je "
    "oprava jednoznačná z dizajnu+kódu a je to RUTINNÉ ZOTAVENIE (reset úlohy / posun baseline / vyčistenie "
    "session), navrhni ju s úprimnou VYSOKOU istotou — vykoná sa AUTOMATICKY bez Directora. Ak je to "
    "nejednoznačné, zmena dizajnu/rozsahu (route_to_designer) alebo deštruktívne → director_decision / nízka "
    "istota → eskaluje sa Directorovi. Genuine blocker = signál slabého dizajnu, eskaluj. "
)

# Fast-Fix Lane relay brief (F-009 §3 D5, CR-NS-103): appended to the Coordinator's relay prompt ONLY on a
# fast_fix flow. At build, a ROUTINE Programmer question → emit `coordinator_answer_question`
# (triage_class=programmer_routine_question) with honest HIGH confidence (≥0.85) and the answer in `rationale`
# — the engine applies it automatically (no Director). At release NEVER ask about the deploy (it is
# engine-owned) — emit a `gate_report` PASS, or a `director_decision` only for a genuine scope.
_FAST_FIX_RELAY_BRIEF = (
    " RÝCHLA OPRAVA (F-009): ak je to RUTINNÁ otázka Programátora vo fáze build (napr. „slovo už je X — "
    "pokračovať?“, „použiť helper A alebo B?“), navrhni `coordinator_answer_question` "
    "(triage_class=programmer_routine_question) s úprimnou VYSOKOU istotou (≥0.85) a samotnou odpoveďou v "
    "`rationale` — engine ju vykoná automaticky, bez Directora. Vo fáze release sa NIKDY nepýtaj na "
    "nasadenie (auto-deploy je engine-owned) — emit `gate_report` PASS, alebo `director_decision` len pri "
    "genuine rozsahu (konverzia na plnú verziu)."
)


def _latest_coordinator_directive(db: Session, version_id: uuid.UUID) -> Optional[dict[str, Any]]:
    """The most recent Coordinator gate_report's structured ``coordinator_directive`` (F-008 §2), or
    ``None`` — the proposal the Director approves via ``apply_coordinator_recommendation``."""
    row = db.execute(
        select(PipelineMessage)
        .where(
            PipelineMessage.version_id == version_id,
            PipelineMessage.author == "coordinator",
            PipelineMessage.kind == "gate_report",
        )
        .order_by(PipelineMessage.seq.desc())
        .limit(1)
    ).scalar_one_or_none()
    return (row.payload or {}).get("coordinator_directive") if row is not None else None


def _latest_gate_g_classifying_directive(db: Session, version_id: uuid.UUID) -> Optional[dict[str, Any]]:
    """gate_g FAIL Fix 2 (CR-NS-057 §F2.1): the newest coordinator directive at stage ``gate_g`` — the
    classifying directive for the re-gate target. KIND-AGNOSTIC: the gate_g FAIL directive rides a
    ``kind="question"`` message (blocked→question), NOT a ``gate_report``, so ``_latest_coordinator_directive``
    cannot see it. The non-null filter is in SQL BEFORE the LIMIT — ``invoke_agent`` ALWAYS writes the
    ``coordinator_directive`` key (JSON-null for a directive-less synthesis turn), so a naive ORDER-BY-LIMIT-1
    + Python check would grab a later synthesis row (value JSON-null) and SHADOW an older real directive.
    ``payload['coordinator_directive'].astext.isnot(None)`` compiles to ``->> IS NOT NULL`` — TRUE for an
    object value, excluded for JSON-null. (NOT ``.isnot(None)`` on the JSON expression — that tests SQL NULL /
    key-absent, not JSON-null value.)"""
    row = db.execute(
        select(PipelineMessage)
        .where(
            PipelineMessage.version_id == version_id,
            PipelineMessage.author == "coordinator",
            PipelineMessage.stage == "gate_g",
            PipelineMessage.payload["coordinator_directive"].astext.isnot(None),
        )
        .order_by(PipelineMessage.seq.desc())
        .limit(1)
    ).scalar_one_or_none()
    return (row.payload or {}).get("coordinator_directive") if row is not None else None


def _infer_regate_entry_stage(db: Session, version_id: uuid.UUID) -> str:
    """gate_g FAIL Fix 2 (CR-NS-057 §F2.1): infer the re-gate target from the latest gate_g classifying
    directive — design/scope class (spec_problem / director_decision / route_to_designer) → ``gate_a`` (full
    design re-gate, the waterfall response); else (code-fixable, OR no gate_g directive = a Director-initiated
    FAIL on a PASS-verified audit) → ``build`` (re-run the build). The Director always overrides via chips."""
    d = _latest_gate_g_classifying_directive(db, version_id)
    if d and (
        d.get("triage_class") in ("spec_problem", "director_decision")
        or d.get("proposed_action") == "coordinator_route_to_designer"
    ):
        return "gate_a"
    return "build"


def _latest_gate_g_findings(db: Session, version_id: uuid.UUID) -> Optional[str]:
    """gate_g FAIL Fix 2 (CR-NS-057 §F2.2): the latest gate_g Auditor audit findings (+ the classifying
    directive's rationale), formatted as a Slovak block to thread into a FAIL→build re-run brief — but ONLY
    when no ``task_plan`` has run SINCE that audit (the sticky-``is_regate`` guard: a build reached via a
    design-class FAIL→gate_a re-runs task_plan, so its pre-redesign findings are stale). Returns None when the
    findings are superseded (task_plan newer) or absent."""
    audit = db.execute(
        select(PipelineMessage)
        .where(
            PipelineMessage.version_id == version_id,
            PipelineMessage.author == "auditor",
            PipelineMessage.stage == "gate_g",
            PipelineMessage.kind == "gate_report",
        )
        .order_by(PipelineMessage.seq.desc())
        .limit(1)
    ).scalar_one_or_none()
    if audit is None:
        return None
    task_plan_seq = db.execute(
        select(func.max(PipelineMessage.seq)).where(
            PipelineMessage.version_id == version_id,
            PipelineMessage.stage == "task_plan",
        )
    ).scalar_one_or_none()
    if task_plan_seq is not None and audit.seq <= task_plan_seq:
        return None  # a task_plan ran after the audit → findings superseded (a gate_a-transitive build re-gate)
    findings = (audit.payload or {}).get("findings") or []
    directive = _latest_gate_g_classifying_directive(db, version_id)
    rationale = (directive or {}).get("rationale") if directive else None
    parts: list[str] = []
    if findings:
        parts.append("\n".join(f"- {f}" for f in findings))
    if rationale:
        parts.append(str(rationale))
    if not parts:
        return None
    return "## Audit zistenia z gate_g (oprav v tomto buildu)\n" + "\n\n".join(parts)


def _latest_surgical_fix_directive(db: Session, version_id: uuid.UUID) -> Optional[str]:
    """gate-g-hardening GAP 2 (CR-D, korekcia #1): the Director's latest ``surgical_fix`` fix directive,
    formatted as a Slovak block to PREPEND (ahead of :func:`_latest_gate_g_findings`) into the surgical re-run
    brief — so the build loop carries the Director's EXPLICIT instruction, not just the Auditor's findings.

    Reads the latest ``director→implementer`` ``directive`` message of THIS iteration — seq strictly past
    :func:`_iteration_boundary_seq` (the latest ``verdict`` seq). ``surgical_fix`` records a ``directive`` (NOT
    a verdict), so it never moves that boundary; the next PASS/FAIL verdict does, which is exactly when the
    directive becomes stale → this returns ``None`` (mirrors the boundary-anchored freshness of
    :func:`_release_acceptance_satisfied`). The ``surgical_fix`` payload marker disambiguates it from any other
    Director→Implementer directive. ``None`` when there is no fresh surgical directive."""
    boundary = _iteration_boundary_seq(db, version_id)
    msg = db.execute(
        select(PipelineMessage)
        .where(
            PipelineMessage.version_id == version_id,
            PipelineMessage.author == "director",
            PipelineMessage.recipient == "implementer",
            PipelineMessage.kind == "directive",
            PipelineMessage.seq > boundary,
        )
        .order_by(PipelineMessage.seq.desc())
        .limit(1)
    ).scalar_one_or_none()
    if msg is None:
        return None
    if not (msg.payload or {}).get("surgical_fix"):
        return None
    directive = (msg.content or "").strip()
    if not directive:
        return None
    return "## Cielená oprava od Directora (vykonaj v tomto buildu)\n" + directive


def _verify_reason_is_scope(directive: Optional[dict[str, Any]]) -> bool:
    """gate_g Fix 1 (CR-NS-056 §F1.2): a verify-judge blocked verdict is SCOPE/DESIGN (Auditor-unfixable →
    escalate) iff its directive is a scope class — ``triage_class=="director_decision"`` OR
    ``proposed_action=="coordinator_route_to_designer"``. Everything else (missing directive, a mechanical
    action, spec_problem/programmer_guidance/nex_studio_bug, a P-2 defect) is MECHANICAL (Auditor CAN fix →
    the existing auto-return loop). Fail-open: no directive ⇒ mechanical (False)."""
    if not directive:
        return False
    return (
        directive.get("triage_class") == "director_decision"
        or directive.get("proposed_action") == "coordinator_route_to_designer"
    )


def _scope_escalations_this_iteration(db: Session, version_id: uuid.UUID) -> int:
    """Count gate_g coordinator scope-questions in the CURRENT iteration (CR-NS-056 §F1.5) — the per-iteration
    cap. A coordinator ``kind=="question"`` message at stage ``gate_g``, seq past the iteration boundary
    (latest verdict seq), whose directive is a scope class. INCLUDES this turn's just-recorded question (it was
    recorded by ``invoke_agent`` inside ``verify_done`` BEFORE the caller runs), so §F1.4's guard is ``<=``.
    Null-safe: the ``coordinator_directive`` key is always present (JSON-null for a directive-less turn) —
    ``(payload or {}).get('coordinator_directive') or {}`` (never ``.get(k, {}).get(...)``)."""
    boundary = _iteration_boundary_seq(db, version_id)
    rows = (
        db.execute(
            select(PipelineMessage.payload).where(
                PipelineMessage.version_id == version_id,
                PipelineMessage.author == "coordinator",
                PipelineMessage.kind == "question",
                PipelineMessage.stage == "gate_g",
                PipelineMessage.seq > boundary,
            )
        )
        .scalars()
        .all()
    )
    count = 0
    for payload in rows:
        directive = (payload or {}).get("coordinator_directive") or {}
        if (
            directive.get("triage_class") == "director_decision"
            or directive.get("proposed_action") == "coordinator_route_to_designer"
        ):
            count += 1
    return count


def _coordinator_directive_executable(directive: Optional[dict[str, Any]]) -> bool:
    """True iff an approved directive should EXECUTE (F-008 §9): an executable proposed_action, a
    non-``director_decision`` triage, and confidence ≥ the conservative floor. Else it's a pure relay."""
    if not directive:
        return False
    action = directive.get("proposed_action")
    if action not in _EXECUTABLE_COORDINATOR_ACTIONS:
        return False
    # E2 (CR-NS-042): capture_backlog_item is a Director-INSTRUCTED write, not a triage judgment under
    # uncertainty — the triage_class/confidence floor (which bounds the auto-triage actions) is meaningless
    # for it, so it executes deterministically once the Director approves the drafted item.
    if action == "capture_backlog_item":
        return True
    if directive.get("triage_class") == "director_decision":
        return False
    if float(directive.get("confidence") or 0.0) < _COORDINATOR_CONFIDENCE_FLOOR:
        return False
    return True


# CR-V2-006: ``directive`` was ``Optional[CoordinatorDirective]`` (the dropped v1 model); annotated
# ``Optional[Any]`` here — this is dead Coordinator-relay code removed wholesale by CR-V2-009.
def _is_director_decision_directive(directive: Optional[Any]) -> bool:
    """True iff a parsed ``coordinator_directive`` (carried on a worker question/blocked turn) is a genuine
    ``director_decision`` scope. Fast-Fix Lane release carve-out (CR-NS-103): the ONLY case in which a
    Coordinator release question still escalates (real scope → convert-to-full-version); ``None`` / any other
    triage means a routine question → the carve-out applies (fall through to the engine-owned auto-deploy)."""
    return directive is not None and directive.triage_class == "director_decision"


def _directive_target_task(db: Session, version_id: uuid.UUID, directive: dict[str, Any]) -> Optional[Task]:
    """The task a directive operates on: ``target.task_id`` (if it belongs to the version), else the
    failed build task; ``None`` if neither resolves."""
    target = directive.get("target") or {}
    task_id = target.get("task_id")
    if task_id:
        feat_ids = select(Feat.id).join(Epic, Epic.id == Feat.epic_id).where(Epic.version_id == version_id)
        try:
            task = db.execute(
                select(Task).where(Task.id == uuid.UUID(str(task_id)), Task.feat_id.in_(feat_ids))
            ).scalar_one_or_none()
        except (ValueError, AttributeError):
            task = None
        if task is not None:
            return task
    return _failed_build_task(db, version_id)


def _coordinator_audit(db: Session, version_id: uuid.UUID, content: str, directive: dict[str, Any]) -> None:
    """Record the director→coordinator audit message for an executed directive (F-008 §4)."""
    _record_message(
        db,
        version_id=version_id,
        stage="build",
        author="director",
        recipient="coordinator",
        kind="approval",
        content=content,
        payload={"executed_directive": directive},
    )


def _coordinator_reset_task(db: Session, state: PipelineState, directive: dict[str, Any]) -> None:
    task = _directive_target_task(db, state.version_id, directive)
    if task is None:
        raise OrchestratorError("Koordinátorov reset: žiadna cieľová zlyhaná úloha")
    task.status = "todo"
    db.flush()
    task_service.recompute_feat_status(db, task.feat_id)
    _coordinator_audit(
        db,
        state.version_id,
        f"Vykonaný Koordinátorov návrh: úloha #{task.number} resetovaná na todo (nový pokus).",
        directive,
    )


def _coordinator_answer_question(db: Session, state: PipelineState, directive: dict[str, Any]) -> None:
    """Director-approved variant of the fast_fix auto-answer (CR-NS-103): reset the held build task to todo so
    the build loop re-attempts it (the Coordinator's answer rides in the recorded relay/directive rationale).
    The AUTONOMOUS path (:func:`_maybe_autonomous_answer`) injects the answer as the resumed task's brief
    directly; here the Director approved the proposal, so the task simply re-runs (a routine question is
    non-destructive). Reached only when a Director explicitly applies an ESCALATED answer proposal."""
    task = _directive_target_task(db, state.version_id, directive)
    if task is None:
        raise OrchestratorError("Koordinátorova odpoveď: žiadna cieľová úloha")
    task.status = "todo"
    db.flush()
    task_service.recompute_feat_status(db, task.feat_id)
    _coordinator_audit(
        db,
        state.version_id,
        f"Vykonaný Koordinátorov návrh: odpoveď na otázku úlohy #{task.number} (build pokračuje).",
        directive,
    )


def _coordinator_move_baseline(db: Session, state: PipelineState, directive: dict[str, Any]) -> None:
    task = _directive_target_task(db, state.version_id, directive)
    if task is None:
        raise OrchestratorError("Koordinátorov move_baseline: žiadna cieľová zlyhaná úloha")
    commit = (directive.get("target") or {}).get("commit") or _latest_reported_commit(db, state.version_id, task.id)
    if not commit:
        raise OrchestratorError("Koordinátorov move_baseline: nie je známy commit na posun baseline")
    project_root = claude_agent.PROJECTS_ROOT / _project_slug_for_version(db, state.version_id)
    parent = _repo_parent(project_root, commit)
    if parent is None:
        raise OrchestratorError(f"Koordinátorov move_baseline: nepodarilo sa zistiť rodiča commitu {commit[:8]}")
    task.baseline_sha = parent
    task.status = "todo"
    db.flush()
    task_service.recompute_feat_status(db, task.feat_id)
    _coordinator_audit(
        db,
        state.version_id,
        f"Vykonaný Koordinátorov návrh: baseline úlohy #{task.number} posunutý na {parent[:8]} "
        f"(rodič nahláseného commitu {commit[:8]}) — úloha sa znova overí.",
        directive,
    )


def _coordinator_clear_session(db: Session, state: PipelineState, directive: dict[str, Any]) -> None:
    role = (directive.get("target") or {}).get("role")
    if not role:
        raise OrchestratorError("Koordinátorov clear_session: chýba cieľová rola (target.role)")
    slug = _project_slug_for_version(db, state.version_id)
    db.execute(
        delete(OrchestratorSession).where(
            OrchestratorSession.project_slug == slug, OrchestratorSession.role == str(role)
        )
    )
    db.flush()
    _coordinator_audit(
        db,
        state.version_id,
        f"Vykonaný Koordinátorov návrh: session roly '{role}' vyčistená (čerstvý štart pri ďalšom dispatchi).",
        directive,
    )


def _coordinator_escalate_dedo(db: Session, state: PipelineState, directive: dict[str, Any]) -> None:
    """Write a structured Dedo-escalation item to the project's channel (F-008 §9). Non-blocking — the
    pipeline stays settled; the Director decides the next step (never halt waiting for Dedo)."""
    import json as _json
    import re as _re
    from datetime import datetime, timezone

    slug = _project_slug_for_version(db, state.version_id)
    inbox = claude_agent.PROJECTS_ROOT / slug / ".dedo-channel" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M")
    topic_raw = str((directive.get("params") or {}).get("topic") or directive.get("triage_class") or "build")
    topic = _re.sub(r"[^a-z0-9-]+", "-", topic_raw.lower()).strip("-")[:40] or "build"
    (inbox / f"coordinator-to-dedo-{ts}-{topic}-escalation.md").write_text(
        f"---\nfrom: coordinator\nto: dedo\ntype: escalation\ndate: {ts}\n"
        f"triage_class: {directive.get('triage_class')}\n---\n\n"
        f"{directive.get('rationale', '')}\n\n"
        f"```json\n{_json.dumps(directive, ensure_ascii=False, indent=2)}\n```\n",
        encoding="utf-8",
    )
    _coordinator_audit(
        db,
        state.version_id,
        "Vykonaný Koordinátorov návrh: eskalácia pre Deda zapísaná do kanála (nečaká sa — Director rozhodne ďalej).",
        directive,
    )


def _coordinator_route_to_designer(db: Session, state: PipelineState, directive: dict[str, Any]) -> None:
    """Route a build spec_problem to the Designer (E7, F-008 §10, CR-NS-034). The failed task stays
    `failed` (held); we dispatch the DESIGNER to fix the spec, marking ``returns_to='build'`` so the
    dispatch returns to _run_build_round on the Designer's DONE (which resets the task → todo against the
    corrected spec). Mirrors the gate_e Branch B designer_edit precedent, adapted to build. Sets up the
    Designer dispatch directly (current_actor=designer) — NOT _begin_dispatch (which would pick the
    Implementer)."""
    task = _directive_target_task(db, state.version_id, directive)
    if task is None:
        raise OrchestratorError("Koordinátorov route_to_designer: žiadna cieľová zlyhaná úloha")
    state.current_actor = "designer"
    state.status = "agent_working"
    state.returns_to = "build"
    state.next_action = "Návrhár opravuje spec pre zlyhanú build úlohu."
    db.flush()
    _coordinator_audit(
        db,
        state.version_id,
        f"Vykonaný Koordinátorov návrh: úloha #{task.number} smerovaná na Návrhára na opravu spec — "
        "po jeho DONE sa build úloha znova spustí proti opravenej spec.",
        directive,
    )


def _coordinator_capture_backlog_item(db: Session, state: PipelineState, directive: dict[str, Any]) -> None:
    """Capture a NEW backlog requirement on the Director's instruction (E2, CR-NS-042).

    The Coordinator drafted it (``params {title, description, priority}``); the Director approved via the
    standard E7 approve UI. The orchestrator writes it to the backlog as ``open`` — the **agent NEVER calls
    the API**. Defensive against LLM-drafted params: title is trimmed + capped at 500, an out-of-enum
    priority falls back to ``medium``."""
    params = directive.get("params") or {}
    title = str(params.get("title") or "").strip()[:500]
    if not title:
        raise OrchestratorError("Koordinátorov capture_backlog_item: chýba title v params")
    priority = params.get("priority")
    if priority not in ("low", "medium", "high", "critical"):
        priority = "medium"
    description = str(params["description"]).strip() if params.get("description") else None
    project_id = db.execute(
        select(Project.id).join(Version, Version.project_id == Project.id).where(Version.id == state.version_id)
    ).scalar_one()
    item = backlog_service.create(
        db,
        BacklogItemCreate(project_id=project_id, title=title, description=description, priority=priority),
    )
    _coordinator_audit(
        db,
        state.version_id,
        f"Vykonaný Koordinátorov návrh: zaevidovaná nová požiadavka REQ-{item.number} („{title}“) do backlogu.",
        directive,
    )


def _execute_coordinator_directive(db: Session, state: PipelineState, directive: dict[str, Any]) -> PipelineState:
    """Execute an approved coordinator_directive (F-008 §4/§9): mutate state + an audit message, then
    re-dispatch — EXCEPT escalate_dedo / capture_backlog_item (non-blocking: write + audit + leave settled)
    and route_to_designer (sets up its OWN Designer dispatch + returns_to marker, not the generic build
    re-dispatch)."""
    proposed = directive.get("proposed_action")
    if proposed == "coordinator_reset_task":
        _coordinator_reset_task(db, state, directive)
    elif proposed == "coordinator_answer_question":
        _coordinator_answer_question(db, state, directive)
    elif proposed == "coordinator_move_baseline":
        _coordinator_move_baseline(db, state, directive)
    elif proposed == "coordinator_clear_session":
        _coordinator_clear_session(db, state, directive)
    elif proposed == "coordinator_escalate_dedo":
        _coordinator_escalate_dedo(db, state, directive)
        state.next_action = "Eskalácia pre Deda zapísaná — rozhodni o ďalšom kroku (build ostáva pozastavený)."
        db.flush()
        return state  # non-blocking: stays awaiting_director, no re-dispatch
    elif proposed == "coordinator_route_to_designer":
        _coordinator_route_to_designer(db, state, directive)
        return state  # the executor already set up the Designer dispatch (current_actor=designer)
    elif proposed == "capture_backlog_item":
        _coordinator_capture_backlog_item(db, state, directive)
        state.next_action = "Požiadavka zaevidovaná do backlogu — rozhodni o ďalšom kroku (build môže pokračovať)."
        db.flush()
        return state  # non-blocking: a backlog write doesn't change the build flow
    else:
        raise OrchestratorError(f"Neznáma vykonateľná akcia Koordinátora: {proposed}")
    _begin_dispatch(db, state)  # reset / move_baseline / clear_session → re-run the build loop (re-verify)
    return state


def _autonomous_count(db: Session, version_id: uuid.UUID, task_id: uuid.UUID) -> int:
    """How many autonomous Coordinator RECOVERIES already happened for this task (Pillar B §B.4 cap) —
    counted from the recorded ``is_autonomous`` Coordinator→Director notes tagged with the task. Filters to
    recovery actions ONLY (``action in _AUTONOMOUS_RECOVERY_ACTIONS``), mirroring
    :func:`_autonomous_answer_count`'s ``action`` filter, so the recovery cap and the fast_fix answer cap
    (CR-NS-103) are truly orthogonal in BOTH directions — an autonomous answer never consumes the recovery
    budget, and a recovery never consumes the answer budget."""
    rows = (
        db.execute(
            select(PipelineMessage.payload).where(
                PipelineMessage.version_id == version_id,
                PipelineMessage.author == "coordinator",
            )
        )
        .scalars()
        .all()
    )
    return sum(
        1
        for p in rows
        if p
        and p.get("is_autonomous")
        and p.get("task_id") == str(task_id)
        and p.get("action") in _AUTONOMOUS_RECOVERY_ACTIONS
    )


def _autonomous_answer_count(db: Session, version_id: uuid.UUID, task_id: uuid.UUID) -> int:
    """Fast-Fix autonomous-ANSWER count for a task (CR-NS-103 cap, ≤2). Like :func:`_autonomous_count` but
    counts ONLY recorded autonomous *answers* (``action == 'coordinator_answer_question'``), so the answer cap
    is independent of the recovery cap (§B.4) — a task may both be recovered AND answered without either
    cap leaking into the other."""
    rows = (
        db.execute(
            select(PipelineMessage.payload).where(
                PipelineMessage.version_id == version_id,
                PipelineMessage.author == "coordinator",
            )
        )
        .scalars()
        .all()
    )
    return sum(
        1
        for p in rows
        if p
        and p.get("is_autonomous")
        and p.get("task_id") == str(task_id)
        and p.get("action") == "coordinator_answer_question"
    )


# ── R4 operator-legibility board aggregations (v0.7.0, D3/D4/D5) ───────────────────────────────────
# Computed at board-fetch (api/routes/pipeline.py:_board) — each a bounded per-version scan / one query, no
# N+1, mirroring the existing per-fetch board counts (build_readiness / _gate_e_open_findings).

#: How many autonomous decisions the board roll-up surfaces (newest first); the full count is unbounded.
_AUTONOMOUS_SUMMARY_RECENT = 5
#: An OrchestratorSession idle longer than this reads as ``stale`` on the rail (R4, D5 — 30 min).
_AGENT_STALE_SECONDS = 1800
#: The agent roles shown on the rail — the OrchestratorSession.role set = ACTOR_VALUES (CR-V2-001),
#: i.e. the two v2 agents (DB values, underscore). CR-V2-007 collapsed the v1 5-role set to these.
_AGENT_SESSION_ROLES = (AI_AGENT_ROLE, AUDITOR_ROLE)


def coordinator_triage(db: Session, version_id: uuid.UUID, state: Optional[PipelineState]) -> Optional[dict[str, Any]]:
    """RETIRED in v2 (CR-V2-009): always ``None``. The Coordinator hub-and-spoke is gone (design §2.2) —
    there is no relay/escalation triage to surface; the AI Agent reports to the Manažér directly and the
    Auditor's verdict is the only second voice. Kept as a symbol the cockpit board route still reads
    (``backend/api/routes/pipeline.py``) until the FE-contract CRs (CR-V2-021/022) drop the field; the
    ``CoordinatorTriage`` board slot then renders empty. ``db`` / ``version_id`` / ``state`` are kept for
    the route call signature."""
    del db, version_id, state
    return None


def autonomous_decisions_summary(db: Session, version_id: uuid.UUID) -> dict[str, Any]:
    """R4 (D4): board roll-up of the ``is_autonomous`` Coordinator→Director notes (Pillar B recoveries
    CR-055 + fast_fix answers CR-103) for this version — ``{count, recent:[{task, action, rationale,
    confidence}]}``, newest first, capped at :data:`_AUTONOMOUS_SUMMARY_RECENT`. Bounded like
    :func:`coordinator_triage` (§5: cheap, no N+1): the ``is_autonomous`` flag is filtered in SQL
    (``payload ->> 'is_autonomous' = 'true'``) — a ``COUNT(*)`` for the total + a separate ``LIMIT``-ed query
    for the recent few — so the board fetch never pulls every coordinator payload into Python. NOTE: this
    reuses only the ``is_autonomous`` flag, NOT :func:`_autonomous_count` (which is per-task + action-filtered);
    the roll-up spans BOTH recoveries and fast_fix answers."""
    is_autonomous = PipelineMessage.payload["is_autonomous"].astext == "true"
    base_where = (
        PipelineMessage.version_id == version_id,
        PipelineMessage.author == "coordinator",
        is_autonomous,
    )
    count = db.execute(select(func.count()).select_from(PipelineMessage).where(*base_where)).scalar_one()
    recent_rows = (
        db.execute(
            select(PipelineMessage.payload)
            .where(*base_where)
            .order_by(PipelineMessage.seq.desc())
            .limit(_AUTONOMOUS_SUMMARY_RECENT)
        )
        .scalars()
        .all()
    )
    recent = [
        {
            "task": (p or {}).get("task_number"),
            # PIPELINE-AUTONOMY §3.3: gate-level auto-ratify records carry ``stage`` (which gate auto-advanced)
            # and no ``task_id``; task-scoped recovery/answer records carry ``task`` and no ``stage`` — both
            # Optional in the schema, so the roll-up shows "which gates auto-ratified" deterministically.
            "stage": (p or {}).get("stage"),
            "action": (p or {}).get("action"),
            "rationale": (p or {}).get("rationale"),
            "confidence": (p or {}).get("confidence"),
        }
        for p in recent_rows
    ]
    return {"count": count, "recent": recent}


def agent_sessions(db: Session, version_id: uuid.UUID, state: Optional[PipelineState]) -> list[dict[str, Any]]:
    """R4 (D5): per-role agent liveness for the rail, from R1's ``OrchestratorSession.last_input_at``
    heartbeat. ``active`` = the state is ``agent_working`` for that role; ``stale`` = ``last_input_at`` older
    than :data:`_AGENT_STALE_SECONDS`; else ``idle`` (a missing session → ``idle``). One query for the
    version's project sessions; cheap."""
    slug = _project_slug_for_version(db, version_id)
    last_input = dict(
        db.execute(
            select(OrchestratorSession.role, OrchestratorSession.last_input_at).where(
                OrchestratorSession.project_slug == slug
            )
        ).all()
    )
    now = datetime.now(timezone.utc)
    working_role = state.current_actor if (state is not None and state.status == "agent_working") else None
    sessions: list[dict[str, Any]] = []
    for role in _AGENT_SESSION_ROLES:
        ts = last_input.get(role)
        if role == working_role:
            session_status = "active"
        elif ts is None:
            session_status = "idle"
        else:
            if ts.tzinfo is None:  # be robust to a naive timestamp (DB stores tz-aware; guard anyway)
                ts = ts.replace(tzinfo=timezone.utc)
            session_status = "stale" if (now - ts).total_seconds() > _AGENT_STALE_SECONDS else "idle"
        sessions.append({"role": role, "status": session_status})
    return sessions


async def _record_autonomous_decision(
    db: Session,
    version_id: uuid.UUID,
    task: Task,
    directive: dict[str, Any],
    *,
    on_message: Optional[MessageCallback] = None,
) -> None:
    """Pillar B §B.1/§B.4 VISIBILITY — record a Director-facing note that the Coordinator AUTONOMOUSLY
    decided + executed a bounded recovery (never silent). Marked ``payload.is_autonomous=true`` (the FE keys
    off it) + the directive's action / rationale / confidence + the task tag (for the per-task cap)."""
    action = directive.get("proposed_action")
    rationale = directive.get("rationale") or ""
    confidence = directive.get("confidence")
    msg = _record_message(
        db,
        version_id=version_id,
        stage="build",
        author="coordinator",
        recipient="director",
        kind="notification",
        content=f"Koordinátor rozhodol (úloha #{task.number}): {rationale or action}",
        payload={
            "is_autonomous": True,
            "task_id": str(task.id),
            "task_number": task.number,
            "action": action,
            "rationale": rationale,
            "confidence": confidence,
        },
    )
    if on_message is not None:
        await on_message(msg)


async def _record_autonomous_gate(
    db: Session,
    version_id: uuid.UUID,
    *,
    stage: str,
    action: str,
    rationale: str,
    on_message: Optional[MessageCallback] = None,
) -> None:
    """PIPELINE-AUTONOMY §3.1 VISIBILITY — record a Director-facing note that the engine AUTONOMOUSLY
    ratified a routine GATE (never silent). SEPARATE from :func:`_record_autonomous_decision` (Issue 6):
    that one is task-scoped — it hardcodes ``stage="build"``, requires a ``Task``, and writes ``task_id``
    so the per-task caps (:func:`_autonomous_count` / :func:`_autonomous_answer_count`, which filter on
    ``task_id ==``) can bound it. THIS gate-level record carries the gate ``stage`` and **NO ``task_id``**,
    so those per-task caps exclude it by construction (a null ``task_id`` never equals a task uuid).

    Marked ``is_autonomous=true`` (the board roll-up + FE key off it) + ``stage`` + ``action`` + a
    DETERMINISTIC ``rationale`` (computed by the caller from the verify signals — NOT riding on the
    synthesis LLM turn, which may ParseFail to None, Issue 7). No ``confidence`` is written (there is none
    on a PASS site — §0.1). Every routine-gate auto-ratify MUST go through here — no silent advance."""
    msg = _record_message(
        db,
        version_id=version_id,
        stage=stage,
        author="coordinator",
        recipient="director",
        kind="notification",
        content=f"Koordinátor auto-ratifikoval rutinnú bránu '{stage}': {rationale}",
        payload={
            "is_autonomous": True,
            "stage": stage,
            "action": action,
            "rationale": rationale,
        },
    )
    if on_message is not None:
        await on_message(msg)


async def _maybe_autonomous_recovery(
    db: Session,
    state: PipelineState,
    task: Task,
    directive: Optional[dict[str, Any]],
    *,
    on_message: Optional[MessageCallback] = None,
) -> bool:
    """Pillar B §B.1 — at a build HALT / Implementer question, AUTO-EXECUTE a clear bounded-recovery directive
    (no Director click) instead of escalating. Returns ``True`` when it executed (the caller CONTINUES the
    build — the executor already re-dispatched via ``_begin_dispatch``), ``False`` to take the existing
    escalate path. Conservative gate: an executable directive (conf ≥ floor + not director_decision) whose
    ``proposed_action`` is in the bounded AUTO_SET, within the per-task cap. The executor + its per-action
    safety guards already exist (CR-NS-053-verified); B only changes the TRIGGER (the Coordinator itself, when
    first-principles-clear) vs the Director's click. Every autonomous decision is recorded VISIBLY."""
    if not _coordinator_directive_executable(directive):
        return False
    assert directive is not None  # _coordinator_directive_executable returns False for None
    if directive.get("proposed_action") not in _AUTONOMOUS_RECOVERY_ACTIONS:
        return False  # route_to_designer / escalate_dedo / capture_backlog → escalate (design-quality / Director)
    if _autonomous_count(db, state.version_id, task.id) >= _MAX_AUTONOMOUS_PER_TASK:
        return False  # §B.4 cap: a repeat HALT after a clean fix is a design-quality signal → escalate
    _execute_coordinator_directive(db, state, directive)  # mutates state + re-dispatches (agent_working)
    await _record_autonomous_decision(db, state.version_id, task, directive, on_message=on_message)
    return True


# ── Miera autonómie — the 4-level autonomy dial (v2.0.0, CR-V2-008 / AUTON-1..6) ───────────────────
# REPLACES the v1 binary ``_autonomy_enabled`` toggle + the ``_maybe_autonomous_*`` decision predicates.
# The dial (design §2.3) governs how often the AI Agent STOPS at a *schvaľovací bod* for the Manažér's
# approval. Four presets:
#   * ``plna``                 — Plná autonómia: runs the whole build non-stop; no dial stop fires.
#   * ``len_na_konci``         — Len na konci: stops only when the build is verified/done.
#   * ``pri_klucovych_bodoch`` — Pri kľúčových bodoch: stops after Návrh + at build-done.
#   * ``po_kazdej_faze``       — Po každej fáze: stops after each dial-governed phase
#                                (Návrh / Programovanie / Verifikácia) for maximum control.
#: Canonical preset tuple — the SINGLE SOURCE for the resolver's validation + the FE picker order
#: (CR-V2-019/030). Declaration order = ascending human-oversight (least → most stops).
MIERA_AUTONOMIE_VALUES = ("plna", "len_na_konci", "pri_klucovych_bodoch", "po_kazdej_faze")
#: The GLOBAL-default fallback when no per-build / per-project / system_settings value resolves, AND the
#: degrade target for an unrecognised stored value. Plná autonómia (matches DEFAULT_SETTINGS).
_MIERA_AUTONOMIE_DEFAULT = "plna"

# Dial-governed *schvaľovacie body* (approval stops) in the 4-phase model. A boundary fires AFTER its
# named phase completes. These are the ONLY stops the dial governs (design §2.3):
SCHVALOVACI_BOD_NAVRH = "navrh"  # after Návrh (design + task plan)
SCHVALOVACI_BOD_PROGRAMOVANIE = "programovanie"  # after Programovanie (the coding phase)
SCHVALOVACI_BOD_VERIFIKACIA = "verifikacia"  # after Verifikácia = build verified/done (the "end" stop)
#: Every dial-governed boundary (the schvaľovacie body the dial can halt at).
DIAL_GOVERNED_BOUNDARIES = frozenset(
    {SCHVALOVACI_BOD_NAVRH, SCHVALOVACI_BOD_PROGRAMOVANIE, SCHVALOVACI_BOD_VERIFIKACIA}
)
#: Two stops are ALWAYS outside the dial — they fire at EVERY level, including ``plna`` (design §2.3,
#: D3/D6). Carved out here so :func:`dial_stops_at` never even consults the dial for them:
#:   * ``approve_spec`` — the Špecifikácia approval at the end of Príprava (ALWAYS mandatory; CR-V2-009
#:     surfaces it as the hard ``approve_spec`` stop, dial-independent).
#:   * ``deploy``       — UAT / PROD deploy is ALWAYS a separate, manual, per-customer action, outside
#:     the build pipeline (the deploy subsystem owns it; the dial never reaches it).
ALWAYS_STOP_BOUNDARIES = frozenset({"approve_spec", "deploy"})

#: For each level, the set of dial-governed boundaries at which the build STOPS for the Manažér. The
#: complement (within :data:`DIAL_GOVERNED_BOUNDARIES`) auto-continues. Derived directly from the design
#: §2.3 table; ``plna`` stops at none, ``po_kazdej_faze`` stops at all three.
_DIAL_STOP_BOUNDARIES: dict[str, frozenset[str]] = {
    "plna": frozenset(),
    "len_na_konci": frozenset({SCHVALOVACI_BOD_VERIFIKACIA}),
    "pri_klucovych_bodoch": frozenset({SCHVALOVACI_BOD_NAVRH, SCHVALOVACI_BOD_VERIFIKACIA}),
    "po_kazdej_faze": frozenset({SCHVALOVACI_BOD_NAVRH, SCHVALOVACI_BOD_PROGRAMOVANIE, SCHVALOVACI_BOD_VERIFIKACIA}),
}

#: OQ-9 — the Auditor's depth/effort scales INVERSELY with human oversight: higher autonomy (fewer
#: Manažér stops) → deeper, more adversarial Auditor (the safety net that compensates). Mapped to the
#: CR-V2-007 ``--effort`` levels. Plná autonómia → the Auditor is the only independent eyes → ``max``;
#: Po každej fáze → the Manažér checks often → ``high`` (still a real audit, just lighter). The DEPTH of
#: the review (how adversarial / how many spot-checks) is applied per-touchpoint in CR-V2-013/014; this
#: is the effort-flag half of the coupling, consumed by :func:`_resolve_dispatch_overrides`.
_AUDITOR_EFFORT_FOR_LEVEL: dict[str, str] = {
    "plna": "max",
    "len_na_konci": "max",
    "pri_klucovych_bodoch": "high",
    "po_kazdej_faze": "high",
}


def _normalize_miera_autonomie(value: Optional[str]) -> Optional[str]:
    """Return *value* iff it is a recognised preset, else ``None`` (so a caller can fall through to the
    next resolution layer / the default). An unrecognised or empty stored value never crashes — it
    degrades, never silently mis-behaves (the value set evolves in code, not via a DB CHECK)."""
    if value is None:
        return None
    v = value.strip()
    return v if v in MIERA_AUTONOMIE_VALUES else None


def resolve_miera_autonomie(db: Session, version_id: uuid.UUID) -> str:
    """Resolve the effective Miera autonómie LEVEL for a build (AUTON-6).

    Resolution order — first NON-NULL (and recognised) layer wins (design §2.3):

        per-build (``pipeline_state.miera_autonomie``)
          → per-project (``projects.miera_autonomie``)
            → global (``DEFAULT_SETTINGS['miera_autonomie']`` / its ``system_settings`` row)
              → :data:`_MIERA_AUTONOMIE_DEFAULT` (belt-and-suspenders if the global is unreadable).

    NULL at a layer means "inherit the next layer up"; an unrecognised stored value at a layer is treated
    as NULL (degrade through, never crash). One cheap row fetch joins the build's project + its state; the
    global read goes through the cached :mod:`system_setting` getter. Always returns one of
    :data:`MIERA_AUTONOMIE_VALUES`.

    **Fast-fix carve-out (design §2.3 — "Fast-fix = dial at full-auto"):** a ``fast_fix`` build ALWAYS
    runs at ``plna``, regardless of any per-build / per-project / global setting. The fast-fix lane is its
    own minimal full-auto path (Oprava → quick verify → done); the override layers govern only
    ``new_version`` builds. This is absolute, so it short-circuits BEFORE the override layers."""
    row = db.execute(
        select(PipelineState.miera_autonomie, Project.miera_autonomie, PipelineState.flow_type)
        .select_from(Version)
        .join(Project, Project.id == Version.project_id)
        .outerjoin(PipelineState, PipelineState.version_id == Version.id)
        .where(Version.id == version_id)
    ).first()
    if row is not None:
        if row[2] == "fast_fix":
            return "plna"  # fast-fix = dial at full-auto (design §2.3), overrides every layer
        per_build = _normalize_miera_autonomie(row[0])
        if per_build is not None:
            return per_build
        per_project = _normalize_miera_autonomie(row[1])
        if per_project is not None:
            return per_project
    # Global layer — the system_settings KV (DEFAULT_SETTINGS-backed). Degrade an unrecognised stored
    # global to the hard default so the dial is ALWAYS one of the four presets.
    try:
        global_value = _normalize_miera_autonomie(system_setting_service.get_str(db, "miera_autonomie"))
    except KeyError:  # key somehow missing from DEFAULT_SETTINGS → hard default
        global_value = None
    return global_value or _MIERA_AUTONOMIE_DEFAULT


def dial_stops_at(level: str, boundary: str) -> bool:
    """Pure dial logic — does the *schvaľovací bod* ``boundary`` HALT the build for the Manažér at the
    given autonomy ``level``? The new evaluator that REPLACES the v1 ``_maybe_autonomous_*`` predicates;
    CR-V2-009's ``apply_action`` consults it at each phase boundary to decide settle-for-Manažér vs
    auto-continue.

    Two carve-outs are independent of the dial and ALWAYS stop (design §2.3, D3/D6):
    :data:`ALWAYS_STOP_BOUNDARIES` (``approve_spec`` end-Príprava + ``deploy``) return ``True`` at EVERY
    level, including ``plna``. For the dial-governed boundaries (after Návrh / Programovanie /
    Verifikácia) the stop set per level is :data:`_DIAL_STOP_BOUNDARIES`. An unrecognised ``level``
    degrades to the default; a boundary that is neither always-stop nor dial-governed never stops
    (an internal step the dial does not gate)."""
    if boundary in ALWAYS_STOP_BOUNDARIES:
        return True  # dial-independent: spec approval + deploy always stop
    lvl = level if level in MIERA_AUTONOMIE_VALUES else _MIERA_AUTONOMIE_DEFAULT
    return boundary in _DIAL_STOP_BOUNDARIES[lvl]


def auditor_effort_for_level(level: str) -> str:
    """OQ-9 — the Auditor ``--effort`` flag for the given autonomy ``level`` (inverse to human oversight:
    higher autonomy → deeper Auditor). An unrecognised level degrades to the default's effort. The DEPTH
    (adversarial spot-check intensity) is applied in CR-V2-013/014; this is the effort-flag coupling
    consumed by :func:`_resolve_dispatch_overrides`."""
    lvl = level if level in MIERA_AUTONOMIE_VALUES else _MIERA_AUTONOMIE_DEFAULT
    return _AUDITOR_EFFORT_FOR_LEVEL[lvl]


def _settle_phase_boundary(db: Session, state: PipelineState) -> bool:
    """Apply the Miera autonómie dial at a SETTLED phase boundary (Milestone-C SHARED dial-settle wiring;
    CR-V2-010, owned here + inherited by CR-V2-011/012). The agent for ``state.current_stage`` produced
    final phase output (a gate_report / done-class turn); decide STOP-for-the-Manažér vs AUTO-CONTINUE.

    Returns:
      * ``True``  → AUTO-CONTINUE: the build advanced to the next phase and is now ``agent_working``; the
        runner's auto-chain loop dispatches it in the SAME single-flight task (no Manažér gate between).
      * ``False`` → STOP: the boundary halts for the Manažér; the caller settles ``awaiting_manazer``.

    The dial governs ONLY the three dial-governed schvaľovacie body (after Návrh / Programovanie /
    Verifikácia — :data:`DIAL_GOVERNED_BOUNDARIES`). Two boundaries are ALWAYS outside the dial and ALWAYS
    stop (:data:`ALWAYS_STOP_BOUNDARIES`, design §2.3 D3/D6):
      * **Príprava → Schváliť špecifikáciu** — the Špecifikácia approval is dial-INDEPENDENT and ALWAYS
        mandatory: Príprava is NOT in ``DIAL_GOVERNED_BOUNDARIES``, so :func:`dial_stops_at` is never even
        consulted for it here → it always returns ``False`` (STOP). Návrh cannot begin until the Manažér
        clicks ``approve_spec``.
      * **Verifikácia end sign-off** — at a non-stopping dial level a PASS verdict auto-signs-off to Hotovo,
        but ONLY through the recorded Auditor PASS verdict (no-silent-done invariant, safeguard #5): if no
        PASS is on record the boundary STOPS regardless of the dial (never a silent done without
        verification). The full Verifikácia behaviour (verdict emission, fix-loop) is CR-V2-014; this wiring
        only governs the dial half of the end stop + preserves the invariant.

    Auto-continue advances ``current_stage`` via :func:`_next_stage` + :func:`_begin_dispatch` (which sets
    ``agent_working`` at the next phase). The sole-mutator invariant is preserved: this runs inside the
    dispatch path, always as a consequence of an action already routed through :func:`apply_action`."""
    stage = state.current_stage
    if stage not in DIAL_GOVERNED_BOUNDARIES:
        # Príprava (approve_spec — always-stop) + any non-boundary phase: never auto-continue here.
        return False
    level = resolve_miera_autonomie(db, state.version_id)
    if dial_stops_at(level, stage):
        return False  # the dial halts this schvaľovací bod for the Manažér
    # Auto-continue (the dial does NOT stop here). The Verifikácia end stop additionally guards the
    # no-silent-done invariant: Hotovo is reachable ONLY through a recorded Auditor PASS verdict.
    if stage == SCHVALOVACI_BOD_VERIFIKACIA and not _verifikacia_passed(db, state.version_id):
        return False  # no PASS on record → STOP (never a silent done without verification)
    state.current_stage = _next_stage(stage, state.flow_type)
    if state.current_stage == "done":
        # Verifikácia auto-sign-off at a non-stopping dial level → Hotovo (terminal; deploy is OUT, D6).
        state.current_actor = "ai_agent"  # terminal — no agent on turn; kept a valid ACTOR value
        state.status = "done"
        state.next_action = "Pipeline dokončená (Hotovo). Nasadenie je samostatná akcia per zákazník."
        db.flush()
        return False  # terminal — nothing left to auto-chain (status is 'done', not 'agent_working')
    _begin_dispatch(db, state)  # agent_working at the next phase → the runner's auto-chain runs it
    return True


def _autonomy_enabled(db: Session, version_id: uuid.UUID) -> bool:
    """Bridge for the v1 ``_maybe_autonomous_*`` routine-gate predicates while their call sites are
    rebuilt in CR-V2-009 (apply_action) — now BACKED BY THE DIAL, not the retired binary kickoff toggle.

    The v1 callers (``_maybe_autonomous_gate_ratify`` / ``_maybe_autonomous_build_ratify`` /
    ``_maybe_autonomous_gate_e_continue``) ask "auto-advance this ROUTINE gate without a Manažér click?".
    In dial terms that is "the dial does NOT make me stop here". The routine v1 gates are NOT the key
    Návrh / build-done stops, so they auto-advance at every level EXCEPT ``po_kazdej_faze`` (which stops
    after each phase for maximum control). Hence: autonomy "on" ⇔ resolved level ≠ ``po_kazdej_faze``.
    This keeps the v1 binary contract intact, sourced from the dial, until CR-V2-009 replaces these
    predicates with direct :func:`dial_stops_at` checks at the new 4-phase boundaries."""
    return resolve_miera_autonomie(db, version_id) != "po_kazdej_faze"


async def _maybe_autonomous_gate_ratify(
    db: Session,
    state: PipelineState,
    reason: Optional[str],
    is_scope: bool,
    *,
    on_message: Optional[MessageCallback] = None,
) -> bool:
    """PIPELINE-AUTONOMY Phase 1 (design §5.1) — at a full-flow routine gate (a–d) PASS, AUTO-RATIFY with
    NO Director click: advance to the next stage + re-dispatch, instead of settling ``awaiting_director``.
    Sibling of :func:`_maybe_autonomous_recovery`. Returns ``True`` when it advanced (the caller returns the
    now-``agent_working`` state; the runner's auto-chain loop dispatches the next stage in the SAME task),
    ``False`` → the caller takes the existing ``awaiting_director`` settle unchanged.

    The guard is purely DETERMINISTIC (design §0.1 — there is NO confidence/triage on a PASS site): the
    Coordinator already ran verify (mechanical + judgment) and it came back clean, so the Director's ratify
    click adds nothing the engine didn't deterministically verify. ALL must hold:

    * ``flow_type == 'new_version'`` — fast_fix / cr / bug keep their generic settle byte-for-byte;
    * ``reason is None`` ∧ ``is_scope is False`` — verify PASS, no scope/design question (from
      :func:`_verify_with_retries`); a FAIL or scope flag already pre-empted this PASS branch upstream;
    * ``current_stage`` ∈ the routine gates a–d AND explicitly NOT ``release`` / ``gate_g`` (Issue 10 —
      engine-owned publish / a release verdict are KEY, never auto);
    * the version's kickoff autonomy toggle is ON (:func:`_autonomy_enabled`, default).

    Every auto-ratify is recorded Director-visibly via :func:`_record_autonomous_gate`
    (``is_autonomous=true`` + ``stage`` + a deterministic rationale) so the board roll-up shows exactly
    which gates auto-advanced. No silent advance is possible."""
    if state.flow_type != "new_version":
        return False
    if reason is not None or is_scope:
        return False
    if state.current_stage in _NEVER_AUTO_RATIFY_STAGES:
        return False  # belt-and-suspenders (Issue 10): never auto-advance release / gate_g
    if state.current_stage not in _AUTO_RATIFY_GATES:
        return False
    if not _autonomy_enabled(db, state.version_id):
        return False  # kickoff opt-out → the Director wants per-gate sign-off
    ratified_stage = state.current_stage
    rationale = (
        f"Brána '{ratified_stage}' prešla overením (mechanical + judgment) bez otázky rozsahu — "
        "auto-ratifikované, postup na ďalšiu fázu."
    )
    state.current_stage = _next_stage(ratified_stage, state.flow_type)
    _begin_dispatch(db, state)  # status=agent_working at the next stage → the runner continues the chain
    await _record_autonomous_gate(
        db,
        state.version_id,
        stage=ratified_stage,
        action="auto_ratify_gate",
        rationale=rationale,
        on_message=on_message,
    )
    return True


# (The v1 ``_maybe_autonomous_build_ratify`` — auto-ratify the build→gate_g sign-off — is RETIRED with
# CR-V2-012's build-round rebuild: it was build-completion-only, referenced the retired v1 ``build``/``gate_g``
# stages, and is subsumed by the Miera autonómie dial (the Programovanie schvaľovací bod auto-continues to
# Verifikácia at a non-stopping level via :func:`_settle_phase_boundary`). The remaining ``_maybe_autonomous_*``
# helpers stay only on the deferred-RED gate-e path CR-V2-013 re-points.)


def _gate_e_budget_reached(db: Session, version_id: uuid.UUID) -> bool:
    """True when Gate E has asked at least the ceiling number of questions (§2.1) — the signal the caller
    uses to ESCALATE to the Director with an extend-or-close ``next_action`` (never a silent close)."""
    _, ceiling = _gate_e_question_budget(db, version_id)
    return _gate_e_question_count(db, version_id) >= ceiling


async def _maybe_autonomous_gate_e_continue(
    db: Session,
    state: PipelineState,
    *,
    boundary: str,
    on_message: Optional[MessageCallback] = None,
) -> bool:
    """PIPELINE-AUTONOMY Phase 3 (design §2.2/§5.2) — at a Gate E per-question (Branch A) or a CLEAN topic
    boundary, AUTO-CONTINUE to the next Customer turn with NO Director click: self-issue ``_begin_dispatch``
    (status=agent_working at gate_e, so the runner auto-chain runs the next Customer turn) instead of settling
    ``awaiting_director``. Returns ``True`` when it continued (the caller returns the now-``agent_working``
    state), ``False`` → the caller takes the existing ``awaiting_director`` settle.

    Sibling of :func:`_maybe_autonomous_gate_ratify`, with the SAME purely DETERMINISTIC discipline (design
    §0.1 — there is NO confidence on the Designer status block; the guard reads only real booleans/counts):

    * ``flow_type == 'new_version'`` — fast_fix / cr / bug never reach Gate E this way, byte-for-byte;
    * 0 open Gate E findings (deterministic :func:`_gate_e_open_findings`, never the Customer's self-report) —
      an open gap blocks any continue, mirroring the close gate;
    * the question count is UNDER the scope-scaled ceiling (§2.1) — reaching the ceiling makes this return
      ``False`` so the caller ESCALATES to the Director (extend or close), it NEVER silent-closes;
    * the kickoff autonomy toggle is ON (:func:`_autonomy_enabled`, default).

    The CALLER gates the two KEY exclusions BEFORE calling this (so they are unmistakable at the settle site):
    ``gap_found`` (Branch B — a genuine spec decision, always the Director, design §2.4) and
    ``coverage_complete`` (the FINAL close — the ONE bounded Director sign-off, design §2.3). A ParseFailure
    can never reach here (it already settled ``blocked`` upstream). Every continue is recorded
    Director-visibly via :func:`_record_autonomous_gate` (``is_autonomous=true`` + ``stage='gate_e'`` + a
    deterministic rationale), so the board roll-up shows the Gate E questions/topics that auto-continued."""
    if state.flow_type != "new_version":
        return False
    if _gate_e_open_findings(db, state.version_id) > 0:
        return False  # an open finding blocks any continue (mirror the deterministic close gate)
    asked = _gate_e_question_count(db, state.version_id)
    _, ceiling = _gate_e_question_budget(db, state.version_id)
    if asked >= ceiling:
        return False  # budget ceiling → the caller escalates to the Director (never silent-close, §2.1)
    if not _autonomy_enabled(db, state.version_id):
        return False  # kickoff opt-out → the Director wants per-question / per-topic sign-off
    if boundary == "topic":
        rationale = (
            f"Okruh Gate E uzavretý bez otvorených nálezov (0 medzier, {asked}/{ceiling} otázok) — "
            "auto-pokračovanie na ďalší okruh previerky."
        )
    else:  # "question" — Branch A
        rationale = (
            f"Odpoveď Návrhára bez medzery ({asked}/{ceiling} otázok, 0 otvorených nálezov) — "
            "auto-pokračovanie na ďalšiu otázku Gate E."
        )
    _begin_dispatch(db, state)  # status=agent_working at gate_e → the runner continues the chain
    await _record_autonomous_gate(
        db,
        state.version_id,
        stage="gate_e",
        action=f"auto_continue_gate_e_{boundary}",
        rationale=rationale,
        on_message=on_message,
    )
    return True


def _fast_fix_answer_brief(task: Task, answer: str) -> str:
    """The re-dispatch brief that resumes a fast_fix build task with the Coordinator's autonomous answer
    (CR-NS-103). Used as the next attempt's ``pending_directive`` (mirrors the Director's framed-return path)."""
    return (
        f"Programátor, pokračuj v úlohe #{task.number} '{task.title}'. Koordinátor odpovedal na tvoju otázku "
        f"(rýchla oprava, F-009): {answer} Vykonaj úlohu podľa tejto odpovede — NEPÝTAJ sa znova na to isté."
    )


async def _maybe_autonomous_answer(
    db: Session,
    state: PipelineState,
    task: Task,
    directive: Optional[dict[str, Any]],
    *,
    on_message: Optional[MessageCallback] = None,
) -> Optional[str]:
    """Fast-Fix Lane (F-009 §3 D5, CR-NS-103) — at a build-stage ROUTINE Programmer question, AUTO-ANSWER it
    (no Director gate) instead of escalating, then resume the SAME task with the answer as its brief. Sibling
    of :func:`_maybe_autonomous_recovery`. Returns the answer prompt for the caller to set as the resumed
    task's first-attempt ``pending_directive`` when it fires (the task is reset to ``todo`` + re-dispatched
    here), else ``None`` → the caller takes the EXISTING escalate path unchanged.

    Guard: ``flow_type == 'fast_fix'`` ONLY — ``new_version`` / ``cr`` / ``bug`` keep escalating worker
    questions byte-for-byte (no autonomy leak). Conservative bounds (D5): a ``coordinator_answer_question``
    directive, ``triage_class != director_decision``, honest confidence ≥ 0.85 (above the 0.80 recovery floor
    — an answer is less reversible than a task reset), within ≤2 answers per task. The 3rd routine question on
    one task → ``None`` → escalate (signals not-trivial → convert-to-full). Every answer is recorded
    Director-visibly (``is_autonomous=true``, reuse :func:`_record_autonomous_decision`)."""
    if state.flow_type != "fast_fix":
        return None
    if not directive:
        return None
    if directive.get("proposed_action") != "coordinator_answer_question":
        return None
    if directive.get("triage_class") == "director_decision":
        return None
    if float(directive.get("confidence") or 0.0) < _FAST_FIX_ANSWER_CONFIDENCE_FLOOR:
        return None
    if _autonomous_answer_count(db, state.version_id, task.id) >= _MAX_AUTONOMOUS_ANSWERS_PER_TASK:
        return None  # D5 cap: the 3rd routine question on one task → escalate (not trivial → convert-to-full)
    answer = (directive.get("rationale") or "").strip()
    if not answer:
        return None  # no answer text to inject → escalate rather than resume the task blind
    await _record_autonomous_decision(db, state.version_id, task, directive, on_message=on_message)
    # Resume the SAME task: reset it to todo so the build loop re-picks it, hand back agent_working so the
    # loop continues the chain. The caller injects the returned brief as attempt 1's prompt (pending_directive).
    db.execute(update(Task).where(Task.id == task.id).values(status="todo"))
    db.flush()
    task_service.recompute_feat_status(db, task.feat_id)
    _begin_dispatch(db, state)
    return _fast_fix_answer_brief(task, answer)


def recover_orphaned_builds_on_startup(db: Session) -> int:
    """On BE startup, recover pipelines stranded at ``agent_working`` by a restart (F-007 §7.3,
    CR-NS-021; all phases since R1-d / D4). Returns the number recovered. R-BLAST safeguard #4
    (resume-safety / startup orphan recovery) — preserved + re-pointed to the 4-phase model in CR-V2-009.

    A dispatch runs as a background task; a backend restart kills it, stranding the pipeline at
    ``<phase>`` / ``agent_working`` with no auto-resume. For every such row this flips to
    ``awaiting_manazer``, records a ``system→manazer`` ``notification`` carrying a ``baseline..HEAD``
    commit audit (so committed-but-lost work is surfaced — the lost-work safeguard #3 on the recovery
    path), and clears the durable single-flight flag + resets the dispatch baseline (the killed process
    left them set — Seam #2: a crash self-heals on startup). A stranded ``programovanie`` phase keeps the
    resume CTA (the Manažér resumes via "Pokračovať" → ``pokracovat``); other phases get a generic
    phase-parametrized message. ``Task.status`` is untouched, so a stranded ``in_progress`` task stays
    counted and the schvaľovací bod stays gated until the loop resumes.
    """
    rows = db.execute(select(PipelineState).where(PipelineState.status == "agent_working")).scalars().all()
    for state in rows:
        stage = state.current_stage
        project_root = claude_agent.PROJECTS_ROOT / _project_slug_for_version(db, state.version_id)
        # Read the baseline into a local BEFORE the settling status write (the set listener resets it).
        baseline = state.dispatch_baseline_sha or _repo_head(project_root)
        head = _repo_head(project_root)
        count = _rev_list_count(project_root, baseline)
        audit = (
            f"môžu byť zapísané zmeny ({count} commitov), over 'git log'" if count >= 1 else "žiadna zmena nezistená"
        )
        if stage == "programovanie":
            # The coding loop keeps the resume CTA ("Pokračovať" = pokracovat); the per-task reclaim is
            # additive (CR-V2-012's self-checking loop owns it), not replaced here.
            state.next_action = "Programovanie prerušené reštartom backendu — pokračuj cez 'Pokračovať'."
            content = (
                "Programovanie bolo prerušené reštartom backendu — obnovené do stavu 'čaká na Manažéra'. "
                "Pokračuj cez 'Pokračovať'."
            )
        else:
            state.next_action = f"Fáza '{stage}' prerušená reštartom — {audit}. Pokračuj."
            content = (
                f"Fáza '{stage}' bola prerušená reštartom backendu — {audit}. Obnovené do stavu 'čaká na Manažéra'."
            )
        _record_message(
            db,
            version_id=state.version_id,
            stage=stage,
            author="system",
            recipient="manazer",
            kind="notification",
            content=content,
            payload={
                "recovery_audit": True,
                "phase": stage,  # per-turn phase stamp (CR-V2-009)
                "dispatch_baseline_sha": baseline,
                "post_restart_head_sha": head,
                "detected_commit_count": count,
            },
        )
        state.status = "awaiting_manazer"  # the set listener also clears the flag + baseline …
        state.dispatch_in_flight = False  # … cleared explicitly too for robustness (Seam #2).
        state.dispatch_baseline_sha = None
    db.commit()
    return len(rows)


# R1-d (D3) session hygiene: OrchestratorSession rows are retained for 7 days since last activity
# (``last_input_at``), then pruned by the background retention task — conservative, mirrors the proven
# ``agent_terminal.idle_cleanup``. A stale ``--resume`` thread is cheap; this only bounds row growth.
ORCHESTRATOR_SESSION_TTL_SECONDS = 7 * 24 * 3600
ORCHESTRATOR_SESSION_CLEANUP_INTERVAL_SECONDS = 24 * 3600


def cleanup_old_orchestrator_sessions(db: Session) -> int:
    """Delete OrchestratorSession rows untouched for > 7 days (TTL on ``last_input_at``); returns the count.

    D3 session hygiene — mirrors ``agent_terminal.idle_cleanup``, wired as a daily background loop in
    ``main.py``'s lifespan. Hygiene, not a crash-preventer: a new-version kickoff already deletes a
    project's sessions, so this just prunes long-idle threads to bound unbounded growth."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=ORCHESTRATOR_SESSION_TTL_SECONDS)
    result = db.execute(delete(OrchestratorSession).where(OrchestratorSession.last_input_at < cutoff))
    db.commit()
    count = result.rowcount or 0
    if count:
        logger.info("cleanup_old_orchestrator_sessions pruned %d idle session(s)", count)
    return count


def _fetch_cross_cutting_rules(db: Session, version_id: uuid.UUID) -> Optional[str]:
    """Re-read the cross-cutting regulated-ledger invariants the AI Agent codified once in the Návrh
    gate_report payload (CR-NS-020 CR-2; v2 CR-V2-011 — the plan + its rules fold into the Návrh phase).
    Injected into every per-task build brief (consumed by the Programovanie loop, CR-V2-012)."""
    msg = db.execute(
        select(PipelineMessage)
        .where(
            PipelineMessage.version_id == version_id,
            PipelineMessage.stage == "navrh",
            PipelineMessage.author == "ai_agent",
            PipelineMessage.kind == "gate_report",
        )
        .order_by(PipelineMessage.seq.desc())
        .limit(1)
    ).scalar_one_or_none()
    if msg is None or not msg.payload:
        return None
    return msg.payload.get("cross_cutting_rules")


def _directive_for_build_task(
    task: Task, cross_cutting_rules: Optional[str], prior_failures: list[str], flow_type: str = "new_version"
) -> str:
    """Per-task brief for the AI Agent's Programovanie SELF-CHECKING loop (CR-V2-012; design §2.1 / §5.1(1)
    "self-check — continuous self-verification while coding, like Dedo").

    DESIGN-BEARING (flagged for the Manažér): this prompt DEFINES the AI Agent's per-task Programovanie
    behaviour — implement ONE task from the Návrh plan AND run its own tests/verification before reporting
    done. There is NO per-task Auditor in v2 (the AI Agent is its own first line of quality; the independent
    Auditor verifies once at Verifikácia, not per task — design §2.2 / D5). The brief carries: the task +
    its description, the authoritative spec section to consult, the cross-cutting invariants, and (on a
    retry) the prior attempts' reasons.

    ``flow_type='fast_fix'`` (design §2.4): the Manažér's directive (the task description) IS the authority —
    there is no spec section to study, and the AI Agent must EXECUTE it directly rather than debate it on
    semantic/opinion grounds (the live v1 run blocked asking "naozaj to chceš premenovať?")."""
    parts = [f"AI Agent, postav JEDNU úlohu (TASK #{task.number}): {task.title}"]
    if task.description:
        parts.append(f"Popis úlohy: {task.description}")
    if flow_type == "fast_fix":
        parts.append(
            "RÝCHLA OPRAVA (fast-fix lane): pokyn Manažéra vyššie je AUTORITATÍVNY — VYKONAJ ho priamo. "
            "NESPOCHYBŇUJ ho z názorových / sémantických dôvodov (napr. „Firmy je správne, naozaj to chceš "
            "premenovať?“). ZASTAV (kind=blocked) IBA ak je to technicky nemožné, alebo naozaj nevieš "
            "identifikovať ČO zmeniť — NIE preto, že s pokynom nesúhlasíš."
        )
    else:
        parts.append(
            "Naštuduj relevantnú sekciu autoritatívneho špecu (docs/specs/) pre túto úlohu — postav presne ju."
        )
    if cross_cutting_rules:
        parts.append(f"Prierezové pravidlá (platia pre KAŽDÚ úlohu, dodrž ich):\n{cross_cutting_rules}")
    if prior_failures:
        joined = "\n".join(f"- pokus {i}: {r}" for i, r in enumerate(prior_failures, 1))
        parts.append(f"Predošlé NEÚSPEŠNÉ pokusy o túto úlohu — oprav uvedené:\n{joined}")
    # The v2 self-check: the AI Agent runs its OWN tests/verification before reporting done (design §2.1 —
    # "never its own final judge" is the Auditor at Verifikácia, but it IS its own first line of quality).
    # NO per-task Auditor turn follows; the engine's per-task gate is the deterministic mechanical commit
    # verify (verify_mechanical), so the agent MUST commit + report commits[]/deliverables[] honestly.
    parts.append(
        "Implementuj úlohu a PRIEBEŽNE si sám over výsledok (spusti vlastné testy / verifikáciu — si prvá "
        "línia kvality; nezávislý Auditor príde až raz vo Verifikácii, NIE po každej úlohe). "
        "Commitni zmeny a ukonči <<<PIPELINE_STATUS>>> blokom s commits[] + deliverables[] "
        "(F-007-orchestration-cockpit.md §5.3)."
    )
    return "\n\n".join(parts)


def _pokusy(n: int) -> str:
    """Slovak plural for the attempt count (1 pokus / 2–4 pokusy / 5+ pokusov)."""
    if n == 1:
        return "1 pokus"
    if 2 <= n <= 4:
        return f"{n} pokusy"
    return f"{n} pokusov"


async def _record_task_summary(
    db: Session,
    version_id: uuid.UUID,
    task: Task,
    *,
    status: str,
    attempts: int,
    work_summary: Optional[str] = None,
    attempt_errors: Optional[list[str]] = None,
    on_message: Optional[MessageCallback] = None,
) -> None:
    """Record ONE factual per-task summary for the Manažér at a Programovanie task settle (``done`` |
    ``failed``) — CR-V2-012. What was done + how many self-check ATTEMPTS + the exact last error for
    drill-down. Pure surfacing of EXISTING loop data (no LLM turn — keeps the build cheap + automated);
    marked ``payload.is_task_summary=true`` (the FE keys off it).

    **CR-V2-012 — NO per-task Auditor verdict.** v1 folded a per-task ``audit_verdict`` (``task_pass`` +
    Auditor ``findings``) into this card; v2 drops it entirely. The AI Agent self-checks its own work
    (design §2.1 / §5.1(1)); the independent Auditor verifies ONCE at Verifikácia, not per task (§2.2 /
    D5). So this card carries only the AI Agent's own work summary + the engine's deterministic
    mechanical-verify outcome — never a per-task audit verdict message. **Additive: never gates the loop.**"""
    errors = attempt_errors or []
    last_error = errors[-1] if errors else None
    done = status == "done"
    content = f"Úloha #{task.number} „{task.title}“ — {'hotovo' if done else 'zlyhalo'} ({_pokusy(attempts)})"
    msg = _record_message(
        db,
        version_id=version_id,
        stage="programovanie",
        author="system",
        recipient="manazer",
        kind="notification",
        content=content,
        payload={
            "is_task_summary": True,
            "phase": "programovanie",  # per-turn phase stamp (CR-V2-009; consumed by CR-V2-029 metrics)
            "task_summary": {
                "task_id": str(task.id),
                "task_number": task.number,
                "title": task.title,
                "final_status": status,
                "attempts": attempts,
                "last_error": last_error,
                "work_summary": work_summary,
                "attempt_errors": errors,
            },
        },
    )
    if on_message is not None:
        await on_message(msg)


#: Per-task SELF-CHECK re-attempt bound for the Programovanie loop (CR-V2-012; replaces the v1 per-task
#: ``_AUTO_FIX_RETRIES``). The AI Agent self-checks its own work as it codes (design §2.1); if a task's
#: deterministic mechanical verify (commit exists + deliverables on disk + in baseline..HEAD) fails, the
#: engine returns the task to the AI Agent with the reason, bounded to this many re-attempts. On exhaustion
#: the build STOPS and surfaces it to the Manažér DIRECTLY (no Coordinator relay — retired in v2, the AI
#: Agent reports to the Manažér itself, design §2.2). DISTINCT from :data:`AUDITOR_LOOP_MAX`, which bounds
#: the Auditor↔AI-Agent fix↔re-verify rounds at Verifikácia (CR-V2-014).
_SELF_CHECK_RETRIES = 5


async def _run_build_round(
    db: Session,
    state: PipelineState,
    *,
    on_event: Optional[claude_agent.EventCallback] = None,
    directive: Optional[str] = None,
    on_message: Optional[MessageCallback] = None,
) -> PipelineState:
    """The Programovanie phase — the AI Agent's SELF-CHECKING coding loop (CR-V2-012; PROG-1, ARCH-5).

    Rebuilds the v1 per-task-audited build loop (Designer→Implementer→Auditor→Coordinator hub-and-spoke,
    per-task Auditor verdict, HALT→Coordinator relay) as ONE agent (``ai_agent``) executing the Návrh task
    plan task-by-task with its OWN continuous self-verification — "like Dedo" (design §2.1 / §5.1(1) / D5).
    Per task the AI Agent implements + runs its own tests/verification, commits, and reports; **there is NO
    per-task Auditor** — the independent Auditor verifies ONCE at Verifikácia (§2.2). The engine's per-task
    gate is the deterministic **mechanical commit verify** (:func:`verify_mechanical` scoped to the task
    baseline: commit exists + deliverables on disk + in ``baseline..HEAD``) — never an LLM audit turn.

    Like v1, build does NOT stop between successful tasks: it loops in plan order, mechanically verifies each
    (bounded self-check re-attempts up to :data:`_SELF_CHECK_RETRIES` with the prior reasons threaded into
    the next brief), and only at the END applies the **Miera autonómie dial** (:func:`_settle_phase_boundary`)
    — auto-continue to Verifikácia (``plna``) or STOP ``awaiting_manazer`` at the Programovanie schvaľovací
    bod. A mid-loop AI-Agent question / a self-check exhaustion / an unreadable baseline settles for the
    Manažér DIRECTLY (``awaiting_manazer`` / ``blocked``) — the Coordinator hub-and-spoke relay is RETIRED
    in v2 (the AI Agent reports to the Manažér itself, §2.2).

    **Safeguards preserved (R-BLAST):** the lost-work audit (``dispatch_baseline_sha`` → :func:`_audit_lost_work`
    fires inside :func:`invoke_agent` on an envelope-loss; surfaced via ``ParseFailure.lost_work`` →
    ``awaiting_manazer``, committed-but-lost work never silently dropped — safeguard #3); mechanical commit
    verify (safeguard backing #1's deliverable honesty); resume-safety (an orphaned ``in_progress`` task is
    reclaimed to ``todo`` and re-run from its persisted ``baseline_sha``); single-flight (this runs inside the
    dispatch path, never re-entered concurrently); cooperative pause (a Manažér ``pause`` lands cleanly at a
    task boundary via the READ-COMMITTED refresh).

    **Helper seam (CR-V2-018):** the AI Agent may spawn ephemeral helpers via its own ``claude`` session's
    sub-agent tool during a bulk task — internal to the turn; CR-V2-018 surfaces them in the Helpers panel.
    No backend helper orchestrator exists here.
    """
    version_id = state.version_id
    slug = _project_slug_for_version(db, version_id)
    project_root = claude_agent.PROJECTS_ROOT / slug
    feat_ids_of_version = select(Feat.id).join(Epic, Epic.id == Feat.epic_id).where(Epic.version_id == version_id)

    # Resume-safety: reclaim a task orphaned mid-build (an in_progress task left by a dispatch that died).
    db.execute(
        update(Task).where(Task.feat_id.in_(feat_ids_of_version), Task.status == "in_progress").values(status="todo")
    )
    db.flush()

    # Cross-cutting invariants the AI Agent codified once in the Návrh gate_report (re-read each round, threaded
    # into every task brief). The v1 gate_g/surgical-fix re-gate threading is GONE — the Verifikácia FAIL fix
    # loop (the Auditor's findings → AI-Agent fix scope) is owned by CR-V2-014, not the per-task build loop.
    cross_cutting = _fetch_cross_cutting_rules(db, version_id)
    # The Manažér's framed return/answer (an ``uprav`` / ``answer`` re-dispatch) seeds attempt 1 of whichever
    # task runs first in THIS dispatch (the resumed task), then is consumed so later turns use generated briefs.
    pending_directive = directive

    while True:
        # CR-NS-027 visibility crux: SessionLocal is expire_on_commit=False, so after the loop's per-message
        # commits the identity-mapped PipelineState keeps STALE attributes. db.refresh forces a fresh row read;
        # Postgres READ COMMITTED then sees a 'paused' the Manažér set in a separate request session → the loop
        # stops cleanly at this task boundary (cooperative pause, never a mid-task kill).
        state = _get_state(db, version_id)
        if state is not None:
            db.refresh(state)
        if state is None or state.status != "agent_working":
            return state  # Manažér intervened (pause / steer) — land cleanly at a task boundary

        task = task_service.get_next_todo_task(db, version_id)
        if task is None:
            # No todo task remains → the phase produced its output. Apply the Miera autonómie dial at the
            # Programovanie schvaľovací bod (SHARED dial-settle, CR-V2-010, inherited here): auto-continue to
            # Verifikácia (``plna`` / fast_fix) or STOP ``awaiting_manazer`` for the Manažér to review. NO
            # Coordinator synthesis / build-ratify (retired — the dial governs the stop; design §2.2 / §2.3).
            if _settle_phase_boundary(db, state):
                return state  # agent_working at Verifikácia — the auto-chain loop continues the build
            if state.status != "done":
                state.status = "awaiting_manazer"
                state.next_action = "Manažér: posúdiť výsledok Programovania (Schváliť / Uprav)."
                db.flush()
            return state

        # Baseline BEFORE dispatch — captured once, immutable across the task's self-check re-attempts. A fresh
        # task anchors to repo HEAD now; a reclaimed (orphaned in_progress) task keeps its PERSISTED baseline_sha
        # so it re-runs against the SAME anchor, never a moved HEAD (never build on an unverified base). ORM
        # assignment keeps the in-memory object in sync so verify_mechanical gets the real baseline, not None.
        if task.baseline_sha is None:
            task.baseline_sha = _repo_head(project_root)
        if task.baseline_sha is None:
            # Fail-closed: repo HEAD unreadable → cannot anchor the diff → NEVER dispatch on an unknowable base.
            # The task STAYS todo (a precondition failure, not a failed attempt) so it auto-retries on resume
            # once HEAD is readable; surface to the Manažér DIRECTLY (no Coordinator relay — retired in v2).
            state.status = "awaiting_manazer"
            state.next_action = (
                f"Úloha #{task.number}: baseline nečitateľný (repo HEAD) — Manažér: oprav repo a pokračuj."
            )
            db.flush()
            return state
        task.status = "in_progress"
        db.flush()
        # Live current-task breadcrumb (CR-NS-025): the task is in_progress NOW, but the AI Agent's first
        # gate_report can be a long turn away — and TaskPlanPanel only refetches when messages.length changes.
        # Record + broadcast ONE task-start notification so the panel refetches immediately. Placed after the
        # fail-closed baseline guard so a never-dispatched task emits no "začal" breadcrumb.
        start_msg = _record_message(
            db,
            version_id=version_id,
            stage="programovanie",
            author="system",
            recipient="manazer",
            kind="notification",
            content=f"▶ Úloha #{task.number}: {task.title} — AI Agent začal.",
            payload={"task_id": str(task.id), "task_number": task.number, "phase": "programovanie"},
        )
        if on_message is not None:
            await on_message(start_msg)

        prior_failures: list[str] = []
        task_done = False
        for attempt in range(1, _SELF_CHECK_RETRIES + 1):
            if attempt == 1 and pending_directive is not None:
                prompt = pending_directive  # the Manažér's framed return/answer for the resumed task
                pending_directive = None  # consume once — later attempts/tasks use generated briefs
            else:
                prompt = _directive_for_build_task(task, cross_cutting, prior_failures, state.flow_type)
            result = await invoke_agent_with_parse_retry(
                db,
                version_id=version_id,
                role=AI_AGENT_ROLE,
                stage="programovanie",
                prompt=prompt,
                on_event=on_event,
                on_message=on_message,
                extra_payload={"task_id": str(task.id), "task_number": task.number, "attempt": attempt},
            )
            if isinstance(result, ParseFailure):
                if result.lost_work is not None:
                    # Lost-work audit (R-BLAST safeguard #3): the AI Agent's envelope was lost (timeout/crash)
                    # but the commit audit ran (inside invoke_agent). Work may have committed — surface "review
                    # & continue" DIRECTLY to the Manažér; the audit notification is already recorded. The task
                    # stays in_progress (reclaimed to todo on the next resume) — committed-but-lost work is
                    # surfaced, NEVER silently dropped or blindly redone.
                    state.status = "awaiting_manazer"
                    state.next_action = result.lost_work["next_action"]
                    db.flush()
                    return state
                prior_failures.append(f"neplatný status blok: {result.reason}")
            elif result.kind in ("question", "blocked"):
                # The AI Agent cannot proceed → it asks the Manažér DIRECTLY (no Coordinator relay — design
                # §2.2). Settle blocked with an agent_question reason so the board offers ``answer``; the
                # answer threads back into the resumed task on the next dispatch.
                state.status = "blocked"
                state.block_reason = "agent_question"
                state.next_action = f"AI Agent (úloha #{task.number}) sa pýta: {result.question}"
                db.flush()
                return state
            else:
                # A gate_report/done-class turn → the AI Agent self-checked + committed. The engine's per-task
                # gate is the DETERMINISTIC mechanical commit verify ONLY (no Auditor turn — design §2.2 / D5).
                mech = verify_mechanical(slug, result, task.baseline_sha)
                if mech is None:
                    db.execute(update(Task).where(Task.id == task.id).values(status="done"))
                    db.flush()
                    task_service.recompute_feat_status(db, task.feat_id)
                    # Factual per-task summary at the DONE settle — the AI Agent's own work summary + attempts
                    # (NO per-task audit verdict; CR-V2-012). `attempt` = the passing try.
                    await _record_task_summary(
                        db,
                        version_id,
                        task,
                        status="done",
                        attempts=attempt,
                        work_summary=result.summary,
                        attempt_errors=prior_failures,
                        on_message=on_message,
                    )
                    task_done = True
                    break
                prior_failures.append(mech)
            # failed this attempt (parse failure / mechanical-verify fail) → record a self-check return + bump
            # the feat's auto-fix counter; the reason threads into the next brief (escalating context).
            fail_metrics = _failure_metrics_payload(result)
            msg = _record_message(
                db,
                version_id=version_id,
                stage="programovanie",
                author="system",
                recipient=AI_AGENT_ROLE,
                kind="return",
                content=f"Self-check {attempt}/{_SELF_CHECK_RETRIES} (úloha #{task.number}): {prior_failures[-1]}",
                payload={
                    "verify_reason": prior_failures[-1],
                    "auto_fix_attempt": attempt,
                    "task_id": str(task.id),
                    "phase": "programovanie",  # per-turn phase stamp (CR-V2-009; CR-V2-029 metrics)
                    # WS-D: when this attempt's failure was a terminal ParseFailure (the AI Agent produced no
                    # message of its own), carry its tokens here so aggregate_pipeline_usage rolls them up.
                    **fail_metrics,
                },
            )
            if on_message is not None:
                await on_message(msg)
            db.execute(update(Feat).where(Feat.id == task.feat_id).values(auto_fix_count=Feat.auto_fix_count + 1))
            db.flush()

        if not task_done:  # self-check bound exhausted → task failed → STOP + surface to the Manažér directly
            db.execute(update(Task).where(Task.id == task.id).values(status="failed"))
            db.flush()
            task_service.recompute_feat_status(db, task.feat_id)
            # Factual per-task summary at the FAILED settle (all _SELF_CHECK_RETRIES tries used).
            await _record_task_summary(
                db,
                version_id,
                task,
                status="failed",
                attempts=_SELF_CHECK_RETRIES,
                work_summary=result.summary if isinstance(result, PipelineStatusBlock) else None,
                attempt_errors=prior_failures,
                on_message=on_message,
            )
            # No Coordinator relay (retired in v2) — settle ``awaiting_manazer`` DIRECTLY. The Manažér steers
            # the AI Agent (``uprav``) or re-runs; the AI Agent fixes (design §2.2, division of labour).
            state.status = "awaiting_manazer"
            state.next_action = (
                f"Úloha #{task.number} zlyhala po {_pokusy(_SELF_CHECK_RETRIES)} self-check — "
                "Manažér: usmerni AI Agenta (Uprav) alebo rozhodni o ďalšom kroku."
            )
            db.flush()
            return state
        # task done → continue the loop to the next todo task (no Manažér stop between successful tasks)


def _stage_order_for(flow_type: str) -> tuple[str, ...]:
    """The ordered phase path for a flow (CR-V2-009). ``fast_fix`` takes the shorter
    ``priprava → programovanie → verifikacia → done`` path (skips the heavy Návrh); ``new_version``
    walks the full 4-phase :data:`STAGE_ORDER`. (OQ-1: only these two flow_types survive — ``cr``/``bug``
    are dropped, so no third variant is needed.)"""
    return FAST_FIX_STAGE_ORDER if flow_type == "fast_fix" else STAGE_ORDER


def _next_stage(stage: str, flow_type: str = "new_version") -> str:
    """The phase that follows ``stage`` in this flow's path; clamps at the terminal ``done``."""
    order = _stage_order_for(flow_type)
    idx = order.index(stage)
    return order[min(idx + 1, len(order) - 1)]


async def apply_action(
    db: Session,
    *,
    version_id: uuid.UUID,
    action: str,
    payload: Optional[dict[str, Any]] = None,
) -> PipelineState:
    """Apply a Manažér action against the 4-phase build pipeline (v2 design §4.4; CR-V2-009).

    **SOLE-MUTATOR invariant (R-BLAST safeguard #1):** this is the ONLY function that mutates
    ``pipeline_state`` rows in response to a Manažér action. The dispatch path (``run_dispatch`` /
    ``_begin_dispatch``) mutates state too, but always as a CONSEQUENCE of an action routed here. No
    other code path writes ``current_stage`` / ``current_actor`` / ``status`` on a Manažér action.

    The 4 phases (priprava → navrh → programovanie → verifikacia → done) collapse the v1 11-stage
    waterfall. The action verbs (:data:`_ACTIONS`): ``start``, the always-mandatory ``approve_spec``
    end-Príprava stop, the dial-governed ``schvalit``/``uprav`` schvaľovacie body, ``pokracovat`` (resume
    a paused build), the Auditor ``verdict`` (PASS→Hotovo / FAIL→bounded AI-Agent fix loop), ``ask`` /
    ``answer`` direct comms, and ``pause``."""
    if action not in _ACTIONS:
        raise OrchestratorError(f"Unknown action: {action!r}")
    payload = payload or {}
    state = _get_state(db, version_id)

    if action == "start":
        if state is not None:
            raise OrchestratorError("Pipeline already started for this version")
        # OQ-1: only two flow_types survive — a full ``new_version`` (4-phase) or a ``fast_fix`` short path.
        flow_type = payload.get("flow_type", "new_version")
        if flow_type not in ("new_version", "fast_fix"):
            raise OrchestratorError(f"Invalid flow_type: {flow_type!r}")
        # Fast-fix lane (design §2.4): the Manažér's directive IS the whole brief — carry it in BOTH the
        # human-readable kickoff content (so it shows on the board) and the payload (so the Príprava round
        # can seed from it). ``None`` for new_version → the Príprava dialogue starts from the saved Zadanie.
        directive = payload.get("directive") if flow_type == "fast_fix" else None
        # "Spustiť tvorbu špecifikácie" (design §2.1): the kickoff message is recorded in the Príprava
        # phase — Príprava is the first phase the AI Agent enters. For new_version the content is generic;
        # for fast_fix it carries the directive so the kickoff brief is honoured.
        kickoff_content = directive if (flow_type == "fast_fix" and directive) else "Spustiť tvorbu špecifikácie."
        # Per-build Miera autonómie override (AUTON-6, CR-V2-008): an explicit ``miera_autonomie`` in the
        # start payload is persisted on the build as the TOP resolution layer (per-build → per-project →
        # global). Validated against the preset set; an unrecognised value degrades to inherit (NULL), it
        # never crashes the start. NULL (the default) inherits the per-project / global dial.
        per_build_dial = _normalize_miera_autonomie(payload.get("miera_autonomie"))
        state = PipelineState(
            version_id=version_id,
            flow_type=flow_type,
            current_stage="priprava",
            current_actor="ai_agent",
            status="agent_working",
            next_action="AI Agent pripravuje špecifikáciu.",
            miera_autonomie=per_build_dial,
        )
        db.add(state)
        db.flush()
        _record_message(
            db,
            version_id=version_id,
            stage="priprava",
            author="manazer",
            recipient="ai_agent",
            kind="kickoff",
            content=kickoff_content,
            payload={
                "flow_type": flow_type,
                "phase": "priprava",  # per-turn phase stamp (CR-V2-009; consumed by CR-V2-029 metrics)
                **({"directive": directive} if directive else {}),
            },
        )
        # WS-B1 (CR-NS-029): a fresh ``start`` resets every agent session — drop the project's
        # OrchestratorSession rows so no stale cross-version --resume context leaks in. A verdict FAIL
        # re-loop (below) PRESERVES sessions: it mutates existing state and never reaches this branch
        # (gated on ``state is None``), so only a genuine kickoff resets.
        db.execute(
            delete(OrchestratorSession).where(
                OrchestratorSession.project_slug == _project_slug_for_version(db, version_id)
            )
        )
        db.flush()
        _begin_dispatch(db, state)
        return state

    if state is None:
        raise OrchestratorError("Pipeline not started for this version")

    # Status guard (CR-NS-018): never act on / advance past an agent that is still working. The advancing
    # actions need a SETTLED agent (awaiting_manazer or a blocked ratify-out-of-a-question); answer needs
    # an actual question (blocked); pause is only meaningful while the agent works. 'paused' (CR-NS-027)
    # is a settled, Manažér-actionable state — the Programovanie loop stopped at a task boundary — so the
    # advancing-action guard lets it through (``pokracovat`` is advancing); the paused guard below
    # restricts WHICH actions are valid from there.
    if action in _ADVANCING_ACTIONS and state.status not in ("awaiting_manazer", "blocked", "paused"):
        raise OrchestratorError("Agent ešte pracuje — počkaj na jeho výstup")
    if action == "answer" and state.status != "blocked":
        raise OrchestratorError("Agent sa na nič nepýta — odpoveď nie je na mieste")
    if action == "pause" and state.status != "agent_working":
        raise OrchestratorError("Pauza je možná len počas práce agenta")
    # Pause is Programovanie-only (CR-NS-027 decision A): only the coding loop has a cooperative task
    # boundary to stop at — a single-turn phase has no boundary, so a pause there would be a silent no-op.
    if action == "pause" and state.current_stage != "programovanie":
        raise OrchestratorError("Pauza je možná len počas fázy Programovanie")
    # From 'paused' (CR-NS-027) only the resume verb (``pokracovat``) or a steer (``uprav``) is valid:
    # everything else must NOT silently un-pause. In particular ``ask`` is not advancing, so without this
    # guard it would fall through to its handler, call _begin_dispatch and flip the status back to
    # agent_working. The Manažér resumes deliberately, never as a side effect of asking/answering.
    if state.status == "paused" and action not in ("pokracovat", "uprav"):
        raise OrchestratorError("Build je pozastavený — pokračuj cez 'Pokračovať' alebo ho usmerni (Uprav).")
    # Durable single-flight dispatch guard (R-BLAST safeguard #2; R1-b / D2, CR-NS-027 hardening):
    # refuse to start a SECOND agent turn while a dispatch is already in flight for this version. The DB
    # flag survives a backend restart (unlike the in-memory ``_ACTIVE_DISPATCH``), and the settle listener
    # clears it the moment the dispatch ends — so in the normal flow this only fires for a genuine
    # in-flight overlap (a stale flag a restart left set before orphan recovery, or a double-submit).
    # ``pause`` is the one exception: it stops the running build loop, it never dispatches.
    if state.dispatch_in_flight and action != "pause":
        raise OrchestratorError("Dispečer už beží pre túto verziu")

    if action == "approve_spec":
        # End-Príprava: the ALWAYS-mandatory Špecifikácia approval (design §2.3, D3 — dial-INDEPENDENT, it
        # fires at every autonomy level including ``plna``). Advances Príprava → Návrh. Only valid in
        # Príprava; the Manažér has read the Špecifikácia in the Príprava tab and signs it off.
        if state.current_stage != "priprava":
            raise OrchestratorError("Schváliť špecifikáciu je platné len vo fáze Príprava")
        _record_message(
            db,
            version_id=version_id,
            stage="priprava",
            author="manazer",
            recipient="ai_agent",
            kind="approval",
            content=payload.get("comment", "Špecifikácia schválená."),
            payload={"phase": "priprava", "approve_spec": True},
        )
        state.current_stage = _next_stage("priprava", state.flow_type)  # new_version → navrh; fast_fix → programovanie
        db.flush()
        _begin_dispatch(db, state)
        return state

    if action == "schvalit":
        # "Schváliť" — the Manažér ratifies the current phase's output at a dial-governed schvaľovací bod
        # (after Návrh / Programovanie / Verifikácia) → advance to the next phase / Hotovo. The dial decides
        # whether the build STOPPED here for the Manažér at all; once it has, this signs it off.
        if state.current_stage not in ("navrh", "programovanie", "verifikacia"):
            raise OrchestratorError("Schváliť je platné len na schvaľovacom bode (Návrh / Programovanie / Verifikácia)")
        # no-silent-done-without-verification (R-BLAST safeguard #5, v2 form): the build may reach Hotovo
        # ONLY through a recorded Auditor PASS verdict at Verifikácia — never a silent sign-off. (v1's
        # "no-silent-done-without-UAT" gate is superseded: deploy is OUT of the pipeline — per-customer,
        # D6/OQ-3 — so Hotovo means "verified", not "deployed". The verification invariant is preserved.)
        if state.current_stage == "verifikacia" and not _verifikacia_passed(db, version_id):
            raise OrchestratorError(
                "Hotovo nedovolené: Auditor ešte nevydal PASS vo Verifikácii — najprv over verdiktom PASS."
            )
        _record_message(
            db,
            version_id=version_id,
            stage=state.current_stage,
            author="manazer",
            recipient=state.current_actor,
            kind="approval",
            content=payload.get("comment", "Schválené."),
            payload={"phase": state.current_stage},
        )
        state.current_stage = _next_stage(state.current_stage, state.flow_type)
        db.flush()
        if state.current_stage == "done":
            state.current_actor = "ai_agent"  # terminal — no agent on turn; kept a valid ACTOR value
            state.status = "done"
            state.next_action = "Pipeline dokončená (Hotovo). Nasadenie je samostatná akcia per zákazník."
            db.flush()
        else:
            _begin_dispatch(db, state)
        return state

    if action == "uprav":
        # "Uprav" — the Manažér's correction back to the AI Agent at a schvaľovací bod (re-work the current
        # phase) OR the error-block recovery ("Skús znova") at any settled phase. The phase does NOT
        # advance; the AI Agent re-runs with the Manažér's comment threaded into its brief (direct comms —
        # the Coordinator relay is retired, design §2.2). A comment is REQUIRED so the agent has guidance.
        comment = payload.get("comment")
        if not comment or not str(comment).strip():
            raise OrchestratorError("uprav requires a non-empty payload.comment")
        # A paused Programovanie loop steered by ``uprav`` resumes from the pause (re-dispatch the loop).
        recipient = state.current_actor
        _record_message(
            db,
            version_id=version_id,
            stage=state.current_stage,
            author="manazer",
            recipient=recipient,
            kind="return",
            content=str(comment),
            payload={"phase": state.current_stage},
        )
        _begin_dispatch(db, state)
        return state

    if action == "ask":
        # Direct Manažér → AI Agent / Auditor consult (design §2.2 — no Coordinator relay): the Manažér's
        # question is threaded into the current actor's next turn. The phase does NOT advance.
        text = payload.get("text")
        if not text or not str(text).strip():
            raise OrchestratorError("ask requires a non-empty payload.text")
        _record_message(
            db,
            version_id=version_id,
            stage=state.current_stage,
            author="manazer",
            recipient=state.current_actor,
            kind="question",
            content=str(text),
            payload={"phase": state.current_stage},
        )
        _begin_dispatch(db, state)
        return state

    if action == "answer":
        # The Manažér answers the agent's blocked question (block_reason=agent_question) — threaded into the
        # resumed turn. The status guard above already required ``blocked``.
        text = payload.get("text")
        if not text or not str(text).strip():
            raise OrchestratorError("answer requires a non-empty payload.text")
        _record_message(
            db,
            version_id=version_id,
            stage=state.current_stage,
            author="manazer",
            recipient=state.current_actor,
            kind="answer",
            content=str(text),
            payload={"phase": state.current_stage},
        )
        _begin_dispatch(db, state)
        return state

    if action == "verdict":
        # The Auditor's Verifikácia verdict (design §2.2 (b)). Only valid at Verifikácia. PASS → settle for
        # the Manažér's end sign-off (``schvalit`` → Hotovo); FAIL → loop the fix back to the AI Agent (the
        # Auditor finds, the AI Agent fixes — §2.2 "Division of labour"), bounded by :data:`AUDITOR_LOOP_MAX`
        # fix↔re-verify rounds, then STOP and escalate to the Manažér (§2.2 (i)). The verdict is the Manažér's
        # ratification of the Auditor's finding (or, autonomously, the engine's at a non-stopping dial level).
        if state.current_stage != "verifikacia":
            raise OrchestratorError("verdict je platné len vo fáze Verifikácia")
        verdict = payload.get("verdict")
        if verdict not in ("PASS", "FAIL"):
            raise OrchestratorError("verdict requires payload.verdict in {PASS, FAIL}")
        _record_message(
            db,
            version_id=version_id,
            stage="verifikacia",
            author="auditor",
            recipient="manazer",
            kind="verdict",
            content=verdict,
            payload={"verdict": verdict, "phase": "verifikacia"},
        )
        if verdict == "PASS":
            # Verified. SETTLE at Verifikácia awaiting the Manažér's end sign-off (``schvalit`` → Hotovo) —
            # the dial-governed end schvaľovací bod. The phase does NOT auto-advance to Hotovo here: whether
            # the build stops for the Manažér or the engine auto-signs-off (``plna``) is the dial's call,
            # applied in the dispatch path (CR-V2-014). Keeping the PASS-then-sign-off split preserves the
            # no-silent-done invariant — Hotovo is only ever reached through this recorded PASS verdict.
            state.status = "awaiting_manazer"
            state.next_action = "Verifikácia PASS — schváľ na Hotovo (nasadenie je samostatná akcia per zákazník)."
            db.flush()
            return state
        # FAIL → bounded fix loop. ``iteration`` counts the fix↔re-verify rounds the Auditor has driven.
        if state.iteration >= AUDITOR_LOOP_MAX:
            # Exhausted the bounded loop → STOP + escalate to the Manažér (§2.2 (i)). Settle to blocked with
            # an agent_error reason so the board surfaces it; the Manažér steers via ``uprav`` or re-runs.
            state.status = "blocked"
            state.block_reason = "agent_error"
            state.next_action = (
                f"Auditor po {AUDITOR_LOOP_MAX} kolách stále FAIL — eskalované Manažérovi. "
                "Usmerni opravu (Uprav) alebo rozhodni o ďalšom kroku."
            )
            db.flush()
            _record_message(
                db,
                version_id=version_id,
                stage="verifikacia",
                author="system",
                recipient="manazer",
                kind="notification",
                content=(
                    f"Verifikácia zlyhala {AUDITOR_LOOP_MAX}× — bounded fix-loop vyčerpaný, eskalované Manažérovi."
                ),
                payload={"phase": "verifikacia", "auditor_loop_exhausted": True},
            )
            return state
        # Loop the fix back to the AI Agent: re-enter Programovanie (the AI Agent fixes), bump the round
        # counter, preserve sessions (warm context — never reset mid-loop). The Auditor re-verifies on the
        # next Verifikácia turn. ``is_regate`` marks the re-loop for the dispatch path.
        state.is_regate = True
        state.iteration += 1
        state.current_stage = "programovanie"
        db.flush()
        _begin_dispatch(db, state)
        return state

    if action == "pokracovat":
        # Resume a Programovanie loop the Manažér paused (cooperative pause boundary) — no comment, no phase
        # change: just re-dispatch the loop (it re-picks the next todo task). The record is Manažér→AI Agent
        # (direct comms). Only valid in Programovanie (the only phase with a pause boundary).
        if state.current_stage != "programovanie":
            raise OrchestratorError("Pokračovať je platné len vo fáze Programovanie")
        _record_message(
            db,
            version_id=version_id,
            stage="programovanie",
            author="manazer",
            recipient="ai_agent",
            kind="approval",
            content="Build pokračuje.",
            payload={"phase": "programovanie"},
        )
        _begin_dispatch(db, state)  # phase stays programovanie; status → agent_working
        return state

    # action == "pause" (CR-NS-027): a genuine paused status, not just a label. The running Programovanie
    # loop re-reads state at its next task boundary (db.refresh, READ COMMITTED) and, seeing a status other
    # than agent_working, settles + stops cleanly — the current task finishes, no mid-task kill. Leaving
    # agent_working also stops the action route from re-dispatching (the no-op-pause bug that spawned a 2nd
    # loop). Resume via ``pokracovat``.
    state.status = "paused"
    state.next_action = "Pozastavené Manažérom — pokračuj cez 'Pokračovať'."
    db.flush()
    return state
