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
from typing import Any, Literal, Optional, Union

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
        "vizual",  # CR-1 (nex-studio-visual): the live-preview phase between Návrh and Programovanie
        "programovanie",
        "verifikacia",
        "done",
    }
)
#: Kinds an *agent* may emit in a status block (subset of pipeline_message.kind;
#: directive/approval/return/notification are orchestrator/Manažér-authored). ``verdict`` is
#: the Auditor's Verifikácia/upfront-review verdict (CR-V2-006 repurposes the findings shape).
#: ``framework_issue`` (Director observation #6) is the AI Agent's AGENT-INITIATED escalation to Dedo when a
#: fix requires a change to NEX Studio ITSELF (§15) — the Dedo message rides in ``question`` (like ``blocked``/
#: ``question``); the settle path routes it to ``blocked``/``block_reason='framework_issue'`` + delivery.
BLOCK_KINDS = frozenset(
    {"question", "answer", "gate_report", "verdict", "done", "blocked", "consultation", "framework_issue"}
)
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
    #: STEP 3 (step3-plan-design.md FIX4): the plain-language, jargon-free one-liner for the Plán úloh rail
    #: (distinct from the technical ``description``). Default empty so an omission parses.
    plain_description: str = ""
    checklist_type: Optional[str] = Field(default=None, max_length=30)
    priority: TaskPriority = "normal"
    estimated_minutes: Optional[int] = None


class TaskPlanFeat(BaseModel):
    """A feat groups ≥1 task."""

    title: str = Field(min_length=1, max_length=500)
    description: str = ""
    #: STEP 3 (step3-plan-design.md FIX4): plain-language one-liner for the Plán úloh rail. Default empty.
    plain_description: str = ""
    estimated_minutes: Optional[int] = None
    tasks: list[TaskPlanTask] = Field(min_length=1)


class TaskPlanEpic(BaseModel):
    """An epic groups ≥1 feat (always project-level — multi-module removed in v2)."""

    title: str = Field(min_length=1, max_length=500)
    #: STEP 3 (step3-plan-design.md FIX4): plain-language one-liner — the Epic's ONLY prose (no technical
    #: ``description`` on an Epic). Default empty so an omission parses.
    plain_description: str = ""
    feats: list[TaskPlanFeat] = Field(min_length=1)


class TaskPlan(BaseModel):
    """The full decomposition the orchestrator materializes into Epic/Feat/Task rows."""

    epics: list[TaskPlanEpic] = Field(min_length=1)


# ── Release-coverage declaration (CR-V2-052) ─────────────────────────────────
# The AI Agent declares, at Návrh close, WHAT the release must demonstrate (flagship features) and WHAT it
# must refuse (safety properties). The risk-floored oracle (CR-V2-051) FAILs the acceptance when the smoke
# ran fewer FEATURE assertions than declared flagship features, or fewer NEGATIVE assertions than declared
# safety properties — so DONE means "the spec's promises hold AND its forbidden ops are refused", not "boots".


class SafetyProperty(BaseModel):
    """One safety invariant the app MUST enforce. ``name`` is the property (e.g. "the read_only preset must
    block writes"); ``risky_op`` is the concrete forbidden operation the release oracle requires a NEGATIVE
    assertion for (the op MUST be REJECTED — a green 'it works' test can never prove a safety invariant, only
    a red-when-abused test can)."""

    name: str = Field(min_length=1, max_length=300)
    risky_op: str = Field(min_length=1, max_length=300)


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
    """A feat in the skeleton pass — title/description/plain_description/estimated_minutes, **NO** tasks
    (tasks arrive in the per-feat passes)."""

    title: str = Field(min_length=1, max_length=500)
    description: str = ""
    #: STEP 3 (step3-plan-design.md FIX4): plain-language one-liner for the Plán úloh rail. Default empty.
    plain_description: str = ""
    estimated_minutes: Optional[int] = None


