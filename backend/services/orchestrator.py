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
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.orchestrator import OrchestratorSession
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import claude_agent
from backend.services.claude_agent import ClaudeAgentError, invoke_claude
from backend.services.pipeline_status import ParseFailure, PipelineStatusBlock, parse_status_block

logger = logging.getLogger(__name__)

# Ordered stages and the agent responsible for each (F-007 §3.1).
STAGE_ORDER: tuple[str, ...] = (
    "kickoff",
    "gate_a",
    "gate_b",
    "gate_c",
    "gate_d",
    "gate_e",
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
    "build": "implementer",
    "gate_g": "auditor",
    "release": "coordinator",
}
_VERIFY_RETRIES = 2
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
        "pause",
    }
)
# Actions that act on / advance past an agent's output — only valid once the
# agent has settled (CR-NS-018). Guarding these stops a stale board / double-click
# from advancing while the agent is mid-work (which skipped a mandatory gate).
_ADVANCING_ACTIONS = frozenset(
    {"approve", "apply_coordinator_recommendation", "fix", "leave", "verdict", "uat_accept", "return", "end_gate_e"}
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


def _gate_e_open_findings(report: Optional[PipelineMessage]) -> list[str]:
    """Open (unresolved) findings at the latest Gate E boundary — empty = clean.

    Contract (F-007-gate-e §4): at a boundary the Customer's ``findings`` list = the
    findings that remain UNRESOLVED (Designer-explained/fixed or Director-decided
    ones are dropped). A non-empty list blocks closing Gate E (final or early-end).
    """
    if report is None or not report.payload:
        return []
    return list(report.payload.get("findings") or [])


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
    # Gate E (F-007-gate-e revised §2): every continue goes to the Customer for the
    # next question (or next okruh — the Customer tracks coverage). A final approve has
    # already advanced to build, so stage != gate_e and this does not fire.
    if action in ("approve", "leave") and stage == "gate_e":
        return (
            "Director schválil — pokračuj v previerke Gate E ďalšou otázkou "
            "(alebo ďalším okruhom). Ukonči <<<PIPELINE_STATUS>>> blokom (§7.2)."
        )
    # Branch B fix: the edit instruction is Coordinator-relayed (Director never writes
    # to the Designer directly, §2) — composed from the proposed fix + the Coordinator's
    # recommendation. Delivered to the Designer, who edits NOW (designer_edit dispatch).
    if action == "fix" and stage == "gate_e":
        ans = _latest_designer_answer(db, version_id)
        proposed = (ans.payload.get("proposed_fix") if ans and ans.payload else None) or "(návrh chýba)"
        recommendation = _latest_coordinator_message_content(db, version_id) or "(bez poznámky)"
        return (
            "Koordinátor odovzdáva Directorom schválený pokyn na opravu. Uprav návrh podľa "
            f"schváleného riešenia: {proposed}. Odporúčanie Koordinátora: {recommendation}. "
            "Ukonči <<<PIPELINE_STATUS>>> blokom (§7.2)."
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
) -> PipelineStatusBlock | ParseFailure:
    """Drive one agent turn headless and record its message.

    Resolves the ``(project, role)`` claude session, invokes claude, parses the
    status block, and appends a ``pipeline_message``. On a claude error or a
    parse failure, records a ``system`` escalation message and returns the
    ``ParseFailure``. Does **not** mutate ``pipeline_state`` (the caller owns it).

    ``timeout`` overrides the per-invocation backstop; ``None`` → the per-stage
    default (:func:`_timeout_for`).
    """
    slug = _project_slug_for_version(db, version_id)
    session_id, is_first = _resolve_orch_session(db, slug, role)
    charter_path: Optional[Path] = None
    if is_first:
        charter_path = claude_agent.PROJECTS_ROOT / slug / ".claude" / "agents" / role / "CLAUDE.md"

    try:
        stdout = await invoke_claude(
            project_slug=slug,
            claude_session_id=session_id,
            prompt=prompt,
            charter_path=charter_path,
            timeout=timeout if timeout is not None else _timeout_for(stage),
            on_event=on_event,
        )
    except ClaudeAgentError as exc:
        _record_message(
            db,
            version_id=version_id,
            stage=stage,
            author="system",
            recipient="director",
            kind="notification",
            content=f"Agent '{role}' invocation failed: {exc}",
            payload={"error": str(exc)},
        )
        return ParseFailure(f"claude invocation failed: {exc}")

    parsed = parse_status_block(stdout)
    if isinstance(parsed, ParseFailure):
        _record_message(
            db,
            version_id=version_id,
            stage=stage,
            author="system",
            recipient="director",
            kind="notification",
            content=f"Status block parse failed for '{role}': {parsed.reason}",
            payload={"parse_error": parsed.reason},
        )
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
    _record_message(
        db,
        version_id=version_id,
        stage=stage,
        author=role,
        recipient="director",
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
        },
    )
    return parsed


