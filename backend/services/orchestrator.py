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
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from pathlib import Path
from time import perf_counter
from typing import Any, Optional

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
from backend.services import claude_agent, fast_fix
from backend.services import epic as epic_service
from backend.services import feat as feat_service
from backend.services import task as task_service
from backend.services.claude_agent import ClaudeAgentError, invoke_claude
from backend.services.pipeline_status import ParseFailure, PipelineStatusBlock, parse_status_block

logger = logging.getLogger(__name__)

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
    result: "tuple[str, Optional[claude_agent.UsageMetadata]] | str",
) -> "tuple[str, Optional[claude_agent.UsageMetadata]]":
    """Normalise :func:`invoke_claude`'s return to ``(text, usage)``.

    Since WS-D (CR-NS-036) ``invoke_claude`` returns ``(text, usage)``, but unit-test doubles that
    monkeypatch ``orchestrator.invoke_claude`` still return a bare ``str`` — tolerate both so the
    engine works under test without forcing every fake to mint usage."""
    if isinstance(result, tuple):
        return result[0], result[1]
    return result, None


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


# Ordered stages and the agent responsible for each (F-007 §3.1).
STAGE_ORDER: tuple[str, ...] = (
    "kickoff",
    "gate_a",
    "gate_b",
    "gate_c",
    "gate_d",
    "gate_e",
    "task_plan",
    "build",
    "gate_g",
    "release",
    "done",
)
# Fast-Fix Lane stage path (F-009, CR-NS-094): the lightweight lane skips the full waterfall
# (gate_a-e / task_plan / gate_g). ``kickoff`` advances straight to ``build`` (after the Coordinator's
# escalation-guard triage), and a settled ``build`` advances to ``release`` — never to a gate. A subset
# of :data:`STAGE_ORDER`, so every member reuses the same :data:`STAGE_ACTOR` mapping below.
FAST_FIX_STAGE_ORDER: tuple[str, ...] = (
    "kickoff",
    "build",
    "release",
    "done",
)
STAGE_ACTOR: dict[str, str] = {
    "kickoff": "coordinator",
    "gate_a": "designer",
    "gate_b": "designer",
    "gate_c": "designer",
    "gate_d": "designer",
    "gate_e": "customer",
    "task_plan": "designer",
    "build": "implementer",
    "gate_g": "auditor",
    "release": "coordinator",
}
_VERIFY_RETRIES = 2
# gate_g FAIL flow Fix 1 (CR-NS-056 §F1.5): a scope/design question escalates to the Director at most ONCE
# per gate_g iteration. A 2nd scope flag in the same iteration settles to awaiting_director (no loop).
_MAX_SCOPE_ESCALATIONS_PER_ITERATION = 1
# Per-task auto-fix bound (F-007 §6, CR-NS-020 CR-3): on a failed task the build loop
# re-dispatches the Programmer with escalating context up to this many times; after the
# last failure the task is marked ``failed`` and the pipeline HALTs for the Director.
# Distinct from ``_VERIFY_RETRIES`` (a within-turn verify retry) and ``_PARSE_RETRIES``.
_AUTO_FIX_RETRIES = 5
# Bounded re-invokes when the agent emits an unparseable <<<PIPELINE_STATUS>>>
# block (CR-NS-018). A single LLM JSON typo must not halt the pipeline; the
# agent runs ``--resume`` so a retry is a cheap re-emit, not a redo of the work.
# Distinct from ``_VERIFY_RETRIES`` (which retries a *valid* report that failed
# verification).
_PARSE_RETRIES = 2
_ACTIONS = frozenset(
    {
        "start",
        "approve",
        "return",
        "ask",
        "answer",
        "apply_coordinator_recommendation",
        "fix",
        "leave",
        "verdict",
        "uat_accept",
        "end_gate_e",
        "end_build",
        "continue_build",
        "accept_merged",
        "pause",
    }
)
# Actions that act on / advance past an agent's output — only valid once the
# agent has settled (CR-NS-018). Guarding these stops a stale board / double-click
# from advancing while the agent is mid-work (which skipped a mandatory gate).
_ADVANCING_ACTIONS = frozenset(
    {
        "approve",
        "apply_coordinator_recommendation",
        "fix",
        "leave",
        "verdict",
        "uat_accept",
        "return",
        "end_gate_e",
        "end_build",
        "continue_build",
        "accept_merged",
    }
)

# Per-stage backstop timeouts (seconds) for a single headless agent turn
# (CR-NS-018 fix-round). Dispatch is async, so these only guard a *hung* agent.
# Build is the heaviest single turn; gates/kickoff are read+produce. Unknown
# stages fall back to the env-tunable ``claude_agent.CLAUDE_INVOKE_TIMEOUT``.
STAGE_TIMEOUT: dict[str, int] = {
    "kickoff": 900,
    "gate_a": 900,
    "gate_b": 900,
    "gate_c": 900,
    "gate_d": 900,
    "gate_e": 900,
    "task_plan": 1200,
    "build": 2400,
    "gate_g": 1200,
    "release": 900,
}


def _timeout_for(stage: str) -> int:
    return STAGE_TIMEOUT.get(stage, claude_agent.CLAUDE_INVOKE_TIMEOUT)


def determine_available_actions(state: PipelineState) -> set[str]:
    """The Director actions valid to OFFER right now, derived from (current_stage, status) — WS-C1
    (CR-NS-030). The single backend source of truth for button presence, so the FE can't drift into
    no-op buttons (the live bug: an "approve" rendered on a build-blocked task, where it is a no-op).

    This is the (stage, status)-level offerable set — a subset of what :func:`apply_action` accepts.
    Finer payload/DB preconditions stay in apply_action and are refined by the FE's message-derived
    signals: a non-empty comment (return), all-tasks-done (approve@build), no open finding
    (end_build / end_gate_e / final approve@gate_e), an open Designer gap (fix/leave), a Coordinator
    report (apply_coordinator_recommendation). This set only removes the GROSS (stage, status)
    mismatches; the FE intersects it with those finer conditions and falls back to its own logic when
    the field is absent."""
    stage, status = state.current_stage, state.status

    if status == "agent_working":
        # Nothing to ratify while the agent works; only a build loop has a cooperative pause boundary.
        return {"pause"} if stage == "build" else set()
    if status == "done":
        return set()
    if status == "paused":
        # CR-NS-027: from a paused build, ONLY the resume pair.
        return {"continue_build", "end_build"}

    # Settled (awaiting_director / blocked): ask + return are universally valid (return has no stage
    # guard in apply_action — it's also the error-block "Skús znova" recovery at any stage).
    actions: set[str] = {"ask", "return"}
    if status == "blocked":
        actions.add("answer")  # a blocked state is an agent question — the Director can answer it

    if stage in ("kickoff", "gate_a", "gate_b", "gate_c", "gate_d", "task_plan"):
        actions.update({"approve", "apply_coordinator_recommendation"})
    elif stage == "gate_e":
        actions.update({"approve", "fix", "leave", "end_gate_e"})
    elif stage == "build":
        actions.update({"continue_build", "end_build"})
        # apply_coordinator_recommendation (E7, F-008 §9): the Director approves the Coordinator's
        # proposal → the orchestrator executes the matching action. Offered at a settled build; the FE
        # refines to "only when an EXECUTABLE coordinator_directive exists" (message-derived) and labels
        # the button from proposed_action — so it never shows without a live proposal.
        actions.add("apply_coordinator_recommendation")
        if status == "awaiting_director":
            actions.add("approve")  # final sign-off only at a settled build — never on a blocked task
            # accept_merged (WS-B2, CR-NS-031): a merged task dead-ends at a HALT, which settles to
            # awaiting_director (never blocked — a blocked build is a programmer QUESTION, with no failed
            # task to recognize). The FE further refines to "only when an open finding exists" via
            # build_open_findings, so it never shows on a clean build.
            actions.add("accept_merged")
    elif stage == "gate_g":
        actions.add("verdict")
    elif stage == "release":
        actions.add("uat_accept")

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
    exact behavior, ``scalar``-safe, never crashes) — EXCEPT the **Coordinator effort defaults to
    ``max``** (Director-approved Effort policy 2026-06-13: the one operator/judgment role, differentiated
    up; it does not participate in Dual-Build, so non-deterministic depth is fine, and its output stays a
    Director-gated proposal). Re-resolved on every :func:`invoke_agent` call, so parse-retries keep it.
    """
    row = db.execute(
        select(UserAgentSettings.model, UserAgentSettings.effort)
        .join(Project, Project.owner_id == UserAgentSettings.user_id)
        .join(Version, Version.project_id == Project.id)
        .where(Version.id == version_id, UserAgentSettings.agent_role == role)
    ).first()
    model = row.model if row is not None else None
    effort = row.effort if row is not None else None
    if effort is None and role == "coordinator":
        effort = "max"
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
            "Ukonči odpoveď strojovým <<<PIPELINE_STATUS>>> blokom (F-007 §7.2)."
        )
    base = (
        f"Pokračuj fázou '{stage}' podľa autoritatívneho spec balíka a svojho charteru. "
        "Ukonči odpoveď strojovým <<<PIPELINE_STATUS>>> blokom (F-007 §7.2)."
    )
    if stage == "task_plan":
        # E5 (CR-NS-045): the per-task human-effort estimate is the metrics page's human-baseline source.
        base += (
            " Pri KAŽDEJ úlohe (TASK) uveď pole `estimated_minutes` = realistický odhad práce pre "
            "schopného ĽUDSKÉHO vývojára v minútach (NIE čas AI výpočtu); pri každom FEAT-e uveď "
            "`estimated_minutes` ako súčet jeho úloh. Je to ADVISORY pole — chýbajúci odhad je povolený "
            "a NIKDY neblokuje build."
        )
    return base


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
    """Frame the Director's interactive message for the re-dispatch prompt, else ``None``.

    For ``return`` / ``ask`` / ``answer`` the Director's content MUST reach the
    agent (CR-NS-018) — otherwise the re-dispatched agent re-runs blind on the
    generic stage directive ("nič sa nezmenilo, nemám čo prerábať"). For a
    fresh-stage dispatch (``start`` / ``approve`` / ``verdict``) there is no
    Director-specific instruction → ``None``, and the caller falls back to
    :func:`_directive_for`. The agent runs ``--resume`` (full thread), so the
    framed line lands in the right context.
    """
    if action == "return":
        comment = str(payload.get("comment", "")).strip()
        return f"Director ťa vrátil na opravu fázy '{stage}': {comment}" if comment else None
    if action == "ask":
        text = str(payload.get("text", "")).strip()
        return f"Director sa pýta: {text}" if text else None
    if action == "answer":
        text = str(payload.get("text", "")).strip()
        return f"Director odpovedal na tvoju otázku: {text}" if text else None
    return None


def latest_coordinator_report(db: Session, version_id: uuid.UUID) -> Optional[str]:
    """Content of the most recent Coordinator ``gate_report`` for a version, or ``None``.

    Author-filtered (``coordinator`` + ``gate_report``) and ordered by the
    monotonic ``seq`` (not ``created_at``, which ties within a transaction), so
    the most recent Coordinator report is unambiguous. Feeds the
    "Schváliť návrh Koordinátora" action (``apply_coordinator_recommendation``):
    its content becomes the re-dispatch directive so the Director accepts the
    Coordinator's recommended fix without retyping it.
    """
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


def _write_task_plan(db: Session, state: PipelineState, block: PipelineStatusBlock) -> Optional[str]:
    """Materialize the Designer's task_plan decomposition into Epic/Feat/Task rows.

    F-007 §5 / CR-NS-020 CR-2. The deterministic mechanical gate for the task_plan
    stage (replaces the disk-deliverable ``verify_mechanical`` — the plan's deliverable
    is DB rows, not files). Returns a failure reason (→ ``status=blocked``, nothing
    written) or ``None`` on success.

    **Idempotent replace + atomic:** a Director ``return`` re-dispatches the Designer,
    which re-runs this; we drop the version's existing epics first (FK cascade →
    feats/tasks) so a re-plan never duplicates. The whole replace runs in a SAVEPOINT —
    any failure rolls back the rows while the caller still records ``blocked`` (never a
    half-written plan). Numbers are service-assigned (MAX+1); status is forced
    (planned/todo — the Designer never pre-marks done); ``baseline_sha`` /
    ``task_count`` / ``auto_fix_count`` stay untouched (CR-3 owns them).
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
                        module_id=epic_in.module_id,
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

    _record_message(
        db,
        version_id=state.version_id,
        stage="task_plan",
        author="system",
        recipient="director",
        kind="notification",
        content=f"Plán úloh zapísaný: {n_epics} epicov, {n_feats} featov, {n_tasks} taskov.",
        payload={"task_plan_summary": {"epics": n_epics, "feats": n_feats, "tasks": n_tasks}},
    )
    return None


