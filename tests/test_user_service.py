"""Tests for :mod:`backend.services.user`.

Exercises every public CRUD entry point against the SAVEPOINT-isolated
session provided by ``tests/conftest.py``. Verifies:

* Happy-path list / get / create / update / delete.
* ``ValueError`` on duplicate ``username`` / ``email`` during create and
  update (unique-constraint guard).
* ``ValueError`` on missing ``id`` for get / update / delete.
* Immutable fields (``id``, ``created_at``) stay unchanged on update.
* List filters (``role``, ``is_active``) and pagination.
* ``delete`` raises :class:`ValueError` when a RESTRICT FK still
  references the user, for each inbound relation.
* No ``commit`` happens inside the service — the outer transaction rolls
  back cleanly at fixture teardown.
"""

from __future__ import annotations

import uuid

import pytest

from backend.db.models.architect import ArchitectSession
from backend.db.models.bugs import Bug
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.specifications import (
    DesignDocument,
    ProfessionalSpecification,
    RawSpecification,
)
from backend.schemas.user import UserCreate, UserUpdate
from backend.services import user as service


def _payload(**overrides) -> UserCreate:
    """Return a :class:`UserCreate` payload with deterministic-ish defaults."""
    suffix = uuid.uuid4().hex[:8]
    defaults = {
        "username": f"user_{suffix}",
        "email": f"{suffix}@example.com",
        "password_hash": "hashed_password_placeholder",
        "role": "ri",
        "is_active": True,
    }
    defaults.update(overrides)
    return UserCreate(**defaults)