async def invoke_agent_with_parse_retry(
    db: Session,
    *,
    version_id: uuid.UUID,
    role: str,
    stage: str,
    prompt: str,
    on_event: Optional[claude_agent.EventCallback] = None,
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
    result = await invoke_agent(db, version_id=version_id, role=role, stage=stage, prompt=prompt, on_event=on_event)
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
        )
    return result


# ---------------------------------------------------------------------------
# Verify hooks (F-007 §5.4)
# ---------------------------------------------------------------------------


def verify_mechanical(slug: str, block: PipelineStatusBlock) -> Optional[str]:
    """Deterministic backend checks. Returns a failure reason or ``None`` (pass).

    Every ``commits[]`` hash must exist in the project repo (``git show``) and
    every ``deliverables[]`` path must exist on disk. No agent involved.
    """
    project_root = claude_agent.PROJECTS_ROOT / slug
    for commit in block.commits:
        if not _commit_exists(project_root, commit):
            return f"commit {commit!r} not found in {slug}"
    for rel in block.deliverables:
        if not (project_root / rel).exists():
            return f"deliverable {rel!r} missing on disk"
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


async def verify_done(db: Session, version_id: uuid.UUID, block: PipelineStatusBlock) -> Optional[str]:
    """Verify a gate_report before awaiting the Director. Reason on FAIL, else None.

    Mechanical checks first (deterministic); then a judgment check by invoking
    the coordinator agent. The coordinator's block must report ``kind != blocked``
    and ``awaiting='director'`` to count as a PASS.
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
    )
    if isinstance(judgment, ParseFailure):
        return f"coordinator verify unparseable: {judgment.reason}"
    if judgment.kind == "blocked":
        return f"coordinator flagged: {judgment.question or judgment.summary}"
    return None


async def _coordinator_relay(db: Session, state: PipelineState, worker_block: PipelineStatusBlock) -> Optional[str]:
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
    designer_edit: bool = False,
) -> Optional[PipelineState]:
    """Run the working agent for a version and settle its status (background).

    ``designer_edit`` (Gate E Branch B ``fix``): the directive is the Coordinator-
    relayed edit instruction — the Designer edits first, then the round continues to
    the next Customer question (F-007-gate-e §2).

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
        return await _run_gate_e_round(db, state, on_event=on_event, directive=directive, designer_edit=designer_edit)

    prompt = directive if directive is not None else _directive_for(stage)
    result = await invoke_agent_with_parse_retry(
        db, version_id=state.version_id, role=actor, stage=stage, prompt=prompt, on_event=on_event
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
        relay = await _coordinator_relay(db, state, result) if actor != "coordinator" else None
        question_text = relay if relay is not None else result.question
        state.status = "blocked"
        state.next_action = f"Agent '{actor}' sa pýta: {question_text}"
        db.flush()
        return state

    if result.kind == "gate_report":
        reason = await _verify_with_retries(db, state, result)
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


async def _coordinator_review_gap(db: Session, state: PipelineState, designer_block: PipelineStatusBlock) -> None:
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
    )


