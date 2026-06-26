"""Tests for the §5.3 status-block parser (CR-NS-018 Phase 2; v2 CR-V2-006).

Deterministic: any deviation → ParseFailure, never a guess.

v2 (CR-V2-006): the block is emitted by the two v2 roles (AI Agent + Auditor); ``stage`` is one
of the 4 phases (priprava/navrh/programovanie/verifikacia) + done; ``awaiting`` is manazer/none
(Director → Manažér, CR-V2-004). The v1 Coordinator-relay (``coordinator_directive``), per-task
audit (``task_pass``) and Gate-E Customer↔Designer signals are dropped; the Auditor verdict
repurposes the ``findings``/``proposed_fix`` shape (+ a PASS/FAIL ``verdict``). The task_plan tree +
narrowed skeleton/per-feat passes + ``extract_task_plan_json`` + dual transport are KEPT.
"""

import json

import pytest

from backend.services.pipeline_status import (
    ParseFailure,
    PipelineStatusBlock,
    TaskPlanFeatTasks,
    TaskPlanSkeleton,
    extract_task_plan_json,
    parse_status_block,
    parse_structured_output,
    parse_task_plan_feat_tasks,
    parse_task_plan_skeleton,
)


def _block(**fields) -> str:
    return f"<<<PIPELINE_STATUS>>>\n{json.dumps(fields)}\n<<<END_PIPELINE_STATUS>>>"


# ── happy path ────────────────────────────────────────────────────────────────


def test_parses_valid_block_amid_noise():
    out = (
        "blah blah\n"
        + _block(
            stage="programovanie",
            kind="gate_report",
            summary="tasks committed",
            awaiting="manazer",
            deliverables=["services/foo.py"],
            commits=["abc1234"],
        )
        + "\ntrailing"
    )
    res = parse_status_block(out)
    assert isinstance(res, PipelineStatusBlock)
    assert res.stage == "programovanie"
    assert res.kind == "gate_report"
    assert res.deliverables == ["services/foo.py"]
    assert res.commits == ["abc1234"]


def test_recipient_is_ignored_not_required():
    res = parse_status_block(_block(stage="priprava", kind="answer", summary="s", awaiting="none", recipient="manazer"))
    assert isinstance(res, PipelineStatusBlock)
    assert not hasattr(res, "recipient")


def test_commits_and_deliverables_default_to_empty():
    res = parse_status_block(_block(stage="programovanie", kind="gate_report", summary="s", awaiting="manazer"))
    assert isinstance(res, PipelineStatusBlock)
    assert res.deliverables == []
    assert res.commits == []


def test_blocked_carries_question():
    res = parse_status_block(
        _block(stage="programovanie", kind="blocked", summary="ctx", awaiting="manazer", question="Ktorý port?")
    )
    assert isinstance(res, PipelineStatusBlock)
    assert res.question == "Ktorý port?"


def test_all_four_phases_plus_done_parse():
    for stage in ("priprava", "navrh", "programovanie", "verifikacia", "done"):
        res = parse_status_block(_block(stage=stage, kind="answer", summary="s", awaiting="none"))
        assert isinstance(res, PipelineStatusBlock), stage
        assert res.stage == stage


# ── dual transport: structured_output runs the SAME validation as the fence (R3 D1) ──


def test_structured_output_validates_like_the_fence():
    ok = parse_structured_output({"stage": "navrh", "kind": "answer", "summary": "s", "awaiting": "none"})
    assert isinstance(ok, PipelineStatusBlock)
    bad = parse_structured_output({"stage": "gate_a", "kind": "answer", "summary": "s", "awaiting": "none"})
    assert isinstance(bad, ParseFailure)  # v1 stage rejected on BOTH transports


def test_structured_output_not_an_object():
    assert isinstance(parse_structured_output([1, 2, 3]), ParseFailure)


# ── failure modes — a malformed block degrades to ParseFailure (→ blocked), NEVER a guess ──


def test_no_fence():
    assert isinstance(parse_status_block("just prose, no block"), ParseFailure)


def test_double_fence():
    out = (
        _block(stage="priprava", kind="answer", summary="s", awaiting="none")
        + "\n"
        + _block(stage="navrh", kind="answer", summary="s", awaiting="none")
    )
    res = parse_status_block(out)
    assert isinstance(res, ParseFailure)
    assert "found 2" in res.reason


