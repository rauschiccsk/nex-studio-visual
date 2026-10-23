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
from backend.services import port_registry
from backend.services import system_setting as system_setting_service
from backend.services.port_registry import (
    PORT_BLOCK_SIZE,
    PORT_RANGE_MIN,
    HostProbeError,
    describe_port_availability,
    reserved_ranges_status,
    suggest_next_port_block,
)

# The REAL Docker map reader, captured at import time — the autouse
# ``_deterministic_host_port_probe`` fixture replaces the module attribute for
# every test, so the handful of tests that exercise the reader ITSELF (missing
# CLI, non-zero exit, timeout) must hold their own reference to it.
_REAL_DOCKER_PUBLISHED_PORTS = port_registry._docker_published_ports


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
        "type": "standard",
        "auth_mode": "password",
        "description": "Test project",
        "created_by": user.id,
    }
    defaults.update(overrides)
    project = Project(**defaults)
    db_session.add(project)
    db_session.flush()
    return project


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


# ==================================================================
# Docker published-port parsing
# ==================================================================


class TestParsePublishedPorts:
    """Tests for _parse_published_ports() — the host map reader's parser.

    Getting this wrong in either direction is expensive: missing a published
    port re-opens the double-book, inventing one blocks a port nobody holds.
    """

    def test_single_published_port(self):
        assert port_registry._parse_published_ports("nex-manager-frontend\t0.0.0.0:10111->80/tcp") == {
            10111: "nex-manager-frontend"
        }

    def test_ipv6_binding_counts(self):
        assert port_registry._parse_published_ports("svc\t[::]:10111->80/tcp") == {10111: "svc"}

    def test_dual_stack_line_yields_one_entry(self):
        """The real-world shape: Docker prints the v4 and v6 binding of one port."""
        parsed = port_registry._parse_published_ports("nex-manager-frontend\t0.0.0.0:10111->80/tcp, [::]:10111->80/tcp")
        assert parsed == {10111: "nex-manager-frontend"}

    def test_published_range_expands(self):
        parsed = port_registry._parse_published_ports("svc\t0.0.0.0:19000-19004->9000-9004/tcp")
        assert sorted(parsed) == [19000, 19001, 19002, 19003, 19004]

    def test_exposed_but_not_published_is_ignored(self):
        """``8000/tcp`` has no host binding — it must NOT read as taken."""
        assert port_registry._parse_published_ports("svc\t8000/tcp, 9000/udp") == {}

    def test_loopback_publish_counts(self):
        """A 127.0.0.1 publish still occupies the port on this host."""
        assert port_registry._parse_published_ports("db\t127.0.0.1:5432->5432/tcp") == {5432: "db"}

    def test_container_with_no_ports(self):
        assert port_registry._parse_published_ports("idle\t") == {}

    def test_implausible_range_is_rejected(self):
        """A malformed giant range must not balloon into a multi-million-port set."""
        assert port_registry._parse_published_ports("bad\t0.0.0.0:1-60000->1/tcp") == {}


# ==================================================================
# Host-aware availability — the 10111 regression
# ==================================================================