async def _run_gate_e_round(
    db: Session,
    state: PipelineState,
    *,
    on_event: Optional[claude_agent.EventCallback] = None,
    directive: Optional[str] = None,
    designer_edit: bool = False,
) -> PipelineState:
    """One Gate E per-question exchange (F-007-gate-e revised §2): Director-gated.

    Hub-and-spoke, **one question at a time** — never chains the next question without
    the Director. Flow per re-dispatch:

    * ``designer_edit`` (Branch B ``fix``): the Designer first edits per the
      Coordinator-relayed directive, then the round continues to the next question.
    * One Customer turn: ``gate_report``+``topic_done`` → round boundary
      (``awaiting_director``); a ``question`` → one Designer answer (no-edit:
      explain / on a gap only PROPOSE) → if ``gap_found`` the Coordinator reviews the
      proposal (Branch B upward leg) → **STOP** (``awaiting_director``).

    Each turn is a ``pipeline_message`` (stage=gate_e, ``seq``-ordered). Only the first
    turn streams ``on_event``. Parse failure → ``blocked`` (never guess).
    """
    stream = on_event
    if designer_edit:  # Branch B: the Designer applies the approved fix, then we continue
        edit = await invoke_agent_with_parse_retry(
            db, version_id=state.version_id, role="designer", stage="gate_e", prompt=directive, on_event=stream
        )
        stream = None
        if isinstance(edit, ParseFailure):
            return _block_failed(state, db, edit.reason)
        customer_prompt = _directive_for("gate_e")
    else:
        customer_prompt = directive if directive is not None else _directive_for("gate_e")

    cust = await invoke_agent_with_parse_retry(
        db, version_id=state.version_id, role="customer", stage="gate_e", prompt=customer_prompt, on_event=stream
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
        )
        if isinstance(designer, ParseFailure):
            return _block_failed(state, db, designer.reason)
        state.status = "awaiting_director"
        if designer.gap_found:  # Branch B upward leg — Coordinator reviews before the Director
            await _coordinator_review_gap(db, state, designer)
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


async def _verify_with_retries(db: Session, state: PipelineState, block: PipelineStatusBlock) -> Optional[str]:
    """Verify; on failure auto-return to the agent up to ``_VERIFY_RETRIES`` times."""
    reason = await verify_done(db, state.version_id, block)
    attempts = 0
    while reason is not None and attempts < _VERIFY_RETRIES:
        attempts += 1
        _record_message(
            db,
            version_id=state.version_id,
            stage=state.current_stage,
            author="system",
            recipient=state.current_actor,
            kind="return",
            content=f"Auto-return (verify {attempts}/{_VERIFY_RETRIES}): {reason}",
            payload={"verify_reason": reason},
        )
        retry = await invoke_agent(
            db,
            version_id=state.version_id,
            role=state.current_actor,
            stage=state.current_stage,
            prompt=f"Verify zlyhal: {reason}. Oprav a znovu ukonči <<<PIPELINE_STATUS>>> blokom (§7.2).",
        )
        if isinstance(retry, ParseFailure) or retry.kind != "gate_report":
            return reason  # give up on non-report → caller escalates
        block = retry
        reason = await verify_done(db, state.version_id, block)
    return reason


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
        # no open finding) signs off → build. An open finding blocks the final close.
        if state.current_stage == "gate_e":
            report = _latest_customer_gate_report(db, version_id)
            if _gate_e_coverage_complete(report):
                open_findings = _gate_e_open_findings(report)
                if open_findings:
                    raise OrchestratorError("Otvorené nálezy blokujú uzavretie Gate E — najprv ich vyrieš")
                _write_gate_e_audit(db, version_id)  # §4 audit record before closing
                state.current_stage = _next_stage("gate_e")  # → build
                db.flush()
                _begin_dispatch(db, state)
            else:
                _begin_dispatch(db, state)  # next topic — stage unchanged
            return state
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
        _record_message(
            db,
            version_id=version_id,
            stage=state.current_stage,
            author="director",
            recipient=state.current_actor,
            kind="return",
            content=str(comment),
        )
        _begin_dispatch(db, state)
        return state

    if action == "ask":
        text = payload.get("text")
        if not text or not str(text).strip():
            raise OrchestratorError("ask requires a non-empty payload.text")
        _record_message(
            db,
            version_id=version_id,
            stage=state.current_stage,
            author="director",
            recipient=state.current_actor,
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
        open_findings = _gate_e_open_findings(_latest_customer_gate_report(db, version_id))
        if open_findings:
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
        state.current_stage = _next_stage("gate_e")  # → build
        db.flush()
        _begin_dispatch(db, state)
        return state

    # action == "pause"
    state.next_action = f"Pozastavené Directorom (fáza '{state.current_stage}')."
    db.flush()
    return state
