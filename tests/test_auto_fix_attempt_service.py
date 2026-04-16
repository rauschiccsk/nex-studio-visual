"""Tests for :mod:`backend.services.auto_fix_attempt`.

Exercises every public CRUD entry point against the SAVEPOINT-isolated
session provided by ``tests/conftest.py``. Verifies:

* Happy-path list / get / create / update / delete.
* ``ValueError`` on missing ``id`` for get / update / delete.
* ``create`` auto-assigns ``attempt_number`` as
  ``MAX(attempt_number) + 1`` per feat, starts at ``1`` for the first
  attempt, independent per feat.
* Update allow-list — only ``error_description``, ``fix_description``
  and ``delegation_id`` are applied; ``id``, ``feat_id``,
  ``attempt_number`` and ``created_at`` are preserved.
* PATCH semantics — omitted fields stay untouched.
* List filters (``feat_id``, ``delegation_id``) and pagination.
* List ordering is ``attempt_number ASC``.
* ``delete`` removes the row.
* No ``commit`` happens inside the service — the outer transaction
  rolls back cleanly at fixture teardown.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select as sa_select

from backend.db.models.delegations import AutoFixAttempt, Delegation
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat
from backend.schemas.auto_fix_attempt import (
    AutoFixAttemptCreate,
    AutoFixAttemptUpdate,
)
from backend.services import auto_fix_attempt as service


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


def _make_delegation(db_session, **overrides) -> Delegation:
    """Create and persist a Delegation for FK references."""
    defaults = {
        "prompt": f"Fix the thing {uuid.uuid4().hex[:6]}",
    }
    defaults.update(overrides)
    delegation = Delegation(**defaults)
    db_session.add(delegation)
    db_session.flush()
    return delegation


def _payload(feat_id, **overrides) -> AutoFixAttemptCreate:
    """Return an :class:`AutoFixAttemptCreate` payload with sensible defaults."""
    defaults = {
        "feat_id": feat_id,
        "error_description": f"Build failed: {uuid.uuid4().hex[:6]}",
    }
    defaults.update(overrides)
    return AutoFixAttemptCreate(**defaults)


class TestAutoFixAttemptService:
    """Synchronous CRUD coverage for the AutoFixAttempt service."""

    # ------------------------------------------------------------------ create
    def test_create_auto_fix_attempt(self, db_session):
        """``create`` persists the row and returns an ORM instance with server defaults."""
        feat = _make_feat(db_session)

        created = service.create(
            db_session,
            _payload(feat.id, error_description="Initial failure"),
        )

        assert isinstance(created, AutoFixAttempt)
        assert created.id is not None
        assert created.created_at is not None
        assert created.updated_at is not None
        assert created.feat_id == feat.id
        assert created.error_description == "Initial failure"
        # Nullable fields default to None.
        assert created.fix_description is None
        assert created.delegation_id is None
        # Auto-assigned attempt number.
        assert created.attempt_number == 1

    def test_create_with_fix_description(self, db_session):
        """``create`` applies explicit ``fix_description`` when supplied."""
        feat = _make_feat(db_session)

        created = service.create(
            db_session,
            _payload(feat.id, fix_description="Re-ran pip install"),
        )

        assert created.fix_description == "Re-ran pip install"

    def test_create_with_delegation_id(self, db_session):
        """``create`` wires ``delegation_id`` when supplied."""
        feat = _make_feat(db_session)
        delegation = _make_delegation(db_session)

        created = service.create(
            db_session,
            _payload(feat.id, delegation_id=delegation.id),
        )

        assert created.delegation_id == delegation.id

    def test_create_auto_numbers_sequentially(self, db_session):
        """``create`` auto-assigns ``attempt_number`` as MAX + 1 per feat."""
        feat = _make_feat(db_session)

        a1 = service.create(db_session, _payload(feat.id))
        a2 = service.create(db_session, _payload(feat.id))
        a3 = service.create(db_session, _payload(feat.id))

        assert (a1.attempt_number, a2.attempt_number, a3.attempt_number) == (1, 2, 3)

    def test_create_numbering_is_per_feat(self, db_session):
        """Two feats each start their attempt numbering at 1 independently."""
        epic = _make_epic(db_session)
        f1 = _make_feat(db_session, epic=epic)
        f2 = _make_feat(db_session, epic=epic)

        a1_f1 = service.create(db_session, _payload(f1.id))
        a2_f1 = service.create(db_session, _payload(f1.id))
        a1_f2 = service.create(db_session, _payload(f2.id))

        assert a1_f1.attempt_number == 1
        assert a2_f1.attempt_number == 2
        assert a1_f2.attempt_number == 1

    # ------------------------------------------------------------------- get
    def test_get_by_id(self, db_session):
        """``get_by_id`` returns the row when it exists."""
        feat = _make_feat(db_session)
        created = service.create(db_session, _payload(feat.id))

        fetched = service.get_by_id(db_session, created.id)
        assert fetched.id == created.id
        assert fetched.feat_id == feat.id

    def test_get_by_id_missing_raises(self, db_session):
        """``get_by_id`` raises ``ValueError`` for an unknown id."""
        with pytest.raises(ValueError, match="not found"):
            service.get_by_id(db_session, uuid.uuid4())

    # ---------------------------------------------------------------- update
    def test_update_error_description(self, db_session):
        """``error_description`` is mutable."""
        feat = _make_feat(db_session)
        created = service.create(
            db_session,
            _payload(feat.id, error_description="Old error"),
        )

        updated = service.update(
            db_session,
            created.id,
            AutoFixAttemptUpdate(error_description="New error"),
        )

        assert updated.id == created.id
        assert updated.error_description == "New error"

    def test_update_fix_description(self, db_session):
        """``fix_description`` is mutable (typical post-attempt update)."""
        feat = _make_feat(db_session)
        created = service.create(db_session, _payload(feat.id))
        assert created.fix_description is None

        updated = service.update(
            db_session,
            created.id,
            AutoFixAttemptUpdate(fix_description="Reinstalled deps"),
        )

        assert updated.fix_description == "Reinstalled deps"

    def test_update_delegation_id(self, db_session):
        """``delegation_id`` is mutable — set once the fix delegation is spawned."""
        feat = _make_feat(db_session)
        created = service.create(db_session, _payload(feat.id))
        assert created.delegation_id is None

        delegation = _make_delegation(db_session)
        updated = service.update(
            db_session,
            created.id,
            AutoFixAttemptUpdate(delegation_id=delegation.id),
        )

        assert updated.delegation_id == delegation.id

    def test_update_partial(self, db_session):
        """``update`` leaves omitted fields untouched (PATCH semantics)."""
        feat = _make_feat(db_session)
        created = service.create(
            db_session,
            _payload(
                feat.id,
                error_description="Initial",
                fix_description="Initial fix",
            ),
        )

        updated = service.update(
            db_session,
            created.id,
            AutoFixAttemptUpdate(fix_description="Updated fix"),
        )

        assert updated.fix_description == "Updated fix"
        # Unchanged fields preserved.
        assert updated.error_description == "Initial"

    def test_update_preserves_immutable_fields(self, db_session):
        """``id``, ``feat_id``, ``attempt_number`` and ``created_at`` must not change on ``update``."""
        feat = _make_feat(db_session)
        created = service.create(db_session, _payload(feat.id))

        original_id = created.id
        original_feat_id = created.feat_id
        original_attempt_number = created.attempt_number
        original_created_at = created.created_at

        updated = service.update(
            db_session,
            created.id,
            AutoFixAttemptUpdate(
                error_description="New error",
                fix_description="New fix",
            ),
        )

        assert updated.id == original_id
        assert updated.feat_id == original_feat_id
        assert updated.attempt_number == original_attempt_number
        assert updated.created_at == original_created_at

    def test_update_empty_payload_is_noop(self, db_session):
        """An :class:`AutoFixAttemptUpdate` with no fields set leaves the row intact."""
        feat = _make_feat(db_session)
        created = service.create(
            db_session,
            _payload(
                feat.id,
                error_description="Keep me",
                fix_description="Keep this too",
            ),
        )

        updated = service.update(db_session, created.id, AutoFixAttemptUpdate())

        assert updated.error_description == "Keep me"
        assert updated.fix_description == "Keep this too"

    def test_update_missing_raises(self, db_session):
        """``update`` on a non-existent id raises ``ValueError``."""
        with pytest.raises(ValueError, match="not found"):
            service.update(
                db_session,
                uuid.uuid4(),
                AutoFixAttemptUpdate(error_description="nope"),
            )

    # ---------------------------------------------------------------- delete
    def test_delete(self, db_session):
        """``delete`` removes the row; subsequent lookup raises."""
        feat = _make_feat(db_session)
        created = service.create(db_session, _payload(feat.id))

        service.delete(db_session, created.id)

        with pytest.raises(ValueError, match="not found"):
            service.get_by_id(db_session, created.id)

    def test_delete_missing_raises(self, db_session):
        """``delete`` on a non-existent id raises ``ValueError``."""
        with pytest.raises(ValueError, match="not found"):
            service.delete(db_session, uuid.uuid4())

    # ------------------------------------------------------------------ list
    def test_list_all(self, db_session):
        """``list_auto_fix_attempts`` returns every row when no filter is supplied."""
        feat = _make_feat(db_session)
        created_ids: set = set()
        for _ in range(3):
            created_ids.add(service.create(db_session, _payload(feat.id)).id)

        rows = service.list_auto_fix_attempts(db_session)
        assert created_ids.issubset({r.id for r in rows})

    def test_list_filter_by_feat(self, db_session):
        """``list_auto_fix_attempts(feat_id=...)`` returns only that feat's attempts."""
        epic = _make_epic(db_session)
        f1 = _make_feat(db_session, epic=epic)
        f2 = _make_feat(db_session, epic=epic)
        mine = service.create(db_session, _payload(f1.id))
        service.create(db_session, _payload(f2.id))

        rows = service.list_auto_fix_attempts(db_session, feat_id=f1.id)
        assert all(r.feat_id == f1.id for r in rows)
        assert any(r.id == mine.id for r in rows)

    def test_list_filter_by_delegation(self, db_session):
        """``delegation_id`` filter returns only attempts linked to that delegation."""
        feat = _make_feat(db_session)
        delegation = _make_delegation(db_session)
        linked = service.create(
            db_session,
            _payload(feat.id, delegation_id=delegation.id),
        )
        service.create(db_session, _payload(feat.id))  # delegation_id=None

        rows = service.list_auto_fix_attempts(
            db_session,
            delegation_id=delegation.id,
        )
        ids = {r.id for r in rows}
        assert linked.id in ids
        assert all(r.delegation_id == delegation.id for r in rows)

    def test_list_combined_filters(self, db_session):
        """Multiple filters AND together."""
        epic = _make_epic(db_session)
        f1 = _make_feat(db_session, epic=epic)
        f2 = _make_feat(db_session, epic=epic)
        delegation = _make_delegation(db_session)

        match = service.create(
            db_session,
            _payload(f1.id, delegation_id=delegation.id),
        )
        # Different feat, same delegation.
        service.create(db_session, _payload(f2.id, delegation_id=delegation.id))
        # Same feat, different delegation.
        service.create(db_session, _payload(f1.id))

        rows = service.list_auto_fix_attempts(
            db_session,
            feat_id=f1.id,
            delegation_id=delegation.id,
        )
        assert len(rows) == 1
        assert rows[0].id == match.id

    def test_list_ordered_by_attempt_number_asc(self, db_session):
        """Results are ordered by ``attempt_number ASC`` (1, 2, 3, …)."""
        feat = _make_feat(db_session)
        a1 = service.create(db_session, _payload(feat.id))
        a2 = service.create(db_session, _payload(feat.id))
        a3 = service.create(db_session, _payload(feat.id))

        rows = service.list_auto_fix_attempts(db_session, feat_id=feat.id)
        ids_in_order = [r.id for r in rows]
        assert ids_in_order.index(a1.id) < ids_in_order.index(a2.id) < ids_in_order.index(a3.id)

    def test_list_pagination(self, db_session):
        """``limit`` / ``offset`` restrict the result window."""
        feat = _make_feat(db_session)
        for _ in range(5):
            service.create(db_session, _payload(feat.id))

        first_page = service.list_auto_fix_attempts(
            db_session,
            feat_id=feat.id,
            limit=2,
            offset=0,
        )
        second_page = service.list_auto_fix_attempts(
            db_session,
            feat_id=feat.id,
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
        feat = _make_feat(db_session)
        created = service.create(db_session, _payload(feat.id))
        # ``in_transaction()`` must be True — commit would clear it.
        assert db_session.in_transaction()
        # Row is visible within the session after flush.
        assert service.get_by_id(db_session, created.id).id == created.id
