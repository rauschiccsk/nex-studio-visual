"""Tests for the F-007 pipeline models (CR-NS-018 Phase 1)."""

import uuid

import pytest
from sqlalchemy.exc import IntegrityError, ProgrammingError

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version


def _make_version(db_session) -> Version:
    user = User(
        username=f"u_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="hashed_password_placeholder",
        role="ri",
    )
    db_session.add(user)
    db_session.flush()
    project = Project(
        name=f"P {uuid.uuid4().hex[:8]}",
        slug=f"p-{uuid.uuid4().hex[:8]}",
        category="singlemodule",
        description="d",
        created_by=user.id,
    )
    db_session.add(project)
    db_session.flush()
    version = Version(project_id=project.id, version_number=f"1.{uuid.uuid4().hex[:4]}.0")
    db_session.add(version)
    db_session.flush()
    return version


def _state(version, **overrides) -> PipelineState:
    defaults = {
        "version_id": version.id,
        "flow_type": "new_version",
        "current_stage": "kickoff",
        "current_actor": "coordinator",
        "status": "agent_working",
        "next_action": "Coordinator robí discovery.",
    }
    defaults.update(overrides)
    return PipelineState(**defaults)


def _message(version, **overrides) -> PipelineMessage:
    defaults = {
        "version_id": version.id,
        "stage": "gate_a",
        "author": "designer",
        "recipient": "director",
        "kind": "gate_report",
        "content": "development-spec.md hotové",
        "status": "delivered",
    }
    defaults.update(overrides)
    return PipelineMessage(**defaults)


# ── PipelineState ─────────────────────────────────────────────────────────────


class TestPipelineState:
    def test_persists_with_defaults(self, db_session):
        version = _make_version(db_session)
        state = _state(version)
        db_session.add(state)
        db_session.flush()
        db_session.refresh(state)
        assert state.id is not None
        assert state.is_regate is False
        assert state.iteration == 0
        assert state.created_at is not None

    def test_version_id_unique(self, db_session):
        version = _make_version(db_session)
        db_session.add(_state(version))
        db_session.flush()
        db_session.add(_state(version, current_stage="gate_a"))
        with pytest.raises(IntegrityError):
            db_session.flush()

    @pytest.mark.parametrize(
        "field,bad",
        [
            ("flow_type", "weekly"),
            ("current_stage", "gate_z"),
            ("current_actor", "intern"),
            ("status", "napping"),
        ],
    )
    def test_check_rejects_bad_enum(self, db_session, field, bad):
        version = _make_version(db_session)
        db_session.add(_state(version, **{field: bad}))
        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.flush()

    def test_accepts_fast_fix_flow_type(self, db_session):
        # F-009 (CR-NS-094): the widened ck_pipeline_state_flow_type accepts 'fast_fix'.
        version = _make_version(db_session)
        db_session.add(_state(version, flow_type="fast_fix"))
        db_session.flush()

    def test_accepts_task_plan_stage(self, db_session):
        # CR-NS-020 CR-1: 'task_plan' is a permissive value (foundation) — the
        # widened ck_pipeline_state_current_stage must accept it even though the
        # flow does not route there yet.
        version = _make_version(db_session)
        db_session.add(_state(version, current_stage="task_plan"))
        db_session.flush()

    def test_awaiting_director_since_lifecycle(self, db_session):
        """WS-D (CR-NS-036): the status `set` event stamps awaiting_director_since on ENTERING a
        Director-wait status, PRESERVES it across wait→wait, and CLEARS it on LEAVING — and the
        value round-trips through the new column."""
        version = _make_version(db_session)
        state = _state(version, status="agent_working")
        db_session.add(state)
        db_session.flush()
        db_session.refresh(state)
        assert state.awaiting_director_since is None  # not waiting → unset

        state.status = "awaiting_director"
        assert state.awaiting_director_since is not None  # stamped on entry
        db_session.flush()
        db_session.refresh(state)
        entered = state.awaiting_director_since
        assert entered is not None  # persisted to the new column

        state.status = "blocked"  # wait → wait must NOT reset the clock
        assert state.awaiting_director_since == entered

        state.status = "agent_working"  # leaving clears it
        assert state.awaiting_director_since is None
        db_session.flush()
        db_session.refresh(state)
        assert state.awaiting_director_since is None

    def test_fk_cascade_on_version_delete(self, db_session):
        version = _make_version(db_session)
        state = _state(version)
        db_session.add(state)
        db_session.flush()
        state_id = state.id

        db_session.delete(version)
        db_session.flush()
        db_session.expire_all()

        assert db_session.get(PipelineState, state_id) is None

    def test_block_reason_check_accepts_valid_and_null(self, db_session):
        # R4 (D1): ck_pipeline_state_block_reason accepts NULL (not blocked) + each canonical value.
        version = _make_version(db_session)
        state = _state(version, status="blocked", block_reason="parse_exhaustion")
        db_session.add(state)
        db_session.flush()  # no raise
        state.status = "awaiting_director"  # leaving blocked clears it (listener) → NULL is valid
        db_session.flush()
        db_session.refresh(state)
        assert state.block_reason is None

    def test_block_reason_check_rejects_bad_value(self, db_session):
        # R4 (D1): a value outside BLOCK_REASON_VALUES violates the CHECK.
        version = _make_version(db_session)
        db_session.add(_state(version, status="blocked", block_reason="meltdown"))
        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.flush()

    def test_block_reason_cleared_on_leaving_blocked(self, db_session):
        """R4 (D1): the status `set` listener clears block_reason the moment the state leaves `blocked`,
        preserves it across a blocked→blocked re-block, and a fresh dispatch (→ agent_working) clears it."""
        version = _make_version(db_session)
        state = _state(version, status="agent_working")
        db_session.add(state)
        db_session.flush()
        db_session.refresh(state)
        assert state.block_reason is None  # not blocked → unset

        state.status = "blocked"  # entering blocked must NOT clear the reason set alongside it
        state.block_reason = "agent_question"
        db_session.flush()
        db_session.refresh(state)
        assert state.block_reason == "agent_question"  # persisted

        state.status = "blocked"  # blocked → blocked (value==oldvalue) is a no-op → reason preserved
        assert state.block_reason == "agent_question"

        state.status = "awaiting_director"  # leaving blocked clears it
        assert state.block_reason is None
        db_session.flush()
        db_session.refresh(state)
        assert state.block_reason is None

        # A fresh dispatch (→ agent_working) also clears any prior reason (re-block re-captures cleanly).
        state.status = "blocked"
        state.block_reason = "agent_error"
        state.status = "agent_working"
        assert state.block_reason is None