class TestHostAwareAvailability:
    """A port the HOST publishes is not free, even when our table is silent.

    Regression cover for the real incident: the cockpit recorded nex-websites
    owning frontend port 10111 while the container ``nex-manager-frontend``
    had been publishing 0.0.0.0:10111 for twelve days. Nothing noticed,
    because "free" was computed from the ``projects`` table alone.
    """

    def test_port_published_by_container_is_taken(self, db_session, host_ports):
        host_ports[10111] = "nex-manager-frontend"
        verdict = describe_port_availability(db_session, 10111)
        assert verdict.state == "taken"
        assert verdict.source == "host"
        assert verdict.holder == "nex-manager-frontend"
        assert verdict.available is False

    def test_reason_names_the_container(self, db_session, host_ports):
        """The operator must be told WHO holds it, not merely that it is taken."""
        host_ports[10111] = "nex-manager-frontend"
        verdict = describe_port_availability(db_session, 10111)
        assert "nex-manager-frontend" in verdict.reason
        assert "10111" in verdict.reason

    def test_free_when_neither_table_nor_host_holds_it(self, db_session, host_ports):
        host_ports[10111] = "nex-manager-frontend"
        verdict = describe_port_availability(db_session, 10112)
        assert verdict.state == "free"
        assert verdict.available is True

    def test_project_conflict_is_reported_before_host(self, db_session, host_ports):
        """Our own project is the more actionable answer, so it wins the report."""
        project = _make_project(db_session, backend_port=10111)
        host_ports[10111] = "nex-manager-frontend"
        verdict = describe_port_availability(db_session, 10111)
        assert verdict.source == "projects"
        assert verdict.holder == project.name

    def test_existing_double_book_is_named(self, db_session, host_ports):
        """The live incident: a project AND a container both hold the port.

        Reporting only the project would hide the collision that motivated
        this whole guard, so the container is named too.
        """
        _make_project(db_session, frontend_port=10111)
        host_ports[10111] = "nex-manager-frontend"
        verdict = describe_port_availability(db_session, 10111)
        assert "nex-manager-frontend" in verdict.reason
        assert "double-booked" in verdict.reason

    def test_no_double_book_note_when_host_is_clear(self, db_session):
        _make_project(db_session, frontend_port=10111)
        verdict = describe_port_availability(db_session, 10111)
        assert "double-booked" not in verdict.reason

    def test_own_project_exclusion_does_not_override_host(self, db_session, host_ports):
        """Editing a project cannot un-take a port the host really publishes."""
        project = _make_project(db_session, backend_port=10111)
        host_ports[10111] = "nex-manager-frontend"
        verdict = describe_port_availability(db_session, 10111, project_id=str(project.id))
        assert verdict.state == "taken"
        assert verdict.source == "host"

    def test_suggest_block_skips_host_held_block(self, db_session, host_ports):
        """A single host-held port disqualifies the whole block it sits in."""
        host_ports[PORT_RANGE_MIN + 5] = "some-neighbour"
        assert suggest_next_port_block(db_session) == PORT_RANGE_MIN + PORT_BLOCK_SIZE

    def test_bind_probe_can_add_taken(self, db_session, monkeypatch):
        """A local bind refusal is hard evidence, even with an empty Docker map."""
        monkeypatch.setattr(port_registry, "_bind_probe_says_taken", lambda port: port == 10123)
        verdict = describe_port_availability(db_session, 10123)
        assert verdict.state == "taken"
        assert verdict.source == "probe"


# ==================================================================
# Fail-closed: an unverifiable port is never offered
# ==================================================================


def _break_host_probe(monkeypatch, message="daemon unreachable"):
    """Make the Docker map unreadable, as when the daemon is down."""

    def _raise(timeout=port_registry.HOST_PROBE_TIMEOUT_SECONDS):
        raise HostProbeError(message)

    monkeypatch.setattr(port_registry, "_docker_published_ports", _raise)
    port_registry.invalidate_host_port_cache()