def test_invalid_json():
    res = parse_status_block("<<<PIPELINE_STATUS>>> {not json} <<<END_PIPELINE_STATUS>>>")
    assert isinstance(res, ParseFailure)
    assert "JSON" in res.reason


def test_json_not_object():
    res = parse_status_block("<<<PIPELINE_STATUS>>> [1,2,3] <<<END_PIPELINE_STATUS>>>")
    assert isinstance(res, ParseFailure)


@pytest.mark.parametrize("missing", ["stage", "kind", "summary", "awaiting"])
def test_missing_required_field(missing):
    fields = {"stage": "navrh", "kind": "answer", "summary": "s", "awaiting": "none"}
    del fields[missing]
    assert isinstance(parse_status_block(_block(**fields)), ParseFailure)


def test_unknown_stage_rejects_v1_gate():
    # the v1 gate path (gate_a … gate_g / task_plan / build / release / kickoff) is gone in v2
    for stale in ("gate_a", "gate_e", "gate_g", "task_plan", "build", "release", "kickoff"):
        res = parse_status_block(_block(stage=stale, kind="answer", summary="s", awaiting="none"))
        assert isinstance(res, ParseFailure), stale
        assert "stage" in res.reason


def test_unknown_kind():
    res = parse_status_block(_block(stage="navrh", kind="gossip", summary="s", awaiting="none"))
    assert isinstance(res, ParseFailure)
    assert "kind" in res.reason


def test_unknown_awaiting_rejects_v1_director():
    # the operator was renamed Director → Manažér (CR-V2-004): ``awaiting`` is manazer/none
    for stale in ("director", "maybe"):
        res = parse_status_block(_block(stage="navrh", kind="answer", summary="s", awaiting=stale))
        assert isinstance(res, ParseFailure), stale
        assert "awaiting" in res.reason


def test_awaiting_manazer_accepted():
    res = parse_status_block(_block(stage="programovanie", kind="gate_report", summary="s", awaiting="manazer"))
    assert isinstance(res, PipelineStatusBlock)
    assert res.awaiting == "manazer"


@pytest.mark.parametrize("kind", ["question", "blocked"])
def test_question_required_for_question_kinds(kind):
    res = parse_status_block(_block(stage="priprava", kind=kind, summary="s", awaiting="manazer"))
    assert isinstance(res, ParseFailure)
    assert "question" in res.reason


@pytest.mark.parametrize("kind", ["question", "blocked"])
def test_question_blank_rejected(kind):
    res = parse_status_block(_block(stage="priprava", kind=kind, summary="s", awaiting="manazer", question="   "))
    assert isinstance(res, ParseFailure)


# ── Auditor verdict (CR-V2-006 — repurposes the v1 findings/proposed_fix shape) ────


def test_auditor_verdict_signals_default_when_absent():
    """An AI-Agent (non-verdict) block gets safe verdict-field defaults."""
    res = parse_status_block(_block(stage="programovanie", kind="answer", summary="s", awaiting="none"))
    assert isinstance(res, PipelineStatusBlock)
    assert res.verdict is None
    assert res.findings == []
    assert res.proposed_fix is None


def test_auditor_pass_verdict_parses():
    res = parse_status_block(
        _block(
            stage="verifikacia",
            kind="verdict",
            summary="release acceptance PASS — app behaves per spec",
            awaiting="manazer",
            verdict=True,
            findings=[],
        )
    )
    assert isinstance(res, PipelineStatusBlock)
    assert res.verdict is True
    assert res.findings == []
    assert res.proposed_fix is None


def test_auditor_fail_verdict_carries_findings_and_fix_scope():
    res = parse_status_block(
        _block(
            stage="verifikacia",
            kind="verdict",
            summary="FAIL — money rounding off by 0.01",
            awaiting="none",
            verdict=False,
            findings=["DPH rounding accumulates per line, not on the cumulative total"],
            proposed_fix="Round on the cumulative total in services/invoice.py compute_totals().",
        )
    )
    assert isinstance(res, PipelineStatusBlock)
    assert res.verdict is False
    assert res.findings == ["DPH rounding accumulates per line, not on the cumulative total"]
    assert res.proposed_fix.startswith("Round on the cumulative total")