class TestUserService:
    """Synchronous CRUD coverage for the User service."""

    # ------------------------------------------------------------------ create
    def test_create_user(self, db_session):
        """``create`` persists the row and returns an ORM instance with server defaults."""
        payload = _payload(username="alice", email="alice@example.com")
        created = service.create(db_session, payload)

        assert isinstance(created, User)
        assert created.id is not None
        assert created.created_at is not None
        assert created.updated_at is not None
        assert created.username == "alice"
        assert created.email == "alice@example.com"
        assert created.role == "ri"
        assert created.is_active is True

    def test_create_duplicate_username_raises(self, db_session):
        """Second ``create`` with the same ``username`` must raise ``ValueError``."""
        service.create(db_session, _payload(username="dup", email="one@example.com"))
        with pytest.raises(ValueError, match="username"):
            service.create(
                db_session,
                _payload(username="dup", email="two@example.com"),
            )

    def test_create_duplicate_email_raises(self, db_session):
        """Second ``create`` with the same ``email`` must raise ``ValueError``."""
        service.create(db_session, _payload(username="a", email="dup@example.com"))
        with pytest.raises(ValueError, match="email"):
            service.create(
                db_session,
                _payload(username="b", email="dup@example.com"),
            )

    def test_create_defaults_is_active_true(self, db_session):
        """``is_active`` defaults to True when omitted via schema default."""
        # UserCreate has default=True so omitting it still produces True.
        payload = UserCreate(
            username=f"def_{uuid.uuid4().hex[:6]}",
            email=f"def_{uuid.uuid4().hex[:6]}@example.com",
            password_hash="h",
            role="ha",
        )
        created = service.create(db_session, payload)
        assert created.is_active is True

    # ------------------------------------------------------------------- get
    def test_get_by_id(self, db_session):
        """``get_by_id`` returns the user when it exists."""
        created = service.create(db_session, _payload())
        fetched = service.get_by_id(db_session, created.id)
        assert fetched.id == created.id

    def test_get_by_id_missing_raises(self, db_session):
        """``get_by_id`` raises ``ValueError`` for an unknown id."""
        with pytest.raises(ValueError, match="not found"):
            service.get_by_id(db_session, uuid.uuid4())

    # ---------------------------------------------------------------- update
    def test_update_mutable_fields(self, db_session):
        """``update`` changes all mutable columns."""
        created = service.create(
            db_session,
            _payload(username="old", email="old@example.com", role="ha"),
        )
        original_id = created.id
        original_created_at = created.created_at

        updated = service.update(
            db_session,
            created.id,
            UserUpdate(
                username="new",
                email="new@example.com",
                password_hash="new_hash",
                role="ri",
                is_active=False,
            ),
        )
        assert updated.username == "new"
        assert updated.email == "new@example.com"
        assert updated.password_hash == "new_hash"
        assert updated.role == "ri"
        assert updated.is_active is False
        # Immutable fields unchanged.
        assert updated.id == original_id
        assert updated.created_at == original_created_at

    def test_update_partial(self, db_session):
        """``update`` with only ``role`` leaves other fields untouched."""
        created = service.create(
            db_session,
            _payload(username="keep", email="keep@example.com", role="shu"),
        )
        updated = service.update(
            db_session,
            created.id,
            UserUpdate(role="ha"),
        )
        assert updated.role == "ha"
        assert updated.username == "keep"
        assert updated.email == "keep@example.com"

    def test_update_missing_raises(self, db_session):
        """``update`` on a non-existent id raises ``ValueError``."""
        with pytest.raises(ValueError, match="not found"):
            service.update(
                db_session,
                uuid.uuid4(),
                UserUpdate(role="ri"),
            )

    def test_update_duplicate_username_raises(self, db_session):
        """``update`` cannot rename into an existing username."""
        service.create(db_session, _payload(username="taken", email="t@example.com"))
        other = service.create(db_session, _payload(username="me", email="me@example.com"))

        with pytest.raises(ValueError, match="username"):
            service.update(db_session, other.id, UserUpdate(username="taken"))

    def test_update_duplicate_email_raises(self, db_session):
        """``update`` cannot change email to one already used by another user."""
        service.create(db_session, _payload(username="u1", email="taken@example.com"))
        other = service.create(db_session, _payload(username="u2", email="free@example.com"))

        with pytest.raises(ValueError, match="email"):
            service.update(db_session, other.id, UserUpdate(email="taken@example.com"))

    def test_update_same_username_allowed(self, db_session):
        """Updating with the same ``username`` is a no-op, not a conflict."""
        created = service.create(
            db_session,
            _payload(username="unchanged", email="unchanged@example.com"),
        )
        updated = service.update(
            db_session,
            created.id,
            UserUpdate(username="unchanged", role="ha"),
        )
        assert updated.username == "unchanged"
        assert updated.role == "ha"

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

    def test_delete_blocked_by_project(self, db_session):
        """``delete`` refuses when a ``project.created_by`` references the user."""
        owner = service.create(db_session, _payload())
        project = Project(
            name=f"P-{uuid.uuid4().hex[:6]}",
            slug=f"p-{uuid.uuid4().hex[:6]}",
            category="singlemodule",
            description="",
            created_by=owner.id,
        )
        db_session.add(project)
        db_session.flush()

        with pytest.raises(ValueError, match="projects"):
            service.delete(db_session, owner.id)

    def test_delete_blocked_by_bug(self, db_session):
        """``delete`` refuses when a ``bugs.created_by`` references the user."""
        owner = service.create(db_session, _payload())
        project = Project(
            name=f"P-{uuid.uuid4().hex[:6]}",
            slug=f"p-{uuid.uuid4().hex[:6]}",
            category="singlemodule",
            description="",
            created_by=owner.id,
        )
        db_session.add(project)
        db_session.flush()

        reporter = service.create(db_session, _payload())
        bug = Bug(
            project_id=project.id,
            bug_number=1,
            title="Broken thing",
            description="desc",
            severity="minor",
            created_by=reporter.id,
        )
        db_session.add(bug)
        db_session.flush()

        with pytest.raises(ValueError, match="bugs"):
            service.delete(db_session, reporter.id)

    def test_delete_blocked_by_architect_session(self, db_session):
        """``delete`` refuses when an ``architect_sessions.created_by`` references the user."""
        owner = service.create(db_session, _payload())
        project = Project(
            name=f"P-{uuid.uuid4().hex[:6]}",
            slug=f"p-{uuid.uuid4().hex[:6]}",
            category="singlemodule",
            description="",
            created_by=owner.id,
        )
        db_session.add(project)
        db_session.flush()

        arch_user = service.create(db_session, _payload())
        session = ArchitectSession(
            project_id=project.id,
            created_by=arch_user.id,
        )
        db_session.add(session)
        db_session.flush()

        with pytest.raises(ValueError, match="architect_sessions"):
            service.delete(db_session, arch_user.id)

    def test_delete_blocked_by_raw_specification(self, db_session):
        """``delete`` refuses when a ``raw_specifications.created_by`` references the user."""
        owner = service.create(db_session, _payload())
        project = Project(
            name=f"P-{uuid.uuid4().hex[:6]}",
            slug=f"p-{uuid.uuid4().hex[:6]}",
            category="singlemodule",
            description="",
            created_by=owner.id,
        )
        db_session.add(project)
        db_session.flush()

        author = service.create(db_session, _payload())
        raw_spec = RawSpecification(
            project_id=project.id,
            input_text="spec",
            created_by=author.id,
        )
        db_session.add(raw_spec)
        db_session.flush()

        with pytest.raises(ValueError, match="raw_specifications"):
            service.delete(db_session, author.id)

    def test_delete_blocked_by_professional_specification(self, db_session):
        """``delete`` refuses when a ``professional_specifications.approved_by`` references the user."""
        owner = service.create(db_session, _payload())
        project = Project(
            name=f"P-{uuid.uuid4().hex[:6]}",
            slug=f"p-{uuid.uuid4().hex[:6]}",
            category="singlemodule",
            description="",
            created_by=owner.id,
        )
        db_session.add(project)
        db_session.flush()

        raw_spec = RawSpecification(
            project_id=project.id,
            input_text="spec",
            created_by=owner.id,
        )
        db_session.add(raw_spec)
        db_session.flush()

        approver = service.create(db_session, _payload())
        prof_spec = ProfessionalSpecification(
            raw_spec_id=raw_spec.id,
            project_id=project.id,
            content="content",
            approved_by=approver.id,
        )
        db_session.add(prof_spec)
        db_session.flush()

        with pytest.raises(ValueError, match="professional_specifications"):
            service.delete(db_session, approver.id)

    def test_delete_blocked_by_design_document(self, db_session):
        """``delete`` refuses when a ``design_documents.approved_by`` references the user."""
        owner = service.create(db_session, _payload())
        project = Project(
            name=f"P-{uuid.uuid4().hex[:6]}",
            slug=f"p-{uuid.uuid4().hex[:6]}",
            category="singlemodule",
            description="",
            created_by=owner.id,
        )
        db_session.add(project)
        db_session.flush()

        approver = service.create(db_session, _payload())
        doc = DesignDocument(
            project_id=project.id,
            doc_type="design",
            content="content",
            approved_by=approver.id,
        )
        db_session.add(doc)
        db_session.flush()

        with pytest.raises(ValueError, match="design_documents"):
            service.delete(db_session, approver.id)

    # ------------------------------------------------------------------ list
    def test_list_all(self, db_session):
        """``list_users`` returns every user when no filter is supplied."""
        created_ids: set = set()
        for _ in range(3):
            created_ids.add(service.create(db_session, _payload()).id)
        rows = service.list_users(db_session)
        assert created_ids.issubset({r.id for r in rows})

    def test_list_filter_by_role(self, db_session):
        """``list_users(role=...)`` returns only users with the matching role."""
        service.create(db_session, _payload(role="ri"))
        ha_user = service.create(db_session, _payload(role="ha"))
        ha_rows = service.list_users(db_session, role="ha")
        assert all(u.role == "ha" for u in ha_rows)
        assert any(u.id == ha_user.id for u in ha_rows)

    def test_list_filter_by_is_active(self, db_session):
        """``list_users(is_active=False)`` returns only soft-disabled users."""
        active = service.create(db_session, _payload(is_active=True))
        disabled = service.create(db_session, _payload(is_active=False))

        active_rows = service.list_users(db_session, is_active=True)
        disabled_rows = service.list_users(db_session, is_active=False)

        assert all(u.is_active is True for u in active_rows)
        assert all(u.is_active is False for u in disabled_rows)
        assert any(u.id == active.id for u in active_rows)
        assert any(u.id == disabled.id for u in disabled_rows)

    def test_list_pagination(self, db_session):
        """``limit`` / ``offset`` restrict the result window."""
        for _ in range(5):
            service.create(db_session, _payload())
        first_page = service.list_users(db_session, limit=2, offset=0)
        second_page = service.list_users(db_session, limit=2, offset=2)
        assert len(first_page) == 2
        assert len(second_page) == 2
        first_ids = {u.id for u in first_page}
        second_ids = {u.id for u in second_page}
        assert first_ids.isdisjoint(second_ids)

    # --------------------------------------------------------------- commit
    def test_service_does_not_commit(self, db_session):
        """Service calls only ``flush`` — rows vanish when the outer transaction rolls back.

        This asserts the contract that transaction control belongs to the
        router, not the service. The SAVEPOINT-isolated ``db_session`` fixture
        rolls back at teardown; a service that called ``commit`` would leak
        rows into the test database and break other tests.
        """
        created = service.create(db_session, _payload())
        # ``in_transaction()`` must be True — commit would clear it.
        assert db_session.in_transaction()
        # Row is visible within the session after flush.
        assert service.get_by_id(db_session, created.id).id == created.id
