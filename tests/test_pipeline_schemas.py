"""R2-a (v0.7.0) — BE schema enum round-trips for the pipeline contract.

The pipeline response schemas (``PipelineStateRead`` / ``PipelineMessageRead``) carry ``Literal``
fields sourced from the DB CHECK value tuples (single source, R2 §3, D2). These tests assert the
schema accepts EXACTLY the DB-valid set and rejects an out-of-enum value, so any drift between the
schema ``Literal`` and the DB CHECK surfaces here (complementing the FE codegen drift-gate). No DB
needed — pure Pydantic construction.
"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.db.models.pipeline import (
    ACTOR_VALUES,
    BLOCK_REASON_VALUES,
    FLOW_TYPE_VALUES,
    MESSAGE_KIND_VALUES,
    STAGE_VALUES,
    STATUS_VALUES,
)
from backend.schemas.pipeline import PipelineMessageRead, PipelineStateRead


def _state_kwargs(**overrides):
    base = {
        "id": uuid4(),
        "version_id": uuid4(),
        "flow_type": "new_version",
        "current_stage": "programovanie",
        "current_actor": "ai_agent",
        "status": "agent_working",
        "next_action": "",
        "is_regate": False,
        "iteration": 0,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    base.update(overrides)
    return base


def _message_kwargs(**overrides):
    base = {
        "id": uuid4(),
        "version_id": uuid4(),
        "stage": "programovanie",
        "author": "ai_agent",
        "recipient": "manazer",
        "kind": "gate_report",
        "content": "x",
        "status": "pending",
        "payload": None,
        "created_at": datetime.now(timezone.utc),
        "seq": 1,
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize("flow_type", FLOW_TYPE_VALUES)
def test_state_accepts_every_db_valid_flow_type(flow_type):
    assert PipelineStateRead(**_state_kwargs(flow_type=flow_type)).flow_type == flow_type


@pytest.mark.parametrize("stage", STAGE_VALUES)
def test_state_accepts_every_db_valid_stage(stage):
    assert PipelineStateRead(**_state_kwargs(current_stage=stage)).current_stage == stage


@pytest.mark.parametrize("actor", ACTOR_VALUES)
def test_state_accepts_every_db_valid_actor(actor):
    assert PipelineStateRead(**_state_kwargs(current_actor=actor)).current_actor == actor


@pytest.mark.parametrize("status", STATUS_VALUES)
def test_state_accepts_every_db_valid_status(status):
    assert PipelineStateRead(**_state_kwargs(status=status)).status == status


@pytest.mark.parametrize("kind", MESSAGE_KIND_VALUES)
def test_message_accepts_every_db_valid_kind(kind):
    assert PipelineMessageRead(**_message_kwargs(kind=kind)).kind == kind


@pytest.mark.parametrize("block_reason", BLOCK_REASON_VALUES)
def test_state_accepts_every_db_valid_block_reason(block_reason):
    # R4 (D1): the Literal sourced from BLOCK_REASON_VALUES accepts exactly the DB-valid set.
    assert PipelineStateRead(**_state_kwargs(status="blocked", block_reason=block_reason)).block_reason == block_reason


def test_state_block_reason_defaults_to_none():
    # R4 (D1): block_reason is Optional → absent ⇒ None (not blocked / legacy rows → FE heuristic fallback).
    assert PipelineStateRead(**_state_kwargs()).block_reason is None


def test_state_rejects_out_of_enum_block_reason():
    with pytest.raises(ValidationError):
        PipelineStateRead(**_state_kwargs(status="blocked", block_reason="meltdown"))


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("flow_type", "patch"),
        ("current_stage", "gate_z"),
        # 'system' is message-only (a participant, never a state actor) — the state schema must reject it.
        ("current_actor", "system"),
        ("status", "running"),
    ],
)
def test_state_rejects_out_of_enum(field, bad):
    with pytest.raises(ValidationError):
        PipelineStateRead(**_state_kwargs(**{field: bad}))


def test_message_rejects_out_of_enum_kind():
    with pytest.raises(ValidationError):
        PipelineMessageRead(**_message_kwargs(kind="bogus"))