def test_auditor_upfront_review_findings_after_navrh():
    # the upfront design/spec review (replaces the Gate-E Customer function) surfaces holes at the
    # post-Návrh schvaľovací bod via the SAME findings shape.
    res = parse_status_block(
        _block(
            stage="navrh",
            kind="verdict",
            summary="upfront review — 2 holes",
            awaiting="manazer",
            verdict=False,
            findings=["password reset flow undefined", "no rate-limit on login"],
            plan={
                "epics": [
                    {"title": "E1", "feats": [{"title": "F1", "tasks": [{"title": "T1", "task_type": "backend"}]}]}
                ]
            },
        )
    )
    assert isinstance(res, PipelineStatusBlock)
    assert res.findings == ["password reset flow undefined", "no rate-limit on login"]


# ── task_plan plan parse↔write parity (CR-NS-020 / CR-NS-022 §1; v2: folds into Návrh) ──


def _navrh_plan_block() -> str:
    epic = {"title": "E1", "feats": [{"title": "F1", "tasks": [{"title": "T1", "task_type": "backend"}]}]}
    return _block(
        stage="navrh",
        kind="gate_report",
        summary="plán",
        awaiting="manazer",
        plan={"epics": [epic]},
    )


def test_navrh_gate_report_parses_project_level_epic():
    # v2: epics are always project-level (multi-module removed). The plan folds into the Návrh phase
    # (CR-V2-011) — a navrh gate_report carries the EPIC→FEAT→TASK tree.
    res = parse_status_block(_navrh_plan_block())
    assert isinstance(res, PipelineStatusBlock)
    assert res.plan.epics[0].title == "E1"


def test_navrh_gate_report_requires_a_plan():
    # the Návrh-close guard (was stage==task_plan in v1): a navrh gate_report with no plan → ParseFailure
    planless = _block(stage="navrh", kind="gate_report", summary="x", awaiting="manazer")
    assert isinstance(parse_status_block(planless), ParseFailure)


def test_non_navrh_gate_report_does_not_require_a_plan():
    # a programovanie/verifikacia gate_report has no plan-required guard
    res = parse_status_block(_block(stage="programovanie", kind="gate_report", summary="ok", awaiting="manazer"))
    assert isinstance(res, PipelineStatusBlock)
    assert res.plan is None


def test_parse_failure_names_the_exact_missing_field():
    # WS-B3 (CR-NS-029): a task_type omission → the ParseFailure reason names the EXACT field + index
    # (so the parse-retry re-prompt is actionable), not a raw stringified Pydantic error array.
    epic = {"title": "E1", "feats": [{"title": "F1", "tasks": [{"title": "T1"}]}]}  # task_type omitted
    block = _block(stage="navrh", kind="gate_report", summary="plán", awaiting="manazer", plan={"epics": [epic]})
    res = parse_status_block(block)
    assert isinstance(res, ParseFailure)
    # the exact dotted+indexed path, not a stringified error array
    assert "plan.epics[0].feats[0].tasks[0].task_type" in res.reason
    assert "[{" not in res.reason  # NOT the raw `exc.errors()` list dump


# ── (v0.7.3) narrowed task_plan-pass parsers (CR-1) — KEPT verbatim in v2 ──────


def test_parse_task_plan_skeleton_accepts_feats_without_tasks():
    # The skeleton pass emits EPIC + FEAT (NO tasks) + cross_cutting_rules — validates against the
    # SEPARATE TaskPlanSkeleton type (the full plan models stay strict; F-007 §9 "schéma nemení").
    obj = {
        "epics": [
            {
                "title": "Foundation",
                "feats": [{"title": "Schema", "description": "tables", "estimated_minutes": 120}],
            }
        ],
        "cross_cutting_rules": "## Invarianty\n- audit",
    }
    res = parse_task_plan_skeleton(obj)
    assert isinstance(res, TaskPlanSkeleton)
    assert res.epics[0].feats[0].title == "Schema"
    assert res.cross_cutting_rules.startswith("## Invarianty")


def test_parse_task_plan_skeleton_rejects_empty_and_non_object():
    assert isinstance(parse_task_plan_skeleton({"epics": []}), ParseFailure)  # min_length=1
    assert isinstance(parse_task_plan_skeleton({"epics": [{"title": "E", "feats": []}]}), ParseFailure)
    assert isinstance(parse_task_plan_skeleton([]), ParseFailure)  # not an object