class TaskPlanSkeletonEpic(BaseModel):
    """An epic in the skeleton pass — title + ≥1 (task-less) feat."""

    title: str = Field(min_length=1, max_length=500)
    #: STEP 3 (step3-plan-design.md FIX4): plain-language one-liner — the Epic's ONLY prose. Default empty.
    plain_description: str = ""
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
    #: CR-V2-052: the behaviour-bearing features the release must DEMONSTRATE (≥1 FEATURE assertion each in
    #: release_smoke_test.sh — the risk-floored oracle CR-V2-051). Declared once, with the skeleton.
    flagship_features: list[str] = Field(default_factory=list)
    #: CR-V2-052: the safety invariants the app must ENFORCE (≥1 NEGATIVE assertion each — the risky op MUST
    #: be rejected). The oracle FAILs a build that declares a property but ships no negative test for it.
    safety_properties: list[SafetyProperty] = Field(default_factory=list)


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


# ── Fix-critic (CR-V2-058 Part B) ────────────────────────────────────────────────────────────────
# The independent adversarial critique of the Auditor's ``proposed_fix`` for a Verifikácia FAIL, BEFORE
# that fix becomes the fix task / the Decision Card's recommendation (§5). A NARROWED schema — NOT a
# :class:`PipelineStatusBlock` (whose ``verdict`` is a bool + findings + proposed_fix) — so the critic's
# ``{accept,narrow,reject}`` shape can NEVER collide with :data:`PIPELINE_STATUS_JSON_SCHEMA` (it would
# ParseFail there). Invoked via the dedicated narrowed path (``_invoke_fix_critique``), the sibling of the
# task_plan passes; ``PIPELINE_STATUS_JSON_SCHEMA`` stays the default for every other agent invocation.


class FixCritique(BaseModel):
    """CR-V2-058 Part B — the fix-critic's verdict on the Auditor's proposed Verifikácia-FAIL fix (the CURE,
    NOT the build). ``accept`` = the fix is a real, enforced-by-construction boundary; ``narrow`` = mostly
    right but the scope must be tightened (``corrected_scope`` carries the corrected fix scope); ``reject`` =
    the fix is a FAKE boundary (a git hook an unattended ``bypassPermissions`` fixer evades via
    ``--no-verify``; an advisory/client-side guard) or fixes the symptom not the cause (``why`` = why it is
    wrong / where the root actually is). Only ``accept``/``narrow`` let the Decision Card recommend the fix
    (§2 invariant); ``reject`` (or NO critique) → the card demotes it and recommends guiding the fix."""

    verdict: Literal["accept", "narrow", "reject"]
    #: The tightened/corrected fix scope — REQUIRED-in-spirit for ``narrow`` (the whole point), optional
    #: otherwise. When non-empty it takes precedence over the Auditor's raw ``proposed_fix`` in the fix brief
    #: (:func:`orchestrator._latest_verifikacia_fix_scope`).
    corrected_scope: str = ""
    #: WHY this verdict — a stated reason is mandatory (a positive verdict with no reasoning is not a vet); a
    #: critique that omits it ParseFails → fail-open → the card recommends guide (never a blind ``accept_fix``).
    #: ``min_length=1`` (review fix): an EMPTY ``why`` is not a vet either → it ParseFails → fail-open to guide.
    why: str = Field(min_length=1)


#: Narrowed JSON Schema for the fix-critic pass (CR-V2-058). Derived from the model (single source); used
#: ONLY by ``_invoke_fix_critique``. :data:`PIPELINE_STATUS_JSON_SCHEMA` stays the default elsewhere.
FIX_CRITIQUE_JSON_SCHEMA = FixCritique.model_json_schema()


# ── Interactive consultation (CR-V2-041) ─────────────────────────────────────────────────────────
# When a problem needs the Manažér (Auditor upfront findings; later any mid-build blocker), the AI Agent
# does NOT dump a verdict — it emits a ``kind=consultation`` block: a queue of plain-language DECISIONS,
# each with options + a recommendation, that the Manažér answers ONE-AT-A-TIME by click (the production
# "Dedo on the screen"). See docs/architecture/interactive-consultation-design.md.


class ConsultOption(BaseModel):
    """One choice for a decision. ``recommended`` marks the AI Agent's single recommended pick."""

    id: str
    label: str
    detail: str = ""
    recommended: bool = False


