"""Deterministic parser for the agent status block (F-007 §5.3, CR-NS-018 Phase 2; v2 CR-V2-006).

Every orchestrated agent (the AI Agent or the Auditor — v2's two roles) ends a
response with a machine-readable block (OQ-10: this block SURVIVES into v2; we do
NOT infer phase/await from live PTY text)::

    <<<PIPELINE_STATUS>>>
    { "stage": "...", "kind": "...", "summary": "...", "awaiting": "...",
      "deliverables": [...], "commits": [...], "question": "..." }
    <<<END_PIPELINE_STATUS>>>

The parser is **deterministic** — any deviation (missing/duplicate fence,
invalid JSON, schema/enum violation, ``question``-required-but-absent) returns
a :class:`ParseFailure`. The orchestrator maps that to ``status=blocked`` +
escalation and **never guesses** (F-007 §5.3, §11.3).

``stage`` is one of the **4 v2 phases** (Príprava → Návrh → Programovanie →
Verifikácia) + ``done`` (CR-V2-006, matching the DB ``STAGE_VALUES`` tuple); the
v1 11-stage gate path (``gate_a``…``gate_g``/``release``) is gone.

Charter §5.3 contract (per Dedo 2026-06-03):
* ``recipient`` is NOT emitted by agents — derived by the orchestrator. Any
  extra field is ignored, not required.
* ``kind=blocked`` carries the blocker in ``question`` (authoritative);
  ``summary`` is human context.
* ``commits`` / ``deliverables`` may be omitted or empty — default to ``[]``.
* ``awaiting`` is one of ``{manazer, none}`` (the operator was renamed
  Director → Manažér in CR-V2-004).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from backend.schemas.task import TaskPriority, TaskType

_FENCE_RE = re.compile(
    r"<<<PIPELINE_STATUS>>>\s*(.*?)\s*<<<END_PIPELINE_STATUS>>>",
    re.DOTALL,
)

# (v0.7.3 CR-1) The narrowed task_plan passes carry their JSON in a DEDICATED sentinel fence — distinct
# from the PIPELINE_STATUS fence so a plan pass can never be mistaken for a status block. ``--json-schema``
# does NOT return a ``structured_output`` field in this CLI (live root-cause 2026-06-18) — the model emits
# the narrowed JSON as TEXT, so :func:`extract_task_plan_json` pulls it out the same way
# :func:`parse_status_block` pulls the status block out of ``invoke_agent``'s stdout.
_TASK_PLAN_FENCE_RE = re.compile(
    r"<<<TASK_PLAN_JSON>>>\s*(.*?)\s*<<<END_TASK_PLAN_JSON>>>",
    re.DOTALL,
)
#: Tolerate the model wrapping the sentinel-fenced JSON in an inner markdown ```json … ``` block.
_MD_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)

#: Phases an agent may report (CR-V2-006 — the 4 v2 phases + ``done``; matches the DB
#: ``STAGE_VALUES`` tuple in ``backend/db/models/pipeline.py``, the single source). The v1
#: 11-stage gate path (kickoff/gate_a…gate_g/release/task_plan/build) is removed.
STAGES = frozenset(
    {
        "priprava",
        "navrh",
        "programovanie",
        "verifikacia",
        "done",
    }
)
#: Kinds an *agent* may emit in a status block (subset of pipeline_message.kind;
#: directive/approval/return/notification are orchestrator/Manažér-authored). ``verdict`` is
#: the Auditor's Verifikácia/upfront-review verdict (CR-V2-006 repurposes the findings shape).
BLOCK_KINDS = frozenset({"question", "answer", "gate_report", "verdict", "done", "blocked"})
#: ``awaiting`` targets (CR-V2-004: Director → Manažér). The agent either hands back to the
#: operator (``manazer``) or signals it keeps the turn (``none``).
_AWAITING = frozenset({"manazer", "none"})
_QUESTION_KINDS = frozenset({"question", "blocked"})


# ── task_plan decomposition (F-007 §4/§5, CR-NS-020 CR-2; v2 CR-V2-011) ──────
# The AI Agent emits the EPIC→FEAT→TASK breakdown of the final design as a typed
# tree on the status block (NOT a free-form payload — PipelineStatusBlock ignores
# extras, so the contract must be declared). Numbers are NOT emitted (the
# epic/feat/task services auto-assign MAX+1); status is NOT emitted (the write-path
# forces planned/todo — the AI Agent never pre-marks anything done).


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
    """An epic groups ≥1 feat (always project-level — multi-module removed in v2)."""

    title: str = Field(min_length=1, max_length=500)
    feats: list[TaskPlanFeat] = Field(min_length=1)


class TaskPlan(BaseModel):
    """The full decomposition the orchestrator materializes into Epic/Feat/Task rows."""

    epics: list[TaskPlanEpic] = Field(min_length=1)


# ── (v0.7.3) incremental task_plan generation — narrowed per-pass schemas (CR-1) ──
# A large design's full EPIC→FEAT→TASK tree overflows ONE structured-output turn (the
# model drops the per-feat tasks → ``parse_exhaustion``). The AI Agent instead emits the
# plan in bounded passes: a skeleton (EPIC + FEAT, NO tasks) then one pass per feat (that
# feat's tasks). These narrowed models constrain each pass; the orchestrator
# accumulates them into ONE full :class:`TaskPlan` (above) and writes it via the existing
# task-plan write-path (the incremental passes fold into the Návrh phase — CR-V2-011). They
# are SEPARATE types — the full-plan
# models are deliberately NOT relaxed (F-007 §9 "schéma nemení"); ``TaskPlanFeat.tasks``
# keeps ``min_length=1`` so the assembled plan is always non-empty.


class TaskPlanSkeletonFeat(BaseModel):
    """A feat in the skeleton pass — title/description/estimated_minutes, **NO** tasks
    (tasks arrive in the per-feat passes)."""

    title: str = Field(min_length=1, max_length=500)
    description: str = ""
    estimated_minutes: Optional[int] = None


class TaskPlanSkeletonEpic(BaseModel):
    """An epic in the skeleton pass — title + ≥1 (task-less) feat."""

    title: str = Field(min_length=1, max_length=500)
    feats: list[TaskPlanSkeletonFeat] = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _accept_features_alias(cls, data: Any) -> Any:
        """Tolerate the most likely real-claude drift: the model emits ``features`` instead of ``feats``
        (observed in the live root-cause repro 2026-06-18). Normalise it to ``feats`` before validation;
        unknown keys (``id``/``project``/…) are dropped by the model's default ``extra='ignore'``."""
        if isinstance(data, dict) and "feats" not in data and "features" in data:
            return {**data, "feats": data["features"]}
        return data