def test_parse_task_plan_feat_tasks_accepts_tasks_only():
    obj = {"tasks": [{"title": "GL výpočet", "task_type": "backend", "estimated_minutes": 90}]}
    res = parse_task_plan_feat_tasks(obj)
    assert isinstance(res, TaskPlanFeatTasks)
    assert res.tasks[0].title == "GL výpočet" and res.tasks[0].task_type == "backend"


def test_parse_task_plan_feat_tasks_rejects_empty_and_bad_task():
    assert isinstance(parse_task_plan_feat_tasks({"tasks": []}), ParseFailure)  # ≥1 task required
    assert isinstance(parse_task_plan_feat_tasks({"tasks": [{"title": "T"}]}), ParseFailure)  # task_type missing
    assert isinstance(parse_task_plan_feat_tasks("nope"), ParseFailure)  # not an object


def test_task_plan_plan_required_guard_unchanged_for_narrowed_passes():
    # The narrowed passes never hit the navrh plan-required guard — only the final assembled
    # navrh gate_report does, and it has a plan.
    planless = _block(stage="navrh", kind="gate_report", summary="x", awaiting="manazer")
    assert isinstance(parse_status_block(planless), ParseFailure)


# ── (v0.7.3) TEXT/FENCE extraction — the real-CLI path (CR-1, point 8) — KEPT ──


def _fence(payload: str) -> str:
    return f"prose before\n<<<TASK_PLAN_JSON>>>\n{payload}\n<<<END_TASK_PLAN_JSON>>>\nprose after"


def test_extract_task_plan_json_pulls_object_from_sentinel_fence():
    obj = extract_task_plan_json(_fence('{"epics": [{"title": "E1", "feats": [{"title": "F1"}]}]}'))
    assert isinstance(obj, dict) and obj["epics"][0]["title"] == "E1"


def test_extract_task_plan_json_tolerates_inner_markdown_wrapper():
    # the model wraps the JSON in a ```json … ``` block INSIDE the sentinel fence
    obj = extract_task_plan_json(_fence('```json\n{"tasks": [{"title": "T", "task_type": "backend"}]}\n```'))
    assert isinstance(obj, dict) and obj["tasks"][0]["task_type"] == "backend"


@pytest.mark.parametrize(
    "text",
    [
        "no fence at all — just prose",  # missing fence
        "<<<TASK_PLAN_JSON>>>\nnot json\n<<<END_TASK_PLAN_JSON>>>",  # invalid JSON
        "<<<TASK_PLAN_JSON>>>\n[1, 2]\n<<<END_TASK_PLAN_JSON>>>",  # JSON but not an object
        _fence("{}") + _fence("{}"),  # two fences
    ],
)
def test_extract_task_plan_json_rejects_bad_input(text):
    assert isinstance(extract_task_plan_json(text), ParseFailure)


def test_parse_skeleton_accepts_features_alias_and_drops_unknown_keys():
    # The exact live root-cause drift: `features` (not `feats`) + extra id/project/version/level keys.
    drift = {
        "project": "x",
        "version": "1",
        "level": "skeleton",
        "epics": [{"id": "EPIC-1", "title": "Foundation", "features": [{"id": "FEAT-1", "title": "Schema"}]}],
        "cross_cutting_rules": "inv",
    }
    res = parse_task_plan_skeleton(drift)
    assert isinstance(res, TaskPlanSkeleton)
    assert res.epics[0].feats[0].title == "Schema"  # features → feats normalised
    # canonical `feats` still works (the alias does not break the normal name)
    ok = parse_task_plan_skeleton({"epics": [{"title": "E", "feats": [{"title": "F"}]}]})
    assert isinstance(ok, TaskPlanSkeleton) and ok.epics[0].feats[0].title == "F"


def test_extract_then_parse_feat_tasks_round_trip():
    obj = extract_task_plan_json(_fence('{"tasks": [{"title": "GL", "task_type": "migration", "id": "x"}]}'))
    res = parse_task_plan_feat_tasks(obj)
    assert isinstance(res, TaskPlanFeatTasks)
    assert res.tasks[0].title == "GL" and res.tasks[0].task_type == "migration"