# ── PipelineMessage ───────────────────────────────────────────────────────────


class TestPipelineMessage:
    def test_persists(self, db_session):
        version = _make_version(db_session)
        msg = _message(version, payload={"deliverables": ["a.md"]})
        db_session.add(msg)
        db_session.flush()
        db_session.refresh(msg)
        assert msg.id is not None
        assert msg.created_at is not None
        assert msg.payload == {"deliverables": ["a.md"]}

    @pytest.mark.parametrize(
        "field,bad",
        [
            ("stage", "gate_z"),
            ("author", "robot"),
            ("recipient", "robot"),
            ("kind", "rumor"),
            ("status", "shrugged"),
        ],
    )
    def test_check_rejects_bad_enum(self, db_session, field, bad):
        version = _make_version(db_session)
        db_session.add(_message(version, **{field: bad}))
        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.flush()

    def test_system_author_allowed(self, db_session):
        version = _make_version(db_session)
        db_session.add(_message(version, author="system", kind="notification"))
        db_session.flush()  # no raise

    def test_accepts_task_plan_stage(self, db_session):
        # CR-NS-020 CR-1: widened ck_pipeline_message_stage accepts 'task_plan'.
        version = _make_version(db_session)
        db_session.add(_message(version, stage="task_plan"))
        db_session.flush()  # no raise

    def test_fk_cascade_on_version_delete(self, db_session):
        version = _make_version(db_session)
        msg = _message(version)
        db_session.add(msg)
        db_session.flush()
        msg_id = msg.id

        db_session.delete(version)
        db_session.flush()
        db_session.expire_all()

        assert db_session.get(PipelineMessage, msg_id) is None

    def test_seq_orders_same_transaction_messages_by_insertion(self, db_session):
        """CR-NS-018: two messages in ONE transaction tie on created_at (func.now)
        but order deterministically by the monotonic ``seq`` — worker report first,
        then the coordinator's verify of it."""
        from sqlalchemy import select

        version = _make_version(db_session)
        worker = _message(version, author="designer", kind="gate_report", content="spec hotové")
        db_session.add(worker)
        db_session.flush()
        verify = _message(version, author="coordinator", kind="gate_report", content="verifikácia OK")
        db_session.add(verify)
        db_session.flush()
        db_session.refresh(worker)
        db_session.refresh(verify)

        # created_at ties within the transaction; seq is monotonic and increasing.
        assert worker.created_at == verify.created_at
        assert worker.seq < verify.seq

        ordered = (
            db_session.execute(
                select(PipelineMessage)
                .where(PipelineMessage.version_id == version.id)
                .order_by(PipelineMessage.seq.asc())
            )
            .scalars()
            .all()
        )
        assert [m.content for m in ordered] == ["spec hotové", "verifikácia OK"]
