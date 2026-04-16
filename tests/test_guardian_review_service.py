"""Tests for :mod:`backend.services.guardian_review`.

Exercises every public CRUD entry point against the SAVEPOINT-isolated
session provided by ``tests/conftest.py``. Verifies:

* Happy-path list / get / create / update / delete.
* ``ValueError`` on missing ``id`` for get / update / delete.
* ``create`` applies the DB-level ``server_default`` values for
  ``findings`` (``[]``) and ``passed`` (``False``) via the Pydantic
  schema when omitted.
* ``create`` accepts explicit ``findings``, ``passed`` and
  ``duration_ms`` overrides.
* Update allow-list — only ``risk_level``, ``findings``, ``passed``
  and ``duration_ms`` are applied; ``id``, ``delegation_id``,
  ``layer`` and ``created_at`` are preserved.
* PATCH semantics — omitted fields stay untouched.
* List filters (``delegation_id``, ``layer``, ``risk_level``,
  ``passed``) and pagination.
* List ordering is ``created_at DESC``.
* ``delete`` removes the row — no inbound FKs to check.
* No ``commit`` happens inside the service — the outer transaction
  rolls back cleanly at fixture teardown.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from backend.db.models.delegations import Delegation
from backend.db.models.guardian import GuardianReview
from backend.schemas.guardian import (
    GuardianReviewCreate,
    GuardianReviewUpdate,
)
from backend.services import guardian_review as service


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


def _payload(
    db_session,
    *,
    delegation: Delegation | None = None,
    **overrides,
) -> GuardianReviewCreate:
    """Return a :class:`GuardianReviewCreate` payload with sensible defaults."""
    if delegation is None:
        delegation = _make_delegation(db_session)
    defaults = {
        "delegation_id": delegation.id,
        "layer": "layer1",
        "risk_level": "low",
    }
    defaults.update(overrides)
    return GuardianReviewCreate(**defaults)


class TestGuardianReviewService:
    """Synchronous CRUD coverage for the GuardianReview service."""

    # ------------------------------------------------------------------ create
    def test_create_minimal(self, db_session):
        """``create`` persists a review with just the required fields."""
        delegation = _make_delegation(db_session)

        created = service.create(db_session, _payload(db_session, delegation=delegation))

        assert isinstance(created, GuardianReview)
        assert created.id is not None
        assert created.created_at is not None
        assert created.delegation_id == delegation.id
        assert created.layer == "layer1"
        assert created.risk_level == "low"
        # Schema / DB defaults.
        assert created.findings == []
        assert created.passed is False
        assert created.duration_ms is None

    def test_create_with_findings(self, db_session):
        """``create`` accepts an explicit JSONB ``findings`` array."""
        findings = [
            {
                "severity": "MUST_FIX",
                "rule": "no-console-log",
                "file_path": "src/app.ts",
                "line_range": "12-14",
                "description": "console.log in production code",
                "suggestion": "remove",
                "confidence": 0.95,
            }
        ]
        created = service.create(
            db_session,
            _payload(db_session, findings=findings),
        )

        assert created.findings == findings

    def test_create_with_passed_true(self, db_session):
        """``create`` honours an explicit ``passed=True``."""
        created = service.create(
            db_session,
            _payload(db_session, passed=True),
        )

        assert created.passed is True

    def test_create_with_duration_ms(self, db_session):
        """``create`` persists the optional ``duration_ms``."""
        created = service.create(
            db_session,
            _payload(db_session, duration_ms=1234),
        )

        assert created.duration_ms == 1234

    @pytest.mark.parametrize("layer", ["layer1", "layer2", "layer3"])
    def test_create_accepts_all_layers(self, db_session, layer):
        """``create`` accepts every value permitted by the layer CHECK."""
        created = service.create(
            db_session,
            _payload(db_session, layer=layer),
        )

        assert created.layer == layer

    @pytest.mark.parametrize("risk_level", ["low", "medium", "high", "critical"])
    def test_create_accepts_all_risk_levels(self, db_session, risk_level):
        """``create`` accepts every value permitted by the risk_level CHECK."""
        created = service.create(
            db_session,
            _payload(db_session, risk_level=risk_level),
        )

        assert created.risk_level == risk_level

    # ------------------------------------------------------------------- get
    def test_get_by_id(self, db_session):
        """``get_by_id`` returns the row when it exists."""
        created = service.create(db_session, _payload(db_session))

        fetched = service.get_by_id(db_session, created.id)
        assert fetched.id == created.id
        assert fetched.layer == created.layer

    def test_get_by_id_missing_raises(self, db_session):
        """``get_by_id`` raises ``ValueError`` for an unknown id."""
        with pytest.raises(ValueError, match="not found"):
            service.get_by_id(db_session, uuid.uuid4())

    # ---------------------------------------------------------------- update
    def test_update_risk_level(self, db_session):
        """``risk_level`` is mutable (e.g. reclassification)."""
        created = service.create(db_session, _payload(db_session, risk_level="low"))

        updated = service.update(
            db_session,
            created.id,
            GuardianReviewUpdate(risk_level="high"),
        )

        assert updated.id == created.id
        assert updated.risk_level == "high"

    def test_update_findings(self, db_session):
        """``findings`` is mutable (post-hoc precedent filtering)."""
        created = service.create(
            db_session,
            _payload(
                db_session,
                findings=[
                    {
                        "severity": "MUST_FIX",
                        "rule": "r1",
                        "file_path": "f.py",
                        "line_range": None,
                        "description": "d",
                        "suggestion": None,
                        "confidence": 0.9,
                    }
                ],
            ),
        )

        updated = service.update(
            db_session,
            created.id,
            GuardianReviewUpdate(findings=[]),
        )

        assert updated.findings == []

    def test_update_passed_flip_to_true(self, db_session):
        """``passed`` can be flipped to ``True`` after precedent filtering."""
        created = service.create(db_session, _payload(db_session))
        assert created.passed is False

        updated = service.update(
            db_session,
            created.id,
            GuardianReviewUpdate(passed=True),
        )

        assert updated.passed is True

    def test_update_duration_ms(self, db_session):
        """``duration_ms`` is mutable."""
        created = service.create(db_session, _payload(db_session))
        assert created.duration_ms is None

        updated = service.update(
            db_session,
            created.id,
            GuardianReviewUpdate(duration_ms=5000),
        )

        assert updated.duration_ms == 5000

    def test_update_partial(self, db_session):
        """``update`` leaves omitted fields untouched (PATCH semantics)."""
        created = service.create(
            db_session,
            _payload(
                db_session,
                risk_level="medium",
                duration_ms=100,
            ),
        )

        updated = service.update(
            db_session,
            created.id,
            GuardianReviewUpdate(passed=True),
        )

        assert updated.passed is True
        # Unchanged fields preserved.
        assert updated.risk_level == "medium"
        assert updated.duration_ms == 100

    def test_update_preserves_immutable_fields(self, db_session):
        """``id``, ``delegation_id``, ``layer`` and ``created_at`` must not change."""
        delegation = _make_delegation(db_session)
        created = service.create(
            db_session,
            _payload(db_session, delegation=delegation, layer="layer2"),
        )

        original_id = created.id
        original_delegation_id = created.delegation_id
        original_layer = created.layer
        original_created_at = created.created_at

        updated = service.update(
            db_session,
            created.id,
            GuardianReviewUpdate(
                risk_level="critical",
                passed=True,
                findings=[],
                duration_ms=99,
            ),
        )

        assert updated.id == original_id
        assert updated.delegation_id == original_delegation_id
        assert updated.layer == original_layer
        assert updated.created_at == original_created_at

    def test_update_empty_payload_is_noop(self, db_session):
        """A :class:`GuardianReviewUpdate` with no fields set leaves the row intact."""
        created = service.create(
            db_session,
            _payload(
                db_session,
                risk_level="medium",
                duration_ms=42,
            ),
        )

        updated = service.update(db_session, created.id, GuardianReviewUpdate())

        assert updated.risk_level == "medium"
        assert updated.duration_ms == 42
        assert updated.passed is False

    def test_update_missing_raises(self, db_session):
        """``update`` on a non-existent id raises ``ValueError``."""
        with pytest.raises(ValueError, match="not found"):
            service.update(
                db_session,
                uuid.uuid4(),
                GuardianReviewUpdate(passed=True),
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
        """``list_guardian_reviews`` returns every row when no filter is supplied."""
        created_ids: set = set()
        for _ in range(3):
            created_ids.add(service.create(db_session, _payload(db_session)).id)

        rows = service.list_guardian_reviews(db_session, limit=1000)
        assert created_ids.issubset({r.id for r in rows})

    def test_list_filter_by_delegation(self, db_session):
        """``delegation_id`` filter returns only reviews for that delegation."""
        delegation = _make_delegation(db_session)
        mine = service.create(db_session, _payload(db_session, delegation=delegation))
        # Unrelated review, different delegation.
        service.create(db_session, _payload(db_session))

        rows = service.list_guardian_reviews(db_session, delegation_id=delegation.id)
        assert all(r.delegation_id == delegation.id for r in rows)
        assert any(r.id == mine.id for r in rows)

    def test_list_filter_by_layer(self, db_session):
        """``layer`` filter returns only matching reviews."""
        l1 = service.create(db_session, _payload(db_session, layer="layer1"))
        l2 = service.create(db_session, _payload(db_session, layer="layer2"))

        rows = service.list_guardian_reviews(db_session, layer="layer2")
        ids = {r.id for r in rows}
        assert l2.id in ids
        assert l1.id not in ids

    def test_list_filter_by_risk_level(self, db_session):
        """``risk_level`` filter returns only matching reviews."""
        low = service.create(db_session, _payload(db_session, risk_level="low"))
        critical = service.create(
            db_session,
            _payload(db_session, risk_level="critical"),
        )

        rows = service.list_guardian_reviews(db_session, risk_level="critical")
        ids = {r.id for r in rows}
        assert critical.id in ids
        assert low.id not in ids

    def test_list_filter_by_passed_false(self, db_session):
        """``passed=False`` lists blocking reviews (natural Guardian-panel query)."""
        blocked = service.create(db_session, _payload(db_session))  # defaults to False
        passed = service.create(
            db_session,
            _payload(db_session, passed=True),
        )

        rows = service.list_guardian_reviews(db_session, passed=False)
        ids = {r.id for r in rows}
        assert blocked.id in ids
        assert passed.id not in ids

    def test_list_filter_by_passed_true(self, db_session):
        """``passed=True`` lists clean reviews."""
        passed = service.create(
            db_session,
            _payload(db_session, passed=True),
        )
        blocked = service.create(db_session, _payload(db_session))

        rows = service.list_guardian_reviews(db_session, passed=True)
        ids = {r.id for r in rows}
        assert passed.id in ids
        assert blocked.id not in ids

    def test_list_combined_filters(self, db_session):
        """Multiple filters AND together."""
        delegation = _make_delegation(db_session)

        match = service.create(
            db_session,
            _payload(
                db_session,
                delegation=delegation,
                layer="layer2",
                risk_level="high",
            ),
        )
        # Same delegation, different layer.
        service.create(
            db_session,
            _payload(db_session, delegation=delegation, layer="layer1"),
        )
        # Different delegation, matching layer.
        service.create(db_session, _payload(db_session, layer="layer2"))

        rows = service.list_guardian_reviews(
            db_session,
            delegation_id=delegation.id,
            layer="layer2",
        )
        assert len(rows) == 1
        assert rows[0].id == match.id

    def test_list_ordered_by_created_at_desc(self, db_session):
        """Results are ordered by ``created_at DESC`` (most recent first).

        Rows created inside a single transaction share the same
        ``NOW()`` value (PostgreSQL ``now()`` is transaction-scoped),
        so the test overrides ``created_at`` explicitly to produce
        unambiguous ordering — the intent is to pin the service-layer
        ``ORDER BY created_at DESC`` contract, not to measure Postgres
        clock resolution.
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

        rows = service.list_guardian_reviews(
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

        first_page = service.list_guardian_reviews(
            db_session,
            delegation_id=delegation.id,
            limit=2,
            offset=0,
        )
        second_page = service.list_guardian_reviews(
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
