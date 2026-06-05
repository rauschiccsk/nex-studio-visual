"""Tests for the §5.3 status-block parser (CR-NS-018 Phase 2).

Deterministic: any deviation → ParseFailure, never a guess.
"""

import json

import pytest

from backend.services.pipeline_status import (
    ParseFailure,
    PipelineStatusBlock,
    parse_status_block,
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