class TaskPlanSkeleton(BaseModel):
    """Pass 1: the EPIC + FEAT skeleton (no tasks) + the cross-cutting rules, codified once.
    The per-feat passes fill in each feat's tasks; the orchestrator assembles the full plan."""

    epics: list[TaskPlanSkeletonEpic] = Field(min_length=1)
    cross_cutting_rules: Optional[str] = None


class TaskPlanFeatTasks(BaseModel):
    """Passes 2..N: ONLY one feat's tasks (≥1). Reuses :class:`TaskPlanTask` so the per-task
    contract is identical to the full plan; the orchestrator grafts these onto the matching
    skeleton feat."""

    tasks: list[TaskPlanTask] = Field(min_length=1)


#: Narrowed JSON Schemas for the two task_plan passes (v0.7.3, CR-1). Derived from the models
#: (single source). Used ONLY by the dedicated ``_invoke_plan_pass`` helper;
#: :data:`PIPELINE_STATUS_JSON_SCHEMA` stays the default for every other agent invocation
#: (byte-identical — ``invoke_agent`` is untouched).
TASK_PLAN_SKELETON_JSON_SCHEMA = TaskPlanSkeleton.model_json_schema()
TASK_PLAN_FEAT_TASKS_JSON_SCHEMA = TaskPlanFeatTasks.model_json_schema()