class TestHostProbeFailureIsNotFree:
    """When the host cannot be consulted the answer is UNKNOWN, never FREE."""

    def test_state_is_unknown(self, db_session, monkeypatch):
        _break_host_probe(monkeypatch)
        verdict = describe_port_availability(db_session, 10123)
        assert verdict.state == "unknown"
        assert verdict.source == "host"

    def test_unknown_reason_is_explained(self, db_session, monkeypatch):
        _break_host_probe(monkeypatch, "daemon unreachable")
        verdict = describe_port_availability(db_session, 10123)
        assert "daemon unreachable" in verdict.reason

    def test_suggest_block_refuses(self, db_session, monkeypatch):
        _break_host_probe(monkeypatch)
        with pytest.raises(HostProbeError):
            suggest_next_port_block(db_session)

    def test_table_conflict_still_answered_without_the_host(self, db_session, monkeypatch):
        """A conflict we can prove from our own table needs no host probe."""
        _make_project(db_session, backend_port=10123)
        _break_host_probe(monkeypatch)
        verdict = describe_port_availability(db_session, 10123)
        assert verdict.state == "taken"
        assert verdict.source == "projects"

    def test_missing_docker_cli_raises_host_probe_error(self, monkeypatch):
        monkeypatch.setattr(port_registry.shutil, "which", lambda name: None)
        with pytest.raises(HostProbeError, match="Docker CLI not found"):
            _REAL_DOCKER_PUBLISHED_PORTS()

    def test_docker_nonzero_exit_raises_host_probe_error(self, monkeypatch):
        class _Proc:
            returncode = 1
            stdout = ""
            stderr = "Cannot connect to the Docker daemon"

        monkeypatch.setattr(port_registry.shutil, "which", lambda name: "/usr/bin/docker")
        monkeypatch.setattr(port_registry.subprocess, "run", lambda *a, **kw: _Proc())
        with pytest.raises(HostProbeError, match="Cannot connect to the Docker daemon"):
            _REAL_DOCKER_PUBLISHED_PORTS()

    def test_docker_timeout_raises_host_probe_error(self, monkeypatch):
        def _timeout(*args, **kwargs):
            raise port_registry.subprocess.TimeoutExpired(cmd="docker ps", timeout=5.0)

        monkeypatch.setattr(port_registry.shutil, "which", lambda name: "/usr/bin/docker")
        monkeypatch.setattr(port_registry.subprocess, "run", _timeout)
        with pytest.raises(HostProbeError, match="Timed out"):
            _REAL_DOCKER_PUBLISHED_PORTS()

    def test_empty_docker_output_is_an_empty_map_not_an_error(self, monkeypatch):
        """A host with no containers is a legitimate, KNOWN answer."""

        class _Proc:
            returncode = 0
            stdout = ""
            stderr = ""

        monkeypatch.setattr(port_registry.shutil, "which", lambda name: "/usr/bin/docker")
        monkeypatch.setattr(port_registry.subprocess, "run", lambda *a, **kw: _Proc())
        assert _REAL_DOCKER_PUBLISHED_PORTS() == {}

    def test_unknown_does_not_read_as_available(self, db_session, monkeypatch):
        _break_host_probe(monkeypatch)
        assert describe_port_availability(db_session, 10123).available is False


# ==================================================================
# Host snapshot cache
# ==================================================================


class TestHostPortCache:
    """The probe must be cheap enough to sit on every port check."""

    def test_repeated_checks_hit_docker_once(self, db_session, monkeypatch):
        calls = []

        def _counting(timeout=port_registry.HOST_PROBE_TIMEOUT_SECONDS):
            calls.append(timeout)
            return {}

        monkeypatch.setattr(port_registry, "_docker_published_ports", _counting)
        port_registry.invalidate_host_port_cache()

        for port in (10120, 10121, 10122):
            describe_port_availability(db_session, port)

        assert len(calls) == 1

    def test_invalidate_forces_a_fresh_read(self, db_session, monkeypatch):
        calls = []

        def _counting(timeout=port_registry.HOST_PROBE_TIMEOUT_SECONDS):
            calls.append(timeout)
            return {}

        monkeypatch.setattr(port_registry, "_docker_published_ports", _counting)
        port_registry.invalidate_host_port_cache()

        describe_port_availability(db_session, 10120)
        port_registry.invalidate_host_port_cache()
        describe_port_availability(db_session, 10120)

        assert len(calls) == 2

    def test_returned_map_is_a_copy(self, db_session, monkeypatch, host_ports):
        """Mutating the returned map must not poison the cached snapshot."""
        host_ports[10130] = "neighbour"
        first = port_registry.get_host_taken_ports()
        first[10131] = "injected"
        assert 10131 not in port_registry.get_host_taken_ports()
        assert describe_port_availability(db_session, 10131).state == "free"

    def test_failure_is_cached_but_still_raises_every_time(self, db_session, monkeypatch):
        """A down daemon must not cost a full timeout on every single check —
        but the cached outcome is still a REFUSAL, never a fallback to free."""
        calls = []

        def _raise(timeout=port_registry.HOST_PROBE_TIMEOUT_SECONDS):
            calls.append(timeout)
            raise HostProbeError("daemon unreachable")

        monkeypatch.setattr(port_registry, "_docker_published_ports", _raise)
        port_registry.invalidate_host_port_cache()

        for port in (10120, 10121, 10122):
            assert describe_port_availability(db_session, port).state == "unknown"

        assert len(calls) == 1