def dispatch_directive(
    db: Session, version_id: uuid.UUID, action: str, payload: dict[str, Any], stage: str
) -> Optional[str]:
    """Resolve the re-dispatch prompt for an ``agent_working`` transition, else ``None``.

    Single entry point for the route (CR-NS-018): payload-framed for
    ``return`` / ``ask`` / ``answer`` (delegates to :func:`directive_for_action`),
    DB-fetched + framed for ``apply_coordinator_recommendation``, ``None`` for a
    fresh-stage dispatch (``start`` / ``approve`` / ``verdict``).
    """
    if action == "apply_coordinator_recommendation":
        content = latest_coordinator_report(db, version_id)
        if content is None:
            return None
        return f"Director schválil odporúčania Koordinátora. Zapracuj ich podľa jeho hlásenia: {content}"
    # Gate E (F-007-gate-e §5): symmetric relay — the continue-directive to the Customer
    # MUST carry the Designer's reply, else the Customer (separate session) re-asks and
    # logs a false open finding. A final approve has already advanced past gate_e
    # (→ task_plan), so stage != gate_e and this does not fire.
    if action == "leave" and stage == "gate_e":
        return (
            "Director rozhodol nález ponechať (podľa odporúčania Koordinátora). "
            "Pokračuj ďalšou otázkou previerky Gate E. Ukonči <<<PIPELINE_STATUS>>> blokom (§7.2)."
        )
    if action == "approve" and stage == "gate_e":
        milestone = _latest_gate_e_milestone(db, version_id)
        if milestone is not None and milestone.author == "designer":  # per-question (Branch A)
            return (
                f"Návrhár odpovedal na tvoju otázku: «{milestone.content}». Director to schválil. "
                "Pokračuj ďalšou otázkou previerky Gate E. Ukonči <<<PIPELINE_STATUS>>> blokom (§7.2)."
            )
        # topic boundary (latest = Customer gate_report, or none) — no stale answer
        return (
            "Director schválil — pokračuj v previerke Gate E ďalším okruhom "
            "(alebo ďalšou otázkou). Ukonči <<<PIPELINE_STATUS>>> blokom (§7.2)."
        )
    # Director ↔ Coordinator only (§2): ask / return @ gate_e are Coordinator-relayed —
    # the Coordinator revises its recommendation (NOT a message to the Customer/Designer).
    if action == "ask" and stage == "gate_e":
        text = str(payload.get("text", "")).strip()
        return (
            f"Director konzultuje s Koordinátorom: {text}. Prepracuj svoje odporúčanie. "
            "Ukonči <<<PIPELINE_STATUS>>> blokom (§7.2)."
        )
    if action == "return" and stage == "gate_e":
        comment = str(payload.get("comment", "")).strip()
        return (
            f"Director vrátil (cez Koordinátora): {comment}. Prepracuj svoje odporúčanie. "
            "Ukonči <<<PIPELINE_STATUS>>> blokom (§7.2)."
        )
    # Branch B fix: "Schváliť návrh Koordinátora" → the edit instruction is the Coordinator's
    # LATEST (possibly consult-revised) recommendation — Coordinator-relayed to the Designer
    # (§2). The Designer's stale ``proposed_fix`` is NOT mixed in (it can contradict a revised
    # recommendation — e.g. proposed 6 cols, revised to 7).
    if action == "fix" and stage == "gate_e":
        recommendation = _latest_coordinator_message_content(db, version_id) or "(bez poznámky)"
        return (
            "Koordinátor odovzdáva Directorom schválené odporúčanie na zapracovanie: "
            f"{recommendation}. Uprav návrh podľa neho. Toto je vykonanie schválenej opravy — "
            "NEhodnoť nové medzery (gap_found nech ostane false). Ukonči <<<PIPELINE_STATUS>>> blokom (§7.2)."
        )
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
    recipient: str = "director",
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
    the next in the chain (default ``"director"``; the gate_e round passes
    ``designer`` / ``coordinator`` per Z→N→K→D). System escalations stay → Director.

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
    # CR-NS-040 (E3(b/c)): per-dispatch model/effort from the project owner's config. Resolved here (not
    # in the parse-retry wrapper) so EVERY dispatch — including each parse-retry, which re-enters
    # invoke_agent — applies the owner's config; unset → no flags (today's behavior).
    model_override, effort_override = _resolve_dispatch_overrides(db, version_id, role)
    charter_path: Optional[Path] = None
    if is_first:
        charter_path = claude_agent.PROJECTS_ROOT / slug / ".claude" / "agents" / role / "CLAUDE.md"

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
        text, usage = _split_claude_result(
            await invoke_claude(
                project_slug=slug,
                claude_session_id=session_id,
                prompt=prompt,
                charter_path=charter_path,
                timeout=timeout if timeout is not None else _timeout_for(stage),
                on_event=tagged_on_event,
                model=model_override,
                effort=effort_override,
            )
        )
    except ClaudeAgentError as exc:
        # A failed invocation still burned wall-clock (and counts as an attempt) — record it so the
        # turn's timing/parse_attempts reflect retries; no usage (no envelope was returned) (WS-D).
        turn_metrics.record(None, perf_counter() - _started)
        # Return the failure SILENTLY (CR-NS-022 §2 — no raw system→director dump here). The
        # caller decides if/how it reaches the Director: invoke_agent_with_parse_retry relays the
        # FINAL unrecovered failure via the Coordinator in plain Slovak; internal direct callers
        # (auditor / coordinator-judge) fold it into their own handling. Suppresses the leak where
        # an intermediate parse-retry later succeeds.
        return ParseFailure(
            f"claude invocation failed: {exc}",
            usage=turn_metrics.usage_payload(),
            timing=turn_metrics.timing_payload(),
        )
    turn_metrics.record(usage, perf_counter() - _started)
    stdout = text

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
            "deliverables": parsed.deliverables,
            "commits": parsed.commits,
            "question": parsed.question,
            "awaiting": parsed.awaiting,
            "block_kind": parsed.kind,
            # Gate E signals (F-007-gate-e) — let apply_action/the FE derive the
            # boundary type (topic vs final), the open-finding gate, and Branch A/B.
            "topic": parsed.topic,
            "topic_done": parsed.topic_done,
            "coverage_complete": parsed.coverage_complete,
            "findings": parsed.findings,
            "gap_found": parsed.gap_found,
            "proposed_fix": parsed.proposed_fix,
            # task_plan decomposition (F-007 §4/§5, CR-NS-020 CR-2). Persisted so the
            # audit trail / TaskPlanPanel can show the plan and CR-3 can re-read the
            # cross-cutting rules from this gate_report payload.
            # mode="json" so a TaskPlanEpic.module_id UUID (CR-NS-022) serializes to a str for JSONB.
            "plan": parsed.plan.model_dump(mode="json") if parsed.plan is not None else None,
            "cross_cutting_rules": parsed.cross_cutting_rules,
            # Per-task Auditor verdict (F-007 §6, CR-NS-020 CR-4) — persisted for CR-5's
            # per-task audit panel (the diff + findings the Director can drill into).
            "task_pass": parsed.task_pass,
            # Structured Coordinator proposal (F-008 §2 A1, E7) — persisted so apply_coordinator_
            # recommendation can read + execute it and the FE can show + label the proposal.
            "coordinator_directive": (
                parsed.coordinator_directive.model_dump(mode="json")
                if parsed.coordinator_directive is not None
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
    on_event: Optional[claude_agent.EventCallback] = None,
    recipient: str = "director",
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
            prompt=(
                f"Tvoj <<<PIPELINE_STATUS>>> blok sa nepodarilo spracovať: {result.reason}. "
                "Najčastejšia príčina je neescapovaná úvodzovka v textovom poli (summary/question/findings) — "
                "v JSON reťazcoch píš slovenské úvodzovky kučeravé (znaky „ a “) alebo ich escapuj spätným lomítkom; "
                "rovná úvodzovka (U+0022) v texte predčasne ukončí reťazec a rozbije celý blok. "
                "Pošli LEN opravený, platný <<<PIPELINE_STATUS>>> blok — rovnaký obsah, správna JSON syntax aj schéma."
            ),
            recipient=recipient,
            on_message=on_message,
            extra_payload=extra_payload,
            metrics=turn_metrics,
        )
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
            "payloade, nie v slovenskom texte). Ukonči <<<PIPELINE_STATUS>>> blokom (§7.2)."
        ),
        on_message=on_message,
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
            payload=_failure_metrics_payload(failed) or None,
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
            "vysvetli zrozumiteľnou rečou, ŠTRUKTÚROVANE (krátke odseky, **tučné** zvýraznenie "
            "podstatného — nie monolitný jednofarebný blok): (1) čo sa stalo, (2) čo je ďalší krok / čo "
            "od Directora treba, (3) riziká alebo poznámky. Ukonči <<<PIPELINE_STATUS>>> blokom (§7.2)."
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
) -> tuple[Optional[str], Optional[dict[str, Any]]]:
    """Verify a gate_report before awaiting the Director. ``(reason, directive)``: reason on FAIL else None;
    the judge's ``coordinator_directive`` (dict) on a blocked verdict so the caller can classify scope vs
    mechanical (CR-NS-056 §F1.1). Mirrors ``_coordinator_relay``'s ``(text, directive)`` contract.

    Mechanical checks first (deterministic); then a judgment check by invoking
    the coordinator agent. The coordinator's block must report ``kind != blocked``
    and ``awaiting='director'`` to count as a PASS. The Coordinator's judgment is a
    real dispatch-path message → ``on_message`` streams it live (CR-NS-018).
    """
    slug = _project_slug_for_version(db, version_id)
    mech = verify_mechanical(slug, block)
    if mech is not None:
        return mech, None

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

    judgment = await invoke_agent(
        db,
        version_id=version_id,
        role="coordinator",
        stage=block.stage,
        prompt=(
            f"Verifikuj DONE report fázy '{block.stage}': spec compliance + žiadny "
            "claim bez authoritative source (P-2). "
            + prior_scope_block
            # E7 (F-008 §3, CR-NS-033): if you flag a problem, triage it + append a structured directive.
            + "Ak nájdeš problém, klasifikuj ho (triage podľa charteru §7.1) a popri slovenskom relayi "
            "pripoj štruktúrovaný `coordinator_directive` (triage_class, proposed_action, target, params, "
            "rationale, úprimná confidence) — pričom `target` musí byť OBJEKT {task_id?, role?, commit?} "
            "alebo úplne vynechaný, NIKDY nie voľný text. Ukonči <<<PIPELINE_STATUS>>> blokom (§7.2)."
        ),
        on_message=on_message,
    )
    if isinstance(judgment, ParseFailure):
        # WS-E (CR-NS-037): the verify-judge turn exhausted parse-retries → no message recorded. Make
        # it visible + count its tokens; the caller still treats the non-None reason as a verify FAIL
        # (control flow unchanged).
        await _record_internal_turn_parse_failure(
            db,
            version_id,
            block.stage,
            turn_label="Overenie DONE reportu Koordinátorom",
            failed=judgment,
            on_message=on_message,
        )
        return f"coordinator verify unparseable: {judgment.reason}", None
    if judgment.kind == "blocked":
        # §F1.1 (CR-NS-056): plumb the judge's directive out so the caller classifies scope vs mechanical.
        directive = (
            judgment.coordinator_directive.model_dump(mode="json")
            if judgment.coordinator_directive is not None
            else None
        )
        return f"coordinator flagged: {judgment.question or judgment.summary}", directive
    return None, None


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
            "(proposed_action + úprimná confidence); Director ho schváli a engine vykoná. "
            "Ukonči <<<PIPELINE_STATUS>>> blokom (§7.2)."
        ),
        on_message=on_message,
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
    state.current_actor = actor
    state.status = "agent_working"
    state.next_action = f"Agent '{actor}' pracuje na fáze '{stage}'."
    db.flush()