class PipelineStatusBlock(BaseModel):
    """Validated agent status block. ``extra='ignore'`` drops derived fields.

    In v2 (CR-V2-006) only TWO roles emit this block — the **AI Agent** (Príprava/Návrh/
    Programovanie turns) and the **Auditor** (the upfront design review + the end Verifikácia
    check). The v1 Coordinator-relay (``coordinator_directive``), per-task audit (``task_pass``)
    and Gate-E Customer↔Designer signals (``topic``/``topic_done``/``coverage_complete``/
    ``gap_found``) are removed; the **Auditor verdict** repurposes the ``findings``/``proposed_fix``
    shape (see the Auditor-verdict block below)."""

    model_config = ConfigDict(extra="ignore")

    stage: str
    kind: str
    summary: str
    awaiting: str
    deliverables: list[str] = Field(default_factory=list)
    commits: list[str] = Field(default_factory=list)
    question: Optional[str] = None

    # task_plan decomposition (F-007 §4/§5, CR-NS-020 CR-2; v2: folds into the Návrh phase —
    # CR-V2-011). The AI Agent emits the EPIC→FEAT→TASK tree as the last part of the Návrh design
    # doc; other phases leave it unset.
    #: Structured EPIC→FEAT→TASK tree the orchestrator write-path materializes.
    plan: Optional[TaskPlan] = None
    #: Cross-cutting invariants (markdown) codified once with the plan; re-read from the
    #: gate_report payload and injected into every per-task build brief.
    cross_cutting_rules: Optional[str] = None

    # ── Auditor verdict (CR-V2-006 — repurposes the v1 Gate-E findings/proposed_fix shape) ──
    # The Auditor is v2's independent verifier with two touchpoints: an UPFRONT design/spec
    # review (after Návrh — replaces the Gate-E Customer function) and the END Verifikácia check
    # (replaces gate_g). Both emit ``kind="verdict"`` carrying these fields; the verdict + findings
    # fill the Verifikácia tab as a durable record (design §4.4.2). Absent on AI-Agent turns.
    #: PASS/FAIL of the Auditor's review. ``None`` on a non-verdict block. ``False`` (or absent on a
    #: verdict turn) is treated as FAIL by the verifier (fail-closed — nothing passes without an
    #: explicit ``verdict=true``).
    verdict: Optional[bool] = None
    #: Structured findings for the Manažér's review view (alongside ``summary``) — the holes /
    #: ambiguities / contradictions (upfront) or behavioural / security / contract failures (end check).
    findings: list[str] = Field(default_factory=list)
    #: The Auditor's proposed fix scope TEXT when the verdict is FAIL — the targeted scope the AI Agent
    #: re-runs in the bounded fix↔re-verify loop (CR-V2-014). Never an edit by the Auditor itself
    #: (independence); ``None`` on a PASS verdict.
    proposed_fix: Optional[str] = None


@dataclass(frozen=True)
class ParseFailure:
    """A status block that could not be parsed deterministically.

    ``usage`` / ``timing`` (WS-D, CR-NS-036) carry the FAILED turn's accumulated token usage +
    dispatch timing. A turn that never parses produces no agent ``PipelineMessage`` of its own, so the
    orchestrator attaches its metrics here (via :func:`dataclasses.replace`) and the terminal
    escalation that records a Director-facing message folds them in — otherwise those tokens would be
    lost from :func:`pipeline_metrics.aggregate_pipeline_usage`. Both ``None`` when no usage was
    captured (never fabricated)."""

    reason: str
    usage: Optional[dict[str, Any]] = None
    timing: Optional[dict[str, Any]] = None
    #: R1-c (v0.7.0 dispatch resilience): set when this failure is an agent envelope-loss (a
    #: ``ClaudeAgentError`` — timeout / crash) for which the engine ran the ``baseline..HEAD`` commit
    #: audit. Carries ``{dispatch_baseline_sha, post_timeout_head_sha, timeout_seconds,
    #: detected_commit_count, next_action}`` so ``run_dispatch`` settles to ``awaiting_director`` with a
    #: "work may have landed — review & continue" next_action instead of a bare ``blocked`` relay. ``None``
    #: for an ordinary parse failure (no dispatch baseline to audit against).
    lost_work: Optional[dict[str, Any]] = None


ParseResult = Union[PipelineStatusBlock, ParseFailure]


def _format_validation_errors(exc: ValidationError) -> str:
    """Render a Pydantic ValidationError as compact ``loc: msg`` entries naming the exact field + index
    (WS-B3, CR-NS-029), e.g. ``plan.epics[0].feats[1].tasks[2].task_type: Field required`` — so the
    parse-retry re-prompt tells the agent EXACTLY which field to fix, instead of dumping the raw error
    array (which caused the multi-round task_type-omission loops)."""
    entries = []
    for err in exc.errors(include_url=False):
        loc = ""
        for part in err["loc"]:
            if isinstance(part, int):
                loc += f"[{part}]"
            else:
                loc += f".{part}" if loc else str(part)
        entries.append(f"{loc}: {err['msg']}")
    return "; ".join(entries)


