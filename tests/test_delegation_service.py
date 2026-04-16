"""Tests for :mod:`backend.services.delegation`.

Exercises every public CRUD entry point against the SAVEPOINT-isolated
session provided by ``tests/conftest.py``. Verifies:

* Happy-path list / get / create / update / delete.
* ``ValueError`` on missing ``id`` for get / update / delete.
* ``create`` supports all four parent FKs (``task_id``, ``feat_id``,
  ``bug_fix_task_id``, ``bug_id``) independently, plus ad-hoc parentless
  delegations.
* ``create`` applies DB-level server defaults via the Pydantic schema
  when optional fields are omitted (``cc_agent='ubuntu_cc'``,
  ``status='pending'``, ``started_at`` auto-stamped).
* Update allow-list — only ``status``, ``raw_output``, ``commit_hash``,
  ``started_at`` and ``completed_at`` are applied; ``id``, ``task_id``,
  ``feat_id``, ``bug_fix_task_id``, ``bug_id``, ``cc_agent``,
  ``prompt`` and ``created_at`` are preserved.
* PATCH semantics — omitted fields stay untouched.
* List filters (``task_id``, ``feat_id``, ``bug_fix_task_id``,
  ``bug_id``, ``status``, ``cc_agent``) and pagination.
* List ordering is ``started_at DESC``.
* ``delete`` removes the row and its inbound FKs behave per DB rules —
  ``execution_logs`` cascade-delete, ``auto_fix_attempts.delegation_id``
  is NULL-ed.
* No ``commit`` happens inside the service — the outer transaction
  rolls back cleanly at fixture teardown.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select as sa_select

from backend.db.models.bugs import Bug, BugFixTask
from backend.db.models.delegations import AutoFixAttempt, Delegation, ExecutionLog
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat, Task
from backend.schemas.delegation import DelegationCreate, DelegationUpdate
from backend.services import delegation as service


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


def _make_bug(db_session, *, project: Project | None = None, user: User | None = None, **overrides) -> Bug:
    """Create and persist a Bug for FK references."""
    if project is None:
        project = _make_project(db_session)
    if user is None:
        user = _make_user(db_session)
    next_number = (
        db_session.execute(
            sa_select(Bug.bug_number).where(Bug.project_id == project.id).order_by(Bug.bug_number.desc()).limit(1)
        ).scalar()
        or 0
    ) + 1
    defaults = {
        "project_id": project.id,
        "bug_number": next_number,
        "title": f"Bug {uuid.uuid4().hex[:6]}",
        "description": "Something broke.",
        "severity": "minor",
        "created_by": user.id,
    }
    defaults.update(overrides)
    bug = Bug(**defaults)
    db_session.add(bug)
    db_session.flush()
    return bug


def _make_bug_fix_task(db_session, *, bug: Bug | None = None, **overrides) -> BugFixTask:
    """Create and persist a BugFixTask for FK references."""
    if bug is None:
        bug = _make_bug(db_session)
    next_number = (
        db_session.execute(
            sa_select(BugFixTask.number).where(BugFixTask.bug_id == bug.id).order_by(BugFixTask.number.desc()).limit(1)
        ).scalar()
        or 0
    ) + 1
    defaults = {
        "bug_id": bug.id,
        "number": next_number,
        "title": f"Fix task {uuid.uuid4().hex[:6]}",
        "task_type": "backend",
    }
    defaults.update(overrides)
    fix_task = BugFixTask(**defaults)
    db_session.add(fix_task)
    db_session.flush()
    return fix_task


def _payload(**overrides) -> DelegationCreate:
    """Return a :class:`DelegationCreate` payload with sensible defaults."""
    defaults = {
        "prompt": f"Do the thing {uuid.uuid4().hex[:6]}",
    }
    defaults.update(overrides)
    return DelegationCreate(**defaults)


class TestDelegationService:
    """Synchronous CRUD coverage for the Delegation service."""

    # ------------------------------------------------------------------ create
    def test_create_delegation_ad_hoc(self, db_session):
        """``create`` persists a parentless delegation with DB-level defaults."""
        created = service.create(
            db_session,
            _payload(prompt="Perform an ad-hoc repair"),
        )

        assert isinstance(created, Delegation)
        assert created.id is not None
        assert created.created_at is not None
        assert created.updated_at is not None
        assert created.prompt == "Perform an ad-hoc repair"
        # Parent FKs default to None.
        assert created.task_id is None
        assert created.feat_id is None
        assert created.bug_fix_task_id is None
        assert created.bug_id is None
        # Schema / DB defaults.
        assert created.cc_agent == "ubuntu_cc"
        assert created.status == "pending"
        assert created.raw_output is None
        assert created.commit_hash is None
        # started_at is auto-stamped via DB server_default.
        assert created.started_at is not None
        assert created.completed_at is None

    def test_create_with_task_parent(self, db_session):
        """``create`` accepts a ``task_id`` FK."""
        task = _make_task(db_session)

        created = service.create(db_session, _payload(task_id=task.id))

        assert created.task_id == task.id
        assert created.feat_id is None
        assert created.bug_fix_task_id is None
        assert created.bug_id is None

    def test_create_with_feat_parent(self, db_session):
        """``create`` accepts a ``feat_id`` FK (feat-level delegation)."""
        feat = _make_feat(db_session)

        created = service.create(db_session, _payload(feat_id=feat.id))

        assert created.feat_id == feat.id

    def test_create_with_bug_fix_task_parent(self, db_session):
        """``create`` accepts a ``bug_fix_task_id`` FK."""
        fix_task = _make_bug_fix_task(db_session)

        created = service.create(db_session, _payload(bug_fix_task_id=fix_task.id))

        assert created.bug_fix_task_id == fix_task.id

    def test_create_with_bug_parent(self, db_session):
        """``create`` accepts a ``bug_id`` FK (direct bug delegation)."""
        bug = _make_bug(db_session)

        created = service.create(db_session, _payload(bug_id=bug.id))

        assert created.bug_id == bug.id

    def test_create_with_custom_status(self, db_session):
        """``create`` applies a non-default ``status`` when supplied."""
        created = service.create(db_session, _payload(status="running"))

        assert created.status == "running"

    def test_create_with_raw_output_and_commit_hash(self, db_session):
        """``create`` accepts explicit ``raw_output`` / ``commit_hash`` for backfill."""
        created = service.create(
            db_session,
            _payload(
                status="done",
                raw_output='{"event": "result", "ok": true}',
                commit_hash="a" * 40,
            ),
        )

        assert created.raw_output == '{"event": "result", "ok": true}'
        assert created.commit_hash == "a" * 40

    def test_create_with_explicit_started_and_completed(self, db_session):
        """``create`` honours explicit ``started_at`` / ``completed_at`` timestamps."""
        started = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        completed = datetime(2025, 1, 1, 12, 30, 0, tzinfo=timezone.utc)

        created = service.create(
            db_session,
            _payload(
                status="done",
                started_at=started,
                completed_at=completed,
            ),
        )

        assert created.started_at == started
        assert created.completed_at == completed

    def test_create_accepts_all_statuses(self, db_session):
        """``create`` accepts every value permitted by the CHECK constraint."""
        for status in ("pending", "running", "done", "failed"):
            created = service.create(db_session, _payload(status=status))
            assert created.status == status

    # ------------------------------------------------------------------- get
    def test_get_by_id(self, db_session):
        """``get_by_id`` returns the row when it exists."""
        created = service.create(db_session, _payload())

        fetched = service.get_by_id(db_session, created.id)
        assert fetched.id == created.id
        assert fetched.prompt == created.prompt

    def test_get_by_id_missing_raises(self, db_session):
        """``get_by_id`` raises ``ValueError`` for an unknown id."""
        with pytest.raises(ValueError, match="not found"):
            service.get_by_id(db_session, uuid.uuid4())

    # ---------------------------------------------------------------- update
    def test_update_status(self, db_session):
        """``status`` is mutable (the typical lifecycle progression)."""
        created = service.create(db_session, _payload())
        assert created.status == "pending"

        updated = service.update(
            db_session,
            created.id,
            DelegationUpdate(status="running"),
        )

        assert updated.id == created.id
        assert updated.status == "running"

    def test_update_raw_output(self, db_session):
        """``raw_output`` is mutable (streamed as the delegation runs)."""
        created = service.create(db_session, _payload())
        assert created.raw_output is None

        updated = service.update(
            db_session,
            created.id,
            DelegationUpdate(raw_output='{"event":"chunk"}\n'),
        )

        assert updated.raw_output == '{"event":"chunk"}\n'

    def test_update_commit_hash(self, db_session):
        """``commit_hash`` is mutable (extracted from CC output at completion)."""
        created = service.create(db_session, _payload())
        assert created.commit_hash is None

        updated = service.update(
            db_session,
            created.id,
            DelegationUpdate(commit_hash="b" * 40),
        )

        assert updated.commit_hash == "b" * 40

    def test_update_completed_at(self, db_session):
        """``completed_at`` is mutable — stamped when the delegation finishes."""
        created = service.create(db_session, _payload())
        assert created.completed_at is None

        completed = datetime(2025, 6, 1, 9, 0, 0, tzinfo=timezone.utc)
        updated = service.update(
            db_session,
            created.id,
            DelegationUpdate(completed_at=completed),
        )

        assert updated.completed_at == completed

    def test_update_started_at_override(self, db_session):
        """``started_at`` is mutable (admin / backfill correction)."""
        created = service.create(db_session, _payload())
        new_started = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

        updated = service.update(
            db_session,
            created.id,
            DelegationUpdate(started_at=new_started),
        )

        assert updated.started_at == new_started

    def test_update_partial(self, db_session):
        """``update`` leaves omitted fields untouched (PATCH semantics)."""
        created = service.create(
            db_session,
            _payload(
                status="running",
                raw_output="initial output",
            ),
        )

        updated = service.update(
            db_session,
            created.id,
            DelegationUpdate(status="done"),
        )

        assert updated.status == "done"
        # Unchanged fields preserved.
        assert updated.raw_output == "initial output"

    def test_update_preserves_immutable_fields(self, db_session):
        """``id``, parent FKs, ``cc_agent``, ``prompt`` and ``created_at`` must not change."""
        task = _make_task(db_session)
        created = service.create(
            db_session,
            _payload(task_id=task.id, prompt="Immutable prompt"),
        )

        original_id = created.id
        original_task_id = created.task_id
        original_cc_agent = created.cc_agent
        original_prompt = created.prompt
        original_created_at = created.created_at

        updated = service.update(
            db_session,
            created.id,
            DelegationUpdate(status="running", raw_output="progress"),
        )

        assert updated.id == original_id
        assert updated.task_id == original_task_id
        assert updated.cc_agent == original_cc_agent
        assert updated.prompt == original_prompt
        assert updated.created_at == original_created_at

    def test_update_empty_payload_is_noop(self, db_session):
        """A :class:`DelegationUpdate` with no fields set leaves the row intact."""
        created = service.create(
            db_session,
            _payload(
                status="running",
                raw_output="Keep me",
                commit_hash="c" * 40,
            ),
        )

        updated = service.update(db_session, created.id, DelegationUpdate())

        assert updated.status == "running"
        assert updated.raw_output == "Keep me"
        assert updated.commit_hash == "c" * 40

    def test_update_missing_raises(self, db_session):
        """``update`` on a non-existent id raises ``ValueError``."""
        with pytest.raises(ValueError, match="not found"):
            service.update(
                db_session,
                uuid.uuid4(),
                DelegationUpdate(status="done"),
            )

    # ---------------------------------------------------------------- delete
    def test_delete(self, db_session):
        """``delete`` removes the row; subsequent lookup raises."""
        created = service.create(db_session, _payload())

        service.delete(db_session, created.id)

        with pytest.raises(ValueError, match="not found"):
            service.get_by_id(db_session, created.id)

    def test_delete_missing_raises(self, db_session):
        """``delete`` on a non-existent id raises ``ValueError``."""
        with pytest.raises(ValueError, match="not found"):
            service.delete(db_session, uuid.uuid4())

    def test_delete_cascades_execution_logs(self, db_session):
        """Deleting a delegation cascades its ``execution_logs`` rows."""
        delegation = service.create(db_session, _payload())
        log = ExecutionLog(
            delegation_id=delegation.id,
            status="done",
        )
        db_session.add(log)
        db_session.flush()
        log_id = log.id

        service.delete(db_session, delegation.id)
        db_session.expire_all()

        remaining = db_session.execute(
            sa_select(ExecutionLog).where(ExecutionLog.id == log_id),
        ).scalar_one_or_none()
        assert remaining is None

    def test_delete_nulls_auto_fix_attempt_delegation_id(self, db_session):
        """Deleting a delegation NULLs ``auto_fix_attempts.delegation_id``."""
        feat = _make_feat(db_session)
        delegation = service.create(db_session, _payload())

        attempt = AutoFixAttempt(
            feat_id=feat.id,
            attempt_number=1,
            error_description="boom",
            delegation_id=delegation.id,
        )
        db_session.add(attempt)
        db_session.flush()
        attempt_id = attempt.id

        service.delete(db_session, delegation.id)
        db_session.expire_all()

        remaining = db_session.execute(
            sa_select(AutoFixAttempt).where(AutoFixAttempt.id == attempt_id),
        ).scalar_one()
        assert remaining.delegation_id is None

    # ------------------------------------------------------------------ list
    def test_list_all(self, db_session):
        """``list_delegations`` returns every row when no filter is supplied."""
        created_ids: set = set()
        for _ in range(3):
            created_ids.add(service.create(db_session, _payload()).id)

        rows = service.list_delegations(db_session, limit=1000)
        assert created_ids.issubset({r.id for r in rows})

    def test_list_filter_by_task(self, db_session):
        """``task_id`` filter returns only delegations for that task."""
        task = _make_task(db_session)
        mine = service.create(db_session, _payload(task_id=task.id))
        service.create(db_session, _payload())  # parentless

        rows = service.list_delegations(db_session, task_id=task.id)
        assert all(r.task_id == task.id for r in rows)
        assert any(r.id == mine.id for r in rows)

    def test_list_filter_by_feat(self, db_session):
        """``feat_id`` filter returns only delegations for that feat."""
        feat = _make_feat(db_session)
        mine = service.create(db_session, _payload(feat_id=feat.id))
        service.create(db_session, _payload())

        rows = service.list_delegations(db_session, feat_id=feat.id)
        assert all(r.feat_id == feat.id for r in rows)
        assert any(r.id == mine.id for r in rows)

    def test_list_filter_by_bug_fix_task(self, db_session):
        """``bug_fix_task_id`` filter returns only delegations for that fix task."""
        fix_task = _make_bug_fix_task(db_session)
        mine = service.create(db_session, _payload(bug_fix_task_id=fix_task.id))
        service.create(db_session, _payload())

        rows = service.list_delegations(db_session, bug_fix_task_id=fix_task.id)
        assert all(r.bug_fix_task_id == fix_task.id for r in rows)
        assert any(r.id == mine.id for r in rows)

    def test_list_filter_by_bug(self, db_session):
        """``bug_id`` filter returns only delegations for that bug."""
        bug = _make_bug(db_session)
        mine = service.create(db_session, _payload(bug_id=bug.id))
        service.create(db_session, _payload())

        rows = service.list_delegations(db_session, bug_id=bug.id)
        assert all(r.bug_id == bug.id for r in rows)
        assert any(r.id == mine.id for r in rows)

    def test_list_filter_by_status(self, db_session):
        """``status`` filter returns only matching delegations."""
        running = service.create(db_session, _payload(status="running"))
        pending = service.create(db_session, _payload(status="pending"))

        rows = service.list_delegations(db_session, status="running")
        ids = {r.id for r in rows}
        assert running.id in ids
        assert pending.id not in ids

    def test_list_filter_by_cc_agent(self, db_session):
        """``cc_agent`` filter returns only matching delegations."""
        mine = service.create(db_session, _payload(cc_agent="ubuntu_cc"))

        rows = service.list_delegations(db_session, cc_agent="ubuntu_cc")
        assert all(r.cc_agent == "ubuntu_cc" for r in rows)
        assert any(r.id == mine.id for r in rows)

    def test_list_combined_filters(self, db_session):
        """Multiple filters AND together."""
        task_a = _make_task(db_session)
        task_b = _make_task(db_session)

        match = service.create(
            db_session,
            _payload(task_id=task_a.id, status="running"),
        )
        # Different task, matching status.
        service.create(db_session, _payload(task_id=task_b.id, status="running"))
        # Matching task, different status.
        service.create(db_session, _payload(task_id=task_a.id, status="pending"))

        rows = service.list_delegations(
            db_session,
            task_id=task_a.id,
            status="running",
        )
        assert len(rows) == 1
        assert rows[0].id == match.id

    def test_list_ordered_by_started_at_desc(self, db_session):
        """Results are ordered by ``started_at DESC`` (most recent first)."""
        now = datetime.now(tz=timezone.utc)
        oldest = service.create(
            db_session,
            _payload(started_at=now - timedelta(hours=2)),
        )
        middle = service.create(
            db_session,
            _payload(started_at=now - timedelta(hours=1)),
        )
        newest = service.create(
            db_session,
            _payload(started_at=now),
        )

        rows = service.list_delegations(db_session, limit=1000)
        ids_in_order = [r.id for r in rows]
        # Newest-first ordering.
        assert ids_in_order.index(newest.id) < ids_in_order.index(middle.id) < ids_in_order.index(oldest.id)

    def test_list_pagination(self, db_session):
        """``limit`` / ``offset`` restrict the result window."""
        task = _make_task(db_session)
        for _ in range(5):
            service.create(db_session, _payload(task_id=task.id))

        first_page = service.list_delegations(
            db_session,
            task_id=task.id,
            limit=2,
            offset=0,
        )
        second_page = service.list_delegations(
            db_session,
            task_id=task.id,
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
        created = service.create(db_session, _payload())
        # ``in_transaction()`` must be True — commit would clear it.
        assert db_session.in_transaction()
        # Row is visible within the session after flush.
        assert service.get_by_id(db_session, created.id).id == created.id
