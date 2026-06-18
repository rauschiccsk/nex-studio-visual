"""Tests for the §5.3 status-block parser (CR-NS-018 Phase 2).

Deterministic: any deviation → ParseFailure, never a guess.
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
            stage="gate_b",
            kind="gate_report",
            summary="openapi + summary done",
            awaiting="director",
            deliverables=["a.yaml", "b.md"],
            commits=["abc1234"],
        )
        + "\ntrailing"
    )
    res = parse_status_block(out)
    assert isinstance(res, PipelineStatusBlock)
    assert res.stage == "gate_b"
    assert res.kind == "gate_report"
    assert res.deliverables == ["a.yaml", "b.md"]
    assert res.commits == ["abc1234"]


def test_recipient_is_ignored_not_required():
    res = parse_status_block(_block(stage="gate_a", kind="answer", summary="s", awaiting="none", recipient="director"))
    assert isinstance(res, PipelineStatusBlock)
    assert not hasattr(res, "recipient")


def test_commits_and_deliverables_default_to_empty():
    res = parse_status_block(_block(stage="build", kind="gate_report", summary="s", awaiting="director"))
    assert isinstance(res, PipelineStatusBlock)
    assert res.deliverables == []
    assert res.commits == []


def test_blocked_carries_question():
    res = parse_status_block(
        _block(stage="gate_c", kind="blocked", summary="ctx", awaiting="director", question="Ktorý port?")
    )
    assert isinstance(res, PipelineStatusBlock)
    assert res.question == "Ktorý port?"


# ── coordinator_directive.target tolerance (NEX Test regression, CR 2026-06-13) ─


def _directive(target):
    return {
        "triage_class": "spec_problem",
        "proposed_action": "relay",
        "target": target,
        "rationale": "r",
        "confidence": 0.5,
    }


@pytest.mark.parametrize("bad_target", ["frontend serializer cr014", None, ["a", "b"]])
def test_coordinator_directive_tolerates_nonobject_target(bad_target):
    # The Coordinator (LLM) emitted `target` as prose/null on a gate_g FAIL → the verify-judge parse
    # auto-returned ("target: Input should be a valid dictionary…"), so the FAIL verdict couldn't route.
    # A non-object target must NOT crash the parse — it degrades to an empty CoordinatorTarget.
    res = parse_status_block(
        _block(
            stage="gate_g",
            kind="gate_report",
            summary="s",
            awaiting="director",
            coordinator_directive=_directive(bad_target),
        )
    )
    assert isinstance(res, PipelineStatusBlock), res
    assert res.coordinator_directive is not None
    t = res.coordinator_directive.target
    assert (t.task_id, t.role, t.commit) == (None, None, None)  # degraded to empty


def test_coordinator_directive_preserves_valid_object_target():
    res = parse_status_block(
        _block(
            stage="gate_g",
            kind="gate_report",
            summary="s",
            awaiting="director",
            coordinator_directive=_directive({"role": "implementer", "commit": "abc1234"}),
        )
    )
    assert isinstance(res, PipelineStatusBlock), res
    assert res.coordinator_directive.target.role == "implementer"
    assert res.coordinator_directive.target.commit == "abc1234"


# ── failure modes ─────────────────────────────────────────────────────────────


def test_no_fence():
    assert isinstance(parse_status_block("just prose, no block"), ParseFailure)


def test_double_fence():
    out = (
        _block(stage="gate_a", kind="answer", summary="s", awaiting="none")
        + "\n"
        + _block(stage="gate_b", kind="answer", summary="s", awaiting="none")
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
    fields = {"stage": "gate_a", "kind": "answer", "summary": "s", "awaiting": "none"}
    del fields[missing]
    assert isinstance(parse_status_block(_block(**fields)), ParseFailure)


def test_unknown_stage():
    res = parse_status_block(_block(stage="gate_x", kind="answer", summary="s", awaiting="none"))
    assert isinstance(res, ParseFailure)
    assert "stage" in res.reason


def test_unknown_kind():
    res = parse_status_block(_block(stage="gate_a", kind="gossip", summary="s", awaiting="none"))
    assert isinstance(res, ParseFailure)
    assert "kind" in res.reason


def test_unknown_awaiting():
    res = parse_status_block(_block(stage="gate_a", kind="answer", summary="s", awaiting="maybe"))
    assert isinstance(res, ParseFailure)
    assert "awaiting" in res.reason


@pytest.mark.parametrize("kind", ["question", "blocked"])
def test_question_required_for_question_kinds(kind):
    res = parse_status_block(_block(stage="gate_a", kind=kind, summary="s", awaiting="director"))
    assert isinstance(res, ParseFailure)
    assert "question" in res.reason


@pytest.mark.parametrize("kind", ["question", "blocked"])
def test_question_blank_rejected(kind):
    res = parse_status_block(_block(stage="gate_a", kind=kind, summary="s", awaiting="director", question="   "))
    assert isinstance(res, ParseFailure)


# ── Gate E signals (F-007-gate-e §5/§7.2, CR-NS-018 Phase 1) ────────────────────


def test_gate_e_signals_default_when_absent():
    """Non-gate-E blocks (and gate_e blocks not emitting them) get safe defaults."""
    res = parse_status_block(_block(stage="gate_a", kind="answer", summary="s", awaiting="none"))
    assert isinstance(res, PipelineStatusBlock)
    assert res.topic is None
    assert res.topic_done is False
    assert res.coverage_complete is False
    assert res.findings == []
    assert res.gap_found is False
    assert res.proposed_fix is None


def test_gate_e_designer_answer_gap_parses():
    res = parse_status_block(
        _block(
            stage="gate_e",
            kind="answer",
            summary="medzera: chýba reset hesla",
            awaiting="none",
            gap_found=True,
            proposed_fix="Pridať tok reset hesla cez email do §4.2.",
        )
    )
    assert isinstance(res, PipelineStatusBlock)
    assert res.gap_found is True
    assert res.proposed_fix == "Pridať tok reset hesla cez email do §4.2."


def test_gate_e_topic_boundary_block_parses():
    res = parse_status_block(
        _block(
            stage="gate_e",
            kind="gate_report",
            summary="okruh prihlásenie dokončený",
            awaiting="director",
            topic="prihlasenie",
            topic_done=True,
            findings=["chýba reset hesla", "2FA nedefinované"],
        )
    )
    assert isinstance(res, PipelineStatusBlock)
    assert res.topic == "prihlasenie"
    assert res.topic_done is True
    assert res.findings == ["chýba reset hesla", "2FA nedefinované"]


def test_gate_e_coverage_complete_block_parses():
    res = parse_status_block(
        _block(
            stage="gate_e",
            kind="gate_report",
            summary="všetkých 7 okruhov pokrytých",
            awaiting="director",
            topic_done=True,
            coverage_complete=True,
        )
    )
    assert isinstance(res, PipelineStatusBlock)
    assert res.coverage_complete is True


# ── task_plan plan parse↔write parity (CR-NS-020 / CR-NS-022 §1) ─────────────────


def _task_plan_block(module_id=None) -> str:
    epic = {"title": "E1", "feats": [{"title": "F1", "tasks": [{"title": "T1", "task_type": "backend"}]}]}
    if module_id is not None:
        epic["module_id"] = module_id
    return _block(
        stage="task_plan",
        kind="gate_report",
        summary="plán",
        awaiting="director",
        plan={"epics": [epic]},
    )


def test_task_plan_rejects_non_uuid_module_id():
    # CR-NS-022 §1: a stray module label ("backend") must fail at PARSE with a clear UUID error,
    # never a cryptic write→blocked (EpicCreate.module_id is Optional[UUID]).
    res = parse_status_block(_task_plan_block(module_id="backend"))
    assert isinstance(res, ParseFailure)


def test_task_plan_accepts_valid_uuid_module_id():
    res = parse_status_block(_task_plan_block(module_id="11111111-1111-1111-1111-111111111111"))
    assert isinstance(res, PipelineStatusBlock)
    assert str(res.plan.epics[0].module_id) == "11111111-1111-1111-1111-111111111111"


def test_task_plan_accepts_omitted_module_id():
    res = parse_status_block(_task_plan_block(module_id=None))
    assert isinstance(res, PipelineStatusBlock)
    assert res.plan.epics[0].module_id is None


def _coordinator_block(directive) -> str:
    return _block(
        stage="build", kind="gate_report", summary="relay", awaiting="director", coordinator_directive=directive
    )


def test_coordinator_directive_parses():
    # A1 (F-008 §2, CR-NS-032): the structured Coordinator proposal parses + validates on the block.
    directive = {
        "triage_class": "programmer_guidance",
        "proposed_action": "coordinator_move_baseline",
        "target": {"commit": "abc123"},
        "params": {},
        "rationale": "task #3 work sits in the merged commit",
        "confidence": 0.9,
    }
    res = parse_status_block(_coordinator_block(directive))
    assert isinstance(res, PipelineStatusBlock)
    assert res.coordinator_directive is not None
    assert res.coordinator_directive.proposed_action == "coordinator_move_baseline"
    assert res.coordinator_directive.target.commit == "abc123"
    assert res.coordinator_directive.confidence == 0.9


def test_coordinator_directive_absent_is_none():
    res = parse_status_block(_block(stage="build", kind="gate_report", summary="ok", awaiting="director"))
    assert isinstance(res, PipelineStatusBlock)
    assert res.coordinator_directive is None


@pytest.mark.parametrize(
    "bad",
    [
        {"triage_class": "nonsense", "proposed_action": "relay", "rationale": "x", "confidence": 0.5},  # bad enum
        {"triage_class": "director_decision", "proposed_action": "relay", "rationale": "x", "confidence": 1.5},  # >1
        {"triage_class": "spec_problem", "proposed_action": "relay", "rationale": "x", "confidence": -0.1},  # <0
        {"proposed_action": "relay", "rationale": "x", "confidence": 0.5},  # missing triage_class
    ],
)
def test_coordinator_directive_rejects_invalid(bad):
    assert isinstance(parse_status_block(_coordinator_block(bad)), ParseFailure)


def test_parse_failure_names_the_exact_missing_field():
    # WS-B3 (CR-NS-029): a task_type omission → the ParseFailure reason names the EXACT field + index
    # (so the parse-retry re-prompt is actionable and the agent fixes it on the first retry), not a
    # raw stringified Pydantic error array.
    epic = {"title": "E1", "feats": [{"title": "F1", "tasks": [{"title": "T1"}]}]}  # task_type omitted
    block = _block(stage="task_plan", kind="gate_report", summary="plán", awaiting="director", plan={"epics": [epic]})
    res = parse_status_block(block)
    assert isinstance(res, ParseFailure)
    # the exact dotted+indexed path, not a stringified error array
    assert "plan.epics[0].feats[0].tasks[0].task_type" in res.reason
    assert "[{" not in res.reason  # NOT the raw `exc.errors()` list dump


# ── (v0.7.3) narrowed task_plan-pass parsers (CR-1) ──────────────────────────


def test_parse_task_plan_skeleton_accepts_feats_without_tasks():
    # The skeleton pass emits EPIC + FEAT (NO tasks) + cross_cutting_rules — validates against the
    # SEPARATE TaskPlanSkeleton type (the full plan models stay strict; F-007 §9 "schéma nemení").
    obj = {
        "epics": [
            {
                "title": "Foundation",
                "module_id": "11111111-1111-1111-1111-111111111111",
                "feats": [{"title": "Schema", "description": "tables", "estimated_minutes": 120}],
            }
        ],
        "cross_cutting_rules": "## Invarianty\n- audit",
    }
    res = parse_task_plan_skeleton(obj)
    assert isinstance(res, TaskPlanSkeleton)
    assert res.epics[0].feats[0].title == "Schema"
    assert res.cross_cutting_rules.startswith("## Invarianty")
    assert str(res.epics[0].module_id) == "11111111-1111-1111-1111-111111111111"


def test_parse_task_plan_skeleton_rejects_empty_and_non_object():
    assert isinstance(parse_task_plan_skeleton({"epics": []}), ParseFailure)  # min_length=1
    assert isinstance(parse_task_plan_skeleton({"epics": [{"title": "E", "feats": []}]}), ParseFailure)
    assert isinstance(parse_task_plan_skeleton([]), ParseFailure)  # not an object
    # a stray module label still fails at parse (parity with the full plan), not a cryptic write
    bad = {"epics": [{"title": "E", "module_id": "backend", "feats": [{"title": "F"}]}]}
    assert isinstance(parse_task_plan_skeleton(bad), ParseFailure)


def test_parse_task_plan_feat_tasks_accepts_tasks_only():
    obj = {"tasks": [{"title": "GL výpočet", "task_type": "backend", "estimated_minutes": 90}]}
    res = parse_task_plan_feat_tasks(obj)
    assert isinstance(res, TaskPlanFeatTasks)
    assert res.tasks[0].title == "GL výpočet" and res.tasks[0].task_type == "backend"


def test_parse_task_plan_feat_tasks_rejects_empty_and_bad_task():
    assert isinstance(parse_task_plan_feat_tasks({"tasks": []}), ParseFailure)  # ≥1 task required
    assert isinstance(parse_task_plan_feat_tasks({"tasks": [{"title": "T"}]}), ParseFailure)  # task_type missing
    assert isinstance(parse_task_plan_feat_tasks("nope"), ParseFailure)  # not an object


def test_task_plan_plan_required_guard_unchanged():
    # Point 7: the ~283 plan-required guard is UNTOUCHED — a task_plan gate_report STILL needs a plan
    # (the narrowed passes never hit this guard; only the final assembled block does, and it has one).
    planless = _block(stage="task_plan", kind="gate_report", summary="x", awaiting="director")
    assert isinstance(parse_status_block(planless), ParseFailure)


# ── (v0.7.3) TEXT/FENCE extraction — the real-CLI path (CR-1, point 8) ───────


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