#: JSON Schema for the agent status block (R3, v0.7.0). Derived from the Pydantic model — the model
#: IS the schema (single source, no hand-written drift). Passed to ``claude --json-schema`` so the
#: runtime grammar-constrains the agent's output to a conforming object (returned in the envelope's
#: ``structured_output`` field), making a malformed block impossible at the source. The imperative
#: enum/cross-field checks below (STAGES / BLOCK_KINDS / question-required / navrh-plan) are NOT
#: expressible as the model's plain ``str`` fields, so :func:`_validate_block` still enforces them on
#: BOTH transports — the schema is the first line of defense, not the only one.
PIPELINE_STATUS_JSON_SCHEMA = PipelineStatusBlock.model_json_schema()


def _validate_block(data: dict) -> ParseResult:
    """Validate a status-block dict through :class:`PipelineStatusBlock` + the imperative
    enum/cross-field rules. The SINGLE validation path shared by the fence transport
    (:func:`parse_status_block`) and the structured-output transport (:func:`parse_structured_output`)
    so both enforce IDENTICAL content rules (R3 D1: the content contract is transport-agnostic).
    Returns the validated block or a :class:`ParseFailure`; never raises, never infers."""
    try:
        block = PipelineStatusBlock.model_validate(data)
    except ValidationError as exc:
        # WS-B3: name the exact field(s) so the parse-retry re-prompt (orchestrator.py, which
        # interpolates this reason) tells the agent what to fix — not a stringified error array.
        return ParseFailure(f"status block schema invalid — {_format_validation_errors(exc)}")

    if block.stage not in STAGES:
        return ParseFailure(f"unknown stage {block.stage!r}")
    if block.kind not in BLOCK_KINDS:
        return ParseFailure(f"unknown kind {block.kind!r}")
    if block.awaiting not in _AWAITING:
        return ParseFailure(f"unknown awaiting {block.awaiting!r}")
    if block.kind in _QUESTION_KINDS and not (block.question and block.question.strip()):
        return ParseFailure(f"kind={block.kind!r} requires a non-empty 'question'")
    # Návrh close (CR-V2-011 — the task plan folds into the Návrh phase): the AI Agent's Návrh
    # gate_report must carry the EPIC→FEAT→TASK decomposition. A question/blocked turn is still
    # allowed (re-plan dialogue); only the gate_report — the turn that closes the phase — requires
    # a non-empty 'plan'. (The narrowed skeleton/per-feat passes never hit this guard — they emit
    # a TaskPlanSkeleton/TaskPlanFeatTasks object, not a PipelineStatusBlock.)
    if block.stage == "navrh" and block.kind == "gate_report" and (block.plan is None or not block.plan.epics):
        return ParseFailure("navrh gate_report requires a non-empty 'plan' (EPIC→FEAT→TASK)")

    return block


