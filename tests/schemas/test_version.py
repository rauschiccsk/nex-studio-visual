"""Pydantic schema tests for :mod:`backend.schemas.version`.

Pure schema-level validation — exercises field constraints, defaults,
enum membership, computed-count defaults and ORM round-trip via
``from_attributes=True`` without touching the database.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import get_args

import pytest
from pydantic import ValidationError

from backend.schemas.bug import BugCreate, BugRead
from backend.schemas.epic import EpicCreate, EpicRead
from backend.schemas.version import (
    VersionCreate,
    VersionRead,
    VersionStatus,
    VersionUpdate,
)

# ---------------------------------------------------------------------------
# VersionStatus Literal
# ---------------------------------------------------------------------------


class TestVersionStatus:
    """The ``VersionStatus`` alias mirrors the DB CHECK constraint."""

    def test_allowed_values(self) -> None:
        """Literal exposes exactly the four DB-allowed values.

        ``done`` pribudlo 03.09.2026 (ICCINT-50): postavená, overená a Manažérom schválená verzia,
        ktorá ešte nebola nasadená. Bez neho niesla ``planned`` a na obrazovke sa nedala odlíšiť
        od nezačatej.
        """
        assert set(get_args(VersionStatus)) == {"planned", "active", "done", "released"}


# ---------------------------------------------------------------------------
# VersionCreate
# ---------------------------------------------------------------------------


class TestVersionCreate:
    """Input schema for ``POST /projects/{id}/versions``."""

    def test_minimal_payload(self) -> None:
        """Only ``version_number`` is required; optional fields default to None."""
        payload = VersionCreate(version_number="1.0.0")
        assert payload.version_number == "1.0.0"
        assert payload.name is None
        assert payload.description is None
        assert payload.target_date is None

    def test_full_payload(self) -> None:
        """All optional fields round-trip with their declared types."""
        payload = VersionCreate(
            version_number="1.1.0",
            name="Bug fix release",
            description="Fix critical regressions from v1.0.0",
            target_date=date(2026, 6, 1),
        )
        assert payload.name == "Bug fix release"
        assert payload.description == "Fix critical regressions from v1.0.0"
        assert payload.target_date == date(2026, 6, 1)

    def test_version_number_required(self) -> None:
        """``version_number`` has no default and must be supplied."""
        with pytest.raises(ValidationError) as excinfo:
            VersionCreate()  # type: ignore[call-arg]
        errors = excinfo.value.errors()
        assert any(err["loc"] == ("version_number",) for err in errors)

    def test_version_number_rejects_empty(self) -> None:
        """Empty ``version_number`` violates ``min_length=1``."""
        with pytest.raises(ValidationError):
            VersionCreate(version_number="")

    def test_version_number_rejects_overlong(self) -> None:
        """``version_number`` is capped at 50 chars (mirrors DB VARCHAR(50))."""
        with pytest.raises(ValidationError):
            VersionCreate(version_number="v" + "0" * 50)

    def test_version_number_accepts_boundary(self) -> None:
        """A 50-char ``version_number`` is accepted (inclusive upper bound)."""
        boundary = "v" + "0" * 49
        payload = VersionCreate(version_number=boundary)
        assert len(payload.version_number) == 50

    def test_name_max_length(self) -> None:
        """``name`` is capped at 255 chars (mirrors DB VARCHAR(255))."""
        with pytest.raises(ValidationError):
            VersionCreate(version_number="1.0.0", name="x" * 256)

    def test_no_status_field_on_create(self) -> None:
        """``status`` is server-defaulted and must not be settable on create."""
        payload = VersionCreate(version_number="1.0.0", status="active")  # type: ignore[call-arg]
        # Extra field is silently ignored by default — the schema must not
        # expose a ``status`` attribute.
        assert not hasattr(payload, "status")

    def test_no_release_date_field_on_create(self) -> None:
        """``release_date`` is service-managed and excluded from create."""
        payload = VersionCreate(
            version_number="1.0.0",
            release_date=date(2026, 1, 1),  # type: ignore[call-arg]
        )
        assert not hasattr(payload, "release_date")


# ---------------------------------------------------------------------------
# VersionUpdate
# ---------------------------------------------------------------------------


class TestVersionUpdate:
    """PATCH-style schema — every field is optional."""

    def test_empty_update_is_valid(self) -> None:
        """An empty PATCH payload is accepted (no-op update)."""
        payload = VersionUpdate()
        assert payload.model_dump(exclude_unset=True) == {}

    def test_partial_update(self) -> None:
        """Only explicitly set fields appear in ``exclude_unset`` dump."""
        payload = VersionUpdate(status="active")
        assert payload.model_dump(exclude_unset=True) == {"status": "active"}

    def test_status_must_be_valid(self) -> None:
        """Unknown status values are rejected by the Literal constraint."""
        with pytest.raises(ValidationError):
            VersionUpdate(status="archived")  # type: ignore[arg-type]

    def test_all_fields_settable(self) -> None:
        """Every mutable field can be supplied in a single PATCH."""
        payload = VersionUpdate(
            version_number="1.0.1",
            name="Patch release",
            status="released",
            description="Security patch",
            target_date=date(2026, 2, 1),
            release_date=date(2026, 2, 2),
        )
        dumped = payload.model_dump(exclude_unset=True)
        assert dumped == {
            "version_number": "1.0.1",
            "name": "Patch release",
            "status": "released",
            "description": "Security patch",
            "target_date": date(2026, 2, 1),
            "release_date": date(2026, 2, 2),
        }


# ---------------------------------------------------------------------------
# VersionRead
# ---------------------------------------------------------------------------


def _make_version_namespace(**overrides: object) -> SimpleNamespace:
    """Build an ORM-like object suitable for ``from_attributes=True``.

    Uses ``SimpleNamespace`` so attribute access succeeds for all mirrored
    columns.  ``overrides`` let individual tests override any default.
    """
    now = datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc)
    base = {
        "id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "version_number": "1.0.0",
        "name": None,
        "status": "planned",
        "description": None,
        "target_date": None,
        "release_date": None,
        "created_at": now,
        "updated_at": now,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class TestVersionRead:
    """Output schema for ``GET /versions/{id}`` and list endpoints."""

    def test_round_trip_from_orm_like(self) -> None:
        """ORM-style object → VersionRead via ``model_validate``."""
        orm = _make_version_namespace(
            version_number="1.0.0",
            status="active",
            description="Initial release",
            target_date=date(2026, 5, 1),
        )
        read = VersionRead.model_validate(orm)

        assert read.id == orm.id
        assert read.project_id == orm.project_id
        assert read.version_number == "1.0.0"
        assert read.status == "active"
        assert read.description == "Initial release"
        assert read.target_date == date(2026, 5, 1)

    def test_computed_counts_default_to_zero(self) -> None:
        """Aggregate counts default to 0 when not supplied by service."""
        read = VersionRead.model_validate(_make_version_namespace())
        assert read.epic_count == 0
        assert read.epics_done == 0
        assert read.bug_count == 0

    def test_computed_counts_accept_positive_integers(self) -> None:
        """Service layer supplies aggregate counts via dict merge."""
        orm = _make_version_namespace()
        data = {
            **{f: getattr(orm, f) for f in VersionRead.model_fields if hasattr(orm, f)},
            "epic_count": 7,
            "epics_done": 3,
            "bug_count": 12,
        }
        read = VersionRead.model_validate(data)
        assert read.epic_count == 7
        assert read.epics_done == 3
        assert read.bug_count == 12

    def test_computed_counts_reject_negative(self) -> None:
        """Aggregate counts are non-negative (``ge=0``)."""
        orm = _make_version_namespace()
        base = {f: getattr(orm, f) for f in VersionRead.model_fields if hasattr(orm, f)}
        for field in ("epic_count", "epics_done", "bug_count"):
            with pytest.raises(ValidationError):
                VersionRead.model_validate({**base, field: -1})

    def test_missing_required_field_raises(self) -> None:
        """Omitting a required mirrored column raises a validation error."""
        orm = _make_version_namespace()
        bad = SimpleNamespace(**{k: v for k, v in orm.__dict__.items() if k != "version_number"})
        with pytest.raises(ValidationError):
            VersionRead.model_validate(bad)

    def test_status_must_be_valid_literal(self) -> None:
        """Unknown status values fail the Literal check on read."""
        orm = _make_version_namespace(status="archived")
        with pytest.raises(ValidationError):
            VersionRead.model_validate(orm)


# ---------------------------------------------------------------------------
# Epic / Bug schemas — version_id propagation
# ---------------------------------------------------------------------------


class TestEpicVersionId:
    """Epic schemas must expose ``version_id`` after Feat 9."""

    def test_create_accepts_version_id(self) -> None:
        """EpicCreate carries the optional ``version_id`` FK."""
        version_id = uuid.uuid4()
        payload = EpicCreate(
            project_id=uuid.uuid4(),
            title="Epic title",
            version_id=version_id,
        )
        assert payload.version_id == version_id

    def test_create_version_id_defaults_none(self) -> None:
        """``version_id`` is optional; service layer auto-assigns if omitted."""
        payload = EpicCreate(project_id=uuid.uuid4(), title="Epic title")
        assert payload.version_id is None

    def test_read_exposes_version_id(self) -> None:
        """EpicRead round-trips ``version_id`` from the ORM instance."""
        version_id = uuid.uuid4()
        now = datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc)
        orm = SimpleNamespace(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            version_id=version_id,
            number=1,
            title="Epic title",
            status="planned",
            created_at=now,
            updated_at=now,
        )
        read = EpicRead.model_validate(orm)
        assert read.version_id == version_id


class TestBugVersionId:
    """Bug schemas must expose ``version_id`` after Feat 9."""

    def test_create_accepts_version_id(self) -> None:
        """BugCreate carries the optional ``version_id`` FK."""
        version_id = uuid.uuid4()
        payload = BugCreate(
            project_id=uuid.uuid4(),
            title="Bug title",
            description="Something broke.",
            severity="major",
            created_by=uuid.uuid4(),
            version_id=version_id,
        )
        assert payload.version_id == version_id

    def test_create_version_id_defaults_none(self) -> None:
        """``version_id`` is optional; service layer auto-assigns if omitted."""
        payload = BugCreate(
            project_id=uuid.uuid4(),
            title="Bug title",
            description="Something broke.",
            severity="major",
            created_by=uuid.uuid4(),
        )
        assert payload.version_id is None

    def test_read_exposes_version_id(self) -> None:
        """BugRead round-trips ``version_id`` from the ORM instance."""
        version_id = uuid.uuid4()
        now = datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc)
        orm = SimpleNamespace(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            version_id=version_id,
            bug_number=1,
            title="Bug title",
            description="Something broke.",
            severity="major",
            status="new",
            source="internal",
            reported_by=None,
            environment=None,
            resolved_at=None,
            commit_hash=None,
            created_by=uuid.uuid4(),
            created_at=now,
            updated_at=now,
        )
        read = BugRead.model_validate(orm)
        assert read.version_id == version_id
