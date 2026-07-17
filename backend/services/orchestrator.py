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
from urllib.parse import urlsplit, urlunsplit

import yaml
from pydantic import ValidationError
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.db.models.backlog import BacklogItem
from backend.db.models.foundation import User, UserAgentSettings
from backend.db.models.orchestrator import OrchestratorSession
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version
from backend.schemas.epic import EpicCreate
from backend.schemas.feat import FeatCreate
from backend.schemas.task import TaskCreate
from backend.services import claude_agent, dedo_escalation, failure_framing, fast_fix, uat_provisioner
from backend.services import epic as epic_service
from backend.services import feat as feat_service
from backend.services import system_setting as system_setting_service
from backend.services import task as task_service
from backend.services.claude_agent import ClaudeAgentError, ClaudeAgentTimeout, invoke_claude
from backend.services.pipeline_metrics import aggregate_pipeline_usage
from backend.services.pipeline_status import (
    FIX_CRITIQUE_JSON_SCHEMA,
    PIPELINE_STATUS_JSON_SCHEMA,
    TASK_PLAN_FEAT_TASKS_JSON_SCHEMA,
    TASK_PLAN_SKELETON_JSON_SCHEMA,
    ConsultationBlock,
    ConsultDecision,
    ConsultOption,
    FixCritique,
    ParseFailure,
    PipelineStatusBlock,
    TaskPlan,
    TaskPlanEpic,
    TaskPlanFeat,
    extract_report_body,
    extract_task_plan_json,
    parse_fix_critique,
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

#: Read-only tool profile for a Konzultácia turn (konzultacia-mode.md Part 1). Passed to
#: :func:`invoke_agent` → :func:`claude_agent.invoke_claude` so a consult on a finished version can ONLY
#: read the project (Read/Grep/Glob) — every mutating/exec/spawn tool is hard-denied there. The
#: non-mutating guarantee is by TOOL PROFILE (the absence of any write tool), not by prompt promise.
CONSULT_READ_ONLY_TOOLS: list[str] = ["Read", "Grep", "Glob"]

#: Default model for BOTH v2 agents when the project owner has no explicit per-role pick in Nastavenia
#: (CR-V2-028). Both the AI Agent (the doer) and the Auditor (the verifier) are strong roles that own /
#: verify the whole build, so the unconfigured default must be the strongest model — NOT the CLI's own
#: default (which is a small/fast model). A per-user ``user_agent_settings`` row still overrides this.
DEFAULT_AGENT_MODEL = "claude-opus-4-8"

#: Default model the AI Agent spawns its ephemeral HELPERS on (Agent/Task tool) when the owner has set no
#: explicit ``helper_model`` (CR-V2-038). Haiku by design: the AI Agent does the hard CORE itself on its own
#: (Opus + max) turn and delegates only parallel/bulk grunt work to helpers — cheap + fast is the right
#: default there. The Manažér can raise it to Opus per project (Nastavenia) for a high-stakes build.
DEFAULT_HELPER_MODEL = "claude-haiku-4-5-20251001"

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


# ── v2.0.0 single-writer guard (CR-V2-015 / SPIKE-IO Model B) ──────────────────────────────────────
# SPIKE-IO confirmed Model B: the ENGINE is the SOLE writer to each warm ``claude`` session — it drives
# every turn via ``invoke_claude(... --resume <claude_session_id>)`` (the proven headless primitive), and
# a Manažér message typed in the AI Agent tab is NOT keystroked into the CLI — it is RELAYED by the engine
# as the next ``-p --resume`` turn (the ``answer``/``ask``/``uprav`` directive path). One UUID, one writer.
#
# The chief residual two-writer hazard (SPIKE-IO Risk (a)): the legacy ``/debug-terminal`` break-glass
# spawns a SEPARATE PTY that ``--resume``s the SAME ``claude_session_id`` and whose ``write_input`` feeds
# keystrokes straight to the CLI — an independent second writer that corrupts session memory if used while
# the engine drives. This registry is the enforcement primitive: every engine turn registers the
# ``claude_session_id`` it is about to drive for the duration of the ``invoke_claude`` call;
# :func:`is_session_engine_busy` lets the PTY layer (``agent_terminal.write_input``) REFUSE a concurrent
# write to a session the engine owns. The registry is keyed by ``claude_session_id`` (the actual write
# target — both writers contend for the same CLI session UUID, not the per-version pipeline_state), is a
# plain ``set`` mutated only on the single-threaded asyncio loop (no lock needed, same invariant as the
# runner's ``_ACTIVE_DISPATCH``), and counts re-entrant registrations so a turn that spans parse-retries
# (which re-enter :func:`invoke_agent`) stays "busy" across the whole logical turn.
_ENGINE_ACTIVE_SESSIONS: dict[uuid.UUID, int] = {}


def is_session_engine_busy(claude_session_id: uuid.UUID) -> bool:
    """True iff an engine turn is currently driving ``claude_session_id`` (CR-V2-015 single-writer guard).

    The PTY layer (:func:`agent_terminal.write_input`) calls this to REFUSE a concurrent keystroke write
    to a warm session the engine owns — the break-glass debug-attach PTY must never become a second writer
    mid-turn (SPIKE-IO Risk (a)). Sole-writer-by-construction: only the engine ever registers here."""
    return _ENGINE_ACTIVE_SESSIONS.get(claude_session_id, 0) > 0


@contextlib.contextmanager
def _engine_session_active(claude_session_id: uuid.UUID):
    """Mark ``claude_session_id`` as engine-busy for the duration of one ``invoke_claude`` turn.

    Re-entrant (a turn spanning parse-retries re-enters :func:`invoke_agent`); the count is decremented on
    exit and the key removed at zero. Synchronous context manager around the ``await invoke_claude`` so the
    busy window is exactly the live CLI write (the PTY guard reads it on the same loop)."""
    _ENGINE_ACTIVE_SESSIONS[claude_session_id] = _ENGINE_ACTIVE_SESSIONS.get(claude_session_id, 0) + 1
    try:
        yield
    finally:
        remaining = _ENGINE_ACTIVE_SESSIONS.get(claude_session_id, 0) - 1
        if remaining > 0:
            _ENGINE_ACTIVE_SESSIONS[claude_session_id] = remaining
        else:
            _ENGINE_ACTIVE_SESSIONS.pop(claude_session_id, None)


# ── v2.0.0 Manažér→AI-Agent relay queue (CR-V2-015 / SPIKE-IO point (1)) ───────────────────────────
# SPIKE-IO point (1): "a per-(project, role) single serialized inbound queue in the orchestrator that
# serializes Manažér messages + autonomous turns into the single ``invoke_agent``→``invoke_claude`` call
# site (the single-writer enforcer)." A Manažér message typed in the read-only AI Agent tab is NOT a
# keystroke — it is ENQUEUED here and RELAYED by the engine as the next ``-p --resume`` turn. The queue is
# keyed per VERSION (the build is the relay unit; the actor is resolved at drain time). It is a plain dict
# of FIFO lists mutated only on the single asyncio loop (lock-free, same invariant as the runner's
# ``_ACTIVE_DISPATCH``). Two arrival cases:
#   * the build is SETTLED (no turn in flight) → :func:`relay_manazer_message` dispatches the message
#     immediately as an ``ask``/``answer`` turn (it never enqueues — there is nothing to wait for).
#   * a turn is IN FLIGHT (``dispatch_in_flight``) → the message is ENQUEUED; the runner drains it as the
#     next turn AFTER the current dispatch (incl. its auto-chain) settles, so a relayed turn and an
#     autonomous turn can never invoke ``invoke_claude`` concurrently on the same session UUID.
_RELAY_QUEUES: dict[uuid.UUID, list[str]] = {}


def _enqueue_relay(version_id: uuid.UUID, text: str) -> None:
    """Append a Manažér relay message to ``version_id``'s FIFO inbound queue (engine drains it next turn)."""
    _RELAY_QUEUES.setdefault(version_id, []).append(text)


def pop_relay_message(version_id: uuid.UUID) -> Optional[str]:
    """Pop the oldest queued Manažér relay message for ``version_id`` (FIFO), or ``None`` if empty.

    Drained by the runner after a dispatch settles so a relayed message becomes the NEXT engine turn —
    never a concurrent writer (CR-V2-015)."""
    queue = _RELAY_QUEUES.get(version_id)
    if not queue:
        return None
    text = queue.pop(0)
    if not queue:
        _RELAY_QUEUES.pop(version_id, None)
    return text


def has_pending_relay(version_id: uuid.UUID) -> bool:
    """True iff ``version_id`` has a queued Manažér relay message awaiting the next turn (CR-V2-015)."""
    return bool(_RELAY_QUEUES.get(version_id))


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
    # CR-1 (nex-studio-visual): live-preview "Vizuál" phase between Návrh and Programovanie (spec §3.A) —
    # the AI-built FE renders live for the Manažér to walk + approve before the coding phase.
    "vizual",
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
    # CR-1: the Vizuál phase is AI-Agent-owned (the doer spins the live preview / applies FE changes).
    "vizual": "ai_agent",
    "programovanie": "ai_agent",
    "verifikacia": "auditor",
}
# Auditor fix-loop bound (v2 design §2.2 "Division of labour"; CR-V2-009). At Verifikácia, an Auditor FAIL
# verdict loops the fix back to the AI Agent (the Auditor only finds; the AI Agent fixes), the Auditor
# re-verifies, bounded to this many fix↔re-verify rounds; on the (n+1)-th still-failing round the build
# STOPS and escalates to the Manažér (design §2.2 (i)). The named constant the runner's auto-chain backstop
# budgets (R-AUTOCHAIN, finalized CR-V2-014): :func:`auto_chain_limit` adds ``2 * AUDITOR_LOOP_MAX`` so a
# legit 5-round Auditor loop never mis-trips the backstop. Driven by :func:`_settle_verifikacia_verdict`.
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
# CR-V2-029: minimum wall-clock (seconds) that must remain in the per-turn budget before a parse-retry
# is started. The whole turn (primary + re-emits) shares ONE budget; if less than this is left we stop
# rather than launch a re-emit we can't finish (which previously let a turn run 3×900s = 45 min).
_MIN_RETRY_BUDGET_S = 60
# CR-V2-029: max length of the agent's raw-output excerpt carried on a ParseFailure + shown in the
# parse-exhaustion notification (enough to diagnose a malformed status block, bounded so the message /
# payload stays sane).
_RAW_EXCERPT_LEN = 4000
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
        # CR-V2-041: the Manažér picks an option for ONE consultation decision (Decision Card). Like
        # ``answer`` it threads input + does not advance the phase; the LAST decide re-dispatches the apply.
        "decide",
        # CR-V2-057: "Over znova" — re-verify a DRIFTED version against the CURRENT code. Valid ONLY when the
        # recorded Verifikácia PASS is stale (:func:`version_verified` == ``sha_drift`` — HEAD moved past the
        # verified commit). Re-enters Verifikácia and re-runs the independent Auditor against HEAD; the fresh
        # verdict re-anchors (PASS bound to the new commit → drift gone) or re-gates (FAIL → targeted fix).
        "overit_znovu",
        # STEP 3 (step3-plan-design.md MD-1=A): "Zostaviť plán" — in a conversation build, AFTER the
        # Špecifikácia is approved, compose the task plan (EPIC→FEAT→TASK) from the frozen Špecifikácia.
        # Honest-by-construction like ``approve_spec`` (NOT advancing — it stays in the conversation register,
        # no phase walk); the board post-filters it to conversation + spec-approved + plan-not-materialized.
        "zostav_plan",
        # STEP 4 (step4-programovanie-design.md MD-A=A): "Spustiť stavbu" — in a conversation build, AFTER the
        # task plan is materialized, MOVE ``current_stage`` priprava→programovanie (mode stays 'conversation')
        # and dispatch the EXISTING ``_run_build_round`` self-checking loop VERBATIM (routed by stage). NOT
        # advancing (it stays in the conversation register — the completion tail returns to priprava, no phase
        # walk); the board post-filters it to conversation + spec-approved + plan-materialized + NOT build-started.
        "spustit_stavbu",
        # CR-1 (nex-studio-visual): "Spustiť vizuál" — in a conversation build, AFTER the task plan is
        # materialized (same window as ``spustit_stavbu``), MOVE ``current_stage`` priprava→vizual (mode stays
        # 'conversation') and dispatch the EXISTING ``_run_vizual_round`` live-preview loop VERBATIM (routed by
        # stage). The FRESH entry spins up the isolated Vite dev-server sandbox and hands the Manažér the preview
        # URL to WALK; later relayed chat messages carry the change-requests the AI applies (HMR). NOT advancing
        # (it stays in the conversation register — no phase walk); the board post-filters it to conversation +
        # spec-approved + plan-materialized + NOT build-started + NOT already-in-vizual.
        "spustit_vizual",
        # STEP 5 (step5-kontrola-design.md K-1=A): "Skontrolovať" — in a conversation build, AFTER Programovanie
        # completes, run the partner's HONEST self-check (real boot + acceptance + spec reconciliation) that
        # STAYS at ``current_stage='priprava'`` and emits ONE ``kind='gate_report'`` (NEVER a verdict). NOT
        # advancing (it never walks the phase automaton — kontrola signs nothing, deploys nothing; it must stay
        # INVISIBLE to the release/deploy path); the board post-filters it to conversation + spec-approved +
        # programming-complete + NOT already-checked.
        "skontrolovat",
        # STEP 6 (step6-hotovo-design.md MD-1=A): "Označiť ako hotové" — the Manažér's TERMINAL sign-off on a
        # conversation build AFTER Kontrola: it settles the build to the terminal ``done`` (verified) stage and
        # records a SHA-anchored manager signature that ``version_verified`` honours → the version becomes
        # DEPLOYABLE. This signature REPLACES an Auditor verdict for conversation builds (no verdict is
        # resurrected). NOT advancing (it is a terminal signature, not a phase-walk — kept out of
        # ``_ADVANCING_ACTIONS`` below); the board post-filters it to conversation + spec-approved +
        # kontrola-done + NOT already-done.
        "hotovo",
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
    # CR-1: the Vizuál round mirrors Návrh's budget (read + produce a live FE preview / apply FE edits).
    "vizual": 1200,
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

    # Director observation #6: a ``framework_issue`` block is an escalation to our technical team — the fix
    # needs a change to NEX Studio ITSELF, which the Manažér objectively CANNOT do (no Uprav / answer / decide
    # — nothing the Manažér can act on). But a pure empty set is a jargon-free dead-end for a non-expert (audit
    # P0): offer the ONE action they DO have — ``nahlasit_znova`` (re-send the report) — so they have agency and
    # a concrete button instead of a locked screen. MUST precede the universal ask+uprav defaults below.
    if status == "blocked" and state.block_reason == "framework_issue":
        return {"nahlasit_znova"}

    # CR-V2-041: a multi-decision CONSULTATION blocks with block_reason="decision_needed" — the Manažér
    # resolves it via Decision Cards (``decide``), one decision at a time, NEVER the raw free-text
    # answer/uprav box (a non-expert must not face a blank box). ``ask`` stays so the Manažér can probe a
    # card before deciding; the card owns the action.
    if status == "blocked" and state.block_reason == "decision_needed":
        return {"decide", "ask"}

    # Settled (awaiting_manazer / blocked): ask + uprav are universally valid — ``uprav`` doubles as the
    # error-block "Skús znova" / re-work recovery at any phase, and ``ask`` opens a direct AI-Agent
    # consult. A blocked state is an agent QUESTION → the Manažér can ``answer`` it.
    actions: set[str] = {"ask", "uprav"}
    if status == "blocked":
        # A blocked state (agent_question / agent_error / system_error / parse_exhaustion — framework_issue and
        # decision_needed already returned above) is a QUESTION or an ERROR the Manažér must answer / recover
        # from (``answer`` / ``uprav`` = "Skús znova"). The phase-ADVANCE verbs below are NOT offered: advancing
        # past an unresolved error/question is a footgun (e.g. "Schváliť špecifikáciu" appearing right after a
        # parse failure — the audit's Theme 1). Only the settled ``awaiting_manazer`` path offers the advance body.
        actions.add("answer")
        return actions

    # Settled (awaiting_manazer): the phase-advance schvaľovacie body.
    if stage == "priprava":
        # End-Príprava: the ALWAYS-mandatory Špecifikácia approval (dial-independent, design §2.3/D3).
        actions.add("approve_spec")
        # STEP 3 (step3-plan-design.md FIX2): "Zostaviť plán" — offered UNCONDITIONALLY here (state-only, like
        # ``schvalit`` at Návrh). The finer DB preconditions (conversation build + spec approved + plan not
        # yet materialized) are the board route's POST-FILTER; ``apply_action`` enforces them authoritatively.
        actions.add("zostav_plan")
        # STEP 4 (step4-programovanie-design.md MD-A): "Spustiť stavbu" — offered UNCONDITIONALLY here too
        # (state-only). The finer DB preconditions (conversation + spec approved + plan materialized + NOT
        # build-started) are the board route's POST-FILTER; ``apply_action`` enforces them authoritatively.
        actions.add("spustit_stavbu")
        # CR-1 (nex-studio-visual): "Spustiť vizuál" — offered UNCONDITIONALLY here too (state-only), the same
        # window as ``spustit_stavbu``. The finer DB preconditions (conversation + spec approved + plan
        # materialized + NOT build-started + NOT already-in-vizual) are the board route's POST-FILTER;
        # ``apply_action`` enforces them authoritatively.
        actions.add("spustit_vizual")
        # STEP 5 (step5-kontrola-design.md K-1): "Skontrolovať" — offered UNCONDITIONALLY here too (state-only).
        # The finer DB preconditions (conversation + spec approved + programming complete + NOT already-checked)
        # are the board route's POST-FILTER; ``apply_action`` enforces them authoritatively.
        actions.add("skontrolovat")
        # STEP 6 (step6-hotovo-design.md MD-1): "Označiť ako hotové" — offered UNCONDITIONALLY here too
        # (state-only). The finer DB preconditions (conversation + spec approved + kontrola done + NOT already
        # done) are the board route's POST-FILTER; ``apply_action`` enforces them authoritatively.
        actions.add("hotovo")
    elif stage in ("navrh", "vizual", "programovanie"):
        # The schvaľovacie body after Návrh / Vizuál (CR-1) / Programovanie — ``schvalit`` advances to the
        # next phase. (Whether the build HALTED here at all is the dial's call; once settled, it's offered.)
        actions.add("schvalit")
    elif stage == "verifikacia":
        # Verifikácia is the Auditor's phase: the Manažér ratifies the Auditor's verdict (``verdict``) and,
        # at the dial-governed end stop, signs off with ``schvalit`` → Hotovo.
        actions.update({"verdict", "schvalit"})

    # CR-1 (nex-studio-visual): a CONVERSATION build sitting AT the ``vizual`` stage still needs the
    # build-launch verb — a conversation build's ``schvalit`` is DROPPED by the board post-filter (it never
    # walks the phase automaton), so ``spustit_stavbu`` is the ONLY path from Vizuál → Programovanie. Offer it
    # here too (+ ``spustit_vizual``, which its OWN post-filter hides once ``current_stage == 'vizual'`` so a
    # re-click can't re-enter the stage). Both are POST-FILTERED authoritatively by the board route (conversation
    # + spec approved + plan materialized + NOT build-started); ``apply_action`` enforces the same gates. For a
    # phase-automaton (mode NULL) build at ``vizual`` both are dropped by the conversation-only post-filter, so
    # this leaves the guided-build board (which offers ``schvalit`` above) unchanged.
    if stage == "vizual":
        actions.add("spustit_stavbu")
        actions.add("spustit_vizual")

    return actions


def build_readiness(db: Session, version_id: uuid.UUID) -> tuple[bool, int]:
    """``(all_tasks_done, open_findings)`` for the build stage (WS-C1, CR-NS-030).

    ``determine_available_actions`` is state-only, so it cannot gate the DB-dependent build
    preconditions: approve@build is rejected while any task is ``todo`` (build not finished) or any is
    ``failed``/unverified (open finding); end_build is rejected while a finding is open. The board
    exposes these two facts so the FE can DISABLE "Schváliť build → Audit" / "Ukončiť build" when not
    satisfiable — mirroring the existing Gate E ``gate_e_open_findings`` gate — instead of offering a
    button that 400s. Cheap counts; the board computes them each fetch like ``_gate_e_open_findings``."""
    # Audit P2 (2026-07-12): "no todo task remains" is NOT "the build is complete" when NO tasks exist at all
    # (an empty / not-started plan) — that would read as "complete / 0 findings" before any work landed. Only
    # call it done when a task EXISTS and none is still todo.
    no_todo = task_service.get_next_todo_task(db, version_id) is None
    has_any_task = (
        db.execute(
            select(func.count())
            .select_from(Task)
            .join(Feat, Feat.id == Task.feat_id)
            .join(Epic, Epic.id == Feat.epic_id)
            .where(Epic.version_id == version_id)
        ).scalar_one()
        > 0
    )
    return (no_todo and has_any_task), _build_open_findings(db, version_id)


def navrh_plan_materialized(db: Session, version_id: uuid.UUID) -> bool:
    """True iff a task plan has actually landed for this version — at least one ``Task`` exists (CR-V2-037).

    The Návrh task plan is written ALL-OR-NOTHING (CR-V2-011 ``_run_navrh_round``): the EPIC→FEAT→TASK rows
    appear only after every per-feat pass succeeds. So 0 tasks means the plan never materialized — e.g. a
    per-feat pass crashed past its bounded re-invokes and the round settled ``awaiting_manazer`` with an
    empty plan. Approving ``schvalit`` out of Návrh then would advance to Programovanie with NOTHING to
    build. This guards that gate (apply_action) and lets the board hide a dead "Schváliť" button. Note
    ``build_readiness``'s ``all_tasks_done`` is True for an empty plan (no todo task), so it must NOT be
    reused as the plan-present signal — this is a positive existence count."""
    return (
        db.execute(
            select(Task.id)
            .join(Feat, Task.feat_id == Feat.id)
            .join(Epic, Feat.epic_id == Epic.id)
            .where(Epic.version_id == version_id)
            .limit(1)
        ).first()
        is not None
    )


def _build_started(db: Session, version_id: uuid.UUID) -> bool:
    """True iff the Programovanie build has BEGUN for this version — at least one ``Task`` has advanced past
    ``todo`` (``in_progress`` / ``done`` / ``failed``) (STEP 4, step4-programovanie-design.md MD-A).

    Distinguishes a freshly materialized plan (every Task ``todo`` → "Spustiť stavbu" is still offerable)
    from a build already in flight or complete (some Task moved → the trigger is spent; the Manažér resumes
    a paused/interrupted build via "Pokračovať", never a second "Spustiť stavbu"). Positive existence count,
    the mirror of :func:`navrh_plan_materialized`; gates the ``spustit_stavbu`` board post-filter AND the
    authoritative ``apply_action`` guard so a stale board / forged call can never re-kick a running build."""
    return (
        db.execute(
            select(Task.id)
            .join(Feat, Task.feat_id == Feat.id)
            .join(Epic, Feat.epic_id == Epic.id)
            .where(Epic.version_id == version_id, Task.status != "todo")
            .limit(1)
        ).first()
        is not None
    )


def _latest_programming_complete_seq(db: Session, version_id: uuid.UUID) -> Optional[int]:
    """The ``seq`` of the LATEST Programovanie-complete notification for this version, or ``None`` when the
    build has never completed (STEP 5, step5-kontrola-design.md).

    The MD-B completion tail (:func:`_run_build_round`) records exactly ONE ``stage='programovanie'`` ∧
    ``kind='notification'`` ∧ ``payload.programming_complete`` message per finished conversation build; a new
    build / fix records a FRESHER one (higher ``seq``). Returning the seq (not just a bool) lets
    :func:`kontrola_done` decide whether a kontrola gate_report is NEWER than the build it checked, so a
    re-built version re-opens the check. Matches the ``.astext == 'true'`` JSONB style used elsewhere."""
    return db.execute(
        select(func.max(PipelineMessage.seq)).where(
            PipelineMessage.version_id == version_id,
            PipelineMessage.stage == "programovanie",
            PipelineMessage.kind == "notification",
            PipelineMessage.payload["programming_complete"].astext == "true",
        )
    ).scalar()


def programming_complete(db: Session, version_id: uuid.UUID) -> bool:
    """True iff the conversation build's Programovanie has COMPLETED for this version — a
    ``stage='programovanie'`` ∧ ``kind='notification'`` ∧ ``payload.programming_complete`` message is on
    record (STEP 5, step5-kontrola-design.md). Gates the ``skontrolovat`` offer (board post-filter) AND the
    authoritative ``apply_action`` guard: the honest self-check is only offerable once there is a finished
    build to check. The mirror of :func:`navrh_plan_materialized` / :func:`_build_started` for STEP 5."""
    return _latest_programming_complete_seq(db, version_id) is not None


def kontrola_done(db: Session, version_id: uuid.UUID) -> bool:
    """True iff a kontrola self-check has ALREADY run for the LATEST completed build (STEP 5, K-4 =
    honest-by-construction "one kontrola per completed build").

    A kontrola is "done" iff the latest ``stage='priprava'`` ∧ ``kind='gate_report'`` ∧ ``payload.kontrola``
    message has a HIGHER ``seq`` than the latest Programovanie-complete notification — i.e. the self-check was
    recorded AFTER the build it checked. A fresh build / fix records a NEWER ``programming_complete`` (higher
    seq than the old kontrola report) → ``kontrola_done`` flips back to ``False``, re-opening "Skontrolovať".
    Returns ``False`` when the build never completed (nothing to have checked yet)."""
    prog_seq = _latest_programming_complete_seq(db, version_id)
    if prog_seq is None:
        return False
    kontrola_seq = db.execute(
        select(func.max(PipelineMessage.seq)).where(
            PipelineMessage.version_id == version_id,
            PipelineMessage.stage == "priprava",
            PipelineMessage.kind == "gate_report",
            PipelineMessage.payload["kontrola"].astext == "true",
        )
    ).scalar()
    return kontrola_seq is not None and kontrola_seq > prog_seq


def kontrola_floor_red(db: Session, version_id: uuid.UUID) -> bool:
    """True iff the LATEST kontrola self-check floored the runtime RED (the app did not boot / the acceptance
    run did not pass). A red-floor kontrola turn records a ``stage='priprava'`` ∧ ``kind='notification'`` ∧
    ``payload.kontrola_floor_red`` note whose ``seq`` is HIGHER than that turn's ``kontrola`` gate_report; a
    later GREEN kontrola turn writes a NEWER gate_report (and no floor note), so the floor note falls behind.
    Hence the latest kontrola is red iff a floor-red note exists with ``seq`` PAST the latest ``kontrola``
    gate_report. Keeps the Hotovo sign-off off a red build (K-3)."""
    kontrola_seq = db.execute(
        select(func.max(PipelineMessage.seq)).where(
            PipelineMessage.version_id == version_id,
            PipelineMessage.stage == "priprava",
            PipelineMessage.kind == "gate_report",
            PipelineMessage.payload["kontrola"].astext == "true",
        )
    ).scalar()
    if kontrola_seq is None:
        return False
    floor_seq = db.execute(
        select(func.max(PipelineMessage.seq)).where(
            PipelineMessage.version_id == version_id,
            PipelineMessage.stage == "priprava",
            PipelineMessage.kind == "notification",
            PipelineMessage.payload["kontrola_floor_red"].astext == "true",
        )
    ).scalar()
    return floor_seq is not None and floor_seq > kontrola_seq


def kontrola_passed(db: Session, version_id: uuid.UUID) -> bool:
    """True iff Kontrola has run for the latest completed build (:func:`kontrola_done`) AND its runtime floor
    was NOT red (:func:`kontrola_floor_red`). This — not the pass-blind ``kontrola_done`` — gates the ``hotovo``
    sign-off (board post-filter + the authoritative ``apply_action`` guard): K-3 = Kontrola never signs off on
    a red build, so a non-booting build can never reach the deployable ``done`` state via one manager click."""
    return kontrola_done(db, version_id) and not kontrola_floor_red(db, version_id)


def hotovo_done(db: Session, version_id: uuid.UUID) -> bool:
    """True iff the Manažér has already signed the LATEST completed build as Hotovo (STEP 6, mirror of
    :func:`kontrola_done`).

    The signature is the latest ``stage='priprava'`` ∧ ``kind='notification'`` ∧ ``payload.hotovo`` message; it
    counts as "done" only if its ``seq`` is HIGHER than the latest Programovanie-complete notification — a fresh
    build/fix records a NEWER ``programming_complete`` (higher seq than the old signature) → ``hotovo_done`` flips
    back to ``False``, honestly re-opening the Hotovo sign-off (kept consistent with :func:`_manazer_signoff`'s
    stale-awareness). Returns ``False`` when the build never completed (nothing to have signed yet)."""
    prog_seq = _latest_programming_complete_seq(db, version_id)
    if prog_seq is None:
        return False
    hotovo_seq = db.execute(
        select(func.max(PipelineMessage.seq)).where(
            PipelineMessage.version_id == version_id,
            PipelineMessage.stage == "priprava",
            PipelineMessage.kind == "notification",
            PipelineMessage.payload["hotovo"].astext == "true",
        )
    ).scalar()
    return hotovo_seq is not None and hotovo_seq > prog_seq


def spec_approved(db: Session, version_id: uuid.UUID) -> bool:
    """True iff the Manažér has approved the Špecifikácia for this version — ≥1 ``kind='approval'``
    :class:`PipelineMessage` exists (STEP 2 durable freeze signal; STEP 3 gating).

    DRY (step3-plan-design.md FIX2): the SINGLE spec-approval probe — one indexed exists query on the
    ``(version_id, kind)`` columns (``version_id`` is indexed), correct for both conversation + legacy
    builds. Shared by the board route (the Špecifikácia badge + the ``zostav_plan`` post-filter) AND the
    STEP-3 conversation plan gating (:func:`apply_action`) so there is no second inline ``exists`` query."""
    return (
        db.execute(
            select(PipelineMessage.id)
            .where(PipelineMessage.version_id == version_id, PipelineMessage.kind == "approval")
            .limit(1)
        ).scalar_one_or_none()
        is not None
    )


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
    per-user subscription). Graceful fallbacks when there is no owner / no row / unset field:
    **model → :data:`DEFAULT_AGENT_MODEL`** (CR-V2-028 — the doer/verifier must never silently run on the
    CLI's small default), and the **Auditor effort scales with the Miera autonómie dial** (CR-V2-008 /
    AUTON-5 / OQ-9): when no explicit per-user effort is set, the
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
    if model is None:
        # CR-V2-028: no explicit per-user pick → default BOTH agents to the strongest model
        # (DEFAULT_AGENT_MODEL), NOT the CLI's small/fast default. The AI Agent owns the whole build and
        # the Auditor independently verifies it; a freshly-created project must not silently run the doer
        # on an underpowered model. A per-user ``user_agent_settings`` row still wins.
        model = DEFAULT_AGENT_MODEL
    if effort is None and role == AUDITOR_ROLE:
        # OQ-9: no explicit per-user Auditor effort → derive it from the autonomy dial (inverse to human
        # oversight). Falls back to the dial default's effort (``max``) when the dial itself is unset.
        effort = auditor_effort_for_level(resolve_miera_autonomie(db, version_id))
    return model, effort


def _resolve_helper_model(db: Session, version_id: uuid.UUID) -> str:
    """Resolve the model the AI Agent should spawn its HELPERS on (CR-V2-038).

    The AI Agent dynamically spawns ephemeral helper agents (Agent/Task tool) for parallel/bulk work;
    those helpers' model can't be forced by a CLI flag (the spawning agent picks it), so the engine
    instructs it via a per-turn directive (:func:`_helper_model_directive`). The value is the project
    owner's ``user_agent_settings(ai_agent).helper_model``; unset → :data:`DEFAULT_HELPER_MODEL` (Haiku).
    """
    row = db.execute(
        select(UserAgentSettings.helper_model)
        .join(Project, Project.owner_id == UserAgentSettings.user_id)
        .join(Version, Version.project_id == Project.id)
        .where(Version.id == version_id, UserAgentSettings.agent_role == AI_AGENT_ROLE)
    ).first()
    return (row.helper_model if row is not None and row.helper_model else None) or DEFAULT_HELPER_MODEL


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


async def _record_parse_exhaustion(
    db: Session,
    state: PipelineState,
    *,
    stage: str,
    result: ParseFailure,
    human_hint: str,
    on_message: Optional[MessageCallback],
    metrics_phase: Optional[str] = None,
) -> None:
    """CR-V2-029: record a human-readable ``system→manazer`` notification when an agent turn produced no
    parseable status block after the bounded retries.

    Without this the FE — which renders the AI Agent tab purely from the persisted message stream — showed
    an EMPTY 'awaiting' screen indistinguishable from a legitimate question (the agent's live output had
    streamed then vanished). The notification names the parser reason and carries a raw-output excerpt in
    its payload, so the failure is visible in both the AI Agent tab and the Vývoj board, and is debuggable
    instead of silent. The caller still sets ``status='blocked'`` + ``block_reason='parse_exhaustion'``.

    ``metrics_phase`` (metrics-v3-followup.md C1): decouples the metrics PHASE stamp (``payload['phase']``,
    which ``aggregate_usage_by_phase`` reads for the failed turn's usage/timing) from the ``stage`` column —
    a v3 conversation failure attributes to Návrh/Verifikácia while ``stage`` stays ``'priprava'``. ``None``
    (every legacy caller) keeps ``phase == stage`` — byte-for-byte the pre-v3 behaviour. ``msg.stage`` is
    NEVER touched (deploy gate + ``_latest_navrh_gate_report_payload`` key on it) — a metrics-only stamp."""
    phase = metrics_phase if metrics_phase is not None else stage
    msg = _record_message(
        db,
        version_id=state.version_id,
        stage=stage,
        author="system",
        recipient="manazer",
        kind="notification",
        content=(
            f"Blokované — agent po opakovaných pokusoch nevrátil platný stavový výstup "
            f"(dôvod: {result.reason}). {human_hint}"
        ),
        payload={
            "phase": phase,
            "parse_failure_reason": result.reason,
            "raw_excerpt": result.raw,
            **(_failure_metrics_payload(result) or {}),
        },
    )
    if on_message is not None:
        await on_message(msg)


def _helper_model_directive(helper_model: str) -> str:
    """The AI-Agent helper-model directive (CR-V2-038), appended to the AI Agent's turns.

    The model a spawned helper runs on can't be forced by a CLI flag (the spawning agent picks it when it
    calls the Agent/Task tool — left to itself it defaults to a small/fast model). So the engine tells the
    AI Agent which model to use, honouring the project owner's ``helper_model`` Nastavenia choice (Haiku by
    default, Opus for a high-stakes build). Harmless on the light phases (Príprava/Návrh) where the charter
    tells it NOT to spawn helpers — it only takes effect when it actually spawns one (Programovanie)."""
    return (
        "KEĎ spúšťaš pomocné agenty (nástroj Agent / Task) pre paralelné/hromadné podúlohy, spúšťaj ich "
        f"NA MODELI `{helper_model}` (parameter modelu pri spustení pomocníka). Ťažké jadro rob sám na "
        "svojom modeli; pomocníkov používaj len na naozaj paralelnú/hromadnú prácu (typicky Programovanie)."
    )


def _status_block_instruction(stage: str) -> str:
    """The status-block + message-formatting contract appended to EVERY agent turn's prompt (CR-V2-031,
    extended CR-V2-034).

    Two jobs, both injected at the single :func:`invoke_agent` chokepoint so they reach the primary turn
    AND every parse-retry re-emit AND (crucially) the ONGOING ``--resume`` session — no charter reset
    needed to take effect:

    * Names the EXACT enum literals the engine validates (``pipeline_status.STAGES`` / ``BLOCK_KINDS`` /
      ``_AWAITING``) so the agent emits them verbatim instead of guessing/translating (Opus emitted
      ``stage='preparation'`` → an ``unknown stage`` ParseFailure). Keep the literals in sync.
    * Mandates that the Manažér-facing fields (``report`` / ``question``) be FORMATTED Markdown with real
      line breaks — the agent had been writing one un-broken wall of text (0 newlines), because a past
      "escape newlines" instruction scared it off line breaks. Newlines + diacritics + Markdown are all
      fine inside a JSON string (the encoder handles them)."""
    return (
        "Text pre Manažéra (`report`, `question`) píš ako PEKNE FORMÁTOVANÝ Markdown, NIE jeden dlhý blok: "
        "oddeľuj odseky PRÁZDNYM riadkom, každú položku zoznamu daj na vlastný riadok s `- `, dôležité "
        "**zvýrazni**, ak pomôže pridaj krátky nadpis. Zalomenia riadkov, diakritika aj Markdown sú v JSON "
        "reťazci ÚPLNE V PORIADKU — kódovač ich ošetrí sám; nikdy nepíš celú správu na jeden riadok ani bez "
        "diakritiky.\n"
        "Ukonči odpoveď JEDNÝM štruktúrovaným stavovým blokom medzi značkami `<<<PIPELINE_STATUS>>>` a "
        "`<<<END_PIPELINE_STATUS>>>` (F-007-orchestration-cockpit.md §5.3), ako POSLEDNÚ vec v odpovedi. "
        "Polia stavového bloku sú PEVNÉ KÓDOVÉ HODNOTY — použi ich PRESNE, NIKDY ich neprekladaj do "
        "angličtiny: "
        f"`stage` = `{stage}` (presne táto hodnota); "
        "`kind` je jedna z {question, answer, gate_report, verdict, done, blocked, framework_issue}; "
        "`awaiting` je `manazer` alebo `none`.\n"
        # §15 escalation to Dedo (Director observation #6) — injected on EVERY turn (incl. --resume), so the
        # agent always knows the escape hatch even mid-build without a charter reset.
        "ESKALÁCIA NA DEDA (§15): ak naďabíš na problém, ktorý NEVIEŠ opraviť, lebo si vyžaduje zmenu "
        "SAMOTNÉHO NEX Studia (nástroja/frameworku — NIE zákazníckeho projektu), NEOPAKUJ pokusy donekonečna "
        "a NEPÝTAJ Manažéra, nech to opraví — on to nevie. Eskaluj Dedovi (meta-vývojárovi NEX Studia): "
        "vráť stavový blok s `kind` = `framework_issue` a do poľa `question` napíš JASNÚ správu pre Deda — "
        "čo zlyhalo (chyba), v akom kontexte, a akú zmenu NEX Studia to podľa teba potrebuje. `awaiting` daj "
        "`manazer`. Build sa zablokuje a Dedo dostane tvoju správu."
    )


def _directive_for(stage: str, flow_type: str = "new_version") -> str:
    """Minimal orchestrator directive for a stage. The agent reads its charter.

    (CR-V2-028: the v1 ``kickoff`` Coordinator-triage fast-fix branch is RETIRED with the rest of the v1
    11-stage waterfall — ``kickoff`` is not a v2 phase, the Coordinator role is gone, and the fast-fix lane
    begins at ``priprava`` with the lightweight directive-IS-the-brief variant of :func:`_priprava_directive`.
    No phase routes through this generic directive for ``fast_fix`` any more.)"""
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
    Návrh design doc + task plan). Mirrors the convention the Auditor's upfront review reads from
    (:func:`_auditor_upfront_directive`) + ``_write_task_plan_doc``."""
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


def _priprava_directive(db: Session, version_id: uuid.UUID, *, flow_type: str = "new_version") -> str:
    """The Príprava phase brief (CR-V2-010; PREP-1..PREP-4, RULES-3 read-first/ask-until-understood).

    DESIGN-BEARING (flagged for the Manažér): this prompt DEFINES the AI Agent's Príprava behaviour —
    the interactive Zadanie→Špecifikácia dialogue. Drafted from ``nex-studio-v2-design.md`` §2.1 / §5.1(1).
    The agent's own ``Pravidlá agenta`` charter (templates/ai-agent-charter.md §2) carries the matching
    rules; this is the per-turn orchestrator injection that names the concrete Zadanie + Špecifikácia paths.

    **Fast-fix short path (CR-V2-028; design §2.4/§2.5):** for ``flow_type='fast_fix'`` the heavy
    interactive Zadanie→Špecifikácia dialogue is SKIPPED — the Manažér's directive IS the brief. Príprava
    is a lightweight "acknowledge the directive + read just enough to fix it" turn that closes immediately
    with ``kind=done`` (no Špecifikácia artifact, no clarification loop), so the lane advances straight to
    Programovanie. The directive rode in as the kickoff content, so it is already in the warm session.

    For ``flow_type='new_version'`` the init prompt ("Načítaj zadanie a začni prípravu špecifikácie" —
    design §2.1) tells the AI Agent to:
      1. READ the Zadanie (``customer-requirements.md``) + existing code / specs / KB (read-before-you-think);
      2. systematize the requirements and ASK the Manažér clarifying questions on EVERY unclear /
         under-thought point — NO design until every detail is understood (set ``kind=question`` and STOP);
      3. proactively PROPOSE improvements (features / UX / quality), the professional taking responsibility;
      4. when (and only when) every detail is understood, WRITE the Špecifikácia as Markdown to the version
         spec path and list it in ``deliverables[]``, closing the phase with ``kind=gate_report``. The
         end-Príprava ``Schváliť špecifikáciu`` stop is ALWAYS mandatory (dial-independent) — Návrh cannot
         begin until the Manažér approves the Špecifikácia.
    """
    if flow_type == "fast_fix":
        # The lightweight fast-fix Príprava: the directive (in this warm session's kickoff) IS the whole
        # brief — read only what's needed to fix it, do NOT run the heavy spec dialogue, do NOT write a
        # Špecifikácia, do NOT block on clarification. Close immediately so the lane reaches Programovanie.
        return (
            "RÝCHLA OPRAVA (fast-fix lane) — Príprava je ĽAHKÁ: pokyn Manažéra (smernica) je VYŠŠIE v tomto "
            "vlákne a JE celé tvoje zadanie. Neraď heavy dialóg špecifikácie a NEZAPISUJ Špecifikáciu.\n"
            "1. Prečítaj IBA toľko kódu/kontextu, koľko treba na pochopenie opravy.\n"
            "2. NEVytváraj Špecifikáciu ani návrhový dokument — smernica je brief; engine ťa AUTOMATICKY "
            "posunie do Programovania (žiadne schválenie medzitým).\n"
            "3. ZASTAV (`kind=question`) IBA ak je oprava naozaj nejednoznačná alebo technicky nemožná — NIE "
            "preto, že by si chcel doplniť proces. Inak UZAVRI toto kolo `kind=done`.\n"
            "Ukonči odpoveď štruktúrovaným stavovým výstupom (F-007-orchestration-cockpit.md §5.3)."
        )
    version_number = db.execute(select(Version.version_number).where(Version.id == version_id)).scalar_one()
    zadanie_rel = f"{_version_spec_rel(version_number)}/customer-requirements.md"
    spec_rel = _priprava_spec_rel(version_number)
    return (
        "Načítaj zadanie a začni prípravu špecifikácie (fáza Príprava). Postupuj KROK ZA KROKOM ako pri "
        "konzultácii s človekom — NIE hromadne.\n"
        f"1. NAČÍTAJ Zadanie (`{zadanie_rel}`) + existujúci kód, špecifikácie a KB — read before you think.\n"
        "2. V PRVOM kole napíš VÝSLEDOK ANALÝZY (čo si pochopil) a STRUČNÝ PREHĽAD otvorených bodov (zoznam "
        "tém na dorozhodnutie), aby Manažér videl rozsah.\n"
        "3. Potom otvorené body konzultuj PO JEDNEJ: polož PRÁVE JEDNU otázku (`kind=question`, pole "
        "`question`) a ZASTAV. Na ďalší bod prejdi AŽ KEĎ je predošlý obojstranne UZAVRETÝ a rovnako "
        "pochopený — na jednu otázku môže byť aj viackolový dialóg. NIKDY nevysýpaj všetky otázky naraz.\n"
        "4. Keď sú VŠETKY body uzavreté, PROAKTÍVNE navrhni vylepšenia (features / UX / kvalita) — profesionál "
        "preberá zodpovednosť za výsledok, Zadanie je len východisko (waterfall filozofia).\n"
        "5. Až keď je každý detail pochopený a zlepšováky prebrané: zapíš FINÁLNU Špecifikáciu ako Markdown do "
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

    Drives the DESIGN-DOC turn only — the EPIC→FEAT→TASK task plan is NO LONGER built in Návrh (Director
    2026-07-13); it is generated at the START of Programovanie (:func:`_run_build_round`) from the final
    design + the Manažér's Vizuál changes, so it reflects the final state. Návrh only SKETCHES the plan as
    the design doc's last part. The brief therefore tells the AI Agent to:
      1. READ the approved Špecifikácia (``specification.md``) + the Zadanie + existing code / KB;
      2. WRITE ONE coherent design ``.md`` to the version spec path — sections SIZED to the project (overview/
         goal · data model · API/interfaces · BE+FE design — only as much as needed; depth is the agent's
         judgment), list it in ``deliverables[]``;
      3. close the design-doc turn with ``kind=done`` — do NOT cram the EPIC→FEAT→TASK tree into this status
         block (the detailed plan is built later, at Programovanie start);
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
        "3. Plán úloh (EPIC → FEAT → TASK) je POSLEDNÁ časť návrhu — načrtni ho v dokumente, ale NEVkladaj "
        "ho do tohto stavového bloku. Detailný plán úloh sa zostaví neskôr, na začiatku programovania (z tohto "
        "návrhu + vizuálnych zmien), aby odrážal finálny stav. Tento ťah UZAVRI `kind=done` (návrhový dokument "
        "je hotový).\n"
        "4. Ak je akýkoľvek detail návrhu ešte nejednoznačný, nastav `kind=question`, polož otázku Manažérovi "
        "a ZASTAV — schvaľovací bod po Návrhu tvoje otázky vynesie.\n"
        "Ukonči odpoveď štruktúrovaným stavovým výstupom (F-007-orchestration-cockpit.md §5.3)."
    )


def _vizual_directive(
    db: Session, version_id: uuid.UUID, manager_request: str, mockup_rel: Optional[str] = None
) -> str:
    """The Vizuál phase brief (CR-1, nex-studio-visual; spec §3.B) — the AI's VISUAL-CONSULTATION turn.

    A sibling of :func:`_priprava_directive` / :func:`_kontrola_directive`: the per-turn orchestrator
    injection for the "Manažér asks for a change → AI edits the LIVE app" HMR loop (spec §1). It instructs
    the AI Agent to build/refine the project's FRONTEND SCREENS ONLY (under ``frontend/``), visual-first
    (Delphi form-first) — real screens / layout / navigation with MOCK / representative data (NO real
    backend yet; that is Programovanie) — assembled from the shared ``nex-shared`` kit for a consistent
    look, and to apply EXACTLY the Manažér's request, then commit. The live dev-server sandbox then reflects
    the change in the cockpit preview via HMR (< 1 s, no rebuild). The status-block contract is appended
    downstream at the :func:`invoke_agent` chokepoint, so the turn still ends with the machine status block
    the engine parses. Slovak where it faces the Manažér; the directive body is normal build language."""
    version_number = db.execute(select(Version.version_number).where(Version.id == version_id)).scalar_one()
    spec_rel = _priprava_spec_rel(version_number)
    design_rel = _navrh_design_doc_rel(version_number)
    if mockup_rel is not None:
        # MOCKUP mode (Director 2026-07-17): the preview IS the self-contained clickable mockup (no backend, no
        # login) — apply the Manažér's change DIRECTLY to that HTML file, keeping it self-contained.
        return (
            "Fáza VIZUÁLNA KONZULTÁCIA — dolaďuješ ŽIVÝ náhľad appky spolu s Manažérom. Náhľad je "
            f"**samostatný klikací mockup** `{mockup_rel}` (self-contained HTML, bez reálneho backendu a bez "
            "prihlásenia, reprezentatívne dáta).\n"
            f"1. Pre kontext si prečítaj Špecifikáciu `{spec_rel}` a Návrh `{design_rel}`.\n"
            f"2. Manažér žiada TÚTO zmenu: «{manager_request}». Aplikuj PRESNE ju PRIAMO do mockupu "
            f"`{mockup_rel}` — nič navyše. Mockup nech ostane **self-contained** (inline štýly/skript, žiadny "
            "reálny backend).\n"
            "3. Zmenu iba ZAPÍŠ do mockupu — NEcommituj (náhľad ju premietne po obnovení). NEROB backend ani "
            "dátové modely — to je Programovanie. Ak je požiadavka naozaj nejednoznačná, `kind=question`, "
            "opýtaj sa PRÁVE JEDNU vec a ZASTAV; inak kolo UZAVRI `kind=done`.\n"
            "Ukonči odpoveď štruktúrovaným stavovým výstupom (F-007-orchestration-cockpit.md §5.3)."
        )
    return (
        "Fáza VIZUÁLNA KONZULTÁCIA — staviaš a dolaďuješ ŽIVÝ vizuál appky spolu s Manažérom. Pracuješ "
        "VÝHRADNE vo FRONTENDE (adresár `frontend/`): reálne obrazovky, rozloženie a navigácia, vizuál-first "
        "(ako Delphi — najprv formuláre/obrazovky). Používaj MOCK / reprezentatívne dáta priamo vo FE "
        "(fixtures / MSW) — REÁLNY backend teraz NErobíš (to je fáza Programovanie).\n"
        f"1. Pre kontext si prečítaj schválenú Špecifikáciu `{spec_rel}` a Návrh `{design_rel}` — nech "
        "obrazovky sedia so zámerom projektu.\n"
        "2. Obrazovky skladaj zo zdieľaného kitu `nex-shared` (rovnaké komponenty a štýl) — kvôli "
        "konzistentnému vzhľadu naprieč appkami.\n"
        f"3. Manažér žiada TÚTO zmenu: «{manager_request}». Aplikuj PRESNE ju — nič navyše, nič menej.\n"
        "4. Zmenu iba ZAPÍŠ do FE zdrojov — NEcommituj (živý náhľad ju cez HMR premietne < 1 s aj bez commitu). "
        "Počas Vizuálu môže byť takýchto drobných úprav veľa; všetky sa spoločne uložia JEDNÝM commitom až keď "
        "Manažér Vizuál schváli. Ži teda len v pracovnom strome, žiadny `git commit`.\n"
        "NEROB backendovú logiku ani dátové modely — to je Programovanie. Ak je požiadavka naozaj "
        "nejednoznačná, nastav `kind=question`, opýtaj sa PRÁVE JEDNU vec a ZASTAV; inak kolo UZAVRI "
        "`kind=done`.\n"
        "Ukonči odpoveď štruktúrovaným stavovým výstupom (F-007-orchestration-cockpit.md §5.3)."
    )


def _prior_auditor_review_context(db: Session, version_id: uuid.UUID) -> Optional[tuple[list[str], str]]:
    """If the Auditor already reviewed this version (a PRIOR ``verdict``) and the AI Agent has spoken since,
    return ``(prior_findings, agent_latest)`` so the upfront directive can turn a STATELESS re-run into a
    dispute-aware RE-review that CONVERGES. Director 2026-07-17: the nex-shopify 3×-identical-verdict loop —
    each review re-derived the SAME findings without re-checking the agent's rebuttal against the CURRENT
    docs. ``None`` on the FIRST review (no prior verdict) → the review runs fresh, unchanged."""
    prior = db.execute(
        select(PipelineMessage)
        .where(
            PipelineMessage.version_id == version_id,
            PipelineMessage.author == "auditor",
            PipelineMessage.kind == "verdict",
        )
        .order_by(PipelineMessage.seq.desc())
        .limit(1)
    ).scalar_one_or_none()
    if prior is None:
        return None
    payload = prior.payload if isinstance(prior.payload, dict) else {}
    raw_findings = payload.get("findings") or []
    findings = [str(f) for f in raw_findings if isinstance(f, str)] if isinstance(raw_findings, list) else []
    agent_latest = db.execute(
        select(PipelineMessage.content)
        .where(
            PipelineMessage.version_id == version_id,
            PipelineMessage.author == "ai_agent",
            PipelineMessage.kind == "gate_report",
            PipelineMessage.seq > prior.seq,
        )
        .order_by(PipelineMessage.seq.desc())
        .limit(1)
    ).scalar_one_or_none()
    if not findings and not agent_latest:
        return None
    return findings, (agent_latest or "").strip()


def _auditor_upfront_directive(db: Session, version_id: uuid.UUID) -> str:
    """The Auditor's UPFRONT spec/design review brief (CR-V2-013; AUD-1(a), AUD-5, NAVRH-4, AUTON-5).

    DESIGN-BEARING (flagged for the Manažér): this prompt DEFINES the independent Auditor's upfront-review
    behaviour — the OLD Customer agent's Gate-E function, now done by the independent Auditor. Drafted from
    ``nex-studio-v2-design.md`` §5.1(2) (Auditor rules → "Upfront spec-completeness") + the design doc
    §3.x.79(a). The Auditor's ``Pravidlá agenta`` charter (``templates/auditor-charter.md``) carries the
    matching standing rules; this is the per-turn orchestrator injection naming the concrete Špecifikácia +
    design-doc paths the Auditor reads.

    After Návrh — before the build commits to coding — the independent Auditor scans the brief
    (``specification.md``) + the AI Agent's design doc (``design.md``) for **holes / ambiguities /
    contradictions** and emits ONE ``verdict`` (the CR-V2-006 repurposed findings shape):

      * **READ + RUN-ONLY (independence):** the Auditor READS the artifacts (and may run the app) — it
        NEVER writes code, edits a file, or commits. It FINDS; the AI Agent FIXES (D2/D5 blind-spot
        safeguard). The brief forbids any edit/commit explicitly.
      * **verdict=true (PASS)** — the spec + design are sound enough to build; ``findings`` may still carry
        non-blocking notes. The post-Návrh schvaľovací bod is then governed by the Miera autonómie dial.
      * **verdict=false (FAIL = a spec/design HOLE)** — list the concrete holes in ``findings`` and the
        targeted clarification/revision scope in ``proposed_fix``. A hole ESCALATES to the Manažér (AUD-4):
        the build STOPS at the post-Návrh schvaľovací bod regardless of the dial; the Manažér clarifies /
        revises the Špecifikácia / Návrh, then re-approves. (Independence: the Auditor proposes the fix
        scope, it never applies it.)
      * **Dial-scaled depth (OQ-9 / AUTON-5):** review intensity scales INVERSELY with human oversight —
        higher autonomy → deeper, more adversarial review (the Auditor is the safety net that compensates
        for fewer human stops); lower autonomy → lighter. The ``--effort`` flag is already coupled to the
        dial in :func:`_resolve_dispatch_overrides` for the Auditor role; the prose below tells the Auditor
        to MATCH its scrutiny to that level.

    Its findings surface at the post-Návrh stop ALONGSIDE the AI Agent's own clarification questions — no
    per-question Customer↔Designer ping-pong (the old Gate-E loop is retired; this is ONE invocation)."""
    version_number = db.execute(select(Version.version_number).where(Version.id == version_id)).scalar_one()
    spec_rel = _priprava_spec_rel(version_number)
    design_rel = _navrh_design_doc_rel(version_number)
    level = resolve_miera_autonomie(db, version_id)
    # Dial → review-depth instruction (OQ-9): higher autonomy (fewer Manažér stops) → deeper, more
    # adversarial review; lower autonomy → lighter (the Manažér + self-check carry more of the load).
    depth = (
        "Miera autonómie je VYSOKÁ (Manažér je málokedy v slučke) — rob DÔKLADNÚ, adverzariálnu previerku: "
        "si jediné nezávislé oči, kompenzuješ menej ľudských kontrol."
        if level in ("plna", "len_na_konci")
        else "Miera autonómie je nižšia (Manažér kontroluje často) — rob ZAMERANÚ, ľahšiu previerku na "
        "rizikové miesta; ťažšiu kontrolu nesie Manažér + self-check AI Agenta."
    )
    # Dispute-aware RE-review (Director 2026-07-17): if a prior verdict + the agent's reaction exist, thread
    # them in so the Auditor RE-CHECKS the disputed findings against the CURRENT docs and CONVERGES, instead of
    # statelessly re-deriving identical findings (the nex-shopify 3× loop).
    prior_ctx = _prior_auditor_review_context(db, version_id)
    reverify_block = ""
    if prior_ctx is not None:
        prior_findings, agent_latest = prior_ctx
        findings_list = "\n".join(f"   - {f}" for f in prior_findings) or "   - (žiadne explicitné)"
        reverify_block = (
            "\n\nRE-PREVIERKA — už si raz posudzoval a AI Agent na tvoje nálezy reagoval; teraz KONVERGUJ, "
            "neopakuj identicky to isté.\n"
            f"Tvoje predošlé nálezy:\n{findings_list}\n"
            + (f"Reakcia AI Agenta: {agent_latest}\n" if agent_latest else "")
            + "Každý predošlý nález ZNOVA over proti AKTUÁLNYM dokumentom (prečítaj konkrétnu sekciu). ZAHOĎ "
            "ho, ak je v aktuálnych dokumentoch naozaj vyriešený. PONECHAJ len tie, čo sú STÁLE naozaj "
            "chýbajúce/nesprávne — a cituj presné miesto. Neopakuj identický nález bez nového overenia."
        )
    return (
        "UPFRONT PREVIERKA (nezávislý Auditor, po fáze Návrh, pred začatím programovania).\n"
        f"1. NAČÍTAJ schválenú Špecifikáciu (`{spec_rel}`) + návrhový dokument (`{design_rel}`) + Zadanie a "
        "existujúci kód/KB. Si NEZÁVISLÝ overovateľ MIMO tímu AI Agenta — kontroluj z VONKU (žiadny agent "
        "nedokáže auditovať sám seba).\n"
        "2. Hľadaj MEDZERY / nejednoznačnosti / protirečenia v Špecifikácii a Návrhu: chýbajúce detaily, "
        "rozpory medzi zadaním a návrhom, nepokryté hraničné prípady, rizikové predpoklady (bezpečnosť, "
        "peniaze, hlavný kontrakt). Buď adverzariálny — aktívne hľadaj diery, nepotvrdzuj happy-path.\n"
        "2b. Predtým než niečo označíš za MEDZERU, POTVRĎ, že to v AKTUÁLNYCH dokumentoch naozaj chýba alebo "
        "je zle (cituj presné miesto). Požiadavka vyriešená cez KONFIGURÁCIU s bezpečným defaultom "
        "(fail-safe), EXPLICITNE odložená mimo rozsah s odôvodnením, alebo HEDGE „re-overiť pri builde“ — "
        "NIE je blokujúca medzera; neoznačuj ju za chýbajúcu.\n"
        f"3. {depth}\n"
        "4. SI READ + RUN-ONLY: smieš ČÍTAŤ (a prípadne spustiť aplikáciu na overenie), ale NIKDY neupravuj "
        "súbor, nepíš kód ani necommituj. TY NÁJDEŠ — opravuje AI Agent (zachovaná nezávislosť).\n"
        "5. Vráť `kind=verdict`:\n"
        "   - ak je Špecifikácia + Návrh bez blokujúcej medzery → `verdict=true` (PASS); do `findings` daj "
        "prípadné neblokujúce poznámky (alebo prázdne).\n"
        "   - ak nájdeš medzeru (HOLE) → `verdict=false` (FAIL); konkrétne diery vymenuj v `findings` a do "
        "`proposed_fix` napíš ZAMERANÝ rozsah vyjasnenia/úpravy pre Manažéra (NEvykonávaj ho). Medzera sa "
        "eskaluje Manažérovi — build sa zastaví na schvaľovacom bode po Návrhu.\n"
        "Ukonči odpoveď štruktúrovaným stavovým výstupom (F-007-orchestration-cockpit.md §5.3)." + reverify_block
    )


def _consultation_directive(
    db: Session, version_id: uuid.UUID, *, source: str, findings: list[str], proposed_fix: Optional[str]
) -> str:
    """CR-V2-041: brief the AI Agent to TRANSLATE a problem into a Manažér CONSULTATION (the production
    "Dedo on the screen"). It must NOT fix anything yet — it emits ONE ``kind=consultation`` block whose
    ``decisions[]`` the Manažér resolves one-at-a-time by click. First consumer: the Auditor's upfront
    review found holes (``source="auditor_upfront"``); generalizes to any mid-build blocker later.

    The AI Agent and the Auditor run in SEPARATE ``claude`` sessions, so the Auditor's findings are passed
    in VERBATIM here (the AI Agent cannot read the Auditor's thread)."""
    del db, version_id  # signature parity with the other directive builders; findings are passed in directly
    findings_block = "\n".join(f"  - {f}" for f in findings) or "  (žiadne explicitné body)"
    fix_block = (
        f"\nNavrhovaný rozsah opravy (od Auditora, len ako kontext, NEvykonávaj ho): {proposed_fix}"
        if proposed_fix
        else ""
    )
    return (
        "KONZULTÁCIA S MANAŽÉROM. Manažér je NEŠPECIALISTA — píš ĽUDSKOU rečou, bez technického žargónu.\n"
        "Pri nezávislej previerke sa našli tieto body, ktoré treba ROZHODNÚŤ pred pokračovaním:\n"
        f"{findings_block}{fix_block}\n\n"
        "NEOPRAVUJ zatiaľ NIČ. Vráť JEDEN `kind=consultation` blok:\n"
        "- `consultation.intro`: 1-2 vety po ľudsky — čo a prečo treba rozhodnúť.\n"
        "- `consultation.decisions[]`: KAŽDÝ bod ako VLASTNÉ rozhodnutie (nezlučuj a nevynechaj žiadny). "
        "Každé rozhodnutie:\n"
        "  • `key` — krátky stabilný identifikátor, UNIKÁTNY v rámci konzultácie (napr. 'telegram', "
        "'topologia'); dva rovnaké kľúče sú chyba.\n"
        "  • `question` — problém po ľudsky, BEZ žargónu (nie 'asyncpg/DDL/lockfile' — vysvetli podstatu "
        "tak, aby ju pochopil nešpecialista) a JASNE čo sa rozhoduje.\n"
        "  • `explanation` — 1 veta prečo to záleží.\n"
        "  • `options` — 2-3 možnosti, každá `label` + `detail` (krátky dôsledok voľby).\n"
        "  • práve JEDNU možnosť označ `recommended: true` a daj jednoriadkové `rationale` (prečo ju odporúčaš).\n"
        "  • `allow_free_text: true` IBA ak sa bod nedá rozumne rozložiť na možnosti.\n"
        f"- `consultation.source`: '{source}'.\n"
        "`summary` daj krátke, po ľudsky. Ukonči štruktúrovaným stavovým výstupom."
    )


async def _consult_fallback(
    db: Session,
    state: PipelineState,
    *,
    note: str,
    on_message: Optional[MessageCallback],
    failure: Optional[ParseFailure] = None,
    findings: Optional[list[str]] = None,
    agent_response: Optional[str] = None,
    next_action: Optional[str] = None,
) -> PipelineState:
    """CR-V2-041 fail-open: when a consultation can't be produced (parse failure / non-consultation output /
    re-consult cap), fall back to a plain ``awaiting_manazer`` stop (today's behaviour) so a flaky turn can
    never wedge the build — the Manažér posúdi návrh klasicky (Schváliť / Uprav).

    Fix B (Director 2026-07-17): carry the Auditor's ``findings`` and the AI Agent's dispute ``agent_response``
    into the notification content + payload, so the Manažér sees BOTH sides concretely instead of a
    context-less stop (the "nevidím konkrétne riešenie" gap). ``next_action`` keeps the state prompt short
    while ``note`` carries the full both-sides detail as the message content."""
    base_payload = (_failure_metrics_payload(failure) if failure is not None else None) or {
        "phase": state.current_stage
    }
    if findings:
        base_payload = {**base_payload, "auditor_findings": list(findings)}
    if agent_response:
        base_payload = {**base_payload, "agent_response": agent_response}
    msg = _record_message(
        db,
        version_id=state.version_id,
        stage=state.current_stage,
        author="system",
        recipient="manazer",
        kind="notification",
        content=note,
        payload=base_payload,
    )
    if on_message is not None:
        await on_message(msg)
    state.status = "awaiting_manazer"
    state.block_reason = None
    state.next_action = next_action or note
    db.flush()
    return state


async def _settle_for_consultation(
    db: Session,
    state: PipelineState,
    *,
    source: str,
    verdict: Optional[PipelineStatusBlock] = None,
    on_event: Optional[claude_agent.EventCallback] = None,
    on_message: Optional[MessageCallback] = None,
) -> PipelineState:
    """CR-V2-041: turn a problem (Auditor findings, later any blocker) into an interactive Manažér
    consultation. Dispatches ONE AI-Agent turn to translate it into a ``kind=consultation`` (decisions[]
    the Manažér answers one-at-a-time). On success settles ``blocked``/``decision_needed``; on parse failure,
    a non-consultation output, or the re-consult cap, FALLS BACK to a plain ``awaiting_manazer`` stop. Runs
    inside the dispatch path (sole-mutator holds)."""
    findings = list(verdict.findings) if verdict is not None else []
    proposed_fix = verdict.proposed_fix if verdict is not None else None

    # Re-consult cap (mirror AUDITOR_LOOP_MAX): bound verdict→consult→re-verdict so it can't loop forever.
    consult_count = db.execute(
        select(func.count())
        .select_from(PipelineMessage)
        .where(PipelineMessage.version_id == state.version_id, PipelineMessage.kind == "consultation")
    ).scalar_one()
    if consult_count >= AUDITOR_LOOP_MAX:
        return await _consult_fallback(
            db,
            state,
            note=(
                f"Konzultácia sa zopakovala {AUDITOR_LOOP_MAX}× — eskalované Manažérovi. "
                "Posúď návrh klasicky (Schváliť / Uprav)."
            ),
            on_message=on_message,
        )

    result = await invoke_agent_with_parse_retry(
        db,
        version_id=state.version_id,
        role=AI_AGENT_ROLE,
        stage=state.current_stage,
        prompt=_consultation_directive(
            db, state.version_id, source=source, findings=findings, proposed_fix=proposed_fix
        ),
        on_event=on_event,
        recipient="manazer",
        on_message=on_message,
    )
    if isinstance(result, ParseFailure) or result.kind != "consultation" or result.consultation is None:
        # MARKDOWN (rendered by ConversationThread's SpecMarkdown): a proper ``- `` list with a blank line
        # before it, so the findings render as a readable bulleted list — NOT one collapsed wall of text
        # (Director 2026-07-17: the first cut used ``•`` + single newlines, which markdown glued into one blob).
        findings_md = "\n".join(f"- {f}" for f in findings)
        # Fix B (Director 2026-07-17): the AI Agent DISPUTED the findings — it returned a normal block (e.g.
        # gate_report, judging them stale/already-resolved) instead of decision cards. Surface BOTH sides so
        # the Manažér decides with full context (the stale-audit → "nevidím konkrétne riešenie" dead-end).
        if not isinstance(result, ParseFailure) and result.kind != "consultation":
            agent_response = (result.summary or "").strip()
            content = (
                f"**Nezávislá previerka označila {len(findings)} bod(ov), ale agent ich rozporuje** — "
                "neurobil rozhodovacie karty, posúdil ich ako už vyriešené. Porovnaj oba pohľady "
                "s aktuálnymi dokumentmi a rozhodni (**Schváliť** / **Uprav**)."
            )
            if findings_md:
                content += f"\n\n**Nálezy previerky:**\n\n{findings_md}"
            if agent_response:
                content += f"\n\n**Odpoveď agenta:**\n\n{agent_response}"
            return await _consult_fallback(
                db,
                state,
                note=content,
                next_action=f"Spor previerka↔agent ({len(findings)} bod.) — posúď oba pohľady (Schváliť / Uprav).",
                on_message=on_message,
                findings=findings or None,
                agent_response=agent_response or None,
            )
        # A genuine parse failure / empty consultation block → fail-open, but still list the findings for
        # context so the stop is never content-less.
        content = "Konzultáciu sa nepodarilo pripraviť — posúď návrh klasicky (Schváliť / Uprav)."
        if findings_md:
            content += f"\n\n**Nálezy previerky:**\n\n{findings_md}"
        return await _consult_fallback(
            db,
            state,
            note=content,
            next_action="Konzultáciu sa nepodarilo pripraviť — posúď návrh klasicky (Schváliť / Uprav).",
            on_message=on_message,
            failure=result if isinstance(result, ParseFailure) else None,
            findings=findings or None,
        )
    n = len(result.consultation.decisions)
    state.status = "blocked"
    state.block_reason = "decision_needed"
    word = "rozhodnutie" if n == 1 else ("rozhodnutia" if 2 <= n <= 4 else "rozhodnutí")
    state.next_action = f"Manažér: rozhodni 1/{n} ({n} {word}, konzultácia)."
    db.flush()
    return state


def _owner_chat_id_for_version(db: Session, version_id: uuid.UUID) -> Optional[str]:
    """Telegram chat_id of the version's project owner, or ``None`` (mirrors
    ``pipeline_runner._owner_chat_id`` — the recipient of the agent → Dedo escalation ping, Director obs #6)."""
    return db.execute(
        select(User.telegram_chat_id)
        .join(Project, Project.owner_id == User.id)
        .join(Version, Version.project_id == Project.id)
        .where(Version.id == version_id)
    ).scalar_one_or_none()


async def _settle_framework_issue(
    db: Session,
    state: PipelineState,
    result: PipelineStatusBlock,
    *,
    stage: str,
    on_message: Optional[MessageCallback] = None,
) -> PipelineState:
    """Director observation #6: settle an AGENT-INITIATED ``framework_issue`` escalation to Dedo.

    Called from the agent-output settle path (:func:`run_conversation_turn`, :func:`run_dispatch`) when the
    parsed block carries ``kind='framework_issue'`` — the AI Agent hit a problem it CANNOT fix because the
    fix needs a change to NEX Studio ITSELF (§15). The build settles ``blocked``/``block_reason=
    'framework_issue'`` (``determine_available_actions`` then offers the Manažér NO recovery actions — only
    Dedo clears it), records a readable ``system→manazer`` notification carrying the Dedo-message +
    ``payload.framework_issue=True`` (the FE renders it with an amber/red accent), and DELIVERS the message
    to Dedo two ways (A: the ``.dedo-channel/inbox`` audit file; B: a Telegram ping to the project owner).

    Delivery is best-effort (:func:`dedo_escalation.escalate_to_dedo` never raises): the block is already
    durable in the DB + the append-only message log, so the escalation is never lost even if the channel
    mount is absent or Telegram hiccups."""
    dedo_message = (result.question or result.summary or "").strip()
    state.status = "blocked"
    state.block_reason = "framework_issue"
    state.next_action = (
        "Túto chybu musí opraviť náš technický tím — nedá sa vyriešiť odtiaľto. Automaticky sme ho na ňu "
        "upozornili. Skús to o chvíľu znova (Nahlásiť znova), alebo zatiaľ pokračuj na inom projekte."
    )

    slug = _project_slug_for_version(db, state.version_id)
    version_number = db.execute(select(Version.version_number).where(Version.id == state.version_id)).scalar_one()

    msg = _record_message(
        db,
        version_id=state.version_id,
        stage=stage,
        author="system",
        recipient="manazer",
        kind="notification",
        content=(
            "Narazili sme na chybu, ktorú musí opraviť náš technický tím — nedá sa vyriešiť odtiaľto. "
            "Automaticky sme ho na ňu upozornili. Skús to o chvíľu znova, alebo zatiaľ pokračuj na inom projekte."
        ),
        payload={
            "phase": stage,
            "framework_issue": True,
            "dedo_message": dedo_message,
        },
    )
    if on_message is not None:
        await on_message(msg)

    # Deliver to Dedo (A + B). Context = the build state that produced the escalation (the message itself is
    # in the frontmatter's "Správa od agenta" section — the block + notification already carry it).
    context = (
        f"Projekt: {slug} · Verzia: v{version_number} · Fáza: {stage} · Stav: blocked / block_reason=framework_issue."
    )
    await dedo_escalation.escalate_to_dedo(
        project_slug=slug,
        version_number=version_number,
        dedo_message=dedo_message,
        context=context,
        owner_chat_id=_owner_chat_id_for_version(db, state.version_id),
    )
    db.flush()
    return state


# CR-V2-059: fake-boundary rigor shared by the fix-critic (vets a PROPOSED fix, CR-V2-058) AND the Auditor's
# Verifikácia verdict (vets an IMPLEMENTED boundary). A security fix/boundary is REAL only if the UNATTENDED
# full_auto agent cannot BYPASS it — ONE source so the critic and the verdict never drift. The pre-push-hook
# miss (2026-07-03): the Auditor ran the COMPLIANT `git push` (rejected → "works" → PASS) but not the
# adversarial `git push --no-verify` (succeeds), so it passed a fake boundary. The verdict now carries this.
_FAKE_BOUNDARY_ANTIPATTERNS = (
    "MODEL OPRÁVNENÍ AGENTA: opravné/fixové kolá bežia BEZ DOZORU v móde `bypassPermissions` / `full_auto` — "
    "agent má plné práva a NIKTO nad ním nestojí. Preto hranica, ktorej vynútenie stojí na nástroji, ktorý si "
    "nedozorovaný agent SÁM vie vypnúť/obísť, NIE JE hranica.\n"
    "ANTI-VZORY FALOŠNEJ HRANICE:\n"
    "   a) git pre-commit / pre-push hook ako hranica — nedozorovaný agent ho obíde cez `git push --no-verify` "
    "/ `git commit --no-verify` / `git config --unset core.hooksPath` / úpravou samotného hooku. Falošná hranica.\n"
    "   b) client-side / advisory / „varovací“ guard namiesto enforced-by-construction defaultu (default "
    "hodnota, typ, DB-constraint, capability, odobratý credential) — čo NIE je vynútené konštrukciou, agent obíde.\n"
    "   c) oprava SYMPTÓMU namiesto PRÍČINY — zamaskuje prejav, koreň ostáva.\n"
    "   d) „self-confirming“ test, ktorý len ZAZELENÁ smoke namiesto reálneho preukázania invariantu (negatívny "
    "test chýba, alebo testuje len POSLUŠNÚ cestu, nie adversariálny bypass).\n"
    "ROZHODUJÚCA OTÁZKA: je hranica VYNÚTENÁ KONŠTRUKCIOU (nedozorovaný full_auto agent ju NEVIE obísť), alebo "
    "len hook/guard, ktorý obíde?"
)


def _verifikacia_directive(
    db: Session, version_id: uuid.UUID, *, smoke_block: str = "", flow_type: str = "new_version"
) -> str:
    """The Auditor's END verification brief (Verifikácia phase; CR-V2-014; VERIF-1..VERIF-3, AUD-1(b),
    AUD-2, AUD-3, AUD-6) — the v2 form of v1 ``gate_g``.

    **Fast-fix LIGHT check (CR-V2-028; design §2.5 "light Auditor check — fix works + no regression,
    focused, not the full release oracle"):** for ``flow_type='fast_fix'`` the Auditor runs a FOCUSED check
    scoped to the directive — does the fix WORK and did it introduce NO regression — NOT the full
    adversarial release oracle a ``new_version`` gets. It still emits a ``kind=verdict`` (PASS → verified;
    FAIL → bounded fix loop) and still verifies §4 hard-security holds, but the depth is deliberately
    lighter (the fast-fix value is the short path; a small obvious fix does not warrant the full audit).

    DESIGN-BEARING (flagged for the Manažér): this prompt DEFINES the independent Auditor's END-verification
    behaviour. Drafted from ``nex-studio-v2-design.md`` §2.5 (release verification) + §5.1(2) (Auditor rules
    → "Behavioural acceptance" + "Security verification"). The Auditor's ``Pravidlá agenta`` charter
    (``templates/auditor-charter.md`` §2(b)/§3) carries the matching standing rules; this is the per-turn
    orchestrator injection.

    After Programovanie — before Hotovo — the independent Auditor runs the END check and emits ONE
    ``kind=verdict`` (the CR-V2-006 repurposed findings shape):

      * **Release-acceptance (behavioural pillar):** the engine already ran the built app via
        :func:`_run_release_smoke` against INTERNAL FIXTURES (an ephemeral ``-p <slug>-smoke`` compose
        up/down — NOT a customer instance; deploy is OUT of the pipeline, OQ-3/D6, so "Hotovo" means
        *verified*, not *deployed*). Its boot + acceptance result is fed below (``smoke_block``); the Auditor
        confirms the app does what the brief promised. The Auditor MAY additionally run the app to verify.
      * **Adversarial spot-checks (targeted, NOT per-task):** actively hunt holes in the RISKY parts —
        security, money/calculations, the core contract — verify-don't-trust against the artifacts + the
        running app, not the AI Agent's say-so.
      * **Explicit §4 hard-security verification:** verify the inviolable P0 rules HOLD in code AND logs — no
        credential written to source / committed / leaked to logs; secrets only in ``.env`` / runtime env;
        ``VITE_*`` public-only. A credential leak is a FAIL.
      * **verdict=true (PASS)** — the version is verified (behavioural acceptance + spot-checks + §4 clean).
        ``findings`` may carry non-blocking notes. The Verifikácia end stop is then governed by the dial
        (auto-sign-off to Hotovo at a non-stopping level, else the Manažér signs off).
      * **verdict=false (FAIL)** — list the concrete failures in ``findings`` and the targeted re-run scope
        in ``proposed_fix`` (the salvaged ``surgical_fix`` scope). FAIL loops the fix back to the AI Agent
        (the Auditor FINDS, the AI Agent FIXES — independence), bounded by :data:`AUDITOR_LOOP_MAX` rounds,
        then STOP + escalate to the Manažér.

    Depth is FIXED — always deep + adversarial, INDEPENDENT of the Miera autonómie dial (CR-V2-053, revising
    OQ-9 / AUD-6). The dial governs WHERE the build stops for the Manažér's approval, NOT how hard the release
    gate is checked: the Auditor is the only reliable independent net before Hotovo and the operator
    (Tibor/Nazar) is a non-expert who cannot backstop it — the old "depth scales with oversight" down-scaled
    the gate exactly when it mattered. The brief mandates refute-don't-confirm + an UNCONDITIONAL negative test
    per declared safety property (the risky op MUST be shown to be rejected). ``fast_fix`` keeps its own
    deliberately-focused light lane (a separate flow_type, not a dial level; the mechanical CR-V2-050/051
    floors still bite there)."""
    if flow_type == "fast_fix":
        # Fast-fix LIGHT verifikácia (CR-V2-028; design §2.5): a FOCUSED fix-works + no-regression check
        # scoped to the directive — NOT the full adversarial release oracle. Still emits a verdict, still
        # checks §4 hard-security + the smoke result, just lighter (the lane's value is the short path).
        return (
            "VERIFIKÁCIA — RÝCHLA OPRAVA (nezávislý Auditor, ĽAHKÁ koncová kontrola; NIE plný release oracle).\n"
            "1. Si NEZÁVISLÝ overovateľ MIMO tímu AI Agenta, READ + RUN-ONLY — smieš ČÍTAŤ a SPUSTIŤ appku, "
            "NIKDY neupravuj/necommituj. TY NÁJDEŠ — opravuje AI Agent.\n"
            "2. ZAMERAJ sa na DVE veci (oprava je malá a jednoznačná, nerob plný adverzariálny audit):\n"
            "   a) OPRAVA FUNGUJE — robí appka to, čo smernica (pokyn Manažéra) žiadala? Over to oproti "
            "bežiacej appke / artefaktom, nie oproti slovu AI Agenta.\n"
            "   b) ŽIADNA REGRESIA — nerozbila oprava nič susedné? Engine spustil release smoke (interné "
            "fixtúry) — výsledok je nižšie; zohľadni ho.\n"
            + smoke_block
            + "3. §4 HARD-SECURITY (rýchla, ale POVINNÁ kontrola): žiadny credential pridaný do zdrojáku / "
            "commitnutý / v logoch; secrets len v `.env`/runtime; `VITE_*` len public. Únik = FAIL.\n"
            "4. Vráť `kind=verdict`:\n"
            "   - oprava funguje + bez regresie + §4 čisté → `verdict=true` (PASS).\n"
            "   - inak → `verdict=false` (FAIL); konkrétne zlyhania do `findings`, zameraný rozsah opravy do "
            "`proposed_fix` (NEvykonávaj — opravuje AI Agent, ty re-verifikuješ). FAIL sa vráti do "
            "ohraničenej slučky.\n"
            "Ukonči odpoveď štruktúrovaným stavovým výstupom (F-007-orchestration-cockpit.md §5.3)."
        )
    # CR-V2-053: the END release verification depth is FIXED — always deep + adversarial, INDEPENDENT of the
    # Miera autonómie dial. The dial governs WHERE the build stops for the Manažér's approval, NOT how hard the
    # release gate is checked: the Auditor is the only reliable independent net before Hotovo, and the operator
    # (Tibor/Nazar) is a non-expert who cannot backstop it. The old "depth scales inversely with human
    # oversight" down-scaled the gate exactly when it mattered — removed.
    coverage_brief = _release_coverage_brief(db, version_id)
    return (
        "VERIFIKÁCIA (nezávislý Auditor, koncová kontrola po Programovaní, pred Hotovo).\n"
        "1. Si NEZÁVISLÝ overovateľ MIMO tímu AI Agenta — over z VONKU (žiadny agent sa nevie auditovať sám). "
        "SI READ + RUN-ONLY: smieš ČÍTAŤ a SPUSTIŤ appku na overenie, ale NIKDY neupravuj súbor, nepíš kód "
        "ani necommituj. TY NÁJDEŠ — opravuje AI Agent (zachovaná nezávislosť).\n"
        "2. RELEASE-ACCEPTANCE (behaviorálny pilier): over, že appka robí to, čo brief sľúbil. Engine už "
        "spustil appku proti INTERNÝM FIXTÚRAM (nie zákazníckej inštancii — deploy je mimo pipeline; "
        "„Hotovo“ = overené, nie nasadené) — výsledok je nižšie. Zohľadni ho v synthéze.\n"
        + smoke_block
        + "3. REFUTUJ, NEPOTVRDZUJ (rovnaká PLNÁ hĺbka VŽDY — nezávisle od Miery autonómie): predpokladaj, že "
        "build je CHYBNÝ, kým sám nedokážeš opak. NEDÔVERUJ zeleným testom AI Agenta — over ich SÁM oproti "
        "bežiacej appke. Aktívne LOV diery v RIZIKOVÝCH častiach (bezpečnosť, peniaze/výpočty, hlavný "
        "kontrakt); verify-don't-trust oproti artefaktom a bežiacej appke, NIE oproti slovu AI Agenta.\n"
        "4. NEGATÍVNE / BEZPEČNOSTNÉ OVERENIE (POVINNÉ, bez ohľadu na dial): pre KAŽDÝ deklarovaný bezpečnostný "
        "invariant SÁM SPUSTI zakázanú operáciu a over, že je SKUTOČNE ODMIETNUTÁ (červený-keď-zneužitá test). "
        "Zelený „funguje to“ test bezpečnostný invariant NEDOKÁŽE — len negatívny. Nepokrytý invariant = FAIL. "
        "Ak deklarácia vyzerá NEÚPLNÁ (chýba zjavný invariant — autentifikácia, autorizácia/scoping, injection, "
        "nebezpečné príkazy/oprávnenia), SPOCHYBNI ju a daj nález.\n"
        + coverage_brief
        + "4b. FALOŠNÉ HRANICE (CR-V2-059 — POVINNÉ pri bezpečnostných opravách/hraniciach): keď je invariant "
        "HRANICA (git-push gate, oprávnenia, capability), NESTAČÍ overiť POSLUŠNÚ cestu — otestuj aj "
        "ADVERSARIÁLNY BYPASS ako nedozorovaný full_auto agent. Hranica, ktorú takto obíde, je FALOŠNÁ → FAIL, "
        "aj keď poslušný negatívny test prejde.\n"
        + _FAKE_BOUNDARY_ANTIPATTERNS
        + "\n"
        + "5. §4 HARD-SECURITY (explicitne): over, že P0 pravidlá držia v KÓDE aj v LOGOCH — žiadny credential "
        "v zdrojáku / commitnutý / v logoch; secrets len v `.env`/runtime env; `VITE_*` len public hodnoty. "
        "Únik credentialu je FAIL.\n"
        "6. Vráť `kind=verdict`:\n"
        "   - ak je verzia overená (acceptance + negatívne bezpečnostné testy + spot-checky + §4 čisté) → "
        "`verdict=true` (PASS); do `findings` daj prípadné neblokujúce poznámky.\n"
        "   - ak nájdeš zlyhanie → `verdict=false` (FAIL); konkrétne zlyhania vymenuj v `findings` a do "
        "`proposed_fix` napíš ZAMERANÝ rozsah opravy pre AI Agenta (NEvykonávaj ho — opravuje AI Agent, ty "
        "re-verifikuješ). FAIL sa vráti AI Agentovi do ohraničenej slučky.\n"
        "Ukonči odpoveď štruktúrovaným stavovým výstupom (F-007-orchestration-cockpit.md §5.3)."
    )


# CR-V2-058 Part B: the fix-critic carries its narrowed JSON in the SAME ``<<<TASK_PLAN_JSON>>>`` sentinel
# fence the task_plan passes use (``extract_task_plan_json`` is a generic fence extractor; ``structured_output``
# is dead in this CLI). The fence must pin the EXACT field names of :class:`FixCritique` and forbid extras.
_FIX_CRITIQUE_FENCE_RULE = (
    "Výstup vráť VÝHRADNE ako jeden JSON objekt vnútri tohto sentinel bloku (nič iné okolo, žiaden "
    "markdown, žiaden komentár):\n<<<TASK_PLAN_JSON>>>\n{…}\n<<<END_TASK_PLAN_JSON>>>\n"
    "Použi PRESNE tieto tri polia a ŽIADNE iné: `verdict` (jedno z: accept, narrow, reject), "
    "`corrected_scope` (text; pri `narrow` POVINNE zúžený/opravený rozsah opravy, inak prázdny reťazec) a "
    "`why` (text — POVINNÉ zdôvodnenie verdiktu; bez neho sa kritika zahodí).\n"
    'Príklad tvaru:\n<<<TASK_PLAN_JSON>>>\n{"verdict":"reject","corrected_scope":"",'
    '"why":"pre-push hook nie je hranica — nedozorovaný full_auto fixer ho obíde cez git push --no-verify; '
    'príčina je inde (default write_commit namiesto push)."}\n<<<END_TASK_PLAN_JSON>>>'
)


def _fix_critique_directive(db: Session, version_id: uuid.UUID, *, verdict_msg: PipelineMessage) -> str:
    """CR-V2-058 Part B — the independent fix-critic's brief: adversarially REFUTE the Auditor's proposed FIX
    (the CURE), NOT the build (CR-V2-053 "REFUTUJ, NEPOTVRDZUJ" pointed at the remedy). The critic is a
    separate AUDITOR_ROLE turn; it must NOT re-judge whether the build passes — it judges whether the
    PROPOSED FIX is a REAL, enforced-by-construction boundary or a fake one.

    Self-audit fix: the brief MUST carry the FIXER's permission model, else it only catches a fake boundary by
    luck. Fix rounds run UNATTENDED in ``bypassPermissions`` / ``full_auto`` — so a fix whose safety rests on a
    tool the fixer itself can turn off (a git hook bypassed with ``--no-verify``; an advisory/client-side
    guard) is NO boundary. The brief enumerates those anti-patterns and asks the decisive question."""
    payload = verdict_msg.payload or {}
    proposed_fix = str(payload.get("proposed_fix") or "").strip() or "(Auditor nedodal explicitný proposed_fix.)"
    findings = payload.get("findings") or []
    findings_block = "\n".join(f"   - {f}" for f in findings) if findings else "   (bez vymenovaných nálezov)"
    return (
        "PREVERENIE NAVRHNUTEJ OPRAVY (nezávislý kritik — REFUTUJ LIEK, nie build).\n"
        "1. Auditor (nálezca) našiel vo Verifikácii zlyhanie a NAVRHOL opravu. TVOJA JEDINÁ úloha je "
        "adversariálne PREVERIŤ TEN NÁVRH OPRAVY — nie znovu posudzovať, či build prešiel (to už padlo). "
        "Nálezca navrhol rozsah; ty ako NEZÁVISLÝ kritik posúď, či ten liek naozaj lieči a či drží.\n"
        f"2. NÁLEZY AUDITORA:\n{findings_block}\n"
        f"   NAVRHNUTÁ OPRAVA (proposed_fix):\n   {proposed_fix}\n"
        "3. "
        + _FAKE_BOUNDARY_ANTIPATTERNS
        + "\n4. Ak návrh sedí na niektorý anti-vzor → `narrow` alebo `reject`. Ak vieš lepší, skutočne vynútený "
        "enforced-by-construction default, daj ho do `corrected_scope`.\n"
        "5. Vráť verdikt:\n"
        "   - `accept` — oprava je reálna, vynútená konštrukciou, lieči príčinu.\n"
        "   - `narrow` — v jadre správna, ale rozsah treba zúžiť/opraviť; napíš opravený rozsah do "
        "`corrected_scope`.\n"
        "   - `reject` — falošná hranica / symptómová oprava / koreň je inde; `why` vysvetlí prečo je zlá a kam "
        "koreň patrí.\n"
        "Keď si NEISTÝ, prikloň sa k `reject`/`narrow` (bezpečnejšie — nepreverená oprava sa nesmie odporučiť).\n"
        + _FIX_CRITIQUE_FENCE_RULE
    )


# E5 (CR-NS-045): the per-task human-effort estimate is the metrics page's human-baseline source — kept
# in BOTH task_plan prompts below (skeleton → feat-level Σ; per-feat → per-task), advisory, never blocking.
_TASK_PLAN_ESTIMATE_NOTE = (
    "`estimated_minutes` = realistický odhad práce pre schopného ĽUDSKÉHO vývojára v minútach "
    "(NIE čas AI výpočtu); ADVISORY pole — chýbajúci odhad je povolený a NIKDY neblokuje build."
)
# D1 (release-smoke-boot-and-batch-fixes.md): the AI drifted to writing per-version "EPIC 1 — …" / "EPIC 2 — …"
# prefixes INTO epic/feat titles, so the cockpit showed a confusing double number ("8. EPIC 1 — …") on top of
# the DB's own continuous numbering. FORBID it: the title is the NAME only — the system numbers epics/feats.
_TASK_PLAN_TITLE_RULE = (
    "`title` je IBA NÁZOV (napr. „Základ appky“) — NEPREfixuj ho číslom, poradím ani „EPIC N“/„FUNKCIA N“; "
    "epiky aj funkcie čísluje systém sám."
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
    '{"epics":[{"title":"Foundation","plain_description":"Základ appky — databáza a spoločné pravidlá.",'
    '"feats":[{"title":"Schéma a migrácie","description":"DB schéma + audit log",'
    '"plain_description":"Založíme databázu a záznam o zmenách.","estimated_minutes":120}]}],'
    '"cross_cutting_rules":"Spoločná transakčná hranica; immutable audit; scoping na firmu.",'
    '"flagship_features":["Export faktúry do Peppol XML","Automatické párovanie dodávateľa"],'
    '"safety_properties":[{"name":"Scoping na firmu (žiadny cross-tenant read)",'
    '"risky_op":"GET /api/faktury inej firmy vráti dáta"}]}\n'
    "<<<END_TASK_PLAN_JSON>>>"
)
_FEAT_TASKS_EXAMPLE = (
    "Príklad tvaru:\n<<<TASK_PLAN_JSON>>>\n"
    '{"tasks":[{"title":"GL tabuľky","task_type":"migration","description":"hlavná kniha + saldokonto",'
    '"plain_description":"Pripravíme tabuľky hlavnej knihy.",'
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
        "Objekt má pole `epics` (zoznam): KAŽDÝ epik má `title`, `plain_description` a pole "
        "`feats` (zoznam, ≥1) — KAŽDÁ funkcia má `title`, `description`, `plain_description` a "
        "`estimated_minutes` (Σ odhadov jej úloh). `plain_description` je JEDNORIADKOVÉ ľudské vysvetlenie "
        "BEZ žargónu (čo daný epik/funkcia znamená pre Manažéra — nie technický popis); epik `description` "
        "NEMÁ, takže `plain_description` je jeho jediný ľudský text. "
        + _TASK_PLAN_TITLE_RULE
        + " Navrch objektu pole `cross_cutting_rules` "
        "(markdown, regulované invarianty knihy, kodifikované RAZ). Úlohy NEemituj — doplnia sa v ďalších "
        "prechodoch po jednej funkcii.\n"
        # CR-V2-052: the release-coverage declaration the risk-floored oracle (CR-V2-051) enforces — every
        # flagship feature needs a FEATURE assertion, every safety property a NEGATIVE assertion at Verifikácia.
        "Navrch objektu aj pole `flagship_features` (zoznam textov, ≥1): kľúčové funkcie, ktoré MUSÍ vydanie "
        "PREUKÁZATEĽNE robiť — release oracle vyžaduje ≥1 pozitívnu (FEATURE) akceptačnú skúšku na každú. "
        "A pole `safety_properties` (zoznam objektov {`name`,`risky_op`}): bezpečnostné invarianty, ktoré appka "
        "MUSÍ VYNÚTIŤ — `risky_op` je konkrétna ZAKÁZANÁ operácia, ktorú oracle vyžaduje otestovať NEGATÍVNE "
        '(musí byť ODMIETNUTÁ; zelený „funguje to" test bezpečnostný invariant nikdy nedokáže). Vymenuj ich '
        "POCTIVO (autentifikácia, autorizácia/scoping, injection, nebezpečné príkazy, …); prázdny zoznam iba ak "
        "appka naozaj nemá žiadny bezpečnostný invariant — Auditor prázdnu deklaráciu spochybní.\n"
        # CR-V2-036: the skeleton pass decides the FEAT COUNT, so the coarse-granularity rule MUST live here
        # (not only in the per-feat task pass — too late). Without it the agent over-decomposed (46 feats >
        # the hard cap) and the engine rejected the plan.
        "GRANULARITA KOSTRY JE HRUBOZRNNÁ — modul ≈ úloha (F-007 §4): zlučuj súvisiace veci do JEDNEJ "
        f"funkcie, nedeľ koherentný modul na drobné, a drž CELKOVÝ počet funkcií VÝRAZNE POD {MAX_PLAN_FEATS} "
        "(tvrdý strop — jemnejší rozklad engine ODMIETNE a budeš musieť kostru prerobiť). "
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
        "docs), `description`, `plain_description`, `checklist_type` (text alebo null), `priority` "
        "(normal | high | urgent) a `estimated_minutes`. `plain_description` je JEDNORIADKOVÉ ľudské "
        "vysvetlenie úlohy BEZ žargónu (čo robí pre Manažéra — nie technický `description`). Granularita "
        "HRUBOZRNNÁ — modul ≈ úloha (F-007 §4); nedeľ koherentný modul. "
        + _TASK_PLAN_ESTIMATE_NOTE
        + "\n\n"
        + _TASK_PLAN_FENCE_RULE
        + "\n\n"
        + _FEAT_TASKS_EXAMPLE
    )


# (CR-V2-028: the v1 ``_prepend_fast_fix_directive`` helper is RETIRED. It prepended the Director directive
# onto the Coordinator's FRESH-session kickoff brief — but in v2 the fast-fix directive rides in as the
# kickoff message CONTENT (``apply_action`` ``start`` sets ``kickoff_content = directive`` for ``fast_fix``),
# so it is already in the AI Agent's warm Príprava session; there is no separate Coordinator kickoff turn to
# prepend onto. The lightweight fast-fix Príprava brief — :func:`_priprava_directive` ``flow_type='fast_fix'``
# — points the AI Agent at that in-session directive directly.)


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


# NOTE (CR-V2-021): the v1 ``latest_coordinator_report`` (Coordinator gate_report, fed the removed
# ``apply_coordinator_recommendation`` action) and ``_latest_customer_gate_report`` (Customer ``gate_e``
# boundary signals) are REMOVED with the v1 board route — both queried retired ``coordinator``/``customer``
# author + ``gate_e`` stage tokens the v2 DB CHECK rejects, and neither had a live referrer.


def _latest_uat_deploy(db: Session, version_id: uuid.UUID) -> Optional[dict[str, Any]]:
    """The most recent ``uat_deploy`` notification payload for a version, or ``None`` if no UAT deploy was
    ever attempted (v0.8.1 CR-2).

    A UAT deploy records a ``{"uat_deploy": {...}}`` ``system→manazer`` notification — a real success
    (``{ok: True}``), a failure (``{ok: False}``), or a skip (``{skipped: True}``). This reports HONESTLY
    whether a UAT was ACTUALLY deployed, instead of the ``uat_slug`` proxy (which lies when a configured
    slug's compose is gone — CR-1 honest-skips, yet the slug stays set). Ordered by the monotonic ``seq``
    so the latest deploy outcome wins; ``None`` when no deploy was ever recorded.

    (CR-V2-028 NOTE: the in-pipeline auto-deploy that wrote these notes — v1 ``_release_auto_uat_deploy`` /
    ``_fast_fix_auto_deploy`` — is RETIRED; deploy is OUT of the pipeline, manual + per-customer, OQ-3/D6.
    This helper + its siblings are v1 release-stage leftovers pending removal with the deploy-subsystem
    cleanup, NOT called from the v2 lane.)"""
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


# (CR-V2-028: the v1 ``_UAT_DEPLOYING_FLOWS`` set is RETIRED — it gated the v1 no-silent-done-WITHOUT-UAT
# guard, which is itself superseded by the v2 no-silent-done-WITHOUT-VERIFICATION invariant
# (:func:`_verifikacia_passed`): deploy is OUT of the pipeline (OQ-3/D6), so Hotovo means *verified*, not
# *deployed*, and there is no in-pipeline UAT deploy to gate. It also referenced the dropped ``cr``/``bug``
# flows (CR-V2-031). The set had no live referrer after the v1 release stage moved to the deploy subsystem.)


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


# NOTE (CR-V2-021): the v1 ``_gate_e_open_findings`` deterministic gap counter (raised by a Designer
# ``gap_found`` answer, resolved by a Director ``fix``/``leave``) is REMOVED with the v1 board route — it read
# the retired ``gate_e`` stage + ``designer``/``director`` author tokens the v2 DB CHECK rejects, and its only
# referrer was the v1 ``_board()`` close-gate. The v2 Auditor upfront review surfaces findings on its own turn.


# (CR-V2-013: the Gate-E per-question budget machinery — ``_gate_e_spec_footprint_lines`` /
# ``_gate_e_question_budget`` / ``_gate_e_question_count`` + the ``_GATE_E_*`` floor/ceiling/topic-slack
# constants — is REMOVED with the rest of the Gate-E sub-state-machine. The v2 Auditor's UPFRONT review
# (after Návrh) is ONE invocation, not a budgeted Customer↔Designer question loop, so there is no
# per-question budget to scale; its DEPTH scales with the dial via :func:`auditor_effort_for_level`.)


def auto_chain_limit(db: Session, version_id: uuid.UUID) -> int:
    """Upper bound for the runner's auto-chain backstop (:mod:`backend.services.pipeline_runner`).

    FINAL 4-phase bound (R-AUTOCHAIN, finalized CR-V2-014). The v1 bound budgeted the full 11-stage waterfall
    PLUS the Gate-E self-loop question ceiling PLUS topic slack — but the 4-phase model has NO Gate-E
    self-loop, so that slack is dropped. The only non-monotonic loop is the Auditor's bounded fix↔re-verify
    cycle, which CR-V2-014 implemented (:func:`_run_verifikacia_round` → :func:`_settle_verifikacia_verdict`):
    a Verifikácia FAIL re-enters Programovanie then Verifikácia → TWO phase steps per round, up to the named
    :data:`AUDITOR_LOOP_MAX` rounds. The bound therefore budgets the monotonic phase advance
    (``len(STAGE_ORDER)``) PLUS ``2 * AUDITOR_LOOP_MAX`` so a legitimately long (but bounded) Auditor loop —
    a full 5-round fix↔re-verify — NEVER mis-trips the runner backstop; only a true runaway (the loop's own
    bound failed) ever hits it. fast_fix is unaffected (its chain is ≤3, far under any bound). The
    ``db``/``version_id`` args are kept (the runner calls it per-version) for a future per-build margin."""
    # Each Auditor FAIL round re-enters Programovanie then Verifikácia → 2 phase steps per round; budget the
    # named AUDITOR_LOOP_MAX such rounds (R-AUTOCHAIN final term) on top of the monotonic phase advance.
    return len(STAGE_ORDER) + 2 * AUDITOR_LOOP_MAX


def _verifikacia_passed(db: Session, version_id: uuid.UUID) -> bool:
    """Whether the Auditor's LATEST Verifikácia verdict is PASS (CR-V2-009 — no-silent-done invariant).

    Hotovo is reachable ONLY through a recorded Auditor PASS verdict at Verifikácia: ``schvalit`` at the
    Verifikácia end-stop is gated on this, never a silent sign-off. Deterministic from the message log —
    the most recent ``stage=verifikacia`` ∧ ``kind=verdict`` message whose ``payload.verdict == 'PASS'``.
    (v2 form of the v1 ``no-silent-done-without-UAT`` safeguard: deploy is OUT of the pipeline — D6/OQ-3 —
    so the gate becomes ``no-silent-done-without-VERIFICATION``.)

    CR-V2-055 — RE-JUDGE ON ESCALATION: a prior PASS is STALE once a fix is directed after it. The gate reads
    the latest of ``{verdict, return}`` (a ``manazer→ai_agent`` verifikacia ``return`` is an operator fix
    directive — an 'Uprav' or an escalation Decision Card, CR-V2-054). If that latest message is a ``return``
    (a fix pending), a PASS can NO LONGER sign off — a FRESH adversarial Auditor re-run must produce a new
    PASS first. This forces the fresh re-judge the deep analysis called for (a stale PASS can never cross an
    escalation)."""
    latest = db.execute(
        select(PipelineMessage)
        .where(
            PipelineMessage.version_id == version_id,
            PipelineMessage.stage == "verifikacia",
            PipelineMessage.kind.in_(("verdict", "return")),
        )
        .order_by(PipelineMessage.seq.desc())
        .limit(1)
    ).scalar_one_or_none()
    if latest is None or latest.kind != "verdict":
        return False  # no verdict yet, or a fix directive is newer than the last verdict → re-judge pending
    return bool(latest.payload and latest.payload.get("verdict") == "PASS")


def _manazer_signoff(db: Session, version_id: uuid.UUID) -> Optional[dict[str, Any]]:
    """The Manažér's TERMINAL Hotovo signature payload for this version, or ``None`` when there is none / it is
    STALE (STEP 6, step6-hotovo-design.md MD-1=A).

    A conversation build reaches deployability through a manager signature (:func:`apply_action` ``hotovo``), NOT
    an Auditor verdict: the signature is ONE ``stage='priprava'`` ∧ ``kind='notification'`` ∧ ``payload.hotovo``
    message carrying the anchored ``hotovo_sha``. Returns that payload so :func:`version_verified` can apply the
    SAME SHA-anchor ladder the verdict path uses. STALE-AWARE (mirror CR-V2-055 / :func:`kontrola_done`): if a
    FRESHER Programovanie-complete notification outranks the signature (a re-build landed AFTER the signoff), the
    old signature no longer counts → ``None`` (the version must be re-checked + re-signed). Legacy (mode NULL /
    Auditor) builds never record this marker → ``None`` → :func:`version_verified` falls through to the unchanged
    verdict path byte-identically."""
    row = db.execute(
        select(PipelineMessage.seq, PipelineMessage.payload)
        .where(
            PipelineMessage.version_id == version_id,
            PipelineMessage.stage == "priprava",
            PipelineMessage.kind == "notification",
            PipelineMessage.payload["hotovo"].astext == "true",
        )
        .order_by(PipelineMessage.seq.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    prog_seq = _latest_programming_complete_seq(db, version_id)
    if prog_seq is not None and prog_seq > row.seq:
        return None  # a fresh build landed AFTER the signoff → stale, must re-check + re-sign
    return row.payload or {}


def version_verified(db: Session, version_id: uuid.UUID, *, head: Optional[str] = None) -> tuple[bool, str]:
    """CR-V2-056 (layer-1 reality-anchoring): is a version VERIFIED *right now*, COMPUTED from real git state
    — not a stored ``done`` snapshot. A version is verified iff its latest Verifikácia PASS verdict is bound
    to a commit SHA that STILL equals the repo HEAD, so a HEAD change past the verified commit AUTO-UN-VERIFIES
    (kills the frozen-PASS bug: the board shows a stale PASS + HEAD X while git moved to Y).

    Returns ``(is_verified, provenance)``. TOTAL function (never raises, always a definite answer — a flaky
    git read never silently un-verifies):
      * ``status == 'released'`` → ``(True, 'released')`` — a shipped release is an immutable, already-shipped
        record; its verification happened at release and NO post-release commit may un-verify it (checked
        FIRST, before any SHA/HEAD comparison). This is the general fix for the §3.6 graduation note-move
        (deploy._move_release_note_dir) — and any future maintenance commit — advancing HEAD past the anchored
        ``verified_sha``/``hotovo_sha``: without it the just-graduated ``v1.0.0`` would read ``sha_drift`` and
        drop out of ``list_verified_versions``, hard-blocking every later deploy (2nd customer / redeploy) of
        a version that can no longer be re-verified. Guardrail: ONLY ``released`` short-circuits — every
        non-released status keeps the byte-identical drift detection below (the real safeguard that catches
        code changing after a Verifikácia PASS).
      * no PASS on record, or a fix directive is newer (CR-V2-055) → ``(False, 'no_pass')``.
      * PASS with ``verified_sha == 'legacy'`` (pre-anchoring backfill) → ``(True, 'legacy')`` — grandfathered.
      * PASS with no ``verified_sha`` (repo unreadable at PASS time, so never anchored) → ``(True, 'unbound')``.
      * repo unreadable NOW (``head is None``) → ``(True, 'repo_unreadable')`` — our own read failure never
        un-verifies a version.
      * ``verified_sha == head`` → ``(True, 'sha_match')``; else → ``(False, 'sha_drift')``.

    ``head`` may be supplied by the caller (batch: read HEAD ONCE per project, compare each version's stored
    SHA in DB) to avoid a git subprocess per version on list endpoints.

    NOTE: the CI-green AND-leg (verified also requires green CI on the tagged commit for remote projects) is a
    clean follow-on increment on top of this SHA anchor; it is NOT applied here.

    STEP 6 (step6-hotovo-design.md MD-1=A): a CONVERSATION build reaches deployability through a Manažér Hotovo
    SIGNATURE (:func:`_manazer_signoff`), NOT an Auditor verdict — the signature REPLACES the verdict. When one is
    present (and fresh), apply the SAME SHA-anchor ladder to its ``hotovo_sha`` and short-circuit; provenance
    strings are the ``hotovo_*`` variants so the FE can tell a manager signoff from an Auditor PASS. Legacy
    builds have no signature → fall through to the UNCHANGED verdict path byte-identically."""
    # A RELEASED version is verified BY DEFINITION — checked FIRST, before any SHA/HEAD drift comparison. The
    # §3.6 graduation note-move commit (or any later maintenance commit) advances HEAD past the anchored SHA;
    # a shipped, immutable release must never un-verify from that. ONLY 'released' short-circuits — the drift
    # detection below is unchanged for every non-released status (the real guardrail).
    version = db.get(Version, version_id)
    if version is not None and version.status == "released":
        return True, "released"
    signoff = _manazer_signoff(db, version_id)
    if signoff is not None:
        pass_sha = signoff.get("hotovo_sha")
        if not pass_sha:
            return True, "hotovo_unbound"  # signed while repo unreadable → never anchored; do not un-verify
        if head is None:
            head = _repo_head(claude_agent.PROJECTS_ROOT / _project_slug_for_version(db, version_id))
        if head is None:
            return True, "repo_unreadable"  # our own read failure — never un-verifies
        return (pass_sha == head), ("hotovo_match" if pass_sha == head else "hotovo_drift")
    if not _verifikacia_passed(db, version_id):
        return False, "no_pass"
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
    pass_sha = (latest.payload or {}).get("verified_sha") if latest else None
    if pass_sha == "legacy":
        return True, "legacy"  # backfilled pre-Layer-1 version — trusted as-was, not recomputed
    if not pass_sha:
        return True, "unbound"  # PASS never got a SHA anchor (repo unreadable at PASS) → do not un-verify
    if head is None:
        head = _repo_head(claude_agent.PROJECTS_ROOT / _project_slug_for_version(db, version_id))
    if head is None:
        return True, "repo_unreadable"  # our own read failure — never un-verifies
    return (pass_sha == head), ("sha_match" if pass_sha == head else "sha_drift")


# (CR-V2-013: the Gate-E milestone / gap / coverage helpers — ``_gate_e_coverage_complete``,
# ``_latest_designer_answer``, ``_latest_gate_e_milestone``, ``_latest_coordinator_message_content``,
# ``_gate_e_gap_open`` — and the Gate-E audit-markdown writers — ``_GATE_E_ROLE_SK``,
# ``gate_e_audit_markdown``, ``_write_gate_e_audit`` — are REMOVED with the rest of the Gate-E
# sub-state-machine. The 4-phase model has no Customer↔Designer↔Director Gate-E thread to mine: the v2
# Auditor's upfront review (after Návrh) emits its findings as ONE ``verdict`` message — see
# :func:`_run_auditor_upfront_review` — and the durable record is that message + the Návrh tab, not a
# separate customer-dialogue.md.)


def _render_task_plan_md(db: Session, version: Version, project: Project, stage: str = "navrh") -> str:
    """Render the version's materialized Epic/Feat/Task rows to a reviewable markdown plan.

    In the Návrh phase (``stage='navrh'``, the default — legacy byte-identical) this is the LAST part of
    the Návrh design doc (CR-V2-011): the Manažér (+ the independent Auditor) review it against the design
    at the post-Návrh schvaľovací bod. In the STEP-3 conversation register (any other ``stage`` — the plan
    is composed straight from the approved Špecifikácia, no Návrh phase, no independent Auditor gate before
    a build) the header DROPS the "fáza Návrh / nezávislému Auditorovi / pred stavbou" clause so the doc's
    provenance line is honest (step3-plan-design.md, Task 2). Re-queried from the DB rows so the displayed
    hierarchical numbers match the cockpit."""
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
    if stage == "navrh":
        provenance = (
            "> Generované automaticky z plánu úloh fázy Návrh (zdroj pravdy = cockpit DB rows). Slúži Manažérovi "
            "(a nezávislému Auditorovi) na overenie plánu proti návrhu pred stavbou. Needituj ručne — pri ďalšom "
            "behu Návrhu sa prepíše."
        )
    else:
        # STEP-3 conversation register — the plan is composed from the approved Špecifikácia in the live 1:1,
        # not the Návrh phase; drop the phase/independent-Auditor/pre-build clause (step3-plan-design.md).
        provenance = (
            "> Generované automaticky z plánu úloh (zdroj pravdy = cockpit DB rows). Needituj ručne — pri ďalšej "
            "úprave plánu sa prepíše."
        )
    header = [
        f"# {project.slug} — Plán úloh v{version.version_number}",
        "",
        provenance,
        "",
        f"**Súhrn:** {n_epics} epicov · {n_feats} featov · {n_tasks} úloh · odhad ~{total_min} min (~{hours} h).",
        "",
    ]
    return "\n".join(header + body).rstrip() + "\n"


def _write_task_plan_doc(db: Session, version: Version, stage: str = "navrh") -> Optional[str]:
    """Write the materialized task plan to ``spec/task-plan.md`` in the project repo
    so it is a reviewable artefact (not DB-only). Skips cleanly (``None``) when the
    project has no ``source_path`` (no checkout to write into — tests / library
    projects). Returns a failure reason (→ caller records ``blocked``) only when a
    checkout exists but the write fails — a checked-out project's plan is not "done"
    without its reviewable doc (2026-06-22 process-gap fix). ``stage`` selects the
    doc's provenance register (:func:`_render_task_plan_md`); default ``navrh`` is
    legacy byte-identical."""
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
        md = _render_task_plan_md(db, version, project, stage)
        doc_path.parent.mkdir(parents=True, exist_ok=True)
        doc_path.write_text(md, encoding="utf-8")
    except OSError as exc:
        return f"task-plan doc write failed: {exc}"
    return None


def _priprava_spec_disk_status(db: Session, state: PipelineState) -> tuple[Optional[str], str]:
    """Pure disk-check core of the Príprava Špecifikácia verify — NO notification / DB write side effect.

    Returns ``(rel, status)`` where ``rel`` is the repo-relative ``specification.md`` path
    (:func:`_priprava_spec_rel`, ``None`` only when the version row itself is gone) and ``status`` is:

      * ``'ok'``          — a checkout exists and ``specification.md`` is present on disk;
      * ``'no_checkout'`` — the project has no ``source_path`` (tests / library projects) — the spec lives
                            only in the DB audit trail; the gate path treats this as a pass;
      * ``'missing'``     — a checkout EXISTS but ``specification.md`` is absent (a real failure);
      * ``'no_version'``  — the version row does not exist (``rel`` is ``None``).

    Extracted so the single on-disk source-of-truth check lives in EXACTLY one place: the Príprava gate
    path (:func:`_persist_priprava_spec`) and the conversation ``approve_spec`` path both call it. This
    helper is intentionally free of ``_record_message`` — the caller decides what (if anything) to record.
    """
    version = db.get(Version, state.version_id)
    if version is None:
        return None, "no_version"
    rel = _priprava_spec_rel(version.version_number)
    project = db.get(Project, version.project_id)
    if project is None or not project.source_path:
        return rel, "no_checkout"
    spec_path = Path(project.source_path) / rel
    if not spec_path.exists():
        return rel, "missing"
    return rel, "ok"


def _persist_priprava_spec(db: Session, state: PipelineState, block: PipelineStatusBlock) -> Optional[str]:
    """Persist + verify the Príprava Špecifikácia artifact at the end of the Príprava dialogue (CR-V2-010,
    PREP-3). Returns a failure reason (→ caller settles ``blocked``, the phase does NOT close) or ``None``.

    The AI Agent writes the Špecifikácia Markdown to disk itself (it has Write tools in its warm session)
    and lists it in ``deliverables[]``; this is the deterministic mechanical gate that the artifact is real
    + readable (the Vývoj → Príprava tab reads this record), the Príprava analogue of ``_write_task_plan``
    for Návrh. The on-disk verify reuses the spec-tree convention (:func:`_priprava_spec_rel`), delegated
    to the notif-free :func:`_priprava_spec_disk_status` core (shared with the conversation approval path).

    No-op pass (``None``) when the project has no checkout to write into (tests / library projects) — the
    spec then lives only as the recorded ``report`` payload of the gate_report message (DB audit trail),
    which is still readable. A checkout that EXISTS but is missing the spec file is a real failure: the
    Špecifikácia phase is not "done" without its reviewable artifact.
    """
    rel, status = _priprava_spec_disk_status(db, state)
    if status == "no_version":
        return "version not found for Špecifikácia write"
    if status == "no_checkout":
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
    if status == "missing":
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


def _write_task_plan(
    db: Session,
    state: PipelineState,
    block: PipelineStatusBlock,
    stage: str = "navrh",
    metrics_phase: Optional[str] = None,
) -> Optional[str]:
    """Materialize the AI Agent's task-plan decomposition into Epic/Feat/Task rows.

    F-007 §5 / CR-NS-020 CR-2; v2 CR-V2-011 (the plan folds into the Návrh design doc); STEP 3 re-homes it
    to the ``priprava`` conversation register too (step3-plan-design.md). The deterministic mechanical gate
    for the task plan (replaces the disk-deliverable ``verify_mechanical`` — the plan's deliverable is DB
    rows, not files). Returns a failure reason (→ ``status=blocked``, nothing written) or ``None`` on success.

    **Idempotent replace + atomic:** a Manažér ``uprav`` (Návrh) or a repeat ``zostav_plan`` (conversation,
    MD-2) re-dispatches the AI Agent, which re-runs this; we drop the version's existing epics first (FK
    cascade → feats/tasks) so a re-plan never duplicates — the plan is rebuilt in place. The whole replace
    runs in a SAVEPOINT — any failure rolls back the rows while the caller still records ``blocked`` (never
    a half-written plan). Numbers are service-assigned (MAX+1); status is forced (planned/todo — the AI Agent
    never pre-marks done); ``plain_description`` (STEP 3) is carried through; ``baseline_sha`` / ``task_count``
    / ``auto_fix_count`` stay untouched (CR-3 owns them). ``stage`` threads the honest phase into the
    notification's stage column + payload ``phase`` + the reviewable doc's provenance; default ``navrh`` is
    legacy byte-identical. ``metrics_phase`` (metrics-v3-three-phases.md Part 1) overrides ONLY the metrics
    ``payload['phase']`` stamp (the conversation plan round passes ``navrh`` while ``stage`` stays
    ``priprava``); ``None`` keeps ``phase == stage`` — byte-for-byte the pre-STEP-3 behaviour.
    """
    phase = metrics_phase if metrics_phase is not None else stage
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
                        plain_description=epic_in.plain_description,
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
                            plain_description=feat_in.plain_description,
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
                                plain_description=task_in.plain_description,
                                checklist_type=task_in.checklist_type,
                                priority=task_in.priority,
                                estimated_minutes=task_in.estimated_minutes,
                            ),
                        )
                        n_tasks += 1
    except (ValueError, ValidationError, IntegrityError) as exc:
        return f"plan write failed: {exc}"

    # Materialize the plan as a reviewable doc (spec/task-plan.md) — not DB-only —
    # so the Manažér (+ the independent Auditor, in the Návrh register) can verify it before the build.
    doc_err = _write_task_plan_doc(db, version, stage)
    if doc_err is not None:
        return doc_err

    _record_message(
        db,
        version_id=state.version_id,
        stage=stage,  # CR-V2-011: navrh (task plan = last part of the design doc); STEP 3: priprava (conversation)
        author="system",
        recipient="manazer",
        kind="notification",
        content=f"Plán úloh zapísaný: {n_epics} epicov, {n_feats} featov, {n_tasks} taskov. Doc: spec/task-plan.md.",
        payload={"task_plan_summary": {"epics": n_epics, "feats": n_feats, "tasks": n_tasks}, "phase": phase},
    )
    return None


def _latest_consultation(db: Session, version_id: uuid.UUID) -> Optional[tuple[dict[str, Any], int]]:
    """CR-V2-041: the ``consultation`` payload (id / intro / source / decisions[]) + its message ``seq`` of
    the LATEST kind=consultation message, or ``None``. The decision queue + the recorded ``decide`` answers
    ARE the consultation's whole state — the "current" decision is derived (first ``decision.key`` with no
    answer), so there is no mutable cursor column to drift.

    Returns the seq so answers are SEQ-scoped (decide-records with a higher seq belong to THIS consultation):
    a re-consultation gets a new, higher-seq message, so correctness never depends on the agent-supplied
    ``consultation.id`` being unique (verify-round blocker fix)."""
    row = db.execute(
        select(PipelineMessage.payload, PipelineMessage.seq)
        .where(PipelineMessage.version_id == version_id, PipelineMessage.kind == "consultation")
        .order_by(PipelineMessage.seq.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    payload, seq = row
    c = payload.get("consultation") if isinstance(payload, dict) else None
    return (c, seq) if isinstance(c, dict) and c.get("decisions") else None


def _consultation_answers(db: Session, version_id: uuid.UUID, after_seq: int) -> dict[str, dict[str, Any]]:
    """CR-V2-041: map ``decision.key`` → its recorded ``decide`` answer for the consultation whose message
    seq is ``after_seq`` — the durable kind=answer decide-records (``payload.consultation_decision``) with a
    HIGHER seq. SEQ-scoped (not id-scoped) so a re-consultation that reuses an id or keys can NEVER mix old
    answers into the new consultation (verify-round blocker fix)."""
    rows = (
        db.execute(
            select(PipelineMessage.payload)
            .where(
                PipelineMessage.version_id == version_id,
                PipelineMessage.kind == "answer",
                PipelineMessage.seq > after_seq,
            )
            .order_by(PipelineMessage.seq.asc())
        )
        .scalars()
        .all()
    )
    out: dict[str, dict[str, Any]] = {}
    for p in rows:
        cd = p.get("consultation_decision") if isinstance(p, dict) else None
        if isinstance(cd, dict) and cd.get("key"):
            out[cd["key"]] = cd
    return out


def dispatch_directive(
    db: Session, version_id: uuid.UUID, action: str, payload: dict[str, Any], stage: str
) -> Optional[str]:
    """Resolve the re-dispatch prompt for an ``agent_working`` transition, else ``None`` (CR-V2-009).

    Single entry point for the route (CR-NS-018): payload-framed for ``uprav`` / ``ask`` / ``answer``
    (delegates to :func:`directive_for_action`), the aggregated decision brief for the FINAL ``decide``
    (CR-V2-041 — reads ALL captured decisions from the DB), ``None`` for a fresh-phase dispatch (``start`` /
    ``approve_spec`` / ``schvalit`` / ``verdict`` / ``pokracovat``).
    """
    if action == "decide":
        # CR-V2-041: only the LAST decide re-dispatches (status went agent_working). APPLY = rework per ALL
        # captured decisions; aggregate them from the DB here (directive_for_action has no DB to do this).
        lc = _latest_consultation(db, version_id)
        if lc is None:
            return None
        c, c_seq = lc
        answers = _consultation_answers(db, version_id, c_seq)
        lines = [
            f"- {d.get('question', '')} → {a.get('label')}" + (f" (poznámka: {a['note']})" if a.get("note") else "")
            for d in c.get("decisions", [])
            if (a := answers.get(d.get("key"))) is not None
        ]
        return (
            "Manažér rozhodol v konzultácii:\n"
            + "\n".join(lines)
            + "\nTeraz PREPRACUJ Špecifikáciu/Návrh podľa týchto rozhodnutí a uzavri fázu (gate_report)."
        )
    del db, version_id  # route-call signature parity; the v1 DB-fetch relay paths are retired (CR-V2-009)
    return directive_for_action(action, payload, stage)


@dataclass
class RelayResult:
    """Outcome of a Manažér relay (CR-V2-015). ``deferred`` → the message was ENQUEUED behind an in-flight
    turn (it will become the next turn when the current dispatch settles; the route does NOT schedule a new
    dispatch). ``state`` is the (possibly updated) pipeline state; ``action`` is the action verb the relay
    mapped to when dispatched now (``ask``/``answer``), else ``None`` when deferred."""

    state: PipelineState
    deferred: bool
    action: Optional[str] = None


async def relay_manazer_message(db: Session, *, version_id: uuid.UUID, text: str) -> RelayResult:
    """Relay a Manažér message typed in the read-only AI Agent tab as the engine's NEXT turn (CR-V2-015).

    SPIKE-IO Model B single-writer enforcer: the message is NOT a keystroke into the warm ``claude``
    session — the engine is the sole writer. Two cases (the per-version inbound queue serializes them):

    * **A turn is in flight** (``dispatch_in_flight`` — the engine is mid-``invoke_claude``): ENQUEUE the
      message (:func:`_enqueue_relay`) and return ``deferred=True``. The runner drains the queue AFTER the
      current dispatch (incl. its auto-chain) settles and dispatches it as the next ``--resume`` turn, so a
      relayed turn and the autonomous turn can never invoke ``invoke_claude`` concurrently on the session.
    * **The build is settled** (no turn in flight): dispatch the message immediately via :func:`apply_action`
      — ``answer`` when the agent is blocked on its own question (so the board's ``answer`` flow is honoured),
      else ``ask`` (direct consult; threads the message into the actor's next turn). Both go through the
      sole-mutator + ``dispatch_in_flight`` single-flight guard, so the relay is just another serialized turn.

    Raises :class:`OrchestratorError` when the pipeline has not started for this version."""
    if not text or not str(text).strip():
        raise OrchestratorError("relay requires a non-empty message")
    state = _get_state(db, version_id)
    if state is None:
        raise OrchestratorError("Pipeline not started for this version")
    text = str(text).strip()

    # In-flight → enqueue behind the running turn (the runner drains it next). NEVER dispatch concurrently:
    # the durable ``dispatch_in_flight`` flag is the same guard ``apply_action`` enforces, made explicit here
    # so the relay path defers instead of raising "Dispečer už beží".
    if state.dispatch_in_flight or state.status == "agent_working":
        _enqueue_relay(version_id, text)
        # Record the Manažér's message immediately for the audit trail / read-only view; the engine will
        # consume the queued text as the next turn's prompt. Recipient = the actor the engine will relay to.
        _record_message(
            db,
            version_id=version_id,
            stage=state.current_stage,
            author="manazer",
            recipient=state.current_actor,
            kind="question",
            content=text,
            payload={"phase": state.current_stage, "relay_queued": True},
        )
        db.flush()
        return RelayResult(state=state, deferred=True, action=None)

    # Konzultácia (konzultacia-mode.md Part 1): a SETTLED TERMINAL version (``current_stage == 'done'`` —
    # covers a done conversation build signed off via ``hotovo``, a legacy schvalit-done build, AND a
    # PROD-released version) is answerable in READ-ONLY advisory mode. Route to the consult path — record the
    # message + arm a read-only turn — instead of ``apply_action('ask')`` whose ``_begin_dispatch`` no-ops on
    # ``done`` (STAGE_ACTOR has no ``done`` actor) and leaves the message a dead-end (the bug this fixes). The
    # ``state is None`` guard above is UNCHANGED, so a NEVER-BUILT version still cold-starts a build.
    if state.current_stage == "done":
        new_state = _begin_consult(db, state, text)
        return RelayResult(state=new_state, deferred=False, action="consult")

    # Settled → dispatch now. ``answer`` when the agent is blocked on its own question; else ``ask``.
    action = "answer" if (state.status == "blocked" and state.block_reason == "agent_question") else "ask"
    new_state = await apply_action(db, version_id=version_id, action=action, payload={"text": text})
    return RelayResult(state=new_state, deferred=False, action=action)


async def drain_relay_turn(
    db: Session,
    version_id: uuid.UUID,
    on_event: Optional[claude_agent.EventCallback] = None,
    on_message: Optional[MessageCallback] = None,
) -> Optional[PipelineState]:
    """Drain ONE queued Manažér relay message and run it as the next engine turn (CR-V2-015).

    Called by the runner after a dispatch settles. Pops the oldest queued relay (:func:`pop_relay_message`),
    threads it as the actor's prompt via the SAME ``run_dispatch`` path every turn uses (so it is serialized
    behind the just-settled turn — never concurrent), and returns the settled state. ``None`` when nothing
    was queued or the version vanished. The relayed message is framed exactly like an interactive
    ``ask``/``answer`` directive so the agent acts on it instead of re-running the generic phase directive."""
    text = pop_relay_message(version_id)
    if text is None:
        return None
    state = _get_state(db, version_id)
    if state is None:
        return None
    # Konzultácia (konzultacia-mode.md Part 1): a drained message on a TERMINAL version (``current_stage ==
    # 'done'``) is a read-only consult, NOT a build turn — ``_begin_dispatch`` would no-op on ``done`` (no
    # STAGE_ACTOR) and leave it a dead-end. Arm the read-only turn instead (the enqueued Manažér message is
    # already on the log — it is the latest ``manazer`` message the consult directive reads). Serialized
    # behind the just-settled turn exactly like every drain.
    if state.current_stage == "done":
        _arm_consult_dispatch(db, state)
        return await run_consult_turn(db, version_id, on_event, on_message=on_message)
    # Re-arm the dispatch (sole-mutator: this mutates state as a CONSEQUENCE of the queued Manažér action,
    # exactly like ``apply_action``'s ask/answer handlers do via ``_begin_dispatch``).
    _begin_dispatch(db, state)
    db.flush()
    directive = f"Manažér ti počas behu napísal: {text}"
    # Spine STEP 1 (adversarial MAJOR fix): route the drained IN-FLIGHT relay by mode, mirroring the runner's
    # ``_run`` selection. Without this, an in-flight Manažér message on a CONVERSATION build would drain
    # through the PHASE AUTOMATON (``run_dispatch`` → ``_persist_priprava_spec`` / ``_settle_phase_boundary``),
    # leaking spec-persistence + phase-advance semantics into the conversation — the exact automaton the
    # spine REPLACES. A conversation build drains through the non-phase conversation loop; everything else
    # keeps the phase automaton. STEP 4 (step4-programovanie-design.md MD-A): a conversation build that is
    # MID-BUILD (``current_stage == 'programovanie'``) drains through ``run_dispatch`` → ``_run_build_round``
    # (the EXISTING build loop, routed by stage) so a Manažér message during the build seeds the resumed task
    # exactly like a legacy build — the conversation loop only owns the priprava register (stage != programovanie).
    # CR-1 (nex-studio-visual): a conversation build at ``current_stage == 'vizual'`` drains through
    # ``run_dispatch`` → ``_run_vizual_round`` too, so an in-flight Manažér change-request threads its
    # ``directive`` into the live-preview round (the AI applies it to the FE; HMR reflects it) — mirroring the
    # runner's ``_run`` selection, which also excludes ``vizual`` from the conversation-loop branch.
    if state.mode == "conversation" and state.current_stage not in ("programovanie", "vizual"):
        return await run_conversation_turn(db, version_id, on_event, directive, on_message=on_message)
    return await run_dispatch(db, version_id, on_event, directive, on_message=on_message)


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
    metrics_phase: Optional[str] = None,
    allowed_tools: Optional[list[str]] = None,
    sandbox: bool = False,
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

    ``metrics_phase`` (metrics-v3-three-phases.md Part 1): a METRICS-ONLY phase stamp written into the
    recorded message's ``payload['phase']`` (one of ``STAGE_VALUES`` — ``navrh`` / ``programovanie`` /
    ``verifikacia``). The v3 conversation flow passes the phase for the round that produced the turn so
    :func:`pipeline_metrics.aggregate_usage_by_phase` attributes it there instead of falling back to
    ``msg.stage``. It does **NOT** touch ``stage`` / ``current_stage`` / any predicate — the deploy/release
    gate still reads the STAGE. ``None`` (every legacy caller) omits the key → byte-for-byte the historical
    payload, so attribution falls back to ``msg.stage`` exactly as before.

    ``allowed_tools`` (konzultacia-mode.md Part 1): the read-only tool profile forwarded to
    :func:`claude_agent.invoke_claude`. When given (the consult turn passes ``CONSULT_READ_ONLY_TOOLS``),
    the turn can ONLY read the project — no ``Bash``/``Write``/``Edit``. ``None`` (every build caller) →
    today's full-auto profile, byte-identical.

    ``sandbox`` (konzultacia-sidecar-sandbox.md Part 2): forwarded to :func:`claude_agent.invoke_claude`.
    ``True`` (only the consult turn) runs the read-only turn inside the OS-isolated sidecar (KERNEL-``:ro``
    project, host unreachable); build turns pass ``False`` and are byte-identical. The sidecar path requires
    ``allowed_tools`` to be set too, so a build turn can never take it regardless of this flag.
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

    # CR-V2-031: append the exact status-block enum values for THIS stage so the agent emits them verbatim
    # instead of guessing/translating (Opus emitted stage='preparation' → 'unknown stage' ParseFailure).
    # The single chokepoint every dispatch + every parse-retry re-emit passes through, so the re-emit also
    # carries the exact `stage` and can actually recover.
    prompt = f"{prompt}\n\n{_status_block_instruction(stage)}"
    # CR-V2-038: tell the AI Agent which model to spawn its dynamic helpers on (the helper model can't be a
    # CLI flag — the spawning agent picks it). Owner's Nastavenia choice; Haiku default. Only the AI Agent
    # spawns helpers, so the directive is AI-Agent-only (the Auditor never gets it).
    if role == AI_AGENT_ROLE:
        prompt = f"{prompt}\n\n{_helper_model_directive(_resolve_helper_model(db, version_id))}"

    # WS-D (CR-NS-036): time + meter this dispatch into the turn accumulator. A fresh local one for
    # single-shot direct callers; the shared one when threaded through the parse-retry loop.
    turn_metrics = metrics if metrics is not None else _DispatchMetrics()
    # build-robustness-crash-handling.md Fix 1: persist this turn's subprocess output to a durable per-turn
    # log so the next crash/timeout is diagnosable (the terminal-logs volume was empty). Keyed on the stage
    # + the (project, role) session id (no Date.now — resume-safe), under a per-version subdir. On a crash /
    # timeout the raising ``ClaudeAgentError`` carries the written path (Fix 3 references it).
    log_dir = claude_agent.TURN_LOG_DIR / str(version_id)
    log_label = f"{stage}-{session_id}"
    _started = perf_counter()
    try:
        # CR-V2-015 single-writer guard: mark this ``claude_session_id`` engine-busy for the live CLI
        # write so the break-glass debug-attach PTY (``agent_terminal.write_input``) cannot become a
        # concurrent second writer mid-turn (SPIKE-IO Risk (a)). Re-entrant across parse-retries.
        with _engine_session_active(session_id):
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
                    # konzultacia-mode.md Part 1: the read-only tool profile (consult turn) or None (build).
                    allowed_tools=allowed_tools,
                    # konzultacia-sidecar-sandbox.md Part 2: route the consult turn through the OS-isolated
                    # sidecar (True only from run_consult_turn); build turns pass False → in-process.
                    sandbox=sandbox,
                    # Fix 1: persist this turn's output to a durable per-turn log (redacted, §4).
                    log_dir=log_dir,
                    log_label=log_label,
                )
            )
    except ClaudeAgentTimeout as exc:
        # build-robustness-crash-handling.md Fix 2: a REAL timeout — the turn burned its whole wall-clock
        # budget. Re-running just risks another 40-min wall, so this stays conservative (NO auto-retry; the
        # build round settles to awaiting_manazer). Distinguished from a crash via ``envelope_loss_kind`` so
        # the round routes the honest, type-specific message (Fix 3). MUST precede ``except ClaudeAgentError``
        # (ClaudeAgentTimeout is a subclass).
        turn_metrics.record(None, perf_counter() - _started)
        lost_work = await _audit_lost_work(
            db,
            version_id=version_id,
            slug=slug,
            stage=stage,
            timeout_seconds=timeout if timeout is not None else _timeout_for(stage),
            on_message=on_message,
            cause_label="Agent vyčerpal časový limit",
        )
        return ParseFailure(
            f"claude invocation failed: {exc}",
            usage=turn_metrics.usage_payload(),
            timing=turn_metrics.timing_payload(),
            lost_work=lost_work,
            envelope_loss_kind="timeout",
            log_path=getattr(exc, "log_path", None),
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
        # build-robustness-crash-handling.md Fix 2: this is a CRASH (connection/decode/non-zero exit — NOT
        # the wall-clock budget), usually transient → the build round auto-retries the turn ONCE before
        # settling. ``envelope_loss_kind='crash'`` routes both the retry decision and the honest message.
        lost_work = await _audit_lost_work(
            db,
            version_id=version_id,
            slug=slug,
            stage=stage,
            timeout_seconds=timeout if timeout is not None else _timeout_for(stage),
            on_message=on_message,
            cause_label="Agent stratil spojenie / spadol",
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
            envelope_loss_kind="crash",
            log_path=getattr(exc, "log_path", None),
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
        # CR-V2-029: also carry a truncated raw-output excerpt so the terminal escalation can show WHAT
        # the agent produced (the failed output otherwise vanishes — the empty-screen bug).
        return replace(
            parsed,
            usage=turn_metrics.usage_payload(),
            timing=turn_metrics.timing_payload(),
            raw=(stdout or "")[:_RAW_EXCERPT_LEN],
        )

    # Map the agent block.kind → message kind (question/blocked → question). The Auditor's ``verdict``
    # block (CR-V2-006 repurposed shape; emitted by the upfront review CR-V2-013 + the end Verifikácia
    # CR-V2-014) is preserved as a ``verdict`` message kind (a valid ``ck_pipeline_message_kind`` value),
    # so the Manažér's review view / the Verifikácia tab can read the structured verdict + findings
    # instead of a downgraded gate_report.
    msg_kind = "question" if parsed.kind in ("question", "blocked") else parsed.kind
    if msg_kind not in (
        "kickoff",
        "question",
        "answer",
        "gate_report",
        "verdict",
        "notification",
        "consultation",  # CR-V2-041: the AI Agent's decision queue (kept as its own kind, not downgraded)
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
            # CR-V2-041: the consultation decision queue (kind=consultation) — the FE DecisionCardStack reads
            # decisions[] from here; mode="json" for JSONB. None on every other block.
            "consultation": parsed.consultation.model_dump(mode="json") if parsed.consultation is not None else None,
            # konzultacia-mode.md Part 2: the change-request marker a read-only consult turn raises when the
            # Manažér's ask needs a NEW version — the FE ChangeRequestBar reads it. None on every build turn.
            "change_request": (
                parsed.change_request.model_dump(mode="json") if parsed.change_request is not None else None
            ),
            # task_plan decomposition (F-007 §4/§5, CR-NS-020 CR-2; v2: folds into Návrh — CR-V2-011).
            # Persisted so the audit trail / TaskPlanPanel can show the plan and CR-3 can re-read the
            # cross-cutting rules from this gate_report payload.
            # mode="json" so any UUID in the plan serializes to a str for JSONB.
            "plan": parsed.plan.model_dump(mode="json") if parsed.plan is not None else None,
            "cross_cutting_rules": parsed.cross_cutting_rules,
            # CR-V2-052: the release-coverage declaration (flagship features + safety properties) carried on a
            # Návrh gate_report — persisted so _declared_release_coverage reads it to risk-floor the oracle.
            "flagship_features": parsed.flagship_features,
            "safety_properties": [sp.model_dump(mode="json") for sp in parsed.safety_properties],
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
            # metrics-v3-three-phases.md Part 1: the metrics-only phase stamp (v3 conversation flow). Spread
            # AFTER extra_payload so this orchestrator-owned stamp is never clobbered; omitted entirely when
            # None → aggregate_usage_by_phase falls back to msg.stage (legacy payload byte-for-byte unchanged).
            **({"phase": metrics_phase} if metrics_phase is not None else {}),
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
    metrics_phase: Optional[str] = None,
    allowed_tools: Optional[list[str]] = None,
    sandbox: bool = False,
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
    # CR-V2-029: the whole turn (primary + every re-emit) shares ONE wall-clock budget. Previously each
    # of the 1+_PARSE_RETRIES invocations got a fresh full timeout, so a turn could legally run up to
    # 3×900s = 45 min. Now each retry gets only the time that REMAINS, and we never launch a re-emit with
    # less than _MIN_RETRY_BUDGET_S left.
    budget = timeout if timeout is not None else _timeout_for(stage)
    turn_start = perf_counter()
    result = await invoke_agent(
        db,
        version_id=version_id,
        role=role,
        stage=stage,
        prompt=prompt,
        timeout=budget,
        on_event=on_event,
        recipient=recipient,
        on_message=on_message,
        extra_payload=extra_payload,
        metrics=turn_metrics,
        metrics_phase=metrics_phase,
        allowed_tools=allowed_tools,
        sandbox=sandbox,
    )
    attempts = 0
    while isinstance(result, ParseFailure) and attempts < _PARSE_RETRIES:
        attempts += 1
        remaining = int(budget - (perf_counter() - turn_start))
        if remaining < _MIN_RETRY_BUDGET_S:
            logger.warning(
                "parse-retry budget exhausted for version=%s role=%s (%ds left) — stopping after %d attempt(s)",
                version_id,
                role,
                remaining,
                attempts - 1,
            )
            break
        result = await invoke_agent(
            db,
            version_id=version_id,
            role=role,
            stage=stage,
            timeout=remaining,
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
            metrics_phase=metrics_phase,
            allowed_tools=allowed_tools,
            sandbox=sandbox,
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
    stage: str = "navrh",
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
        # CR-V2-015 single-writer guard: a task_plan generation pass runs inside the AI Agent's warm
        # session — mark it engine-busy so debug-attach can't write concurrently (same guard as
        # :func:`invoke_agent`).
        with _engine_session_active(session_id):
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
    except ClaudeAgentTimeout as exc:
        # A genuine TIMEOUT — the turn burned its whole budget. A failed invocation still burned wall-clock
        # (no usage envelope) — count it (WS-D).
        metrics.record(None, perf_counter() - _started)
        # R1 envelope-loss parity (CR-1, audit 2026-06-18): a timeout may have left real commits behind even
        # though the JSON envelope was lost — audit baseline..HEAD and ride the audit dict on
        # ParseFailure.lost_work so the round settles to awaiting_manazer ("review & continue"), exactly
        # like invoke_agent. A no-op (None) when no dispatch baseline was armed; the prefix below then lets
        # the round set block_reason=agent_error (a ClaudeAgentError), never the parse_exhaustion mislabel.
        # ``lost_work`` set ⇒ the per-pass retry loop does NOT re-invoke (re-running just risks another long
        # timeout — CR-V2-037 keeps this conservative for a real timeout).
        lost_work = await _audit_lost_work(
            db,
            version_id=version_id,
            slug=slug,
            stage=stage,  # CR-V2-011 navrh (folds into Návrh); STEP 3 priprava (conversation) — honest phase
            timeout_seconds=_timeout_for("navrh"),
            on_message=on_message,
        )
        return ParseFailure(
            f"{_PLAN_PASS_ENVELOPE_LOSS_PREFIX} {exc}",
            usage=metrics.usage_payload(),
            timing=metrics.timing_payload(),
            lost_work=lost_work,
        )
    except ClaudeAgentError as exc:
        # CR-V2-037: a FAST crash (non-zero exit / decode / stream-end — NOT a timeout). The agent produced
        # nothing this turn, but it cost almost no wall-clock and is usually transient (a CLI hiccup, a
        # too-large --resume, a rate blip), so DON'T audit/settle here — return a RETRYABLE envelope-loss
        # (``lost_work`` stays None) so :func:`_invoke_plan_pass` re-invokes this single pass (bounded)
        # rather than discard the whole accumulated plan. Same envelope-loss prefix as a timeout (it IS a
        # claude invocation failure → block_reason=agent_error if the retries are exhausted), but no
        # lost_work ⇒ the retry loop picks it up. Logged (the cause was previously swallowed → undiagnosable).
        metrics.record(None, perf_counter() - _started)
        logger.warning("task_plan pass crashed (retryable) for version=%s: %s", version_id, exc)
        return ParseFailure(
            f"{_PLAN_PASS_ENVELOPE_LOSS_PREFIX} {exc}",
            usage=metrics.usage_payload(),
            timing=metrics.timing_payload(),
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
    stage: str = "navrh",
    metrics_phase: Optional[str] = None,
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
    on retry-exhaustion (carrying the accumulated metrics → the round's fail-closed HALT).

    ``metrics_phase`` (metrics-v3-three-phases.md Part 1): overrides the synthetic note's ``payload['phase']``
    (the metrics stamp) WITHOUT moving ``msg.stage`` — the STEP-3 conversation plan round passes ``navrh``
    while its ``stage`` stays ``priprava``. ``None`` (the legacy Návrh caller) keeps ``phase == stage``,
    byte-for-byte the pre-STEP-3 behaviour."""
    phase = metrics_phase if metrics_phase is not None else stage
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
        stage=stage,
    )
    attempts = 0
    # Retry a re-emittable failure within ``_PARSE_RETRIES``: a PARSE typo (re-ask for the SAME content) or
    # — CR-V2-037 — a FAST CRASH (re-invoke the SAME pass; the agent crashed without producing output and a
    # crash is usually transient). A genuine TIMEOUT sets ``lost_work`` → the loop condition excludes it
    # (re-invoking just risks another long timeout), so a real timeout still settles the R1 path at once.
    while isinstance(result, ParseFailure) and result.lost_work is None and attempts < _PARSE_RETRIES:
        attempts += 1
        # A crash (envelope-loss prefix, no lost_work) → re-run the ORIGINAL prompt; a parse typo → ask the
        # agent to resend the same content as one well-formed JSON block.
        is_crash = result.reason.startswith(_PLAN_PASS_ENVELOPE_LOSS_PREFIX)
        retry_prompt = (
            prompt
            if is_crash
            else (
                f"Tvoj výstup sa nepodarilo spracovať: {result.reason}. Pošli ho ZNOVA — rovnaký obsah, "
                "ale VÝHRADNE ako jeden JSON objekt vnútri bloku <<<TASK_PLAN_JSON>>> … "
                "<<<END_TASK_PLAN_JSON>>>, s presnými názvami polí a bez čohokoľvek navyše."
            )
        )
        result = await _plan_pass_once(
            db,
            state,
            prompt=retry_prompt,
            json_schema=json_schema,
            parser=parser,
            on_event=None,  # cheap re-emit retries don't stream (mirror invoke_agent_with_parse_retry)
            on_message=on_message,
            metrics=metrics,
            stage=stage,
        )
    if isinstance(result, ParseFailure):
        # CR-V2-037: a CRASH that STILL failed after the bounded re-invokes is a PERSISTENT envelope-loss —
        # audit baseline..HEAD now (it was deferred so the retries could run) and ride the lost-work dict so
        # the round settles awaiting_manazer ("review & continue"), exactly like a timeout, instead of a
        # parse_exhaustion mislabel / blocked dead-end. (No-op → None when no dispatch baseline was armed;
        # the envelope-loss prefix then still yields block_reason=agent_error in _settle_plan_pass_failure.)
        if result.lost_work is None and result.reason.startswith(_PLAN_PASS_ENVELOPE_LOSS_PREFIX):
            lost_work = await _audit_lost_work(
                db,
                version_id=state.version_id,
                slug=_project_slug_for_version(db, state.version_id),
                stage=stage,
                timeout_seconds=_timeout_for("navrh"),
                on_message=on_message,
                cause_label="Agent opakovane zlyhal",
            )
            result = replace(result, lost_work=lost_work)
        # Attach the accumulated turn metrics so the fail-closed relay can carry the lost tokens.
        return replace(result, usage=metrics.usage_payload(), timing=metrics.timing_payload())
    msg = _record_message(
        db,
        version_id=state.version_id,
        # CR-V2-011: navrh (the plan passes fold into Návrh); STEP 3: priprava (conversation) — honest phase.
        stage=stage,
        author="ai_agent",
        recipient="manazer",
        kind="notification",
        content=label_fn(result),
        payload={"usage": metrics.usage_payload(), "timing": metrics.timing_payload(), "phase": phase},
    )
    if on_message is not None:
        await on_message(msg)
    return result


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
        # Audit P2 (2026-07-12): a per-task build MUST land at least one commit — an EMPTY ``commits[]`` with a
        # baseline set makes the loops below no-ops and passes trivially, marking the task ``done`` with zero
        # committed work. Require the agent to have reported a commit.
        if not block.commits:
            return "task reported no commits — no work landed on the task baseline"
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


def _git_tag_version(project_root: Path, version_number: str, sha: str) -> None:
    """CR-V2-056 (layer-1): create/refresh an annotated git tag ``v{version_number}`` at the verified commit.
    BEST-EFFORT, NEVER raises — the payload ``verified_sha`` (:func:`version_verified`) is the authoritative
    anchor; the tag is the reproducible human artifact. ``-f`` re-anchors on a FAIL→fix→re-PASS (the verified
    commit legitimately moved to the new PASS commit)."""
    import subprocess

    tag = f"v{version_number}"
    try:
        subprocess.run(
            ["git", "-C", str(project_root), "tag", "-f", "-a", tag, sha, "-m", f"NEX Studio: {tag} verified"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def _commit_release_note(db: Session, version_id: uuid.UUID, project_root: Path, version_number: str) -> None:
    """Part 1 (per-app-changelog-standard.md §1): NEX Studio OWNS the user-facing ``RELEASE_NOTES.md`` —
    (re)generate it from the completing version's Epics + commit it INTO the app repo. BEST-EFFORT, NEVER
    raises: the note is a served artifact, not a release gate, so a git/FS hiccup must never sink a sign-off.

    Placed at each completion seam BEFORE the sign-off/verified SHA is read, so the note rides the SAME
    signed/tagged commit (and is baked into the backend image, whose Dockerfile COPYs only ``RELEASE_NOTES.md``)
    AND :func:`version_verified` stays green — it recomputes against the note commit, not its parent.

    A version already ``released`` is immutable (:func:`release_note_writer.write_release_note` returns None)
    → no write, no commit. The commit is pathspec-scoped so only the note is committed, never stray changes."""
    from backend.services import release_note_writer

    try:
        path = release_note_writer.write_release_note(db, version_id, project_root)
    except Exception:  # noqa: BLE001 — best-effort artifact; a generator failure must not sink the sign-off
        return
    if path is None:
        return
    try:
        rel = str(path.relative_to(project_root))
    except ValueError:
        return
    if not _git_ok(project_root, ["add", "-A", "--", rel]):
        return
    # Pathspec-scoped commit → only the note lands, even if the worktree carries other changes. A no-op
    # (note unchanged since the last commit) exits non-zero — harmless, the artifact is already in place.
    _git_ok(
        project_root,
        ["commit", "-m", f"docs(release-notes): v{version_number} — user-facing changelog", "--", rel],
    )


def _commit_vizual_changes(project_root: Path) -> None:
    """Squash the whole Vizuál session into ONE commit at approval (#3, Director 2026-07-13).

    The Vizuál round no longer commits each change — :func:`_vizual_directive` tells the AI to only WRITE the
    FE sources (HMR reflects them live WITHOUT a commit), so many small live tweaks accumulate uncommitted in
    the worktree. At the Vizuál schvaľovací bod (vizual → programovanie) the engine stages the whole
    ``frontend/`` and makes ONE commit, so the visual session lands as a single tidy commit before the build.
    BEST-EFFORT, NEVER raises (a git hiccup must not sink the approval). Nothing changed → ``git commit`` exits
    non-zero → no empty commit. The sandbox's transient override config is ``.git/info/exclude``-d, so
    ``git add -A`` never stages it (:mod:`vizual_sandbox`)."""
    if not (project_root / ".git").is_dir():
        return  # dry-run / no checkout — nothing to commit
    if not _git_ok(project_root, ["add", "-A", "--", "frontend"]):
        return
    _git_ok(project_root, ["commit", "-m", "feat(vizual): manažérom schválené vizuálne úpravy"])


def _commit_navrh_deliverables(project_root: Path) -> None:
    """Fix A (Director 2026-07-17): freeze the Príprava/Návrh deliverables (Špecifikácia + design doc + spec
    edits under ``docs/``) into ONE commit BEFORE the Auditor upfront review. Two payoffs:

    * The audit reviews a STABLE, committed snapshot — not a working tree the agent turn was still writing.
      (The stale-audit that FAILed on already-resolved gaps: the agent updates spec + design across a turn,
      and a review reading the uncommitted tree can catch a partially-updated state.)
    * The Príprava/Návrh output becomes DURABLE (it was otherwise uncommitted until a much later seam).

    Scoped to ``docs`` so only the design deliverables land, never stray worktree changes (mirrors
    :func:`_commit_vizual_changes` scoping to ``frontend``). BEST-EFFORT, NEVER raises (a git hiccup must not
    sink the phase); nothing changed → ``git commit`` exits non-zero → no empty commit."""
    if not (project_root / ".git").is_dir():
        return  # dry-run / no checkout — nothing to commit
    if not _git_ok(project_root, ["add", "-A", "--", "docs"]):
        return
    _git_ok(
        project_root,
        ["commit", "-m", "docs(navrh): Špecifikácia + návrhový dokument (zmrazené pred previerkou)"],
    )


def _write_release_note_to_disk(db: Session, version_id: uuid.UUID, project_root: Path) -> None:
    """obs-2 Part B Part 2 (per-app-changelog-part2-gate.md): (re)generate the completing version's
    ``RELEASE_NOTES.md`` ONTO DISK (NO commit) immediately BEFORE a release smoke, so the ephemeral image
    bakes the REAL note and the 2a behavioural gate asserts a SERVED note.

    Why the pre-smoke write (the second-version deadlock): the note is otherwise committed only at the PASS
    seam (:func:`_commit_release_note`) — AFTER the smoke. The 2a gate requires the endpoint to SERVE the
    completing version, which needs the note baked into the image BEFORE the build. For a 2nd+ version the
    served list would carry the PRIOR releases but never the completing one (its note is written only at a
    PASS the failing 2a prevents) → the gate could never pass → deadlock. Writing the note to disk here breaks
    it: the completing version's note rides the smoke build.

    Idempotent + best-effort: a ``released`` version is immutable (:func:`release_note_writer.write_release_note`
    returns None) and any generator/FS hiccup is swallowed so it never sinks the smoke. The PASS-time
    :func:`_commit_release_note` seam is UNCHANGED — its idempotent re-write + commit still anchors the note to
    the verified SHA."""
    from backend.services import release_note_writer

    try:
        release_note_writer.write_release_note(db, version_id, project_root)
    except Exception:  # noqa: BLE001 — a served artifact, never a release gate; a hiccup must not sink the smoke
        logger.warning("pre-smoke release-note write failed (version_id=%s)", version_id, exc_info=True)


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
    cause_label: str = "Vypršal čas agenta",
) -> Optional[dict[str, Any]]:
    """R1-c (D1): on an agent envelope-loss (timeout/crash), audit ``baseline..HEAD`` and surface any
    committed-but-lost work to the Director — *review & continue*, never silently lost, never auto-merged.

    Reads the dispatch's frozen ``dispatch_baseline_sha``, compares it to the current HEAD, and records ONE
    ``system→director`` ``notification`` carrying ``{dispatch_baseline_sha, post_timeout_head_sha,
    timeout_seconds, detected_commit_count}`` (idempotent per baseline). Returns the audit dict (with the
    Slovak ``next_action`` the caller settles on), or ``None`` when there is no dispatch baseline to audit
    against (e.g. an internal sub-turn before ``_begin_dispatch`` armed one, or an unreadable repo) — in which
    case the caller keeps its existing escalation. Status is NOT mutated here (the caller owns it).

    ``cause_label`` (CR-V2-037) opens the ``next_action`` so it tells the truth about WHY the envelope was
    lost: the default ``"Vypršal čas agenta"`` for a genuine timeout, but e.g. ``"Agent opakovane zlyhal"``
    when a task-plan pass crashed past its bounded re-invokes (no time expired — calling it a timeout was
    misleading)."""
    state = _get_state(db, version_id)
    if state is None or not state.dispatch_baseline_sha:
        return None
    baseline = state.dispatch_baseline_sha
    project_root = claude_agent.PROJECTS_ROOT / slug
    head = _repo_head(project_root)
    count = _rev_list_count(project_root, baseline)
    if count >= 1:
        next_action = f"{cause_label} — môžu byť zapísané zmeny ({count} commitov). Over 'git log' a pokračuj."
    else:
        next_action = f"{cause_label} — žiadna zmena nezistená. Pokračuj."
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


# NOTE (CR-V2-021): the v1 ``_mark_latest_coordinator_brief`` (tagged the latest Coordinator turn
# ``is_director_brief`` for the FE prominent rail) is REMOVED — the Coordinator hub-and-spoke is gone
# (design §2.2); there is no Coordinator turn to tag, and it had no live caller.


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


# UAT redeploy backend (F-009, CR-NS-098/-101; v2 owner = the per-customer deploy subsystem, deploy.py).
# REDEPLOYS an existing UAT — it does NOT re-provision it: a plain ``docker compose up -d --build
# --force-recreate`` against the UAT's OWN ``/opt/uat/<slug>/docker-compose.yml`` (hand-authored like NEX
# Ledger OR uat-deploy.py-provisioned like NEX Inbox), so there is no template re-render, no port
# reallocation, no nginx rewrite — the working UAT is preserved (uat-deploy.py is a PROVISIONER and would
# overwrite all three). ``/opt/uat`` + /var/run/docker.sock are mounted into the backend image, so the
# compose is reachable. The FE build-arg is stamped via ``VITE_APP_VERSION`` (post-commit version scheme).
# Module-level so tests can monkeypatch the path/existence; the timeout backstops the docker build (~1–2 min).
# (CR-V2-028: this is NO LONGER invoked from the fast-fix lane — deploy is OUT of the pipeline (OQ-3/D6),
# manual + per-customer; ``_run_uat_deploy`` is now called only by the deploy subsystem, deploy.py.)
UAT_ROOT: Path = Path("/opt/uat")
# PROD instances live under the per-customer control-plane root: a PROD deploy redeploys the compose
# at ``/opt/customers/<customer-slug>/<full-project-slug>/`` (mirrors uat_provisioner.PROD_ROOT, §2).
PROD_ROOT: Path = Path("/opt/customers")
UAT_DEPLOY_TIMEOUT = 900


def _uat_compose_path(
    uat_slug: str,
    *,
    environment: str = "uat",
    customer_slug: Optional[str] = None,
    full_project_slug: Optional[str] = None,
) -> Path:
    """The instance's existing compose file.

    Per-customer (customer_slug + full_project_slug given): nested ``<root>/<customer>/<project>/`` — PROD
    under ``/opt/customers``, UAT under ``/opt/uat`` (audit fix 2026-07-11). Project-level UAT (no
    customer_slug, the uat-deploy.py path) → flat ``/opt/uat/<uat_slug>/`` (unchanged).
    """
    if customer_slug and full_project_slug:
        root = PROD_ROOT if environment == "prod" else UAT_ROOT
        return root / customer_slug / full_project_slug / "docker-compose.yml"
    return UAT_ROOT / uat_slug / "docker-compose.yml"


def _uat_compose_exists(uat_slug: str) -> bool:
    """True if the UAT has a redeployable compose (hand-authored or provisioned)."""
    return _uat_compose_path(uat_slug).is_file()


async def _run_uat_deploy(
    project_slug: str,
    uat_slug: str,
    *,
    environment: str = "uat",
    customer_slug: Optional[str] = None,
    app: Optional[str] = None,
    full_project_slug: Optional[str] = None,
    version_number: Optional[str] = None,
) -> tuple[bool, str]:
    """Plain redeploy of an instance's EXISTING compose (``docker compose -f … up -d --build --force-recreate``).

    Respects the instance compose as-is — no re-render, no port reallocation, no nginx rewrite (unlike
    the uat-deploy.py provisioner) — and stamps the FE build-arg via ``VITE_APP_VERSION`` (post-commit
    version scheme). Environment-aware: UAT reads ``/opt/uat/<uat_slug>/docker-compose.yml``
    (unchanged), PROD reads ``/opt/customers/<customer_slug>/<full_project_slug>/docker-compose.yml``
    (§2). ``environment`` defaults to ``'uat'``; the PROD entry point is :func:`_run_prod_deploy`.

    Returns ``(ok, detail)``: ``ok`` is True only when ``up`` exits 0 AND the deployed app actually
    SERVES (icc-deploy §5.6 #2 — "exit 0" is not "serves"); ``detail`` is ``"OK"`` on success, else a
    short tail of the deploy error / the serve-verify reason. Never raises — a spawn failure / timeout /
    serve-verify failure becomes ``(False, reason)`` so the caller settles to ``blocked`` rather than a
    false success. Async (``create_subprocess_exec`` + ``await``) so the ~1–2 min docker build never
    blocks the event loop.
    """
    compose = _uat_compose_path(
        uat_slug, environment=environment, customer_slug=customer_slug, full_project_slug=full_project_slug
    )
    cmd = ["docker", "compose", "-f", str(compose), "up", "-d", "--build", "--force-recreate"]
    # A GENERATED app shows its OWN semantic version (each change = a new version), NOT a build counter (Director
    # 2026-07-11: NEX Studio itself is regularly patched → a counter; the apps we build get their real version).
    # Stamp the DEPLOYED version_number (bare, no leading 'v' — matches the deploy-matrix display) into BOTH the
    # FE + BE build-args, so the app's sidebar reads the same 1.1.0 as its Aktualizácie. Falls back to the
    # scaffold's initial real version "0.1.0" only when the version is unknown (defensive) — never a build
    # counter (obs P2-1: a counter is the very thing Director 2026-07-11 rejected for generated apps).
    stamp = (version_number[1:] if version_number[:1].lower() == "v" else version_number) if version_number else None
    build_ver = stamp or "0.1.0"
    env = {**os.environ, "APP_VERSION": build_ver, "VITE_APP_VERSION": build_ver}
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
    # false-success bug). Verify the app actually responds before reporting success. UAT keeps the exact
    # 2-arg call (byte-identical — a monkeypatched serve-verify fake gets only project_slug + uat_slug);
    # PROD threads the layout kwargs so the FE cross-probe targets the ``<customer>-<app>-<svc>`` name.
    if customer_slug:
        return await _verify_uat_serves(
            project_slug,
            uat_slug,
            environment=environment,
            customer_slug=customer_slug,
            app=app,
            full_project_slug=full_project_slug,
        )
    return await _verify_uat_serves(project_slug, uat_slug)


async def _run_prod_deploy(
    project_slug: str, customer_slug: str, app: str, full_project_slug: str, version_number: Optional[str] = None
) -> tuple[bool, str]:
    """PROD sibling of :func:`_run_uat_deploy` — redeploy the customer's PROD compose (§2).

    Redeploys ``/opt/customers/<customer_slug>/<full_project_slug>/docker-compose.yml`` and
    serve-verifies via the ``<customer_slug>-<app>-<svc>`` container names. Thin wrapper over the
    env-aware :func:`_run_uat_deploy` so the subprocess dance + serve-verify gate are shared.
    """
    return await _run_uat_deploy(
        project_slug,
        f"{customer_slug}-{app}",
        environment="prod",
        customer_slug=customer_slug,
        app=app,
        full_project_slug=full_project_slug,
        version_number=version_number,
    )


async def _verify_uat_serves(
    project_slug: str,
    uat_slug: str,
    *,
    environment: str = "uat",
    customer_slug: Optional[str] = None,
    app: Optional[str] = None,
    full_project_slug: Optional[str] = None,
) -> tuple[bool, str]:
    """Post-``up`` readiness gate for a deploy (icc-deploy §5.6 #2): confirm the deployed app actually
    SERVES before :func:`_run_uat_deploy` reports success — every backend ``/api`` responds AND every
    frontend serves (HTTP ``< 500``). Returns ``(True, "OK")`` once verified, else ``(False, reason)`` so
    the caller settles to ``blocked`` rather than a false success.

    The compose strips host ports (Traefik routes by network), so this probes IN-network via
    ``docker compose exec``: the backend probes itself at ``localhost`` and probes the frontend (nginx, no
    Python) over the network by its unique container name (``uat-<slug>-<svc>`` for UAT,
    ``<customer>-<app>-<svc>`` for PROD, §2). Service keys + container ports are read from the SOURCE
    compose (the instance compose's stripped ports can't reveal the container port); ``up --build``
    rebuilds from that same source, so the ports match the live containers.

    Defensive skips return ``(True, "OK")`` (the app deployed; we just can't probe it) — NEVER a new false
    FAIL: no instance compose (the caller already guards existence), an unreadable source compose, or no
    backend service (no Python container to probe from). The real serve check runs whenever a backend exists."""
    uat_compose = _uat_compose_path(
        uat_slug, environment=environment, customer_slug=customer_slug, full_project_slug=full_project_slug
    )
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
        name_base = (
            (f"{customer_slug}-{app}" if environment == "prod" else f"uat-{customer_slug}-{app}")
            if customer_slug
            else f"uat-{uat_slug}"
        )
        fe_host = f"{name_base}-{fe_role}"
        fe_ready, fe_last = await _await_http_ready(base, be_role, fe_port, host=fe_host, path="/")
        if not fe_ready:
            return False, f"frontend '{fe_role}' not serving within {ACCEPTANCE_SMOKE_READY_TIMEOUT}s: {fe_last}"
        # The checks above prove the app serves IN-network; they do NOT prove the PUBLIC Traefik route works.
        # The andros-payables outage (2026-07-10): healthy containers, but a poisoned Host() label left no route
        # for the real domain → 404 at the public URL, while the cockpit reported "✓ Nasadené". Probe the route
        # the internet actually uses — from the app's own container (on nex-proxy-net), hit Traefik with the
        # public Host header — so a broken public route is a real deploy FAILURE, not a false success.
        public_host = f"{name_base}.{uat_provisioner.UAT_DOMAIN_SUFFIX}"
        route_state, route_last = await _verify_public_route(base, be_role, public_host)
        if route_state == "down":
            return False, (
                f"appka beží, ale verejná adresa {public_host} nie je dostupná — smerovanie zlyhalo: {route_last}"
            )
        if route_state == "skip":
            # Audit P1 (2026-07-12): "couldn't verify the public route" is NOT clean success — it is the SAME
            # condition as a real ingress outage (Traefik renamed / not on nex-proxy-net / transient DNS), the
            # exact blindspot the probe closes. On PROD (customer-facing) FAIL-CLOSED — a public route we cannot
            # confirm blocks the deploy (a retried deploy beats a silent outage). On UAT surface an amber
            # warning in the detail so the manager checks the address, without failing the test run.
            if environment == "prod":
                return False, (
                    f"appka beží, ale verejnú adresu {public_host} sa nepodarilo overiť (smerovanie/Traefik "
                    f"nedostupné): {route_last}. Nasadenie na PROD je zastavené, kým sa dostupnosť nepotvrdí."
                )
            logger.warning(
                "public-route verify skipped (host=%s) — Traefik unreachable from probe: %s", public_host, route_last
            )
            return True, (
                f"OK — ⚠ verejnú adresu {public_host} sa nepodarilo overiť; skontroluj, či je appka dostupná."
            )
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


def _acceptance_smoke_override(compose_path: Path, smoke_env: Optional[Path] = None) -> str:
    """Build an ephemeral compose override that strips ``container_name`` + host ``ports`` from
    every service of *compose_path* — and, when *smoke_env* is given, injects it as an ``env_file``
    so services that read ``env_file: .env`` (migrate/backend) get the COMPLETE rendered env INSIDE
    the container (``--env-file`` alone only feeds compose interpolation, never the containers).

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
        # A (fix): inject the rendered env as an env_file so a service reading ``env_file: .env`` (migrate,
        # backend) gets the COMPLETE env INSIDE the container. Compose CONCATENATES env_file lists and the
        # LATER file wins → the rendered values override the incomplete live .env (e.g. the missing
        # DATABASE_URL). Harmless for interpolation-only services (extra vars ignored; an explicit
        # ``environment:`` key still wins over env_file).
        if smoke_env is not None:
            lines.append("    env_file:")
            lines.append(f"      - {smoke_env}")
    return "\n".join(lines) + "\n"


# ── A (release-smoke-boot-and-batch-fixes.md): render a COMPLETE, ``.env.example``-derived env for the smoke
# boot ─────────────────────────────────────────────────────────────────────────────────────────────────────
# The generated app's ``docker-compose.yml`` fail-fast-guards required vars (``POSTGRES_PASSWORD:
# ${POSTGRES_PASSWORD:?set …}``). The app's LIVE ``.env`` can be INCOMPLETE (it may lack ``POSTGRES_PASSWORD``)
# → compose interpolation FAILS before any container starts → the app never boots → Verifikácia can never pass
# (the nex-payables 1.1.0 blocker). The app's ``.env.example`` HAS a consistent dev default. This renders a
# THROWAWAY env from ``.env.example`` (handed to ``docker compose --env-file``) so every ``${…:?}`` guard
# resolves, WITHOUT clobbering the live ``.env`` (which may hold real secrets). Mirrors the seeded
# ``scripts/ci_render_dotenv.py`` the CI ``migrate`` job uses (same host/password rewrite + guaranteed
# ``POSTGRES_PASSWORD``), so the smoke boots against the SAME shape CI migrates against.
_SMOKE_ENV_PASSWORD = "ci"  # same value as scripts/ci_render_dotenv.py CI_PASSWORD (smoke render == CI render).
_SMOKE_ENV_HOST = "db"  # the compose ``db`` service name (ci_render_dotenv CI_HOST) — the in-network DB target.


def _rewrite_smoke_database_url(value: str) -> str:
    """Rewrite the ``DATABASE_URL`` host + password to the compose ``db`` service, PRESERVING the scheme (incl.
    any ``+driver``), user, port, and dbname — the exact contract of ``ci_render_dotenv._rewrite_database_url``
    so the smoke DB URL matches what the online migrate path exercises."""
    parts = urlsplit(value)
    username = parts.username or ""
    port = f":{parts.port}" if parts.port is not None else ""
    userinfo = f"{username}:{_SMOKE_ENV_PASSWORD}" if username else _SMOKE_ENV_PASSWORD
    netloc = f"{userinfo}@{_SMOKE_ENV_HOST}{port}"
    # urlunsplit keeps ``scheme`` (incl. the ``+pg8000`` driver) byte-for-byte.
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _render_smoke_env(env_example: Path, dst: Path) -> bool:
    """Render a COMPLETE throwaway env from *env_example* into *dst* for the smoke ``docker compose --env-file``
    (A). Returns ``True`` when it rendered, ``False`` when there is no readable ``.env.example`` to render from
    (the caller then boots WITHOUT an ``--env-file``, byte-identical to the pre-fix behaviour). Never raises.

    Mirrors ``scripts/ci_render_dotenv.py`` exactly: ``DATABASE_URL`` host/password rewritten to the compose
    ``db`` service (scheme preserved), ``DB_PASSWORD``/``POSTGRES_PASSWORD`` forced to the known dev value, and
    ``POSTGRES_PASSWORD`` GUARANTEED present (APPENDED when the example omitted it) so the compose
    ``${POSTGRES_PASSWORD:?…}`` fail-fast guard always resolves. Every other line is copied verbatim."""
    try:
        raw_lines = env_example.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    out: list[str] = []
    seen_postgres_password = False
    for raw in raw_lines:
        stripped = raw.strip()
        if stripped.startswith("DATABASE_URL="):
            key, _, val = raw.partition("=")
            out.append(f"{key}={_rewrite_smoke_database_url(val)}")
        elif stripped.startswith("DB_PASSWORD="):
            out.append(f"DB_PASSWORD={_SMOKE_ENV_PASSWORD}")
        elif stripped.startswith("POSTGRES_PASSWORD="):
            out.append(f"POSTGRES_PASSWORD={_SMOKE_ENV_PASSWORD}")
            seen_postgres_password = True
        else:
            out.append(raw)
    if not seen_postgres_password:
        out.append(f"POSTGRES_PASSWORD={_SMOKE_ENV_PASSWORD}")
    dst.write_text("\n".join(out) + "\n", encoding="utf-8")
    return True


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


# The shared ICC Traefik that fronts every customer instance on ``nex-proxy-net`` (its web entrypoint). The
# public-route probe below hits THIS with a ``Host`` header — the same routing the internet exercises, minus the
# edge WAF (which 403s a container's direct public request, so probing the public URL from a container is
# useless). If the name ever changes the probe degrades to a SKIP (never a false FAIL — see _verify_public_route).
_PUBLIC_ROUTE_TRAEFIK_HOST = "nex-uat-traefik"
_PUBLIC_ROUTE_TRAEFIK_PORT = 80


def _traefik_public_route_probe_src(public_host: str) -> str:
    """In-container probe of the PUBLIC Traefik route. From a container ON ``nex-proxy-net`` it GETs the
    Traefik web entrypoint with ``Host: <public_host>`` — exactly what the internet hits. A working route →
    the app answers (2xx/3xx, or any non-404 app status). A MISSING route → Traefik's own ``404`` (the
    andros-payables outage shape: a poisoned ``Host()`` label left no route for the real domain). Exit codes:
    ``0`` = route OK; ``1`` = reached Traefik but the route is DOWN (``404`` no-route / ``>=500``); ``2`` =
    could NOT reach Traefik at all (inconclusive → the caller SKIPS, never a false FAIL)."""
    url = f"http://{_PUBLIC_ROUTE_TRAEFIK_HOST}:{_PUBLIC_ROUTE_TRAEFIK_PORT}/"
    return (
        "import sys, urllib.request, urllib.error\n"
        f"req = urllib.request.Request('{url}', headers={{'Host': '{public_host}'}})\n"
        "try:\n"
        "    r = urllib.request.urlopen(req, timeout=8)\n"
        "    print('status', getattr(r, 'status', 200)); sys.exit(0)\n"
        "except urllib.error.HTTPError as e:\n"
        "    print('status', e.code); sys.exit(0 if (e.code < 500 and e.code != 404) else 1)\n"
        "except Exception as e:\n"
        "    print('err', e); sys.exit(2)\n"
    )


async def _verify_public_route(base: list[str], exec_service: str, public_host: str) -> tuple[str, str]:
    """Probe the PUBLIC Traefik route for ``public_host`` from ``exec_service`` (a container on
    ``nex-proxy-net``). Returns ``(state, last)`` where state is ``"ok"`` (route serves), ``"down"`` (reached
    Traefik but the route is missing/erroring → the deploy must NOT report success), or ``"skip"`` (could not
    reach Traefik at all → defensive skip, NEVER a false FAIL). Retries over the readiness budget (Traefik
    needs a moment to register a freshly-``up``'d instance's labels)."""
    cmd = base + ["exec", "-T", exec_service, "python", "-c", _traefik_public_route_probe_src(public_host)]
    attempts = max(1, ACCEPTANCE_SMOKE_READY_TIMEOUT // ACCEPTANCE_SMOKE_READY_INTERVAL)
    last, last_rc = "no response", 2
    for i in range(attempts):
        rc, out = await _compose_smoke_step(cmd, 30)
        last = out.strip()[-200:] or f"exit {rc}"
        if rc == 0:
            return "ok", last
        last_rc = rc
        if i < attempts - 1:
            await asyncio.sleep(ACCEPTANCE_SMOKE_READY_INTERVAL)
    return ("down" if last_rc == 1 else "skip"), last


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
    # A: boot against a COMPLETE, .env.example-derived env (throwaway) so ${POSTGRES_PASSWORD:?…} & co. always
    # resolve — the live .env may be incomplete → compose interpolation would fail before any container starts.
    # --env-file feeds compose interpolation WITHOUT touching the app's live .env (which may hold real secrets).
    # No readable .env.example → no --env-file (unchanged legacy boot).
    smoke_env = tmpdir / "smoke.env"
    # ``--env-file`` feeds compose INTERPOLATION; the SAME rendered file is ALSO injected as a per-service
    # ``env_file`` in the override below, so services reading ``env_file: .env`` (migrate/backend) get the
    # complete env inside the container (--env-file alone does not reach them → DATABASE_URL was missing).
    smoke_env_rendered = _render_smoke_env(compose.parent / ".env.example", smoke_env)
    env_file_args = ["--env-file", str(smoke_env)] if smoke_env_rendered else []
    base = ["docker", "compose", "-p", project, *env_file_args, "-f", str(compose), "-f", str(override)]
    stack = _SmokeStack(
        base=base, compose=compose, override=override, project=project, roles=roles, up_rc=-1, up_detail=""
    )
    try:
        # Isolate — ephemeral override stripping container_name + host ports — then up (build + boot;
        # ``--wait`` blocks until healthchecks pass; Ollama reached via the app's own extra_hosts).
        override.write_text(_acceptance_smoke_override(compose, smoke_env if smoke_env_rendered else None))
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


# gate-g-hardening GAP 1 (B) + CR-V2-051 risk floor: ``release_smoke_test.sh`` MUST print three sentinels —
# ``ASSERTIONS_RUN=<n>`` (anti-empty floor: an empty ``set -e`` script that exit-0's without asserting is a
# FALSE green), ``FEATURE_ASSERTIONS_RUN=<n>`` (≥1 per declared flagship feature) and
# ``NEGATIVE_ASSERTIONS_RUN=<n>`` (≥1 per declared safety property — the risky op must be REJECTED). The
# absence of a sentinel / an insufficient count is a FAIL, not a pass (parsed by the engine, below).
_ASSERTIONS_RUN_RE = re.compile(r"\bASSERTIONS_RUN=(\d+)")
_FEATURE_ASSERTIONS_RUN_RE = re.compile(r"\bFEATURE_ASSERTIONS_RUN=(\d+)")
_NEGATIVE_ASSERTIONS_RUN_RE = re.compile(r"\bNEGATIVE_ASSERTIONS_RUN=(\d+)")


def _parse_last_sentinel(output: str, pattern: re.Pattern[str]) -> Optional[int]:
    """The LAST ``<NAME>=<n>`` count printed by ``release_smoke_test.sh`` for *pattern*, or ``None`` when the
    script printed no such sentinel at all."""
    matches = pattern.findall(output)
    return int(matches[-1]) if matches else None


def _parse_assertions_run(output: str) -> Optional[int]:
    """The LAST ``ASSERTIONS_RUN=<n>`` count (anti-empty floor). ``None`` / ``0`` ⇒ the script asserted
    nothing (a false exit-0) → the caller FAILs it. ``FEATURE_ASSERTIONS_RUN`` / ``NEGATIVE_ASSERTIONS_RUN``
    also end in ``ASSERTIONS_RUN=`` but the ``\\b`` word-boundary anchor makes this match ONLY the bare
    total (the ``_`` before ``ASSERTIONS`` in the named sentinels is a word char, so ``\\b`` does not match
    there)."""
    return _parse_last_sentinel(output, _ASSERTIONS_RUN_RE)


def _evaluate_release_coverage(
    *, total: Optional[int], feature: int, negative: int, coverage_req: tuple[int, int]
) -> tuple[bool, str]:
    """CR-V2-051 — the spec-derived, risk-floored acceptance verdict from the parsed sentinel counts + the
    DECLARED coverage requirement ``(n_flagship_features, n_safety_properties)`` from the Návrh design. Pure
    (unit-tested). A green boot alone is NOT a pass: every declared flagship feature needs ≥1 FEATURE
    assertion and every declared safety property needs ≥1 NEGATIVE assertion (the risky op MUST be rejected)
    — missing coverage is a FAIL, never a silent pass. With no declaration ``(0, 0)`` it degrades to the
    existing anti-empty floor (backward compatible)."""
    n_features, n_safety = coverage_req
    if not total:  # None (no sentinel) or 0 — the anti-empty floor.
        return False, f"anti-empty floor: ASSERTIONS_RUN={total} — the acceptance script ran no assertions"
    if feature < n_features:
        return False, (
            f"missing behavioural coverage: the design declared {n_features} flagship feature(s) but the "
            f"acceptance ran {feature} FEATURE assertion(s) — every flagship feature needs one"
        )
    if negative < n_safety:
        return False, (
            f"missing safety coverage: the design declared {n_safety} safety property/ies but the acceptance "
            f"ran {negative} NEGATIVE assertion(s) — every safety property needs a negative test (the risky "
            f"op MUST be rejected)"
        )
    return True, (
        f"release acceptance PASS — {total} assertions ({feature} feature / {negative} negative; "
        f"declared {n_features} feature / {n_safety} safety)"
    )


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


async def _run_release_acceptance(
    stack: _SmokeStack, project_slug: str, coverage_req: tuple[int, int] = (0, 0)
) -> tuple[bool, str, bool]:
    """Release-acceptance leg (gate-g-hardening GAP 1 A1; CR-V2-051 risk floor): run the project's black-box
    host-executable ``release_smoke_test.sh`` against the ALREADY-BOOTED isolated *stack* (NOT pytest in the
    prod image), requiring exit-0 AND the spec-derived coverage floor. Returns ``(ok, detail, skipped)``.

    **The risk floor (CR-V2-051):** *coverage_req* is ``(n_flagship_features, n_safety_properties)`` declared
    in the Návrh design (read by :func:`_declared_release_coverage`). Beyond the anti-empty floor
    (``ASSERTIONS_RUN>0``), the script must have run ≥1 FEATURE assertion per declared flagship feature and
    ≥1 NEGATIVE assertion per declared safety property (the risky op MUST be rejected). Missing coverage is a
    FAIL, never a silent pass — proving the app BOOTS is not proving it does what the spec promises nor that
    it refuses what the spec forbids. With no declaration ``(0, 0)`` it degrades to the anti-empty floor.

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
    total = _parse_assertions_run(out)
    feature = _parse_last_sentinel(out, _FEATURE_ASSERTIONS_RUN_RE) or 0
    negative = _parse_last_sentinel(out, _NEGATIVE_ASSERTIONS_RUN_RE) or 0
    ok, detail = _evaluate_release_coverage(total=total, feature=feature, negative=negative, coverage_req=coverage_req)
    return ok, detail, False


# ── obs-2 Part B Part 2: the per-app "Aktualizácie" changelog release gate (per-app-changelog-part2-gate.md) ─
# Two NEX-Studio-OWNED release blockers, both surfaced through the SAME boot leg → smoke_block path (2c): 2b —
# a STATIC check that the generated app's source still wires the scaffolded Aktualizácie FE tab; 2a — a
# BEHAVIOURAL probe that the booted backend actually SERVES ``GET /api/v1/release-notes`` with the completing
# version. A build must NOT reach done/deploy while either fails.

_RELEASE_NOTES_STATUS_RE = re.compile(r"RELEASE_NOTES_STATUS (\d+)")
# 2a probe retry budget (Fix 1, per-app-changelog-part2-followup.md): the release-notes handler reads many
# files, so its FIRST cold request can exceed the in-probe 10s timeout → no status line. Retry the probe for a
# bounded budget before declaring "neodpovedalo"; a REAL HTTP status (200/404/500) is evaluated on the FIRST
# response (no wasteful retry — a genuine drop won't self-heal). Mirrors ``_await_http_ready``'s loop/budget.
RELEASE_NOTES_PROBE_TIMEOUT = 60  # bounded retry budget for a cold/slow release-notes endpoint.
RELEASE_NOTES_PROBE_INTERVAL = 3  # seconds between probe retries.
#: 2b route detection — a wired ``/updates`` route in EITHER react-router form: JSX ``path="updates"`` /
#: ``path={"/updates"}`` OR the data-router object ``path: "updates"`` (a ``path:`` property, ``createBrowser
#: Router``). ``\bpath`` is word-anchored so a stray ``const filepath = "updates"`` does NOT match (it is not a
#: route). react-router nests without a leading slash → ``/?`` accepts both ``updates`` and ``/updates``.
#: Matched against comment-stripped source (a commented-out route is not a wired route).
_UPDATES_ROUTE_RE = re.compile(r"""\bpath\s*[=:]\s*\{?\s*["']/?updates\b""")
#: 2b page detection (broadened) — a source that IMPORTS an updates page module, so a page validly renamed away
#: from ``Updates*.tsx`` still counts. Keyed on an ``import … from "…updates…"`` module path (case-insensitive).
_UPDATES_PAGE_IMPORT_RE = re.compile(r"""\bfrom\s+["'][^"'\n]*updates[^"'\n]*["']""", re.IGNORECASE)
#: 2b nav detection (route-anchored, language-agnostic) — a nav entry whose TARGET path ends in ``…/updates``:
#: ``navigate("/updates")`` / ``to="/updates"`` / ``href="/updates"`` / ``to={"/updates"}`` AND the data-router
#: object form ``{ to: "/admin/updates", … }`` driven by ``navigate(item.to)`` — i.e. ``to:``/``href:`` with a
#: COLON as well as ``=``, and a PREFIXED path (a nav can legitimately live under ``/admin/updates``). Matching
#: only the ``=``/exact-``/updates`` form false-FAILED a compliant app (nex-payables 2026-07-10). Keyed on the
#: ``…/updates`` navigation TARGET, NOT the accent-stem — so an unrelated "Naposledy aktualizované" label can't
#: false-PASS and an English "Updates"/"Changelog" label can't false-FAIL. The trailing lookahead ``["'/]`` keeps
#: a distinct route like ``/updates-log`` from matching, and a bare route ``path="updates"`` (no to/href/navigate)
#: is still not a nav target.
_UPDATES_NAV_RE = re.compile(r"""(?:navigate\(|\b(?:to|href)\s*[=:]\s*)\{?\s*["'](?:[^"'\n]*/)?updates(?=["'/])""")


def _strip_ts_comments(text: str) -> str:
    """Strip ``/* … */`` block comments (covers JSX ``{/* … */}``) and ``// …`` line comments so a route/nav
    detection matches only LIVE source, never a commented-out mention (a false PRESENT). Deliberately naive (a
    ``//`` inside a string literal is dropped too) — acceptable here, the gate's patterns never live in URLs."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"(?m)//.*$", "", text)


def _fe_src_matches(fe_src: Path, pattern: re.Pattern[str], *, exclude: "frozenset[Path]" = frozenset()) -> bool:
    """True iff any ``frontend/src`` TypeScript/TSX source (comments stripped) matches *pattern*. Best-effort
    per file — an unreadable file is skipped, never fatal. The router + sidebar are somewhere under this tree;
    grepping the whole tree keeps the check robust to the generated app's exact file names. *exclude* skips the
    Updates PAGE file(s), whose own "Aktualizácie" heading / self-route must not satisfy the WIRING checks."""
    for pat in ("*.tsx", "*.ts"):
        for f in fe_src.rglob(pat):
            if f in exclude:
                continue
            try:
                body = _strip_ts_comments(f.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            if pattern.search(body):
                return True
    return False


def _check_aktualizacie_frontend(proj_root: Path) -> Optional[str]:
    """2b (STATIC, pure) — the generated app's ``frontend/src`` still wires the scaffolded *Aktualizácie* UI:
    ALL of an Updates page, a ``/updates`` route, and a ``/updates`` nav entry. Returns the specific
    missing-piece message (Slovak) or ``None`` when all three are present. No boot → unit-testable.

    Detectors are broadened/tightened for robustness (per-app-changelog-part2-followup.md Fix 2) so a valid
    app is never false-blocked and a dropped piece is never false-passed:

    * **Page** — any ``frontend/src/**/Updates*.tsx`` (a validly renamed page, not only ``pages/UpdatesPage.tsx``)
      OR a source importing an updates page module — so a rename isn't blocked.
    * **Route** — JSX ``path="updates"`` OR the data-router object ``path: "updates"``; word-anchored so a stray
      ``const filepath = "updates"`` is not mistaken for a route.
    * **Nav** — keyed on the ``/updates`` navigation TARGET (``navigate("/updates")`` / ``to`` / ``href``), NOT
      the accent-stem, so an unrelated "Naposledy aktualizované" label can't false-PASS (defeating the gate) and
      an English label can't false-FAIL.

    The route + nav are WIRING that lives OUTSIDE the page (the router / sidebar), so the page file(s) are
    excluded from those greps — a page's own self-route / heading must not paper over a dropped sidebar entry.
    Mirrors the flagship-app drop (nex-payables: page + route + nav all missing → the first, page, message). A
    missing ``frontend/src`` surfaces as the page message — the caller only runs this for a full web app."""
    fe_src = Path(proj_root) / "frontend" / "src"
    page_files = frozenset(fe_src.rglob("Updates*.tsx"))
    if not page_files and not _fe_src_matches(fe_src, _UPDATES_PAGE_IMPORT_RE):
        return "chýba stránka Aktualizácie (napr. frontend/src/pages/UpdatesPage.tsx)"
    if not _fe_src_matches(fe_src, _UPDATES_ROUTE_RE, exclude=page_files):
        return "chýba /updates route v routeri"
    if not _fe_src_matches(fe_src, _UPDATES_NAV_RE, exclude=page_files):
        return "chýba navigácia na /updates v menu (sidebar)"
    return None


def _bare_version(version_number: str) -> str:
    """Version number without a leading ``v`` — the normalisation the served list + the completing version are
    both compared under (``"v1.0.0"`` and ``"1.0.0"`` → ``"1.0.0"``)."""
    return version_number[1:] if version_number.startswith("v") else version_number


def _evaluate_release_notes(status: int, body: str, version_label: str) -> Optional[str]:
    """2a (BEHAVIOURAL verdict, pure) — from the probed ``GET /api/v1/release-notes`` response. Returns the
    blocker message (Slovak) or ``None`` when the endpoint served HTTP 200 AND a JSON list INCLUDING the
    completing version (matched ``v``-normalised on the ``version`` field). Unit-testable without booting."""
    if status != 200:
        return f"Aktualizácie chýba: /api/v1/release-notes vrátil HTTP {status} (očakávané 200)"
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return f"Aktualizácie chýba: /api/v1/release-notes nevrátil platný JSON (telo: {body[:120]!r})"
    if not isinstance(data, list):
        return "Aktualizácie chýba: /api/v1/release-notes nevrátil JSON zoznam"
    target = _bare_version(version_label)
    served = {_bare_version(str(item.get("version", ""))) for item in data if isinstance(item, dict)}
    if target not in served:
        return (
            f"Aktualizácie chýba: /api/v1/release-notes neobsahuje verziu v{target} "
            f"(vrátené: {sorted(served) or 'žiadne'})"
        )
    return None


def _release_notes_probe_src(port: int, *, path: str = "/api/v1/release-notes") -> str:
    """In-container stdlib probe (no curl — slim images ship none) that GETs the release-notes endpoint on the
    backend's own ``localhost:<port>`` and prints ``RELEASE_NOTES_STATUS <code>`` + ``RELEASE_NOTES_BODY
    <json>`` (compact single-line JSON) for :func:`_parse_release_notes_probe`. A transport error prints only
    ``RELEASE_NOTES_ERR`` (no status line) → the caller reports "neodpovedalo". Always exits 0 — the verdict is
    in the printed status, not the exit code."""
    url = f"http://localhost:{port}{path}"
    return (
        "import sys, urllib.request, urllib.error\n"
        "try:\n"
        f"    r = urllib.request.urlopen('{url}', timeout=10)\n"
        "    b = r.read().decode('utf-8', 'replace')\n"
        "    print('RELEASE_NOTES_STATUS', getattr(r, 'status', 200))\n"
        "    print('RELEASE_NOTES_BODY', b)\n"
        "except urllib.error.HTTPError as e:\n"
        "    b = e.read().decode('utf-8', 'replace')\n"
        "    print('RELEASE_NOTES_STATUS', e.code)\n"
        "    print('RELEASE_NOTES_BODY', b)\n"
        "except Exception as e:\n"
        "    print('RELEASE_NOTES_ERR', e)\n"
        "sys.exit(0)\n"
    )


def _parse_release_notes_probe(out: str) -> tuple[Optional[int], str]:
    """Parse the in-container probe stdout → ``(status, body)``. ``status`` is ``None`` when the probe printed
    no status line (a transport error / no response); ``body`` is the raw text after the ``RELEASE_NOTES_BODY``
    marker (empty when absent). Robust to interleaved ``docker compose`` warnings."""
    m = _RELEASE_NOTES_STATUS_RE.search(out)
    if m is None:
        return None, ""
    status = int(m.group(1))
    marker = "RELEASE_NOTES_BODY "
    body = ""
    for line in out.splitlines():
        idx = line.find(marker)
        if idx != -1:
            body = line[idx + len(marker) :]
            break
    return status, body


async def _probe_release_notes(
    base: list[str],
    exec_service: str,
    port: int,
    version_label: str,
    *,
    timeout: int = RELEASE_NOTES_PROBE_TIMEOUT,
    interval: int = RELEASE_NOTES_PROBE_INTERVAL,
) -> tuple[bool, str]:
    """2a (behavioural) — run the release-notes probe INSIDE *exec_service* (``docker compose exec``; host
    ports were stripped, so probe in-network) and evaluate the response via :func:`_evaluate_release_notes`.
    Returns ``(True, detail)`` when the endpoint serves 200 + a list including the completing version, else
    ``(False, "Aktualizácie chýba: …")``. Never raises.

    Fix 1 (per-app-changelog-part2-followup.md): the release-notes handler reads many files, so its FIRST cold
    request can exceed the in-probe 10s timeout → no status line → a good build would be false-blocked. So RETRY
    the probe for a bounded budget (mirrors :func:`_await_http_ready`) but ONLY when the probe COULDN'T RUN (no
    status line — a transport error / cold-start read still in flight). A REAL HTTP status (200/404/500) is
    evaluated on the FIRST response — no wasteful retry (a genuine 404/500 won't self-heal; a 200's verdict is
    already final). Only "the probe never got a response" yields the transient-fail "neodpovedalo" message."""
    cmd = base + ["exec", "-T", exec_service, "python", "-c", _release_notes_probe_src(port)]
    attempts = max(1, timeout // interval)
    last = "no response"
    for i in range(attempts):
        rc, out = await _compose_smoke_step(cmd, 30)
        status, body = _parse_release_notes_probe(out)
        if status is not None:  # a real HTTP status → final verdict now, never retry
            err = _evaluate_release_notes(status, body, version_label)
            if err:
                return False, err
            return True, "Aktualizácie OK — /api/v1/release-notes serves the completing version"
        last = out.strip()[-200:] or f"exit {rc}"  # probe couldn't run (cold start / transport error) → retry
        if i < attempts - 1:
            await asyncio.sleep(interval)
    return False, f"Aktualizácie chýba: /api/v1/release-notes neodpovedalo ({last})"


async def _run_aktualizacie_gate(stack: "_SmokeStack", proj_root: Path, version_label: str) -> tuple[bool, str]:
    """obs-2 Part B Part 2: the per-app *Aktualizácie* changelog release gate — 2b (static FE) THEN 2a
    (behavioural BE), both release blockers. Applies ONLY to a full web app (a backend serves the endpoint + a
    frontend hosts the tab); a pure API / worker / FE-less stack has no Aktualizácie tab requirement → SKIP (a
    pass). 2b (cheap, no container) runs before 2a (needs the booted backend). Never raises."""
    if stack.roles["backend"] is None or stack.roles["frontend"] is None:
        return True, "Aktualizácie gate SKIP — nie je plná web app (backend + frontend)"
    missing = _check_aktualizacie_frontend(proj_root)
    if missing:
        return False, f"Aktualizácie chýba vo frontende: {missing}"
    port = _compose_backend_port(stack.compose)
    if port is None:
        return True, "Aktualizácie gate SKIP — backend port neurčiteľný (2a sa nedá odmerať)"
    return await _probe_release_notes(stack.base, stack.roles["backend"], port, version_label)


async def _run_release_smoke(
    project_slug: str, version_label: str, coverage_req: tuple[int, int] = (0, 0)
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
        # obs-2 Part B Part 2 (per-app-changelog-part2-gate.md): the per-app Aktualizácie changelog is a
        # release blocker — 2b (static FE tab wired) THEN 2a (BE actually serves the completing version). A
        # failure is surfaced as a boot-leg FAIL → smoke_block → the runtime floor bites the verdict (2c);
        # acceptance never runs (mirrors a boot FAIL).
        akt_ok, akt_detail = await _run_aktualizacie_gate(stack, root, version_label)
        if not akt_ok:
            return (False, akt_detail), None
        acceptance = await _run_release_acceptance(stack, project_slug, coverage_req)
        return (boot_ok, boot_detail), acceptance


def _latest_navrh_gate_report_payload(db: Session, version_id: uuid.UUID) -> dict[str, Any]:
    """The payload of the plan gate_report (the AI Agent's design close carrying the plan + cross_cutting_rules
    + the CR-V2-052 release-coverage declaration), or ``{}`` when none is on record. Shared by
    :func:`_declared_release_coverage` (the oracle floor) and :func:`_release_coverage_brief` (the Auditor's
    adversarial brief).

    The plan gate_report used to be recorded in the Návrh phase (``stage='navrh'``); since nex-studio-visual
    (Director 2026-07-13) the task plan is built at Programovanie ENTRY, so its gate_report lands under
    ``stage='programovanie'`` (with ``payload['phase']=='navrh'``). Match it by the ``plan`` payload it
    uniquely carries — the per-task build gate_reports of the loop carry none — newest-first, regardless of
    which stage the plan gate_report landed under."""
    msgs = (
        db.execute(
            select(PipelineMessage)
            .where(
                PipelineMessage.version_id == version_id,
                PipelineMessage.stage.in_(("navrh", "programovanie")),
                PipelineMessage.author == "ai_agent",
                PipelineMessage.kind == "gate_report",
            )
            .order_by(PipelineMessage.seq.desc())
        )
        .scalars()
        .all()
    )
    for msg in msgs:
        if msg.payload and msg.payload.get("plan"):
            return msg.payload
    return {}


def _declared_release_coverage(db: Session, version_id: uuid.UUID) -> tuple[int, int]:
    """CR-V2-051 — the ``(n_flagship_features, n_safety_properties)`` the Návrh design DECLARED
    (:func:`_run_navrh_round` records ``flagship_features`` + ``safety_properties`` on the navrh gate_report —
    CR-V2-052). This is the risk floor the release-acceptance oracle enforces: ≥1 FEATURE assertion per
    flagship feature, ≥1 NEGATIVE assertion per safety property. Defensive: returns ``(0, 0)`` when no design
    is on record or the payload predates the declaration (graceful degradation to the anti-empty floor)."""
    payload = _latest_navrh_gate_report_payload(db, version_id)
    features = payload.get("flagship_features")
    safety = payload.get("safety_properties")
    n_features = len(features) if isinstance(features, list) else 0
    n_safety = len(safety) if isinstance(safety, list) else 0
    return n_features, n_safety


def _release_coverage_brief(db: Session, version_id: uuid.UUID) -> str:
    """CR-V2-053 — a Slovak block enumerating the Návrh-declared flagship features + safety properties for the
    Auditor's END brief, so the adversarial negative-test mandate names the EXACT risky ops to run and
    reject. Empty string when nothing was declared (the directive already tells the Auditor to challenge a
    missing declaration)."""
    payload = _latest_navrh_gate_report_payload(db, version_id)
    features = payload.get("flagship_features") or []
    safety = payload.get("safety_properties") or []
    if not features and not safety:
        return ""
    lines = ["   Deklarované pokrytie z Návrhu (over KAŽDÉ položku):\n"]
    if features:
        lines.append("   Flagship funkcie (každá potrebuje POZITÍVNE overenie voči bežiacej appke):\n")
        lines += [f"     - {f}\n" for f in features if isinstance(f, str)]
    if safety:
        lines.append(
            "   Bezpečnostné invarianty (každý potrebuje NEGATÍVNY test — zakázanú operáciu SÁM spusti, MUSÍ "
            "byť odmietnutá):\n"
        )
        for sp in safety:
            if isinstance(sp, dict):
                lines.append(f"     - {sp.get('name', '?')} → over odmietnutie: {sp.get('risky_op', '?')}\n")
    return "".join(lines)


# NOTE (CR-V2-021): the v1 ``_release_acceptance_satisfied`` (the gate_g PASS-button gate the v1 ``_board()``
# exposed) is REMOVED with the v1 board route. In v2 the release-acceptance smoke runs INSIDE the Auditor's
# Verifikácia round (:func:`_run_release_acceptance` in :func:`_settle_verifikacia_verdict`) and gates the
# PASS verdict THERE — the board no longer needs a separate gate_g PASS-button predicate.


async def run_dispatch(
    db: Session,
    version_id: uuid.UUID,
    on_event: Optional[claude_agent.EventCallback] = None,
    directive: Optional[str] = None,
    *,
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

    (The v1 ``gate_e_dispatch`` sub-flow selector param was removed in CR-V2-017 — the 4-phase model has
    no Gate E, the Auditor's upfront review after Návrh replaces it.)
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

    # Vizuál round (CR-1, nex-studio-visual — spec §3.A/§3.B/§3.C). Brings the project's frontend up LIVE in
    # the isolated Vite dev-server sandbox so the Manažér can WALK the running app in the cockpit + approve it
    # before Programovanie. On a FRESH entry (``directive`` None) it just spins the preview up + hands the
    # Manažér the URL to review; on a Manažér CHANGE-REQUEST (``directive`` set — the framed relay message) it
    # DISPATCHES the AI to apply the change to the live FE (HMR reflects it). Owns its own settle, so it
    # early-returns like the other rounds. ``directive`` is threaded (two-way comms, mirroring navrh/programovanie).
    if stage == "vizual":
        return await _run_vizual_round(db, state, on_event=on_event, directive=directive, on_message=on_message)

    # Programovanie round (CR-V2-012): the AI Agent's SELF-CHECKING coding loop executing the Návrh task plan
    # (implement + own tests/verification per task; NO per-task Auditor — the independent Auditor verifies once
    # at Verifikácia). Owns its own multi-task lifecycle + the SHARED dial-settle at the end, so it
    # early-returns here. ``directive`` (an uprav/answer/pokracovat re-dispatch) seeds attempt 1 of the resumed
    # task (two-way comms — the Coordinator relay is retired in v2).
    if stage == "programovanie":
        return await _run_build_round(db, state, on_event=on_event, directive=directive, on_message=on_message)

    # Verifikácia round (CR-V2-014): the independent Auditor's END verification — release-acceptance against
    # INTERNAL FIXTURES (via _run_release_smoke; never a customer instance — OQ-3/D6) + adversarial spot-checks
    # + explicit §4 hard-security verification. Emits ONE kind=verdict; PASS → dial-governed end sign-off to
    # Hotovo (no-silent-done invariant), FAIL → bounded fix↔re-verify loop back to the AI Agent (AUDITOR_LOOP_MAX),
    # then escalate. Owns its own smoke → verdict → settle lifecycle, so it early-returns here (the v1 gate_g
    # Coordinator-relay verify_done / _infer_regate_entry_stage Director PASS/FAIL regate inference is replaced).
    if stage == "verifikacia":
        return await _run_verifikacia_round(db, state, on_event=on_event, directive=directive, on_message=on_message)

    # 4-phase dispatch. The v1 stage-specific routing (gate_e per-question round / build per-task loop /
    # task_plan incremental passes / kickoff triage / release publish) is collapsed: each phase owns its own
    # round runner above (Návrh → _run_navrh_round + upfront review CR-V2-013; Programovanie → _run_build_round
    # CR-V2-012; Verifikácia → _run_verifikacia_round CR-V2-014). The ONLY phase that reaches this generic
    # single-turn path is Príprava (the interactive Zadanie→Špecifikácia dialogue, CR-V2-010) — plus a Manažér
    # uprav/ask/answer ``directive`` re-dispatch of any phase (the framed message IS the prompt; direct comms).
    # The v1 ``_run_gate_e_round`` per-question machinery is REMOVED wholesale (CR-V2-013) — there is no Gate-E
    # routing anywhere in this 4-phase dispatch.
    if directive is not None:
        prompt = directive  # the Manažér's framed uprav/ask/answer message IS the prompt (direct comms)
    elif stage == "priprava":
        # Príprava round (CR-V2-010): the init prompt + the interactive spec-dialogue brief (read Zadanie →
        # systematize → ask until understood → propose → write the Špecifikácia .md). DESIGN-BEARING prompt.
        # Fast-fix (CR-V2-028) takes a lightweight directive-IS-the-brief variant (no spec dialogue).
        prompt = _priprava_directive(db, state.version_id, flow_type=state.flow_type)
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
            # Audit P2 (2026-07-12): but if NOTHING committed (detected_commit_count 0) the deliverable is
            # definitively ABSENT — settle BLOCKED/agent_error, NOT awaiting_manazer, so the phase-ADVANCE verbs
            # (approve_spec / schvalit) are hidden (a non-expert must not be able to approve an empty spec after
            # a timeout). Keep awaiting_manazer only when work actually landed (count >= 1: review & continue).
            if result.lost_work.get("detected_commit_count", 0) < 1:
                state.status = "blocked"
                state.block_reason = "agent_error"
                state.next_action = result.lost_work["next_action"]
                db.flush()
                return state
            state.status = "awaiting_manazer"
            state.next_action = result.lost_work["next_action"]
            db.flush()
            return state
        # Parse-retries exhausted (CR-NS-022 §2): settle blocked directly (no Coordinator relay — retired in
        # v2; the AI Agent reports to the Manažér itself, design §2.2). CR-V2-029: record a readable
        # notification (+ raw-output excerpt) so the AI Agent tab is never left empty.
        state.status = "blocked"
        state.block_reason = "parse_exhaustion"  # R4 (D1): worker produced no parseable output after retries
        state.next_action = "Blokované — agent nevrátil platný výstup. Usmerni (Uprav) alebo odpovedz."
        await _record_parse_exhaustion(
            db,
            state,
            stage=stage,
            result=result,
            human_hint="Skús znova (Uprav) alebo upresni zadanie.",
            on_message=on_message,
        )
        db.flush()
        return state

    if result.kind == "framework_issue":
        # §15 escalation to Dedo (Director obs #6): the agent hit a problem it CANNOT fix because the fix
        # needs a change to NEX Studio ITSELF — settle blocked/framework_issue + deliver the message to Dedo
        # (NO recovery actions for the Manažér — only Dedo clears it). Same helper as the conversation spine.
        return await _settle_framework_issue(db, state, result, stage=stage, on_message=on_message)

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
    # FAST-FIX EXCEPTION (CR-V2-028): the fast-fix Príprava is lightweight — the directive IS the brief and
    # NO Špecifikácia is written, so the artifact gate must NOT fire (it would over-block the short path).
    if stage == "priprava" and result.kind == "gate_report" and state.flow_type != "fast_fix":
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


# ── Spine STEP 1: the conversation loop (REPLACES run_dispatch for a 'conversation' build) ──────────


def _conversation_directive(db: Session, version_id: uuid.UUID) -> str:
    """The spine's minimal, PHASE-FREE brief for a conversation turn (STEP 1; REDESIGN §5/§6).

    REPLACES the phase-specific ``_priprava_directive`` chain — it carries NO phase semantics
    (no Zadanie→Špecifikácia state machine, no artifact gate, no stage advance). It just tells the AI
    partner to continue the live 1:1 with the Manažér — read the append-only log for the whole
    conversation context, react like a human (celé vety, žiaden žargón), answer his last message OR ask
    what it needs (one thing at a time, with a recommendation), and be honest (surface the risk / wobbly
    part itself). The status-block contract is appended downstream at the :func:`invoke_agent` chokepoint
    (``_status_block_instruction``), so the turn still ends with the machine status block the engine parses.

    STEP 2 (Špecifikácia) names the concrete artifact paths WITHOUT adding phase semantics: the partner keeps
    ONE ``specification.md`` on disk as the single source of truth (MD-1 = A — no second copy anywhere) and
    may read the optional ``customer-requirements.md`` Zadanie if it exists. There is NO gate, NO stage
    advance — approval is a separate Manažér action (``approve_spec``) that only freezes the file.
    """
    version_number = db.execute(select(Version.version_number).where(Version.id == version_id)).scalar_one()
    zadanie_rel = f"{_version_spec_rel(version_number)}/customer-requirements.md"
    spec_rel = _priprava_spec_rel(version_number)
    return (
        "Pokračuj v živom rozhovore 1:1 s Manažérom projektu — presne ako Dedo so Zoltánom. "
        "Prečítaj si doterajší denník správ (to je kontext celej konverzácie) a reaguj po ľudsky, "
        "celými vetami bez žargónu a bez vysypaných kódov: odpovedz na jeho poslednú správu, alebo sa "
        "opýtaj, čo potrebuješ vedieť — JEDNO NARAZ, s odporúčaním. Buď proaktívny a čestný: ak je niečo "
        "riziko alebo vratké, povedz to sám. Nič nerozhoduj za neho — vysvetli a nechaj ho rozhodnúť.\n"
        f"Zadanie od zákazníka je v `{zadanie_rel}` — prečítaj ho AK EXISTUJE; je NEPOVINNÉ, ak ho niet, "
        "staviame Špecifikáciu od nuly z rozhovoru.\n"
        f"Ako sa priebežne dohodneme, udržiavaj Špecifikáciu ako JEDEN dokument v `{spec_rel}` (adresár "
        "vytvor ak treba) — je to jediný zdroj pravdy; keď Manažér povie, že schvaľuje, musí byť kompletný. "
        "NEVKLADAJ celý text `specification.md` do svojej odpovede v rozhovore — súbor na disku je úplná "
        "kópia; v odpovedi len povedz, čo si do neho zapísal alebo zmenil (napr. „aktualizoval som "
        "specification.md“), aby denník ostal zhrnutím a súbor jedinou plnou kópiou."
    )


def _kontrola_directive(db: Session, version_id: uuid.UUID, *, smoke_block: str) -> str:
    """STEP 5 (step5-kontrola-design.md K-1/K-2/K-5): the partner's HONEST self-check brief — the SAME AI
    Agent that wrote the code checks its OWN work after Programovanie and reports PEVNÉ / VRATKÉ as an
    ordinary conversation message (``kind=gate_report`` — NEVER a ``verdict``; a verdict at Verifikácia is a
    release PASS the deploy path reads, and kontrola must NEVER touch that path).

    Honesty anchor (K-1=A): the engine ALREADY ran the app in an ephemeral isolated stack (boot + acceptance)
    and its result is fed below (``smoke_block``) + is already on the append-only log — the partner must
    RECONCILE with it, not around it (it cannot claim PEVNÉ over a machine that says broken). The release
    oracle is the approved ``specification.md``. K-5: the depth is the BASELINE self-check (real boot +
    acceptance + honest spec reconciliation); per-feature / negative coverage is NOT enforced in the
    conversation flow — if something is unproven, the partner names it HONESTLY under VRATKÉ instead of
    claiming it. Distinct from :func:`_verifikacia_directive` (the INDEPENDENT Auditor's release verdict):
    kontrola is the partner's own read of its work in the rozhovor, signs nothing, deploys nothing."""
    version_number = db.execute(select(Version.version_number).where(Version.id == version_id)).scalar_one()
    spec_rel = _priprava_spec_rel(version_number)
    return (
        "KONTROLA — čestná sebakontrola po Programovaní. Toto robíš TY (ten istý AI partner, čo písal kód), "
        "NIE nezávislý Auditor, a NIE je to release verdikt: nič nepodpisuješ ani nenasadzuješ — len po ľudsky "
        "povieš, čo je hotové a čo ešte nie, a vrátiš kormidlo Manažérovi.\n"
        f"1. Prečítaj si SCHVÁLENÚ Špecifikáciu `{spec_rel}` (jediný zdroj pravdy) a porovnaj ju s reálnym "
        "stavom kódu — robí appka to, čo Špecifikácia sľúbila?\n"
        "2. ČESTNE sa vyrovnaj s behom appky: engine ju UŽ NAOZAJ spustil v izolovanom kontajneri (boot + "
        "akceptačný beh) — výsledok je nižšie a je aj v denníku. Zohľadni ho. Ak je červený, NEMÔŽEŠ tvrdiť, "
        "že je všetko PEVNÉ — priznaj to.\n"
        + smoke_block
        + "3. Napíš PO ĽUDSKY (celé vety, žiaden žargón, žiadne vysypané kódy) DVE časti:\n"
        "   - PEVNÉ: čo je overené a drží (oproti Špecifikácii aj behu appky).\n"
        "   - VRATKÉ: čo je rizikové, nedotiahnuté alebo NEOVERENÉ. Čo si nestihol reálne overiť, patrí SEM — "
        "radšej čestne priznaj neistotu, než sľúbiť PEVNÉ.\n"
        "4. Nič nerozhoduj za Manažéra a nič neschvaľuj — kontrola len ukáže stav a nechá ho rozhodnúť.\n"
        "Ukonči odpoveď štruktúrovaným stavovým výstupom (F-007-orchestration-cockpit.md §5.3) ako "
        "`gate_report` (NIE verdict)."
    )


async def run_conversation_turn(
    db: Session,
    version_id: uuid.UUID,
    on_event: Optional[claude_agent.EventCallback] = None,
    directive: Optional[str] = None,
    *,
    on_message: Optional[MessageCallback] = None,
) -> Optional[PipelineState]:
    """Run ONE spine conversation turn and SETTLE — the non-phase loop that REPLACES :func:`run_dispatch`
    for a ``mode='conversation'`` build (STEP 1; REDESIGN §5/§6).

    A deliberately SIMPLE turn: no ``STAGE_ACTOR`` walk, no ``_settle_phase_boundary``, no ``_next_stage``,
    no artifact gate. It mirrors ``run_dispatch``'s guards (reload the ``agent_working`` state; nothing to
    run otherwise), invokes the partner through the SHARED :func:`invoke_agent_with_parse_retry` (ALWAYS —
    never ``invoke_claude`` raw, never a parse without retry; INVARIANT), threads ``on_event`` / ``on_message``
    exactly as ``run_dispatch`` does (so the live WS feed + incremental broadcast are unchanged), and then
    SETTLES to the Manažér — it NEVER silently advances a phase (the whole point of cutting the automaton):

      * :class:`ParseFailure` → ``blocked`` / ``block_reason='parse_exhaustion'`` via
        :func:`_record_parse_exhaustion` (readable notification + raw excerpt, never an empty screen).
      * ``kind in {question, blocked}`` → ``blocked`` / ``block_reason='agent_question'`` (the partner asked
        the Manažér something — the board offers ``answer``, relayed back as the next turn).
      * a normal reply → ``awaiting_manazer`` (the partner answered — the Manažér reads it and writes back).

    Returns the settled state (``None`` if the version/state vanished). The turn carries the valid
    ``stage='priprava'`` + ``actor='ai_agent'`` (both already in the CHECK sets) — the ``mode`` column, not
    the stage, is what routed us here."""
    state = _get_state(db, version_id)
    if state is None:
        return None
    if state.status != "agent_working":
        # Mirror run_dispatch's guard — a settled/paused build has nothing to run (a stale re-entry, or a
        # Manažér intervention that already moved the state). Return it untouched.
        return state
    # STEP 3 (step3-plan-design.md FIX3): a durable compose_plan directive marker (recorded by
    # ``apply_action(zostav_plan)``) delegates this turn to the incremental plan round — a RESTART-SAFE DB
    # read, NOT the in-memory ``directive`` arg (None for zostav_plan, lost on restart). SOLELY the marker.
    if _pending_compose_plan_marker(db, version_id):
        return await _run_conversation_plan_round(db, state, on_event=on_event, on_message=on_message)
    # STEP 5 (step5-kontrola-design.md K-1): a durable check directive marker (recorded by
    # ``apply_action(skontrolovat)``) delegates this turn to the honest self-check round — the SAME restart-safe
    # DB read as the compose_plan marker (NOT the in-memory ``directive`` arg, None for skontrolovat + lost on
    # restart). The two markers carry distinct payload flags (compose_plan vs check), so checking one after the
    # other is unambiguous. Both keep ``current_stage='priprava'`` so subsequent turns route back here.
    if _pending_check_marker(db, version_id):
        return await _run_conversation_kontrola_round(db, state, on_event=on_event, on_message=on_message)
    stage = state.current_stage
    actor = state.current_actor
    prompt = directive if directive is not None else _conversation_directive(db, version_id)
    result = await invoke_agent_with_parse_retry(
        db,
        version_id=version_id,
        role=actor,
        stage=stage,
        prompt=prompt,
        on_event=on_event,
        on_message=on_message,
        # metrics-v3-three-phases.md Part 1: a pre-build conversation turn is Návrh work (alignment +
        # specification) — stamp 'navrh' for metrics while stage stays 'priprava' (the routing/gate register).
        metrics_phase="navrh",
    )

    if isinstance(result, ParseFailure):
        # The partner produced no parseable status block after the bounded retries → settle blocked with a
        # readable notification (+ raw excerpt) so the conversation is never left on an empty screen.
        state.status = "blocked"
        state.block_reason = "parse_exhaustion"
        state.next_action = "Blokované — AI partner nevrátil platný výstup. Napíš mu znova alebo upresni."
        await _record_parse_exhaustion(
            db,
            state,
            stage=stage,
            result=result,
            human_hint="Napíš mu znova alebo upresni, čo potrebuješ.",
            on_message=on_message,
            # metrics-v3-followup.md C1: a pre-build conversation failure is Návrh work — stamp 'navrh' for
            # metrics while stage stays 'priprava' (mirrors the SUCCESS turn's metrics_phase='navrh').
            metrics_phase="navrh",
        )
        db.flush()
        return state

    if result.kind == "framework_issue":
        # §15 escalation to Dedo (Director obs #6): the partner hit a problem it CANNOT fix because the fix
        # needs a change to NEX Studio ITSELF — settle blocked/framework_issue + deliver the message to Dedo
        # (NO recovery actions for the Manažér — only Dedo clears it).
        return await _settle_framework_issue(db, state, result, stage=stage, on_message=on_message)

    if result.kind in ("question", "blocked"):
        # The partner asked the Manažér something → blocked on an agent_question so the board offers answer.
        state.status = "blocked"
        state.block_reason = "agent_question"
        state.next_action = f"AI partner sa pýta: {result.question}"
        db.flush()
        return state

    # A normal reply → SETTLE for the Manažér (never a phase advance — the spine always hands the turn back).
    state.status = "awaiting_manazer"
    state.next_action = "AI partner odpovedal — pokračuj v rozhovore."
    db.flush()
    return state


# ── Konzultácia: the read-only advisory turn on a FINISHED version (konzultacia-mode.md Part 1) ──────

#: The version's terminal resting next_action after a consult answer settles — still done/released.
_CONSULT_REST_NEXT_ACTION = "Konzultácia — verzia je hotová. Napíš ďalšiu otázku, alebo založ novú verziu z požiadavky."


def _latest_manazer_message_text(db: Session, version_id: uuid.UUID) -> Optional[str]:
    """The content of the LATEST ``manazer``-authored message for a version (the consult question), or None.

    Restart-safe source for the consult directive: the Manažér's message is recorded (:func:`_begin_consult`
    on the settled path, or the in-flight enqueue on the drain path) BEFORE the turn runs, so it is the latest
    ``manazer`` message when :func:`run_consult_turn` reads it — no in-memory directive threading needed."""
    return db.execute(
        select(PipelineMessage.content)
        .where(PipelineMessage.version_id == version_id, PipelineMessage.author == "manazer")
        .order_by(PipelineMessage.seq.desc())
        .limit(1)
    ).scalar_one_or_none()


def _consult_directive(db: Session, version_id: uuid.UUID) -> str:
    """The read-only Konzultácia brief (konzultacia-mode.md Part 1.3) — a sibling of :func:`_conversation_directive`
    WITHOUT the ``specification.md`` write instruction.

    Contract: the partner is in read-only advisory mode over a FINISHED version — it answers/analyses/explains
    grounded in the project's specs, code, plan, metrics and history (Read/Grep/Glob only) and must change
    NOTHING. If the Manažér asks for a change to the app, it does NOT attempt it — it states plainly that it
    needs a new version and emits the ``change_request`` marker (Part 2) the cockpit turns into "Založiť novú
    verziu z tejto požiadavky". The status-block contract is appended downstream at the :func:`invoke_agent`
    chokepoint, so the turn still ends with the machine block the engine parses (stage=``done``)."""
    question = _latest_manazer_message_text(db, version_id)
    ask = f"Manažér sa ťa v režime Konzultácia pýta:\n{question}\n\n" if question else ""
    return (
        ask + "Si v režime KONZULTÁCIA nad HOTOVOU verziou (dokončená / nasadená). Toto je LEN poradný "
        "rozhovor: odpovedz, analyzuj a vysvetli — po ľudsky, celými vetami. Opri sa o Špecifikáciu, kód, "
        "plán úloh, metriky a históriu projektu; máš práva LEN na ČÍTANIE (Read/Grep/Glob). NIČ NEMENÍŠ — "
        "nepíšeš do súborov, nespúšťaš príkazy, nekomituješ; ani specification.md neupravuj.\n"
        "Ak Manažér žiada ZMENU aplikácie (novú funkciu, opravu správania, úpravu), NEROB ju — jasne po "
        "ľudsky povedz, že si to vyžaduje NOVÚ VERZIU, a v stavovom bloku vráť pole `change_request` s krátkym "
        "`summary` (čo treba spraviť, jazykom Manažéra) a voliteľným `title`. Ak ide len o otázku alebo "
        "vysvetlenie, `change_request` nevypĺňaj."
    )


def _arm_consult_dispatch(db: Session, state: PipelineState) -> None:
    """Arm a read-only Konzultácia turn WITHOUT the build mutations of :func:`_begin_dispatch` (Part 1.4).

    Deliberately NOT ``_begin_dispatch``: no baseline-SHA capture (the turn cannot commit — nothing to audit)
    and NO phase move — ``current_stage`` stays ``done`` so the runner routes to :func:`run_consult_turn` and
    the version returns to its terminal resting state (still done/released) after the answer. It only flips to
    ``agent_working`` for the duration of the turn (a transient working state, not a sticking status) and arms
    the durable single-flight flag so a second consult message queues behind this one instead of running
    concurrently. The status set-listener clears ``dispatch_in_flight`` when the turn settles back off
    ``agent_working``."""
    state.current_actor = AI_AGENT_ROLE
    state.dispatch_in_flight = True
    state.status = "agent_working"
    state.next_action = "AI partner odpovedá v režime Konzultácia (len číta, nič nemení)."
    db.flush()


def _begin_consult(db: Session, state: PipelineState, text: str) -> PipelineState:
    """Record the Manažér's consult message + arm the read-only turn on a terminal version (Part 1.1).

    The message is recorded at ``stage='done'`` with a ``consult`` marker (NO ``payload.phase`` in a
    comparison bucket — it folds into system-overhead, Part 1.5). Then :func:`_arm_consult_dispatch` arms the
    read-only turn (no ``_begin_dispatch`` mutation). Returns the armed state."""
    _record_message(
        db,
        version_id=state.version_id,
        stage="done",
        author="manazer",
        recipient=AI_AGENT_ROLE,
        kind="question",
        content=text,
        payload={"consult": True, "phase": "done"},
    )
    _arm_consult_dispatch(db, state)
    return state


async def run_consult_turn(
    db: Session,
    version_id: uuid.UUID,
    on_event: Optional[claude_agent.EventCallback] = None,
    *,
    on_message: Optional[MessageCallback] = None,
) -> Optional[PipelineState]:
    """Run ONE read-only Konzultácia turn on a FINISHED version and SETTLE back to terminal rest (Part 1).

    The consult counterpart of :func:`run_conversation_turn`: it drives the SAME shared
    :func:`invoke_agent_with_parse_retry` chokepoint but with three hard differences that make it read-only
    and metrics-safe:

      * **Read-only tool profile + OS-isolated sidecar** — ``allowed_tools=CONSULT_READ_ONLY_TOOLS``
        (Read/Grep/Glob only) AND ``sandbox=True`` so the turn runs in an ephemeral container where the
        project is KERNEL-enforced ``:ro`` and the host is unreachable (konzultacia-sidecar-sandbox.md); the
        turn provably cannot write/exec/commit (Part 1.2 tool profile + the kernel guarantee).
      * **The read-only directive** (:func:`_consult_directive`) — no ``specification.md`` write instruction
        (Part 1.3); a change request is routed to a NEW version, never attempted here.
      * **Metrics safety** — ``stage='done'`` + NO ``metrics_phase`` → the usage folds into the ``done``
        bucket (system-overhead), never a navrh/programovanie/verifikacia build phase (Part 1.5).

    It NEVER calls ``_begin_dispatch`` and NEVER advances a phase: on any outcome the version returns to its
    terminal resting state (``status='done'``, ``current_stage='done'`` — still done/released). Guards on
    ``agent_working`` like ``run_dispatch`` (a stale re-entry / already-settled turn returns untouched).
    Returns the settled state (``None`` if the version/state vanished)."""
    state = _get_state(db, version_id)
    if state is None:
        return None
    if state.status != "agent_working":
        # Not armed (stale re-entry, or the turn already settled) — nothing to run; leave it terminal.
        return state
    result = await invoke_agent_with_parse_retry(
        db,
        version_id=version_id,
        role=AI_AGENT_ROLE,
        stage="done",
        prompt=_consult_directive(db, version_id),
        on_event=on_event,
        on_message=on_message,
        allowed_tools=CONSULT_READ_ONLY_TOOLS,
        # konzultacia-sidecar-sandbox.md Part 2: run the read-only consult in the OS-isolated sidecar
        # (KERNEL-``:ro`` project, host unreachable). Honest in-process fallback if the sidecar is
        # unavailable (logged by claude_agent._invoke_once). Build turns never set this.
        sandbox=True,
        # Part 1.5 metrics safety: NO metrics_phase → the recorded message carries no payload.phase, so
        # aggregate_usage_by_phase folds the consult usage/timing into the 'done' bucket (system-overhead),
        # NEVER a COMPARISON_PHASES bucket. The navrh/programovanie/verifikacia totals are untouched.
    )
    if isinstance(result, ParseFailure):
        # A read-only consult that produced no parseable block: record the failure notification (with the
        # turn's metrics, still stage='done') and return to terminal rest — a finished version never shows a
        # 'blocked' recovery board; the Manažér just asks again.
        await _record_parse_exhaustion(
            db,
            state,
            stage="done",
            result=result,
            human_hint="Skús mu napísať znova alebo upresni otázku.",
            on_message=on_message,
        )
    # Part 1.4: NO phase advance, NO sticking agent_working — settle back to the terminal resting state so the
    # build state (status/current_stage/baseline SHA) is exactly what it was before the consult. The status
    # set-listener clears the single-flight flag + baseline on this transition off 'agent_working'.
    state.status = "done"
    state.next_action = _CONSULT_REST_NEXT_ACTION
    db.flush()
    return state


def _pending_compose_plan_marker(db: Session, version_id: uuid.UUID) -> bool:
    """True iff the LATEST pipeline message is an unprocessed compose_plan directive (STEP 3 restart-safe
    trigger; step3-plan-design.md FIX3).

    ``apply_action(zostav_plan)`` records a ``manazer→ai_agent`` ``kind='directive'`` marker
    (``payload.compose_plan``) and arms ``agent_working``; the plan round is driven SOLELY by this durable DB
    marker (the in-memory dispatch directive is None for ``zostav_plan`` and is lost on a restart, so it can
    never be the trigger). The marker IS the latest message the instant the round fires — ``apply_action``
    records nothing after it — and once the round records its passes / gate_report (higher ``seq``) it is no
    longer latest → not pending, so a stale re-entry or a follow-up Manažér message never re-runs the plan."""
    latest = db.execute(
        select(PipelineMessage)
        .where(PipelineMessage.version_id == version_id)
        .order_by(PipelineMessage.seq.desc())
        .limit(1)
    ).scalar_one_or_none()
    return (
        latest is not None
        and latest.kind == "directive"
        and latest.author == "manazer"
        and isinstance(latest.payload, dict)
        and bool(latest.payload.get("compose_plan"))
    )


def _pending_check_marker(db: Session, version_id: uuid.UUID) -> bool:
    """True iff the LATEST pipeline message is an unprocessed kontrola directive (STEP 5 restart-safe trigger;
    step5-kontrola-design.md K-1) — the exact mirror of :func:`_pending_compose_plan_marker`.

    ``apply_action(skontrolovat)`` records a ``manazer→ai_agent`` ``kind='directive'`` marker
    (``payload.check``) and arms ``agent_working``; the kontrola round is driven SOLELY by this durable DB
    marker (the in-memory dispatch directive is None for ``skontrolovat`` and is lost on a restart, so it can
    never be the trigger). The marker IS the latest message the instant the round fires — ``apply_action``
    records nothing after it — and once the round records its smoke legs / gate_report (higher ``seq``) it is
    no longer latest → not pending, so a stale re-entry or a follow-up Manažér message never re-runs kontrola."""
    latest = db.execute(
        select(PipelineMessage)
        .where(PipelineMessage.version_id == version_id)
        .order_by(PipelineMessage.seq.desc())
        .limit(1)
    ).scalar_one_or_none()
    return (
        latest is not None
        and latest.kind == "directive"
        and latest.author == "manazer"
        and isinstance(latest.payload, dict)
        and bool(latest.payload.get("check"))
    )


async def _run_conversation_plan_round(
    db: Session,
    state: PipelineState,
    *,
    on_event: Optional[claude_agent.EventCallback] = None,
    on_message: Optional[MessageCallback] = None,
) -> PipelineState:
    """STEP 3 (step3-plan-design.md): compose the task plan in the conversation register — the plan round the
    durable compose_plan marker delegates to.

    REUSES the proven incremental machinery (:func:`_generate_incremental_plan`, ``stage='priprava'``) —
    skeleton pass + per-feat passes + :data:`MAX_PLAN_FEATS` + fail-closed HALT — NEVER a whole-tree parse
    off one turn. MD-2: it re-reads the CURRENT ``specification.md`` and rebuilds the plan IN PLACE
    (:func:`_write_task_plan`'s SAVEPOINT drop-and-recreate), so a repeat "Zostaviť plán" can never diverge
    from the frozen spec. It NEVER advances a phase (the spine invariant — no ``_settle_phase_boundary`` /
    ``_next_stage`` / independent-Auditor gate): on success it settles ``awaiting_manazer`` with
    ``current_stage`` UNCHANGED (``priprava``), handing the turn back to the Manažér. A plan-pass failure
    already settled (blocked / awaiting_manazer) inside the shared machinery — returned directly."""
    version_number = db.execute(select(Version.version_number).where(Version.id == state.version_id)).scalar_one()
    spec_rel = _priprava_spec_rel(version_number)
    # MD-2: re-read the CURRENT (approved, frozen) Špecifikácia — the single source of truth — and build the
    # plan from its present state. Prepended as the skeleton pass's framed brief (like _navrh_directive names
    # the Špecifikácia path). The skeleton directive itself carries the field/granularity/fence contract.
    directive = (
        f"Zostav plán úloh (EPIC → FEAT → TASK) zo schválenej Špecifikácie. NAJPRV si ZNOVA prečítaj "
        f"`{spec_rel}` (jediný zdroj pravdy) a plán postav podľa jej AKTUÁLNEHO stavu."
    )
    settled = await _generate_incremental_plan(
        db,
        state,
        stage="priprava",
        on_event=on_event,
        directive=directive,
        on_message=on_message,
        # metrics-v3-three-phases.md Part 1: the task plan is Návrh work — attribute its tokens to 'navrh'
        # for metrics while stage stays the conversation register's 'priprava' (routing unchanged).
        metrics_phase="navrh",
    )
    if settled is not None:
        return settled  # a plan-pass failure already settled (blocked / awaiting_manazer)
    # No phase advance — the spine hands the turn back to the Manažér (current_stage stays 'priprava').
    state.status = "awaiting_manazer"
    state.next_action = "Plán úloh je zostavený — pozri ho a pokračuj v rozhovore."
    db.flush()
    return state


def _apply_hotovo_signoff(
    db: Session,
    version_id: uuid.UUID,
    state: PipelineState,
    *,
    content: str = "Označené ako hotové — verzia je pripravená na nasadenie.",
) -> None:
    """Record the manager's SHA-anchored Hotovo signature + settle the conversation build to terminal ``done``.

    Shared by the ``hotovo`` action (STEP 6, the manager's explicit sign-off) and the drifted-version
    ``overit_znovu`` auto re-anchor (audit #8, Director 2026-07-12 chose one-click auto re-sign): a GREEN
    re-verify re-signs to the CURRENT HEAD without a second click. The caller MUST have already gated
    (conversation + spec approved + kontrola passed + NOT floor-red). Mirrors the verdict path's SHA-anchor
    ladder: (re)generate + commit the user-facing RELEASE_NOTES BEFORE anchoring so the note rides the signed
    commit; tag ``v{version}`` at HEAD so a later HEAD move past it AUTO-UN-VERIFIES (→ ``hotovo_drift``);
    ``hotovo_sha`` stays None when the repo is unreadable (→ ``hotovo_unbound``).
    """
    proj_root = claude_agent.PROJECTS_ROOT / _project_slug_for_version(db, version_id)
    _vnum = db.execute(select(Version.version_number).where(Version.id == version_id)).scalar_one()
    _commit_release_note(db, version_id, proj_root, _vnum)
    hotovo_sha = _repo_head(proj_root)
    if hotovo_sha:
        _git_tag_version(proj_root, _vnum, hotovo_sha)
    signoff_payload: dict[str, Any] = {"phase": "priprava", "hotovo": True}
    if hotovo_sha:
        signoff_payload["hotovo_sha"] = hotovo_sha
    _record_message(
        db,
        version_id=version_id,
        stage="priprava",
        author="manazer",
        recipient="ai_agent",
        kind="notification",
        content=content,
        payload=signoff_payload,
    )
    state.current_stage = "done"
    state.current_actor = "ai_agent"
    state.status = "done"
    state.next_action = "Verzia je hotová — nasadenie (UAT/PROD) je samostatný krok."
    db.flush()


async def _run_conversation_kontrola_round(
    db: Session,
    state: PipelineState,
    *,
    on_event: Optional[claude_agent.EventCallback] = None,
    on_message: Optional[MessageCallback] = None,
) -> PipelineState:
    """STEP 5 (step5-kontrola-design.md): the honest self-check round the durable check marker delegates to.

    After Programovanie completes, the partner (the SAME AI Agent that wrote the code — NOT the independent
    Auditor) honestly checks its own work and reports PEVNÉ / VRATKÉ as an ordinary conversation message
    (``kind='gate_report'`` — NEVER a ``verdict``), STAYING at ``current_stage='priprava'`` so it is
    INVISIBLE to the release/deploy path (a verdict at ``verifikacia`` reads as a release PASS to
    :func:`_verifikacia_passed` / :func:`version_verified` / ``deploy.list_verified_versions``).

    **Honesty-by-construction (K-1=A):** the engine runs the PROOF FIRST — :func:`_run_release_smoke` boots
    the built app in an ephemeral, deploy-free ``-p <slug>-smoke`` stack + runs the acceptance leg — and
    records BOTH legs ``system→manazer`` at ``stage='priprava'`` BEFORE the partner turn. So a red boot / red
    acceptance is on the log before the partner speaks; it cannot claim "PEVNÉ" over a machine that says
    broken. REUSES the Verifikácia SMOKE machinery (:func:`_run_release_smoke` + :func:`_declared_release_coverage`
    → ``(0,0)`` for a conversation build, which never produced a ``navrh`` gate_report → the acceptance
    degrades to the anti-empty floor) WITHOUT its verdict/gate tail (no Auditor, no ``verdict``, no
    :func:`_settle_verifikacia_verdict`, no git tag, no fix loop).

    **K-3=A — NO auto-fix loop.** The round ALWAYS runs the partner turn (it reconciles honestly with the
    machine result). On a red runtime floor it then settles ``awaiting_manazer`` + records ONE
    ``kontrola_floor_red`` notification — the Manažér steers the fix; kontrola never signs off. The round
    NEVER advances a phase (no :func:`_settle_phase_boundary` / :func:`_next_stage`); ``current_stage`` stays
    ``priprava``. The settle mirrors :func:`run_conversation_turn`: ParseFailure → blocked/parse_exhaustion;
    question/blocked → blocked/agent_question; a normal gate_report → awaiting_manazer."""
    version_id = state.version_id
    slug = _project_slug_for_version(db, version_id)
    version_label = db.execute(select(Version.version_number).where(Version.id == version_id)).scalar_one()
    # audit #8: a re-verify of a drifted Hotovo (overit_znovu → hotovo_drift) flags the TRIGGERING check marker
    # with ``auto_hotovo`` — read it up front so a GREEN runtime floor auto re-anchors the Hotovo signature to
    # HEAD in one click (Director 2026-07-12). A normal ``skontrolovat`` has no such flag → the settle is unchanged.
    _latest_check = db.execute(
        select(PipelineMessage)
        .where(
            PipelineMessage.version_id == version_id,
            PipelineMessage.stage == "priprava",
            PipelineMessage.kind == "directive",
            PipelineMessage.payload["check"].astext == "true",
        )
        .order_by(PipelineMessage.seq.desc())
        .limit(1)
    ).scalar_one_or_none()
    auto_hotovo = bool(_latest_check and (_latest_check.payload or {}).get("auto_hotovo"))

    # 1. HONESTY PROOF FIRST (K-1=A): boot the app + run acceptance in ONE ephemeral -p <slug>-smoke cycle
    # (deploy-free — NEVER a customer instance / uat_provisioner / deploy.py). Recorded system→manazer at
    # stage='priprava' (kontrola LIVES in the conversation register — never 'verifikacia', which the
    # release/deploy path reads as a PASS) BEFORE the partner turn, so the machine's result is on the log
    # before the partner can speak. The coverage floor is the conversation build's (0,0) — it produced no
    # navrh gate_report — so the acceptance degrades to the anti-empty floor (ASSERTIONS_RUN>0); per-feature/
    # negative coverage is NOT enforced in the conversation flow (K-5, honestly stated, tightened later).
    coverage_req = _declared_release_coverage(db, version_id)
    # obs-2 Part B Part 2: bake the completing version's REAL note onto disk BEFORE the smoke so the 2a gate
    # asserts a served note (not a placeholder / a 2nd-version list missing its own note). PASS-time commit
    # (:func:`_commit_release_note`) is unchanged — this is an idempotent pre-write.
    _write_release_note_to_disk(db, version_id, claude_agent.PROJECTS_ROOT / slug)
    (smoke_ok, smoke_detail), acceptance = await _run_release_smoke(slug, version_label, coverage_req)
    smoke_msg = _record_message(
        db,
        version_id=version_id,
        stage="priprava",
        author="system",
        recipient="manazer",
        kind="notification",
        content=(f"Kontrola — beh appky (interné fixtúry): boot {'PASS' if smoke_ok else 'FAIL'}: {smoke_detail}"),
        payload={"phase": "priprava", "kontrola": True, "smoke": {"pass": smoke_ok, "detail": smoke_detail}},
    )
    if on_message is not None:
        await on_message(smoke_msg)
    # The acceptance leg only ran if boot passed (else None). Record it + build the Slovak block for the brief.
    if acceptance is not None:
        acc_ok, acc_detail, acc_skipped = acceptance
        acc_msg = _record_message(
            db,
            version_id=version_id,
            stage="priprava",
            author="system",
            recipient="manazer",
            kind="notification",
            content=(
                f"Kontrola — akceptačný beh — {'PASS' if acc_ok else ('SKIP' if acc_skipped else 'FAIL')}: {acc_detail}"
            ),
            payload={
                "phase": "priprava",
                "kontrola": True,
                "release_acceptance": {"pass": acc_ok, "detail": acc_detail, "skipped": acc_skipped},
            },
        )
        if on_message is not None:
            await on_message(acc_msg)
        acc_line = "PASS" if acc_ok else ("SKIP" if acc_skipped else "FAIL")
        smoke_block = (
            f"   Beh appky (interné fixtúry): boot {'PASS' if smoke_ok else 'FAIL'} — {smoke_detail}; "
            f"akceptácia {acc_line} — {acc_detail}.\n"
        )
    else:
        smoke_block = (
            f"   Beh appky (interné fixtúry): boot FAIL — {smoke_detail} "
            "(akceptácia sa nespustila). Priznaj to čestne v kontrole.\n"
        )
    # Deterministic runtime floor — the SAME mechanical truth the Verifikácia oracle computes (CR-V2-050): a
    # red boot, or an acceptance leg that RAN but did not pass (a SKIP is not red). The partner cannot talk it
    # away. Unlike Verifikácia this floors NO verdict (there is none) — it drives the kontrola_floor_red note.
    runtime_floor_red = (not smoke_ok) or (acceptance is not None and not acceptance[0] and not acceptance[2])

    # 2. The partner's honest self-check turn — role=state.current_actor (the AI Agent, NOT the Auditor:
    # kontrola is the partner checking its OWN work in the rozhovor, not the independent release gate),
    # stage='priprava', recipient='manazer', payload.kontrola marker. → ONE kind='gate_report' (never a
    # verdict). ALWAYS invoked through the SHARED invoke_agent_with_parse_retry (INVARIANT).
    result = await invoke_agent_with_parse_retry(
        db,
        version_id=version_id,
        role=state.current_actor,
        stage="priprava",
        prompt=_kontrola_directive(db, version_id, smoke_block=smoke_block),
        on_event=on_event,
        recipient="manazer",
        on_message=on_message,
        extra_payload={"kontrola": True},
        # metrics-v3-three-phases.md Part 1: the honest self-check is Verifikácia work — stamp 'verifikacia'
        # for metrics while stage STAYS 'priprava' so it remains invisible to the release/deploy path (a
        # verifikacia STAGE reads as a release PASS; only the metrics PHASE moves).
        metrics_phase="verifikacia",
    )

    if isinstance(result, ParseFailure):
        # The partner produced no parseable status block after the bounded retries → settle blocked with a
        # readable notification (+ raw excerpt) so the conversation is never left on an empty screen (mirror
        # run_conversation_turn). No phase advance — the spine always hands the turn back.
        state.status = "blocked"
        state.block_reason = "parse_exhaustion"
        state.next_action = "Blokované — AI partner nevrátil platný výstup kontroly. Napíš mu znova alebo upresni."
        await _record_parse_exhaustion(
            db,
            state,
            stage="priprava",
            result=result,
            human_hint="Napíš mu znova alebo upresni, čo má prekontrolovať.",
            on_message=on_message,
            # metrics-v3-followup.md C1: the honest self-check is Verifikácia work — stamp 'verifikacia' for
            # metrics while stage STAYS 'priprava' (mirrors the SUCCESS kontrola turn's metrics_phase).
            metrics_phase="verifikacia",
        )
        db.flush()
        return state

    if result.kind in ("question", "blocked"):
        # The partner asked the Manažér something → blocked on an agent_question so the board offers answer.
        state.status = "blocked"
        state.block_reason = "agent_question"
        state.next_action = f"AI partner sa pýta: {result.question}"
        db.flush()
        return state

    # A normal self-check report (gate_report) → SETTLE for the Manažér. current_stage STAYS 'priprava' — NO
    # _settle_phase_boundary / _next_stage (the spine invariant; kontrola signs nothing, deploys nothing).
    if runtime_floor_red:
        # K-3=A: a red runtime floor STOPS and hands the wheel back — NO auto-fix loop. Record ONE honest
        # floor-red notification (the machine floored it, not the partner's say-so) so the board flags it; the
        # Manažér steers the fix and re-runs kontrola. Kontrola never signs off on a red build.
        floor_msg = _record_message(
            db,
            version_id=version_id,
            stage="priprava",
            author="system",
            recipient="manazer",
            kind="notification",
            content=(
                "Kontrola — beh appky je ČERVENÝ (appka nenaštartovala alebo akceptačný beh neprešiel). "
                "Kontrola nič nepodpisuje — oprav to a spusti kontrolu znova."
            ),
            payload={"phase": "priprava", "kontrola": True, "kontrola_floor_red": True},
        )
        if on_message is not None:
            await on_message(floor_msg)
    if auto_hotovo and not runtime_floor_red:
        # audit #8: a GREEN re-verify of a drifted Hotovo re-anchors the manager's signature to the CURRENT
        # commit in ONE click (Director 2026-07-12: auto re-sign). Same runtime-floor gate as the manual Hotovo
        # (K-3 — the objective boot+acceptance floor, not the partner's advisory PEVNÉ/VRATKÉ prose). A RED floor
        # NEVER auto-signs — it recorded the floor-red note above and settles re-opened below so the manager fixes.
        _apply_hotovo_signoff(
            db,
            version_id,
            state,
            content="Znovu overené po zmene kódu — beh appky je v poriadku, verzia je opäť označená ako hotová.",
        )
        return state
    state.status = "awaiting_manazer"
    state.next_action = (
        "Kontrola: beh appky je červený — pozri nález a oprav, potom spusti kontrolu znova."
        if runtime_floor_red
        else "Kontrola hotová — pozri, čo je PEVNÉ a čo VRATKÉ, a pokračuj v rozhovore."
    )
    db.flush()
    return state


# (CR-V2-013: ``_GATE_E_NO_EDIT`` + ``_block_failed`` + ``_coordinator_review_gap`` +
# ``_gate_e_scope_directive`` + ``_gate_e_continue_prompt`` + the ``_run_gate_e_round`` per-question
# sub-state-machine are REMOVED with the rest of the Gate-E machinery. The v2 Auditor's UPFRONT review
# replaces the Customer↔Designer↔Director Gate-E loop with ONE independent invocation after Návrh —
# see :func:`_run_auditor_upfront_review`, wired into :func:`_run_navrh_round`.)


async def _settle_plan_pass_failure(
    db: Session,
    state: PipelineState,
    failed: ParseFailure,
    *,
    note: str,
    on_message: Optional[MessageCallback],
    stage: str = "navrh",
    metrics_phase: Optional[str] = None,
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

    ``metrics_phase`` (metrics-v3-followup.md C1): decouples the metrics PHASE stamp (``payload['phase']``)
    from the ``stage`` column, exactly like :func:`_record_parse_exhaustion` and
    :func:`_generate_incremental_plan`. The STEP-3 conversation plan round passes ``navrh`` so a failed
    plan pass attributes to Návrh for metrics while its ``stage`` stays the conversation register's
    ``priprava``; ``None`` (the legacy Návrh caller) keeps ``phase == stage`` — byte-for-byte unchanged.
    """
    if failed.lost_work is not None:
        state.status = "awaiting_manazer"
        state.next_action = failed.lost_work["next_action"]
        db.flush()
        return state
    phase = metrics_phase if metrics_phase is not None else stage
    msg = _record_message(
        db,
        version_id=state.version_id,
        stage=stage,
        author="system",
        recipient="manazer",
        kind="notification",
        content=f"Plán úloh sa nepodarilo vygenerovať: {note}. Usmerni agenta (Uprav) a zopakuj.",
        payload={"phase": phase, **(_failure_metrics_payload(failed) or {})},
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


async def _generate_incremental_plan(
    db: Session,
    state: PipelineState,
    *,
    stage: str,
    on_event: Optional[claude_agent.EventCallback],
    directive: Optional[str],
    on_message: Optional[MessageCallback],
    metrics_phase: Optional[str] = None,
) -> Optional[PipelineState]:
    """Generate the EPIC→FEAT→TASK task plan INCREMENTALLY and materialize it (CR-V2-011; STEP 3 re-home).

    The PROVEN incremental machinery, extracted so BOTH registers reuse it byte-for-byte (step3-plan-design.md
    — do NOT parse a whole plan tree off one turn): the Návrh phase (``stage='navrh'`` —
    :func:`_fold_task_plan_into_navrh`) and the STEP-3 conversation plan round (``stage='priprava'`` —
    :func:`_run_conversation_plan_round`). Runs on the SAME warm AI-Agent session (so the design doc /
    Špecifikácia + the just-emitted skeleton stay in context), then materializes via :func:`_write_task_plan`:

    * **Pass 1 — skeleton:** EPIC + FEAT (no tasks) + ``cross_cutting_rules`` (+ the release-coverage
      declaration). ``directive`` prepends the register's framed brief (MD-2: re-read the current spec).
    * **Passes 2..N — per feat (skeleton order):** that feat's ``tasks[]``, accumulated in memory.
    * **Assemble** the full :class:`TaskPlan` in skeleton order (so ``_write_task_plan``'s MAX+1 numbering
      matches what the Manažér reviews) carrying every node's ``plain_description``, record the AI-Agent
      ``gate_report`` (carries the plan + ``cross_cutting_rules`` the build loop re-reads via
      :func:`_fetch_cross_cutting_rules`), then call :func:`_write_task_plan`.

    ``stage`` threads the HONEST phase into every ``_record_message`` stage column + payload ``phase`` + the
    assembled block + both plan-pass helpers + both settles + the reviewable doc — nothing hardcodes
    ``navrh`` (step3-plan-design.md FIX1); default-free (the caller always passes it explicitly).

    ``metrics_phase`` (metrics-v3-three-phases.md Part 1): decouples the metrics PHASE stamp (``payload
    ['phase']``) from the ``stage`` column. The STEP-3 conversation plan round passes ``navrh`` so the task
    plan attributes to Návrh for metrics while its ``stage`` stays the conversation register's ``priprava``.
    ``None`` (the legacy Návrh caller) keeps ``phase == stage`` — byte-for-byte the pre-STEP-3 behaviour.

    Fail-closed (NO parse exhaustion on a large plan — that is the whole point of the incremental passes):
    a skeleton/per-feat exhaustion → ``blocked`` via :func:`_settle_plan_pass_failure` **naming the feat**,
    writing **nothing**; :data:`MAX_PLAN_FEATS` caps total feats; a defensive assemble/write failure →
    ``blocked``. Returns the SETTLED state on any failure (the caller returns it directly), or ``None`` on
    success (the caller then settles for its register). The passes use the dedicated
    :func:`_invoke_plan_pass` — ``invoke_agent`` stays byte-identical."""
    version_id = state.version_id
    # metrics-v3-three-phases.md Part 1: the metrics phase stamp for every message this round records — the
    # conversation plan round overrides it to 'navrh' while stage stays 'priprava'; None → phase == stage.
    phase = metrics_phase if metrics_phase is not None else stage

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
        stage=stage,
        metrics_phase=metrics_phase,
    )
    if isinstance(skeleton, ParseFailure):
        # Skeleton failure: a genuine parse exhaustion → blocked; an envelope-loss (timeout) → R1
        # awaiting_manazer (never a blocked dead-end). See the helper.
        return await _settle_plan_pass_failure(
            db,
            state,
            skeleton,
            note="agent nevrátil platnú kostru plánu ani po opravách",
            on_message=on_message,
            stage=stage,
            metrics_phase=metrics_phase,  # C1: in-scope metrics phase (navrh for the STEP-3 conversation round)
        )

    # MAX_PLAN_FEATS cap (fail-closed) — a coarse-grained plan (module ≈ task) never needs this many.
    feat_refs = [(ei, fi, feat) for ei, epic in enumerate(skeleton.epics) for fi, feat in enumerate(epic.feats)]
    if len(feat_refs) > MAX_PLAN_FEATS:
        msg = _record_message(
            db,
            version_id=version_id,
            stage=stage,
            author="system",
            recipient="manazer",
            kind="notification",
            content=(
                f"Plán má priveľa funkcií ({len(feat_refs)} > strop {MAX_PLAN_FEATS}) — rozklad je príliš "
                "jemnozrnný; treba hrubšiu granularitu (modul ≈ úloha, F-007 §4)."
            ),
            payload={"phase": phase},
        )
        if on_message is not None:
            await on_message(msg)
        state.status = "blocked"
        state.block_reason = "system_error"
        state.next_action = "Plán úloh zamietnutý — rozklad je príliš jemnozrnný. Usmerni plán (Uprav)."
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
            stage=stage,
            metrics_phase=metrics_phase,
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
                stage=stage,
                metrics_phase=metrics_phase,  # C1: in-scope metrics phase (navrh for the STEP-3 conversation round)
            )
        feat_tasks[(ei, fi)] = pass_result.tasks

    # Assemble the FULL TaskPlan in skeleton order, carrying every node's plain_description (STEP 3 FIX4 —
    # the epic's plain_description is its ONLY prose; the tasks are already TaskPlanTask objects that carry
    # theirs). TaskPlanFeat.tasks min_length=1 + the per-feat passes' own ≥1 guarantee make this non-empty;
    # a defensive ValidationError → fail-closed HALT (nothing written).
    try:
        full_plan = TaskPlan(
            epics=[
                TaskPlanEpic(
                    title=epic.title,
                    plain_description=epic.plain_description,
                    feats=[
                        TaskPlanFeat(
                            title=feat.title,
                            description=feat.description,
                            plain_description=feat.plain_description,
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
            stage=stage,
            author="system",
            recipient="manazer",
            kind="notification",
            content=f"Zostavený plán úloh je neúplný: {exc}.",
            payload={"phase": phase},
        )
        if on_message is not None:
            await on_message(msg)
        state.status = "blocked"
        state.block_reason = "system_error"
        state.next_action = "Plán úloh zamietnutý — zostavený plán je neúplný. Usmerni plán (Uprav)."
        db.flush()
        return state

    # Register-aware summary — the Návrh phase closes the whole design (doc + plan) BYTE-IDENTICALLY to
    # pre-STEP-3; the conversation register composed only the plan from the approved Špecifikácia.
    summary = (
        "Návrh hotový — návrhový dokument + plán úloh (kostra + úlohy po funkciách)."
        if stage == "navrh"
        else "Plán úloh zostavený zo schválenej Špecifikácie (kostra + úlohy po funkciách)."
    )
    assembled = PipelineStatusBlock(
        stage=stage,
        kind="gate_report",
        summary=summary,
        awaiting="manazer",
        plan=full_plan,
        cross_cutting_rules=skeleton.cross_cutting_rules,
        # CR-V2-052: carry the release-coverage declaration (flagship features + safety properties) the
        # risk-floored oracle (CR-V2-051) reads from this gate_report's payload.
        flagship_features=skeleton.flagship_features,
        safety_properties=skeleton.safety_properties,
    )
    # Record the AI-Agent gate_report carrying the assembled plan + cross_cutting_rules: the build loop
    # re-reads the rules from THIS message (_fetch_cross_cutting_rules), and it is the audit-trail record of
    # the plan the Manažér reviews. No usage of its own (orchestrator-synthesized — the per-pass notes
    # already accounted the agent tokens); mode="json" so any UUID in the plan serializes for JSONB.
    plan_msg = _record_message(
        db,
        version_id=version_id,
        stage=stage,
        author="ai_agent",
        recipient="manazer",
        kind="gate_report",
        content=assembled.summary,
        payload={
            "plan": full_plan.model_dump(mode="json"),
            "cross_cutting_rules": skeleton.cross_cutting_rules,
            # CR-V2-052: the declared release coverage — _declared_release_coverage(db, version_id) reads these
            # to floor the acceptance (≥1 FEATURE assertion per flagship feature, ≥1 NEGATIVE per safety prop).
            "flagship_features": skeleton.flagship_features,
            "safety_properties": [sp.model_dump(mode="json") for sp in skeleton.safety_properties],
            "phase": phase,
        },
    )
    if on_message is not None:
        await on_message(plan_msg)

    reason = _write_task_plan(db, state, assembled, stage=stage, metrics_phase=metrics_phase)
    if reason is not None:
        # Plan write failed → blocked: a direct system→manazer note (no Coordinator relay, design §2.2).
        msg = _record_message(
            db,
            version_id=version_id,
            stage=stage,
            author="system",
            recipient="manazer",
            kind="notification",
            content=f"Plán úloh sa nepodarilo zapísať: {reason}.",
            payload={"phase": phase},
        )
        if on_message is not None:
            await on_message(msg)
        state.status = "blocked"
        state.block_reason = "system_error"  # R4 (D1): task-plan write failed (engine-side)
        state.next_action = "Plán úloh sa nepodarilo zapísať — usmerni plán (Uprav)."
        db.flush()
        return state
    return None  # success — the caller settles for its register


async def _fold_task_plan_into_navrh(
    db: Session,
    state: PipelineState,
    *,
    on_event: Optional[claude_agent.EventCallback],
    directive: Optional[str],
    on_message: Optional[MessageCallback],
) -> Optional[PipelineState]:
    """Fold the incremental task plan into the Návrh phase (CR-V2-011) — the ``stage='navrh'`` wrapper over
    the shared :func:`_generate_incremental_plan`.

    The standalone ``task_plan`` stage/round is removed; the plan is the LAST part of the Návrh design doc
    (design §2.1(2)), generated pass-by-pass so a large plan never overflows one turn. Byte-identical to the
    pre-STEP-3 behaviour (every record stays on the ``navrh`` stage). Returns the SETTLED state on any
    failure (the caller returns it directly), or ``None`` on success (the caller then runs the SHARED
    dial-settle)."""
    return await _generate_incremental_plan(
        db, state, stage="navrh", on_event=on_event, directive=directive, on_message=on_message
    )


async def _run_auditor_upfront_review(
    db: Session,
    state: PipelineState,
    *,
    on_event: Optional[claude_agent.EventCallback] = None,
    on_message: Optional[MessageCallback] = None,
) -> Optional[PipelineStatusBlock]:
    """The Auditor's UPFRONT spec/design review (CR-V2-013; AUD-1(a), AUD-5, NAVRH-4, AUTON-5) — replaces
    the Gate-E Customer function. Runs ONCE inside :func:`_run_navrh_round` after the design doc + task plan
    are persisted, before the post-Návrh dial-settle.

    The independent Auditor (``role=AUDITOR_ROLE``, READ + RUN-ONLY — its charter forbids edits/commits)
    scans the Špecifikácia + design doc for holes / ambiguities / contradictions and emits ONE
    ``kind=verdict`` block (the CR-V2-006 repurposed ``verdict``/``findings``/``proposed_fix`` shape). The
    verdict message is recorded ``author=auditor`` → ``recipient=manazer`` at ``stage=navrh`` (all valid v2
    DB CHECK values — no ``director``/``coordinator``/``gate_e`` tokens), so the Manažér's review view at the
    post-Návrh schvaľovací bod shows the Auditor's findings ALONGSIDE the AI Agent's own clarification
    questions. Review DEPTH scales with the dial via :func:`_resolve_dispatch_overrides` (Auditor effort
    coupling, OQ-9); the brief (:func:`_auditor_upfront_directive`) tells the Auditor to match its scrutiny.

    Returns ``True`` when the Auditor found a HOLE (``verdict`` block with ``verdict`` not True) — the caller
    FORCES the post-Návrh stop regardless of the dial (AUD-4: a spec/design hole escalates to the Manažér).
    Returns ``False`` when the review PASSED (or could not be completed) — the caller lets the dial govern
    the stop normally; the AI Agent's own questions + the Manažér still gate Programovanie.

    A parse failure of the review is NON-BLOCKING (fail-open for control flow, fail-CLOSED on the verdict is
    not appropriate here — the upfront review is an EARLY safety net, not the release gate; the Manažér still
    sees the design at the dial-governed stop). It is recorded visibly + metered (``system→manazer`` note)
    and treated as "no hole found" so a flaky Auditor turn can never wedge the build. The sole-mutator
    invariant holds: this runs inside the dispatch path, always a consequence of an action routed through
    :func:`apply_action`."""
    review = await invoke_agent_with_parse_retry(
        db,
        version_id=state.version_id,
        role=AUDITOR_ROLE,
        stage="navrh",
        prompt=_auditor_upfront_directive(db, state.version_id),
        on_event=on_event,
        recipient="manazer",  # the Auditor's findings are for the Manažér at the post-Návrh stop
        on_message=on_message,
        # Structural marker (orchestrator record, not agent self-report): this verdict is the UPFRONT review
        # (vs the end Verifikácia check), so the Návrh tab / Manažér review view can label it.
        extra_payload={"upfront_review": True},
    )
    if isinstance(review, ParseFailure):
        # Non-blocking observability: make the failed review visible + count its tokens, then proceed as if
        # clean. Record a v2 ``system → manazer`` note (the v1 internal-turn-parse-failure recorder that
        # wrote to the retired operator token was excised wholesale in CR-V2-017).
        msg = _record_message(
            db,
            version_id=state.version_id,
            stage="navrh",
            author="system",
            recipient="manazer",
            kind="notification",
            content=(
                "Upfront previerka Auditora sa nepodarila ani po opakovaných pokusoch — pokračuje sa bez nej "
                "(Manažér aj tak posúdi návrh na schvaľovacom bode). Pozri priebeh."
            ),
            payload=_failure_metrics_payload(review) or None,
        )
        if on_message is not None:
            await on_message(msg)
        return None  # no hole on record → the dial governs the stop normally
    # A clean review with no hole → verdict True (PASS). A hole → verdict not True (fail-closed on the
    # finding: an absent/False verdict on a verdict turn is a hole, mirroring _verifikacia_passed). The
    # ``kind=verdict`` message was already recorded by invoke_agent with author=auditor / recipient=manazer.
    hole_found = review.kind == "verdict" and not review.verdict
    if not hole_found:
        return None  # PASS → the dial governs the post-Návrh stop normally
    # AUD-4: a spec/design hole escalates to the Manažér — record the escalation note (system→manazer) so the
    # board / Telegram surfaces it; the caller (CR-V2-041) turns the verdict into an interactive consultation.
    note = _record_message(
        db,
        version_id=state.version_id,
        stage="navrh",
        author="system",
        recipient="manazer",
        kind="notification",
        content=(
            "Auditor našiel medzeru v Špecifikácii/Návrhu (upfront previerka) — spúšťa sa konzultácia "
            "s Manažérom (rozhodnutia po jednom)."
        ),
        payload={"phase": "navrh", "upfront_review_hole": True},
    )
    if on_message is not None:
        await on_message(note)
    # Return the verdict block (carries findings / proposed_fix) so the caller can drive the consultation.
    return review


async def _run_navrh_round(
    db: Session,
    state: PipelineState,
    *,
    on_event: Optional[claude_agent.EventCallback] = None,
    directive: Optional[str] = None,
    on_message: Optional[MessageCallback] = None,
) -> PipelineState:
    """The Návrh round (CR-V2-011; NAVRH-1..NAVRH-4, ARCH-2): ONE coherent design doc (the task plan is built
    LATER, at Programovanie start — Director 2026-07-13).

    Replaces the v1 standalone design + ``_run_task_plan_round`` passes with a single Návrh phase:

    1. **Design-doc turn** — the AI Agent (warm session, resumed from Príprava) writes ONE coherent design
       ``.md`` (overview/data-model/API/BE+FE, sized to the project) per :func:`_navrh_directive`. A
       ``question``/``blocked`` turn settles ``blocked`` (the Manažér answers — the post-Návrh schvaľovací
       bod surfaces clarification questions; the Auditor's upfront review hooks here in CR-V2-013); a
       ``ParseFailure`` settles the R1 lost-work / parse-exhaustion path; a ``directive`` (uprav/ask/answer)
       IS the agent's prompt (two-way comms).
    2. **Persist + verify** the design-doc artifact (mirror of the Príprava spec gate). A checkout that
       exists but is missing the doc → ``blocked`` (the phase is not "done" without its artifact).
    3. **Auditor upfront review** (:func:`_run_auditor_upfront_review`, CR-V2-013) — the task plan is NO
       LONGER folded in here; it is generated at the START of Programovanie (:func:`_run_build_round`) from
       the final design + Vizuál changes, so it reflects the final state (Director 2026-07-13).
    4. **Settle via the SHARED dial** (:func:`_settle_phase_boundary`): the Návrh schvaľovací bod is
       dial-governed — auto-continue (``plna``) or stop ``awaiting_manazer`` (the Manažér reviews the design
       doc + the AI Agent's clarification questions).

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
        await _record_parse_exhaustion(
            db,
            state,
            stage="navrh",
            result=result,
            human_hint="Skús znova (Uprav) alebo upresni návrh.",
            on_message=on_message,
        )
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

    # 3. The task plan is NO LONGER generated here (nex-studio-visual, Director 2026-07-13). The Vizuál step
    # keeps refining the app AFTER Návrh, so a plan built now would be stale (it would miss the screens/fields
    # the Manažér adds while walking the live preview). Návrh produces the design DOCUMENT ONLY; the
    # EPIC→FEAT→TASK plan is generated at the START of Programovanie (:func:`_run_build_round`), from the FINAL
    # design + the Manažér's Vizuál changes (the warm session carries them). Any inline plan the design turn
    # happens to emit is ignored — the plan is always built fresh at build time.

    # 3b. Fix A (Director 2026-07-17): freeze the deliverables into a commit BEFORE the Auditor reviews them,
    # so the upfront review scans a STABLE committed snapshot (not a still-being-written worktree — the
    # stale-audit that FAILed on already-resolved gaps) AND the Príprava/Návrh output becomes durable.
    _commit_navrh_deliverables(claude_agent.PROJECTS_ROOT / _project_slug_for_version(db, state.version_id))

    # 4. AUDITOR UPFRONT REVIEW (CR-V2-013; AUD-1(a)/AUD-5/NAVRH-4 — replaces the Gate-E Customer function).
    # The independent Auditor (READ + RUN-ONLY, no write/commit) scans the Špecifikácia + the design doc for
    # holes / ambiguities / contradictions and emits ONE ``verdict`` (findings + proposed_fix). Its findings
    # surface at the post-Návrh schvaľovací bod ALONGSIDE the AI Agent's own clarification questions. A
    # spec/design HOLE (verdict FAIL) ESCALATES to the Manažér (AUD-4): the review forces the post-Návrh stop
    # regardless of the dial, so a hole can never auto-continue into Programovanie. A parse failure of the
    # review is non-blocking (visible + metered) — it must never wedge the build; the dial then governs the
    # stop as if the review were clean (the AI Agent's own questions + the Manažér still gate Programovanie).
    review_verdict = await _run_auditor_upfront_review(db, state, on_event=on_event, on_message=on_message)

    # 5. CR-V2-041: a spec/design HOLE → turn the Auditor's verdict into an INTERACTIVE Manažér consultation
    # (the AI Agent translates the findings into plain-language decision cards the Manažér answers one-at-a-
    # time). This OVERRIDES the dial (AUD-4 — a hole always escalates). Otherwise the SHARED dial-settle
    # governs: auto-continue to Vizuál/Programovanie vs stop at the post-Návrh schvaľovací bod (the design doc
    # + the AI Agent's own clarification questions; the task plan is now built later, at Programovanie start).
    if review_verdict is not None:
        return await _settle_for_consultation(
            db, state, source="auditor_upfront", verdict=review_verdict, on_event=on_event, on_message=on_message
        )
    if _settle_phase_boundary(db, state):
        return state  # agent_working at the next phase — the auto-chain loop continues
    if state.status != "done":
        state.status = "awaiting_manazer"
        state.next_action = "Manažér: posúdiť návrhový dokument (Schváliť / Uprav)."
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


def _latest_fix_critique(db: Session, version_id: uuid.UUID) -> Optional[dict[str, Any]]:
    """CR-V2-058 Part B — the ``fix_critique`` record ({verdict, corrected_scope, why}) for the CURRENT FAIL
    round, or ``None``.

    The critic note (``author=auditor`` / ``kind=notification`` / ``payload.fix_critique``) is recorded at the
    FAIL seam BEFORE the settle, so within a round it has a HIGHER ``seq`` than the round's ``kind=verdict`` and
    a LOWER ``seq`` than the card the settle then builds. A critique belongs to THIS round iff it is NEWER than
    the latest FAIL verdict — scanning the ``verdict``/``notification`` messages newest-first, the first hit
    decides: a ``fix_critique`` note → return it; a ``verdict`` first → ``None``. This makes every un-vetted
    path fail-safe BY CONSTRUCTION (§2): a round that recorded NO critique (fail-open / engine-red skip /
    manual verdict path) leaves the verdict on top → ``None``; a PRIOR round's stale critique is older than
    THIS round's verdict → also ``None`` (never stale-recommends an un-vetted fix)."""
    rows = db.execute(
        select(PipelineMessage.payload, PipelineMessage.kind)
        .where(
            PipelineMessage.version_id == version_id,
            PipelineMessage.stage == "verifikacia",
            PipelineMessage.kind.in_(("verdict", "notification")),
        )
        .order_by(PipelineMessage.seq.desc())
    ).all()
    for payload, kind in rows:
        p = payload if isinstance(payload, dict) else {}
        if kind == "notification" and isinstance(p.get("fix_critique"), dict):
            return p["fix_critique"]
        if kind == "verdict":
            # the current round's verdict is newer than any critique → no critique for THIS round
            return None
    return None


def _build_fix_consultation(db: Session, version_id: uuid.UUID, state: PipelineState) -> ConsultationBlock:
    """CR-V2-058 Part A — the deliberated Decision Card on a Verifikácia FAIL (the FIRST FAIL onward, not only
    loop exhaustion). The SHARED, engine-side card builder that enforces the §2 nosný invariant BY
    CONSTRUCTION: *"Spustiť pripravenú opravu" (``accept_fix``) is OFFERED + recommended ONLY when a POSITIVE
    ``fix_critique`` (verdict ∈ {accept, narrow}) is on record for THIS round; otherwise it is omitted and
    "Usmerniť opravu" (``guide``) is recommended.*

    Because the recommendation reads the SAME :func:`_latest_fix_critique` every path writes (or does not),
    each un-vetted path fail-safes to ``guide`` with NO special-casing — the manual verdict path (records no
    critique), the fail-open critic (records no critique), and the engine-red skip (records no critique) ALL
    default to ``guide``; a ``reject`` critique likewise. No path can one-click an un-vetted fix.

    Engine-built cards do NOT pass ``_validate_block`` (they are assembled here, not parsed from an agent), so
    the builder SELF-ASSERTS exactly one ``recommended`` option (§2)."""
    critique = _latest_fix_critique(db, version_id)
    positive = bool(critique) and critique.get("verdict") in ("accept", "narrow")
    scope = _latest_verifikacia_fix_scope(db, version_id) or "Auditor našiel blokujúce zlyhanie vo Verifikácii."

    explanation_parts = [
        "Auditor (nezávislý overovateľ) našiel pri koncovej Verifikácii blokujúce zlyhanie a navrhol cielenú opravu:",
        scope,
    ]
    if critique:
        crit_verdict = critique.get("verdict")
        crit_why = str(critique.get("why") or "").strip()
        corrected = str(critique.get("corrected_scope") or "").strip()
        if positive:
            head = (
                "Navrhnutú opravu nezávisle PREVERIL kritik (accept — je vynútená konštrukciou)"
                if crit_verdict == "accept"
                else "Navrhnutú opravu nezávisle PREVERIL kritik (narrow — v jadre správna, so zúženým rozsahom)"
            )
            explanation_parts.append(f"{head}. {crit_why}".strip())
            if corrected:
                explanation_parts.append(f"Preverený (opravený) rozsah: {corrected}")
        else:  # reject
            explanation_parts.append(
                f"Nezávislý kritik navrhnutú opravu ZAMIETOL (reject) — nie je dôveryhodná. {crit_why}".strip()
            )
    else:
        explanation_parts.append(
            "Navrhnutá oprava NEbola nezávisle preverená (kritik nebol dostupný alebo išlo o mechanické "
            "engine-červené zlyhanie), preto ju nemôžem odporučiť na jednoklik."
        )
    explanation = "\n\n".join(explanation_parts)

    options: list[ConsultOption] = []
    if positive:
        # Only a positively-vetted fix is even OFFERED for one-click (§2/§5 "skrytá" otherwise) — recommended.
        options.append(
            ConsultOption(
                id="accept_fix",
                label="Spustiť pripravenú opravu",
                detail="Spustí už pripravenú cielenú opravu (AI Agent ju vykoná v Programovaní a Auditor ju "
                "znova overí). Navrhnutá oprava prešla nezávislým preverením kritika.",
                recommended=True,
            )
        )
    options.append(
        ConsultOption(
            id="guide",
            label="Usmerniť opravu",
            detail="Napíš konkrétny pokyn (pole nižšie) — pošle sa AI Agentovi (opravárovi) ako cielená oprava "
            "a Auditor ju znova overí. Odporúčané, keď navrhnutá oprava nie je preverená alebo bola zamietnutá.",
            recommended=not positive,
        )
    )
    options.append(
        ConsultOption(
            id="hold",
            label="Zatiaľ podržať (rozhodnem neskôr)",
            detail="Build ostane blokovaný, kým nerozhodneš; kartu môžeš vyriešiť neskôr (spustiť opravu alebo "
            "usmerniť).",
        )
    )
    # §2 by construction: exactly one recommended (positive → accept_fix; else → guide). Self-assert because
    # engine cards bypass _validate_block; a future refactor that broke it would fail loudly here, never ship a
    # card that recommends an un-vetted fix (or none).
    recommended_count = sum(1 for o in options if o.recommended)
    if recommended_count != 1:  # pragma: no cover - defensive; construction guarantees exactly one
        raise OrchestratorError(
            f"fix-consultation invariant violated: expected exactly one recommended option, got {recommended_count}"
        )

    rationale = (
        "Odporúčam spustiť pripravenú opravu — nezávislý kritik ju preveril (je vynútená konštrukciou)."
        if positive
        else "Odporúčam usmerniť opravu — navrhnutá oprava nebola nezávisle preverená (alebo bola zamietnutá), "
        "tak ju nespúšťaj naslepo; napíš adresný pokyn a Auditor ho znova overí."
    )
    return ConsultationBlock(
        id=f"verifikacia-fix-{version_id}-{state.iteration}",
        intro="Verifikácia našla chybu — potrebné je tvoje rozhodnutie.",
        source="verifikacia_fix",
        decisions=[
            ConsultDecision(
                key="verifikacia_fix_next",
                question="Verifikácia našla blokujúcu chybu. Ako chceš pokračovať?",
                explanation=explanation,
                options=options,
                rationale=rationale,
                allow_free_text=True,
            )
        ],
    )


async def _settle_verifikacia_verdict(
    db: Session,
    state: PipelineState,
    *,
    verdict: str,
    runtime_floor_red: bool = False,
    on_message: Optional[MessageCallback] = None,
) -> PipelineState:
    """Apply an Auditor Verifikácia ``verdict`` (PASS / FAIL) and settle the state (CR-V2-014; VERIF-1..3,
    AUD-2, AUD-3). The SINGLE source of truth shared by BOTH verdict paths so they can never diverge:

      * the AUTONOMOUS path — :func:`_run_verifikacia_round` runs the Auditor + smoke and applies the
        Auditor's own verdict at a non-stopping dial level;
      * the MANUAL path — :func:`apply_action` ``action="verdict"`` when the Manažér ratifies/overrides at a
        dial-governed Verifikácia stop.

    The caller has ALREADY recorded the ``kind=verdict`` message (``author=auditor`` / ``recipient=manazer``
    / ``stage=verifikacia`` — all valid v2 DB CHECK tokens). This applies the consequence:

      * **PASS** — SETTLE ``awaiting_manazer`` for the dial-governed end sign-off (``schvalit`` → Hotovo). The
        phase does NOT auto-advance to Hotovo here; whether the build stops for the Manažér or the engine
        auto-signs-off (``plna``) is the dial's call, applied in the dispatch path / :func:`_settle_phase_boundary`.
        Keeping the PASS-then-sign-off split preserves the no-silent-done invariant (safeguard #5): Hotovo is
        reached ONLY through this recorded PASS verdict (:func:`_verifikacia_passed`).
      * **FAIL** — the bounded Auditor fix↔re-verify loop (the Auditor FINDS, the AI Agent FIXES — §2.2
        "Division of labour"). ``iteration`` counts the rounds. On the (n+1)-th still-failing round
        (``iteration >= AUDITOR_LOOP_MAX``) STOP + escalate to the Manažér (``blocked``, a visible
        ``system→manazer`` note). Otherwise loop a **TARGETED** fix back to the AI Agent (A+B, Director
        2026-06-30): materialize ONE fix task (:func:`_ensure_verifikacia_fix_task`) carrying the Auditor's
        findings as its brief (threaded by :func:`_run_build_round` via :func:`_latest_verifikacia_fix_scope`)
        — the already-done plan tasks **STAY done** (B: replaces the v1 gate_g whole-build reset
        :func:`_reset_done_tasks_for_regate`, the overnight-token-burn cause). Mark ``is_regate``, bump the
        round counter, re-enter Programovanie. A new_version then **STOPS** (``paused``) for the Manažér to
        confirm the fix re-run (A: mandatory phase gate, dial-independent — 'Pokračovať' resumes); only a
        ``fast_fix`` auto-re-dispatches its bounded one-task lane (warm sessions preserved — never reset mid-loop).

    The sole-mutator invariant holds whichever path called it: the autonomous path runs inside the dispatch
    path (a consequence of an action already routed through :func:`apply_action`), the manual path IS
    :func:`apply_action`."""
    version_id = state.version_id
    # CR-V2-050: even on a PASS string, a red runtime floor (boot/acceptance) coerces to the FAIL path — the
    # mechanically-computed evidence is authoritative; a self-reported PASS can never cross a red floor. Guards
    # BOTH the autonomous caller and the manual apply_action verdict override.
    if verdict == "PASS" and not runtime_floor_red:
        state.status = "awaiting_manazer"
        state.next_action = "Verifikácia PASS — schváľ na Hotovo (nasadenie je samostatná akcia per zákazník)."
        db.flush()
        return state
    # FAIL (or a PASS string floored to FAIL by the runtime floor) → bounded fix loop.
    if state.iteration >= AUDITOR_LOOP_MAX:
        # Exhausted the bounded AUTONOMOUS loop → STOP + surface an operator DECISION (CR-V2-054, safeguard #5):
        # a kind=consultation (source=verifikacia_fail) the DecisionCardStack renders with an explanation +
        # recommendation + one action, so a non-expert operator (Tibor/Nazar) can act without knowing the
        # fixer/finder split. block_reason=decision_needed (not agent_error) so 'decide' is valid + the route
        # never auto-dispatches. The scope (why it is stuck) is the card's explanation.
        scope = _latest_verifikacia_fix_scope(db, version_id) or "Auditor nevie verziu dostať cez Verifikáciu."
        consult = ConsultationBlock(
            id=f"verifikacia-fail-{version_id}-{state.iteration}",
            intro=f"Auditor po {AUDITOR_LOOP_MAX} kolách stále FAIL — potrebné je tvoje rozhodnutie.",
            source="verifikacia_fail",
            decisions=[
                ConsultDecision(
                    key="verifikacia_fail_next",
                    question="Auditor nevie verziu dostať cez koncovú Verifikáciu. Ako chceš pokračovať?",
                    explanation=scope,
                    options=[
                        ConsultOption(
                            id="guide_fix",
                            label="Usmerniť opravu pre AI Agenta",
                            detail="Napíš konkrétny pokyn (pole nižšie) — pošle sa AI Agentovi ako cielená "
                            "oprava a Auditor ju znova overí.",
                            recommended=True,
                        ),
                        ConsultOption(
                            id="hold",
                            label="Zatiaľ podržať (rozhodnem neskôr)",
                            detail="Build ostane blokovaný, kým nerozhodneš; neskôr môžeš usmerniť opravu "
                            "aj cez 'Uprav'.",
                        ),
                    ],
                    rationale="Odporúčam usmerniť opravu — Auditor zvyčajne uviazol na konkrétnej veci, ktorú "
                    "vieš adresne opísať; pokyn dostane AI Agent (opravár), nie Auditor (nálezca).",
                    allow_free_text=True,
                )
            ],
        )
        state.status = "blocked"
        state.block_reason = "decision_needed"  # CR-V2-054: a Manažér DECISION, surfaced as a Decision Card
        state.next_action = (
            f"Auditor po {AUDITOR_LOOP_MAX} kolách stále FAIL — rozhodni (Decision Card): usmerni opravu, alebo podrž."
        )
        db.flush()
        note = _record_message(
            db,
            version_id=version_id,
            stage="verifikacia",
            author="system",
            recipient="manazer",
            kind="consultation",
            content=consult.intro,
            payload={
                "phase": "verifikacia",
                "auditor_loop_exhausted": True,
                "consultation": consult.model_dump(mode="json"),
            },
        )
        if on_message is not None:
            await on_message(note)
        return state
    # Loop a TARGETED fix back to the AI Agent (A+B, Director 2026-06-30).
    # B: materialize ONE fix task carrying the Auditor's findings as its brief — the already-done plan tasks
    #    STAY done. NO whole-build re-run (the v1 gate_g `_reset_done_tasks_for_regate` reset-all was the
    #    overnight-token-burn cause: a single behavioural-acceptance FAIL re-ran all N tasks from #1).
    # A: GATE the re-run for a new_version — STOP for the Manažér to confirm the fix (status `paused` →
    #    'Pokračovať' resumes Programovanie and runs the fix task), NEVER an unattended auto re-dispatch
    #    across a phase boundary. fast_fix keeps its bounded auto fix-loop (zero-approval lane, design §2.4).
    _ensure_verifikacia_fix_task(db, version_id)
    state.is_regate = True
    state.iteration += 1
    state.current_stage = "programovanie"
    state.current_actor = "ai_agent"  # the Programovanie actor (the gated/paused path skips _begin_dispatch)
    db.flush()
    if state.flow_type == "fast_fix":
        _begin_dispatch(db, state)  # bounded auto fix-loop (one task; the lane is full-auto by design)
        return state
    # CR-V2-058 Part A: replace the blind ``paused`` + {Pokračovať/Uprav} (which one-clicked an UN-VETTED fix)
    # with a DELIBERATED Decision Card the Manažér resolves from the screen — human explanation + INDEPENDENTLY
    # vetted options + recommendation. The fix task + the iteration bump already happened above (once per FAIL
    # round), so ``accept_fix`` resumes the SAME task with NO second bump (D6). The §2 invariant (never
    # recommend an un-vetted fix) is enforced BY CONSTRUCTION inside :func:`_build_fix_consultation`.
    consult = _build_fix_consultation(db, version_id, state)
    state.status = "blocked"
    state.block_reason = "decision_needed"  # a Manažér DECISION, surfaced as a Decision Card (like exhaustion)
    state.next_action = (
        "Verifikácia našla chybu — rozhodni (Decision Card): spusti preverenú opravu, usmerni ju, alebo podrž."
    )
    db.flush()
    note = _record_message(
        db,
        version_id=version_id,
        stage="verifikacia",
        author="system",
        recipient="manazer",
        kind="consultation",
        content=consult.intro,
        payload={"phase": "verifikacia", "consultation": consult.model_dump(mode="json")},
    )
    if on_message is not None:
        await on_message(note)
    return state


async def _invoke_fix_critique(
    db: Session,
    state: PipelineState,
    *,
    verdict_msg: PipelineMessage,
    metrics: "_DispatchMetrics",
    on_event: Optional[claude_agent.EventCallback] = None,
) -> Optional[FixCritique]:
    """CR-V2-058 Part B — ONE narrowed invocation of the independent fix-critic (``role=AUDITOR_ROLE``),
    modelled on :func:`_plan_pass_once` but for the :class:`FixCritique` ``{accept,narrow,reject}`` shape (NOT
    :data:`PIPELINE_STATUS_JSON_SCHEMA`, whose ``verdict`` is a bool → ParseFail there). Grammar-constrains to
    :data:`FIX_CRITIQUE_JSON_SCHEMA`, meters the turn into ``metrics``, and parses the ``<<<TASK_PLAN_JSON>>>``
    fence (``structured_output`` is dead in this CLI — the same TEXT/fence survival path the task_plan passes
    use). Runs in a FRESH, isolated session (a new ``--session-id`` under the Auditor charter — NOT the
    Auditor's warm verdict session), so the critic is independent of the FINDER, not merely role-split from the
    fixer (review fix). The self-contained directive supplies the findings + proposed_fix; the critic reads the
    code fresh. The CRITIQUE is adversarial (refute the FIX), never a re-confirm.

    FAIL-OPEN (§5): any crash / timeout / parse failure returns ``None`` (the caller records NO ``fix_critique``
    → the Decision Card demotes ``accept_fix`` + recommends guide). We NEVER fall back to a ``paused`` state
    with a one-click un-vetted fix."""
    version_id = state.version_id
    slug = _project_slug_for_version(db, version_id)
    # CR-V2-058 independence (review fix): the critic runs in a FRESH, one-shot session — NOT the Auditor's warm
    # session that authored this FAIL verdict + proposed_fix. Resuming the finder's session would let it re-judge
    # its OWN cure in-context (the exact "same finder re-judges" shape §1 Diera B exists to break). The directive
    # is fully self-contained (embeds findings + proposed_fix + the fixer's permission model + the fake-boundary
    # anti-patterns) and the critic reads the code fresh under the read-only Auditor charter, so a cold session
    # loses only the bias. A fresh uuid + charter → invoke_claude opens a NEW --session-id (claude_agent.py:255);
    # ephemeral — never persisted as an OrchestratorSession (never resumed), so no warm-session bookkeeping.
    session_id = uuid.uuid4()
    model_override, effort_override = _resolve_dispatch_overrides(db, version_id, AUDITOR_ROLE)
    charter_path: Optional[Path] = (
        claude_agent.PROJECTS_ROOT / slug / ".claude" / "agents" / _charter_slug_for_role(AUDITOR_ROLE) / "CLAUDE.md"
    )
    prompt = _fix_critique_directive(db, version_id, verdict_msg=verdict_msg)
    _started = perf_counter()
    try:
        with _engine_session_active(session_id):
            text, usage, structured = _split_claude_result(
                await invoke_claude(
                    project_slug=slug,
                    claude_session_id=session_id,
                    prompt=prompt,
                    charter_path=charter_path,
                    timeout=_timeout_for("verifikacia"),
                    on_event=on_event,
                    model=model_override,
                    effort=effort_override,
                    json_schema=FIX_CRITIQUE_JSON_SCHEMA,
                )
            )
    except (ClaudeAgentError, ClaudeAgentTimeout) as exc:
        # Fail-OPEN: a critic crash/timeout leaves NO fix_critique record → the card demotes accept_fix (§5).
        metrics.record(None, perf_counter() - _started)
        logger.warning("fix-critique invoke failed (fail-open → guide) for version=%s: %s", version_id, exc)
        return None
    metrics.record(usage, perf_counter() - _started)
    obj: Any = structured if structured is not None else extract_task_plan_json(text)
    if isinstance(obj, ParseFailure):
        logger.info("fix-critique fence parse failed (fail-open → guide) for version=%s: %s", version_id, obj.reason)
        return None
    parsed = parse_fix_critique(obj)
    if isinstance(parsed, ParseFailure):
        logger.info("fix-critique invalid (fail-open → guide) for version=%s: %s", version_id, parsed.reason)
        return None
    return parsed


async def _run_fix_critique(
    db: Session,
    state: PipelineState,
    *,
    verdict_msg: PipelineMessage,
    on_event: Optional[claude_agent.EventCallback] = None,
    on_message: Optional[MessageCallback] = None,
) -> None:
    """CR-V2-058 Part B — adversarially critique the Auditor's ``proposed_fix`` BEFORE it becomes the fix task /
    the Decision Card's recommendation, and record an APPEND-ONLY ``fix_critique`` note. Called at the FAIL seam
    of :func:`_run_verifikacia_round` (after the verdict, before the settle), ONLY for a non-fast_fix,
    NON-engine-red FAIL (the mechanical runtime floor IS the truth — D4 — no ``proposed_fix`` to vet).

    On a well-formed critique the note carries ``{verdict, corrected_scope, why}`` (``author=auditor`` →
    ``manazer``, ``kind=notification``): :func:`_latest_verifikacia_fix_scope` then prefers a ``corrected_scope``
    and :func:`_build_fix_consultation` recommends ``accept_fix`` ONLY on accept/narrow. FAIL-OPEN records
    NOTHING → the card demotes ``accept_fix`` + recommends guide (§2 invariant, by construction)."""
    metrics = _DispatchMetrics()
    critique = await _invoke_fix_critique(db, state, verdict_msg=verdict_msg, metrics=metrics, on_event=on_event)
    if critique is None:
        return  # fail-open: no fix_critique record → the card-builder demotes accept_fix, recommends guide
    note = _record_message(
        db,
        version_id=state.version_id,
        stage="verifikacia",
        author="auditor",
        recipient="manazer",
        kind="notification",
        content=f"Preverenie navrhnutej opravy — {critique.verdict.upper()}: {critique.why}".strip(),
        payload={
            "phase": "verifikacia",
            "fix_critique": {
                "verdict": critique.verdict,
                "corrected_scope": critique.corrected_scope,
                "why": critique.why,
            },
            "usage": metrics.usage_payload(),
            "timing": metrics.timing_payload(),
        },
    )
    if on_message is not None:
        await on_message(note)


async def _run_verifikacia_round(
    db: Session,
    state: PipelineState,
    *,
    on_event: Optional[claude_agent.EventCallback] = None,
    directive: Optional[str] = None,
    on_message: Optional[MessageCallback] = None,
) -> PipelineState:
    """The Verifikácia round (CR-V2-014; VERIF-1..VERIF-3, AUD-1(b), AUD-2, AUD-3, AUD-6) — the v2 form of v1
    ``gate_g``, now the independent Auditor's END verification.

    Replaces the v1 ``gate_g`` Coordinator-relay verify (``verify_done`` / ``_verify_with_retries`` per-question
    judge + ``_infer_regate_entry_stage`` Director PASS/FAIL regate inference) with ONE independent Auditor
    invocation governed by the Miera autonómie dial. Today (before this CR) ``verifikacia`` fell through to the
    generic agent turn with no smoke, no verdict, no fix loop; this is the missing round.

    1. **Release-acceptance against INTERNAL FIXTURES** (the behavioural pillar, §2.5): the engine runs the
       built app via :func:`_run_release_smoke` — an ephemeral isolated ``-p <slug>-smoke`` compose up/down,
       NOT a customer instance (deploy is OUT of the pipeline, OQ-3/D6; "Hotovo" = verified, not deployed).
       The boot + acceptance outcome is recorded ``system→manazer`` (valid v2 tokens) and fed into the
       Auditor's brief. A boot/acceptance FAIL does NOT short-circuit — it is fed HONESTLY to the Auditor,
       which weighs it into its verdict (the Auditor is the judge; the engine surfaces the deterministic
       runtime floor).
    2. **Auditor verdict turn** — the independent Auditor (``role=AUDITOR_ROLE``, READ + RUN-ONLY) runs the
       adversarial spot-checks + the explicit §4 hard-security verification per :func:`_verifikacia_directive`
       and emits ONE ``kind=verdict``. The verdict message is recorded ``author=auditor`` / ``recipient=manazer``
       / ``stage=verifikacia`` / ``kind=verdict`` (all valid v2 DB CHECK tokens — never director/coordinator/
       gate_g). DEPTH scales with the dial (OQ-9) via :func:`_resolve_dispatch_overrides` (effort) + the brief.
    3. **Apply the verdict** via the shared :func:`_settle_verifikacia_verdict`:
       * **PASS** → SETTLE for the dial-governed end stop, then the SHARED dial-settle
         (:func:`_settle_phase_boundary`) auto-signs-off to Hotovo at a non-stopping level (gated by the
         no-silent-done invariant — only through a recorded PASS verdict) or stops ``awaiting_manazer``.
       * **FAIL** → loop the fix back to the AI Agent (re-enter Programovanie, bounded by
         :data:`AUDITOR_LOOP_MAX`), then escalate.

    A parse failure of the Auditor turn is fail-CLOSED here (unlike the upfront review, which is an early
    safety net): Verifikácia IS the release gate — an unparseable verdict must NEVER reach Hotovo. It settles
    ``blocked`` with a visible ``system→manazer`` note so the Manažér steers (Uprav / answer); the
    no-silent-done invariant holds (no PASS on record → Hotovo unreachable).

    The sole-mutator invariant holds: this runs inside the dispatch path, always a consequence of an action
    routed through :func:`apply_action`. (``directive`` — a Manažér uprav/ask/answer re-dispatch — is accepted
    for signature symmetry with the other round runners; the Auditor's verdict brief is engine-owned, so a
    Manažér steer that lands here is folded into the brief context, never replacing the verdict instruction.)"""
    version_id = state.version_id
    slug = _project_slug_for_version(db, version_id)
    version_label = db.execute(select(Version.version_number).where(Version.id == version_id)).scalar_one()

    # 1. Release-acceptance against INTERNAL FIXTURES (boot leg + acceptance leg in ONE up/down cycle). NEVER
    # touches a customer instance / uat_provisioner / deploy.py — an ephemeral -p <slug>-smoke stack only.
    # CR-V2-051: the acceptance is risk-floored against the Návrh design's DECLARED flagship features + safety
    # properties — ≥1 FEATURE assertion each, ≥1 NEGATIVE assertion each; missing coverage is a FAIL.
    coverage_req = _declared_release_coverage(db, version_id)
    # obs-2 Part B Part 2: bake the completing version's REAL note onto disk BEFORE the smoke so the 2a gate
    # asserts a served note (not a placeholder / a 2nd-version list missing its own note). PASS-time commit
    # (:func:`_commit_release_note`) is unchanged — this is an idempotent pre-write.
    _write_release_note_to_disk(db, version_id, claude_agent.PROJECTS_ROOT / slug)
    (smoke_ok, smoke_detail), acceptance = await _run_release_smoke(slug, version_label, coverage_req)
    # Plain-language framing (self-sufficiency kernel): the manager-facing content is the HUMANISED WHY, never
    # the raw probe detail; the raw rides in payload.technical_detail for the FE's collapsible "Technický detail".
    smoke_content = (
        "Skúška spustenia (interné fixtúry) — aplikácia sa spustila ✓"
        if smoke_ok
        else f"Skúška spustenia (interné fixtúry) — {failure_framing.humanize_release_failure(smoke_detail)}"
    )
    smoke_msg = _record_message(
        db,
        version_id=version_id,
        stage="verifikacia",
        author="system",
        recipient="manazer",
        kind="notification",
        content=smoke_content,
        payload={
            "phase": "verifikacia",
            "smoke": {"pass": smoke_ok, "detail": smoke_detail},
            **({} if smoke_ok else {"technical_detail": smoke_detail}),
        },
    )
    if on_message is not None:
        await on_message(smoke_msg)
    # The acceptance leg only ran if boot passed (else None). Record it + build the Slovak block for the brief.
    if acceptance is not None:
        acc_ok, acc_detail, acc_skipped = acceptance
        if acc_ok:
            acc_content = "Automatická skúška po spustení — prešla ✓"
        elif acc_skipped:
            acc_content = "Automatická skúška po spustení — preskočená (nie sú definované kontroly)"
        else:
            acc_content = f"Automatická skúška po spustení — {failure_framing.humanize_release_failure(acc_detail)}"
        acc_msg = _record_message(
            db,
            version_id=version_id,
            stage="verifikacia",
            author="system",
            recipient="manazer",
            kind="notification",
            content=acc_content,
            payload={
                "phase": "verifikacia",
                "release_acceptance": {"pass": acc_ok, "detail": acc_detail, "skipped": acc_skipped},
                **({"technical_detail": acc_detail} if (not acc_ok and not acc_skipped) else {}),
            },
        )
        if on_message is not None:
            await on_message(acc_msg)
        acc_line = "PASS" if acc_ok else ("SKIP" if acc_skipped else "FAIL")
        smoke_block = (
            f"   Engine release smoke (interné fixtúry): boot {'PASS' if smoke_ok else 'FAIL'} — {smoke_detail}; "
            f"acceptance {acc_line} — {acc_detail}.\n"
        )
    else:
        smoke_block = (
            f"   Engine release smoke (interné fixtúry): boot FAIL — {smoke_detail} "
            "(acceptance sa nespustila). Zohľadni to vo verdikte.\n"
        )
    # CR-V2-050 (fail-closed hard-gate): the mechanically-computed release evidence is AUTHORITATIVE, not
    # advisory. A red boot smoke, or an acceptance leg that RAN but did not pass (a SKIP is not red), floors
    # the verdict to FAIL below regardless of what the Auditor LLM says — the single change that stops a red
    # smoke coexisting with a green gate (the NEX Agents self-confirming-test hole).
    runtime_floor_red = (not smoke_ok) or (acceptance is not None and not acceptance[0] and not acceptance[2])

    # B (release-smoke-boot-and-batch-fixes.md): a boot-FAIL is a DECISIVE product FAIL — the app never
    # started, so there is NOTHING for the Auditor to verify. Settle a clean Verifikácia FAIL carrying the boot
    # reason DETERMINISTICALLY, AHEAD of (and independent of) the Auditor turn + its verdict-parse block. Without
    # this, a stack that never booted still burned an Auditor turn whose verdict could time out / not parse →
    # the manager saw the CONFUSING "verdikt sa nepodarilo spracovať / blocked" instead of the TRUTH: the app
    # didn't boot. The recorded FAIL verdict's findings carry the boot reason, so the fix↔re-verify loop
    # (_latest_verifikacia_fix_scope) threads it to the AI Agent as the fix brief. Settled via the SHARED
    # _settle_verifikacia_verdict (runtime_floor_red=True → the bounded fix loop / escalation, never a PASS).
    if not smoke_ok:
        # Plain-language framing: the manager-facing finding is the humanised WHY (it flows into the Decision
        # Card explanation via _latest_verifikacia_fix_scope), never the raw probe detail. The AI Agent fixer
        # reproduces the boot failure itself (`docker compose up`, per proposed_fix); the raw probe string is
        # kept in payload.technical_detail as a breadcrumb + the FE's collapsible "Technický detail".
        boot_fail_content = f"Appka sa nespustila — {failure_framing.humanize_release_failure(smoke_detail)}"
        boot_verdict_msg = _record_message(
            db,
            version_id=version_id,
            stage="verifikacia",
            author="auditor",  # the release verdict channel (valid v2 token) — consistent with the CR-V2-050 floor
            recipient="manazer",
            kind="verdict",
            content=boot_fail_content,
            payload={
                "verdict": "FAIL",
                "findings": [boot_fail_content],
                "proposed_fix": ("Zisti a oprav dôvod, prečo sa appka nespustí (`docker compose up`), a over znova."),
                "phase": "verifikacia",
                "engine_override": "boot_fail",
                "technical_detail": smoke_detail,
            },
        )
        if on_message is not None:
            await on_message(boot_verdict_msg)
        return await _settle_verifikacia_verdict(
            db, state, verdict="FAIL", runtime_floor_red=True, on_message=on_message
        )

    # 2. The Auditor's verdict turn (independent, READ + RUN-ONLY). Recorded author=auditor / recipient=manazer
    # / stage=verifikacia / kind=verdict by invoke_agent — all valid v2 tokens. Effort scales with the dial.
    review = await invoke_agent_with_parse_retry(
        db,
        version_id=version_id,
        role=AUDITOR_ROLE,
        stage="verifikacia",
        prompt=_verifikacia_directive(db, version_id, smoke_block=smoke_block, flow_type=state.flow_type),
        on_event=on_event,
        recipient="manazer",
        on_message=on_message,
    )
    if isinstance(review, ParseFailure):
        # Fail-CLOSED at the release gate: an unparseable verdict must NEVER reach Hotovo (unlike the upfront
        # review's fail-open early net). Record it visibly + metered (system → manazer) and settle blocked.
        # The no-silent-done invariant holds: no PASS verdict on record → Hotovo unreachable.
        msg = _record_message(
            db,
            version_id=version_id,
            stage="verifikacia",
            author="system",
            recipient="manazer",
            kind="notification",
            content=(
                "Verdikt Auditora vo Verifikácii sa nepodarilo spracovať ani po opakovaných pokusoch — "
                "Verifikácia je blokovaná (release gate, fail-closed). Usmerni (Uprav) alebo over znova."
            ),
            payload=_failure_metrics_payload(review) or None,
        )
        if on_message is not None:
            await on_message(msg)
        state.status = "blocked"
        state.block_reason = "agent_error"  # R4 (D1): the release verdict turn produced no parseable output
        state.next_action = "Blokované — Auditor nevrátil platný verdikt Verifikácie. Usmerni (Uprav) alebo over znova."
        db.flush()
        return state

    # 3. Apply the verdict (fail-closed: a verdict block without an explicit verdict=true is a FAIL — mirrors
    # _verifikacia_passed). The kind=verdict message was already recorded by invoke_agent (author=auditor) but
    # WITHOUT the canonical PASS/FAIL payload _verifikacia_passed / _latest_verifikacia_fix_scope read — record
    # the canonical verdict message now (the durable Verifikácia artifact) so both gates see it.
    # CR-V2-050: the computed runtime floor OVERRIDES the Auditor LLM string — a red smoke/acceptance is a
    # deterministic FAIL the LLM cannot upgrade to PASS.
    llm_pass = review.kind == "verdict" and bool(review.verdict)
    is_pass = llm_pass and not runtime_floor_red
    verdict_str = "PASS" if is_pass else "FAIL"
    # CR-V2-056 (layer-1): bind the PASS to the commit it verified + tag it, so version_verified() recomputes
    # against the live HEAD (a moved HEAD auto-un-verifies — kills the frozen-PASS bug). slug + version_label
    # are already in scope in _run_verifikacia_round.
    # Part 1 (per-app-changelog-standard.md §1): on a PASS, (re)generate + commit the user-facing
    # RELEASE_NOTES.md into the app repo BEFORE anchoring the verified SHA, so the note rides the
    # verified/tagged commit (version_verified recomputes against the note commit, not its parent).
    if is_pass:
        _commit_release_note(db, version_id, claude_agent.PROJECTS_ROOT / slug, version_label)
    verified_sha = _repo_head(claude_agent.PROJECTS_ROOT / slug) if is_pass else None
    if verified_sha:
        _git_tag_version(claude_agent.PROJECTS_ROOT / slug, version_label, verified_sha)
    verdict_msg = _record_message(
        db,
        version_id=version_id,
        stage="verifikacia",
        author="auditor",
        recipient="manazer",
        kind="verdict",
        content=review.summary or f"Verifikácia {verdict_str}.",
        payload={
            "verdict": verdict_str,
            "findings": (
                [
                    *review.findings,
                    "ENGINE OVERRIDE (CR-V2-050): a red release smoke/acceptance floored the verdict to FAIL "
                    "regardless of the Auditor's PASS.",
                ]
                if (llm_pass and runtime_floor_red)
                else review.findings
            ),
            "proposed_fix": review.proposed_fix,
            "phase": "verifikacia",
            **({"engine_override": "runtime_floor_red"} if (llm_pass and runtime_floor_red) else {}),
            **({"verified_sha": verified_sha} if verified_sha else {}),
        },
    )
    if on_message is not None:
        await on_message(verdict_msg)

    # CR-V2-058 Part B (the NOSNÁ half): before the settle builds the Decision Card, adversarially PRE-VET the
    # Auditor's proposed_fix with an INDEPENDENT critic (finder/fixer/critic split — the finder no longer both
    # proposes AND has its raw scope trusted). Always-on for ``new_version`` (Director-approved cost, §6). SKIP
    # a ``runtime_floor_red`` FAIL (the mechanical floor IS the truth — no proposed_fix to vet, D4) and the
    # ``fast_fix`` lane (its focused auto loop is unchanged, §6 D3). Fail-open inside → no record → the card
    # demotes accept_fix (§2). The critic writes an append-only fix_critique note the settle then reads.
    if verdict_str == "FAIL" and not runtime_floor_red and state.flow_type != "fast_fix":
        await _run_fix_critique(db, state, verdict_msg=verdict_msg, on_event=on_event, on_message=on_message)

    settled = await _settle_verifikacia_verdict(
        db, state, verdict=verdict_str, runtime_floor_red=runtime_floor_red, on_message=on_message
    )
    if verdict_str == "FAIL":
        return settled  # the fix loop re-entered Programovanie (or escalated) — already settled
    # PASS → the dial governs the end sign-off. _settle_verifikacia_verdict put it awaiting_manazer; now apply
    # the SHARED dial-settle: a non-stopping level auto-signs-off to Hotovo (gated by the no-silent-done
    # invariant — the PASS verdict is now on record), else it stays awaiting_manazer for the Manažér.
    if _settle_phase_boundary(db, settled):
        return settled  # (Verifikácia auto-sign-off advances to done inside _settle_phase_boundary, not here)
    return settled


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
    Re-run tasks keep their ``baseline_sha`` (a fresh anchor is a separate Director ``move_baseline``).

    SUPERSEDED (A+B, Director 2026-06-30) on the Verifikácia-FAIL path by :func:`_ensure_verifikacia_fix_task`
    (a TARGETED one-task fix; done stays done). Kept for any other re-gate caller; do NOT re-introduce the
    whole-build reset on a behavioural-acceptance FAIL — it re-ran all N tasks from #1 (the token-burn bug)."""
    feat_ids = select(Feat.id).join(Epic, Epic.id == Feat.epic_id).where(Epic.version_id == version_id)
    db.execute(update(Task).where(Task.feat_id.in_(feat_ids), Task.status == "done").values(status="todo"))
    db.flush()


#: Marker Epic title for the targeted Verifikácia-FAIL fix task — used to find+reuse it across the bounded
#: fix↔re-verify rounds (so a multi-round loop never accumulates fix tasks). Visible in the task plan (honest).
#: Title (a plain LABEL, never a lookup key) of the per-round targeted Verifikácia-FAIL fix Epic/Feat/Task.
_VERIFIKACIA_FIX_TITLE = "Oprava po Verifikácii"


def _ensure_verifikacia_fix_task(db: Session, version_id: uuid.UUID) -> None:
    """B (Director 2026-06-30): materialize a fresh TARGETED fix Task for a Verifikácia FAIL so ONLY the fix
    re-runs — the already-done plan tasks STAY done (replaces the whole-build :func:`_reset_done_tasks_for_regate`,
    the overnight-token-burn cause). The Auditor's findings ARE the fix brief: set as the task description AND
    threaded into attempt 1 by :func:`_run_build_round` (``is_regate`` → :func:`_latest_verifikacia_fix_scope`).

    Creates a FRESH Epic→Feat→Task each FAIL round — it does NOT reuse-by-title: an Epic title has no unique
    constraint, so a title-match query could hijack a user- OR agent-authored Epic of the same name and
    corrupt the plan (review blocker, 2026-06-30). The loop is bounded by ``AUDITOR_LOOP_MAX``, so at most that
    many small fix epics accrue — an acceptable, honest record of each fix attempt; the build loop's
    ``get_next_todo_task`` picks the fresh todo fix task while the prior (done) plan tasks stay done."""
    version = db.get(Version, version_id)
    if version is None:
        return
    scope = _latest_verifikacia_fix_scope(db, version_id) or "Oprav blokujúce zlyhanie z koncovej Verifikácie."
    epic = epic_service.create(
        db, EpicCreate(project_id=version.project_id, version_id=version_id, title=_VERIFIKACIA_FIX_TITLE)
    )
    feat = feat_service.create(db, FeatCreate(epic_id=epic.id, title=_VERIFIKACIA_FIX_TITLE, description=scope))
    task_service.create(
        db, TaskCreate(feat_id=feat.id, title=_VERIFIKACIA_FIX_TITLE, description=scope, task_type="backend")
    )
    db.flush()


async def _route_manazer_fix_to_ai_agent(
    db: Session, state: PipelineState, *, comment: str, on_message: Optional[MessageCallback] = None
) -> PipelineState:
    """CR-V2-054 — route a Manažér-directed fix at Verifikácia to the AI Agent (the FIXER), NOT the Auditor
    (the finder). This is the operator-actionable half of safeguard #5: a release-gate blocker becomes a
    concrete fix the operator can trigger, without having to know the fixer/finder split (the bug that made
    the NEX Agents dogfood need Dedo — an 'Uprav' at Verifikácia hit the Auditor, which just re-confirmed).

    The operator's comment (an 'Uprav' or an escalation Decision Card answer) IS the fix brief: record it as a
    ``manazer→ai_agent`` return (:func:`_latest_verifikacia_fix_scope` reads it as the most-recent verifikacia
    directive), materialize ONE targeted fix task (the done plan tasks STAY done), RESET the bounded loop
    counter (a human now steers — ``AUDITOR_LOOP_MAX`` bounds the AUTONOMOUS re-verify loop, not human
    interventions), and re-enter Programovanie (``paused`` for a ``new_version`` so the Manažér confirms the
    re-run via 'Pokračovať'; auto-dispatched on the ``fast_fix`` lane)."""
    version_id = state.version_id
    ret = _record_message(
        db,
        version_id=version_id,
        stage="verifikacia",
        author="manazer",
        recipient="ai_agent",
        kind="return",
        content=comment,
        payload={"phase": "verifikacia", "manazer_fix_directive": True},
    )
    if on_message is not None:
        await on_message(ret)
    _ensure_verifikacia_fix_task(db, version_id)  # brief = the manazer directive just recorded (fix-scope reader)
    state.is_regate = True
    state.iteration = 0  # human-directed fresh attempt — reset the bounded AUTONOMOUS loop counter
    state.current_stage = "programovanie"
    state.current_actor = "ai_agent"
    db.flush()
    if state.flow_type == "fast_fix":
        _begin_dispatch(db, state)  # zero-approval lane drives the fix through
    else:
        state.status = "paused"  # mandatory phase gate — Manažér confirms the re-run via 'Pokračovať'
        state.next_action = "Oprava podľa tvojho pokynu je pripravená — 'Pokračovať' ju spustí."
        db.flush()
    return state


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


# ---------------------------------------------------------------------------
# Verifikácia FAIL fix-scope (v2; CR-V2-014, AUD-3). The v1 gate_g re-gate-inference family
# (``_latest_gate_g_classifying_directive`` / ``_infer_regate_entry_stage`` / ``_latest_gate_g_findings`` /
# ``_latest_surgical_fix_directive``) is REMOVED with the v1 board route (CR-V2-021): it read the retired
# ``gate_g`` stage + ``coordinator``/``director`` author tokens the v2 DB CHECK rejects, and its only live
# referrer was the v1 ``_board()`` regate proposal — dropped here. The v2 source of a fix scope is the
# Auditor's own Verifikácia verdict (:func:`_latest_verifikacia_fix_scope`).
# ---------------------------------------------------------------------------


def _latest_verifikacia_fix_scope(db: Session, version_id: uuid.UUID) -> Optional[str]:
    """The Auditor's latest Verifikácia FAIL findings + ``proposed_fix``, formatted as the AI-Agent fix-scope
    brief threaded into the Programovanie re-loop (CR-V2-014; AUD-3 — the salvaged ``surgical_fix`` targeted
    re-run scope, now an AI-AGENT fix scope, NOT a Director directive).

    Replaces the v1 ``_latest_surgical_fix_directive`` + ``_latest_gate_g_findings`` re-gate threading
    (which read ``director→implementer``/``gate_g`` tokens the v2 DB CHECK rejects). The v2 source is the
    Auditor's own verdict: the LATEST ``stage=verifikacia`` ∧ ``kind=verdict`` ∧ ``payload.verdict=='FAIL'``
    message (``author=auditor`` — a valid v2 token). Its ``findings`` (the concrete failures) + ``proposed_fix``
    (the targeted scope the Auditor proposes, never an edit by it — independence) become the fix brief the AI
    Agent re-runs against in the bounded fix↔re-verify loop. ``None`` when there is no FAIL verdict on record
    (a fresh build, or the last verdict was a PASS) → the build loop falls back to its generated task briefs.

    CR-V2-054: a Manažér 'Uprav' / escalation-decision at Verifikácia records a ``manazer→ai_agent``
    ``kind=return`` — the OPERATOR's own fix instruction. When that return is the MOST RECENT verifikacia
    message, it IS the fix brief (the operator is steering the fix directly), taking precedence over a prior
    Auditor FAIL verdict."""
    latest = db.execute(
        select(PipelineMessage)
        .where(
            PipelineMessage.version_id == version_id,
            PipelineMessage.stage == "verifikacia",
            PipelineMessage.kind.in_(("verdict", "return")),
        )
        .order_by(PipelineMessage.seq.desc())
        .limit(1)
    ).scalar_one_or_none()
    if latest is None:
        return None
    if latest.kind == "return":
        # CR-V2-054: the operator's directed fix instruction (most recent) IS the brief.
        directive = (latest.content or "").strip()
        if not directive:
            return None
        return (
            "## Verifikácia — oprav podľa pokynu Manažéra (cielená oprava, potom Auditor re-verifikuje)\n" + directive
        )
    if not latest.payload or latest.payload.get("verdict") != "FAIL":
        return None
    findings = latest.payload.get("findings") or []
    # CR-V2-058 Part B (read-precedence): when an independent fix-critic vetted THIS round's proposed_fix and
    # returned a non-empty ``corrected_scope`` (a ``narrow`` — or an ``accept``/``reject`` that still supplied a
    # better default), the fix task must materialize the VETTED scope, NOT the Auditor's raw proposed_fix. The
    # manazer-``return`` precedence above still wins (a human steer has the highest seq); this only refines the
    # Auditor-verdict branch. No critique / no corrected_scope → the raw proposed_fix, exactly as before.
    critique = _latest_fix_critique(db, version_id)
    corrected = str((critique or {}).get("corrected_scope") or "").strip()
    proposed_fix = corrected or latest.payload.get("proposed_fix")
    parts: list[str] = []
    if proposed_fix:
        parts.append(str(proposed_fix).strip())
    if findings:
        parts.append("\n".join(f"- {f}" for f in findings))
    if not parts:
        return None
    heading = (
        "## Verifikácia FAIL — oprav podľa PREVERENÉHO rozsahu (kritik upravil rozsah; potom Auditor re-verifikuje)\n"
        if corrected
        else "## Verifikácia FAIL — oprav podľa nálezov Auditora (cielená oprava, potom Auditor re-verifikuje)\n"
    )
    return heading + "\n\n".join(parts)


def _latest_runtime_floor_red(db: Session, version_id: uuid.UUID) -> bool:
    """CR-V2-050 — recompute the fail-closed runtime floor for the MANUAL verdict path from the canonical
    release-evidence messages the autonomous Verifikácia round already recorded
    (:func:`_run_verifikacia_round` writes ``payload.smoke`` for the boot leg and ``payload.release_acceptance``
    for the acceptance leg). The floor is RED when the latest boot smoke FAILED, or the latest acceptance leg
    RAN but did not pass (a SKIP is not red). This guarantees a Manažér PASS-override at a Verifikácia stop can
    no more cross a red floor than the autonomous verdict can. Returns ``False`` (floor clear) when no evidence
    is on record — a manual verdict with no recorded smoke is not the release oracle's to hold."""
    rows = (
        db.execute(
            select(PipelineMessage)
            .where(
                PipelineMessage.version_id == version_id,
                PipelineMessage.stage == "verifikacia",
                PipelineMessage.author == "system",
                PipelineMessage.kind == "notification",
            )
            .order_by(PipelineMessage.seq.desc())
            .limit(40)
        )
        .scalars()
        .all()
    )
    smoke_pass: Optional[bool] = None
    acc: Optional[tuple[bool, bool]] = None  # (pass, skipped) of the latest acceptance leg
    for m in rows:
        p = m.payload or {}
        if smoke_pass is None and isinstance(p.get("smoke"), dict):
            smoke_pass = bool(p["smoke"].get("pass"))
        if acc is None and isinstance(p.get("release_acceptance"), dict):
            ra = p["release_acceptance"]
            acc = (bool(ra.get("pass")), bool(ra.get("skipped")))
        if smoke_pass is not None and acc is not None:
            break
    if smoke_pass is None:
        return False  # no boot evidence on record → floor is not the oracle's to hold
    if not smoke_pass:
        return True
    return acc is not None and not acc[0] and not acc[1]


# ── v2 board aggregation (CR-V2-021) ───────────────────────────────────────────────────────────────
# Computed at board-fetch (api/routes/pipeline.py:_board) — a bounded per-version scan, no N+1, mirroring
# the existing per-fetch build_readiness count. The v1 R4 operator-legibility roll-ups (``coordinator_triage``
# / ``autonomous_decisions_summary`` + ``_scope_escalations_this_iteration``) are REMOVED here with the v1
# board route: they read the retired ``coordinator``/``gate_g`` tokens the v2 DB CHECK rejects (the Coordinator
# hub-and-spoke is gone, design §2.2 — the AI Agent reports to the Manažér directly, the Auditor's verdict is
# the only second voice). Only the per-agent liveness chip (:func:`agent_sessions`) survives for the who's-up
# status of the two v2 agents.

#: An OrchestratorSession idle longer than this reads as ``stale`` on the rail (D5 — 30 min).
_AGENT_STALE_SECONDS = 1800
#: The agent roles shown on the rail — the OrchestratorSession.role set = ACTOR_VALUES (CR-V2-001),
#: i.e. the two v2 agents (DB values, underscore). CR-V2-007 collapsed the v1 5-role set to these.
_AGENT_SESSION_ROLES = (AI_AGENT_ROLE, AUDITOR_ROLE)


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
        if ts is not None and ts.tzinfo is None:  # DB stores tz-aware; guard a naive timestamp
            ts = ts.replace(tzinfo=timezone.utc)
        if role == working_role:
            # Audit P2 (2026-07-12): don't hard-code "active" for the working role — a WEDGED dispatch
            # (``agent_working`` but its ``last_input_at`` heartbeat is old) would otherwise show a healthy
            # green "working" chip for the whole wall-clock budget. A stale heartbeat → "stale" so the manager
            # sees the turn is hung, not progressing. (No heartbeat yet = just started → "active".)
            session_status = (
                "stale" if (ts is not None and (now - ts).total_seconds() > _AGENT_STALE_SECONDS) else "active"
            )
        elif ts is None:
            session_status = "idle"
        else:
            if ts.tzinfo is None:  # be robust to a naive timestamp (DB stores tz-aware; guard anyway)
                ts = ts.replace(tzinfo=timezone.utc)
            session_status = "stale" if (now - ts).total_seconds() > _AGENT_STALE_SECONDS else "idle"
        sessions.append({"role": role, "status": session_status})
    return sessions


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
SCHVALOVACI_BOD_VIZUAL = "vizual"  # CR-1: after Vizuál (the live-preview walk) → Programovanie
SCHVALOVACI_BOD_PROGRAMOVANIE = "programovanie"  # after Programovanie (the coding phase)
SCHVALOVACI_BOD_VERIFIKACIA = "verifikacia"  # after Verifikácia = build verified/done (the "end" stop)
#: Every dial-governed boundary (the schvaľovacie body the dial can halt at). For a ``new_version`` EVERY
#: phase boundary is a mandatory Manažér stop regardless of the dial (see :func:`_settle_phase_boundary`),
#: so the Vizuál boundary (``vizual → programovanie``) belongs here too. (The minimal CR-1 Vizuál round
#: settles ``awaiting_manazer`` itself rather than going through the dial, so membership here is the design
#: signal + future-proofing for when the round auto-continues at a non-stopping level.)
DIAL_GOVERNED_BOUNDARIES = frozenset(
    {SCHVALOVACI_BOD_NAVRH, SCHVALOVACI_BOD_VIZUAL, SCHVALOVACI_BOD_PROGRAMOVANIE, SCHVALOVACI_BOD_VERIFIKACIA}
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
        mandatory FOR A ``new_version``: Príprava is NOT in ``DIAL_GOVERNED_BOUNDARIES``, so
        :func:`dial_stops_at` is never even consulted for it here → it always returns ``False`` (STOP).
        Návrh cannot begin until the Manažér clicks ``approve_spec``. **A ``fast_fix`` is the exception
        (CR-V2-028):** it produces NO Špecifikácia (the directive IS the brief; submitting it is the
        authorization), so its Príprava AUTO-CONTINUES straight to Programovanie — zero mid-flight
        approvals (design §2.4/§2.5).
      * **Verifikácia end sign-off** — at a non-stopping dial level a PASS verdict auto-signs-off to Hotovo,
        but ONLY through the recorded Auditor PASS verdict (no-silent-done invariant, safeguard #5): if no
        PASS is on record the boundary STOPS regardless of the dial (never a silent done without
        verification). The full Verifikácia behaviour (verdict emission, fix-loop) is CR-V2-014; this wiring
        only governs the dial half of the end stop + preserves the invariant.

    Auto-continue advances ``current_stage`` via :func:`_next_stage` + :func:`_begin_dispatch` (which sets
    ``agent_working`` at the next phase). The sole-mutator invariant is preserved: this runs inside the
    dispatch path, always as a consequence of an action already routed through :func:`apply_action`."""
    stage = state.current_stage
    # Fast-fix Príprava (CR-V2-028; design §2.4/§2.5 "Autonomous — zero mid-flight approvals"): the
    # ``approve_spec`` always-stop carve-out exists to gate the Manažér's reading + approval of a real
    # Špecifikácia (a ``new_version`` deliverable). A fast-fix produces NO Špecifikácia — the directive IS
    # the brief and SUBMITTING the fast-fix directive is itself the authorization — so there is nothing to
    # approve. Auto-continue Príprava → Programovanie so the lane runs full-auto through to verified
    # (consistent with the fast-fix dial=plna carve-out). Only fast-fix; a new_version Príprava still
    # ALWAYS stops at ``approve_spec`` (D3, dial-independent).
    if stage == "priprava":
        if state.flow_type != "fast_fix":
            return False  # new_version: the Špecifikácia approval is ALWAYS mandatory (approve_spec stop)
        state.current_stage = _next_stage("priprava", state.flow_type)  # fast_fix → programovanie
        _begin_dispatch(db, state)  # agent_working at Programovanie → the auto-chain loop runs it
        return True
    if stage not in DIAL_GOVERNED_BOUNDARIES:
        # Any other non-boundary phase: never auto-continue here.
        return False
    # A (Director 2026-06-30): a new_version build STOPS at EVERY phase boundary (Návrh→Programovanie,
    # Programovanie→Verifikácia, Verifikácia→Hotovo) for the Manažér's confirmation ('schvalit'),
    # INDEPENDENT of the Miera autonómie dial — a hard gate so an autonomous run can NEVER cross a phase
    # unattended (the overnight-token-burn safeguard). The dial no longer skips phase gates; it now governs
    # only the Auditor's depth (:func:`auditor_effort_for_level`). A ``fast_fix`` keeps its zero-approval
    # lane (the directive IS the authorization; one bounded task — design §2.4/§2.5).
    if state.flow_type != "fast_fix":
        return False  # mandatory phase gate — STOP for the Manažér (dial-independent)
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


# (The v1 ``_maybe_autonomous_build_ratify`` — auto-ratify the build→gate_g sign-off — is RETIRED with
# CR-V2-012's build-round rebuild: it was build-completion-only, referenced the retired v1 ``build``/``gate_g``
# stages, and is subsumed by the Miera autonómie dial (the Programovanie schvaľovací bod auto-continues to
# Verifikácia at a non-stopping level via :func:`_settle_phase_boundary`).
# CR-V2-013 RETIRES the Gate-E auto-continue helpers ``_gate_e_budget_reached`` +
# ``_maybe_autonomous_gate_e_continue`` with the rest of the Gate-E machinery: the v2 Auditor upfront review
# is ONE invocation (no per-question Branch-A/topic auto-continue loop), and the post-Návrh stop is governed
# by the Miera autonómie dial (:func:`dial_stops_at`) — a found HOLE forces the stop regardless of the dial,
# see :func:`_run_auditor_upfront_review`.)


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
    """Re-read the cross-cutting regulated-ledger invariants the AI Agent codified once in the plan
    gate_report payload (CR-NS-020 CR-2; v2 CR-V2-011). Injected into every per-task build brief (consumed
    by the Programovanie loop, CR-V2-012).

    The plan gate_report used to be recorded in the Návrh phase (``stage='navrh'``); since nex-studio-visual
    (Director 2026-07-13) the task plan is built at Programovanie ENTRY, so its gate_report is recorded under
    ``stage='programovanie'`` (with ``payload['phase']=='navrh'``). Match it by the ``cross_cutting_rules``
    payload it uniquely carries — the per-task build gate_reports of the loop carry none — newest-first, so
    the rules are found regardless of which stage the plan gate_report landed under."""
    msgs = (
        db.execute(
            select(PipelineMessage)
            .where(
                PipelineMessage.version_id == version_id,
                PipelineMessage.stage.in_(("navrh", "programovanie")),
                PipelineMessage.author == "ai_agent",
                PipelineMessage.kind == "gate_report",
            )
            .order_by(PipelineMessage.seq.desc())
        )
        .scalars()
        .all()
    )
    for msg in msgs:
        if msg.payload and msg.payload.get("cross_cutting_rules"):
            return msg.payload["cross_cutting_rules"]
    return None


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


def _envelope_loss_next_action(kind: str, timeout_seconds: int, log_path: Optional[str]) -> str:
    """Plain-language build-round settle message routed by the envelope-loss TYPE
    (build-robustness-crash-handling.md Fix 3). A REAL timeout and a CRASH must NOT share the misleading
    "Vypršal čas agenta" string:

      * ``timeout`` — the turn burned its whole wall-clock budget; the work committed so far is safe and the
        manager resumes the build.
      * ``crash`` — the agent lost its connection / crashed (NOT a timeout); ONE auto-retry (Fix 2) already
        ran and failed again; the diagnostic log path (Fix 1) is cited so the operator/Dedo can read the
        cause.
    """
    if kind == "timeout":
        minutes = max(1, round(timeout_seconds / 60))
        return f"Agent vyčerpal časový limit ({minutes} min) — hotové zmeny sú zapísané, môžeš pokračovať v stavbe."
    log_note = f" (log: {log_path})" if log_path else ""
    return (
        "Agent stratil spojenie / spadol (nie časový limit) — skúsil som to raz znova, opäť zlyhalo. "
        f"Hotové zmeny sú zapísané, môžeš pokračovať.{log_note}"
    )


async def _dispatch_build_turn(
    db: Session,
    *,
    version_id: uuid.UUID,
    task: Task,
    attempt: int,
    prompt: str,
    mode: Optional[str],
    on_event: Optional[claude_agent.EventCallback],
    on_message: Optional[MessageCallback],
) -> PipelineStatusBlock | ParseFailure:
    """One Programovanie build turn (build-robustness-crash-handling.md Fix 2 seam). A thin wrapper over
    :func:`invoke_agent_with_parse_retry` so :func:`_run_build_round` can run the SAME turn twice — the
    initial dispatch AND the single crash auto-retry — from one call site (no duplication, no in-loop
    closure that would capture the loop task)."""
    return await invoke_agent_with_parse_retry(
        db,
        version_id=version_id,
        role=AI_AGENT_ROLE,
        stage="programovanie",
        prompt=prompt,
        on_event=on_event,
        on_message=on_message,
        extra_payload={"task_id": str(task.id), "task_number": task.number, "attempt": attempt},
        # metrics-v3-three-phases.md Part 1: stamp the build turn's metrics phase explicitly (already
        # 'programovanie' via the stage fallback — this is robustness). Only for the conversation flow;
        # the legacy automaton (mode NULL) passes None so its payload stays byte-for-byte unchanged.
        metrics_phase="programovanie" if mode == "conversation" else None,
    )


def _vizual_url_recorded(db: Session, version_id: uuid.UUID) -> bool:
    """True iff a Vizuál preview-URL notification has ALREADY been recorded for this version (CR-1).

    The live-preview URL is announced to the Manažér ONCE — on the FIRST entry into the ``vizual`` stage. The
    round is re-entered on every Manažér change-request (the HMR loop) and ``vizual_sandbox.spin_up`` is
    idempotent, so re-announcing the URL each turn would SPAM the board. Detect a first entry by the ABSENCE
    of any prior ``vizual`` ∧ ``notification`` ∧ ``payload.vizual_url`` message (the same ``.astext`` JSONB
    probe style used by :func:`_latest_programming_complete_seq` / the kontrola probes)."""
    seq = db.execute(
        select(func.max(PipelineMessage.seq)).where(
            PipelineMessage.version_id == version_id,
            PipelineMessage.stage == "vizual",
            PipelineMessage.kind == "notification",
            PipelineMessage.payload["vizual_url"].astext.isnot(None),
        )
    ).scalar()
    return seq is not None


def latest_vizual_url(db: Session, version_id: uuid.UUID) -> Optional[str]:
    """The live-preview URL to embed in the cockpit Vizuál iframe, or None (CR-1, cockpit Vizuál surface).

    Returns the ``payload.vizual_url`` of the LATEST (max ``seq``) ``vizual`` ∧ ``notification`` message that
    carries one — the URL announced by :func:`_run_vizual_round` on entry into the stage. ``None`` when the
    version never entered ``vizual`` (or no URL was recorded). The board route surfaces this on
    ``PipelineBoardRead.vizual_url``; the FE embeds the running dev-server so the Manažér can walk it live.
    Same ``.astext`` JSONB probe as :func:`_vizual_url_recorded`, ordered newest-first."""
    return db.execute(
        select(PipelineMessage.payload["vizual_url"].astext)
        .where(
            PipelineMessage.version_id == version_id,
            PipelineMessage.stage == "vizual",
            PipelineMessage.kind == "notification",
            PipelineMessage.payload["vizual_url"].astext.isnot(None),
        )
        .order_by(PipelineMessage.seq.desc())
        .limit(1)
    ).scalar_one_or_none()


def _vizual_mockup_rel(project_root: Path, version_number: str) -> Optional[str]:
    """Repo-relative path of the version's Vizuál MOCKUP html, or ``None`` (Director 2026-07-17).

    The AI's self-contained clickable mockup lives under ``docs/specs/versions/v<N>/visual/``. When present,
    the Vizuál preview serves THIS (walkable with no backend and no login) instead of the auth-gated raw FE
    Vite scaffold — a new project's FE is behind ``ProtectedRoute`` → login, and the sandbox has no backend /
    user / token, so the live scaffold is a dead login screen (nex-shopify 2026-07-17). Prefers ``index.html``;
    else the newest ``*.html`` in the dir."""
    vis_dir = project_root / _version_spec_rel(version_number) / "visual"
    if not vis_dir.is_dir():
        return None
    index = vis_dir / "index.html"
    if index.is_file():
        chosen = index
    else:
        htmls = sorted(
            (p for p in vis_dir.glob("*.html") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not htmls:
            return None
        chosen = htmls[0]
    return chosen.relative_to(project_root).as_posix()


def read_vizual_mockup(db: Session, version_id: uuid.UUID) -> Optional[str]:
    """Return the version's Vizuál mockup HTML text, or ``None`` when there is none (the public
    ``/{version_id}/vizual-mockup`` route serves this into the cockpit iframe). Path-scoped to the version's
    ``visual/`` dir (defense-in-depth ``relative_to`` check) — never an arbitrary file read."""
    slug = _project_slug_for_version(db, version_id)
    version_number = db.execute(select(Version.version_number).where(Version.id == version_id)).scalar_one()
    project_root = claude_agent.PROJECTS_ROOT / slug
    rel = _vizual_mockup_rel(project_root, version_number)
    if rel is None:
        return None
    path = (project_root / rel).resolve()
    vis_dir = (project_root / _version_spec_rel(version_number) / "visual").resolve()
    try:
        path.relative_to(vis_dir)
    except ValueError:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def has_vizual_mockup(db: Session, version_id: uuid.UUID) -> bool:
    """True iff a Vizuál mockup HTML exists for this version (lightweight — dir stat, no file read). The board
    prefers the mockup preview route over any recorded live-sandbox URL when this is True (Director
    2026-07-17)."""
    slug = _project_slug_for_version(db, version_id)
    version_number = db.execute(select(Version.version_number).where(Version.id == version_id)).scalar_one()
    return _vizual_mockup_rel(claude_agent.PROJECTS_ROOT / slug, version_number) is not None


async def _run_vizual_round(
    db: Session,
    state: PipelineState,
    *,
    on_event: Optional[claude_agent.EventCallback] = None,
    directive: Optional[str] = None,
    on_message: Optional[MessageCallback] = None,
) -> PipelineState:
    """The Vizuál phase — the LIVE-preview VISUAL-CONSULTATION round (CR-1, nex-studio-visual; spec §3.A/§3.B/§3.C).

    Brings the project's frontend up LIVE in an isolated Vite dev-server sandbox so the Manažér can WALK the
    running app in the cockpit and approve it before Programovanie, and — when the Manažér asks for a change —
    DISPATCHES the AI Agent to apply it to the live FE (HMR reflects it in the preview, spec §1). The stage
    NEVER advances here: the loop always hands the turn back to the Manažér; only a ``schvalit`` action moves
    ``vizual → programovanie``.

    Flow:
      1. Resolve the project slug.
      2. :func:`vizual_sandbox.spin_up` (idempotent — safe to call every turn) → the public preview URL.
         Wrapped in try/except so a sandbox failure NEVER crashes the pipeline: it settles ``blocked`` /
         ``system_error`` with a plain-Slovak note instead.
      3. Record ONE ``system → manazer`` preview-URL notification — but only on FIRST entry
         (:func:`_vizual_url_recorded` is False), so the change-request loop does not re-spam the URL.
      4a. ``directive`` is None (a FRESH entry into the stage): settle ``awaiting_manazer`` — the Manažér
          reviews the vizual and either asks for a change (relayed back as the next turn's ``directive``) or
          advances with ``schvalit`` (the ``vizual → programovanie`` phase gate; dial-independent).
      4b. ``directive`` is set (a Manažér CHANGE-REQUEST): DISPATCH the AI Agent full-auto
          (:func:`invoke_agent_with_parse_retry`, :func:`_vizual_directive`) to apply the change to the FE +
          commit. On the parsed result, settle the way :func:`run_conversation_turn` does — a ``ParseFailure``
          → ``blocked`` / ``parse_exhaustion`` (readable note, never a crash); ``framework_issue`` → escalate
          to Dedo; a ``question`` / ``blocked`` → ``blocked`` / ``agent_question``; a normal reply →
          ``awaiting_manazer`` (the change is in the vizual — walk it, then ask for more or approve).

    The sole-mutator invariant holds: this runs inside the dispatch path, always a consequence of an action
    already routed through :func:`apply_action`.
    """
    version_id = state.version_id
    slug = _project_slug_for_version(db, version_id)
    version_number = db.execute(select(Version.version_number).where(Version.id == version_id)).scalar_one()
    mockup_rel = _vizual_mockup_rel(claude_agent.PROJECTS_ROOT / slug, version_number)

    # Director 2026-07-17: when the AI's self-contained mockup exists, the preview serves IT (walkable with NO
    # backend and NO login) via a same-origin backend route — NOT the auth-gated live FE Vite scaffold (a new
    # project's FE is behind ProtectedRoute→login and the sandbox has no backend/token → a dead login screen).
    url: str
    if mockup_rel is not None:
        url = f"/api/v1/pipeline/{version_id}/vizual-mockup"
    else:
        # Lazy, module-level-style reference (avoids an import cycle at orchestrator load; the ``docker``-heavy
        # sandbox module stays out of the hot import path). Referenced as ``vizual_sandbox.spin_up`` — a module
        # attribute, NOT ``from ... import spin_up`` — so tests can monkeypatch ``spin_up`` without real docker.
        from backend.services import vizual_sandbox

        try:
            url = vizual_sandbox.spin_up(slug)
        except Exception as exc:  # noqa: BLE001 — a sandbox failure must NEVER crash the pipeline; settle honestly.
            logger.exception("vizual sandbox spin_up failed for %s", slug)
            state.status = "blocked"
            state.block_reason = "system_error"  # R4 (D1): an engine-side step (the live preview) failed
            state.next_action = (
                "Živý náhľad sa nepodarilo spustiť — skús to znova (Uprav) alebo počkaj na technický tím."
            )
            err_msg = _record_message(
                db,
                version_id=version_id,
                stage="vizual",
                author="system",
                recipient="manazer",
                kind="notification",
                content="Živý náhľad projektu sa nepodarilo spustiť. Skús to znova alebo počkaj na technický tím.",
                payload={"phase": "vizual", "vizual_error": str(exc)},
            )
            if on_message is not None:
                await on_message(err_msg)
            db.flush()
            return state

    # Announce the preview URL ONCE — on the first entry into the stage (no prior vizual_url note on record).
    # The change-request loop re-enters this round every turn (spin_up is idempotent), so re-recording the URL
    # each time would spam the board; guard it on the durable-message probe instead.
    if not _vizual_url_recorded(db, version_id):
        ready_msg = _record_message(
            db,
            version_id=version_id,
            stage="vizual",
            author="system",
            recipient="manazer",
            kind="notification",
            content=f"Vizuál je pripravený — otvor si ho: {url}",
            payload={"phase": "vizual", "vizual_url": url},
        )
        if on_message is not None:
            await on_message(ready_msg)

    # FRESH entry (no change-request): hand the Manažér the live preview to WALK + approve (sub-task 3 behaviour).
    if directive is None:
        state.status = "awaiting_manazer"
        state.next_action = "Prezri si vizuál a keď sedí, schváľ (Hotovo/Schváliť)."
        db.flush()
        return state

    # A Manažér CHANGE-REQUEST → DISPATCH the AI full-auto to apply it to the live FE (allowed_tools=None → it
    # edits /opt/projects/<slug>/frontend, HMR reflects it, commits). Mirrors _dispatch_build_turn's use of the
    # SHARED invoke-with-parse-retry chokepoint (recipient=manazer, on_event/on_message threaded).
    result = await invoke_agent_with_parse_retry(
        db,
        version_id=version_id,
        role=AI_AGENT_ROLE,
        stage="vizual",
        prompt=_vizual_directive(db, version_id, directive, mockup_rel=mockup_rel),
        on_event=on_event,
        recipient="manazer",
        on_message=on_message,
    )

    if isinstance(result, ParseFailure):
        # The AI produced no parseable status block after the bounded retries → settle blocked with a readable
        # note (+ raw excerpt), exactly like run_conversation_turn — NEVER a crash, never an empty screen.
        state.status = "blocked"
        state.block_reason = "parse_exhaustion"
        state.next_action = "Blokované — AI nevrátil platný výstup. Napíš mu znova alebo upresni zmenu vo vizuáli."
        await _record_parse_exhaustion(
            db,
            state,
            stage="vizual",
            result=result,
            human_hint="Napíš mu znova alebo upresni, akú zmenu chceš vo vizuáli.",
            on_message=on_message,
        )
        db.flush()
        return state

    if result.kind == "framework_issue":
        # §15 escalation to Dedo: a problem the AI CANNOT fix without changing NEX Studio itself — settle
        # blocked/framework_issue + deliver the message to Dedo (no Manažér recovery actions).
        return await _settle_framework_issue(db, state, result, stage="vizual", on_message=on_message)

    if result.kind in ("question", "blocked"):
        # The AI asked the Manažér something → blocked on an agent_question so the board offers ``answer``.
        state.status = "blocked"
        state.block_reason = "agent_question"
        state.next_action = f"AI sa pýta: {result.question}"
        db.flush()
        return state

    # The AI applied the change (edited the FE + committed; HMR reflected it) → hand the turn BACK to the
    # Manažér. NEVER advance the stage here — only a ``schvalit`` action moves vizual → programovanie.
    state.status = "awaiting_manazer"
    state.next_action = "Zmena je vo vizuáli — pozri sa; napíš ďalšiu úpravu, alebo schváľ."
    db.flush()
    return state


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

    # Build the task plan HERE, at the START of Programovanie (nex-studio-visual, Director 2026-07-13) — NOT in
    # Návrh. The Vizuál step keeps refining the app after Návrh, so the plan is generated now, from the FINAL
    # design + the Manažér's Vizuál changes (the warm session carries them), never stale. Návrh emits the
    # design doc only; the FIRST build dispatch builds the EPIC→FEAT→TASK plan, then codes it. Idempotent: a
    # resumed / re-dispatched build finds the plan already materialized and skips straight to the task loop.
    # metrics_phase='navrh' keeps the planning effort accounted as design work even though it runs at build time.
    # ONLY the new_version flow: the fast_fix short path skips Návrh entirely and materializes its ONE Task from
    # the directive below (fast_fix.ensure_build_task) — it must never run the heavy EPIC→FEAT→TASK plan passes.
    if state.flow_type != "fast_fix" and not navrh_plan_materialized(db, version_id):
        plan_settled = await _generate_incremental_plan(
            db,
            state,
            stage="programovanie",
            on_event=on_event,
            directive=None,
            on_message=on_message,
            metrics_phase="navrh",
        )
        if plan_settled is not None:
            return plan_settled  # a plan-generation failure already settled (blocked / awaiting_manazer)

    # Cross-cutting invariants the AI Agent codified once in the Návrh gate_report (re-read each round, threaded
    # into every task brief).
    cross_cutting = _fetch_cross_cutting_rules(db, version_id)
    # The Manažér's framed return/answer (an ``uprav`` / ``answer`` re-dispatch) seeds attempt 1 of whichever
    # task runs first in THIS dispatch (the resumed task), then is consumed so later turns use generated briefs.
    pending_directive = directive
    # build-robustness-crash-handling.md Fix 2: auto-retry a CRASH (ClaudeAgentError — connection/decode,
    # usually transient) ONCE per dispatch before settling; a REAL timeout is never retried (re-running just
    # risks another 40-min wall). Bounded to ONE across the whole build round (all tasks/attempts) so a
    # persistent crash still surfaces, never an infinite retry loop.
    crash_retried = False
    # Verifikácia FAIL fix-loop (CR-V2-014; AUD-3): a re-gate re-entry (``is_regate`` set by the verdict FAIL
    # settle) re-runs the build against the Auditor's findings → the SALVAGED ``surgical_fix`` scope (the
    # Auditor FINDS, the AI Agent FIXES — independence). Thread the Auditor's latest Verifikácia FAIL
    # findings/proposed_fix as the first task's brief, so the AI Agent's re-run is targeted, not blind. A
    # Manažér directive (an explicit steer) takes precedence — it is the more specific instruction.
    if pending_directive is None and state.is_regate:
        pending_directive = _latest_verifikacia_fix_scope(db, version_id)
    # Consume the re-gate flag for THIS re-run: the fix scope is now threaded (or there was none on record).
    # A NEXT Verifikácia FAIL re-sets it (verdict settle); this prevents a stale flag from re-threading a
    # superseded fix scope on a later (e.g. Manažér-steered) Programovanie re-dispatch.
    if state.is_regate:
        state.is_regate = False
        db.flush()

    # Fast-fix short path (CR-V2-028; design §2.4/§2.5): the fast-fix lane skips the heavy Návrh phase
    # (FAST_FIX_STAGE_ORDER = priprava → programovanie → verifikacia → done), so NO task plan is
    # materialized upstream. Re-target ``fast_fix.ensure_build_task`` ONTO this v2 short path: the Manažér's
    # directive (carried in the kickoff payload) IS the brief, so materialize the ONE minimal Task here, at
    # the START of Programovanie, before the build loop reads ``get_next_todo_task`` (which would otherwise
    # see no task and falsely settle the phase as done with zero work). Idempotent — a Verifikácia FAIL
    # re-entry / a resumed dispatch reuses the existing Task (the v2 self-checking loop then re-runs it).
    if state.flow_type == "fast_fix":
        fast_fix.ensure_build_task(db, version_id)
        db.flush()

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

        # Token-stop poistka (spine STEP 1, REDESIGN §9 — "must ACTUALLY pause"): between tasks, honour the
        # GLOBAL ``programovanie_token_stop_millions`` cap. When set (>0) and this version's total spend has
        # crossed the cap — the append-only log IS the ledger (``aggregate_pipeline_usage``; NO new counter)
        # — PAUSE cooperatively HERE, exactly like a Manažér ``pause`` (apply_action :6506), resumed via the
        # existing ``pokracovat`` verb (the paused-state guard keeps ask/answer/schvalit out). Write ONE
        # system→manazer notification flagged ``token_stop=True`` so the board shows why AND the away-Manažér
        # Telegram nudge (``pipeline_runner._maybe_notify``) fires ONLY for this automatic pause, never a
        # manual one. 0 = non-stop → this whole block is a no-op (byte-identical pre-spine behaviour). The
        # notification payload carries no ``usage``/``timing``, so it never inflates the token ledger itself.
        limit_millions = system_setting_service.get_int(db, "programovanie_token_stop_millions")
        if limit_millions > 0:
            spent = aggregate_pipeline_usage(db, version_id).version
            tokens_spent = spent.input_tokens + spent.output_tokens
            if tokens_spent >= limit_millions * 1_000_000:
                state.status = "paused"
                state.next_action = (
                    f"Pozastavené — build prekročil token-limit ({limit_millions} mil.). "
                    "Skontroluj stav a pokračuj cez „Pokračovať“."
                )
                stop_msg = _record_message(
                    db,
                    version_id=version_id,
                    stage="programovanie",
                    author="system",
                    recipient="manazer",
                    kind="notification",
                    content=(
                        f"⏸️ Build pozastavený — prekročený token-limit "
                        f"({tokens_spent:,} tokenov ≥ {limit_millions} mil.). "
                        "Skontroluj stav token-limitu a rozhodni, či pokračovať."
                    ),
                    payload={
                        "phase": "programovanie",
                        "token_stop": True,
                        "tokens_spent": tokens_spent,
                        "limit_millions": limit_millions,
                    },
                )
                if on_message is not None:
                    await on_message(stop_msg)
                db.flush()
                return state

        task = task_service.get_next_todo_task(db, version_id)
        if task is None:
            # P0 (audit 2026-07-12): a build task that exhausted its auto-fix budget is left ``failed`` and is
            # SKIPPED by ``get_next_todo_task`` — so "no todo remains" does NOT mean the build succeeded. Gate
            # the completion on the deterministic open-findings count (``failed`` / stuck ``in_progress``): if
            # any remain, the build did NOT finish → settle blocked (agent_error) with an honest count and do
            # NOT record ``programming_complete`` / advance the phase, so nothing downstream reads a green
            # completion over dropped work. The manager's fix (Uprav / Pokračovať) resets ``failed``→``todo``
            # so the build re-runs them.
            open_findings = _build_open_findings(db, version_id)
            if open_findings > 0:
                state.status = "blocked"
                state.block_reason = "agent_error"
                state.next_action = (
                    f"{open_findings} úloh sa nepodarilo dokončiť — stavba ešte nie je hotová. "
                    "Napíš, čo treba opraviť (Uprav), a spustím stavbu znova."
                )
                db.flush()
                return state
            # STEP 4 (step4-programovanie-design.md MD-B): a CONVERSATION build's Programovanie has NO phase to
            # advance into — the build ran INSIDE the 1:1 rozhovor. SKIP the dial-settle entirely (no
            # ``_settle_phase_boundary``, no ``_next_stage``, no Auditor verdict — kontrola is STEP 5), RETURN
            # ``current_stage`` to the conversation register (``priprava``, so the next turn routes back to
            # ``run_conversation_turn``), settle ``awaiting_manazer`` and record ONE plain system→manazer
            # completion notification. The notification rides ``stage='programovanie'`` (its author is the
            # build loop — it brackets the build log with the ``▶ Úloha`` starts + task summaries, all
            # ``programovanie``); the ``current_stage`` column governs ROUTING, independent of where the event
            # is LOGGED. The LEGACY phase automaton (``mode`` NULL) keeps its dial-governed settle
            # BYTE-IDENTICAL below.
            if state.mode == "conversation":
                state.current_stage = "priprava"
                state.status = "awaiting_manazer"
                state.next_action = "Programovanie dokončené — pokračujeme v rozhovore."
                done_msg = _record_message(
                    db,
                    version_id=version_id,
                    stage="programovanie",
                    author="system",
                    recipient="manazer",
                    kind="notification",
                    content="Programovanie dokončené — pokračujeme v rozhovore.",
                    payload={"phase": "programovanie", "programming_complete": True},
                )
                if on_message is not None:
                    await on_message(done_msg)
                db.flush()
                return state
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
            result = await _dispatch_build_turn(
                db,
                version_id=version_id,
                task=task,
                attempt=attempt,
                prompt=prompt,
                mode=state.mode,
                on_event=on_event,
                on_message=on_message,
            )
            # build-robustness-crash-handling.md Fix 2: a CRASH (envelope-loss, kind='crash' — connection /
            # decode, NOT the wall-clock budget) is usually transient → re-invoke the SAME turn ONCE before
            # settling. Bounded to ONE crash retry per dispatch (``crash_retried``) so a persistent problem
            # still surfaces, never an infinite loop. A REAL timeout (kind='timeout') is NEVER retried — a
            # re-run just risks another 40-min wall (kept conservative). The build round is resume-safe, so a
            # retry re-runs the same task cleanly.
            if (
                isinstance(result, ParseFailure)
                and result.lost_work is not None
                and result.envelope_loss_kind == "crash"
                and not crash_retried
            ):
                crash_retried = True
                logger.warning(
                    "build turn crashed (envelope loss) for version=%s task=#%s — auto-retrying ONCE: %s",
                    version_id,
                    task.number,
                    result.reason,
                )
                result = await _dispatch_build_turn(
                    db,
                    version_id=version_id,
                    task=task,
                    attempt=attempt,
                    prompt=prompt,
                    mode=state.mode,
                    on_event=on_event,
                    on_message=on_message,
                )
            if isinstance(result, ParseFailure):
                if result.lost_work is not None:
                    # Lost-work audit (R-BLAST safeguard #3): the AI Agent's envelope was lost (timeout/crash)
                    # but the commit audit ran (inside invoke_agent). Work may have committed — surface "review
                    # & continue" DIRECTLY to the Manažér; the audit notification is already recorded. The task
                    # stays in_progress (reclaimed to todo on the next resume) — committed-but-lost work is
                    # surfaced, NEVER silently dropped or blindly redone.
                    state.status = "awaiting_manazer"
                    # Fix 3: route the honest, type-specific settle message from the exception TYPE — a real
                    # timeout and a (retried) crash must not share the misleading "Vypršal čas agenta". Fall
                    # back to the audit's own next_action for a legacy envelope-loss with no kind stamped.
                    kind = result.envelope_loss_kind
                    if kind in ("timeout", "crash"):
                        state.next_action = _envelope_loss_next_action(
                            kind, _timeout_for("programovanie"), result.log_path
                        )
                    else:
                        state.next_action = result.lost_work["next_action"]
                    db.flush()
                    return state
                # Audit P2 (2026-07-12): distinguish a system/tooling CRASH (an envelope loss with no dispatch
                # baseline → ``lost_work`` None but ``envelope_loss_kind`` still stamped) from a genuine
                # unparseable agent output. Labeling a crash "neplatný status blok" frames OUR tooling fault
                # as the agent's output problem.
                if result.envelope_loss_kind in ("timeout", "crash"):
                    prior_failures.append(f"systémová chyba ({result.envelope_loss_kind}): {result.reason}")
                else:
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
        # Spine STEP 1 (ADDITIVE mode toggle): an explicit ``mode='conversation'`` selects the non-phase
        # conversation loop (``run_conversation_turn``, routed by ``pipeline_runner._run``); anything else
        # (incl. absent) is NULL = the phase automaton (``run_dispatch``), so every existing new_version/
        # fast_fix start + every existing v2 PROD row is UNCHANGED. Same build shape either way
        # (current_stage='priprava' / actor='ai_agent' / status='agent_working') — only ``mode`` + a
        # conversation-appropriate next_action differ.
        mode = "conversation" if payload.get("mode") == "conversation" else None
        # The Manažér's directive rides in as the kickoff for BOTH the fast-fix lane AND the conversation
        # COLD-START (spine STEP 1 HOT-FIX — the FIRST message STARTS the rozhovor). Fast-fix (design §2.4):
        # the directive IS the whole brief — carried in BOTH the human-readable kickoff content (so it shows
        # on the board) and the payload (so the Programovanie round can seed the one Task from it). Conversation
        # cold-start: a freshly-created version has NO ``pipeline_state``, so nothing ever calls ``start`` — the
        # Manažér's FIRST Riadiace-centrum message does, carrying itself as the ``directive``; it becomes the
        # kickoff the partner reads first from the append-only log. ``None`` for a generic new_version → the
        # Príprava dialogue starts from the saved Zadanie.
        directive = payload.get("directive") if (flow_type == "fast_fix" or mode == "conversation") else None
        # Conversation cold-start normalization: an empty / whitespace-only first message is no directive at
        # all — the rozhovor still cold-starts, just with the generic kickoff. Fast-fix keeps its RAW directive
        # (byte-identical legacy behaviour — this normalization is the conversation branch ONLY).
        if mode == "conversation" and not (isinstance(directive, str) and directive.strip()):
            directive = None
        # "Spustiť tvorbu špecifikácie" (design §2.1): the kickoff message is recorded in the Príprava phase —
        # the first phase the AI Agent / partner enters. new_version → generic; fast_fix → the directive brief;
        # conversation → the Manažér's first message (when non-empty), else the generic kickoff. (For fast_fix
        # this is byte-identical to the old ``flow_type == "fast_fix" and directive`` gate.)
        kickoff_content = directive if directive else "Spustiť tvorbu špecifikácie."
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
            next_action=(
                "AI partner načítava kontext a začína rozhovor."
                if mode == "conversation"
                else "AI Agent pripravuje špecifikáciu."
            ),
            miera_autonomie=per_build_dial,
            mode=mode,
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
        # Spine STEP 2 (ADDITIVE): a conversation build (``mode='conversation'``) has NO Návrh phase to
        # advance into — approval FREEZES the on-disk Špecifikácia as the binding source of truth and settles
        # back to the Manažér; the rozhovor then continues (STEP 3 wires the task plan in). The legacy phase
        # automaton (``mode`` NULL) stays BYTE-IDENTICAL below.
        if state.mode == "conversation":
            rel, disk_status = _priprava_spec_disk_status(db, state)
            # A checkout that EXISTS but is missing specification.md is a real failure — there is nothing to
            # freeze. ``no_checkout`` (tests / library projects with no source_path) and ``ok`` both approve:
            # the spec is captured (on disk when a checkout exists, in the append-only log otherwise).
            if disk_status == "missing":
                raise OrchestratorError("Špecifikácia ešte nie je napísaná — nedá sa schváliť")
            _record_message(
                db,
                version_id=version_id,
                stage="priprava",
                author="manazer",
                recipient="ai_agent",
                kind="approval",
                content=payload.get("comment", "Špecifikácia schválená."),
                payload={
                    "phase": "priprava",
                    "approve_spec": True,
                    "mode": "conversation",
                    "spec_path": rel,
                },
            )
            # NO _next_stage / NO _begin_dispatch — the conversation does not walk the phase automaton; it
            # settles to the Manažér (awaiting_manazer) and continues as an ordinary 1:1 turn afterwards.
            state.status = "awaiting_manazer"
            state.next_action = "Špecifikácia schválená a zmrazená — pokračujeme v rozhovore."
            db.flush()
            return state
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

    if action == "zostav_plan":
        # STEP 3 (step3-plan-design.md MD-1=A): "Zostaviť plán" — the conversation build composes the task
        # plan FROM the approved Špecifikácia. AUTHORITATIVE gate (the board post-filter merely hides the
        # button): valid ONLY in a conversation build whose spec is approved and whose plan is not yet
        # materialized — a repeat is the Uprav/rebuild path, not a second first-build.
        if state.mode != "conversation":
            raise OrchestratorError("Zostaviť plán je platné len v rozhovorovom režime.")
        if not spec_approved(db, version_id):
            raise OrchestratorError("Zostaviť plán je platné až po schválení Špecifikácie.")
        if navrh_plan_materialized(db, version_id):
            raise OrchestratorError("Plán úloh už existuje — jeho úpravu rieš v rozhovore (Uprav).")
        # Durable, restart-safe trigger (FIX3): record a manazer→ai_agent kind='directive' marker carrying
        # payload.compose_plan. ``run_conversation_turn`` delegates to the plan round SOLELY on this DB marker
        # — the in-memory dispatch directive is None for ``zostav_plan`` (directive_for_action) and is lost on
        # a restart, so it must never be the trigger. The marker rides the ``priprava`` conversation stage.
        _record_message(
            db,
            version_id=version_id,
            stage="priprava",
            author="manazer",
            recipient="ai_agent",
            kind="directive",
            content="Zostav plán úloh zo schválenej Špecifikácie.",
            payload={"phase": "priprava", "compose_plan": True},
        )
        _begin_dispatch(db, state)
        return state

    if action == "spustit_stavbu":
        # STEP 4 (step4-programovanie-design.md MD-A=A): "Spustiť stavbu" — the conversation build starts
        # programming the materialized plan. AUTHORITATIVE gate (the board post-filter merely hides the
        # button): valid ONLY in a conversation build whose spec is approved, whose plan is materialized, and
        # whose build has NOT yet started (a re-click after the build began is the Pokračovať/Uprav path).
        if state.mode != "conversation":
            raise OrchestratorError("Spustiť stavbu je platné len v rozhovorovom režime.")
        if not spec_approved(db, version_id):
            raise OrchestratorError("Spustiť stavbu je platné až po schválení Špecifikácie.")
        if not navrh_plan_materialized(db, version_id):
            raise OrchestratorError("Spustiť stavbu je platné až po zostavení plánu úloh.")
        if _build_started(db, version_id):
            raise OrchestratorError("Stavba už beží alebo je dokončená — pokračuj cez „Pokračovať v stavbe“.")
        # Durable audit breadcrumb (MINOR — NOT the trigger): a manazer→ai_agent kind='directive' start_build
        # marker for the audit trail. The ACTUAL trigger + restart-safety is the durable current_stage=
        # 'programovanie' + _begin_dispatch (the runner routes on STAGE via run_dispatch→_run_build_round);
        # NOTHING reads this marker (_run_build_round starts from get_next_todo_task). Rides the programovanie
        # stage it kicks off — the same "record the kickoff at the phase being entered" shape as ``start``.
        _record_message(
            db,
            version_id=version_id,
            stage="programovanie",
            author="manazer",
            recipient="ai_agent",
            kind="directive",
            content="Spustiť stavbu — naprogramuj plán úloh úlohu po úlohe.",
            payload={"phase": "programovanie", "start_build": True},
        )
        # MOVE the phase (mode STAYS 'conversation'): the runner then routes this build through run_dispatch →
        # _run_build_round (the EXISTING self-checking loop, UNCHANGED) because current_stage == 'programovanie'.
        state.current_stage = "programovanie"
        db.flush()
        _begin_dispatch(db, state)
        return state

    if action == "spustit_vizual":
        # CR-1 (nex-studio-visual): "Spustiť vizuál" — the conversation build ENTERS the Vizuál stage: the AI
        # spins up the live FE preview sandbox and the Manažér WALKS the running app, asking for changes the AI
        # applies live (HMR) before committing to the build. AUTHORITATIVE gate (the board post-filter merely
        # hides the button): valid ONLY in a conversation build whose spec is approved, whose plan is
        # materialized, whose build has NOT yet started (mirrors ``spustit_stavbu`` — Vizuál precedes the build),
        # and which is NOT already IN the Vizuál stage (a re-click there is a no-op — change-requests flow through
        # the chat relay into ``_run_vizual_round``, not this action).
        if state.mode != "conversation":
            raise OrchestratorError("Spustiť vizuál je platné len v rozhovorovom režime.")
        if not spec_approved(db, version_id):
            raise OrchestratorError("Spustiť vizuál je platné až po schválení Špecifikácie.")
        if not navrh_plan_materialized(db, version_id):
            raise OrchestratorError("Spustiť vizuál je platné až po zostavení plánu úloh.")
        if _build_started(db, version_id):
            raise OrchestratorError("Stavba už beží alebo je dokončená — vizuál sa spúšťa pred stavbou.")
        if state.current_stage == "vizual":
            raise OrchestratorError("Vizuál už beží — zmeny píš do rozhovoru, alebo pokračuj cez „Spustiť stavbu“.")
        # Durable audit breadcrumb (MINOR — NOT the trigger): a manazer→ai_agent kind='directive' marker for the
        # audit trail. The ACTUAL trigger + restart-safety is the durable current_stage='vizual' + _begin_dispatch
        # (the runner routes a conversation build at ``vizual`` through run_dispatch → _run_vizual_round). NO
        # directive is set here on purpose: the FRESH-entry ``_run_vizual_round(directive=None)`` just spins up the
        # live preview and hands the Manažér the URL — the manager's LATER relayed chat messages carry the
        # change-requests (each drains as a ``directive`` into ``_run_vizual_round``). Rides the vizual stage it
        # kicks off — the same "record the kickoff at the phase being entered" shape as ``spustit_stavbu``.
        _record_message(
            db,
            version_id=version_id,
            stage="vizual",
            author="manazer",
            recipient="ai_agent",
            kind="directive",
            content="Spustiť vizuál — priprav živý náhľad frontendu na prechádzku.",
            payload={"phase": "vizual", "start_vizual": True},
        )
        # MOVE the phase (mode STAYS 'conversation'): the runner then routes this build through run_dispatch →
        # _run_vizual_round (fresh-entry preview spin-up, directive None) because current_stage == 'vizual'.
        state.current_stage = "vizual"
        db.flush()
        _begin_dispatch(db, state)
        return state

    if action == "skontrolovat":
        # STEP 5 (step5-kontrola-design.md K-1=A): "Skontrolovať" — the conversation build runs the partner's
        # HONEST self-check of its OWN Programovanie output. MIRRORS ``zostav_plan`` (it STAYS at
        # ``current_stage='priprava'`` — NOT ``spustit_stavbu`` which MOVES the stage): the round is INVISIBLE
        # to the release/deploy path (a verdict at ``verifikacia`` reads as a release PASS to _verifikacia_passed
        # / version_verified / deploy.list_verified_versions — kontrola must NEVER touch that path). AUTHORITATIVE
        # gate (the board post-filter merely hides the button): valid ONLY in a conversation build whose spec is
        # approved, whose Programovanie has COMPLETED, and whose latest completed build has NOT yet been checked
        # (a repeat is refused — one kontrola per completed build, K-4; a new build/fix re-opens it).
        if state.mode != "conversation":
            raise OrchestratorError("Skontrolovať je platné len v rozhovorovom režime.")
        if not spec_approved(db, version_id):
            raise OrchestratorError("Skontrolovať je platné až po schválení Špecifikácie.")
        if not programming_complete(db, version_id):
            raise OrchestratorError("Skontrolovať je platné až po dokončení Programovania.")
        if kontrola_done(db, version_id):
            raise OrchestratorError("Kontrola pre túto stavbu už prebehla — nová stavba/oprava ju znovu otvorí.")
        # Durable, restart-safe trigger (mirror of the compose_plan marker, FIX3): record a manazer→ai_agent
        # kind='directive' marker carrying payload.check. ``run_conversation_turn`` delegates to the kontrola
        # round SOLELY on this DB marker — the in-memory dispatch directive is None for ``skontrolovat`` and is
        # lost on a restart, so it must never be the trigger. The marker rides the ``priprava`` conversation
        # stage (kontrola LIVES in the conversation register — current_stage STAYS 'priprava' throughout).
        _record_message(
            db,
            version_id=version_id,
            stage="priprava",
            author="manazer",
            recipient="ai_agent",
            kind="directive",
            content="Skontroluj vlastnú robotu po Programovaní — čestne, čo je PEVNÉ a čo VRATKÉ.",
            payload={"phase": "priprava", "check": True},
        )
        # NO _next_stage / NO stage move — kontrola never walks the phase automaton. _begin_dispatch arms the
        # priprava actor (ai_agent) as agent_working; the runner routes it through run_conversation_turn (stage
        # stays 'priprava'), which delegates to _run_conversation_kontrola_round on the check marker.
        _begin_dispatch(db, state)
        return state

    if action == "hotovo":
        # STEP 6 (step6-hotovo-design.md MD-1=A): "Označiť ako hotové" — the Manažér's TERMINAL sign-off on a
        # conversation build. Unlike the legacy Auditor path (a ``verdict`` PASS at Verifikácia signed off via
        # ``schvalit``), a conversation build reaches DEPLOYABILITY through THIS manager signature: it settles
        # the build to the terminal ``done`` (verified) stage and records a SHA-anchored marker that
        # ``version_verified`` honours (the signature REPLACES a verdict — no verdict is resurrected). AUTHORITATIVE
        # gate (the board post-filter merely hides the button): valid ONLY in a conversation build whose spec is
        # approved, whose Kontrola has run for the latest build, and which is NOT already ``done`` (a re-sign is
        # refused — the terminal state itself blocks it, MD-2; a new build/fix re-opens Kontrola → Hotovo).
        if state.mode != "conversation":
            raise OrchestratorError("Označiť ako hotové je platné len v rozhovorovom režime.")
        if not spec_approved(db, version_id):
            raise OrchestratorError("Označiť ako hotové je platné až po schválení Špecifikácie.")
        if not kontrola_done(db, version_id):
            raise OrchestratorError("Označiť ako hotové je platné až po Kontrole.")
        if kontrola_floor_red(db, version_id):
            # K-3: Kontrola never signs off on a red build. The pass-blind ``kontrola_done`` is True even on a
            # red floor (the partner always emits its honest gate_report); the runtime-floor gate is what keeps
            # a non-booting build off the deployable ``done`` state.
            raise OrchestratorError(
                "Označiť ako hotové sa nedá — beh appky je červený (appka nenaštartovala alebo akceptačný "
                "beh neprešiel). Oprav to a spusti Kontrolu znova."
            )
        if state.current_stage == "done":
            raise OrchestratorError("Verzia je už hotová.")
        # SHA-anchor the manager signature to the exact code state + settle terminal ``done`` — the SAME ladder
        # as the verdict path (a later HEAD move past the signed commit AUTO-UN-VERIFIES → ``hotovo_drift``).
        # Shared with the drifted-version auto re-anchor (audit #8) via ``_apply_hotovo_signoff``: the recorded
        # marker is kind='notification' at stage='priprava' (INVISIBLE to ``_verifikacia_passed``); no
        # ``_begin_dispatch`` — a pure terminal signature, the partner never self-signs.
        _apply_hotovo_signoff(db, version_id, state)
        return state

    if action == "schvalit":
        # STEP 4 (step4-programovanie-design.md MAJOR): a CONVERSATION build NEVER walks the phase automaton —
        # after Programovanie it returns to the rozhovor (MD-B completion tail), and kontrola is the separate
        # STEP 5. So ``schvalit`` (the legacy phase-gate sign-off) is INVALID for a conversation build; raise
        # here BEFORE the legacy stage-guard below. Without this belt, a settled conversation Programovanie
        # (``current_stage='programovanie'``) would accept ``schvalit`` and _next_stage it into the Auditor's
        # Verifikácia — corrupting the conversation build into the phase automaton. The board post-filter drops
        # ``schvalit`` for conversation too (two-layer belt, mirroring ``zostav_plan``).
        if state.mode == "conversation":
            raise OrchestratorError(
                "Schváliť fázu nie je v rozhovorovom režime — po programovaní pokračujeme v rozhovore."
            )
        # "Schváliť" — the Manažér ratifies the current phase's output at a dial-governed schvaľovací bod
        # (after Návrh / Programovanie / Verifikácia) → advance to the next phase / Hotovo. The dial decides
        # whether the build STOPPED here for the Manažér at all; once it has, this signs it off.
        if state.current_stage not in ("navrh", "vizual", "programovanie", "verifikacia"):
            raise OrchestratorError(
                "Schváliť je platné len na schvaľovacom bode (Návrh / Vizuál / Programovanie / Verifikácia)"
            )
        # NOTE (nex-studio-visual, Director 2026-07-13): the old "never advance out of Návrh with an EMPTY plan"
        # gate is GONE — the task plan is no longer built in Návrh (it is built at Programovanie start, from the
        # final design + Vizuál changes; see :func:`_run_build_round`). So approving Návrh with no plan is now
        # the normal path. The empty-plan safety moved WITH the plan: :func:`_run_build_round` blocks on a
        # plan-generation failure, so Programovanie can never silently run with nothing to build.
        # no-silent-done-without-verification (R-BLAST safeguard #5, v2 form): the build may reach Hotovo
        # ONLY through a recorded Auditor PASS verdict at Verifikácia — never a silent sign-off. (v1's
        # "no-silent-done-without-UAT" gate is superseded: deploy is OUT of the pipeline — per-customer,
        # D6/OQ-3 — so Hotovo means "verified", not "deployed". The verification invariant is preserved.)
        if state.current_stage == "verifikacia" and not _verifikacia_passed(db, version_id):
            raise OrchestratorError(
                "Hotovo nedovolené: Auditor ešte nevydal PASS vo Verifikácii — najprv over verdiktom PASS."
            )
        # Audit P2 (2026-07-12): a recorded PASS is not enough — it must still bind the CURRENT code. If HEAD
        # moved past the verified commit before sign-off, schvalit would advance to ``done`` against code the
        # PASS never covered. Gate on LIVE verification too; "Over znova" re-anchors a drifted PASS.
        if state.current_stage == "verifikacia" and not version_verified(db, version_id)[0]:
            raise OrchestratorError(
                "Hotovo nedovolené: overenie je zastarané — kód sa pohol za overený commit. "
                "Najprv spusti Over znova, potom schváľ."
            )
        # #3 (Director 2026-07-13): the Vizuál round no longer commits each change (the AI just writes the FE —
        # HMR reflects it live), so squash the whole visual session into ONE commit now, at approval, before
        # advancing vizual → programovanie. Best-effort; a no-op when nothing changed.
        if state.current_stage == "vizual":
            _commit_vizual_changes(claude_agent.PROJECTS_ROOT / _project_slug_for_version(db, version_id))
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
        # CR-V2-054: an 'Uprav' at Verifikácia is a FIX directive → route it to the AI Agent (the fixer),
        # NOT state.current_actor (the Auditor/finder — which would just re-confirm). Re-enters the bounded
        # fix loop with the operator's comment as the brief. (The was-the-bug: the NEX Agents dogfood 'Uprav'
        # hit the Auditor and re-passed.)
        if state.current_stage == "verifikacia":
            return await _route_manazer_fix_to_ai_agent(db, state, comment=str(comment))
        # P0 (audit 2026-07-12): a Programovanie build re-dispatched by the manager's fix must RE-RUN its
        # ``failed`` tasks — otherwise ``get_next_todo_task`` skips them and the fix silently drops the work
        # (the same open-findings gate that now blocks completion). Reset failed→todo before the loop resumes.
        if state.current_stage == "programovanie":
            _reset_failed_tasks_to_todo(db, version_id)
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

    if action == "overit_znovu":
        # CR-V2-057 + audit #8: "Over znova" — re-verify a DRIFTED version against the CURRENT code. Two shapes,
        # both fail-closed on a re-read HEAD (a stale / forged board can't force a re-run with nothing to verify)
        # and both valid from a SETTLED state only (``done`` / ``awaiting_manazer`` — never mid-turn):
        #   * ``sha_drift`` — a phase build's Auditor Verifikácia PASS whose HEAD moved on → re-enter Verifikácia
        #     and re-run the INDEPENDENT Auditor against HEAD (:func:`_run_verifikacia_round`); the fresh verdict
        #     re-anchors (PASS bound to the new commit) or re-gates (FAIL → one targeted fix). Never re-stamps.
        #   * ``hotovo_drift`` — a CONVERSATION build's manager Hotovo signature whose HEAD moved on → re-run the
        #     partner's honest self-check against HEAD; on a GREEN runtime floor it AUTO re-anchors the Hotovo
        #     signature to the new commit in ONE click (Director 2026-07-12), on RED it stays re-opened for the
        #     manager to fix. The Auditor is NEVER routed for a conversation build (it has no Verifikácia phase).
        if state.status not in ("done", "awaiting_manazer"):
            raise OrchestratorError("Over znova je platné len na ustálenej verzii (Hotovo alebo čaká na Manažéra).")
        _, provenance = version_verified(db, version_id)
        if provenance == "sha_drift":
            state.current_stage = "verifikacia"
            state.is_regate = True
            state.iteration += 1
            db.flush()
            # _begin_dispatch re-points the actor to the Auditor (STAGE_ACTOR['verifikacia']), flips to
            # agent_working, and re-captures the dispatch baseline from the current HEAD → the background turn
            # routes to _run_verifikacia_round (a fresh, independent Auditor + smoke against HEAD).
            _begin_dispatch(db, state)
            return state
        if provenance == "hotovo_drift":
            # Re-open the conversation build's self-check against HEAD. current_stage returns to the conversation
            # register ('priprava'); the durable check marker carries ``auto_hotovo`` so the kontrola completion
            # tail re-signs Hotovo on a GREEN runtime floor (else it settles re-opened for the manager). Mirrors
            # the ``skontrolovat`` trigger (durable manazer→ai_agent kind='directive' payload.check) — the round
            # is driven SOLELY by this DB marker (restart-safe), never the in-memory dispatch directive.
            state.current_stage = "priprava"
            _record_message(
                db,
                version_id=version_id,
                stage="priprava",
                author="manazer",
                recipient="ai_agent",
                kind="directive",
                content=(
                    "Over znova po zmene kódu — čestne prekontroluj vlastnú robotu; ak je beh appky v poriadku, "
                    "verzia sa znovu označí ako hotová."
                ),
                payload={"phase": "priprava", "check": True, "auto_hotovo": True},
            )
            _begin_dispatch(db, state)
            return state
        raise OrchestratorError("Over znova je platné len keď je overenie zastarané (kód sa pohol za overený commit).")

    if action == "nahlasit_znova":
        # Audit P0: the manager's ONE action on a ``framework_issue`` block (a NEX-Studio-side bug only our
        # technical team can clear) — RE-SEND the escalation. Re-reads the recorded escalation message and
        # re-delivers it best-effort, then records a fresh "re-reported" notification so the manager sees it
        # went out again (a concrete button + a live timestamp instead of a locked, jargon-named dead-end).
        if not (state.status == "blocked" and state.block_reason == "framework_issue"):
            raise OrchestratorError("Nahlásiť znova je platné len keď je projekt zablokovaný na chybe NEX Studia.")
        fw = db.execute(
            select(PipelineMessage)
            .where(
                PipelineMessage.version_id == version_id,
                PipelineMessage.payload["framework_issue"].astext == "true",
            )
            .order_by(PipelineMessage.seq.desc())
            .limit(1)
        ).scalar_one_or_none()
        dedo_message = ((fw.payload or {}).get("dedo_message") if fw else "") or ""
        slug = _project_slug_for_version(db, version_id)
        version_number = db.execute(select(Version.version_number).where(Version.id == version_id)).scalar_one()
        await dedo_escalation.escalate_to_dedo(
            project_slug=slug,
            version_number=version_number,
            dedo_message=dedo_message,
            context=f"Projekt: {slug} · Verzia: v{version_number} · Opätovné nahlásenie (Manažér).",
            owner_chat_id=_owner_chat_id_for_version(db, version_id),
        )
        _record_message(
            db,
            version_id=version_id,
            stage=state.current_stage,
            author="system",
            recipient="manazer",
            kind="notification",
            content="Chybu sme znova nahlásili nášmu technickému tímu.",
            payload={"phase": state.current_stage, "framework_issue": True, "dedo_message": dedo_message},
        )
        db.flush()
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

    if action == "decide":
        # CR-V2-041: the Manažér picks an option for ONE consultation decision (a Decision Card). Record it
        # (durable kind=answer with payload.consultation_decision); if more decisions remain → RE-BLOCK
        # decision_needed WITHOUT dispatching (pure DB — the route only dispatches on agent_working, so zero
        # tokens per intermediate click); only the LAST decide re-dispatches the AI Agent to apply ALL the
        # decisions (dispatch_directive aggregates them from the recorded answers).
        if not (state.status == "blocked" and state.block_reason == "decision_needed"):
            raise OrchestratorError("decide je platné len počas konzultácie (decision_needed)")
        lc = _latest_consultation(db, version_id)
        if lc is None:
            raise OrchestratorError("Žiadna aktívna konzultácia.")
        c, c_seq = lc
        decision_key = payload.get("decision_key")
        decision = next((d for d in c.get("decisions", []) if d.get("key") == decision_key), None)
        if decision is None:
            raise OrchestratorError(f"Neznáme rozhodnutie {decision_key!r}.")
        option_id = payload.get("option_id")
        free_text = str(payload.get("free_text", "")).strip() or None
        if not option_id and not free_text:
            raise OrchestratorError("decide vyžaduje option_id alebo free_text")
        # CR-V2-058 security: honour ONLY an option the card actually OFFERED. The card-builder omits unsafe
        # options BY CONSTRUCTION (e.g. accept_fix without a positive fix_critique — §2). Without this check the
        # handler would blindly execute a FORGED/replayed option_id (accept_fix on a guide-only card → a
        # one-click UN-VETTED fix — the exact footgun the CR exists to prevent). Reject any option_id not among
        # this decision's offered options; the allow_free_text escape (option_id absent, free_text present)
        # stays valid. Hardens ALL consultation cards, not just verifikacia_fix.
        offered_ids = {o.get("id") for o in decision.get("options", [])}
        if option_id and option_id not in offered_ids:
            raise OrchestratorError(f"Neponúknutá možnosť {option_id!r} pre rozhodnutie {decision_key!r}.")
        label = free_text or next(
            (o.get("label") for o in decision.get("options", []) if o.get("id") == option_id), option_id
        )
        _record_message(
            db,
            version_id=version_id,
            stage=state.current_stage,
            author="manazer",
            recipient=state.current_actor,
            kind="answer",
            content=f"{decision.get('question', '')} → {label}",
            payload={
                "phase": state.current_stage,
                "consultation_decision": {
                    "consultation_id": c.get("id"),
                    "key": decision_key,
                    "option_id": option_id,
                    "free_text": free_text,
                    "label": label,
                    "note": (str(payload.get("note", "")).strip() or None),
                },
            },
        )
        keys = [d.get("key") for d in c.get("decisions", [])]
        answered = _consultation_answers(db, version_id, c_seq)
        if len(answered) < len(keys):
            # more decisions remain → re-block, NO dispatch (status stays blocked → the route won't dispatch)
            state.next_action = f"Manažér: rozhodni {len(answered) + 1}/{len(keys)} (konzultácia)."
            db.flush()
            return state
        # CR-V2-054: a verifikacia_fail escalation Decision Card routes to the AI-Agent FIX loop, NOT a
        # re-dispatch of the current actor (the Auditor). The operator's answer (free text preferred, else the
        # chosen option label) is the fix brief; a plain 'hold' keeps the build blocked (they can steer later
        # via 'Uprav').
        if c.get("source") == "verifikacia_fail":
            ans = answered.get("verifikacia_fail_next") or {}
            if ans.get("option_id") == "hold" and not ans.get("free_text"):
                state.next_action = "Podržané — usmerni opravu neskôr (Decision Card alebo 'Uprav')."
                db.flush()
                return state
            brief = (ans.get("free_text") or ans.get("label") or "Oprav blokujúce zlyhanie z Verifikácie.").strip()
            return await _route_manazer_fix_to_ai_agent(db, state, comment=brief)
        # CR-V2-058 Part A: the PER-FAIL Decision Card (distinct source key ``verifikacia_fix`` so it never
        # collides with the exhaustion ``verifikacia_fail`` handler above — self-audit found the collision on
        # the hardcoded next-key). Three vetted options resolved from the screen:
        #   * ``accept_fix`` — D6: resume the ALREADY-materialized fix task (Programovanie picks it up via
        #     ``get_next_todo_task``) — NO second task, NO second iteration bump (both happened in the settle).
        #     Only offered when the fix was positively vetted (:func:`_build_fix_consultation` invariant).
        #   * ``guide`` — route the operator's own fix brief to the AI Agent (the fixer), resetting the loop.
        #   * ``hold`` — re-block WITHOUT consuming the card: the card stays the action surface (no dead-end).
        if c.get("source") == "verifikacia_fix":
            ans = answered.get("verifikacia_fix_next") or {}
            opt = ans.get("option_id")
            if opt == "hold" and not ans.get("free_text"):
                state.next_action = (
                    "Podržané — rozhodni neskôr (Decision Card): spusti opravu, usmerni ju, alebo podrž."
                )
                db.flush()
                return state
            if opt == "accept_fix" and not ans.get("free_text"):
                # resume the already-materialized (and critic-vetted) fix task — the settle set stage=
                # programovanie / actor=ai_agent and bumped the counter; _begin_dispatch just flips to working.
                _begin_dispatch(db, state)
                return state
            # ``guide`` (or an ``accept_fix`` the Manažér amended with a free-text steer) → route the operator's
            # brief to the AI Agent (fixer). _route_manazer_fix_to_ai_agent resets the bounded loop (human steers).
            brief = (ans.get("free_text") or ans.get("label") or "Oprav blokujúce zlyhanie z Verifikácie.").strip()
            return await _route_manazer_fix_to_ai_agent(db, state, comment=brief)
        # all decided → APPLY: re-dispatch the AI Agent (dispatch_directive frames every captured decision)
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
        # CR-V2-050: the fail-closed runtime floor overrides a manual PASS-override too — recompute it from the
        # recorded release evidence and RECORD the EFFECTIVE verdict (a floored PASS becomes FAIL) so the canonical
        # kind=verdict message the fix-loop reads (:func:`_latest_verifikacia_fix_scope`) can never say PASS while
        # the settle takes the FAIL branch.
        floor_red = _latest_runtime_floor_red(db, version_id)
        effective_verdict = "FAIL" if (verdict == "PASS" and floor_red) else verdict
        # CR-V2-056 (layer-1): bind a manual PASS to the verified commit + tag it (same as the autonomous path).
        verified_sha: Optional[str] = None
        if effective_verdict == "PASS":
            _proj_root = claude_agent.PROJECTS_ROOT / _project_slug_for_version(db, version_id)
            # Part 1 (per-app-changelog-standard.md §1): (re)generate + commit the user-facing RELEASE_NOTES.md
            # into the app repo BEFORE anchoring the verified SHA, so the note rides the verified/tagged commit.
            _vnum = db.execute(select(Version.version_number).where(Version.id == version_id)).scalar_one()
            _commit_release_note(db, version_id, _proj_root, _vnum)
            verified_sha = _repo_head(_proj_root)
            if verified_sha:
                _git_tag_version(_proj_root, _vnum, verified_sha)
        verdict_payload: dict[str, Any] = {"verdict": effective_verdict, "phase": "verifikacia"}
        if verified_sha:
            verdict_payload["verified_sha"] = verified_sha
        if effective_verdict != verdict:
            verdict_payload["engine_override"] = "runtime_floor_red"
            verdict_payload["findings"] = [
                "ENGINE OVERRIDE (CR-V2-050): a red release smoke/acceptance floored the Manažér's PASS to FAIL."
            ]
        _record_message(
            db,
            version_id=version_id,
            stage="verifikacia",
            author="auditor",
            recipient="manazer",
            kind="verdict",
            content=effective_verdict,
            payload=verdict_payload,
        )
        # Apply the verdict via the SHARED settle (CR-V2-014) so the MANUAL path here and the AUTONOMOUS path
        # (:func:`_run_verifikacia_round`) can never diverge: PASS → settle for the dial-governed end sign-off
        # (no-silent-done invariant); FAIL → bounded fix↔re-verify loop (reset done tasks + re-enter
        # Programovanie with the Auditor's fix scope threaded, bounded by :data:`AUDITOR_LOOP_MAX`, then
        # escalate). The ``kind=verdict`` message above is the canonical record both gates read.
        return await _settle_verifikacia_verdict(db, state, verdict=effective_verdict, runtime_floor_red=floor_red)

    if action == "pokracovat":
        # Resume a Programovanie loop the Manažér paused (cooperative pause boundary) — no comment, no phase
        # change: just re-dispatch the loop (it re-picks the next todo task). The record is Manažér→AI Agent
        # (direct comms). Only valid in Programovanie (the only phase with a pause boundary).
        if state.current_stage != "programovanie":
            raise OrchestratorError("Pokračovať je platné len vo fáze Programovanie")
        # P0 (audit 2026-07-12): resuming the build also re-runs any ``failed`` tasks (else they stay skipped
        # and the build silently completes over dropped work — mirrors the ``uprav`` reset).
        _reset_failed_tasks_to_todo(db, version_id)
        _record_message(
            db,
            version_id=version_id,
            stage="programovanie",
            author="manazer",
            recipient="ai_agent",
            kind="approval",
            content="Stavba pokračuje.",
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
