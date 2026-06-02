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
* ``change_password``: ri can change any user's password, ha/shu can only
  change their own, bcrypt hash is updated, token rotation invalidates JWTs.
* No ``commit`` happens inside the service — the outer transaction rolls
  back cleanly at fixture teardown.
"""

from __future__ import annotations

import uuid

import bcrypt
import pytest

from backend.db.models.bugs import Bug
from backend.db.models.foundation import User, UserSession
from backend.db.models.projects import Project
from backend.schemas.user import UserCreate, UserUpdate
from backend.services import user as service


def _payload(**overrides) -> UserCreate:
    """Return a :class:`UserCreate` payload with deterministic-ish defaults."""
    suffix = uuid.uuid4().hex[:8]
    defaults = {
        "username": f"user_{suffix}",
        "email": f"{suffix}@example.com",
        "password": "SecurePass123",
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
            password="SecurePass123",
            role="ha",
        )
        created = service.create(db_session, payload)
        assert created.is_active is True

    def test_create_with_first_last_name(self, db_session):
        """``first_name`` / ``last_name`` persist when supplied (migration 042)."""
        created = service.create(
            db_session,
            _payload(first_name="Tibor", last_name="Rausch"),
        )
        assert created.first_name == "Tibor"
        assert created.last_name == "Rausch"

    def test_create_without_name_fields_is_valid(self, db_session):
        """Legacy / minimal create without name fields still works (nullable)."""
        created = service.create(db_session, _payload())
        # _payload doesn't set first_name / last_name → None on the ORM.
        assert created.first_name is None
        assert created.last_name is None

    def test_create_rejects_password_under_min_length(self):
        """Pydantic enforces min_length=5 on ``password`` (Director directive 2026-05-13)."""
        import pytest as _pt
        from pydantic import ValidationError

        with _pt.raises(ValidationError, match="at least 5 characters"):
            UserCreate(
                username="tooshort",
                email="x@y.sk",
                password="abcd",  # 4 chars
                role="ha",
            )

    def test_create_accepts_password_exactly_5_chars(self, db_session):
        """``password='abcde'`` (exactly 5) is the new minimum (was 8)."""
        created = service.create(db_session, _payload(password="abcde"))
        # Bcrypt-hashed plaintext was 5 chars; hash itself is 60 chars.
        assert len(created.password_hash) >= 50

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
                role="ri",
                is_active=False,
            ),
        )
        assert updated.username == "new"
        assert updated.email == "new@example.com"
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


class TestChangePassword:
    """Tests for :func:`backend.services.user.change_password`.

    Validates authorization rules (ri vs ha/shu), bcrypt hashing,
    password_hash update, and token rotation (token_version bump).
    """

    def test_ri_can_change_any_users_password(self, db_session):
        """An ``ri`` user can change another user's password."""
        ri_user = service.create(db_session, _payload(username="admin", email="admin@ex.com", role="ri"))
        target = service.create(db_session, _payload(username="target", email="target@ex.com", role="ha"))
        old_hash = target.password_hash

        updated = service.change_password(db_session, target.id, "NewSecurePass1!", ri_user)

        assert updated.password_hash != old_hash
        assert bcrypt.checkpw(b"NewSecurePass1!", updated.password_hash.encode("utf-8"))

    def test_ri_can_change_own_password(self, db_session):
        """An ``ri`` user can change their own password."""
        ri_user = service.create(db_session, _payload(username="ri_self", email="ri_self@ex.com", role="ri"))
        updated = service.change_password(db_session, ri_user.id, "MyNewPass!", ri_user)
        assert bcrypt.checkpw(b"MyNewPass!", updated.password_hash.encode("utf-8"))

    def test_ha_can_change_own_password(self, db_session):
        """An ``ha`` user can change their own password."""
        ha_user = service.create(db_session, _payload(username="ha_user", email="ha@ex.com", role="ha"))
        updated = service.change_password(db_session, ha_user.id, "HaNewPass!", ha_user)
        assert bcrypt.checkpw(b"HaNewPass!", updated.password_hash.encode("utf-8"))

    def test_shu_can_change_own_password(self, db_session):
        """An ``shu`` user can change their own password."""
        shu_user = service.create(db_session, _payload(username="shu_user", email="shu@ex.com", role="shu"))
        updated = service.change_password(db_session, shu_user.id, "ShuNewPass!", shu_user)
        assert bcrypt.checkpw(b"ShuNewPass!", updated.password_hash.encode("utf-8"))

    def test_ha_cannot_change_other_users_password(self, db_session):
        """An ``ha`` user cannot change another user's password."""
        ha_user = service.create(db_session, _payload(username="ha_actor", email="ha_a@ex.com", role="ha"))
        target = service.create(db_session, _payload(username="other", email="other@ex.com", role="shu"))
        with pytest.raises(ValueError, match="Insufficient permissions"):
            service.change_password(db_session, target.id, "Nope!", ha_user)

    def test_shu_cannot_change_other_users_password(self, db_session):
        """An ``shu`` user cannot change another user's password."""
        shu_user = service.create(db_session, _payload(username="shu_actor", email="shu_a@ex.com", role="shu"))
        target = service.create(db_session, _payload(username="other2", email="other2@ex.com", role="ha"))
        with pytest.raises(ValueError, match="Insufficient permissions"):
            service.change_password(db_session, target.id, "Nope!", shu_user)

    def test_change_password_for_nonexistent_user_raises(self, db_session):
        """``change_password`` raises ``ValueError`` for unknown user_id."""
        ri_user = service.create(db_session, _payload(username="ri_404", email="ri404@ex.com", role="ri"))
        with pytest.raises(ValueError, match="not found"):
            service.change_password(db_session, uuid.uuid4(), "Pass!", ri_user)

    def test_password_rotation_invalidates_tokens(self, db_session):
        """``change_password`` bumps ``token_version``, invalidating old JWTs."""
        from sqlalchemy import select

        ri_user = service.create(db_session, _payload(username="ri_rot", email="ri_rot@ex.com", role="ri"))
        target = service.create(db_session, _payload(username="rot_target", email="rot@ex.com", role="ha"))

        # create() now creates a session with token_version=0
        stmt = select(UserSession).where(UserSession.user_id == target.id)
        session_before = db_session.execute(stmt).scalar_one_or_none()
        assert session_before is not None
        assert session_before.token_version == 0

        # Change password → bumps token_version to 1
        service.change_password(db_session, target.id, "Rotated1!", ri_user)

        db_session.expire(session_before)
        session_after = db_session.execute(stmt).scalar_one_or_none()
        assert session_after is not None
        assert session_after.token_version == 1

        # Change password again → bumps token_version to 2
        service.change_password(db_session, target.id, "Rotated2!", ri_user)

        db_session.expire(session_after)
        session_final = db_session.execute(stmt).scalar_one()
        assert session_final.token_version == 2

    def test_change_password_produces_valid_bcrypt_hash(self, db_session):
        """The stored hash is a valid bcrypt hash verifiable with the new password."""
        user = service.create(db_session, _payload(username="hash_test", email="hash@ex.com", role="ri"))
        service.change_password(db_session, user.id, "V3ryS3cure!", user)

        # Verify the hash starts with the bcrypt prefix
        assert user.password_hash.startswith("$2")
        # Verify the new password matches the hash
        assert bcrypt.checkpw(b"V3ryS3cure!", user.password_hash.encode("utf-8"))
        # Verify the old password does NOT match
        assert not bcrypt.checkpw(b"SecurePass123", user.password_hash.encode("utf-8"))
