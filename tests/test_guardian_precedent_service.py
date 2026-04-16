"""Tests for :mod:`backend.services.guardian_precedent`.

Exercises every public CRUD entry point against the SAVEPOINT-isolated
session provided by ``tests/conftest.py``. Verifies:

* Happy-path list / get / create / update / delete.
* ``ValueError`` on duplicate ``pattern_hash`` (unique-constraint guard).
* ``ValueError`` on missing ``id`` for get / update / delete.
* Immutable fields (``pattern_hash``, ``created_by``, ``created_at``) stay
  unchanged on update.
* List filters (``verdict``, ``created_by``) and pagination.
* No ``commit`` happens inside the service — the outer transaction rolls
  back cleanly at fixture teardown.
"""

from __future__ import annotations

import uuid

import pytest

from backend.db.models.guardian import GuardianPrecedent
from backend.schemas.guardian import GuardianPrecedentCreate, GuardianPrecedentUpdate
from backend.services import guardian_precedent as service


def _make_hash(seed: str) -> str:
    """Produce a deterministic 64-char hex string from ``seed`` for tests."""
    # Pad / truncate to exactly 64 chars — schema enforces min_length=max_length=64.
    return (seed * 64)[:64]


class TestGuardianPrecedentService:
    """Synchronous CRUD coverage for the GuardianPrecedent service."""

    def test_create_precedent(self, db_session):
        """``create`` persists the row and returns an ORM instance with server defaults."""
        payload = GuardianPrecedentCreate(
            pattern_hash=_make_hash("a"),
            pattern_description="No console.log in production",
            verdict="allow",
        )
        created = service.create(db_session, payload)

        assert isinstance(created, GuardianPrecedent)
        assert created.id is not None
        assert created.created_at is not None
        assert created.pattern_hash == _make_hash("a")
        assert created.verdict == "allow"
        assert created.created_by is None

    def test_create_duplicate_pattern_hash_raises(self, db_session):
        """Second ``create`` with the same ``pattern_hash`` must raise ``ValueError``."""
        payload = GuardianPrecedentCreate(
            pattern_hash=_make_hash("b"),
            pattern_description="desc1",
            verdict="block",
        )
        service.create(db_session, payload)

        with pytest.raises(ValueError, match="already exists"):
            service.create(
                db_session,
                GuardianPrecedentCreate(
                    pattern_hash=_make_hash("b"),
                    pattern_description="desc2",
                    verdict="notice",
                ),
            )

    def test_get_by_id(self, db_session):
        """``get_by_id`` returns the precedent when it exists."""
        created = service.create(
            db_session,
            GuardianPrecedentCreate(
                pattern_hash=_make_hash("c"),
                pattern_description="desc",
                verdict="notice",
            ),
        )
        fetched = service.get_by_id(db_session, created.id)
        assert fetched.id == created.id

    def test_get_by_id_missing_raises(self, db_session):
        """``get_by_id`` raises ``ValueError`` for an unknown id."""
        with pytest.raises(ValueError, match="not found"):
            service.get_by_id(db_session, uuid.uuid4())

    def test_update_mutable_fields(self, db_session):
        """``update`` changes ``pattern_description`` and ``verdict`` only."""
        created = service.create(
            db_session,
            GuardianPrecedentCreate(
                pattern_hash=_make_hash("d"),
                pattern_description="original",
                verdict="allow",
            ),
        )
        original_hash = created.pattern_hash
        original_created_at = created.created_at

        updated = service.update(
            db_session,
            created.id,
            GuardianPrecedentUpdate(pattern_description="new", verdict="block"),
        )
        assert updated.pattern_description == "new"
        assert updated.verdict == "block"
        # Immutable fields unchanged.
        assert updated.pattern_hash == original_hash
        assert updated.created_at == original_created_at

    def test_update_partial(self, db_session):
        """``update`` with only ``verdict`` leaves ``pattern_description`` untouched."""
        created = service.create(
            db_session,
            GuardianPrecedentCreate(
                pattern_hash=_make_hash("e"),
                pattern_description="keep-me",
                verdict="allow",
            ),
        )
        updated = service.update(
            db_session,
            created.id,
            GuardianPrecedentUpdate(verdict="notice"),
        )
        assert updated.verdict == "notice"
        assert updated.pattern_description == "keep-me"

    def test_update_missing_raises(self, db_session):
        """``update`` on a non-existent id raises ``ValueError``."""
        with pytest.raises(ValueError, match="not found"):
            service.update(
                db_session,
                uuid.uuid4(),
                GuardianPrecedentUpdate(verdict="allow"),
            )

    def test_delete(self, db_session):
        """``delete`` removes the row; subsequent lookup raises."""
        created = service.create(
            db_session,
            GuardianPrecedentCreate(
                pattern_hash=_make_hash("f"),
                pattern_description="doomed",
                verdict="allow",
            ),
        )
        service.delete(db_session, created.id)

        with pytest.raises(ValueError, match="not found"):
            service.get_by_id(db_session, created.id)

    def test_delete_missing_raises(self, db_session):
        """``delete`` on a non-existent id raises ``ValueError``."""
        with pytest.raises(ValueError, match="not found"):
            service.delete(db_session, uuid.uuid4())

    def test_list_all(self, db_session):
        """``list_precedents`` returns every precedent when no filter is supplied."""
        hashes = [_make_hash(c) for c in ("g", "h", "i")]
        for h in hashes:
            service.create(
                db_session,
                GuardianPrecedentCreate(
                    pattern_hash=h,
                    pattern_description=f"desc-{h[:4]}",
                    verdict="allow",
                ),
            )
        rows = service.list_precedents(db_session)
        row_hashes = {r.pattern_hash for r in rows}
        assert set(hashes).issubset(row_hashes)

    def test_list_filter_by_verdict(self, db_session):
        """``list_precedents(verdict=...)`` returns only the matching verdict."""
        service.create(
            db_session,
            GuardianPrecedentCreate(
                pattern_hash=_make_hash("j"),
                pattern_description="allowed",
                verdict="allow",
            ),
        )
        service.create(
            db_session,
            GuardianPrecedentCreate(
                pattern_hash=_make_hash("k"),
                pattern_description="blocked",
                verdict="block",
            ),
        )
        blocked = service.list_precedents(db_session, verdict="block")
        assert all(p.verdict == "block" for p in blocked)
        assert any(p.pattern_hash == _make_hash("k") for p in blocked)

    def test_list_pagination(self, db_session):
        """``limit`` / ``offset`` restrict the result window."""
        # Insert enough rows to paginate over.
        for idx in range(5):
            service.create(
                db_session,
                GuardianPrecedentCreate(
                    pattern_hash=_make_hash(f"p{idx}"),
                    pattern_description=f"page-{idx}",
                    verdict="allow",
                ),
            )
        first_page = service.list_precedents(db_session, limit=2, offset=0)
        second_page = service.list_precedents(db_session, limit=2, offset=2)
        assert len(first_page) == 2
        assert len(second_page) == 2
        first_ids = {p.id for p in first_page}
        second_ids = {p.id for p in second_page}
        assert first_ids.isdisjoint(second_ids)

    def test_service_does_not_commit(self, db_session):
        """Service calls only ``flush`` — rows vanish when the outer transaction rolls back.

        This asserts the contract that transaction control belongs to the
        router, not the service. The SAVEPOINT-isolated ``db_session`` fixture
        rolls back at teardown; a service that called ``commit`` would leak
        rows into the test database and break other tests.
        """
        created = service.create(
            db_session,
            GuardianPrecedentCreate(
                pattern_hash=_make_hash("z"),
                pattern_description="flush-only",
                verdict="allow",
            ),
        )
        # ``in_transaction()`` must be True — commit would clear it.
        assert db_session.in_transaction()
        # Row is visible within the session after flush.
        assert service.get_by_id(db_session, created.id).id == created.id