class ConsultDecision(BaseModel):
    """One decision the Manažér resolves: a plain-language (non-expert) problem + 2-3 options + the
    AI Agent's recommendation. ``key`` is the stable id the Manažér's answer is recorded against (the
    consultation cursor — first decision with no recorded answer — derives from it)."""

    key: str
    question: str
    explanation: str = ""
    options: list[ConsultOption] = Field(min_length=2)
    rationale: str = ""
    allow_free_text: bool = False


class ConsultationBlock(BaseModel):
    """A queue of decisions surfaced to the Manažér (CR-V2-041). ``source`` labels provenance
    (``auditor_upfront`` | ``verifikacia_fail`` | ``build_blocker`` | ``agent_ambiguity``) — for the audit
    trail + the apply-directive wording; the Manažér's experience is identical regardless."""

    id: str
    intro: str = ""
    source: str = "auditor_upfront"
    decisions: list[ConsultDecision] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_decision_keys(self) -> "ConsultationBlock":
        """``decision.key`` is what an answer is recorded against (and aggregated by in the apply directive);
        a duplicate key would make two decisions indistinguishable → silently drop one decision's answer.
        Reject at parse time (CR-V2-041 verify-round: the second of the three correctness invariants — the
        first, cross-consultation isolation, is handled by SEQ-scoping the answers)."""
        keys = [d.key for d in self.decisions]
        if len(keys) != len(set(keys)):
            dupes = sorted({k for k in keys if keys.count(k) > 1})
            raise ValueError(f"consultation decision keys must be unique (duplicates: {', '.join(dupes)})")
        return self


