"""Tests for :mod:`backend.services.kb_document`.

Exercises every public CRUD entry point against the SAVEPOINT-isolated
session provided by ``tests/conftest.py``. Verifies:

* Happy-path list / get / create / update / delete.
* ``ValueError`` on missing ``id`` for get / update / delete.
* ``create`` persists every supplied column — ``project_id`` /
  ``module_id`` / ``qdrant_collection`` / ``qdrant_point_id`` /
  ``indexed_at`` nullable.
* ``create`` accepts ``project_id=None`` (ICC-wide document per
  DESIGN.md §1.4).
* Update allow-list — only ``module_id``, ``title``, ``file_path``,
  ``qdrant_collection``, ``qdrant_point_id`` and ``indexed_at`` are
  applied; ``project_id``, ``doc_category``, ``id`` and ``created_at``
  are preserved even when smuggled past the schema.
* PATCH semantics — omitted / ``None`` fields stay untouched.
* No UNIQUE constraints — multiple rows sharing ``(project_id,
  module_id, doc_category, file_path)`` coexist.
* List filters (``project_id``, ``module_id``, ``doc_category``,
  ``qdrant_point_id``) and pagination.
* List ordering is ``created_at DESC`` (newest first).
* ``delete`` removes the row; no RESTRICT guard is needed because
  ``kb_documents`` has no inbound FKs.
* No ``commit`` happens inside the service — the outer transaction
  rolls back cleanly at fixture teardown.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from backend.db.models.foundation import User
from backend.db.models.kb import KbDocument
from backend.db.models.projects import Project, ProjectModule
from backend.schemas.kb_document import (
    KbDocumentCreate,
    KbDocumentUpdate,
)
from backend.services import kb_document as service


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


def _payload(project_id, **overrides) -> KbDocumentCreate:
    """Return a :class:`KbDocumentCreate` payload with sensible defaults."""
    suffix = uuid.uuid4().hex[:8]
    defaults = {
        "project_id": project_id,
        "title": f"KB Doc {suffix}",
        "file_path": f"/opt/knowledge/{suffix}.md",
        "doc_category": "standards",
    }
    defaults.update(overrides)
    return KbDocumentCreate(**defaults)


class TestKbDocumentService:
    """Synchronous CRUD coverage for the KbDocument service."""

    # ------------------------------------------------------------------ create
    def test_create_document(self, db_session):
        """``create`` persists the row and returns an ORM instance with server defaults."""
        project = _make_project(db_session)

        created = service.create(db_session, _payload(project.id))

        assert isinstance(created, KbDocument)
        assert created.id is not None
        assert created.created_at is not None
        assert created.updated_at is not None
        assert created.project_id == project.id
        assert created.module_id is None
        assert created.doc_category == "standards"
        # Optional indexing columns default to None.
        assert created.qdrant_collection is None
        assert created.qdrant_point_id is None
        assert created.indexed_at is None

    def test_create_icc_wide_document(self, db_session):
        """``create`` accepts ``project_id=None`` for ICC-wide documents (DESIGN.md §1.4)."""
        created = service.create(db_session, _payload(None))

        assert created.project_id is None

    def test_create_module_level_document(self, db_session):
        """``create`` accepts a ``module_id`` to scope the document to a module."""
        project = _make_project(db_session)
        module = _make_module(db_session, project=project)

        created = service.create(
            db_session,
            _payload(project.id, module_id=module.id),
        )

        assert created.module_id == module.id

    def test_create_with_all_fields(self, db_session):
        """``create`` applies every supplied field, including optional indexing columns."""
        project = _make_project(db_session)
        module = _make_module(db_session, project=project)
        indexed_at = datetime(2026, 4, 15, 10, 0, tzinfo=timezone.utc)

        created = service.create(
            db_session,
            _payload(
                project.id,
                module_id=module.id,
                doc_category="decisions",
                qdrant_collection="nex_studio_kb",
                qdrant_point_id="qp_00001",
                indexed_at=indexed_at,
            ),
        )

        assert created.doc_category == "decisions"
        assert created.qdrant_collection == "nex_studio_kb"
        assert created.qdrant_point_id == "qp_00001"
        assert created.indexed_at == indexed_at

    def test_create_all_doc_categories(self, db_session):
        """Every allowed ``doc_category`` literal persists through the service."""
        project = _make_project(db_session)
        for cat in [
            "standards",
            "decisions",
            "lessons",
            "patterns",
            "design",
            "behavior",
            "session",
        ]:
            created = service.create(
                db_session,
                _payload(project.id, doc_category=cat),
            )
            assert created.doc_category == cat

    def test_create_many_rows_same_natural_key(self, db_session):
        """No UNIQUE constraints — multiple rows on the same natural key coexist."""
        project = _make_project(db_session)
        module = _make_module(db_session, project=project)

        a = service.create(
            db_session,
            _payload(
                project.id,
                module_id=module.id,
                doc_category="standards",
                file_path="/opt/knowledge/same.md",
                title="Same Doc",
            ),
        )
        b = service.create(
            db_session,
            _payload(
                project.id,
                module_id=module.id,
                doc_category="standards",
                file_path="/opt/knowledge/same.md",
                title="Same Doc",
            ),
        )
        c = service.create(
            db_session,
            _payload(
                project.id,
                module_id=module.id,
                doc_category="standards",
                file_path="/opt/knowledge/same.md",
                title="Same Doc",
            ),
        )

        assert len({a.id, b.id, c.id}) == 3
        assert all(d.project_id == project.id for d in (a, b, c))
        assert all(d.module_id == module.id for d in (a, b, c))
        assert all(d.file_path == "/opt/knowledge/same.md" for d in (a, b, c))

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
    def test_update_title_and_file_path(self, db_session):
        """``update`` patches mutable fields."""
        project = _make_project(db_session)
        created = service.create(db_session, _payload(project.id))

        updated = service.update(
            db_session,
            created.id,
            KbDocumentUpdate(
                title="Updated Title",
                file_path="/opt/knowledge/updated.md",
            ),
        )

        assert updated.id == created.id
        assert updated.title == "Updated Title"
        assert updated.file_path == "/opt/knowledge/updated.md"

    def test_update_module_id(self, db_session):
        """``module_id`` is mutable — a document can be re-scoped to a module."""
        project = _make_project(db_session)
        module = _make_module(db_session, project=project)
        created = service.create(db_session, _payload(project.id))
        assert created.module_id is None

        updated = service.update(
            db_session,
            created.id,
            KbDocumentUpdate(module_id=module.id),
        )

        assert updated.module_id == module.id

    def test_update_indexing_columns(self, db_session):
        """``qdrant_collection``, ``qdrant_point_id`` and ``indexed_at`` are mutable."""
        project = _make_project(db_session)
        created = service.create(db_session, _payload(project.id))
        indexed_at = datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc)

        updated = service.update(
            db_session,
            created.id,
            KbDocumentUpdate(
                qdrant_collection="nex_studio_kb",
                qdrant_point_id="qp_42",
                indexed_at=indexed_at,
            ),
        )

        assert updated.qdrant_collection == "nex_studio_kb"
        assert updated.qdrant_point_id == "qp_42"
        assert updated.indexed_at == indexed_at

    def test_update_partial_only_title(self, db_session):
        """``update`` leaves omitted fields untouched (PATCH semantics)."""
        project = _make_project(db_session)
        module = _make_module(db_session, project=project)
        indexed_at = datetime(2026, 4, 15, 10, 0, tzinfo=timezone.utc)
        created = service.create(
            db_session,
            _payload(
                project.id,
                module_id=module.id,
                qdrant_collection="coll",
                qdrant_point_id="qp_1",
                indexed_at=indexed_at,
            ),
        )

        updated = service.update(
            db_session,
            created.id,
            KbDocumentUpdate(title="Only Title Changed"),
        )

        # Only title changed; every other field preserved.
        assert updated.title == "Only Title Changed"
        assert updated.module_id == module.id
        assert updated.qdrant_collection == "coll"
        assert updated.qdrant_point_id == "qp_1"
        assert updated.indexed_at == indexed_at

    def test_update_none_fields_leave_unchanged(self, db_session):
        """Fields explicitly set to ``None`` in the payload are treated as "leave unchanged"."""
        project = _make_project(db_session)
        module = _make_module(db_session, project=project)
        indexed_at = datetime(2026, 4, 15, 10, 0, tzinfo=timezone.utc)
        created = service.create(
            db_session,
            _payload(
                project.id,
                module_id=module.id,
                qdrant_collection="coll",
                qdrant_point_id="qp_1",
                indexed_at=indexed_at,
            ),
        )

        # Explicitly passing None for every mutable field — the service
        # must ignore them and leave the row untouched. This is the
        # PATCH-semantics contract documented on
        # ``backend.services.kb_document.update``.
        updated = service.update(
            db_session,
            created.id,
            KbDocumentUpdate(
                module_id=None,
                title=None,
                file_path=None,
                qdrant_collection=None,
                qdrant_point_id=None,
                indexed_at=None,
            ),
        )

        assert updated.module_id == module.id
        assert updated.qdrant_collection == "coll"
        assert updated.qdrant_point_id == "qp_1"
        assert updated.indexed_at == indexed_at

    def test_update_preserves_immutable_fields(self, db_session):
        """``id``, ``project_id``, ``doc_category`` and ``created_at`` must not change across ``update``."""
        project = _make_project(db_session)
        created = service.create(
            db_session,
            _payload(project.id, doc_category="design"),
        )

        original_id = created.id
        original_project_id = created.project_id
        original_doc_category = created.doc_category
        original_created_at = created.created_at

        updated = service.update(
            db_session,
            created.id,
            KbDocumentUpdate(title="New Title"),
        )

        assert updated.id == original_id
        assert updated.project_id == original_project_id
        assert updated.doc_category == original_doc_category
        assert updated.created_at == original_created_at

    def test_update_allow_list_blocks_smuggled_immutable_fields(self, db_session):
        """Immutable fields smuggled past the schema must still be rejected by the service allow-list.

        Uses ``model_construct`` to bypass Pydantic validation and
        fabricate a payload with ``project_id`` / ``doc_category`` —
        the service's defensive allow-list must filter these out.
        """
        project_a = _make_project(db_session)
        project_b = _make_project(db_session)
        created = service.create(
            db_session,
            _payload(project_a.id, doc_category="design"),
        )

        # ``model_construct`` bypasses schema validation so we can
        # fabricate a payload that smuggles ``project_id`` and
        # ``doc_category`` — mimicking a rogue / bypassed client. The
        # service's allow-list must silently drop these fields.
        smuggled = KbDocumentUpdate.model_construct(
            title="Patched",
            project_id=project_b.id,
            doc_category="decisions",
        )

        updated = service.update(db_session, created.id, smuggled)

        assert updated.title == "Patched"
        # Immutable fields are preserved — allow-list filtered them out.
        assert updated.project_id == project_a.id
        assert updated.doc_category == "design"

    def test_update_empty_payload_is_noop(self, db_session):
        """A :class:`KbDocumentUpdate` with no fields set leaves the row intact."""
        project = _make_project(db_session)
        created = service.create(
            db_session,
            _payload(project.id, title="Keep Me", doc_category="lessons"),
        )

        updated = service.update(db_session, created.id, KbDocumentUpdate())

        assert updated.title == "Keep Me"
        assert updated.doc_category == "lessons"
        assert updated.project_id == project.id

    def test_update_missing_raises(self, db_session):
        """``update`` on a non-existent id raises ``ValueError``."""
        with pytest.raises(ValueError, match="not found"):
            service.update(
                db_session,
                uuid.uuid4(),
                KbDocumentUpdate(title="nope"),
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

    def test_delete_one_document_leaves_siblings_intact(self, db_session):
        """Deleting one document does not affect sibling documents on the same project."""
        project = _make_project(db_session)
        a = service.create(db_session, _payload(project.id, title="Doc A"))
        b = service.create(db_session, _payload(project.id, title="Doc B"))

        service.delete(db_session, a.id)

        assert service.get_by_id(db_session, b.id).id == b.id

    # ------------------------------------------------------------------ list
    def test_list_all(self, db_session):
        """``list_kb_documents`` returns every row when no filter is supplied."""
        project = _make_project(db_session)
        created_ids: set = set()
        for _ in range(3):
            created_ids.add(service.create(db_session, _payload(project.id)).id)

        rows = service.list_kb_documents(db_session)
        assert created_ids.issubset({r.id for r in rows})

    def test_list_filter_by_project(self, db_session):
        """``list_kb_documents(project_id=...)`` returns only that project's documents."""
        p1 = _make_project(db_session)
        p2 = _make_project(db_session)
        mine = service.create(db_session, _payload(p1.id))
        other = service.create(db_session, _payload(p2.id))

        rows = service.list_kb_documents(db_session, project_id=p1.id)
        row_ids = {r.id for r in rows}
        assert mine.id in row_ids
        assert other.id not in row_ids
        assert all(r.project_id == p1.id for r in rows)

    def test_list_filter_by_module(self, db_session):
        """``module_id`` filter returns only documents scoped to that module."""
        project = _make_project(db_session)
        module = _make_module(db_session, project=project)
        module_doc = service.create(db_session, _payload(project.id, module_id=module.id))
        project_doc = service.create(db_session, _payload(project.id))

        rows = service.list_kb_documents(db_session, module_id=module.id)
        row_ids = {r.id for r in rows}
        assert module_doc.id in row_ids
        # Project-level doc has module_id IS NULL — excluded by the filter.
        assert project_doc.id not in row_ids

    def test_list_filter_by_doc_category(self, db_session):
        """``doc_category`` filter returns only matching documents."""
        project = _make_project(db_session)
        standards_doc = service.create(
            db_session,
            _payload(project.id, doc_category="standards"),
        )
        decisions_doc = service.create(
            db_session,
            _payload(project.id, doc_category="decisions"),
        )

        standards_rows = service.list_kb_documents(
            db_session,
            project_id=project.id,
            doc_category="standards",
        )
        standards_ids = {r.id for r in standards_rows}
        assert standards_doc.id in standards_ids
        assert decisions_doc.id not in standards_ids

        decisions_rows = service.list_kb_documents(
            db_session,
            project_id=project.id,
            doc_category="decisions",
        )
        decisions_ids = {r.id for r in decisions_rows}
        assert decisions_doc.id in decisions_ids
        assert standards_doc.id not in decisions_ids

    def test_list_filter_by_qdrant_point_id(self, db_session):
        """``qdrant_point_id`` filter supports reverse-lookup from Qdrant hits."""
        project = _make_project(db_session)
        indexed = service.create(
            db_session,
            _payload(
                project.id,
                qdrant_collection="coll",
                qdrant_point_id="qp_unique_42",
            ),
        )
        other = service.create(
            db_session,
            _payload(
                project.id,
                qdrant_collection="coll",
                qdrant_point_id="qp_other",
            ),
        )
        unindexed = service.create(db_session, _payload(project.id))

        rows = service.list_kb_documents(db_session, qdrant_point_id="qp_unique_42")
        row_ids = {r.id for r in rows}
        assert indexed.id in row_ids
        assert other.id not in row_ids
        assert unindexed.id not in row_ids

    def test_list_combined_filters(self, db_session):
        """Multiple filters AND together."""
        p1 = _make_project(db_session)
        p2 = _make_project(db_session)
        module = _make_module(db_session, project=p1)

        match = service.create(
            db_session,
            _payload(p1.id, module_id=module.id, doc_category="design"),
        )
        # Different project.
        service.create(db_session, _payload(p2.id, doc_category="design"))
        # Different doc_category.
        service.create(
            db_session,
            _payload(p1.id, module_id=module.id, doc_category="behavior"),
        )
        # Project-level (module_id IS NULL).
        service.create(db_session, _payload(p1.id, doc_category="design"))

        rows = service.list_kb_documents(
            db_session,
            project_id=p1.id,
            module_id=module.id,
            doc_category="design",
        )
        assert len(rows) == 1
        assert rows[0].id == match.id

    def test_list_ordered_by_created_at_desc(self, db_session):
        """Results are ordered newest-first to match the KB-browser UI.

        Rows created inside a single transaction share the same ``NOW()``
        value (PostgreSQL ``now()`` is transaction-scoped), so the test
        overrides ``created_at`` explicitly to produce unambiguous
        ordering — the intent is to pin the service-layer ``ORDER BY
        created_at DESC`` contract, not to measure Postgres clock
        resolution.
        """
        project = _make_project(db_session)

        base_time = datetime(2026, 4, 15, 10, 0, tzinfo=timezone.utc)
        d1 = service.create(db_session, _payload(project.id, title="first"))
        d2 = service.create(db_session, _payload(project.id, title="second"))
        d3 = service.create(db_session, _payload(project.id, title="third"))
        d1.created_at = base_time
        d2.created_at = base_time + timedelta(minutes=1)
        d3.created_at = base_time + timedelta(minutes=2)
        db_session.flush()

        rows = service.list_kb_documents(db_session, project_id=project.id)
        ids_in_order = [r.id for r in rows]
        # Newest first.
        assert ids_in_order.index(d3.id) < ids_in_order.index(d2.id) < ids_in_order.index(d1.id)

    def test_list_pagination(self, db_session):
        """``limit`` / ``offset`` restrict the result window."""
        project = _make_project(db_session)
        for i in range(5):
            service.create(db_session, _payload(project.id, title=f"doc {i}"))

        first_page = service.list_kb_documents(
            db_session,
            project_id=project.id,
            limit=2,
            offset=0,
        )
        second_page = service.list_kb_documents(
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
