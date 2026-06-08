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

import logging
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Optional

from pydantic import ValidationError
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.db.models.orchestrator import OrchestratorSession
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version
from backend.schemas.epic import EpicCreate
from backend.schemas.feat import FeatCreate
from backend.schemas.task import TaskCreate
from backend.services import claude_agent
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


def _directive_for(stage: str) -> str:
    """Minimal orchestrator directive for a stage. The agent reads its charter."""
    return (
        f"Pokračuj fázou '{stage}' podľa autoritatívneho spec balíka a svojho charteru. "
        "Ukonči odpoveď strojovým <<<PIPELINE_STATUS>>> blokom (F-007 §7.2)."
    )


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
    """
    slug = _project_slug_for_version(db, version_id)
    session_id, is_first = _resolve_orch_session(db, slug, role)
    charter_path: Optional[Path] = None
    if is_first:
        charter_path = claude_agent.PROJECTS_ROOT / slug / ".claude" / "agents" / role / "CLAUDE.md"

    tagged_on_event: Optional[claude_agent.EventCallback] = None
    if on_event is not None:

        async def tagged_on_event(evt: dict) -> None:
            await on_event({**evt, "_role": role} if isinstance(evt, dict) else evt)

        await tagged_on_event({"type": "active_role"})  # per-turn rail signal (steps Z→N→K)

    try:
        stdout = await invoke_claude(
            project_slug=slug,
            claude_session_id=session_id,
            prompt=prompt,
            charter_path=charter_path,
            timeout=timeout if timeout is not None else _timeout_for(stage),
            on_event=tagged_on_event,
        )
    except ClaudeAgentError as exc:
        msg = _record_message(
            db,
            version_id=version_id,
            stage=stage,
            author="system",
            recipient="director",
            kind="notification",
            content=f"Agent '{role}' invocation failed: {exc}",
            payload={"error": str(exc)},
        )
        if on_message is not None:
            await on_message(msg)
        return ParseFailure(f"claude invocation failed: {exc}")

    parsed = parse_status_block(stdout)
    if isinstance(parsed, ParseFailure):
        msg = _record_message(
            db,
            version_id=version_id,
            stage=stage,
            author="system",
            recipient="director",
            kind="notification",
            content=f"Status block parse failed for '{role}': {parsed.reason}",
            payload={"parse_error": parsed.reason},
        )
        if on_message is not None:
            await on_message(msg)
        return parsed

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
            "plan": parsed.plan.model_dump() if parsed.plan is not None else None,
            "cross_cutting_rules": parsed.cross_cutting_rules,
            # Per-task Auditor verdict (F-007 §6, CR-NS-020 CR-4) — persisted for CR-5's
            # per-task audit panel (the diff + findings the Director can drill into).
            "task_pass": parsed.task_pass,
            # Caller-supplied structural markers (e.g. is_fix_edit) for the deterministic
            # open-finding count — orchestrator record, not agent self-report (§5).
            **(extra_payload or {}),
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
                f"Tvoj <<<PIPELINE_STATUS>>> blok nebol platný JSON: {result.reason}. "
                "Pošli LEN opravený, platný <<<PIPELINE_STATUS>>> blok — rovnaký obsah, správny JSON."
            ),
            recipient=recipient,
            on_message=on_message,
            extra_payload=extra_payload,
        )
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


async def verify_done(
    db: Session,
    version_id: uuid.UUID,
    block: PipelineStatusBlock,
    on_message: Optional[MessageCallback] = None,
) -> Optional[str]:
    """Verify a gate_report before awaiting the Director. Reason on FAIL, else None.

    Mechanical checks first (deterministic); then a judgment check by invoking
    the coordinator agent. The coordinator's block must report ``kind != blocked``
    and ``awaiting='director'`` to count as a PASS. The Coordinator's judgment is a
    real dispatch-path message → ``on_message`` streams it live (CR-NS-018).
    """
    slug = _project_slug_for_version(db, version_id)
    mech = verify_mechanical(slug, block)
    if mech is not None:
        return mech

    judgment = await invoke_agent(
        db,
        version_id=version_id,
        role="coordinator",
        stage=block.stage,
        prompt=(
            f"Verifikuj DONE report fázy '{block.stage}': spec compliance + žiadny "
            "claim bez authoritative source (P-2). Ukonči <<<PIPELINE_STATUS>>> blokom (§7.2)."
        ),
        on_message=on_message,
    )
    if isinstance(judgment, ParseFailure):
        return f"coordinator verify unparseable: {judgment.reason}"
    if judgment.kind == "blocked":
        return f"coordinator flagged: {judgment.question or judgment.summary}"
    return None


async def _coordinator_relay(
    db: Session,
    state: PipelineState,
    worker_block: PipelineStatusBlock,
    on_message: Optional[MessageCallback] = None,
) -> Optional[str]:
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
            "relay — čo treba rozhodnúť. Ukonči <<<PIPELINE_STATUS>>> blokom (§7.2)."
        ),
        on_message=on_message,
    )
    if isinstance(relay, ParseFailure):
        return None
    return relay.question or relay.summary


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
        return await _run_build_round(db, state, on_event=on_event, directive=directive, on_message=on_message)

    prompt = directive if directive is not None else _directive_for(stage)
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
        state.status = "blocked"
        state.next_action = f"Blokované: {result.reason}. Eskalované Directorovi."
        db.flush()
        return state

    if result.kind in ("question", "blocked"):
        # Hub-and-spoke (CR-NS-018): a worker's question/blocked turn is reviewed
        # by the Coordinator first, who relays it to the Director. The Coordinator's
        # own question (kickoff) is surfaced directly — no double-review. On an
        # unparseable relay, fall back to the worker's question (never a dead-end).
        relay = await _coordinator_relay(db, state, result, on_message) if actor != "coordinator" else None
        question_text = relay if relay is not None else result.question
        state.status = "blocked"
        state.next_action = f"Agent '{actor}' sa pýta: {question_text}"
        db.flush()
        return state

    if stage == "task_plan" and result.kind == "gate_report":
        # F-007 §5 / CR-NS-020 CR-2: the plan's mechanical gate is the deterministic
        # write-path (not the disk-deliverable verify_mechanical, nor a Coordinator judge
        # turn — the Director reviews the materialized tree himself, per Dedo 2026-06-07).
        reason = _write_task_plan(db, state, result)
        if reason is not None:
            state.status = "blocked"
            state.next_action = f"Plán úloh zamietnutý: {reason}. Eskalované."
        else:
            state.status = "awaiting_director"
            state.next_action = "Director: schváliť/vrátiť plán úloh."
        db.flush()
        return state

    if result.kind == "gate_report":
        reason = await _verify_with_retries(db, state, result, on_message=on_message)
        if reason is not None:
            state.status = "blocked"
            state.next_action = f"Verify zlyhal po retries: {reason}. Eskalované."
        else:
            state.status = "awaiting_director"
            state.next_action = f"Director: schváliť/vrátiť fázu '{stage}'."
        db.flush()
        return state

    # kickoff / answer / done-class agent output → await the Director.
    state.status = "awaiting_director"
    state.next_action = f"Director: posúdiť výstup fázy '{stage}'."
    db.flush()
    return state


_GATE_E_NO_EDIT = (
    "odpovedz — vysvetli, či je to pokryté; ak je to medzera, LEN navrhni riešenie "
    "(nastav gap_found=true + proposed_fix), NEUPRAVUJ žiadny súbor"
)


def _block_failed(state: PipelineState, db: Session, reason: str) -> PipelineState:
    state.status = "blocked"
    state.next_action = f"Blokované: {reason}. Eskalované Directorovi."
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
    await invoke_agent_with_parse_retry(
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
            return _block_failed(state, db, revised.reason)
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
            return _block_failed(state, db, edit.reason)
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
        return _block_failed(state, db, cust.reason)

    if cust.kind == "gate_report" and cust.topic_done:  # round boundary
        state.status = "awaiting_director"
        state.next_action = f"Director: posúď okruh '{cust.topic or 'okruh'}' (nálezy + riešenia Návrhára)."
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
            return _block_failed(state, db, designer.reason)
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
) -> Optional[str]:
    """Verify; on failure auto-return to the agent up to ``_VERIFY_RETRIES`` times.

    Every recorded turn here is a dispatch-path message → ``on_message`` streams each
    live (the Coordinator judgment via :func:`verify_done`, the system auto-return, and
    the worker's corrected report) so none is lost once the end batch is dropped."""
    reason = await verify_done(db, state.version_id, block, on_message)
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
        if isinstance(retry, ParseFailure) or retry.kind != "gate_report":
            return reason  # give up on non-report → caller escalates
        block = retry
        reason = await verify_done(db, state.version_id, block, on_message)
    return reason


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


def _directive_for_build_task(task: Task, cross_cutting_rules: Optional[str], prior_failures: list[str]) -> str:
    """Per-task brief for the Programmer (§6): one task, its description, the authoritative
    spec to consult, the cross-cutting block, and (on a retry) the prior attempts' reasons."""
    parts = [f"Programátor, postav JEDNU úlohu (TASK #{task.number}): {task.title}"]
    if task.description:
        parts.append(f"Popis úlohy: {task.description}")
    parts.append("Naštuduj relevantnú sekciu autoritatívneho špecu (docs/specs/) pre túto úlohu — postav presne ju.")
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
    ≤5 bound, the done/failed transitions and the HALT stay untouched (the seam)."""
    slug = _project_slug_for_version(db, state.version_id)
    mech = verify_mechanical(slug, block, task.baseline_sha)
    if mech is not None:
        return mech  # mechanical fail short-circuits — no point auditing a missing commit (saves a turn)
    cross_cutting = _fetch_cross_cutting_rules(db, state.version_id)
    audit = await invoke_agent(
        db,
        version_id=state.version_id,
        role="auditor",
        stage="build",
        prompt=_audit_prompt_for_task(task, block, cross_cutting),
        on_message=on_message,
    )
    if isinstance(audit, ParseFailure):
        return f"audit nečitateľný: {audit.reason}"
    if audit.kind == "blocked":
        return f"audit blokovaný: {audit.question or audit.summary}"
    if not audit.task_pass:  # fail-closed: absent / None / false → FAIL (never pass without an explicit verdict)
        findings = "; ".join(audit.findings) if audit.findings else (audit.summary or "audit zlyhal")
        return f"audit zlyhal: {findings}"
    return None


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
    # The Director's framed return/answer (if this is a re-dispatch) seeds the first attempt
    # of whichever task runs first in THIS dispatch — i.e. the resumed/returned task, NOT
    # necessarily the globally-first task — then is consumed so later turns use briefs.
    pending_directive = directive

    while True:
        state = _get_state(db, version_id)
        if state is None or state.status != "agent_working":
            return state  # Director intervened (pause/return) — land cleanly at a task boundary
        task = task_service.get_next_todo_task(db, version_id)
        if task is None:  # no todo task remains → final build sign-off
            state.status = "awaiting_director"
            state.next_action = "Director: finálne schválenie buildu (→ Audit)."
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
        task.status = "in_progress"
        db.flush()

        prior_failures: list[str] = []
        task_done = False
        for attempt in range(1, _AUTO_FIX_RETRIES + 1):
            if attempt == 1 and pending_directive is not None:
                prompt = pending_directive  # Director's framed return/answer for the resumed task
                pending_directive = None  # consume once — later attempts/tasks use generated briefs
            else:
                prompt = _directive_for_build_task(task, cross_cutting, prior_failures)
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
                # The Programmer cannot proceed → Coordinator relay + HALT (Director input needed).
                relay = await _coordinator_relay(db, state, result, on_message)
                question_text = relay if relay is not None else result.question
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
                payload={"verify_reason": prior_failures[-1], "auto_fix_attempt": attempt, "task_id": str(task.id)},
            )
            if on_message is not None:
                await on_message(msg)
            db.execute(update(Feat).where(Feat.id == task.feat_id).values(auto_fix_count=Feat.auto_fix_count + 1))
            db.flush()

        if not task_done:  # auto-fix bound exhausted → task failed → HALT
            db.execute(update(Task).where(Task.id == task.id).values(status="failed"))
            db.flush()
            task_service.recompute_feat_status(db, task.feat_id)
            # Coordinator relays the failure to the Director (hub-and-spoke; §3).
            await invoke_agent_with_parse_retry(
                db,
                version_id=version_id,
                role="coordinator",
                stage="build",
                prompt=(
                    f"Úloha #{task.number} '{task.title}' zlyhala po {_AUTO_FIX_RETRIES} auto-fix pokusoch. "
                    f"Posledný dôvod: {prior_failures[-1]}. Priprav pre Directora relay — čo treba rozhodnúť "
                    "(vrátiť na prepracovanie / konzultovať). Ukonči <<<PIPELINE_STATUS>>> blokom (§7.2)."
                ),
                on_event=on_event,
                on_message=on_message,
            )
            state.status = "awaiting_director"
            state.next_action = (
                f"Úloha #{task.number} zlyhala po {_AUTO_FIX_RETRIES} pokusoch — Director: vrátiť / konzultovať."
            )
            db.flush()
            return state
        # task done → continue the loop to the next todo task (no Director click; §6)


def _next_stage(stage: str) -> str:
    idx = STAGE_ORDER.index(stage)
    return STAGE_ORDER[min(idx + 1, len(STAGE_ORDER) - 1)]


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
        if flow_type not in ("new_version", "cr", "bug"):
            raise OrchestratorError(f"Invalid flow_type: {flow_type!r}")
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
            content="Spustenie pipeline.",
            payload={"flow_type": flow_type},
        )
        _begin_dispatch(db, state)
        return state

    if state is None:
        raise OrchestratorError("Pipeline not started for this version")

    # Status guard (CR-NS-018): never act on / advance past an agent that is still
    # working. The advancing actions need a settled agent (awaiting_director or a
    # blocked ratify-out-of-a-question); answer needs an actual question (blocked);
    # pause is only meaningful while the agent works.
    if action in _ADVANCING_ACTIONS and state.status not in ("awaiting_director", "blocked"):
        raise OrchestratorError("Agent ešte pracuje — počkaj na jeho výstup")
    if action == "answer" and state.status != "blocked":
        raise OrchestratorError("Agent sa na nič nepýta — odpoveď nie je na mieste")
    if action == "pause" and state.status != "agent_working":
        raise OrchestratorError("Pauza je možná len počas práce agenta")

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
                state.current_stage = _next_stage("gate_e")  # → task_plan
                db.flush()
                _begin_dispatch(db, state)
            else:
                _begin_dispatch(db, state)  # next topic — stage unchanged
            return state
        # Build (F-007 §6): the final sign-off advances build → gate_g, but a failed /
        # unverified task blocks the close (deterministic gate from the orchestrator's record).
        if state.current_stage == "build" and _build_open_findings(db, version_id) > 0:
            raise OrchestratorError("Otvorené úlohy (failed/neoverené) blokujú uzavretie buildu — najprv ich vyrieš")
        state.current_stage = _next_stage(state.current_stage)
        db.flush()
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
        if STAGE_ACTOR.get(state.current_stage) is None:
            raise OrchestratorError("Aktuálna fáza nemá agenta na re-dispatch")
        # Audit only; the Coordinator's report is threaded as the re-dispatch
        # directive by ``dispatch_directive`` (route). Stage does NOT advance.
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
            entry = payload.get("entry_stage", "gate_a")
            if entry not in STAGE_ORDER:
                raise OrchestratorError(f"Invalid entry_stage: {entry!r}")
            state.is_regate = True
            state.iteration += 1
            state.current_stage = entry
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
            content="UAT accepted — pipeline done (prod-deploy hook deferred to Phase 5).",
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
        state.current_stage = _next_stage("gate_e")  # → task_plan
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
        state.current_stage = _next_stage("build")  # → gate_g
        db.flush()
        _begin_dispatch(db, state)
        return state

    # action == "pause"
    state.next_action = f"Pozastavené Directorom (fáza '{state.current_stage}')."
    db.flush()
    return state
