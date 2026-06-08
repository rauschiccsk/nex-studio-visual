"""Deterministic parser for the agent status block (F-007 §5.3, CR-NS-018 Phase 2).

Every orchestrated agent response ends with a machine-readable block::

    <<<PIPELINE_STATUS>>>
    { "stage": "...", "kind": "...", "summary": "...", "awaiting": "...",
      "deliverables": [...], "commits": [...], "question": "..." }
    <<<END_PIPELINE_STATUS>>>

The parser is **deterministic** — any deviation (missing/duplicate fence,
invalid JSON, schema/enum violation, ``question``-required-but-absent) returns
a :class:`ParseFailure`. The orchestrator maps that to ``status=blocked`` +
escalation and **never guesses** (F-007 §5.3, §11.3).

Charter §5.3 contract (per Dedo 2026-06-03):
* ``recipient`` is NOT emitted by agents — derived by the orchestrator. Any
  extra field is ignored, not required.
* ``kind=blocked`` carries the blocker in ``question`` (authoritative);
  ``summary`` is human context.
* ``commits`` / ``deliverables`` may be omitted or empty — default to ``[]``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.schemas.task import TaskPriority, TaskType

_FENCE_RE = re.compile(
    r"<<<PIPELINE_STATUS>>>\s*(.*?)\s*<<<END_PIPELINE_STATUS>>>",
    re.DOTALL,
)

#: Stages an agent may report (F-007 §3.1).
STAGES = frozenset(
    {
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
    }
)
#: Kinds an *agent* may emit in a status block (subset of pipeline_message.kind;
#: directive/approval/return/verdict/notification are orchestrator/director-authored).
BLOCK_KINDS = frozenset({"kickoff", "question", "answer", "gate_report", "done", "blocked"})
_AWAITING = frozenset({"director", "none"})
_QUESTION_KINDS = frozenset({"question", "blocked"})


# ── task_plan decomposition (F-007 §4/§5, CR-NS-020 CR-2) ────────────────────
# The Designer emits the EPIC→FEAT→TASK breakdown of the final design as a typed
# tree on the status block (NOT a free-form payload — PipelineStatusBlock ignores
# extras, so the contract must be declared). Numbers are NOT emitted (the
# epic/feat/task services auto-assign MAX+1); status is NOT emitted (the write-path
# forces planned/todo — the Designer never pre-marks anything done).


class TaskPlanTask(BaseModel):
    """One coarse task (module = task, §4) under a feat."""

    title: str = Field(min_length=1, max_length=500)
    task_type: TaskType
    description: str = ""
    checklist_type: Optional[str] = Field(default=None, max_length=30)
    priority: TaskPriority = "normal"
    estimated_minutes: Optional[int] = None


class TaskPlanFeat(BaseModel):
    """A feat groups ≥1 task."""

    title: str = Field(min_length=1, max_length=500)
    description: str = ""
    estimated_minutes: Optional[int] = None
    tasks: list[TaskPlanTask] = Field(min_length=1)


class TaskPlanEpic(BaseModel):
    """An epic groups ≥1 feat. ``module_id`` is optional (project-level when null)."""

    title: str = Field(min_length=1, max_length=500)
    module_id: Optional[str] = None
    feats: list[TaskPlanFeat] = Field(min_length=1)


class TaskPlan(BaseModel):
    """The full decomposition the orchestrator materializes into Epic/Feat/Task rows."""

    epics: list[TaskPlanEpic] = Field(min_length=1)


class PipelineStatusBlock(BaseModel):
    """Validated agent status block. ``extra='ignore'`` drops derived fields."""

    model_config = ConfigDict(extra="ignore")

    stage: str
    kind: str
    summary: str
    awaiting: str
    deliverables: list[str] = Field(default_factory=list)
    commits: list[str] = Field(default_factory=list)
    question: Optional[str] = None

    # task_plan decomposition (F-007 §4/§5, CR-NS-020 CR-2). Only the Designer at
    # stage=task_plan emits these; other stages leave them unset.
    #: Structured EPIC→FEAT→TASK tree the orchestrator write-path materializes.
    plan: Optional[TaskPlan] = None
    #: Cross-cutting regulated-ledger invariants (markdown), codified once by the
    #: Designer; CR-3 re-reads this from the gate_report payload and injects it into
    #: every per-task build brief.
    cross_cutting_rules: Optional[str] = None
    #: Per-task Auditor verdict (F-007 §6, CR-NS-020 CR-4). Only the Auditor's build-stage
    #: audit turn emits it; ``None`` (absent) is treated as FAIL by ``_verify_task``
    #: (fail-closed — a task never passes without an explicit ``task_pass=true``). The
    #: per-task audit findings ride in the reused ``findings`` field below.
    task_pass: Optional[bool] = None

    # Gate E signals (F-007-gate-e §5/§7.2, CR-NS-018). All optional; only the
    # Customer↔Designer loop (stage=gate_e) emits them, so non-gate-E blocks are
    # unaffected. The Customer/Designer charters §7.2 are aligned to exactly these.
    #: Which of the 7 review okruhov this block concerns (Customer).
    topic: Optional[str] = None
    #: Customer signals the current okruh is finished → round boundary (with kind=gate_report).
    topic_done: bool = False
    #: All 7 okruhy covered → final boundary; the Director's approve advances to task_plan (Customer).
    coverage_complete: bool = False
    #: Structured findings for the Director's boundary view (alongside ``summary``).
    findings: list[str] = Field(default_factory=list)
    #: Designer answer (revised flow): a gap was found → Branch B (propose-only, no edit).
    gap_found: bool = False
    #: Designer's proposed fix TEXT when ``gap_found`` — never an edit (edit happens only
    #: on a Director-approved, Coordinator-relayed ``fix`` directive).
    proposed_fix: Optional[str] = None


@dataclass(frozen=True)
class ParseFailure:
    """A status block that could not be parsed deterministically."""

    reason: str


ParseResult = Union[PipelineStatusBlock, ParseFailure]


def parse_status_block(stdout: str) -> ParseResult:
    """Parse the single PIPELINE_STATUS block from an agent's stdout.

    Returns the validated :class:`PipelineStatusBlock` or a
    :class:`ParseFailure` describing why parsing failed. Never raises, never
    infers missing data.
    """
    matches = _FENCE_RE.findall(stdout or "")
    if not matches:
        return ParseFailure("no PIPELINE_STATUS block found")
    if len(matches) > 1:
        return ParseFailure(f"expected exactly one PIPELINE_STATUS block, found {len(matches)}")

    try:
        data = json.loads(matches[0])
    except ValueError as exc:
        return ParseFailure(f"status block is not valid JSON: {exc}")
    if not isinstance(data, dict):
        return ParseFailure("status block JSON is not an object")

    try:
        block = PipelineStatusBlock.model_validate(data)
    except ValidationError as exc:
        return ParseFailure(f"status block schema invalid: {exc.errors(include_url=False)}")

    if block.stage not in STAGES:
        return ParseFailure(f"unknown stage {block.stage!r}")
    if block.kind not in BLOCK_KINDS:
        return ParseFailure(f"unknown kind {block.kind!r}")
    if block.awaiting not in _AWAITING:
        return ParseFailure(f"unknown awaiting {block.awaiting!r}")
    if block.kind in _QUESTION_KINDS and not (block.question and block.question.strip()):
        return ParseFailure(f"kind={block.kind!r} requires a non-empty 'question'")
    # task_plan close (F-007 §5, CR-NS-020 CR-2): the Designer's gate_report must carry the
    # decomposition. A question/blocked turn is still allowed (re-plan dialogue); only the
    # gate_report — the turn that closes the stage — requires a non-empty 'plan'.
    if block.stage == "task_plan" and block.kind == "gate_report" and (block.plan is None or not block.plan.epics):
        return ParseFailure("task_plan gate_report requires a non-empty 'plan' (EPIC→FEAT→TASK)")

    return block