def parse_status_block(stdout: str) -> ParseResult:
    """Parse the single PIPELINE_STATUS block from an agent's stdout (the fence transport).

    R3 (v0.7.0): this is the **fallback** — the orchestrator prefers the grammar-constrained
    ``structured_output`` (:func:`parse_structured_output`) and only parses the fence when no
    structured output was produced (older CLI / no schema) or it failed validation (D2
    defense-in-depth). Returns the validated :class:`PipelineStatusBlock` or a :class:`ParseFailure`
    describing why parsing failed. Never raises, never infers missing data.
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

    return _validate_block(data)


def parse_structured_output(obj: dict) -> ParseResult:
    """Validate a grammar-constrained ``structured_output`` object (R3, v0.7.0 — the PRIMARY transport).

    The agent was invoked with ``--json-schema`` (:data:`PIPELINE_STATUS_JSON_SCHEMA`), so the runtime
    already forced the shape; here we run the SAME :func:`_validate_block` the fence path runs (reusing
    every validator + the enum/cross-field rules), returning a :class:`ParseFailure` on any violation
    exactly like the fence path — so a schema the model couldn't satisfy degrades to the fence
    fallback + the parse-retry, never a silent loss. Never raises, never infers missing data."""
    if not isinstance(obj, dict):
        return ParseFailure("structured_output is not an object")
    return _validate_block(obj)


def extract_report_body(text: str) -> str:
    """Return the agent's human-readable markdown report — everything in its raw output EXCEPT the
    machine sentinel fences (legible-cockpit-output fix).

    Every orchestrated agent writes a full structured report (e.g. the Implementer's ``## Dokončené``
    with bold section headings, numbered/bulleted lists, inline code + code blocks) and THEN the
    ``<<<PIPELINE_STATUS>>>`` status block. :func:`invoke_agent` records only the block's one-line
    ``summary`` as the message ``content`` (the FE ``deriveBrief`` / preview source); the rich report
    body was parsed for the status block and otherwise DISCARDED. This recovers it so the cockpit
    bubble can render the report richly (persisted, additively, as ``payload['report']``).

    Strips BOTH sentinel fences — the ``<<<PIPELINE_STATUS>>>`` status block and, defensively, a
    ``<<<TASK_PLAN_JSON>>>`` fence — wherever they sit, and returns the remaining markdown trimmed.
    Returns ``""`` when the agent emitted nothing but the fence(s). Deterministic; never raises.

    NOTE: deliberately a standalone extractor, NOT a field on :class:`PipelineStatusBlock` — the model
    IS the schema (:data:`PIPELINE_STATUS_JSON_SCHEMA` = ``PipelineStatusBlock.model_json_schema()``),
    so a model field would tell the agent to cram the whole markdown report INTO the status JSON (the
    very monolithic block this fix removes). The body is the surrounding TEXT, not a JSON field."""
    body = _FENCE_RE.sub("", text or "")
    body = _TASK_PLAN_FENCE_RE.sub("", body)
    return body.strip()


# ── (v0.7.3) narrowed task_plan-pass parsers (CR-1) ──────────────────────────
# The narrowed passes emit a :class:`TaskPlanSkeleton` / :class:`TaskPlanFeatTasks` object
# (NOT a :class:`PipelineStatusBlock`), so they do NOT go through ``_validate_block`` and
# therefore never hit the ``stage==navrh`` plan-required guard above (it stays unchanged
# and only fires for the FINAL assembled status block). These parsers mirror
# :func:`parse_structured_output`'s shape: validate the grammar-constrained structured_output
# against the narrowed model, returning the model or a :class:`ParseFailure` (the per-pass
# parse-retry feeds the reason back). Never raise, never infer.


def parse_task_plan_skeleton(obj: dict) -> Union[TaskPlanSkeleton, ParseFailure]:
    """Validate a skeleton pass's structured_output — EPIC + FEAT (no tasks) + cross_cutting_rules."""
    if not isinstance(obj, dict):
        return ParseFailure("task_plan skeleton structured_output is not an object")
    try:
        return TaskPlanSkeleton.model_validate(obj)
    except ValidationError as exc:
        return ParseFailure(f"task_plan skeleton invalid — {_format_validation_errors(exc)}")


def parse_task_plan_feat_tasks(obj: dict) -> Union[TaskPlanFeatTasks, ParseFailure]:
    """Validate a per-feat pass's structured_output — ONLY that feat's tasks (≥1)."""
    if not isinstance(obj, dict):
        return ParseFailure("task_plan feat-tasks structured_output is not an object")
    try:
        return TaskPlanFeatTasks.model_validate(obj)
    except ValidationError as exc:
        return ParseFailure(f"task_plan feat-tasks invalid — {_format_validation_errors(exc)}")


def extract_task_plan_json(text: str) -> Union[dict, ParseFailure]:
    """Pull the narrowed-pass JSON object out of a ``<<<TASK_PLAN_JSON>>>`` sentinel fence in ``text``
    (v0.7.3, CR-1 — the TEXT/fence path the live CLI forces; ``structured_output`` is dead).

    Returns the parsed ``dict`` (handed to :func:`parse_task_plan_skeleton` / :func:`parse_task_plan_feat_tasks`)
    or a :class:`ParseFailure` (missing/duplicate fence, non-JSON, non-object). Tolerates an inner markdown
    ```json … ``` wrapper. Deterministic — never raises, never infers."""
    matches = _TASK_PLAN_FENCE_RE.findall(text or "")
    if not matches:
        return ParseFailure("no <<<TASK_PLAN_JSON>>> fence found")
    if len(matches) > 1:
        return ParseFailure(f"expected exactly one <<<TASK_PLAN_JSON>>> fence, found {len(matches)}")
    raw = matches[0].strip()
    inner = _MD_JSON_FENCE_RE.match(raw)
    if inner:  # the model wrapped the JSON in a ```json … ``` block inside the sentinel
        raw = inner.group(1).strip()
    try:
        obj = json.loads(raw)
    except ValueError as exc:
        return ParseFailure(f"task_plan fence is not valid JSON: {exc}")
    if not isinstance(obj, dict):
        return ParseFailure("task_plan fence JSON is not an object")
    return obj