class ChangeRequestMarker(BaseModel):
    """A change-request the read-only Konzultácia turn raises (konzultacia-mode.md Part 2).

    Set ONLY when the Manažér's ask in a read-only consult would require MODIFYING the finished app —
    which the consult turn cannot do. The AI's plain-language reply still says "this needs a new version";
    this structured marker lets the cockpit render the "Založiť novú verziu z tejto požiadavky" bar, whose
    click captures ``summary`` as a backlog REQ and the new DRAFT version's Zadanie. ``None`` on every build
    turn and on a purely advisory consult answer (nothing to change)."""

    summary: str
    #: Optional short title for the backlog REQ; falls back to a truncated ``summary`` when absent.
    title: Optional[str] = None


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
    #: CR-V2-052: release-coverage declaration carried on the Návrh gate_report — the flagship features the
    #: release must demonstrate + the safety properties it must enforce. The risk-floored oracle (CR-V2-051)
    #: reads these from the recorded payload and FAILs a build with fewer FEATURE / NEGATIVE assertions than
    #: declared. Empty on non-Návrh blocks.
    flagship_features: list[str] = Field(default_factory=list)
    safety_properties: list[SafetyProperty] = Field(default_factory=list)

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

    #: CR-V2-041: a ``kind=consultation`` turn carries the AI Agent's decision queue here (plain-language
    #: decisions + options + recommendation the Manažér answers one-at-a-time). ``None`` on every other block.
    consultation: Optional[ConsultationBlock] = None

    #: konzultacia-mode.md Part 2: a read-only Konzultácia turn sets this when the Manažér's ask requires
    #: modifying the finished app — the cockpit turns it into "Založiť novú verziu z tejto požiadavky".
    #: ``None`` on every build turn and on a purely advisory consult answer.
    change_request: Optional[ChangeRequestMarker] = None


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
    #: CR-V2-029: a truncated excerpt of the agent's raw output for this failed turn, attached by
    #: ``invoke_agent`` via :func:`dataclasses.replace`. A parse-exhausted turn records no agent message,
    #: so the terminal escalation folds this excerpt into its Manažér notification — the failure is then
    #: visible + debuggable instead of vanishing (it previously left the screen empty). ``None`` until set.
    raw: Optional[str] = None
    #: R1-c (v0.7.0 dispatch resilience): set when this failure is an agent envelope-loss (a
    #: ``ClaudeAgentError`` — timeout / crash) for which the engine ran the ``baseline..HEAD`` commit
    #: audit. Carries ``{dispatch_baseline_sha, post_timeout_head_sha, timeout_seconds,
    #: detected_commit_count, next_action}`` so ``run_dispatch`` settles to ``awaiting_director`` with a
    #: "work may have landed — review & continue" next_action instead of a bare ``blocked`` relay. ``None``
    #: for an ordinary parse failure (no dispatch baseline to audit against).
    lost_work: Optional[dict[str, Any]] = None
    #: build-robustness-crash-handling.md Fix 2/3: the KIND of envelope loss — ``"timeout"`` (the turn
    #: burned its whole wall-clock budget → a real :class:`ClaudeAgentTimeout`) or ``"crash"`` (a
    #: :class:`ClaudeAgentError` — connection/decode/non-zero-exit, NOT the budget). Set only by
    #: :func:`orchestrator.invoke_agent` on an envelope-loss so the build round can auto-retry a crash ONCE
    #: (but never a real timeout) and route the honest, type-specific settle message. ``None`` for an
    #: ordinary parse failure.
    envelope_loss_kind: Optional[str] = None
    #: build-robustness-crash-handling.md Fix 1: the per-turn diagnostic log written for this failing turn
    #: (redacted stderr / stdout tail / stream-event tail), carried up from the raising
    #: :class:`ClaudeAgentError`. The honest crash/timeout notification (Fix 3) references it so the
    #: operator/Dedo can read the cause. ``None`` when no log dir was configured for the turn.
    log_path: Optional[str] = None


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
    # Director observation #6: a ``framework_issue`` escalation MUST carry the message for Dedo (the error,
    # the context, the NEX Studio change needed) in ``question`` — the settle path captures it for delivery
    # (the Dedo escalation channel inbox file + the Telegram ping). Kept OUT of ``_QUESTION_KINDS`` so the
    # orchestrator msg_kind mapping never downgrades a framework_issue to a plain "question" message.
    if block.kind == "framework_issue" and not (block.question and block.question.strip()):
        return ParseFailure("kind='framework_issue' requires a non-empty 'question' (the message for Dedo)")
    # Návrh close (CR-V2-011 — the task plan folds into the Návrh phase): the AI Agent's Návrh
    # gate_report must carry the EPIC→FEAT→TASK decomposition. A question/blocked turn is still
    # allowed (re-plan dialogue); only the gate_report — the turn that closes the phase — requires
    # a non-empty 'plan'. (The narrowed skeleton/per-feat passes never hit this guard — they emit
    # a TaskPlanSkeleton/TaskPlanFeatTasks object, not a PipelineStatusBlock.)
    if block.stage == "navrh" and block.kind == "gate_report" and (block.plan is None or not block.plan.epics):
        return ParseFailure("navrh gate_report requires a non-empty 'plan' (EPIC→FEAT→TASK)")
    # CR-V2-041: a consultation turn must carry the decision queue, and each decision must have EXACTLY
    # one recommended option (so the card pre-highlights a default). decisions≥1 / options≥2 are enforced by
    # the models; this adds the presence + "exactly one recommended" cross-field check. (consultation is NOT
    # in _QUESTION_KINDS, so the question-required rule above never fires for it — it carries no 'question'.)
    if block.kind == "consultation":
        if block.consultation is None or not block.consultation.decisions:
            return ParseFailure("kind='consultation' requires a non-empty 'consultation.decisions'")
        for d in block.consultation.decisions:
            n_rec = sum(1 for o in d.options if o.recommended)
            if n_rec != 1:
                return ParseFailure(
                    f"consultation decision {d.key!r} must have exactly ONE recommended option (has {n_rec})"
                )

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


def parse_fix_critique(obj: dict) -> Union[FixCritique, ParseFailure]:
    """Validate the fix-critic pass's narrowed output (CR-V2-058 Part B) — the ``{accept,narrow,reject}``
    verdict + ``corrected_scope`` + ``why``. Never raises, never infers: an unparseable / incomplete critique
    (e.g. a missing ``why``, or a ``verdict`` outside the Literal) → :class:`ParseFailure`, which the caller
    treats as FAIL-OPEN (no ``fix_critique`` record → the Decision Card demotes ``accept_fix`` + recommends
    guide; §5). A critique is trusted ONLY when it is well-formed."""
    if not isinstance(obj, dict):
        return ParseFailure("fix_critique structured_output is not an object")
    try:
        return FixCritique.model_validate(obj)
    except ValidationError as exc:
        return ParseFailure(f"fix_critique invalid — {_format_validation_errors(exc)}")


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
