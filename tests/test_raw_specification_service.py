"""Tests for :mod:`backend.services.raw_specification`.

Exercises every public CRUD entry point against the SAVEPOINT-isolated
session provided by ``tests/conftest.py``. Verifies:

* Happy-path list / get / create / update / delete.
* ``ValueError`` on missing ``id`` for get / update / delete.
* ``create`` persists every supplied column — schema-level defaults
  (``input_format='text'``, ``language='sk'``, ``status='pending'``)
  flow through via the Pydantic schema.
* Update allow-list — only ``input_text``, ``input_format``,
  ``language`` and ``status`` are applied; ``project_id``,
  ``created_by``, ``id`` and ``created_at`` are preserved.
* PATCH semantics — omitted fields stay untouched.
* No UNIQUE constraints — multiple raw specifications coexist on the
  same project.
* List filters (``project_id``, ``status``, ``created_by``,
  ``input_format``, ``language``) and pagination.
* List ordering is ``created_at DESC`` (newest upload first).
* ``delete`` removes the row; dependent professional specifications
  cascade away (verified at the model layer — this test exercises the
  service-level contract that no RESTRICT guard is raised).
* No ``commit`` happens inside the service — the outer transaction
  rolls back cleanly at fixture teardown.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.specifications import RawSpecification
from backend.schemas.raw_specification import (
    RawSpecificationCreate,
    RawSpecificationUpdate,
)
from backend.services import raw_specification as service


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


def _payload(project_id, created_by, **overrides) -> RawSpecificationCreate:
    """Return a :class:`RawSpecificationCreate` payload with sensible defaults."""
    defaults = {
        "project_id": project_id,
        "created_by": created_by,
        "input_text": "Customer specification text for testing.",
    }
    defaults.update(overrides)
    return RawSpecificationCreate(**defaults)


class TestRawSpecificationService:
    """Synchronous CRUD coverage for the RawSpecification service."""

    # ------------------------------------------------------------------ create
    def test_create_specification(self, db_session):
        """``create`` persists the row and returns an ORM instance with server defaults."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)

        created = service.create(db_session, _payload(project.id, user.id))

        assert isinstance(created, RawSpecification)
        assert created.id is not None
        assert created.created_at is not None
        assert created.updated_at is not None
        assert created.project_id == project.id
        assert created.created_by == user.id
        assert created.input_text == "Customer specification text for testing."
        # Schema-level defaults.
        assert created.input_format == "text"
        assert created.language == "sk"
        assert created.status == "pending"

    def test_create_with_pdf_format(self, db_session):
        """``create`` accepts ``input_format='pdf'``."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)

        created = service.create(
            db_session,
            _payload(project.id, user.id, input_format="pdf"),
        )

        assert created.input_format == "pdf"

    def test_create_with_docx_format(self, db_session):
        """``create`` accepts ``input_format='docx'``."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)

        created = service.create(
            db_session,
            _payload(project.id, user.id, input_format="docx"),
        )

        assert created.input_format == "docx"

    def test_create_with_all_fields(self, db_session):
        """``create`` applies every supplied field, overriding schema defaults."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)

        created = service.create(
            db_session,
            _payload(
                project.id,
                user.id,
                input_text="Custom specification in English.",
                input_format="pdf",
                language="en",
                status="processing",
            ),
        )

        assert created.input_text == "Custom specification in English."
        assert created.input_format == "pdf"
        assert created.language == "en"
        assert created.status == "processing"

    def test_create_many_specs_same_project(self, db_session):
        """No UNIQUE constraint — many raw specs may share a project."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)

        s1 = service.create(db_session, _payload(project.id, user.id))
        s2 = service.create(db_session, _payload(project.id, user.id))
        s3 = service.create(db_session, _payload(project.id, user.id))

        assert len({s1.id, s2.id, s3.id}) == 3
        assert all(s.project_id == project.id for s in (s1, s2, s3))

    # ------------------------------------------------------------------- get
    def test_get_by_id(self, db_session):
        """``get_by_id`` returns the row when it exists."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        created = service.create(db_session, _payload(project.id, user.id))

        fetched = service.get_by_id(db_session, created.id)
        assert fetched.id == created.id
        assert fetched.project_id == project.id

    def test_get_by_id_missing_raises(self, db_session):
        """``get_by_id`` raises ``ValueError`` for an unknown id."""
        with pytest.raises(ValueError, match="not found"):
            service.get_by_id(db_session, uuid.uuid4())

    # ---------------------------------------------------------------- update
    def test_update_input_text(self, db_session):
        """``update`` patches the ``input_text`` field."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        created = service.create(db_session, _payload(project.id, user.id))

        updated = service.update(
            db_session,
            created.id,
            RawSpecificationUpdate(input_text="Revised specification text."),
        )

        assert updated.id == created.id
        assert updated.input_text == "Revised specification text."

    def test_update_status_transition(self, db_session):
        """``update`` can advance ``status`` through the lifecycle."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        created = service.create(db_session, _payload(project.id, user.id))
        assert created.status == "pending"

        updated = service.update(
            db_session,
            created.id,
            RawSpecificationUpdate(status="processing"),
        )
        assert updated.status == "processing"

        updated = service.update(
            db_session,
            created.id,
            RawSpecificationUpdate(status="done"),
        )
        assert updated.status == "done"

    def test_update_input_format_and_language(self, db_session):
        """``input_format`` and ``language`` are mutable."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        created = service.create(db_session, _payload(project.id, user.id))

        updated = service.update(
            db_session,
            created.id,
            RawSpecificationUpdate(input_format="pdf", language="en"),
        )

        assert updated.input_format == "pdf"
        assert updated.language == "en"

    def test_update_partial_only_status(self, db_session):
        """``update`` leaves omitted fields untouched (PATCH semantics)."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        created = service.create(
            db_session,
            _payload(
                project.id,
                user.id,
                input_text="Original text.",
                input_format="pdf",
                language="en",
            ),
        )

        updated = service.update(
            db_session,
            created.id,
            RawSpecificationUpdate(status="failed"),
        )

        assert updated.status == "failed"
        # Every other field preserved.
        assert updated.input_text == "Original text."
        assert updated.input_format == "pdf"
        assert updated.language == "en"

    def test_update_preserves_immutable_fields(self, db_session):
        """``id``, ``project_id``, ``created_by`` and ``created_at`` must not change across ``update``."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        created = service.create(db_session, _payload(project.id, user.id))

        original_id = created.id
        original_project_id = created.project_id
        original_created_by = created.created_by
        original_created_at = created.created_at

        updated = service.update(
            db_session,
            created.id,
            RawSpecificationUpdate(input_text="Changed.", status="done"),
        )

        assert updated.id == original_id
        assert updated.project_id == original_project_id
        assert updated.created_by == original_created_by
        assert updated.created_at == original_created_at

    def test_update_empty_payload_is_noop(self, db_session):
        """A :class:`RawSpecificationUpdate` with no fields set leaves the row intact."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        created = service.create(
            db_session,
            _payload(
                project.id,
                user.id,
                input_text="Original text.",
                input_format="docx",
                language="cs",
                status="processing",
            ),
        )

        updated = service.update(db_session, created.id, RawSpecificationUpdate())

        assert updated.input_text == "Original text."
        assert updated.input_format == "docx"
        assert updated.language == "cs"
        assert updated.status == "processing"

    def test_update_missing_raises(self, db_session):
        """``update`` on a non-existent id raises ``ValueError``."""
        with pytest.raises(ValueError, match="not found"):
            service.update(
                db_session,
                uuid.uuid4(),
                RawSpecificationUpdate(input_text="nope"),
            )

    # ---------------------------------------------------------------- delete
    def test_delete(self, db_session):
        """``delete`` removes the row; subsequent lookup raises."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        created = service.create(db_session, _payload(project.id, user.id))

        service.delete(db_session, created.id)

        with pytest.raises(ValueError, match="not found"):
            service.get_by_id(db_session, created.id)

    def test_delete_missing_raises(self, db_session):
        """``delete`` on a non-existent id raises ``ValueError``."""
        with pytest.raises(ValueError, match="not found"):
            service.delete(db_session, uuid.uuid4())

    def test_delete_one_spec_leaves_siblings_intact(self, db_session):
        """Deleting one spec does not affect sibling specs on the same project."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        s1 = service.create(db_session, _payload(project.id, user.id))
        s2 = service.create(db_session, _payload(project.id, user.id))

        service.delete(db_session, s1.id)

        assert service.get_by_id(db_session, s2.id).id == s2.id

    # ------------------------------------------------------------------ list
    def test_list_all(self, db_session):
        """``list_raw_specifications`` returns every row when no filter is supplied."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        created_ids: set = set()
        for _ in range(3):
            created_ids.add(service.create(db_session, _payload(project.id, user.id)).id)

        rows = service.list_raw_specifications(db_session)
        assert created_ids.issubset({r.id for r in rows})

    def test_list_filter_by_project(self, db_session):
        """``list_raw_specifications(project_id=...)`` returns only that project's specs."""
        user = _make_user(db_session)
        p1 = _make_project(db_session, user=user)
        p2 = _make_project(db_session, user=user)
        mine = service.create(db_session, _payload(p1.id, user.id))
        service.create(db_session, _payload(p2.id, user.id))

        rows = service.list_raw_specifications(db_session, project_id=p1.id)
        assert all(r.project_id == p1.id for r in rows)
        assert any(r.id == mine.id for r in rows)

    def test_list_filter_by_status(self, db_session):
        """``status`` filter returns only matching specifications."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        pending = service.create(db_session, _payload(project.id, user.id, status="pending"))
        done = service.create(db_session, _payload(project.id, user.id, status="done"))

        pending_rows = service.list_raw_specifications(
            db_session,
            project_id=project.id,
            status="pending",
        )
        pending_ids = {r.id for r in pending_rows}
        assert pending.id in pending_ids
        assert done.id not in pending_ids

        done_rows = service.list_raw_specifications(
            db_session,
            project_id=project.id,
            status="done",
        )
        done_ids = {r.id for r in done_rows}
        assert done.id in done_ids
        assert pending.id not in done_ids

    def test_list_filter_by_created_by(self, db_session):
        """``created_by`` filter returns only specs from that user."""
        u1 = _make_user(db_session)
        u2 = _make_user(db_session)
        project = _make_project(db_session, user=u1)

        mine = service.create(db_session, _payload(project.id, u1.id))
        theirs = service.create(db_session, _payload(project.id, u2.id))

        rows = service.list_raw_specifications(db_session, created_by=u1.id)
        ids = {r.id for r in rows}
        assert mine.id in ids
        assert theirs.id not in ids

    def test_list_filter_by_input_format(self, db_session):
        """``input_format`` filter returns only specs in the chosen format."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        text_spec = service.create(db_session, _payload(project.id, user.id, input_format="text"))
        pdf_spec = service.create(db_session, _payload(project.id, user.id, input_format="pdf"))
        docx_spec = service.create(db_session, _payload(project.id, user.id, input_format="docx"))

        pdf_rows = service.list_raw_specifications(
            db_session,
            project_id=project.id,
            input_format="pdf",
        )
        pdf_ids = {r.id for r in pdf_rows}
        assert pdf_spec.id in pdf_ids
        assert text_spec.id not in pdf_ids
        assert docx_spec.id not in pdf_ids

    def test_list_filter_by_language(self, db_session):
        """``language`` filter returns only specs in the chosen language."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        sk_spec = service.create(db_session, _payload(project.id, user.id, language="sk"))
        en_spec = service.create(db_session, _payload(project.id, user.id, language="en"))

        en_rows = service.list_raw_specifications(
            db_session,
            project_id=project.id,
            language="en",
        )
        en_ids = {r.id for r in en_rows}
        assert en_spec.id in en_ids
        assert sk_spec.id not in en_ids

    def test_list_combined_filters(self, db_session):
        """Multiple filters AND together."""
        u1 = _make_user(db_session)
        u2 = _make_user(db_session)
        p1 = _make_project(db_session, user=u1)
        p2 = _make_project(db_session, user=u1)

        match = service.create(
            db_session,
            _payload(p1.id, u1.id, status="processing", input_format="pdf"),
        )
        # Different project.
        service.create(
            db_session,
            _payload(p2.id, u1.id, status="processing", input_format="pdf"),
        )
        # Different status.
        service.create(
            db_session,
            _payload(p1.id, u1.id, status="done", input_format="pdf"),
        )
        # Different user.
        service.create(
            db_session,
            _payload(p1.id, u2.id, status="processing", input_format="pdf"),
        )
        # Different format.
        service.create(
            db_session,
            _payload(p1.id, u1.id, status="processing", input_format="text"),
        )

        rows = service.list_raw_specifications(
            db_session,
            project_id=p1.id,
            status="processing",
            created_by=u1.id,
            input_format="pdf",
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

        base_time = datetime(2026, 4, 15, 10, 0, tzinfo=timezone.utc)
        s1 = service.create(db_session, _payload(project.id, user.id))
        s2 = service.create(db_session, _payload(project.id, user.id))
        s3 = service.create(db_session, _payload(project.id, user.id))
        s1.created_at = base_time
        s2.created_at = base_time + timedelta(minutes=1)
        s3.created_at = base_time + timedelta(minutes=2)
        db_session.flush()

        rows = service.list_raw_specifications(db_session, project_id=project.id)
        ids_in_order = [r.id for r in rows]
        # Newest first.
        assert ids_in_order.index(s3.id) < ids_in_order.index(s2.id) < ids_in_order.index(s1.id)

    def test_list_pagination(self, db_session):
        """``limit`` / ``offset`` restrict the result window."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        for _ in range(5):
            service.create(db_session, _payload(project.id, user.id))

        first_page = service.list_raw_specifications(
            db_session,
            project_id=project.id,
            limit=2,
            offset=0,
        )
        second_page = service.list_raw_specifications(
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
        created = service.create(db_session, _payload(project.id, user.id))
        # ``in_transaction()`` must be True — commit would clear it.
        assert db_session.in_transaction()
        # Row is visible within the session after flush.
        assert service.get_by_id(db_session, created.id).id == created.id