# Fast-Fix UAT auto-deploy (F-009, CR-NS-098). The lane redeploys UAT via the EXISTING tool
# ``scripts/uat-deploy.py`` (redeploy-safe by default: preserves secrets, owns the version). The script
# lives in THIS repo's tree; the backend image does not COPY scripts/, but ``/opt/projects`` is mounted
# (with /var/run/docker.sock + /opt/uat), so it is reachable at the mounted repo path. Module-level so a
# test can monkeypatch the path; the timeout is a backstop over the script's own waits (~1–2 min build).
UAT_DEPLOY_SCRIPT: Path = claude_agent.PROJECTS_ROOT / "nex-studio" / "scripts" / "uat-deploy.py"
UAT_DEPLOY_TIMEOUT = 900


async def _run_uat_deploy(project_slug: str, uat_slug: str) -> tuple[bool, str]:
    """Run ``scripts/uat-deploy.py <uat_slug> --project <project_slug>`` as an async subprocess.

    Returns ``(ok, detail)``: ``ok`` is True on exit 0; ``detail`` is ``"OK"`` on success, else a short
    tail of the combined output (the deploy error to surface to the Director). Never raises — a spawn
    failure / timeout becomes ``(False, reason)`` so the caller settles to ``blocked`` rather than hanging.
    Async (``create_subprocess_exec`` + ``await``) so the ~1–2 min docker build never blocks the event loop.
    """
    cmd = ["python3", str(UAT_DEPLOY_SCRIPT), uat_slug, "--project", project_slug]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
    except OSError as exc:
        return False, f"deploy sa nepodarilo spustiť: {exc}"
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=UAT_DEPLOY_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        return False, f"deploy prekročil časový limit ({UAT_DEPLOY_TIMEOUT}s)"
    if proc.returncode == 0:
        return True, "OK"
    tail = (stdout or b"").decode("utf-8", "replace").strip()[-300:]
    return False, (f"exit {proc.returncode}: {tail}" if tail else f"exit {proc.returncode}")


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
    """Run the working agent for a version and settle its status (background).

    ``on_message`` (CR-NS-018) is the incremental-broadcast hook: it fires right after
    each dispatch-path message is recorded so the runner commits + streams it live,
    instead of batching at round end. Threaded into EVERY message-recording invoke site
    reachable from here (the worker turn, the Coordinator relay, the verify judgment +
    retries) — the end-of-run batch is dropped, so a missed thread = a lost message.

    ``gate_e_dispatch`` selects the Gate E sub-flow (F-007-gate-e §2/§5):
    ``"designer_edit"`` (Branch B ``fix`` — Coordinator-relayed edit, Designer edits
    then the round continues to the next Customer question), ``"coordinator_consult"``
    (``ask`` / ``return`` @ gate_e — the Coordinator revises its recommendation; the
    Director never addresses the Customer/Designer directly), or ``None``.

    Second half of the old ``_dispatch``: reloads the (already ``agent_working``)
    state, invokes the actor headless, and settles ``status`` to ``blocked`` or
    ``awaiting_director``. Runs in :mod:`backend.services.pipeline_runner`'s
    background task against a fresh session — never inside the request. Returns
    the settled state (``None`` if the version/state vanished).

    ``on_event`` (CR-NS-018) streams the **primary** agent's activity; the
    secondary verify/retry invocations don't stream (short, secondary).

    ``directive`` (CR-NS-018) is the Director's framed message for ``return`` /
    ``ask`` / ``answer`` re-dispatch (see :func:`directive_for_action`). When
    present it IS the agent's prompt; otherwise the generic
    :func:`_directive_for` is used (fresh-stage ``start`` / ``approve`` /
    ``verdict``). Threading it here is what makes the Director↔agent loop
    two-way: without it the agent re-runs blind on the generic directive.
    """
    state = _get_state(db, version_id)
    if state is None:
        return None
    stage = state.current_stage
    actor = state.current_actor
    if STAGE_ACTOR.get(stage) is None:  # terminal — nothing to run.
        return state

    # Gate E (F-007-gate-e revised §2): per-question, Director-gated Customer↔Designer
    # exchange — one Q&A then STOP. Not a single generic agent turn.
    if stage == "gate_e":
        return await _run_gate_e_round(
            db, state, on_event=on_event, directive=directive, gate_e_dispatch=gate_e_dispatch, on_message=on_message
        )

    # Build (F-007 §6, CR-NS-020 CR-3): the continuous per-task loop — dispatches the
    # Programmer task-by-task with mechanical verify + auto-fix, not a single opaque turn.
    if stage == "build":
        # E7 route_to_designer (F-008 §10, CR-NS-034): a Designer spec-fix turn is pending mid-build —
        # run it instead of the Programmer loop; it resets the held task + re-enters the loop on DONE.
        if state.returns_to == "build":
            return await _run_designer_spec_fix(db, state, on_event=on_event, on_message=on_message)
        return await _run_build_round(db, state, on_event=on_event, directive=directive, on_message=on_message)

    # E2 (CR-NS-042): on the FRESH gate_a dispatch (directive is None), prepend the version's included
    # backlog items so the Designer authors them as the version's requirements (no-op for other stages /
    # no items). A Director return/ask (directive set) does NOT re-inject — once-only, same --resume thread.
    if directive is not None:
        prompt = directive
    else:
        prompt = _augment_brief_with_backlog(db, state.version_id, stage, _directive_for(stage, state.flow_type))
        # Fast-Fix Lane (F-009 §1, CR-NS-097): the fresh-session kickoff agent's only context is this brief —
        # prepend the Director directive so the escalation-guard triage acts on the ACTUAL fix, not blind.
        if stage == "kickoff" and state.flow_type == "fast_fix":
            prompt = _prepend_fast_fix_directive(db, state.version_id, prompt)
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
        # Parse-retries exhausted (CR-NS-022 §2): the Coordinator relays it to the Director in
        # plain Slovak; the board shows a plain next_action, never the raw parser error.
        await _coordinator_relay_engine_failure(
            db,
            version_id,
            stage,
            f"agent '{actor}' nevrátil platný výstup ani po opravách: {result.reason}",
            on_message,
            # WS-D (CR-NS-036): the worker produced no message — carry its lost tokens into the relay.
            failed=result,
        )
        state.status = "blocked"
        state.next_action = "Blokované — Koordinátor poslal Directorovi vysvetlenie a ďalší krok."
        db.flush()
        return state

    if result.kind in ("question", "blocked"):
        # Hub-and-spoke (CR-NS-018): a worker's question/blocked turn is reviewed
        # by the Coordinator first, who relays it to the Director. The Coordinator's
        # own question (kickoff) is surfaced directly — no double-review. On an
        # unparseable relay, fall back to the worker's question (never a dead-end).
        # Gate-level question (not the build loop) → relay + escalate, unchanged. The directive (2nd tuple
        # element) is for the build loop's autonomous recovery (Pillar B) — ignored here.
        relay_text = (await _coordinator_relay(db, state, result, on_message))[0] if actor != "coordinator" else None
        question_text = relay_text if relay_text is not None else result.question
        state.status = "blocked"
        state.next_action = f"Agent '{actor}' sa pýta: {question_text}"
        db.flush()
        return state

    if stage == "kickoff" and state.flow_type == "fast_fix":
        # Fast-Fix Lane (F-009 §2, CR-NS-097): a fast_fix kickoff that did NOT escalate (the non-trivial
        # case is the question/blocked branch above — convert-to-full-version proposal) is the Coordinator's
        # "trivial & clear" triage. The Director's submission IS the authorization, so AUTO-proceed to build
        # with NO awaiting_director gate. Mirror the approve(kickoff→build) path: advance + materialize the
        # single Task, then hand back agent_working so the runner runs the build round in THIS same
        # single-flight dispatch (a fresh schedule_dispatch would be skipped by the single-flight guard).
        state.current_stage = _next_stage("kickoff", state.flow_type)  # → build
        fast_fix.ensure_build_task(db, state.version_id)
        _begin_dispatch(db, state)  # status=agent_working at build → pipeline_runner continues the chain
        return state

    if stage == "release" and state.flow_type == "fast_fix":
        # Fast-Fix Lane release (F-009 §3, CR-NS-098): the release turn is the Coordinator's final verify.
        # A gate_report runs the verify-retry loop first (a real FAIL → blocked, NO deploy); a done/answer-
        # class turn is already the pass. On a PASS, AUTO-deploy the project's UAT so the Director SEES the
        # fix running on UAT before the single uat_accept, then settle (the auto-deploy sets status +
        # next_action: success → awaiting_director, failure → blocked, NULL uat_slug → skip + awaiting).
        # new_version / cr / bug never reach here (flow_type guard) — their release stays the generic
        # gate_report path below, byte-for-byte unchanged.
        if result.kind == "gate_report":
            reason, _is_scope = await _verify_with_retries(db, state, result, on_message=on_message)
            if reason is not None:
                state.status = "blocked"
                state.next_action = "Fáza 'release' neprešla overením — pozri správy Koordinátora a rozhodni."
                db.flush()
                return state
        await _fast_fix_auto_deploy(db, state, on_message=on_message)
        db.flush()
        return state

    if stage == "task_plan" and result.kind == "gate_report":
        # F-007 §5 / CR-NS-020 CR-2: the plan's mechanical gate is the deterministic
        # write-path (not the disk-deliverable verify_mechanical, nor a Coordinator judge
        # turn — the Director reviews the materialized tree himself, per Dedo 2026-06-07).
        reason = _write_task_plan(db, state, result)
        if reason is not None:
            # Plan write failed → blocked (CR-NS-022 §2): Coordinator relays it in plain Slovak.
            await _coordinator_relay_engine_failure(
                db, version_id, stage, f"plán úloh sa nepodarilo zapísať: {reason}", on_message
            )
            state.status = "blocked"
            state.next_action = "Plán úloh zamietnutý — Koordinátor poslal Directorovi vysvetlenie."
        else:
            # §A.2 site 1 (gate_report PASS — task_plan): Coordinator synthesis before settling.
            synthesis = await _coordinator_synthesis(db, state, trigger="plán úloh", on_message=on_message)
            state.status = "awaiting_director"
            state.next_action = synthesis or "Director: schváliť/vrátiť plán úloh."
        db.flush()
        return state

    if result.kind == "gate_report":
        reason, is_scope = await _verify_with_retries(db, state, result, on_message=on_message)
        if reason is not None and is_scope and state.current_stage == "gate_g":
            # §F1.4 (CR-NS-056): a gate_g SCOPE/DESIGN question — escalate ONCE per iteration, never loop it
            # against the Auditor. The cap counter INCLUDES this turn's just-recorded scope question (recorded
            # by invoke_agent inside verify_done BEFORE this caller), so the guard is <= (the current question
            # is the one allowed escalation): 1st flag count==1 (1<=1 escalate); 2nd flag count==2 (2<=1 cap).
            if _scope_escalations_this_iteration(db, state.version_id) <= _MAX_SCOPE_ESCALATIONS_PER_ITERATION:
                # Synthesis FIRST while current_actor is still 'auditor' (the §B guard lets it fire), THEN settle
                # blocked — current_actor STAYS auditor, current_stage STAYS gate_g (the scope question is on the
                # board as a coordinator→director message; answerable even if the synthesis ParseFails, per §F1.7).
                await _coordinator_synthesis(
                    db, state, trigger=f"fáza '{stage}' — otázka rozsahu", on_message=on_message
                )
                state.status = "blocked"
                state.next_action = (
                    "Audit položil otázku rozsahu — odpovedz (vysvetli) alebo rozhodni (PASS / FAIL → fáza)."
                )
            else:
                # 2nd scope flag this iteration (the Director already responded once) → do NOT loop; the
                # Director makes the definitive call (the FAIL→target verdict renders here — Fix 2).
                state.status = "awaiting_director"
                state.next_action = "Audit označil otázku rozsahu druhýkrát — rozhodni: PASS alebo FAIL → fáza."
        elif reason is not None:
            # Mechanical fail (or a scope flag at a non-gate_g gate — falls through to today's behavior).
            # The Coordinator already judged this (verify_done) — keep a plain next_action, no raw
            # reason on the board (CR-NS-022 §2 refinement: no technical dump reaches the Director).
            state.status = "blocked"
            state.next_action = f"Fáza '{stage}' neprešla overením — pozri správy Koordinátora a rozhodni."
        else:
            # §A.2 site 1 (gate_report PASS — gates A–D, release): Coordinator synthesis before settling.
            synthesis = await _coordinator_synthesis(db, state, trigger=f"fáza '{stage}'", on_message=on_message)
            state.status = "awaiting_director"
            state.next_action = synthesis or f"Director: schváliť/vrátiť fázu '{stage}'."
        db.flush()
        return state

    # kickoff / answer / done-class agent output → await the Director.
    # §A.2 site 4 (kickoff/answer/fallback completion): Coordinator synthesis before settling.
    synthesis = await _coordinator_synthesis(db, state, trigger=f"fáza '{stage}'", on_message=on_message)
    state.status = "awaiting_director"
    state.next_action = synthesis or f"Director: posúdiť výstup fázy '{stage}'."
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
            "Prekontroluj návrh a daj Directorovi odporúčanie (opraviť / ponechať + prečo). "
            "Ukonči <<<PIPELINE_STATUS>>> blokom (§7.2)."
        ),
        on_message=on_message,
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
            "Pokračuj ďalšou otázkou previerky Gate E. Ukonči <<<PIPELINE_STATUS>>> blokom (§7.2)."
        )
    else:
        customer_prompt = directive if directive is not None else _directive_for("gate_e")

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
        # §A.2 site 3 (Gate E topic boundary): Coordinator synthesis before settling.
        synthesis = await _coordinator_synthesis(
            db, state, trigger=f"okruh '{cust.topic or 'okruh'}'", on_message=on_message
        )
        state.status = "awaiting_director"
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
                "Ukonči <<<PIPELINE_STATUS>>> blokom (§7.2)."
            ),
            on_event=on_event,
            recipient="coordinator",  # N→K: the Designer's answer is for the Coordinator
            on_message=on_message,
        )
        if isinstance(designer, ParseFailure):
            return await _block_failed(state, db, designer.reason, failed=designer, on_message=on_message)
        state.status = "awaiting_director"
        if designer.gap_found:  # Branch B upward leg — Coordinator reviews before the Director
            await _coordinator_review_gap(db, state, designer, on_message)
            state.next_action = "Director: Návrhár našiel medzeru a navrhol opravu — rozhodni Opraviť/Ponechať."
        else:  # Branch A — routine answer
            state.next_action = "Director: posúď odpoveď Návrhára (schváliť → ďalšia otázka)."
        db.flush()
        return state

    # Unexpected Customer output → let the Director judge.
    state.status = "awaiting_director"
    state.next_action = "Director: posúď výstup fázy gate_e."
    db.flush()
    return state


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

    Every recorded turn here is a dispatch-path message → ``on_message`` streams each
    live (the Coordinator judgment via :func:`verify_done`, the system auto-return, and
    the worker's corrected report) so none is lost once the end batch is dropped."""
    reason, directive = await verify_done(db, state.version_id, block, on_message)
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
        retry = await invoke_agent(
            db,
            version_id=state.version_id,
            role=state.current_actor,
            stage=state.current_stage,
            prompt=f"Verify zlyhal: {reason}. Oprav a znovu ukonči <<<PIPELINE_STATUS>>> blokom (§7.2).",
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
        reason, directive = await verify_done(db, state.version_id, block, on_message)
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
    """How many autonomous Coordinator recoveries already happened for this task (Pillar B §B.4 cap) —
    counted from the recorded ``is_autonomous`` Coordinator→Director notes tagged with the task."""
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
    return sum(1 for p in rows if p and p.get("is_autonomous") and p.get("task_id") == str(task_id))


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


def recover_orphaned_builds_on_startup(db: Session) -> int:
    """On BE startup, recover BUILD pipelines stranded at ``agent_working`` by a restart
    (F-007 §7.3, CR-NS-021). Returns the number recovered.

    The build loop runs as a background dispatch; a backend restart kills it, leaving the
    pipeline stuck at ``build`` / ``agent_working`` with no auto-resume. This flips such rows
    to ``awaiting_director`` (+ a clear ``next_action``) and records a system→director
    ``notification`` so the Director can resume via "Pokračovať v builde" (``continue_build``)
    — whose ``_run_build_round`` already reclaims the orphaned ``in_progress`` task and re-runs
    it on its persisted ``baseline_sha``. Recovery ONLY flips state + notifies (the reclaim
    stays in the loop, DRY). **BUILD only** — non-build stages are short, Director-attended
    turns. ``Task.status`` is untouched, so the orphaned ``in_progress`` task stays counted by
    :func:`_build_open_findings` and ``approve`` stays blocked until ``continue_build`` runs.
    """
    rows = (
        db.execute(
            select(PipelineState).where(
                PipelineState.current_stage == "build",
                PipelineState.status == "agent_working",
            )
        )
        .scalars()
        .all()
    )
    for state in rows:
        state.status = "awaiting_director"
        state.next_action = "Build prerušený reštartom backendu — pokračuj cez 'Pokračovať v builde'."
        _record_message(
            db,
            version_id=state.version_id,
            stage="build",
            author="system",
            recipient="director",
            kind="notification",
            content=(
                "Build bol prerušený reštartom backendu — obnovený do stavu 'čaká na Directora'. "
                "Pokračuj cez 'Pokračovať v builde'."
            ),
        )
    db.commit()
    return len(rows)


def _fetch_cross_cutting_rules(db: Session, version_id: uuid.UUID) -> Optional[str]:
    """Re-read the cross-cutting regulated-ledger invariants the Designer codified once in
    the task_plan gate_report payload (CR-NS-020 CR-2). Injected into every per-task brief."""
    msg = db.execute(
        select(PipelineMessage)
        .where(
            PipelineMessage.version_id == version_id,
            PipelineMessage.stage == "task_plan",
            PipelineMessage.author == "designer",
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
    """Per-task brief for the Programmer (§6): one task, its description, the authoritative
    spec to consult, the cross-cutting block, and (on a retry) the prior attempts' reasons.

    ``flow_type='fast_fix'`` (F-009 §3, CR-NS-097): the Director directive (the task description) IS the
    authority — there is no spec section to study, and the Programmer must EXECUTE it directly rather than
    debate it on semantic/opinion grounds (the live run blocked asking "naozaj to chceš premenovať?")."""
    parts = [f"Programátor, postav JEDNU úlohu (TASK #{task.number}): {task.title}"]
    if task.description:
        parts.append(f"Popis úlohy: {task.description}")
    if flow_type == "fast_fix":
        parts.append(
            "RÝCHLA OPRAVA (fast-fix lane, F-009): pokyn Directora vyššie je AUTORITATÍVNY — VYKONAJ ho "
            "priamo. NESPOCHYBŇUJ ho z názorových / sémantických dôvodov (napr. „Firmy je správne, naozaj to "
            "chceš premenovať?“). ZASTAV (kind=blocked) IBA ak je to technicky nemožné, alebo naozaj nevieš "
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
    parts.append("Commitni zmeny a ukonči <<<PIPELINE_STATUS>>> blokom s commits[] + deliverables[] (§7.2).")
    return "\n\n".join(parts)


def _audit_prompt_for_task(task: Task, block: PipelineStatusBlock, cross_cutting_rules: Optional[str]) -> str:
    """Per-task Auditor brief (§6, CR-NS-020 CR-4): audit-vs-spec scoped to ONE task — its
    deliverables + the diff ``baseline_sha..HEAD`` + the relevant spec section + cross-cutting.
    Lighter than the release audit (the Dual-Build / Tibor audit stays at gate_g)."""
    parts = [f"Audítor, sprav audit-vs-spec JEDNEJ úlohy (TASK #{task.number}): {task.title}."]
    if task.description:
        parts.append(f"Popis úlohy: {task.description}")
    parts.append(f"Deliverables Programátora: {', '.join(block.deliverables) if block.deliverables else '(žiadne)'}.")
    if task.baseline_sha:
        parts.append(f"Audituj IBA túto úlohu — preskúmaj diff `{task.baseline_sha}..HEAD` (git), nie celý projekt.")
    parts.append(
        "Over: spec compliance deliverables voči relevantnej sekcii autoritatívneho špecu "
        "(docs/specs/), konzistenciu a dodržanie prierezových pravidiel."
    )
    if cross_cutting_rules:
        parts.append(f"Prierezové pravidlá (musia byť dodržané):\n{cross_cutting_rules}")
    parts.append("Ukonči <<<PIPELINE_STATUS>>> blokom: task_pass (true/false) + findings[] (čo treba opraviť). (§7.2)")
    return "\n\n".join(parts)


def _coordinator_verify_prompt_for_task(
    task: Task, block: PipelineStatusBlock, cross_cutting_rules: Optional[str]
) -> str:
    """Fast-Fix per-task verify brief for the COORDINATOR (F-009 §3, CR-NS-094): the independent
    verify of the single fast-fix Task — NO Auditor, NO Dual-Build. The Coordinator checks the
    Implementer's deliverables against the Director directive (the task brief) + P-2 (no claim without
    an authoritative source), scoped to the task diff, and emits the same ``task_pass`` + ``findings``
    contract the build loop's auto-fix already consumes (so the ≤5 bound / done-failed / HALT seam is
    untouched — only the verifying agent differs)."""
    parts = [f"Koordinátor, nezávisle over JEDNU rýchlu opravu (TASK #{task.number}): {task.title}."]
    if task.description:
        parts.append(f"Smernica Directora (zadanie úlohy): {task.description}")
    parts.append(f"Deliverables Implementéra: {', '.join(block.deliverables) if block.deliverables else '(žiadne)'}.")
    if task.baseline_sha:
        parts.append(f"Over IBA túto opravu — preskúmaj diff `{task.baseline_sha}..HEAD` (git), nie celý projekt.")
    parts.append(
        "Over: rieši zmena smernicu Directora, je konzistentná a bez claimu bez authoritative source "
        "(P-2)? Toto je rýchla oprava — žiadny plný Auditor, žiadny Dual-Build."
    )
    if cross_cutting_rules:
        parts.append(f"Prierezové pravidlá (musia byť dodržané):\n{cross_cutting_rules}")
    parts.append("Ukonči <<<PIPELINE_STATUS>>> blokom: task_pass (true/false) + findings[] (čo treba opraviť). (§7.2)")
    return "\n\n".join(parts)


async def _verify_task(
    db: Session,
    state: PipelineState,
    task: Task,
    block: PipelineStatusBlock,
    on_message: Optional[MessageCallback] = None,
) -> Optional[str]:
    """Per-task quality gate (§6). Returns a failure reason or ``None`` (pass).

    **CR-3: deterministic mechanical verify** scoped to the task's ``baseline_sha`` (commit
    exists + deliverables on disk + commits in ``baseline..HEAD``). **CR-4: + the Auditor
    audit-vs-spec turn** after a mechanical pass — scoped to this ONE task, emitting
    ``task_pass`` + per-task ``findings``. The findings-summary returned here is what the
    CR-3 auto-fix loop escalates into the next brief + the HALT path relays; the loop, the
    ≤5 bound, the done/failed transitions and the HALT stay untouched (the seam).

    **Fast-Fix Lane (F-009, CR-NS-094):** the verifying agent is the **Coordinator** (independent
    verify, reuse the verify_done path — NO Auditor, NO Dual-Build), not the Auditor. Only the
    verify *agent* + prompt differ; the mechanical check, the ``task_pass`` contract, the auto-fix
    loop and every transition stay identical — so ``new_version`` / ``cr`` / ``bug`` are unchanged."""
    slug = _project_slug_for_version(db, state.version_id)
    mech = verify_mechanical(slug, block, task.baseline_sha)
    if mech is not None:
        return mech  # mechanical fail short-circuits — no point auditing a missing commit (saves a turn)
    cross_cutting = _fetch_cross_cutting_rules(db, state.version_id)
    # Fast-Fix routes the per-task verify to the Coordinator (NO Auditor); every other flow keeps the
    # Auditor audit-vs-spec turn. Both emit the identical task_pass + findings contract below.
    fast_fix_flow = state.flow_type == "fast_fix"
    verify_role = "coordinator" if fast_fix_flow else "auditor"
    verify_prompt = (
        _coordinator_verify_prompt_for_task(task, block, cross_cutting)
        if fast_fix_flow
        else _audit_prompt_for_task(task, block, cross_cutting)
    )
    # Parse-retry on the VERIFIER (not the Programmer): an unparseable verify block is the verifier's
    # own formatting bug (e.g. an unescaped quote in a Slovak summary), so the fix is to re-ask it to
    # re-emit valid JSON — NOT to bounce a failure into the auto-fix loop, which would re-run the
    # Programmer's (correct) work on the wrong target (Dedo 2026-06-10: per-task verify JSON-robustness).
    audit = await invoke_agent_with_parse_retry(
        db,
        version_id=state.version_id,
        role=verify_role,
        stage="build",
        prompt=verify_prompt,
        on_message=on_message,
        # Tag the verify message so the FE per-task audit panel can match it to its task
        # (CR-NS-020 CR-5 — mirrors the Programmer turn's tag; payload merges it at invoke_agent).
        extra_payload={"task_id": str(task.id), "task_number": task.number},
    )
    if isinstance(audit, ParseFailure):
        # WS-E (CR-NS-037 addendum — the 6th + FINAL Class-F site, §WS-E amended 5→6): the Auditor
        # judge exhausted parse-retries → its tokens would leak + the failure was invisible. Make it
        # visible + count it, then return the IDENTICAL reason so the auto-fix loop / ≤5 bound /
        # failed+awaiting_director HALT stay byte-for-byte preserved (pure observability, no control-flow
        # change).
        await _record_internal_turn_parse_failure(
            db,
            state.version_id,
            "build",
            turn_label="Audítorov verdikt úlohy",
            failed=audit,
            on_message=on_message,
        )
        return f"audit nečitateľný: {audit.reason}"
    if audit.kind == "blocked":
        return f"audit blokovaný: {audit.question or audit.summary}"
    if not audit.task_pass:  # fail-closed: absent / None / false → FAIL (never pass without an explicit verdict)
        findings = "; ".join(audit.findings) if audit.findings else (audit.summary or "audit zlyhal")
        return f"audit zlyhal: {findings}"
    return None


def _pokusy(n: int) -> str:
    """Slovak plural for the attempt count (1 pokus / 2–4 pokusy / 5+ pokusov)."""
    if n == 1:
        return "1 pokus"
    if 2 <= n <= 4:
        return f"{n} pokusy"
    return f"{n} pokusov"


def _task_audit_verdict(db: Session, version_id: uuid.UUID, task_id: uuid.UUID) -> Optional[dict[str, Any]]:
    """Surface the EXISTING per-task verify verdict (``task_pass`` + ``findings``) from its tagged build
    message (CR-NS-054). Returns the latest such verdict for the task, or ``None`` when no verify message
    exists (a mechanical-only fail, or a verifier ParseFailure that produced no parsed block — both handled
    by the caller's degraded note).

    The verifying agent is the **Auditor** for full flows (preferred — byte-identical to CR-NS-054) and the
    **Coordinator** for the Fast-Fix Lane (F-009, CR-NS-094 — NO Auditor). The Coordinator authors many
    build messages (relays, synthesis), so its fallback requires a non-NULL ``task_pass`` to pick out the
    verify turn — a relay carries ``task_pass=None`` and is correctly skipped."""
    rows = db.execute(
        select(PipelineMessage.author, PipelineMessage.payload)
        .where(
            PipelineMessage.version_id == version_id,
            PipelineMessage.author.in_(("auditor", "coordinator")),
            PipelineMessage.stage == "build",
        )
        .order_by(PipelineMessage.seq.asc())
    ).all()
    # Prefer the Auditor verdict (full flows) — keeps CR-NS-054 behavior exact, including an Auditor
    # block that omitted task_pass (None). Only when no Auditor verdict exists for the task does the
    # Coordinator (fast_fix) verdict apply — and only a real verdict (task_pass not None), never a relay.
    for author, payload in reversed(rows):
        if author == "auditor" and payload and payload.get("task_id") == str(task_id):
            return {"task_pass": payload.get("task_pass"), "findings": payload.get("findings") or []}
    for author, payload in reversed(rows):
        if (
            author == "coordinator"
            and payload
            and payload.get("task_id") == str(task_id)
            and payload.get("task_pass") is not None
        ):
            return {"task_pass": payload.get("task_pass"), "findings": payload.get("findings") or []}
    return None


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
    """§C.1/§C.2 (CR-NS-054, Pillar C) — record ONE factual per-task summary for the Director at a build-task
    settle (``done`` | ``failed``). NEX Command parity: what was done + the audit verdict + how many ATTEMPTS
    + the exact error for drill-down. Pure surfacing of EXISTING loop data (no LLM turn — keeps the build cheap
    + automated); marked ``payload.is_task_summary=true`` (the FE keys off it — mirrors Pillar A's
    ``is_synthesis``). The payload extends §C.1's listed fields with ``work_summary`` (the Implementer's final
    report summary — §C.3a) and ``attempt_errors`` (every auto-fix attempt's reason — §C.3c per-pokus
    drill-down) so the FE card is self-contained. **Additive: never gates the loop;** partial data (no /
    unreadable audit) degrades to a clear note, never blocks."""
    errors = attempt_errors or []
    last_error = errors[-1] if errors else None
    verdict = _task_audit_verdict(db, version_id, task.id)
    if verdict is not None:
        audit_verdict: dict[str, Any] = {"task_pass": verdict["task_pass"], "findings": verdict["findings"]}
    elif last_error and "audit nečitateľný" in last_error:
        audit_verdict = {"task_pass": None, "findings": [], "note": "(audit nečitateľný)"}
    else:
        audit_verdict = {"task_pass": None, "findings": [], "note": "(audit neprebehol)"}

    done = status == "done"
    content = f"Úloha #{task.number} „{task.title}“ — {'hotovo' if done else 'zlyhalo'} ({_pokusy(attempts)})"
    msg = _record_message(
        db,
        version_id=version_id,
        stage="build",
        author="system",
        recipient="director",
        kind="notification",
        content=content,
        payload={
            "is_task_summary": True,
            "task_summary": {
                "task_id": str(task.id),
                "task_number": task.number,
                "title": task.title,
                "final_status": status,
                "attempts": attempts,
                "audit_verdict": audit_verdict,
                "last_error": last_error,
                "work_summary": work_summary,
                "attempt_errors": errors,
            },
        },
    )
    if on_message is not None:
        await on_message(msg)


async def _run_build_round(
    db: Session,
    state: PipelineState,
    *,
    on_event: Optional[claude_agent.EventCallback] = None,
    directive: Optional[str] = None,
    on_message: Optional[MessageCallback] = None,
) -> PipelineState:
    """The continuous per-task build loop (F-007 §6).

    Unlike a gate, build does NOT stop between successful tasks: it dispatches the
    Programmer task-by-task in plan order, mechanically verifies each (auto-fix up to
    ``_AUTO_FIX_RETRIES`` with escalating context), and settles to ``awaiting_director``
    only at the end (all tasks ``done`` → final build sign-off) or on a HALT (a task
    ``failed`` after the bound → Coordinator relays). Every turn streams live via
    ``on_message``. ``baseline_sha`` is captured (repo HEAD) BEFORE each task's first
    dispatch and held immutable across its retries (never build on an unverified base).

    Resume-safe (Dedo 2026-06-08): a task left ``in_progress`` by a dispatch that died
    mid-loop (e.g. a backend restart) is reclaimed to ``todo`` on entry and re-run from its
    persisted ``baseline_sha`` (``done`` stays done; ``failed`` stays for the Director)."""
    version_id = state.version_id
    slug = _project_slug_for_version(db, version_id)
    project_root = claude_agent.PROJECTS_ROOT / slug
    feat_ids_of_version = select(Feat.id).join(Epic, Epic.id == Feat.epic_id).where(Epic.version_id == version_id)

    # Resume-safety: reclaim a task orphaned mid-build.
    db.execute(
        update(Task).where(Task.feat_id.in_(feat_ids_of_version), Task.status == "in_progress").values(status="todo")
    )
    db.flush()

    cross_cutting = _fetch_cross_cutting_rules(db, version_id)
    # gate_g FAIL Fix 2 (CR-NS-057 §F2.2): on a direct FAIL→build re-gate, thread the gate_g audit findings
    # into every task brief so the re-run is NOT blind. _latest_gate_g_findings self-guards staleness (returns
    # None once a task_plan has run since the audit — i.e. a gate_a-transitive build), so the sticky is_regate
    # flag can't leak pre-redesign findings. None ⇒ cross_cutting is untouched.
    if state.is_regate and state.current_stage == "build":
        _gg = _latest_gate_g_findings(db, version_id)
        if _gg:
            cross_cutting = _gg + ("\n\n" + cross_cutting if cross_cutting else "")
    # The Director's framed return/answer (if this is a re-dispatch) seeds the first attempt
    # of whichever task runs first in THIS dispatch — i.e. the resumed/returned task, NOT
    # necessarily the globally-first task — then is consumed so later turns use briefs.
    pending_directive = directive

    while True:
        # CR-NS-027 visibility crux: SessionLocal is expire_on_commit=False, so after the loop's
        # per-message commits the identity-mapped PipelineState keeps its STALE attributes — a plain
        # _get_state returns the cached object and would never observe a Director's mid-build commit.
        # db.refresh forces a fresh row read; Postgres READ COMMITTED then sees the committed status
        # (e.g. a 'paused' set by the Director's separate request session) → the loop stops cleanly.
        state = _get_state(db, version_id)
        if state is not None:
            db.refresh(state)
        if state is None or state.status != "agent_working":
            return state  # Director intervened (pause/return) — land cleanly at a task boundary
        task = task_service.get_next_todo_task(db, version_id)
        if task is None:  # no todo task remains → final build sign-off
            # Fast-Fix Lane (F-009, CR-NS-097): a CLEAN fast_fix build AUTO-advances to release with NO
            # Director approve gate — the one-touch flow ends at the Director's uat_accept. Reaching here for
            # a fast_fix means the single Task is `done` (a failed task HALTs the loop earlier, never getting
            # here), so there is no open finding to gate on. Hand back agent_working so the runner runs the
            # release (Coordinator-verify) turn in THIS dispatch. Other flows settle for the final sign-off.
            if state.flow_type == "fast_fix":
                state.current_stage = _next_stage("build", state.flow_type)  # → release
                _begin_dispatch(db, state)  # agent_working at release → pipeline_runner continues the chain
                return state
            # §A.2 site 2 (build completion): Coordinator synthesis before settling.
            synthesis = await _coordinator_synthesis(db, state, trigger="build", completed=True, on_message=on_message)
            state.status = "awaiting_director"
            state.next_action = synthesis or "Director: finálne schválenie buildu (→ Audit)."
            db.flush()
            return state

        # Baseline BEFORE dispatch — captured once and immutable across the task's whole
        # lifecycle (auto-fix retries + resume/return). A fresh task anchors to repo HEAD
        # now; a reclaimed (orphaned in_progress) or a returned task keeps its PERSISTED
        # baseline_sha so it re-runs against the SAME anchor (Dedo 2026-06-08), never against
        # a moved HEAD. ORM assignment (not a Core UPDATE) keeps the in-memory object in sync
        # so _verify_task passes the real baseline — not a stale None — to verify_mechanical.
        if task.baseline_sha is None:
            task.baseline_sha = _repo_head(project_root)
        if task.baseline_sha is None:
            # Fail-closed (CR-NS-020 CR-4.1): repo HEAD unreadable → cannot anchor the diff →
            # NEVER dispatch on an unknowable base. The task STAYS todo (a precondition failure,
            # not a failed attempt) so it auto-retries on resume once HEAD is readable; the
            # Coordinator relays to the Director (mirrors the 5-fail HALT path).
            relay = await invoke_agent_with_parse_retry(
                db,
                version_id=version_id,
                role="coordinator",
                stage="build",
                prompt=(
                    f"Úloha #{task.number} '{task.title}': nepodarilo sa zachytiť baseline — repo HEAD "
                    "je nečitateľný (git zlyhal). Priprav pre Directora relay: treba opraviť repo a "
                    "pokračovať. "
                    # E7 (F-008 §3, CR-NS-033): triage this build HALT + append a directive (typically
                    # nex_studio_bug / director_decision — a repo/environment problem).
                    "Klasifikuj problém (triage podľa charteru §7.1) a popri slovenskom relayi pripoj "
                    "štruktúrovaný `coordinator_directive` (proposed_action + úprimná confidence). "
                    "Ukonči <<<PIPELINE_STATUS>>> blokom (§7.2)."
                ),
                on_event=on_event,
                on_message=on_message,
            )
            if isinstance(relay, ParseFailure):
                # WS-E (CR-NS-037): relay result was unchecked → silent. Make it visible + count its
                # tokens; the settled awaiting_director outcome below is UNCHANGED.
                await _record_internal_turn_parse_failure(
                    db,
                    version_id,
                    "build",
                    turn_label="Relay Koordinátora (baseline nečitateľný)",
                    failed=relay,
                    on_message=on_message,
                )
            state.status = "awaiting_director"
            state.next_action = (
                f"Úloha #{task.number}: baseline nečitateľný (repo HEAD) — Director: oprav repo a pokračuj."
            )
            db.flush()
            return state
        task.status = "in_progress"
        db.flush()
        # CR-NS-025 Part 1: live current-task breadcrumb. The task is in_progress NOW, but the
        # Programmer's first gate_report (the next recorded message) can be a long turn away — and
        # TaskPlanPanel only refetches the plan when messages.length changes. Record ONE task-start
        # notification per task (here, before the attempt loop) and broadcast it, so the panel
        # refetches immediately and the in_progress task shows live. Auto-fix retries and the
        # completion gate_report record their own messages → only the START was missing. Placed
        # after the fail-closed baseline guard so a never-dispatched task emits no "začal" breadcrumb.
        start_msg = _record_message(
            db,
            version_id=version_id,
            stage="build",
            author="system",
            recipient="director",
            kind="notification",
            content=f"▶ Úloha #{task.number}: {task.title} — Programátor začal.",
            payload={"task_id": str(task.id), "task_number": task.number},
        )
        if on_message is not None:
            await on_message(start_msg)

        prior_failures: list[str] = []
        task_done = False
        autonomous_recovered = False  # Pillar B (CR-NS-055): the Coordinator auto-recovered this task → re-loop
        for attempt in range(1, _AUTO_FIX_RETRIES + 1):
            if attempt == 1 and pending_directive is not None:
                prompt = pending_directive  # Director's framed return/answer for the resumed task
                pending_directive = None  # consume once — later attempts/tasks use generated briefs
            else:
                prompt = _directive_for_build_task(task, cross_cutting, prior_failures, state.flow_type)
            result = await invoke_agent_with_parse_retry(
                db,
                version_id=version_id,
                role="implementer",
                stage="build",
                prompt=prompt,
                on_event=on_event,
                on_message=on_message,
                extra_payload={"task_id": str(task.id), "task_number": task.number, "attempt": attempt},
            )
            if isinstance(result, ParseFailure):
                prior_failures.append(f"neplatný status blok: {result.reason}")
            elif result.kind in ("question", "blocked"):
                # The Programmer cannot proceed → the Coordinator reviews. Pillar B (CR-NS-055, §B.1): if it
                # proposes a clear bounded recovery with honest high confidence (within the per-task cap),
                # AUTO-EXECUTE it + re-loop — no Director click. Else relay + HALT (Director input needed).
                relay_text, directive = await _coordinator_relay(db, state, result, on_message)
                if await _maybe_autonomous_recovery(db, state, task, directive, on_message=on_message):
                    autonomous_recovered = True
                    break  # the while loop re-picks the reset task (no failed settle)
                question_text = relay_text if relay_text is not None else result.question
                state.status = "blocked"
                state.next_action = f"Programátor (úloha #{task.number}) sa pýta: {question_text}"
                db.flush()
                return state
            else:
                reason = await _verify_task(db, state, task, result, on_message)
                if reason is None:
                    db.execute(update(Task).where(Task.id == task.id).values(status="done"))
                    db.flush()
                    task_service.recompute_feat_status(db, task.feat_id)
                    # §C.2 (CR-NS-054): per-task summary at the DONE settle. `attempt` = the passing try;
                    # `result` is the passing Implementer report (its summary = "čo urobené").
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
                prior_failures.append(reason)
            # failed this attempt → record an auto-return + bump the feat's auto-fix counter
            msg = _record_message(
                db,
                version_id=version_id,
                stage="build",
                author="system",
                recipient="implementer",
                kind="return",
                content=f"Auto-fix {attempt}/{_AUTO_FIX_RETRIES} (úloha #{task.number}): {prior_failures[-1]}",
                payload={
                    "verify_reason": prior_failures[-1],
                    "auto_fix_attempt": attempt,
                    "task_id": str(task.id),
                    # WS-D (CR-NS-036): when this attempt's failure was a terminal ParseFailure (the
                    # Programmer produced no message of its own), carry its tokens here — keyed by
                    # task_id so aggregate_pipeline_usage rolls them up to the task. A verify-failed
                    # gate_report attempt already recorded its own metric-bearing message → no-op.
                    **_failure_metrics_payload(result),
                },
            )
            if on_message is not None:
                await on_message(msg)
            db.execute(update(Feat).where(Feat.id == task.feat_id).values(auto_fix_count=Feat.auto_fix_count + 1))
            db.flush()

        if autonomous_recovered:
            # Pillar B (CR-NS-055): the Coordinator auto-recovered this task at an Implementer question
            # (executor already reset it + set agent_working) → re-run the build loop, no failed settle.
            continue

        if not task_done:  # auto-fix bound exhausted → task failed → HALT
            db.execute(update(Task).where(Task.id == task.id).values(status="failed"))
            db.flush()
            task_service.recompute_feat_status(db, task.feat_id)
            # §C.2 (CR-NS-054): per-task summary at the FAILED settle (all _AUTO_FIX_RETRIES tries used).
            # `result` is the last attempt's output (a block → its summary; a ParseFailure → no summary).
            await _record_task_summary(
                db,
                version_id,
                task,
                status="failed",
                attempts=_AUTO_FIX_RETRIES,
                work_summary=result.summary if isinstance(result, PipelineStatusBlock) else None,
                attempt_errors=prior_failures,
                on_message=on_message,
            )
            # Coordinator relays the failure to the Director (hub-and-spoke; §3).
            relay = await invoke_agent_with_parse_retry(
                db,
                version_id=version_id,
                role="coordinator",
                stage="build",
                prompt=(
                    f"Úloha #{task.number} '{task.title}' zlyhala po {_AUTO_FIX_RETRIES} auto-fix pokusoch. "
                    f"Posledný dôvod: {prior_failures[-1]}. Priprav pre Directora relay — čo treba rozhodnúť "
                    "(vrátiť na prepracovanie / konzultovať). " + _FIRST_PRINCIPLES_TRIAGE +
                    # Pillar B (CR-NS-055 §B.2): first-principles triage — a clear bounded recovery with honest
                    # high confidence auto-executes (no Director click); ambiguity / design-scope / destructive
                    # escalates.
                    # E7 (F-008 §3, CR-NS-033): this failed-task HALT is the PRIME triage point — classify
                    # it and propose a concrete fix (reset_task / move_baseline / route_to_designer /
                    # escalate_dedo) the Director approves + the engine executes.
                    "Klasifikuj problém (triage podľa charteru §7.1) a popri relayi pripoj štruktúrovaný "
                    "`coordinator_directive` (proposed_action + úprimná confidence). "
                    "Ukonči <<<PIPELINE_STATUS>>> blokom (§7.2)."
                ),
                on_event=on_event,
                on_message=on_message,
            )
            if isinstance(relay, ParseFailure):
                # WS-E (CR-NS-037): relay result was unchecked → silent on the PRIME triage point. Make
                # it visible + count its tokens; the settled awaiting_director HALT below is UNCHANGED.
                await _record_internal_turn_parse_failure(
                    db,
                    version_id,
                    "build",
                    turn_label="Relay Koordinátora (úloha zlyhala)",
                    failed=relay,
                    on_message=on_message,
                )
            else:
                # Pillar B (CR-NS-055, §B.1): if the Coordinator proposes a clear bounded recovery with honest
                # high confidence (within the per-task cap), AUTO-EXECUTE it + continue the build — no Director
                # click. Else fall through to the existing escalate (awaiting_director).
                directive = (
                    relay.coordinator_directive.model_dump(mode="json")
                    if relay.coordinator_directive is not None
                    else None
                )
                if await _maybe_autonomous_recovery(db, state, task, directive, on_message=on_message):
                    continue
            state.status = "awaiting_director"
            state.next_action = (
                f"Úloha #{task.number} zlyhala po {_AUTO_FIX_RETRIES} pokusoch — Director: vrátiť / konzultovať."
            )
            db.flush()
            return state
        # task done → continue the loop to the next todo task (no Director click; §6)


async def _run_designer_spec_fix(
    db: Session,
    state: PipelineState,
    *,
    on_event: Optional[claude_agent.EventCallback] = None,
    on_message: Optional[MessageCallback] = None,
) -> PipelineState:
    """E7 route_to_designer (F-008 §10, CR-NS-034): a mid-build Designer spec-fix turn. The Designer fixes
    the spec/design for the held failed task (per the latest coordinator_directive's params/rationale) and
    reports DONE; we then reset that task → todo (fresh ≤5 budget, corrected spec), clear the returns_to
    marker, hand current_actor back to the Implementer, and re-enter _run_build_round so the Programmer
    re-attempts. Mirrors the gate_e Branch B designer_edit precedent, adapted to build."""
    version_id = state.version_id
    task = _failed_build_task(db, version_id)
    directive = _latest_coordinator_directive(db, version_id) or {}
    section = (directive.get("params") or {}).get("section")
    rationale = directive.get("rationale") or "spec problém pri build úlohe"
    task_label = f"#{task.number} '{task.title}'" if task is not None else "build úloha"
    prompt = (
        f"Build úloha {task_label} narazila na problém v spec/dizajne: {rationale}. "
        + (f"Týka sa to sekcie: {section}. " if section else "")
        + "Oprav príslušnú spec/dizajn v `docs/specs/…` (si jediný s právom editovať spec), aby build "
        "úloha mohla prejsť. Ukonči <<<PIPELINE_STATUS>>> blokom (§7.2)."
    )
    edit = await invoke_agent_with_parse_retry(
        db,
        version_id=version_id,
        role="designer",
        stage="build",
        prompt=prompt,
        on_event=on_event,
        recipient="coordinator",
        on_message=on_message,
    )
    if isinstance(edit, ParseFailure):
        # Designer turn unparseable → CLEAR the marker (returns_to is for the duration of ONE Designer
        # dispatch only) and block. The build returns to its HALT (the task stays failed); the Director's
        # normal build/blocked actions work, and a re-route needs a FRESH Coordinator directive (re-triaged)
        # — never a blind, unbounded Designer re-run, and never a dangling marker that hijacks return/ask.
        state.returns_to = None
        state.current_actor = "implementer"
        db.flush()
        return await _block_failed(state, db, edit.reason, failed=edit, on_message=on_message)
    # Designer DONE → reset the held failed task (corrected spec), clear the marker, hand back to build.
    if task is not None:
        task.status = "todo"
        db.flush()
        task_service.recompute_feat_status(db, task.feat_id)
    state.returns_to = None
    state.current_actor = "implementer"
    db.flush()
    return await _run_build_round(db, state, on_event=on_event, on_message=on_message)


def _stage_order_for(flow_type: str) -> tuple[str, ...]:
    """The ordered stage path for a flow. Fast-Fix (F-009, CR-NS-094) takes the shorter
    ``kickoff → build → release → done`` path (skips gate_a-e / task_plan / gate_g); every other
    flow (``new_version`` / ``cr`` / ``bug``) keeps the full :data:`STAGE_ORDER` unchanged."""
    return FAST_FIX_STAGE_ORDER if flow_type == "fast_fix" else STAGE_ORDER


def _next_stage(stage: str, flow_type: str = "new_version") -> str:
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
    """Apply a Director action (F-007 §5.2). Sole mutator of ``pipeline_state``."""
    if action not in _ACTIONS:
        raise OrchestratorError(f"Unknown action: {action!r}")
    payload = payload or {}
    state = _get_state(db, version_id)

    if action == "start":
        if state is not None:
            raise OrchestratorError("Pipeline already started for this version")
        flow_type = payload.get("flow_type", "new_version")
        if flow_type not in ("new_version", "cr", "bug", "fast_fix"):
            raise OrchestratorError(f"Invalid flow_type: {flow_type!r}")
        # Fast-Fix Lane (F-009, CR-NS-094): the Director's directive is the whole task brief — carry it
        # in the kickoff payload so the Coordinator triages it and the build-reuse step can materialize
        # the single minimal Task from it. ``None`` for every other flow → kickoff payload unchanged.
        directive = payload.get("directive") if flow_type == "fast_fix" else None
        # Fast-Fix Lane (F-009 §1, CR-NS-097): the Director directive IS the kickoff message the
        # Coordinator triages — carry it in the human-readable CONTENT (not just the payload) so it shows
        # on the board and the kickoff brief's "smernica je vyššie" claim is honoured. Other flows keep the
        # generic kickoff content.
        kickoff_content = directive if (flow_type == "fast_fix" and directive) else "Spustenie pipeline."
        state = PipelineState(
            version_id=version_id,
            flow_type=flow_type,
            current_stage="kickoff",
            current_actor="coordinator",
            status="agent_working",
            next_action="Coordinator robí discovery.",
        )
        db.add(state)
        db.flush()
        _record_message(
            db,
            version_id=version_id,
            stage="kickoff",
            author="director",
            recipient="coordinator",
            kind="kickoff",
            content=kickoff_content,
            payload={"flow_type": flow_type, **({"directive": directive} if directive else {})},
        )
        # WS-B1 (CR-NS-029): a new-version kickoff starts every agent fresh — drop all of the project's
        # OrchestratorSession rows so no stale cross-version --resume context leaks in. Per Director
        # decision D2, a re-gate (verdict FAIL → rewind, below) must PRESERVE sessions — and it does
        # automatically: re-gate mutates existing state, it never reaches this "start" branch (which is
        # gated on state is None), so only a genuine kickoff resets.
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

    # Status guard (CR-NS-018): never act on / advance past an agent that is still
    # working. The advancing actions need a settled agent (awaiting_director or a
    # blocked ratify-out-of-a-question); answer needs an actual question (blocked);
    # pause is only meaningful while the agent works.
    # 'paused' (CR-NS-027) is a settled, Director-actionable state — the build loop has stopped at a
    # task boundary — so the advancing-action guard lets it through (the resume pair continue_build /
    # end_build live in _ADVANCING_ACTIONS); the dedicated paused guard just below restricts WHICH.
    if action in _ADVANCING_ACTIONS and state.status not in ("awaiting_director", "blocked", "paused"):
        raise OrchestratorError("Agent ešte pracuje — počkaj na jeho výstup")
    if action == "answer" and state.status != "blocked":
        raise OrchestratorError("Agent sa na nič nepýta — odpoveď nie je na mieste")
    if action == "pause" and state.status != "agent_working":
        raise OrchestratorError("Pauza je možná len počas práce agenta")
    # Pause is build-only (CR-NS-027 decision A): only the build loop has a cooperative task boundary
    # to stop at — a single-turn gate has no boundary, so a gate-pause would be a silent no-op.
    if action == "pause" and state.current_stage != "build":
        raise OrchestratorError("Pauza je možná len počas buildu")
    # From 'paused' (CR-NS-027) ONLY the resume pair is valid: continue_build (re-dispatch the loop) or
    # end_build (skip the rest → gate_g). Everything else must NOT silently un-pause — in particular
    # 'ask' is not in _ADVANCING_ACTIONS, so without this it would fall through to its handler, call
    # _begin_dispatch and flip the status back to agent_working (the route would then re-dispatch).
    # The Director resumes deliberately, never as a side effect of asking/answering/returning.
    if state.status == "paused" and action not in ("continue_build", "end_build"):
        raise OrchestratorError(
            "Build je pozastavený — pokračuj cez 'Pokračovať v builde' alebo ho ukonči (Ukončiť build)"
        )

    if action == "approve":
        _record_message(
            db,
            version_id=version_id,
            stage=state.current_stage,
            author="director",
            recipient=state.current_actor,
            kind="approval",
            content=payload.get("comment", "Schválené."),
        )
        # Gate E (F-007-gate-e §3/§4): a topic boundary ratifies + continues to the
        # NEXT okruh (stage STAYS gate_e); only a final boundary (coverage_complete +
        # no open finding) signs off → task_plan. An open finding blocks the final close.
        if state.current_stage == "gate_e":
            report = _latest_customer_gate_report(db, version_id)
            if _gate_e_coverage_complete(report):
                if _gate_e_open_findings(db, version_id) > 0:
                    raise OrchestratorError("Otvorené nálezy blokujú uzavretie Gate E — najprv ich vyrieš")
                _write_gate_e_audit(db, version_id)  # §4 audit record before closing
                state.current_stage = _next_stage("gate_e", state.flow_type)  # → task_plan
                db.flush()
                _begin_dispatch(db, state)
            else:
                _begin_dispatch(db, state)  # next topic — stage unchanged
            return state
        # Build (F-007 §6): the final sign-off advances build → gate_g. The invariant (CR-4.1
        # option B): you cannot finally sign off a build with tasks still unbuilt — so a remaining
        # `todo` task blocks `approve` (this also closes the baseline-HALT hole, where a task left
        # todo is NOT counted by _build_open_findings). A failed / unverified (in_progress) task
        # blocks too (the deterministic gate). `end_build` is the separate, deliberate early exit.
        if state.current_stage == "build":
            if task_service.get_next_todo_task(db, version_id) is not None:
                raise OrchestratorError(
                    "Build nie je hotový — ostávajú nepostavené úlohy (todo); finálne schválenie nie je možné"
                )
            if _build_open_findings(db, version_id) > 0:
                raise OrchestratorError(
                    "Otvorené úlohy (failed/neoverené) blokujú uzavretie buildu — najprv ich vyrieš"
                )
        prev_stage = state.current_stage
        state.current_stage = _next_stage(state.current_stage, state.flow_type)
        db.flush()
        # Fast-Fix Lane (F-009, CR-NS-094): entering build (kickoff→build) materializes the ONE minimal
        # Task from the Director directive so the existing per-task build loop runs unchanged. Idempotent
        # (no-op if the Task already exists). Other flows decompose tasks via the Designer's task_plan.
        if state.flow_type == "fast_fix" and prev_stage == "kickoff" and state.current_stage == "build":
            fast_fix.ensure_build_task(db, version_id)
        if state.current_stage == "done":
            state.current_actor = "director"
            state.status = "done"
            state.next_action = "Pipeline dokončená."
            db.flush()
        else:
            _begin_dispatch(db, state)
        return state

    if action == "return":
        comment = payload.get("comment")
        if not comment or not str(comment).strip():
            raise OrchestratorError("return requires a non-empty payload.comment")
        # Gate E + task_plan + build (§2/§5/§6): Director ↔ Coordinator only — a return is
        # Coordinator-relayed, never addressed to the worker directly.
        recipient = "coordinator" if state.current_stage in ("gate_e", "task_plan", "build") else state.current_actor
        _record_message(
            db,
            version_id=version_id,
            stage=state.current_stage,
            author="director",
            recipient=recipient,
            kind="return",
            content=str(comment),
        )
        # Build HALT (§6/§7): a return reworks the failed task — reset it to todo so the
        # build loop re-attempts it (fresh ≤5 budget) with the Director's comment threaded in.
        if state.current_stage == "build":
            _reset_failed_tasks_to_todo(db, version_id)
        # task_plan refine (CR-NS-024): a return KEEPS the Designer's (slug, designer) --resume
        # session, so the next dispatch remembers the prior plan and applies just the Director's
        # edit (the comment threads into the brief) — incremental refinement, not a from-scratch
        # re-decompose. The Designer still re-reads the on-disk spec each turn, so an explicit
        # "re-plan from scratch" comment is still honoured. (CR-NS-022 §3 deleted the session to
        # force a one-time charter reload; that need is satisfied. Reloading a fixed charter is now
        # a deliberate maintenance concern, never an implicit cost of every refine-return.)
        _begin_dispatch(db, state)
        return state

    if action == "ask":
        text = payload.get("text")
        if not text or not str(text).strip():
            raise OrchestratorError("ask requires a non-empty payload.text")
        # Gate E + task_plan + build (§2/§5/§6): "Konzultovať s Koordinátorom" — the Director's
        # input (question or constatation) goes to the Coordinator, never to the worker directly.
        recipient = "coordinator" if state.current_stage in ("gate_e", "task_plan", "build") else state.current_actor
        _record_message(
            db,
            version_id=version_id,
            stage=state.current_stage,
            author="director",
            recipient=recipient,
            kind="question",
            content=str(text),
        )
        _begin_dispatch(db, state)
        return state

    if action == "answer":
        text = payload.get("text")
        if not text or not str(text).strip():
            raise OrchestratorError("answer requires a non-empty payload.text")
        _record_message(
            db,
            version_id=version_id,
            stage=state.current_stage,
            author="director",
            recipient=state.current_actor,
            kind="answer",
            content=str(text),
        )
        _begin_dispatch(db, state)
        return state

    if action == "apply_coordinator_recommendation":
        if latest_coordinator_report(db, version_id) is None:
            raise OrchestratorError("Žiadne odporúčanie Koordinátora na zapracovanie")
        # E7 (F-008 §9, contract A — the no-op fix): at build, an EXECUTABLE coordinator_directive runs
        # its matching internal executor (reset_task / move_baseline / clear_session / escalate_dedo)
        # instead of threading advisory text. A relay / low-confidence / director_decision directive (or
        # any non-build stage) falls through to the advisory re-dispatch below.
        if state.current_stage == "build":
            directive = _latest_coordinator_directive(db, version_id)
            if _coordinator_directive_executable(directive):
                return _execute_coordinator_directive(db, state, directive)
        if STAGE_ACTOR.get(state.current_stage) is None:
            raise OrchestratorError("Aktuálna fáza nemá agenta na re-dispatch")
        # Advisory relay (unchanged): the Coordinator's report is threaded as the re-dispatch directive
        # by ``dispatch_directive`` (route). Stage does NOT advance.
        _record_message(
            db,
            version_id=version_id,
            stage=state.current_stage,
            author="director",
            recipient=state.current_actor,
            kind="approval",
            content="Schválené odporúčania Koordinátora.",
        )
        _begin_dispatch(db, state)
        return state

    if action in ("fix", "leave"):
        # Gate E Branch B (F-007-gate-e §2): only at a per-question stop with a Designer
        # gap. The decision travels Director→Coordinator→Designer (never direct): we
        # record it as director→coordinator; `fix` then re-dispatches with a
        # Coordinator-relayed edit directive (designer_edit), `leave` continues to the
        # next question with no edit.
        if state.current_stage != "gate_e":
            raise OrchestratorError(f"{action} je platné len vo fáze Gate E")
        if not _gate_e_gap_open(db, version_id):
            raise OrchestratorError("Žiadny návrh Návrhára na rozhodnutie (gap_found)")
        content = (
            "Director schválil opravu — Koordinátor odovzdá pokyn Návrhárovi."
            if action == "fix"
            else "Director ponechal bez úpravy — podľa odporúčania Koordinátora."
        )
        _record_message(
            db,
            version_id=version_id,
            stage="gate_e",
            author="director",
            recipient="coordinator",
            kind="approval",
            content=content,
            payload={"resolves_gap": True},  # deterministic open-finding gate marker (§5)
        )
        _begin_dispatch(db, state)
        return state

    if action == "verdict":
        verdict = payload.get("verdict")
        if verdict not in ("PASS", "FAIL"):
            raise OrchestratorError("verdict requires payload.verdict in {PASS, FAIL}")
        _record_message(
            db,
            version_id=version_id,
            stage=state.current_stage,
            author="director",
            recipient="auditor",
            kind="verdict",
            content=verdict,
            payload={"verdict": verdict},
        )
        if verdict == "PASS":
            state.current_stage = "release"
            db.flush()
            _begin_dispatch(db, state)
        else:
            # gate_g FAIL Fix 2 (CR-NS-057 §F2.4): default to the INFERRED re-gate target (design/scope →
            # gate_a; code-fixable / Director-initiated FAIL on a PASS audit → build) instead of a blind
            # "gate_a". An explicit Director payload.entry_stage (a chip override) always wins; the verdict
            # stays the Director's. The STAGE_ORDER guard is unchanged.
            entry = payload.get("entry_stage") or _infer_regate_entry_stage(db, version_id)
            if entry not in STAGE_ORDER:
                raise OrchestratorError(f"Invalid entry_stage: {entry!r}")
            state.is_regate = True
            state.iteration += 1
            state.current_stage = entry
            # A build re-gate re-runs the WHOLE build → flip done→todo (a gate_a re-gate rebuilds the epics via
            # the task_plan write-path, so it needs no reset). Sessions preserved on both targets.
            if entry == "build":
                _reset_done_tasks_for_regate(db, version_id)
            db.flush()
            _begin_dispatch(db, state)
        return state

    if action == "uat_accept":
        # Phase 2: transition to done + notification; real prod-deploy hook is Phase 5.
        state.current_stage = "done"
        state.current_actor = "director"
        state.status = "done"
        state.next_action = "Verzia akceptovaná (UAT). Prod deploy hook príde vo Phase 5."
        _record_message(
            db,
            version_id=version_id,
            stage="release",
            author="system",
            recipient="director",
            kind="notification",
            content="UAT akceptované zákazníkom — pipeline dokončená.",
        )
        db.flush()
        return state

    if action == "end_gate_e":
        # Director ends Gate E early ("pokrytie stačí", F-007-gate-e §4) → advance to
        # build. Skips remaining COVERAGE, but any open finding of a covered topic
        # still blocks closing — no unresolved finding may pass to Build.
        if state.current_stage != "gate_e":
            raise OrchestratorError("end_gate_e je platné len vo fáze Gate E")
        if _gate_e_open_findings(db, version_id) > 0:
            raise OrchestratorError("Otvorené nálezy blokujú uzavretie Gate E — najprv ich vyrieš")
        _record_message(
            db,
            version_id=version_id,
            stage="gate_e",
            author="director",
            recipient="customer",
            kind="approval",
            content="Gate E ukončené Directorom (pokrytie stačí).",
        )
        _write_gate_e_audit(db, version_id)  # §4 audit record before closing
        state.current_stage = _next_stage("gate_e", state.flow_type)  # → task_plan
        db.flush()
        _begin_dispatch(db, state)
        return state

    if action == "end_build":
        # Director ends build early ("zvyšok do auditu", F-007 §6) → advance to gate_g.
        # Early end, but any failed/unverified task still blocks the close — no unresolved
        # task may pass to the Auditor (deterministic gate from the orchestrator's record).
        if state.current_stage != "build":
            raise OrchestratorError("end_build je platné len vo fáze build")
        if _build_open_findings(db, version_id) > 0:
            raise OrchestratorError("Otvorené úlohy (failed/neoverené) blokujú uzavretie buildu — najprv ich vyrieš")
        _record_message(
            db,
            version_id=version_id,
            stage="build",
            author="director",
            recipient="implementer",
            kind="approval",
            content="Build ukončený Directorom (zvyšok do auditu).",
        )
        # Fast-Fix Lane (F-009, CR-NS-094): build → release (skips gate_g); full flows → gate_g.
        state.current_stage = _next_stage("build", state.flow_type)
        db.flush()
        _begin_dispatch(db, state)
        return state

    if action == "continue_build":
        # Director resumes the build loop after a HALT ("prostredie opravené, pokračuj", F-007 §7.2)
        # — no comment, no stage change: just re-dispatch _run_build_round (it re-picks the next
        # todo task). Distinct from `return` (rework a failed task, comment required) and `end_build`
        # (skip the rest → gate_g). The record is Director↔Coordinator (§6/§7 — the Director never
        # addresses the worker directly; the engine re-dispatches the Implementer via _begin_dispatch).
        if state.current_stage != "build":
            raise OrchestratorError("continue_build je platné len vo fáze build")
        _record_message(
            db,
            version_id=version_id,
            stage="build",
            author="director",
            recipient="coordinator",
            kind="approval",
            content="Build pokračuje (prostredie opravené).",
        )
        _begin_dispatch(db, state)  # stage stays build; status → agent_working; the route schedules it
        return state

    if action == "accept_merged":
        # WS-B2 (CR-NS-031): a legitimately-MERGED task dead-ends because its work sits in a commit
        # at/before its baseline (verify_mechanical: "commit predates the task baseline" — e.g. status +
        # transitions committed together, so task #3's work is in task #2's commit = task #3's baseline).
        # The Director recognizes the Programmer's reported commit by moving the task's baseline to that
        # commit's PARENT, so it falls back inside baseline..HEAD; the task resets to todo and the build
        # loop re-verifies it (the Auditor checks the content as usual). Explicit Director action only —
        # never silent auto-recognition (a task must never silently claim a prior commit).
        if state.current_stage != "build":
            raise OrchestratorError("accept_merged je platné len vo fáze build")
        task = _failed_build_task(db, version_id)
        if task is None:
            raise OrchestratorError("Žiadna zlyhaná úloha — niet pri ktorej uznať spoločný commit")
        commit = _latest_reported_commit(db, version_id, task.id)
        if commit is None:
            raise OrchestratorError("Programátor nenahlásil commit pre túto úlohu — nemožno uznať spoločný commit")
        project_root = claude_agent.PROJECTS_ROOT / _project_slug_for_version(db, version_id)
        parent = _repo_parent(project_root, commit)
        if parent is None:
            raise OrchestratorError(
                f"Nepodarilo sa zistiť rodičovský commit pre {commit[:8]} — repo nečitateľné alebo koreňový commit"
            )
        task.baseline_sha = parent  # ORM assignment keeps the in-memory object in sync (CR-3 lesson)
        task.status = "todo"  # re-attempt → the loop re-verifies against the moved baseline
        db.flush()
        task_service.recompute_feat_status(db, task.feat_id)
        _record_message(
            db,
            version_id=version_id,
            stage="build",
            author="director",
            recipient="coordinator",
            kind="approval",
            content=(
                f"Uznaný spoločný commit pre úlohu #{task.number}: baseline presunutý na {parent[:8]} "
                f"(rodič nahláseného commitu {commit[:8]}) — úloha sa znova overí."
            ),
            payload={"task_id": str(task.id), "accept_merged_commit": commit, "new_baseline": parent},
        )
        _begin_dispatch(db, state)  # re-run the build loop → re-verify the merged task against the moved baseline
        return state

    # action == "pause" (CR-NS-027): a genuine paused status, not just a label. The running build
    # loop re-reads state at its next task boundary (db.refresh, READ COMMITTED) and, seeing a status
    # other than agent_working, settles + stops cleanly — the current task finishes, no mid-task kill.
    # Leaving agent_working also stops the action route from re-dispatching (the no-op-pause bug that
    # spawned a 2nd loop). Resume via continue_build.
    state.status = "paused"
    state.next_action = "Pozastavené Directorom — pokračuj cez 'Pokračovať v builde'."
    db.flush()
    return state
