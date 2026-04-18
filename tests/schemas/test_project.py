"""Pydantic schema tests for :mod:`backend.schemas.project`.

Pure schema-level validation — exercises field constraints, defaults,
Literal membership and ORM round-trip via ``from_attributes=True``
without touching the database.

NOTE: ``project_members`` table was dropped in Feat 26.  These tests
verify that no member-related fields (``member_ids``, ``members``)
exist on any project schema.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import get_args

import pytest
from pydantic import ValidationError

from backend.schemas.project import (
    ProjectCategory,
    ProjectCreate,
    ProjectRead,
    ProjectStatus,
    ProjectUpdate,
)

# ---------------------------------------------------------------------------
# Literal aliases
# ---------------------------------------------------------------------------


class TestProjectCategory:
    """``ProjectCategory`` mirrors the DB CHECK constraint."""

    def test_allowed_values(self) -> None:
        assert set(get_args(ProjectCategory)) == {"singlemodule", "multimodule"}


class TestProjectStatus:
    """``ProjectStatus`` mirrors the DB CHECK constraint."""

    def test_allowed_values(self) -> None:
        assert set(get_args(ProjectStatus)) == {"active", "archived", "paused"}


# ---------------------------------------------------------------------------
# ProjectCreate
# ---------------------------------------------------------------------------


class TestProjectCreate:
    """Input schema for ``POST /projects``."""

    def _minimal(self, **overrides: object) -> dict:
        base: dict = {
            "name": "Test Project",
            "slug": "test-project",
            "category": "singlemodule",
            "description": "A test project.",
            "created_by": uuid.uuid4(),
        }
        base.update(overrides)
        return base

    def test_minimal_payload(self) -> None:
        payload = ProjectCreate(**self._minimal())
        assert payload.name == "Test Project"
        assert payload.slug == "test-project"
        assert payload.category == "singlemodule"
        assert payload.status == "active"
        assert payload.backend_port is None
        assert payload.frontend_port is None
        assert payload.db_port is None
        assert payload.repo_url is None
        assert payload.source_path is None
        assert payload.kb_path is None
        assert payload.guardian_enabled is False

    def test_full_payload(self) -> None:
        payload = ProjectCreate(
            **self._minimal(
                status="paused",
                backend_port=9100,
                frontend_port=9101,
                db_port=5432,
                repo_url="org/repo",
                source_path="/opt/repo-src/",
                kb_path="/home/icc/knowledge/projects/repo/",
                guardian_enabled=True,
            )
        )
        assert payload.status == "paused"
        assert payload.backend_port == 9100
        assert payload.guardian_enabled is True

    def test_required_fields(self) -> None:
        """name, slug, category, description are required. created_by is optional (resolved server-side)."""
        for field in ("name", "slug", "category", "description"):
            data = self._minimal()
            del data[field]
            with pytest.raises(ValidationError) as excinfo:
                ProjectCreate(**data)
            errors = excinfo.value.errors()
            assert any(err["loc"] == (field,) for err in errors), f"Expected error for missing '{field}'"

    def test_created_by_is_optional(self) -> None:
        """created_by defaults to None — resolved from the active session on the server."""
        data = self._minimal()
        del data["created_by"]
        project = ProjectCreate(**data)
        assert project.created_by is None

    def test_name_rejects_empty(self) -> None:
        with pytest.raises(ValidationError):
            ProjectCreate(**self._minimal(name=""))

    def test_name_rejects_overlong(self) -> None:
        with pytest.raises(ValidationError):
            ProjectCreate(**self._minimal(name="x" * 256))

    def test_slug_rejects_empty(self) -> None:
        with pytest.raises(ValidationError):
            ProjectCreate(**self._minimal(slug=""))

    def test_slug_rejects_overlong(self) -> None:
        with pytest.raises(ValidationError):
            ProjectCreate(**self._minimal(slug="x" * 101))

    def test_category_rejects_invalid(self) -> None:
        with pytest.raises(ValidationError):
            ProjectCreate(**self._minimal(category="unknown"))

    def test_status_rejects_invalid(self) -> None:
        with pytest.raises(ValidationError):
            ProjectCreate(**self._minimal(status="deleted"))

    def test_no_member_ids_field(self) -> None:
        """member_ids was removed in Feat 26 — must not exist."""
        assert "member_ids" not in ProjectCreate.model_fields

    def test_no_members_field(self) -> None:
        """members was removed in Feat 26 — must not exist."""
        assert "members" not in ProjectCreate.model_fields


# ---------------------------------------------------------------------------
# ProjectUpdate
# ---------------------------------------------------------------------------


class TestProjectUpdate:
    """PATCH-style schema — every field is optional."""

    def test_empty_update_is_valid(self) -> None:
        payload = ProjectUpdate()
        assert payload.model_dump(exclude_unset=True) == {}

    def test_partial_update(self) -> None:
        payload = ProjectUpdate(name="Updated Name")
        assert payload.model_dump(exclude_unset=True) == {"name": "Updated Name"}

    def test_status_must_be_valid(self) -> None:
        with pytest.raises(ValidationError):
            ProjectUpdate(status="deleted")  # type: ignore[arg-type]

    def test_all_mutable_fields_settable(self) -> None:
        payload = ProjectUpdate(
            name="New Name",
            description="New description",
            status="archived",
            backend_port=9200,
            frontend_port=9201,
            db_port=5433,
            repo_url="org/new-repo",
            source_path="/opt/new/",
            kb_path="/home/icc/knowledge/new/",
            guardian_enabled=True,
        )
        dumped = payload.model_dump(exclude_unset=True)
        assert dumped["name"] == "New Name"
        assert dumped["status"] == "archived"
        assert dumped["guardian_enabled"] is True

    def test_immutable_fields_excluded(self) -> None:
        """id, slug, category, created_at, created_by are not settable."""
        for field in ("id", "slug", "category", "created_at", "created_by"):
            assert field not in ProjectUpdate.model_fields, f"Immutable field '{field}' should not be in ProjectUpdate"

    def test_no_members_field(self) -> None:
        """members was removed in Feat 26 — must not exist."""
        assert "members" not in ProjectUpdate.model_fields


# ---------------------------------------------------------------------------
# ProjectRead
# ---------------------------------------------------------------------------


def _make_project_namespace(**overrides: object) -> SimpleNamespace:
    """Build an ORM-like object suitable for ``from_attributes=True``."""
    now = datetime(2026, 4, 18, 12, 0, tzinfo=timezone.utc)
    base: dict = {
        "id": uuid.uuid4(),
        "name": "Test Project",
        "slug": "test-project",
        "category": "singlemodule",
        "description": "A test project.",
        "status": "active",
        "backend_port": None,
        "frontend_port": None,
        "db_port": None,
        "repo_url": None,
        "source_path": None,
        "kb_path": None,
        "guardian_enabled": False,
        "created_by": uuid.uuid4(),
        "created_at": now,
        "updated_at": now,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class TestProjectRead:
    """Output schema for ``GET /projects/{id}`` and list endpoints."""

    def test_round_trip_from_orm_like(self) -> None:
        orm = _make_project_namespace(
            name="Horizont",
            category="multimodule",
            status="paused",
        )
        read = ProjectRead.model_validate(orm)
        assert read.id == orm.id
        assert read.name == "Horizont"
        assert read.category == "multimodule"
        assert read.status == "paused"
        assert read.created_by == orm.created_by
        assert read.created_at == orm.created_at

    def test_all_optional_fields_none(self) -> None:
        read = ProjectRead.model_validate(_make_project_namespace())
        assert read.backend_port is None
        assert read.frontend_port is None
        assert read.db_port is None
        assert read.repo_url is None
        assert read.source_path is None
        assert read.kb_path is None

    def test_missing_required_field_raises(self) -> None:
        orm = _make_project_namespace()
        bad = SimpleNamespace(**{k: v for k, v in orm.__dict__.items() if k != "name"})
        with pytest.raises(ValidationError):
            ProjectRead.model_validate(bad)

    def test_status_must_be_valid_literal(self) -> None:
        orm = _make_project_namespace(status="deleted")
        with pytest.raises(ValidationError):
            ProjectRead.model_validate(orm)

    def test_category_must_be_valid_literal(self) -> None:
        orm = _make_project_namespace(category="triplemodule")
        with pytest.raises(ValidationError):
            ProjectRead.model_validate(orm)

    def test_no_members_field(self) -> None:
        """members was removed in Feat 26 — must not exist on read schema."""
        assert "members" not in ProjectRead.model_fields

    def test_no_member_ids_field(self) -> None:
        """member_ids was removed in Feat 26 — must not exist on read schema."""
        assert "member_ids" not in ProjectRead.model_fields
