"""Tests for :mod:`backend.services.professional_specification`.

Exercises every public CRUD entry point against the SAVEPOINT-isolated
session provided by ``tests/conftest.py``. Verifies:

* Happy-path list / get / create / update / delete.
* ``ValueError`` on missing ``id`` for get / update / delete.
* ``create`` persists every supplied column — ``version`` default via
  schema, ``approved_by`` / ``approved_at`` nullable.
* Update allow-list — only ``content``, ``version``, ``approved_by``
  and ``approved_at`` are applied; ``project_id``, ``raw_spec_id``,
  ``id`` and ``created_at`` are preserved.
* PATCH semantics — omitted fields stay untouched.
* Auto-stamp ``approved_at`` when ``approved_by`` transitions from
  ``None`` to a user UUID without an explicit ``approved_at`` in the
  payload.
* Explicit ``approved_at`` wins over the auto-stamp.
* No auto-stamp when the specification is already approved.
* No UNIQUE constraints — multiple versions of the same
  ``(project_id, raw_spec_id)`` pair coexist.
* List filters (``project_id``, ``raw_spec_id``, ``approved_by``,
  ``version``) and pagination.
* List ordering is ``created_at DESC`` (newest version first).
* ``delete`` removes the row; no RESTRICT guard is needed because
  ``professional_specifications`` has no inbound FKs.
* No ``commit`` happens inside the service — the outer transaction
  rolls back cleanly at fixture teardown.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.specifications import (
    ProfessionalSpecification,
    RawSpecification,
)
from backend.schemas.professional_specification import (
    ProfessionalSpecificationCreate,
    ProfessionalSpecificationUpdate,
)
from backend.services import professional_specification as service


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


def _make_raw_spec(
    db_session,
    *,
    project: Project | None = None,
    user: User | None = None,
    **overrides,
) -> RawSpecification:
    """Create a RawSpecification for FK references."""
    if user is None:
        user = _make_user(db_session)
    if project is None:
        project = _make_project(db_session, user=user)
    defaults = {
        "project_id": project.id,
        "input_text": "Customer specification text for testing.",
        "created_by": user.id,
    }
    defaults.update(overrides)
    spec = RawSpecification(**defaults)
    db_session.add(spec)
    db_session.flush()
    return spec


def _payload(
    project_id,
    raw_spec_id,
    **overrides,
) -> ProfessionalSpecificationCreate:
    """Return a :class:`ProfessionalSpecificationCreate` payload with defaults."""
    defaults = {
        "project_id": project_id,
        "raw_spec_id": raw_spec_id,
        "content": "# Professional Specification\n\n## Business requirements...",
    }
    defaults.update(overrides)
    return ProfessionalSpecificationCreate(**defaults)


class TestProfessionalSpecificationService:
    """Synchronous CRUD coverage for the ProfessionalSpecification service."""

    # ------------------------------------------------------------------ create
    def test_create_specification(self, db_session):
        """``create`` persists the row and returns an ORM instance with server defaults."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        raw_spec = _make_raw_spec(db_session, project=project, user=user)

        created = service.create(db_session, _payload(project.id, raw_spec.id))

        assert isinstance(created, ProfessionalSpecification)
        assert created.id is not None
        assert created.created_at is not None
        assert created.updated_at is not None
        assert created.project_id == project.id
        assert created.raw_spec_id == raw_spec.id
        assert created.content == "# Professional Specification\n\n## Business requirements..."
        # Schema-level default.
        assert created.version == 1
        # Nullable approval columns default to None.
        assert created.approved_by is None
        assert created.approved_at is None

    def test_create_with_all_fields(self, db_session):
        """``create`` applies every supplied field, including approval columns."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        raw_spec = _make_raw_spec(db_session, project=project, user=user)
        approver = _make_user(db_session)
        approved_at = datetime(2026, 4, 15, 10, 0, tzinfo=timezone.utc)

        created = service.create(
            db_session,
            _payload(
                project.id,
                raw_spec.id,
                version=3,
                approved_by=approver.id,
                approved_at=approved_at,
            ),
        )

        assert created.version == 3
        assert created.approved_by == approver.id
        assert created.approved_at == approved_at

    def test_create_many_versions_same_raw_spec(self, db_session):
        """No UNIQUE constraint — many rows may share ``(project_id, raw_spec_id)``."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        raw_spec = _make_raw_spec(db_session, project=project, user=user)

        v1 = service.create(db_session, _payload(project.id, raw_spec.id, version=1))
        v2 = service.create(db_session, _payload(project.id, raw_spec.id, version=2))
        v3 = service.create(db_session, _payload(project.id, raw_spec.id, version=3))

        assert len({v1.id, v2.id, v3.id}) == 3
        assert all(s.project_id == project.id for s in (v1, v2, v3))
        assert all(s.raw_spec_id == raw_spec.id for s in (v1, v2, v3))

    # ------------------------------------------------------------------- get
    def test_get_by_id(self, db_session):
        """``get_by_id`` returns the row when it exists."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        raw_spec = _make_raw_spec(db_session, project=project, user=user)
        created = service.create(db_session, _payload(project.id, raw_spec.id))

        fetched = service.get_by_id(db_session, created.id)
        assert fetched.id == created.id
        assert fetched.project_id == project.id
        assert fetched.raw_spec_id == raw_spec.id

    def test_get_by_id_missing_raises(self, db_session):
        """``get_by_id`` raises ``ValueError`` for an unknown id."""
        with pytest.raises(ValueError, match="not found"):
            service.get_by_id(db_session, uuid.uuid4())

    # ---------------------------------------------------------------- update
    def test_update_content_and_version(self, db_session):
        """``update`` patches mutable fields."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        raw_spec = _make_raw_spec(db_session, project=project, user=user)
        created = service.create(db_session, _payload(project.id, raw_spec.id))

        updated = service.update(
            db_session,
            created.id,
            ProfessionalSpecificationUpdate(content="# Updated\n", version=2),
        )

        assert updated.id == created.id
        assert updated.content == "# Updated\n"
        assert updated.version == 2

    def test_update_auto_stamps_approved_at(self, db_session):
        """``approved_at`` is auto-stamped when ``approved_by`` transitions from None."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        raw_spec = _make_raw_spec(db_session, project=project, user=user)
        approver = _make_user(db_session)
        created = service.create(db_session, _payload(project.id, raw_spec.id))
        assert created.approved_by is None
        assert created.approved_at is None

        before = datetime.now(tz=timezone.utc)
        updated = service.update(
            db_session,
            created.id,
            ProfessionalSpecificationUpdate(approved_by=approver.id),
        )
        after = datetime.now(tz=timezone.utc)

        assert updated.approved_by == approver.id
        assert updated.approved_at is not None
        assert before <= updated.approved_at <= after

    def test_update_explicit_approved_at_wins(self, db_session):
        """An explicit ``approved_at`` in the payload overrides the auto-stamp."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        raw_spec = _make_raw_spec(db_session, project=project, user=user)
        approver = _make_user(db_session)
        created = service.create(db_session, _payload(project.id, raw_spec.id))
        explicit_ts = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)

        updated = service.update(
            db_session,
            created.id,
            ProfessionalSpecificationUpdate(
                approved_by=approver.id,
                approved_at=explicit_ts,
            ),
        )

        assert updated.approved_by == approver.id
        assert updated.approved_at == explicit_ts

    def test_update_no_auto_stamp_when_already_approved(self, db_session):
        """``approved_at`` is not re-stamped on subsequent approvals."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        raw_spec = _make_raw_spec(db_session, project=project, user=user)
        first_approver = _make_user(db_session)
        second_approver = _make_user(db_session)
        original_ts = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)

        created = service.create(
            db_session,
            _payload(
                project.id,
                raw_spec.id,
                approved_by=first_approver.id,
                approved_at=original_ts,
            ),
        )

        updated = service.update(
            db_session,
            created.id,
            ProfessionalSpecificationUpdate(approved_by=second_approver.id),
        )

        assert updated.approved_by == second_approver.id
        # Unchanged — no auto-stamp because spec was already approved.
        assert updated.approved_at == original_ts

    def test_update_partial_only_content(self, db_session):
        """``update`` leaves omitted fields untouched (PATCH semantics)."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        raw_spec = _make_raw_spec(db_session, project=project, user=user)
        approver = _make_user(db_session)
        ts = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
        created = service.create(
            db_session,
            _payload(
                project.id,
                raw_spec.id,
                version=5,
                approved_by=approver.id,
                approved_at=ts,
            ),
        )

        updated = service.update(
            db_session,
            created.id,
            ProfessionalSpecificationUpdate(content="# Patched\n"),
        )

        # Only content changed; every other field preserved.
        assert updated.content == "# Patched\n"
        assert updated.version == 5
        assert updated.approved_by == approver.id
        assert updated.approved_at == ts

    def test_update_preserves_immutable_fields(self, db_session):
        """``id``, ``project_id``, ``raw_spec_id`` and ``created_at`` must not change across ``update``."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        raw_spec = _make_raw_spec(db_session, project=project, user=user)
        created = service.create(db_session, _payload(project.id, raw_spec.id))

        original_id = created.id
        original_project_id = created.project_id
        original_raw_spec_id = created.raw_spec_id
        original_created_at = created.created_at

        updated = service.update(
            db_session,
            created.id,
            ProfessionalSpecificationUpdate(content="# Updated\n", version=2),
        )

        assert updated.id == original_id
        assert updated.project_id == original_project_id
        assert updated.raw_spec_id == original_raw_spec_id
        assert updated.created_at == original_created_at

    def test_update_empty_payload_is_noop(self, db_session):
        """A :class:`ProfessionalSpecificationUpdate` with no fields leaves the row intact."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        raw_spec = _make_raw_spec(db_session, project=project, user=user)
        created = service.create(
            db_session,
            _payload(project.id, raw_spec.id, version=4),
        )

        updated = service.update(
            db_session,
            created.id,
            ProfessionalSpecificationUpdate(),
        )

        assert updated.content == "# Professional Specification\n\n## Business requirements..."
        assert updated.version == 4
        assert updated.approved_by is None
        assert updated.approved_at is None

    def test_update_missing_raises(self, db_session):
        """``update`` on a non-existent id raises ``ValueError``."""
        with pytest.raises(ValueError, match="not found"):
            service.update(
                db_session,
                uuid.uuid4(),
                ProfessionalSpecificationUpdate(content="# nope"),
            )

    # ---------------------------------------------------------------- delete
    def test_delete(self, db_session):
        """``delete`` removes the row; subsequent lookup raises."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        raw_spec = _make_raw_spec(db_session, project=project, user=user)
        created = service.create(db_session, _payload(project.id, raw_spec.id))

        service.delete(db_session, created.id)

        with pytest.raises(ValueError, match="not found"):
            service.get_by_id(db_session, created.id)

    def test_delete_missing_raises(self, db_session):
        """``delete`` on a non-existent id raises ``ValueError``."""
        with pytest.raises(ValueError, match="not found"):
            service.delete(db_session, uuid.uuid4())

    def test_delete_one_version_leaves_siblings_intact(self, db_session):
        """Deleting one version does not affect sibling versions."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        raw_spec = _make_raw_spec(db_session, project=project, user=user)
        v1 = service.create(db_session, _payload(project.id, raw_spec.id, version=1))
        v2 = service.create(db_session, _payload(project.id, raw_spec.id, version=2))

        service.delete(db_session, v1.id)

        assert service.get_by_id(db_session, v2.id).id == v2.id

    # ------------------------------------------------------------------ list
    def test_list_all(self, db_session):
        """``list_professional_specifications`` returns every row when no filter is supplied."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        raw_spec = _make_raw_spec(db_session, project=project, user=user)
        created_ids: set = set()
        for i in range(3):
            created_ids.add(
                service.create(
                    db_session,
                    _payload(project.id, raw_spec.id, version=i + 1),
                ).id
            )

        rows = service.list_professional_specifications(db_session)
        assert created_ids.issubset({r.id for r in rows})

    def test_list_filter_by_project(self, db_session):
        """``list_professional_specifications(project_id=...)`` returns only that project's specs."""
        user = _make_user(db_session)
        p1 = _make_project(db_session, user=user)
        p2 = _make_project(db_session, user=user)
        r1 = _make_raw_spec(db_session, project=p1, user=user)
        r2 = _make_raw_spec(db_session, project=p2, user=user)

        mine = service.create(db_session, _payload(p1.id, r1.id))
        service.create(db_session, _payload(p2.id, r2.id))

        rows = service.list_professional_specifications(db_session, project_id=p1.id)
        assert all(r.project_id == p1.id for r in rows)
        assert any(r.id == mine.id for r in rows)

    def test_list_filter_by_raw_spec(self, db_session):
        """``raw_spec_id`` filter returns only specs derived from that raw spec."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        r1 = _make_raw_spec(db_session, project=project, user=user)
        r2 = _make_raw_spec(db_session, project=project, user=user)

        mine = service.create(db_session, _payload(project.id, r1.id))
        theirs = service.create(db_session, _payload(project.id, r2.id))

        rows = service.list_professional_specifications(db_session, raw_spec_id=r1.id)
        ids = {r.id for r in rows}
        assert mine.id in ids
        assert theirs.id not in ids

    def test_list_filter_by_approved_by(self, db_session):
        """``approved_by`` filter returns only specs approved by that user."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        raw_spec = _make_raw_spec(db_session, project=project, user=user)
        approver = _make_user(db_session)
        other = _make_user(db_session)

        approved = service.create(
            db_session,
            _payload(project.id, raw_spec.id, approved_by=approver.id),
        )
        other_approved = service.create(
            db_session,
            _payload(project.id, raw_spec.id, approved_by=other.id, version=2),
        )
        unapproved = service.create(
            db_session,
            _payload(project.id, raw_spec.id, version=3),
        )

        rows = service.list_professional_specifications(db_session, approved_by=approver.id)
        ids = {r.id for r in rows}
        assert approved.id in ids
        assert other_approved.id not in ids
        assert unapproved.id not in ids

    def test_list_filter_by_version(self, db_session):
        """``version`` filter returns only specs at that version number."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        raw_spec = _make_raw_spec(db_session, project=project, user=user)

        v1 = service.create(db_session, _payload(project.id, raw_spec.id, version=1))
        v2 = service.create(db_session, _payload(project.id, raw_spec.id, version=2))

        rows = service.list_professional_specifications(
            db_session,
            project_id=project.id,
            version=2,
        )
        ids = {r.id for r in rows}
        assert v2.id in ids
        assert v1.id not in ids

    def test_list_combined_filters(self, db_session):
        """Multiple filters AND together."""
        user = _make_user(db_session)
        p1 = _make_project(db_session, user=user)
        p2 = _make_project(db_session, user=user)
        r1 = _make_raw_spec(db_session, project=p1, user=user)
        r2 = _make_raw_spec(db_session, project=p1, user=user)
        r3 = _make_raw_spec(db_session, project=p2, user=user)

        match = service.create(
            db_session,
            _payload(p1.id, r1.id, version=2),
        )
        # Different project.
        service.create(db_session, _payload(p2.id, r3.id, version=2))
        # Different raw_spec_id.
        service.create(db_session, _payload(p1.id, r2.id, version=2))
        # Different version.
        service.create(db_session, _payload(p1.id, r1.id, version=1))

        rows = service.list_professional_specifications(
            db_session,
            project_id=p1.id,
            raw_spec_id=r1.id,
            version=2,
        )
        assert len(rows) == 1
        assert rows[0].id == match.id

    def test_list_ordered_by_created_at_desc(self, db_session):
        """Results are ordered newest-first to match the Specification Pipeline UI.

        Rows created inside a single transaction share the same ``NOW()``
        value (PostgreSQL ``now()`` is transaction-scoped), so the test
        overrides ``created_at`` explicitly to produce unambiguous
        ordering — the intent is to pin the service-layer ``ORDER BY
        created_at DESC`` contract, not to measure Postgres clock
        resolution.
        """
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        raw_spec = _make_raw_spec(db_session, project=project, user=user)

        base_time = datetime(2026, 4, 15, 10, 0, tzinfo=timezone.utc)
        s1 = service.create(db_session, _payload(project.id, raw_spec.id, version=1))
        s2 = service.create(db_session, _payload(project.id, raw_spec.id, version=2))
        s3 = service.create(db_session, _payload(project.id, raw_spec.id, version=3))
        s1.created_at = base_time
        s2.created_at = base_time + timedelta(minutes=1)
        s3.created_at = base_time + timedelta(minutes=2)
        db_session.flush()

        rows = service.list_professional_specifications(
            db_session,
            project_id=project.id,
        )
        ids_in_order = [r.id for r in rows]
        # Newest first.
        assert ids_in_order.index(s3.id) < ids_in_order.index(s2.id) < ids_in_order.index(s1.id)

    def test_list_pagination(self, db_session):
        """``limit`` / ``offset`` restrict the result window."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        raw_spec = _make_raw_spec(db_session, project=project, user=user)
        for i in range(5):
            service.create(db_session, _payload(project.id, raw_spec.id, version=i + 1))

        first_page = service.list_professional_specifications(
            db_session,
            project_id=project.id,
            limit=2,
            offset=0,
        )
        second_page = service.list_professional_specifications(
            db_session,
            project_id=project.id,
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
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        raw_spec = _make_raw_spec(db_session, project=project, user=user)
        created = service.create(db_session, _payload(project.id, raw_spec.id))
        # ``in_transaction()`` must be True — commit would clear it.
        assert db_session.in_transaction()
        # Row is visible within the session after flush.
        assert service.get_by_id(db_session, created.id).id == created.id