# ==================================================================
# reserved_port_ranges — no longer inert
# ==================================================================


def _set_reserved(db_session, value: str) -> None:
    system_setting_service.upsert(db_session, "reserved_port_ranges", value)


class TestReservedRanges:
    """The setting ships EMPTY; that must be visible, and when set it must bite."""

    def test_unconfigured_is_reported_as_such(self, db_session):
        status = reserved_ranges_status(db_session)
        assert status.configured is False
        assert status.ranges == ()

    def test_unconfigured_produces_an_operator_warning(self, db_session):
        """Empty used to be silently inert — the operator now gets told."""
        warnings = reserved_ranges_status(db_session).warnings
        assert len(warnings) == 1
        assert "nie sú nastavené" in warnings[0]

    def test_warning_travels_with_the_verdict(self, db_session):
        verdict = describe_port_availability(db_session, 10123)
        assert any("nie sú nastavené" in w for w in verdict.warnings)

    def test_configured_range_parses(self, db_session):
        _set_reserved(db_session, "10200-10209, 10300-10301")
        status = reserved_ranges_status(db_session)
        assert status.configured is True
        assert status.ranges == ((10200, 10209), (10300, 10301))
        assert status.warnings == []

    def test_reserved_port_is_taken(self, db_session):
        _set_reserved(db_session, "10200-10209")
        verdict = describe_port_availability(db_session, 10205)
        assert verdict.state == "taken"
        assert verdict.source == "reserved"
        assert verdict.holder == "10200-10209"

    def test_reserved_block_is_not_suggested(self, db_session):
        _set_reserved(db_session, f"{PORT_RANGE_MIN + 2}-{PORT_RANGE_MIN + 2}")
        assert suggest_next_port_block(db_session) == PORT_RANGE_MIN + PORT_BLOCK_SIZE

    def test_malformed_entry_is_surfaced_not_swallowed(self, db_session):
        """A typo means a guard the operator believes in is doing nothing."""
        _set_reserved(db_session, "10200-10209, not-a-range")
        status = reserved_ranges_status(db_session)
        assert status.malformed == ("not-a-range",)
        assert any("Nezrozumiteľné" in w for w in status.warnings)

    def test_valid_entries_still_apply_alongside_a_malformed_one(self, db_session):
        _set_reserved(db_session, "10200-10209, not-a-range")
        assert reserved_ranges_status(db_session).ranges == ((10200, 10209),)
        assert describe_port_availability(db_session, 10205).state == "taken"

    def test_inverted_range_is_malformed(self, db_session):
        """``10209-10200`` silently expanded to nothing before."""
        _set_reserved(db_session, "10209-10200")
        status = reserved_ranges_status(db_session)
        assert status.malformed == ("10209-10200",)
        assert status.ranges == ()

    def test_entry_without_separator_is_malformed(self, db_session):
        _set_reserved(db_session, "10200")
        assert reserved_ranges_status(db_session).malformed == ("10200",)
