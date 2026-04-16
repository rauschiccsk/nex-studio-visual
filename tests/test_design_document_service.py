"""Tests for :mod:`backend.services.design_document`.

Exercises every public CRUD entry point against the SAVEPOINT-isolated
session provided by ``tests/conftest.py``. Verifies:

* Happy-path list / get / create / update / delete.
* ``ValueError`` on missing ``id`` for get / update / delete.
* ``create`` persists every supplied column — ``version`` default via
  schema, ``module_id`` / ``approved_by`` / ``approved_at`` nullable.
* Update allow-list — only ``module_id``, ``content``, ``version``,
  ``approved_by`` and ``approved_at`` are applied; ``project_id``,
  ``doc_type``, ``id`` and ``created_at`` are preserved.
* PATCH semantics — omitted fields stay untouched.
* Auto-stamp ``approved_at`` when ``approved_by`` transitions from
  ``None`` to a user UUID without an explicit ``approved_at`` in the
  payload.
* Explicit ``approved_at`` wins over the auto-stamp.
* No auto-stamp when the document is already approved.
* No UNIQUE constraints — multiple versions of the same
  ``(project_id, module_id, doc_type)`` triple coexist.
* List filters (``project_id``, ``module_id``, ``doc_type``,
  ``approved_by``) and pagination.
* List ordering is ``created_at DESC`` (newest version first).
* ``delete`` removes the row; no RESTRICT guard is needed because
  ``design_documents`` has no inbound FKs.
* No ``commit`` happens inside the service — the outer transaction
  rolls back cleanly at fixture teardown.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from backend.db.models.foundation import User
from backend.db.models.projects import Project, ProjectModule
from backend.db.models.specifications import DesignDocument
from backend.schemas.design_document import (
    DesignDocumentCreate,
    DesignDocumentUpdate,
)
from backend.services import design_document as service


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


def _make_module(db_session, *, project: Project | None = None, **overrides) -> ProjectModule:
    """Create a ProjectModule for FK references."""
    if project is None:
        project = _make_project(db_session)
    suffix = uuid.uuid4().hex[:4].upper()
    defaults = {
        "project_id": project.id,
        "code": f"M{suffix}",
        "name": f"Module {suffix}",
        "category": "General",
    }
    defaults.update(overrides)
    module = ProjectModule(**defaults)
    db_session.add(module)
    db_session.flush()
    return module


def _payload(project_id, **overrides) -> DesignDocumentCreate:
    """Return a :class:`DesignDocumentCreate` payload with sensible defaults."""
    defaults = {
        "project_id": project_id,
        "doc_type": "design",
        "content": "# Design Document\n\nTest content.",
    }
    defaults.update(overrides)
    return DesignDocumentCreate(**defaults)


class TestDesignDocumentService:
    """Synchronous CRUD coverage for the DesignDocument service."""

    # ------------------------------------------------------------------ create
    def test_create_document(self, db_session):
        """``create`` persists the row and returns an ORM instance with server defaults."""
        project = _make_project(db_session)

        created = service.create(db_session, _payload(project.id))

        assert isinstance(created, DesignDocument)
        assert created.id is not None
        assert created.created_at is not None
        assert created.updated_at is not None
        assert created.project_id == project.id
        assert created.module_id is None
        assert created.doc_type == "design"
        assert created.content == "# Design Document\n\nTest content."
        # Schema-level default.
        assert created.version == 1
        # Nullable approval columns default to None.
        assert created.approved_by is None
        assert created.approved_at is None

    def test_create_behavior_document(self, db_session):
        """``create`` accepts doc_type='behavior'."""
        project = _make_project(db_session)

        created = service.create(
            db_session,
            _payload(project.id, doc_type="behavior", content="# Behavior\n"),
        )

        assert created.doc_type == "behavior"
        assert created.content == "# Behavior\n"

    def test_create_module_level_document(self, db_session):
        """``create`` accepts a module_id to scope the document to a module."""
        project = _make_project(db_session)
        module = _make_module(db_session, project=project)

        created = service.create(
            db_session,
            _payload(project.id, module_id=module.id),
        )

        assert created.module_id == module.id

    def test_create_with_all_fields(self, db_session):
        """``create`` applies every supplied field, including approval columns."""
        project = _make_project(db_session)
        approver = _make_user(db_session)
        approved_at = datetime(2026, 4, 15, 10, 0, tzinfo=timezone.utc)

        created = service.create(
            db_session,
            _payload(
                project.id,
                version=3,
                approved_by=approver.id,
                approved_at=approved_at,
            ),
        )

        assert created.version == 3
        assert created.approved_by == approver.id
        assert created.approved_at == approved_at

    def test_create_many_versions_same_triple(self, db_session):
        """No UNIQUE constraint — many rows may share ``(project_id, module_id, doc_type)``."""
        project = _make_project(db_session)

        v1 = service.create(db_session, _payload(project.id, version=1))
        v2 = service.create(db_session, _payload(project.id, version=2))
        v3 = service.create(db_session, _payload(project.id, version=3))

        assert len({v1.id, v2.id, v3.id}) == 3
        assert all(d.project_id == project.id for d in (v1, v2, v3))
        assert all(d.doc_type == "design" for d in (v1, v2, v3))
        assert all(d.module_id is None for d in (v1, v2, v3))

    # ------------------------------------------------------------------- get
    def test_get_by_id(self, db_session):
        """``get_by_id`` returns the row when it exists."""
        project = _make_project(db_session)
        created = service.create(db_session, _payload(project.id))

        fetched = service.get_by_id(db_session, created.id)
        assert fetched.id == created.id
        assert fetched.project_id == project.id

    def test_get_by_id_missing_raises(self, db_session):
        """``get_by_id`` raises ``ValueError`` for an unknown id."""
        with pytest.raises(ValueError, match="not found"):
            service.get_by_id(db_session, uuid.uuid4())

    # ---------------------------------------------------------------- update
    def test_update_content_and_version(self, db_session):
        """``update`` patches mutable fields."""
        project = _make_project(db_session)
        created = service.create(db_session, _payload(project.id))

        updated = service.update(
            db_session,
            created.id,
            DesignDocumentUpdate(content="# Updated\n", version=2),
        )

        assert updated.id == created.id
        assert updated.content == "# Updated\n"
        assert updated.version == 2

    def test_update_module_id(self, db_session):
        """``module_id`` is mutable — a document can be re-scoped to a module."""
        project = _make_project(db_session)
        module = _make_module(db_session, project=project)
        created = service.create(db_session, _payload(project.id))
        assert created.module_id is None

        updated = service.update(
            db_session,
            created.id,
            DesignDocumentUpdate(module_id=module.id),
        )

        assert updated.module_id == module.id

    def test_update_auto_stamps_approved_at(self, db_session):
        """``approved_at`` is auto-stamped when ``approved_by`` transitions from None."""
        project = _make_project(db_session)
        approver = _make_user(db_session)
        created = service.create(db_session, _payload(project.id))
        assert created.approved_by is None
        assert created.approved_at is None

        before = datetime.now(tz=timezone.utc)
        updated = service.update(
            db_session,
            created.id,
            DesignDocumentUpdate(approved_by=approver.id),
        )
        after = datetime.now(tz=timezone.utc)

        assert updated.approved_by == approver.id
        assert updated.approved_at is not None
        assert before <= updated.approved_at <= after

    def test_update_explicit_approved_at_wins(self, db_session):
        """An explicit ``approved_at`` in the payload overrides the auto-stamp."""
        project = _make_project(db_session)
        approver = _make_user(db_session)
        created = service.create(db_session, _payload(project.id))
        explicit_ts = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)

        updated = service.update(
            db_session,
            created.id,
            DesignDocumentUpdate(approved_by=approver.id, approved_at=explicit_ts),
        )

        assert updated.approved_by == approver.id
        assert updated.approved_at == explicit_ts

    def test_update_no_auto_stamp_when_already_approved(self, db_session):
        """``approved_at`` is not re-stamped on subsequent approvals."""
        project = _make_project(db_session)
        first_approver = _make_user(db_session)
        second_approver = _make_user(db_session)
        original_ts = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)

        created = service.create(
            db_session,
            _payload(
                project.id,
                approved_by=first_approver.id,
                approved_at=original_ts,
            ),
        )

        updated = service.update(
            db_session,
            created.id,
            DesignDocumentUpdate(approved_by=second_approver.id),
        )

        assert updated.approved_by == second_approver.id
        # Unchanged — no auto-stamp because doc was already approved.
        assert updated.approved_at == original_ts

    def test_update_partial_only_content(self, db_session):
        """``update`` leaves omitted fields untouched (PATCH semantics)."""
        project = _make_project(db_session)
        approver = _make_user(db_session)
        ts = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
        created = service.create(
            db_session,
            _payload(
                project.id,
                version=5,
                approved_by=approver.id,
                approved_at=ts,
            ),
        )

        updated = service.update(
            db_session,
            created.id,
            DesignDocumentUpdate(content="# Patched\n"),
        )

        # Only content changed; every other field preserved.
        assert updated.content == "# Patched\n"
        assert updated.version == 5
        assert updated.approved_by == approver.id
        assert updated.approved_at == ts

    def test_update_preserves_immutable_fields(self, db_session):
        """``id``, ``project_id``, ``doc_type`` and ``created_at`` must not change across ``update``."""
        project = _make_project(db_session)
        created = service.create(db_session, _payload(project.id))

        original_id = created.id
        original_project_id = created.project_id
        original_doc_type = created.doc_type
        original_created_at = created.created_at

        updated = service.update(
            db_session,
            created.id,
            DesignDocumentUpdate(content="# Updated\n", version=2),
        )

        assert updated.id == original_id
        assert updated.project_id == original_project_id
        assert updated.doc_type == original_doc_type
        assert updated.created_at == original_created_at

    def test_update_empty_payload_is_noop(self, db_session):
        """A :class:`DesignDocumentUpdate` with no fields set leaves the row intact."""
        project = _make_project(db_session)
        created = service.create(db_session, _payload(project.id, version=4))

        updated = service.update(db_session, created.id, DesignDocumentUpdate())

        assert updated.content == "# Design Document\n\nTest content."
        assert updated.version == 4
        assert updated.approved_by is None
        assert updated.approved_at is None

    def test_update_missing_raises(self, db_session):
        """``update`` on a non-existent id raises ``ValueError``."""
        with pytest.raises(ValueError, match="not found"):
            service.update(
                db_session,
                uuid.uuid4(),
                DesignDocumentUpdate(content="# nope"),
            )

    # ---------------------------------------------------------------- delete
    def test_delete(self, db_session):
        """``delete`` removes the row; subsequent lookup raises."""
        project = _make_project(db_session)
        created = service.create(db_session, _payload(project.id))

        service.delete(db_session, created.id)

        with pytest.raises(ValueError, match="not found"):
            service.get_by_id(db_session, created.id)

    def test_delete_missing_raises(self, db_session):
        """``delete`` on a non-existent id raises ``ValueError``."""
        with pytest.raises(ValueError, match="not found"):
            service.delete(db_session, uuid.uuid4())

    def test_delete_one_version_leaves_siblings_intact(self, db_session):
        """Deleting one version does not affect sibling versions on the same triple."""
        project = _make_project(db_session)
        v1 = service.create(db_session, _payload(project.id, version=1))
        v2 = service.create(db_session, _payload(project.id, version=2))

        service.delete(db_session, v1.id)

        assert service.get_by_id(db_session, v2.id).id == v2.id

    # ------------------------------------------------------------------ list
    def test_list_all(self, db_session):
        """``list_design_documents`` returns every row when no filter is supplied."""
        project = _make_project(db_session)
        created_ids: set = set()
        for _ in range(3):
            created_ids.add(service.create(db_session, _payload(project.id)).id)

        rows = service.list_design_documents(db_session)
        assert created_ids.issubset({r.id for r in rows})

    def test_list_filter_by_project(self, db_session):
        """``list_design_documents(project_id=...)`` returns only that project's documents."""
        p1 = _make_project(db_session)
        p2 = _make_project(db_session)
        mine = service.create(db_session, _payload(p1.id))
        service.create(db_session, _payload(p2.id))

        rows = service.list_design_documents(db_session, project_id=p1.id)
        assert all(r.project_id == p1.id for r in rows)
        assert any(r.id == mine.id for r in rows)

    def test_list_filter_by_module(self, db_session):
        """``module_id`` filter returns only documents scoped to that module."""
        project = _make_project(db_session)
        module = _make_module(db_session, project=project)
        module_doc = service.create(db_session, _payload(project.id, module_id=module.id))
        foundation_doc = service.create(db_session, _payload(project.id))

        rows = service.list_design_documents(db_session, module_id=module.id)
        row_ids = {r.id for r in rows}
        assert module_doc.id in row_ids
        # Foundation doc has module_id IS NULL — excluded by the filter.
        assert foundation_doc.id not in row_ids

    def test_list_filter_by_doc_type(self, db_session):
        """``doc_type`` filter returns only matching documents."""
        project = _make_project(db_session)
        design_doc = service.create(db_session, _payload(project.id, doc_type="design"))
        behavior_doc = service.create(db_session, _payload(project.id, doc_type="behavior"))

        design_rows = service.list_design_documents(
            db_session,
            project_id=project.id,
            doc_type="design",
        )
        design_ids = {r.id for r in design_rows}
        assert design_doc.id in design_ids
        assert behavior_doc.id not in design_ids

        behavior_rows = service.list_design_documents(
            db_session,
            project_id=project.id,
            doc_type="behavior",
        )
        behavior_ids = {r.id for r in behavior_rows}
        assert behavior_doc.id in behavior_ids
        assert design_doc.id not in behavior_ids

    def test_list_filter_by_approved_by(self, db_session):
        """``approved_by`` filter returns only documents approved by that user."""
        project = _make_project(db_session)
        approver = _make_user(db_session)
        other = _make_user(db_session)

        approved = service.create(
            db_session,
            _payload(project.id, approved_by=approver.id),
        )
        other_approved = service.create(
            db_session,
            _payload(project.id, approved_by=other.id),
        )
        unapproved = service.create(db_session, _payload(project.id))

        rows = service.list_design_documents(db_session, approved_by=approver.id)
        ids = {r.id for r in rows}
        assert approved.id in ids
        assert other_approved.id not in ids
        assert unapproved.id not in ids

    def test_list_combined_filters(self, db_session):
        """Multiple filters AND together."""
        p1 = _make_project(db_session)
        p2 = _make_project(db_session)
        module = _make_module(db_session, project=p1)

        match = service.create(
            db_session,
            _payload(p1.id, module_id=module.id, doc_type="design"),
        )
        # Different project.
        service.create(db_session, _payload(p2.id, doc_type="design"))
        # Different doc_type.
        service.create(
            db_session,
            _payload(p1.id, module_id=module.id, doc_type="behavior"),
        )
        # Foundation-level (module_id IS NULL).
        service.create(db_session, _payload(p1.id, doc_type="design"))

        rows = service.list_design_documents(
            db_session,
            project_id=p1.id,
            module_id=module.id,
            doc_type="design",
        )
        assert len(rows) == 1
        assert rows[0].id == match.id

    def test_list_ordered_by_created_at_desc(self, db_session):
        """Results are ordered newest-first to match the version-history UI.

        Rows created inside a single transaction share the same ``NOW()``
        value (PostgreSQL ``now()`` is transaction-scoped), so the test
        overrides ``created_at`` explicitly to produce unambiguous
        ordering — the intent is to pin the service-layer ``ORDER BY
        created_at DESC`` contract, not to measure Postgres clock
        resolution.
        """
        project = _make_project(db_session)

        base_time = datetime(2026, 4, 15, 10, 0, tzinfo=timezone.utc)
        d1 = service.create(db_session, _payload(project.id, version=1))
        d2 = service.create(db_session, _payload(project.id, version=2))
        d3 = service.create(db_session, _payload(project.id, version=3))
        d1.created_at = base_time
        d2.created_at = base_time + timedelta(minutes=1)
        d3.created_at = base_time + timedelta(minutes=2)
        db_session.flush()

        rows = service.list_design_documents(db_session, project_id=project.id)
        ids_in_order = [r.id for r in rows]
        # Newest first.
        assert ids_in_order.index(d3.id) < ids_in_order.index(d2.id) < ids_in_order.index(d1.id)

    def test_list_pagination(self, db_session):
        """``limit`` / ``offset`` restrict the result window."""
        project = _make_project(db_session)
        for i in range(5):
            service.create(db_session, _payload(project.id, version=i + 1))

        first_page = service.list_design_documents(
            db_session,
            project_id=project.id,
            limit=2,
            offset=0,
        )
        second_page = service.list_design_documents(
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
        project = _make_project(db_session)
        created = service.create(db_session, _payload(project.id))
        # ``in_transaction()`` must be True — commit would clear it.
        assert db_session.in_transaction()
        # Row is visible within the session after flush.
        assert service.get_by_id(db_session, created.id).id == created.id
