"""Tests for :mod:`backend.services.execution_log`.

Exercises every public CRUD entry point against the SAVEPOINT-isolated
session provided by ``tests/conftest.py``. Verifies:

* Happy-path list / get / create / update / delete.
* ``ValueError`` on missing ``id`` for get / update / delete.
* ``create`` applies the DB-level ``server_default`` for
  ``commit_verified`` via the Pydantic schema when omitted.
* ``create`` accepts ``task_id`` (optional parent) and all the numeric
  metrics fields.
* Update allow-list — only ``status``, ``duration_seconds``,
  ``input_tokens``, ``output_tokens``, ``total_cost_usd``,
  ``commit_hash`` and ``commit_verified`` are applied; ``id``,
  ``delegation_id``, ``task_id`` and ``created_at`` are preserved.
* PATCH semantics — omitted fields stay untouched.
* List filters (``delegation_id``, ``task_id``, ``status``,
  ``commit_verified``) and pagination.
* List ordering is ``created_at DESC``.
* ``delete`` removes the row — no inbound FKs to check.
* No ``commit`` happens inside the service — the outer transaction
  rolls back cleanly at fixture teardown.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select as sa_select

from backend.db.models.delegations import Delegation, ExecutionLog
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat, Task
from backend.schemas.execution_log import (
    ExecutionLogCreate,
    ExecutionLogUpdate,
)
from backend.services import execution_log as service


def _make_user(db_session, **overrides) -> User:
    """Create and persist a User for FK references."""
    defaults = {
        "username": f"user_{uuid.uuid4().hex[:8]}",
        "email": f"{uuid.uuid4().hex[:8]}@example.com",
        "password_hash": "hashed_password_placeholder",
        "role": "ri",
    }
    defaults.update(overrides)
    user = User(**defaults)
    db_session.add(user)
    db_session.flush()
    return user


def _make_project(db_session, *, user: User | None = None, **overrides) -> Project:
    """Create and persist a Project for FK references."""
    if user is None:
        user = _make_user(db_session)
    suffix = uuid.uuid4().hex[:8]
    defaults = {
        "name": f"Project {suffix}",
        "slug": f"project-{suffix}",
        "category": "multimodule",
        "description": "Test project description",
        "created_by": user.id,
    }
    defaults.update(overrides)
    project = Project(**defaults)
    db_session.add(project)
    db_session.flush()
    return project


def _make_epic(db_session, *, project: Project | None = None, **overrides) -> Epic:
    """Create and persist an Epic for FK references."""
    if project is None:
        project = _make_project(db_session)
    next_number = (
        db_session.execute(
            sa_select(Epic.number).where(Epic.project_id == project.id).order_by(Epic.number.desc()).limit(1)
        ).scalar()
        or 0
    ) + 1
    defaults = {
        "project_id": project.id,
        "number": next_number,
        "title": f"Epic {uuid.uuid4().hex[:6]}",
    }
    defaults.update(overrides)
    epic = Epic(**defaults)
    db_session.add(epic)
    db_session.flush()
    return epic


def _make_feat(db_session, *, epic: Epic | None = None, **overrides) -> Feat:
    """Create and persist a Feat for FK references."""
    if epic is None:
        epic = _make_epic(db_session)
    next_number = (
        db_session.execute(
            sa_select(Feat.number).where(Feat.epic_id == epic.id).order_by(Feat.number.desc()).limit(1)
        ).scalar()
        or 0
    ) + 1
    defaults = {
        "epic_id": epic.id,
        "number": next_number,
        "title": f"Feat {uuid.uuid4().hex[:6]}",
    }
    defaults.update(overrides)
    feat = Feat(**defaults)
    db_session.add(feat)
    db_session.flush()
    return feat


def _make_task(db_session, *, feat: Feat | None = None, **overrides) -> Task:
    """Create and persist a Task for FK references."""
    if feat is None:
        feat = _make_feat(db_session)
    next_number = (
        db_session.execute(
            sa_select(Task.number).where(Task.feat_id == feat.id).order_by(Task.number.desc()).limit(1)
        ).scalar()
        or 0
    ) + 1
    defaults = {
        "feat_id": feat.id,
        "number": next_number,
        "title": f"Task {uuid.uuid4().hex[:6]}",
        "task_type": "backend",
    }
    defaults.update(overrides)
    task = Task(**defaults)
    db_session.add(task)
    db_session.flush()
    return task


def _make_delegation(db_session, **overrides) -> Delegation:
    """Create and persist a Delegation for FK references."""
    defaults = {
        "prompt": f"Delegation prompt {uuid.uuid4().hex[:6]}",
    }
    defaults.update(overrides)
    delegation = Delegation(**defaults)
    db_session.add(delegation)
    db_session.flush()
    return delegation


def _payload(db_session, *, delegation: Delegation | None = None, **overrides) -> ExecutionLogCreate:
    """Return an :class:`ExecutionLogCreate` payload with sensible defaults."""
    if delegation is None:
        delegation = _make_delegation(db_session)
    defaults = {
        "delegation_id": delegation.id,
        "status": "done",
    }
    defaults.update(overrides)
    return ExecutionLogCreate(**defaults)


class TestExecutionLogService:
    """Synchronous CRUD coverage for the ExecutionLog service."""

    # ------------------------------------------------------------------ create
    def test_create_minimal(self, db_session):
        """``create`` persists a log with just the required fields."""
        delegation = _make_delegation(db_session)

        created = service.create(db_session, _payload(db_session, delegation=delegation))

        assert isinstance(created, ExecutionLog)
        assert created.id is not None
        assert created.created_at is not None
        assert created.updated_at is not None
        assert created.delegation_id == delegation.id
        assert created.status == "done"
        # Defaults.
        assert created.task_id is None
        assert created.duration_seconds is None
        assert created.input_tokens is None
        assert created.output_tokens is None
        assert created.total_cost_usd is None
        assert created.commit_hash is None
        # Schema / DB default.
        assert created.commit_verified is False

    def test_create_with_task(self, db_session):
        """``create`` accepts an optional ``task_id`` FK."""
        task = _make_task(db_session)

        created = service.create(
            db_session,
            _payload(db_session, task_id=task.id),
        )

        assert created.task_id == task.id

    def test_create_with_all_metrics(self, db_session):
        """``create`` persists every optional numeric metric and commit hash."""
        created = service.create(
            db_session,
            _payload(
                db_session,
                duration_seconds=42,
                input_tokens=1000,
                output_tokens=250,
                total_cost_usd=Decimal("0.123456"),
                commit_hash="a" * 40,
            ),
        )

        assert created.duration_seconds == 42
        assert created.input_tokens == 1000
        assert created.output_tokens == 250
        assert created.total_cost_usd == Decimal("0.123456")
        assert created.commit_hash == "a" * 40

    def test_create_with_commit_verified_true(self, db_session):
        """``create`` honours an explicit ``commit_verified=True``."""
        created = service.create(
            db_session,
            _payload(db_session, commit_verified=True),
        )

        assert created.commit_verified is True

    @pytest.mark.parametrize("status", ["done", "failed"])
    def test_create_accepts_all_statuses(self, db_session, status):
        """``create`` accepts every value permitted by the CHECK constraint."""
        created = service.create(
            db_session,
            _payload(db_session, status=status),
        )

        assert created.status == status

    # ------------------------------------------------------------------- get
    def test_get_by_id(self, db_session):
        """``get_by_id`` returns the row when it exists."""
        created = service.create(db_session, _payload(db_session))

        fetched = service.get_by_id(db_session, created.id)
        assert fetched.id == created.id
        assert fetched.status == created.status

    def test_get_by_id_missing_raises(self, db_session):
        """``get_by_id`` raises ``ValueError`` for an unknown id."""
        with pytest.raises(ValueError, match="not found"):
            service.get_by_id(db_session, uuid.uuid4())

    # ---------------------------------------------------------------- update
    def test_update_status(self, db_session):
        """``status`` is mutable (``done`` <-> ``failed``)."""
        created = service.create(db_session, _payload(db_session, status="done"))

        updated = service.update(
            db_session,
            created.id,
            ExecutionLogUpdate(status="failed"),
        )

        assert updated.id == created.id
        assert updated.status == "failed"

    def test_update_metrics(self, db_session):
        """Numeric metrics are mutable — typically populated at completion."""
        created = service.create(db_session, _payload(db_session))
        assert created.duration_seconds is None
        assert created.input_tokens is None

        updated = service.update(
            db_session,
            created.id,
            ExecutionLogUpdate(
                duration_seconds=17,
                input_tokens=500,
                output_tokens=150,
                total_cost_usd=Decimal("0.050000"),
            ),
        )

        assert updated.duration_seconds == 17
        assert updated.input_tokens == 500
        assert updated.output_tokens == 150
        assert updated.total_cost_usd == Decimal("0.050000")

    def test_update_commit_hash(self, db_session):
        """``commit_hash`` is mutable (extracted from CC output)."""
        created = service.create(db_session, _payload(db_session))
        assert created.commit_hash is None

        updated = service.update(
            db_session,
            created.id,
            ExecutionLogUpdate(commit_hash="b" * 40),
        )

        assert updated.commit_hash == "b" * 40

    def test_update_commit_verified_flip_to_true(self, db_session):
        """``commit_verified`` can be flipped to True after GitHub verification."""
        created = service.create(db_session, _payload(db_session))
        assert created.commit_verified is False

        updated = service.update(
            db_session,
            created.id,
            ExecutionLogUpdate(commit_verified=True),
        )

        assert updated.commit_verified is True

    def test_update_partial(self, db_session):
        """``update`` leaves omitted fields untouched (PATCH semantics)."""
        created = service.create(
            db_session,
            _payload(
                db_session,
                status="done",
                commit_hash="c" * 40,
            ),
        )

        updated = service.update(
            db_session,
            created.id,
            ExecutionLogUpdate(status="failed"),
        )

        assert updated.status == "failed"
        # Unchanged fields preserved.
        assert updated.commit_hash == "c" * 40

    def test_update_preserves_immutable_fields(self, db_session):
        """``id``, ``delegation_id``, ``task_id`` and ``created_at`` must not change."""
        task = _make_task(db_session)
        created = service.create(
            db_session,
            _payload(db_session, task_id=task.id),
        )

        original_id = created.id
        original_delegation_id = created.delegation_id
        original_task_id = created.task_id
        original_created_at = created.created_at

        updated = service.update(
            db_session,
            created.id,
            ExecutionLogUpdate(status="failed", commit_hash="d" * 40),
        )

        assert updated.id == original_id
        assert updated.delegation_id == original_delegation_id
        assert updated.task_id == original_task_id
        assert updated.created_at == original_created_at

    def test_update_empty_payload_is_noop(self, db_session):
        """An :class:`ExecutionLogUpdate` with no fields set leaves the row intact."""
        created = service.create(
            db_session,
            _payload(
                db_session,
                status="done",
                duration_seconds=7,
                commit_hash="e" * 40,
            ),
        )

        updated = service.update(db_session, created.id, ExecutionLogUpdate())

        assert updated.status == "done"
        assert updated.duration_seconds == 7
        assert updated.commit_hash == "e" * 40

    def test_update_missing_raises(self, db_session):
        """``update`` on a non-existent id raises ``ValueError``."""
        with pytest.raises(ValueError, match="not found"):
            service.update(
                db_session,
                uuid.uuid4(),
                ExecutionLogUpdate(status="failed"),
            )

    # ---------------------------------------------------------------- delete
    def test_delete(self, db_session):
        """``delete`` removes the row; subsequent lookup raises."""
        created = service.create(db_session, _payload(db_session))

        service.delete(db_session, created.id)

        with pytest.raises(ValueError, match="not found"):
            service.get_by_id(db_session, created.id)

    def test_delete_missing_raises(self, db_session):
        """``delete`` on a non-existent id raises ``ValueError``."""
        with pytest.raises(ValueError, match="not found"):
            service.delete(db_session, uuid.uuid4())

    # ------------------------------------------------------------------ list
    def test_list_all(self, db_session):
        """``list_execution_logs`` returns every row when no filter is supplied."""
        created_ids: set = set()
        for _ in range(3):
            created_ids.add(service.create(db_session, _payload(db_session)).id)

        rows = service.list_execution_logs(db_session, limit=1000)
        assert created_ids.issubset({r.id for r in rows})

    def test_list_filter_by_delegation(self, db_session):
        """``delegation_id`` filter returns only logs for that delegation."""
        delegation = _make_delegation(db_session)
        mine = service.create(db_session, _payload(db_session, delegation=delegation))
        # Unrelated log, different delegation.
        service.create(db_session, _payload(db_session))

        rows = service.list_execution_logs(db_session, delegation_id=delegation.id)
        assert all(r.delegation_id == delegation.id for r in rows)
        assert any(r.id == mine.id for r in rows)

    def test_list_filter_by_task(self, db_session):
        """``task_id`` filter returns only logs for that task."""
        task = _make_task(db_session)
        mine = service.create(db_session, _payload(db_session, task_id=task.id))
        # Unrelated log, no task.
        service.create(db_session, _payload(db_session))

        rows = service.list_execution_logs(db_session, task_id=task.id)
        assert all(r.task_id == task.id for r in rows)
        assert any(r.id == mine.id for r in rows)

    def test_list_filter_by_status(self, db_session):
        """``status`` filter returns only matching logs."""
        done = service.create(db_session, _payload(db_session, status="done"))
        failed = service.create(db_session, _payload(db_session, status="failed"))

        rows = service.list_execution_logs(db_session, status="failed")
        ids = {r.id for r in rows}
        assert failed.id in ids
        assert done.id not in ids

    def test_list_filter_by_commit_verified_false(self, db_session):
        """``commit_verified=False`` is the natural query for the verification job."""
        unverified = service.create(db_session, _payload(db_session))
        verified = service.create(
            db_session,
            _payload(db_session, commit_verified=True),
        )

        rows = service.list_execution_logs(db_session, commit_verified=False)
        ids = {r.id for r in rows}
        assert unverified.id in ids
        assert verified.id not in ids

    def test_list_filter_by_commit_verified_true(self, db_session):
        """``commit_verified=True`` lists already-verified commits."""
        verified = service.create(
            db_session,
            _payload(db_session, commit_verified=True),
        )
        unverified = service.create(db_session, _payload(db_session))

        rows = service.list_execution_logs(db_session, commit_verified=True)
        ids = {r.id for r in rows}
        assert verified.id in ids
        assert unverified.id not in ids

    def test_list_combined_filters(self, db_session):
        """Multiple filters AND together."""
        delegation = _make_delegation(db_session)

        match = service.create(
            db_session,
            _payload(db_session, delegation=delegation, status="failed"),
        )
        # Same delegation, different status.
        service.create(
            db_session,
            _payload(db_session, delegation=delegation, status="done"),
        )
        # Different delegation, matching status.
        service.create(db_session, _payload(db_session, status="failed"))

        rows = service.list_execution_logs(
            db_session,
            delegation_id=delegation.id,
            status="failed",
        )
        assert len(rows) == 1
        assert rows[0].id == match.id

    def test_list_ordered_by_created_at_desc(self, db_session):
        """Results are ordered by ``created_at DESC`` (most recent first).

        Rows created inside a single transaction share the same ``NOW()``
        value (PostgreSQL ``now()`` is transaction-scoped), so the test
        overrides ``created_at`` explicitly to produce unambiguous
        ordering — the intent is to pin the service-layer ``ORDER BY
        created_at DESC`` contract, not to measure Postgres clock
        resolution.
        """
        delegation = _make_delegation(db_session)
        oldest = service.create(db_session, _payload(db_session, delegation=delegation))
        middle = service.create(db_session, _payload(db_session, delegation=delegation))
        newest = service.create(db_session, _payload(db_session, delegation=delegation))

        base_time = datetime(2026, 4, 15, 10, 0, tzinfo=timezone.utc)
        oldest.created_at = base_time
        middle.created_at = base_time + timedelta(minutes=1)
        newest.created_at = base_time + timedelta(minutes=2)
        db_session.flush()

        rows = service.list_execution_logs(
            db_session,
            delegation_id=delegation.id,
            limit=1000,
        )
        ids_in_order = [r.id for r in rows]
        # Newest-first ordering.
        assert ids_in_order.index(newest.id) < ids_in_order.index(middle.id) < ids_in_order.index(oldest.id)

    def test_list_pagination(self, db_session):
        """``limit`` / ``offset`` restrict the result window."""
        delegation = _make_delegation(db_session)
        for _ in range(5):
            service.create(db_session, _payload(db_session, delegation=delegation))

        first_page = service.list_execution_logs(
            db_session,
            delegation_id=delegation.id,
            limit=2,
            offset=0,
        )
        second_page = service.list_execution_logs(
            db_session,
            delegation_id=delegation.id,
            limit=2,
            offset=2,
        )
        assert len(first_page) == 2
        assert len(second_page) == 2
        first_ids = {r.id for r in first_page}
        second_ids = {r.id for r in second_page}
        assert first_ids.isdisjoint(second_ids)

    # --------------------------------------------------------------- commit
    def test_service_does_not_commit(self, db_session):
        """Service calls only ``flush`` — rows vanish when the outer transaction rolls back.

        This asserts the contract that transaction control belongs to
        the router, not the service. The SAVEPOINT-isolated
        ``db_session`` fixture rolls back at teardown; a service that
        called ``commit`` would leak rows into the test database and
        break other tests.
        """
        created = service.create(db_session, _payload(db_session))
        # ``in_transaction()`` must be True — commit would clear it.
        assert db_session.in_transaction()
        # Row is visible within the session after flush.
        assert service.get_by_id(db_session, created.id).id == created.id
