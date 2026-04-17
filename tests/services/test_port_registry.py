"""Tests for Port Registry Management Service.

Covers port availability checks, next-port suggestion, port conflict
detection, and allocated-port querying within the 9100–9299 range.
"""

from __future__ import annotations

import uuid

import pytest

from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.services.port_registry import (
    PORT_RANGE_MAX,
    PORT_RANGE_MIN,
    check_port_available,
    get_all_allocated_ports,
    suggest_next_port,
)

# ------------------------------------------------------------------
# Factory helpers
# ------------------------------------------------------------------


def _make_user(db_session, **overrides) -> User:
    defaults = {
        "username": f"user_{uuid.uuid4().hex[:8]}",
        "email": f"{uuid.uuid4().hex[:8]}@test.com",
        "password_hash": "hashed",
        "role": "ri",
    }
    defaults.update(overrides)
    user = User(**defaults)
    db_session.add(user)
    db_session.flush()
    return user


def _make_project(db_session, *, user=None, **overrides) -> Project:
    if user is None:
        user = _make_user(db_session)
    suffix = uuid.uuid4().hex[:8]
    defaults = {
        "name": f"Project {suffix}",
        "slug": f"proj-{suffix}",
        "category": "singlemodule",
        "description": "Test project",
        "created_by": user.id,
    }
    defaults.update(overrides)
    project = Project(**defaults)
    db_session.add(project)
    db_session.flush()
    return project


# ==================================================================
# check_port_available
# ==================================================================


class TestCheckPortAvailable:
    """Tests for check_port_available()."""

    def test_port_available_when_no_projects(self, db_session):
        """Port in valid range is available when no projects exist."""
        assert check_port_available(db_session, 9100) is True

    def test_port_unavailable_when_used_as_backend(self, db_session):
        _make_project(db_session, backend_port=9150)
        assert check_port_available(db_session, 9150) is False

    def test_port_unavailable_when_used_as_frontend(self, db_session):
        _make_project(db_session, frontend_port=9151)
        assert check_port_available(db_session, 9151) is False

    def test_port_unavailable_when_used_as_db(self, db_session):
        _make_project(db_session, db_port=9152)
        assert check_port_available(db_session, 9152) is False

    def test_port_available_for_same_project(self, db_session):
        """Port used by a project is available when checking for that project (edit mode)."""
        project = _make_project(db_session, backend_port=9160)
        assert check_port_available(db_session, 9160, project_id=str(project.id)) is True

    def test_port_unavailable_for_different_project(self, db_session):
        """Port used by project A is not available for project B."""
        _make_project(db_session, backend_port=9161)
        other = _make_project(db_session)
        assert check_port_available(db_session, 9161, project_id=str(other.id)) is False

    def test_cross_type_conflict(self, db_session):
        """A port used as frontend_port blocks its use as backend_port."""
        _make_project(db_session, frontend_port=9170)
        assert check_port_available(db_session, 9170) is False

    def test_port_below_range_raises(self, db_session):
        with pytest.raises(ValueError, match="outside the allowed range"):
            check_port_available(db_session, 9099)

    def test_port_above_range_raises(self, db_session):
        with pytest.raises(ValueError, match="outside the allowed range"):
            check_port_available(db_session, 9300)

    def test_boundary_min_valid(self, db_session):
        assert check_port_available(db_session, PORT_RANGE_MIN) is True

    def test_boundary_max_valid(self, db_session):
        assert check_port_available(db_session, PORT_RANGE_MAX) is True


# ==================================================================
# suggest_next_port
# ==================================================================


class TestSuggestNextPort:
    """Tests for suggest_next_port()."""

    def test_suggests_min_when_no_ports_allocated(self, db_session):
        """First suggestion should be the range minimum."""
        result = suggest_next_port(db_session, "backend")
        assert result == PORT_RANGE_MIN

    def test_skips_allocated_ports(self, db_session):
        """Suggestion skips already-allocated ports."""
        _make_project(db_session, backend_port=9100, frontend_port=9101, db_port=9102)
        result = suggest_next_port(db_session, "backend")
        assert result == 9103

    def test_skips_cross_type_allocation(self, db_session):
        """Suggestion for 'backend' skips ports used by frontend or db."""
        _make_project(db_session, frontend_port=9100)
        result = suggest_next_port(db_session, "backend")
        assert result == 9101

    def test_invalid_type_raises(self, db_session):
        with pytest.raises(ValueError, match="Invalid port type"):
            suggest_next_port(db_session, "invalid")

    def test_all_valid_types(self, db_session):
        """All three port types return a valid suggestion."""
        for port_type in ("backend", "frontend", "db"):
            result = suggest_next_port(db_session, port_type)
            assert PORT_RANGE_MIN <= result <= PORT_RANGE_MAX

    def test_suggests_gap_port(self, db_session):
        """When 9100 is taken but 9101 is free, suggests 9101."""
        _make_project(db_session, backend_port=9100)
        result = suggest_next_port(db_session, "backend")
        assert result == 9101


# ==================================================================
# get_all_allocated_ports
# ==================================================================


class TestGetAllAllocatedPorts:
    """Tests for get_all_allocated_ports()."""

    def test_empty_when_no_projects(self, db_session):
        result = get_all_allocated_ports(db_session)
        assert result == {"backend": [], "frontend": [], "db": []}

    def test_returns_allocated_ports(self, db_session):
        _make_project(db_session, backend_port=9100, frontend_port=9101, db_port=9102)
        result = get_all_allocated_ports(db_session)
        assert 9100 in result["backend"]
        assert 9101 in result["frontend"]
        assert 9102 in result["db"]

    def test_ignores_null_ports(self, db_session):
        _make_project(db_session, backend_port=9100)
        result = get_all_allocated_ports(db_session)
        assert result["backend"] == [9100]
        assert result["frontend"] == []
        assert result["db"] == []

    def test_multiple_projects(self, db_session):
        user = _make_user(db_session)
        _make_project(db_session, user=user, backend_port=9100)
        _make_project(db_session, user=user, backend_port=9110)
        result = get_all_allocated_ports(db_session)
        assert result["backend"] == [9100, 9110]

    def test_ports_sorted(self, db_session):
        user = _make_user(db_session)
        _make_project(db_session, user=user, backend_port=9200)
        _make_project(db_session, user=user, backend_port=9100)
        _make_project(db_session, user=user, backend_port=9150)
        result = get_all_allocated_ports(db_session)
        assert result["backend"] == [9100, 9150, 9200]
