"""Tests for Port Registry Management Service.

Covers port availability checks, next-port suggestion, block-based
port suggestion, port conflict detection, and allocated-port querying
within the 10100–14999 range (ICC DECISIONS.md D-020, Port Registry v2,
commercial projects band — 10-port blocks per project).
"""

from __future__ import annotations

import uuid

import pytest

from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.services.port_registry import (
    PORT_BLOCK_SIZE,
    PORT_RANGE_MAX,
    PORT_RANGE_MIN,
    check_port_available,
    get_all_allocated_ports,
    suggest_next_port,
    suggest_next_port_block,
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
        assert check_port_available(db_session, 10100) is True

    def test_port_unavailable_when_used_as_backend(self, db_session):
        _make_project(db_session, backend_port=10150)
        assert check_port_available(db_session, 10150) is False

    def test_port_unavailable_when_used_as_frontend(self, db_session):
        _make_project(db_session, frontend_port=10151)
        assert check_port_available(db_session, 10151) is False

    def test_port_unavailable_when_used_as_db(self, db_session):
        _make_project(db_session, db_port=10152)
        assert check_port_available(db_session, 10152) is False

    def test_port_available_for_same_project(self, db_session):
        """Port used by a project is available when checking for that project (edit mode)."""
        project = _make_project(db_session, backend_port=10160)
        assert check_port_available(db_session, 10160, project_id=str(project.id)) is True

    def test_port_unavailable_for_different_project(self, db_session):
        """Port used by project A is not available for project B."""
        _make_project(db_session, backend_port=10161)
        other = _make_project(db_session)
        assert check_port_available(db_session, 10161, project_id=str(other.id)) is False

    def test_cross_type_conflict(self, db_session):
        """A port used as frontend_port blocks its use as backend_port."""
        _make_project(db_session, frontend_port=10170)
        assert check_port_available(db_session, 10170) is False

    def test_port_below_range_raises(self, db_session):
        with pytest.raises(ValueError, match="outside the allowed range"):
            check_port_available(db_session, 10099)

    def test_port_above_range_raises(self, db_session):
        with pytest.raises(ValueError, match="outside the allowed range"):
            check_port_available(db_session, 15000)

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
        _make_project(db_session, backend_port=10100, frontend_port=10101, db_port=10102)
        result = suggest_next_port(db_session, "backend")
        assert result == 10103

    def test_skips_cross_type_allocation(self, db_session):
        """Suggestion for 'backend' skips ports used by frontend or db."""
        _make_project(db_session, frontend_port=10100)
        result = suggest_next_port(db_session, "backend")
        assert result == 10101

    def test_invalid_type_raises(self, db_session):
        with pytest.raises(ValueError, match="Invalid port type"):
            suggest_next_port(db_session, "invalid")

    def test_all_valid_types(self, db_session):
        """All three port types return a valid suggestion."""
        for port_type in ("backend", "frontend", "db"):
            result = suggest_next_port(db_session, port_type)
            assert PORT_RANGE_MIN <= result <= PORT_RANGE_MAX

    def test_suggests_gap_port(self, db_session):
        """When 10100 is taken but 10101 is free, suggests 10101."""
        _make_project(db_session, backend_port=10100)
        result = suggest_next_port(db_session, "backend")
        assert result == 10101


# ==================================================================
# get_all_allocated_ports
# ==================================================================


class TestGetAllAllocatedPorts:
    """Tests for get_all_allocated_ports()."""

    def test_empty_when_no_projects(self, db_session):
        result = get_all_allocated_ports(db_session)
        assert result == {"backend": [], "frontend": [], "db": []}

    def test_returns_allocated_ports(self, db_session):
        _make_project(db_session, backend_port=10100, frontend_port=10101, db_port=10102)
        result = get_all_allocated_ports(db_session)
        assert 10100 in result["backend"]
        assert 10101 in result["frontend"]
        assert 10102 in result["db"]

    def test_ignores_null_ports(self, db_session):
        _make_project(db_session, backend_port=10100)
        result = get_all_allocated_ports(db_session)
        assert result["backend"] == [10100]
        assert result["frontend"] == []
        assert result["db"] == []

    def test_multiple_projects(self, db_session):
        user = _make_user(db_session)
        _make_project(db_session, user=user, backend_port=10100)
        _make_project(db_session, user=user, backend_port=10110)
        result = get_all_allocated_ports(db_session)
        assert result["backend"] == [10100, 10110]

    def test_ports_sorted(self, db_session):
        user = _make_user(db_session)
        _make_project(db_session, user=user, backend_port=10200)
        _make_project(db_session, user=user, backend_port=10100)
        _make_project(db_session, user=user, backend_port=10150)
        result = get_all_allocated_ports(db_session)
        assert result["backend"] == [10100, 10150, 10200]


# ==================================================================
# suggest_next_port_block
# ==================================================================


class TestSuggestNextPortBlock:
    """Tests for suggest_next_port_block()."""

    def test_empty_db_returns_range_min(self, db_session):
        """With no projects, the first free block starts at PORT_RANGE_MIN."""
        base = suggest_next_port_block(db_session)
        assert base == PORT_RANGE_MIN

    def test_default_block_size_is_ten(self, db_session):
        assert PORT_BLOCK_SIZE == 10

    def test_first_block_in_use_returns_second_block(self, db_session):
        """A single port in the first block marks the whole block occupied."""
        _make_project(db_session, backend_port=PORT_RANGE_MIN)
        base = suggest_next_port_block(db_session)
        assert base == PORT_RANGE_MIN + PORT_BLOCK_SIZE

    def test_port_in_middle_of_block_occupies_block(self, db_session):
        """A port at base+5 still marks the block as occupied."""
        _make_project(db_session, backend_port=PORT_RANGE_MIN + 5)
        base = suggest_next_port_block(db_session)
        assert base == PORT_RANGE_MIN + PORT_BLOCK_SIZE

    def test_two_blocks_in_use_returns_third(self, db_session):
        user = _make_user(db_session)
        _make_project(db_session, user=user, backend_port=PORT_RANGE_MIN)
        _make_project(db_session, user=user, backend_port=PORT_RANGE_MIN + PORT_BLOCK_SIZE)
        base = suggest_next_port_block(db_session)
        assert base == PORT_RANGE_MIN + 2 * PORT_BLOCK_SIZE

    def test_gap_block_is_preferred(self, db_session):
        """If block 1 is taken and block 3 is taken, block 2 (first free) wins."""
        user = _make_user(db_session)
        _make_project(db_session, user=user, backend_port=PORT_RANGE_MIN)
        _make_project(db_session, user=user, backend_port=PORT_RANGE_MIN + 2 * PORT_BLOCK_SIZE)
        base = suggest_next_port_block(db_session)
        assert base == PORT_RANGE_MIN + PORT_BLOCK_SIZE

    def test_block_reserve_slots_occupy_block(self, db_session):
        """A port at +9 (last reserve slot) still marks the block occupied."""
        _make_project(db_session, backend_port=PORT_RANGE_MIN + PORT_BLOCK_SIZE - 1)
        base = suggest_next_port_block(db_session)
        assert base == PORT_RANGE_MIN + PORT_BLOCK_SIZE

    def test_all_three_services_in_same_block_counted_once(self, db_session):
        """One project using backend+frontend+db in the first block still frees only the second."""
        _make_project(
            db_session,
            backend_port=PORT_RANGE_MIN,
            frontend_port=PORT_RANGE_MIN + 1,
            db_port=PORT_RANGE_MIN + 2,
        )
        base = suggest_next_port_block(db_session)
        assert base == PORT_RANGE_MIN + PORT_BLOCK_SIZE

    def test_invalid_block_size_raises(self, db_session):
        with pytest.raises(ValueError, match="block_size must be positive"):
            suggest_next_port_block(db_session, block_size=0)

    def test_negative_block_size_raises(self, db_session):
        with pytest.raises(ValueError, match="block_size must be positive"):
            suggest_next_port_block(db_session, block_size=-1)

    def test_custom_block_size(self, db_session):
        """A smaller block size still respects the allocation logic."""
        _make_project(db_session, backend_port=PORT_RANGE_MIN)
        # With block_size=5, the port PORT_RANGE_MIN occupies block [MIN..MIN+4].
        base = suggest_next_port_block(db_session, block_size=5)
        assert base == PORT_RANGE_MIN + 5
