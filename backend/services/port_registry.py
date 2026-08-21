"""Port Registry Management Service.

Provides port availability checking, next-port suggestion, block-based
port suggestion and allocated-port querying for projects managed in
NEX Studio.

Range follows ICC DECISIONS.md D-020 (Port Registry v2): new projects
land in the **commercial projects** band ``10100–14999``, ten ports
per block. Legacy ICC internal apps (NEX Command, NEX Automat,
emcenter-web, NEX Studio itself, …) stay on their 9100–9199 ports in
their own infrastructure — their rows live in other databases, so this
validator never sees them and does not need to tolerate the old range.

Three sources of truth
----------------------
"Free" used to mean *"free according to our own ``projects`` table"* —
which is not the same thing as free. The cockpit is one tenant of a
shared host: NEX Manager, per-customer UAT stacks and hand-rolled infra
publish ports on ANDROS without ever appearing in this database. That
gap was not theoretical — the cockpit recorded ``nex-websites`` owning
frontend port ``10111`` while the container ``nex-manager-frontend`` had
been publishing ``0.0.0.0:10111`` for twelve days. A silent double-book.

Availability is therefore resolved against **three** sources, and a port
is offered only when *all three* agree it is free:

1. ``projects`` — ports this cockpit has already handed out.
2. ``reserved_port_ranges`` — operator-declared reservations for stacks
   managed outside NEX Studio (see :func:`reserved_ranges_status`).
3. **the host itself** — the Docker published-port map, read through the
   mounted ``/var/run/docker.sock`` (:func:`get_host_taken_ports`), plus
   a local bind probe that can only ever *add* a "taken" verdict.

The host probe is fail-CLOSED. When the host cannot be consulted the
answer is :data:`UNKNOWN`, never "free" — an unverifiable port is not an
available port, and handing one out is how the 10111 collision happened.
Callers that allocate (:func:`suggest_next_port`,
:func:`suggest_next_port_block`) refuse outright with
:class:`HostProbeError` rather than guess.
"""

from __future__ import annotations

import errno
import logging
import re
import shutil
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Literal
from uuid import UUID

import yaml
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from backend.db.models.projects import Project
from backend.services import system_setting as system_setting_service

logger = logging.getLogger(__name__)

# ICC Port Registry v2 — commercial projects band (DECISIONS.md D-020).
# The actual range + block size are resolved at request time from
# ``system_settings`` (keys ``port_range_min`` / ``port_range_max`` /
# ``port_block_size``) via the helpers below; ``DEFAULT_SETTINGS`` in
# :mod:`backend.services.system_setting` carries the initial values.
#
# The module-level constants below are the compile-time defaults —
# tests import them as expected values and they match the registered
# defaults. Runtime code paths always read from the DB via the
# private helpers so a Settings-UI override takes effect.
PORT_RANGE_MIN = 10100
PORT_RANGE_MAX = 14999
PORT_BLOCK_SIZE = 10
PORT_TYPES = ("backend", "frontend", "db")

#: Wall-clock budget for one ``docker ps`` call. Generous enough for a busy
#: daemon, short enough that the new-project form never feels stuck: the
#: result is cached (:data:`HOST_PROBE_CACHE_TTL_SECONDS`) so a create that
#: validates three ports shells out at most once.
HOST_PROBE_TIMEOUT_SECONDS = 5.0

#: How long a host published-port snapshot stays fresh. Ports do not appear
#: and vanish on a sub-second cadence, and every allocation path re-reads the
#: ``projects`` table (uncached) anyway, so a few seconds of staleness cannot
#: produce a double-book that the DB unique checks would not already catch.
HOST_PROBE_CACHE_TTL_SECONDS = 5.0

#: Failures are cached too, briefly — otherwise a stopped Docker daemon makes
#: every single port check pay the full timeout.
HOST_PROBE_FAILURE_CACHE_TTL_SECONDS = 2.0

FREE: Literal["free"] = "free"
TAKEN: Literal["taken"] = "taken"
UNKNOWN: Literal["unknown"] = "unknown"

PortState = Literal["free", "taken", "unknown"]

#: Where a verdict came from — surfaced so the operator is told *who* holds a
#: port, not merely that "something" does.
PortSource = Literal["projects", "reserved", "host", "probe"]


class HostProbeError(RuntimeError):
    """The host's published-port map could not be determined.

    Raised by :func:`get_host_taken_ports` and propagated by the allocating
    helpers. It is deliberately NOT swallowed into a "free" verdict: the whole
    point of consulting the host is that we stop offering ports we cannot
    vouch for.
    """


# ── Host consultation ────────────────────────────────────────────────


# Matches the HOST side of a Docker published-port mapping, e.g.
#   ``0.0.0.0:10111->80/tcp``          → 10111
#   ``[::]:10111->80/tcp``             → 10111
#   ``127.0.0.1:5432->5432/tcp``       → 5432
#   ``0.0.0.0:19000-19004->9000-9004/tcp`` → 19000..19004
# The ``ip:`` prefix AND the ``->`` are both required, which is exactly what
# separates a *published* port (reachable on the host) from a merely *exposed*
# one (``8000/tcp``, no host binding, must NOT count as taken).
_PUBLISHED_PORT_RE = re.compile(
    r"(?:\[[0-9A-Fa-f:]+\]|\d{1,3}(?:\.\d{1,3}){3}):(\d+)(?:-(\d+))?->",
)

_host_cache_lock = threading.Lock()
#: ``(ports_by_number, error, expires_at_monotonic)`` — exactly one of the
#: first two is set.
_host_cache: tuple[dict[int, str] | None, HostProbeError | None, float] | None = None


def invalidate_host_port_cache() -> None:
    """Drop the cached host published-port snapshot.

    Called by tests, and available to any caller that has just changed the
    host's port layout and wants the next check to see reality immediately.
    """
    global _host_cache
    with _host_cache_lock:
        _host_cache = None


def _parse_published_ports(names_and_ports: str) -> dict[int, str]:
    """Parse one ``<name>\\t<ports>`` line into ``{host_port: container_name}``."""
    name, _, ports_field = names_and_ports.partition("\t")
    name = name.strip() or "?"
    found: dict[int, str] = {}
    for match in _PUBLISHED_PORT_RE.finditer(ports_field):
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else start
        if end < start:
            start, end = end, start
        # A published range is normally a handful of ports; clamp defensively so
        # a malformed line can never make us allocate a multi-million-entry set.
        if end - start > 4096:
            logger.warning(
                "Ignoring implausible published port range %d-%d from container %r",
                start,
                end,
                name,
            )
            continue
        for port in range(start, end + 1):
            found.setdefault(port, name)
    return found


def _docker_published_ports(timeout: float = HOST_PROBE_TIMEOUT_SECONDS) -> dict[int, str]:
    """Return ``{host_port: container_name}`` for every published Docker port.

    Reads the HOST's map through the mounted ``/var/run/docker.sock``. This is
    the primary host signal precisely because it is namespace-independent: the
    backend runs in its own network namespace (``docker-compose.yml`` publishes
    ``9213:9176``), so binding a socket in here says nothing whatsoever about
    what the host has bound.

    Raises
    ------
    HostProbeError
        When the Docker CLI is missing, the daemon is unreachable, the call
        times out, or the command exits non-zero. Never returns a partial or
        empty map to paper over a failure.
    """
    docker_bin = shutil.which("docker")
    if docker_bin is None:
        raise HostProbeError(
            "Docker CLI not found on PATH — cannot read the host's published-port map, "
            "so port availability cannot be verified against the host."
        )

    try:
        proc = subprocess.run(  # noqa: S603 — fixed argv, no shell, resolved binary
            [docker_bin, "ps", "--no-trunc", "--format", "{{.Names}}\t{{.Ports}}"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise HostProbeError(
            f"Timed out after {timeout:g}s reading the host's published-port map from Docker."
        ) from exc
    except OSError as exc:
        raise HostProbeError(f"Could not run the Docker CLI to read the host's published-port map: {exc}") from exc

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        hint = detail[-1] if detail else f"exit code {proc.returncode}"
        raise HostProbeError(f"Docker refused to list containers while checking host ports: {hint}")

    published: dict[int, str] = {}
    for line in proc.stdout.splitlines():
        for port, name in _parse_published_ports(line).items():
            published.setdefault(port, name)
    return published


def get_host_taken_ports(*, timeout: float = HOST_PROBE_TIMEOUT_SECONDS) -> dict[int, str]:
    """Return the cached ``{host_port: container_name}`` map for this host.

    Raises
    ------
    HostProbeError
        When the host cannot be consulted. Callers MUST treat this as
        :data:`UNKNOWN` — never as "free".
    """
    global _host_cache
    now = time.monotonic()

    with _host_cache_lock:
        cached = _host_cache
        if cached is not None and cached[2] > now:
            ports, error, _ = cached
            if error is not None:
                raise error
            if ports is None:
                # Cannot happen by construction, but this decides whether a port
                # is offered — so a broken invariant refuses rather than trusting
                # itself. (Not an ``assert``: ``python -O`` strips those, and this
                # guard must survive it.)
                raise HostProbeError("Host published-port cache is in an inconsistent state.")
            # Defensive copy — a caller mutating the returned map would otherwise
            # poison every subsequent availability answer for the cache lifetime.
            return dict(ports)

    try:
        ports = _docker_published_ports(timeout)
    except HostProbeError as exc:
        with _host_cache_lock:
            _host_cache = (None, exc, time.monotonic() + HOST_PROBE_FAILURE_CACHE_TTL_SECONDS)
        raise

    with _host_cache_lock:
        _host_cache = (ports, None, time.monotonic() + HOST_PROBE_CACHE_TTL_SECONDS)
    return dict(ports)


def _bind_probe_says_taken(port: int) -> bool:
    """Return ``True`` only when a local bind PROVES *port* is held.

    Supplementary signal, deliberately one-directional. A *failed* bind is
    hard evidence that something holds the port. A *successful* bind proves
    nothing about the host when we run inside a container network namespace,
    so this function never contributes a "free" verdict — it can only add
    ``taken`` on top of the Docker map.

    ``SO_REUSEADDR`` is set so a socket lingering in ``TIME_WAIT`` (genuinely
    free) is not misreported as taken.
    """
    taken = False
    for family, address in ((socket.AF_INET, "0.0.0.0"), (socket.AF_INET6, "::")):  # noqa: S104
        try:
            with socket.socket(family, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                if family == socket.AF_INET6:
                    try:
                        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                    except OSError:  # pragma: no cover — platform-dependent
                        pass
                sock.bind((address, port))
        except OSError as exc:
            if exc.errno in (errno.EADDRINUSE, errno.EACCES):
                taken = True
            # Anything else (no IPv6 stack, EAFNOSUPPORT, …) is simply "no
            # signal from this family" — never evidence of availability.
    return taken


def _port_range(db: Session) -> tuple[int, int]:
    return (
        system_setting_service.get_int(db, "port_range_min"),
        system_setting_service.get_int(db, "port_range_max"),
    )


def _port_block_size(db: Session) -> int:
    return system_setting_service.get_int(db, "port_block_size")


@dataclass(frozen=True)
class PortAvailability:
    """Tri-state verdict for a single port.

    ``state`` is deliberately three-valued. A plain boolean forced every
    caller to collapse "we could not check" into one of the two answers, and
    the collapse always went the wrong way — an unverifiable port read as
    available.
    """

    port: int
    state: PortState
    #: Who holds the port — a project name, a container name, or a reserved
    #: range rendered as ``"10110-10159"``. ``None`` when free or unknown.
    holder: str | None = None
    source: PortSource | None = None
    #: Human-readable explanation, always set for ``taken`` and ``unknown``.
    reason: str | None = None
    #: Configuration warnings gathered while answering (see
    #: :meth:`ReservedRangesStatus.warnings`).
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def available(self) -> bool:
        """``True`` only when the port is *provably* free on every source."""
        return self.state == FREE


def _project_holding_port(db: Session, port: int, project_id: str | None) -> str | None:
    """Return the name of another project occupying *port*, or ``None``."""
    stmt = select(Project.name).where(
        or_(
            Project.backend_port == port,
            Project.frontend_port == port,
            Project.db_port == port,
        )
    )
    if project_id is not None:
        pid = UUID(project_id) if isinstance(project_id, str) else project_id
        stmt = stmt.where(Project.id != pid)
    row = db.execute(stmt).first()
    return row[0] if row is not None else None


def describe_port_availability(
    db: Session,
    port: int,
    project_id: str | None = None,
) -> PortAvailability:
    """Resolve *port* against the ``projects`` table, the reservations AND the host.

    The port is reported ``free`` only when all three sources agree. If the
    host cannot be consulted the verdict is ``unknown`` — see the module
    docstring for why that is not the same as ``free``.

    Parameters
    ----------
    db:
        Active SQLAlchemy session.
    port:
        The port number to check.
    project_id:
        If supplied, that project is excluded from the conflict search, so a
        project editing its own ports does not collide with itself. Note this
        exclusion applies to the cockpit table ONLY: a port the project has
        actually published on the host is still reported as held by the host,
        which is correct — it *is* in use.

    Raises
    ------
    ValueError
        If *port* is outside the configured registry range.
    """
    range_min, range_max = _port_range(db)
    if port < range_min or port > range_max:
        raise ValueError(f"Port {port} is outside the allowed range ({range_min}–{range_max}).")

    reserved = reserved_ranges_status(db)
    warnings = tuple(reserved.warnings)

    # 1. Our own table — the most actionable answer, so it is reported first.
    project_name = _project_holding_port(db, port, project_id)
    if project_name is not None:
        reason = f"Port {port} is already allocated to project {project_name!r}."
        # Enrichment, best-effort: a port claimed by one of OUR projects while a
        # container is also publishing it is an existing double-book — exactly
        # the nex-websites / nex-manager-frontend collision on 10111 that went
        # unnoticed for twelve days. Say so, so the operator can see it. This
        # never changes the verdict and a failing probe never downgrades an
        # answer we already know: the port is taken either way.
        try:
            host_holder = get_host_taken_ports().get(port)
        except HostProbeError:
            host_holder = None
        if host_holder is not None:
            reason += (
                f" It is ALSO published on this host by container {host_holder!r} — "
                f"the port is double-booked between this project and a service outside the cockpit."
            )
        return PortAvailability(
            port=port,
            state=TAKEN,
            holder=project_name,
            source="projects",
            reason=reason,
            warnings=warnings,
        )

    # 2. The host — reality, and the source the cockpit used to be blind to.
    try:
        host_ports = get_host_taken_ports()
    except HostProbeError as exc:
        return PortAvailability(
            port=port,
            state=UNKNOWN,
            source="host",
            reason=(
                f"Availability of port {port} could not be verified against this host: {exc} "
                f"An unverified port is not offered — resolve the host check and retry."
            ),
            warnings=warnings,
        )

    container = host_ports.get(port)
    if container is not None:
        return PortAvailability(
            port=port,
            state=TAKEN,
            holder=container,
            source="host",
            reason=(
                f"Port {port} is already published on this host by container {container!r}, "
                f"even though no project in this cockpit records it."
            ),
            warnings=warnings,
        )

    if _bind_probe_says_taken(port):
        return PortAvailability(
            port=port,
            state=TAKEN,
            holder=None,
            source="probe",
            reason=f"Port {port} is held by a process on this machine (bind probe refused it).",
            warnings=warnings,
        )

    # 3. Operator-declared reservations for stacks we cannot see at all.
    reserved_range = reserved.holder_of(port)
    if reserved_range is not None:
        start, end = reserved_range
        return PortAvailability(
            port=port,
            state=TAKEN,
            holder=f"{start}-{end}",
            source="reserved",
            reason=(
                f"Port {port} falls inside reserved range {start}-{end}, which is set aside "
                f"for a stack managed outside NEX Studio."
            ),
            warnings=warnings,
        )

    return PortAvailability(port=port, state=FREE, warnings=warnings)


def check_port_available(
    db: Session,
    port: int,
    project_id: str | None = None,
) -> bool:
    """Return ``True`` only when *port* is provably free on every source.

    Backwards-compatible boolean face of :func:`describe_port_availability`.
    An ``unknown`` verdict returns ``False``: refusing a port we cannot vouch
    for is recoverable, handing out a port a neighbour is serving on is not.
    Callers that need to tell "taken" from "could not check" — and every
    caller that reports to a human should — must use
    :func:`describe_port_availability`.

    Raises
    ------
    ValueError
        If *port* is outside the configured registry range.
    """
    return describe_port_availability(db, port, project_id).available


def _blocked_ports(db: Session) -> tuple[set[int], ReservedRangesStatus]:
    """Union of every port an allocator must avoid: table + reservations + host.

    Raises
    ------
    HostProbeError
        When the host cannot be consulted. Allocation refuses rather than
        proceeding on a partial picture — the union would silently be missing
        exactly the neighbours it exists to protect against.
    """
    reserved = reserved_ranges_status(db)
    blocked = _get_all_used_ports(db) | reserved.ports | set(get_host_taken_ports())
    return blocked, reserved


def suggest_next_port(db: Session, port_type: str) -> int:
    """Find the first free port in the registry range for *port_type*.

    "Free" means free on all three sources (see the module docstring), not
    merely absent from the ``projects`` table.

    Parameters
    ----------
    db:
        Active SQLAlchemy session.
    port_type:
        One of ``"backend"``, ``"frontend"``, or ``"db"``.

    Returns
    -------
    int
        The lowest available port in the range.

    Raises
    ------
    ValueError
        If *port_type* is invalid or no free port remains.
    HostProbeError
        If the host's published-port map cannot be read. No port is suggested
        in that case — an unverifiable suggestion is how ports get double-booked.
    """
    if port_type not in PORT_TYPES:
        raise ValueError(f"Invalid port type '{port_type}'. Must be one of: {', '.join(PORT_TYPES)}.")

    blocked, _reserved = _blocked_ports(db)
    range_min, range_max = _port_range(db)

    for port in range(range_min, range_max + 1):
        # The bind probe too, not only the three sets. `describe_port_availability` applies it, so
        # without it the suggester would hand out a port its own validator then rejects — and worse,
        # a NON-docker holder (a systemd service on the host, the backend running host-mode in local
        # dev or the CI test job) is invisible to the docker port map and would be silently
        # double-booked. Sources must agree, or "free" means two different things on one screen.
        if port not in blocked and not _bind_probe_says_taken(port):
            return port

    raise ValueError(f"No free ports available in range {range_min}–{range_max}.")


def get_all_allocated_ports(db: Session) -> dict[str, list[int]]:
    """Return all allocated ports grouped by type.

    Returns
    -------
    dict
        Keys: ``"backend"``, ``"frontend"``, ``"db"``.
        Values: sorted list of ports currently in use for that type.
    """
    stmt = select(
        Project.backend_port,
        Project.frontend_port,
        Project.db_port,
    )
    rows = db.execute(stmt).all()

    result: dict[str, list[int]] = {
        "backend": [],
        "frontend": [],
        "db": [],
    }

    for backend_port, frontend_port, db_port in rows:
        if backend_port is not None:
            result["backend"].append(backend_port)
        if frontend_port is not None:
            result["frontend"].append(frontend_port)
        if db_port is not None:
            result["db"].append(db_port)

    for key in result:
        result[key].sort()

    return result


def get_conflict_project_name(
    db: Session,
    port: int,
    project_id: str | None = None,
) -> str | None:
    """Return the name of the project that occupies *port*, or ``None``.

    When *project_id* is given, that project is excluded from the search
    (useful when editing an existing project).

    Covers the ``projects`` table only — a port held by a neighbouring
    container has no project name. Use :func:`describe_port_availability`
    for the full picture including the host.
    """
    return _project_holding_port(db, port, project_id)


def _get_all_used_ports(db: Session) -> set[int]:
    """Collect every port number currently allocated across all projects."""
    stmt = select(
        Project.backend_port,
        Project.frontend_port,
        Project.db_port,
    )
    rows = db.execute(stmt).all()

    used: set[int] = set()
    for backend_port, frontend_port, db_port in rows:
        if backend_port is not None:
            used.add(backend_port)
        if frontend_port is not None:
            used.add(frontend_port)
        if db_port is not None:
            used.add(db_port)
    return used


@dataclass(frozen=True)
class ReservedRangesStatus:
    """Parsed state of the ``reserved_port_ranges`` setting.

    Exists so an *unconfigured* or *mistyped* reservation list stops being
    inert. Previously an empty value meant "no reservations" and a malformed
    entry was skipped in silence — in both cases the operator believed a
    guard existed that was doing nothing at all.
    """

    #: ``False`` when the setting is empty — i.e. NO external reservation is
    #: declared and the host probe is the only thing standing between us and a
    #: neighbour's port.
    configured: bool
    #: Successfully parsed ``(start, end)`` pairs, inclusive.
    ranges: tuple[tuple[int, int], ...] = ()
    #: Entries that could not be parsed, verbatim, so they can be shown back.
    malformed: tuple[str, ...] = ()

    @property
    def ports(self) -> set[int]:
        """Every reserved port, expanded."""
        out: set[int] = set()
        for start, end in self.ranges:
            out.update(range(start, end + 1))
        return out

    def holder_of(self, port: int) -> tuple[int, int] | None:
        """Return the reserved range containing *port*, if any."""
        for start, end in self.ranges:
            if start <= port <= end:
                return (start, end)
        return None

    @property
    def warnings(self) -> list[str]:
        """Operator-facing Slovak warnings for the Settings / project forms.

        Slovak because these strings are rendered to the Manažér verbatim; the
        surrounding registry metadata in ``system_setting.py`` is Slovak too.
        """
        out: list[str] = []
        if not self.configured:
            out.append(
                "Rezervované rozsahy portov nie sú nastavené. Systém preto pozná len porty "
                "z vlastnej evidencie projektov a porty, ktoré práve teraz drží Docker na tomto "
                "stroji. Služba, ktorá je dočasne vypnutá alebo nebeží v Dockeri, sa takto "
                "nezistí — jej rozsah doplň v Nastaveniach (kľúč „reserved_port_ranges“)."
            )
        if self.malformed:
            out.append(
                "Nezrozumiteľné položky v rezervovaných rozsahoch portov: "
                + ", ".join(f"„{entry}“" for entry in self.malformed)
                + ". Tieto rozsahy sa NEUPLATŇUJÚ. Správny tvar je „10110-10159“, "
                "viac rozsahov oddeľ čiarkou."
            )
        return out


#: Guard so the "not configured" notice is logged once per process rather than
#: on every port check — visible in the log without drowning it.
_reserved_warning_logged: set[str] = set()


#: The one registry of record — read, never copied (ICCINT-2). Before it existed the
#: same allocations lived in four places (DECISIONS.md, ICC_STANDARDS.md §5, the orphaned
#: PORTS.md, and the ``reserved_port_ranges`` setting) and none of them was complete: the
#: cockpit offered 10120-10122 to a new project, inside NEX Automat's reserved 10110-10159.
PORT_REGISTRY_FILE = Path("/home/icc/knowledge/infrastructure/port-registry.yaml")


def _ranges_from_registry_file() -> tuple[tuple[tuple[int, int], ...], tuple[str, ...], bool]:
    """Read reserved ranges from the KB registry. Returns ``(ranges, malformed, found)``.

    Only entries the cockpit CANNOT see for itself are returned — every ``bloky`` entry
    whose ``druh`` is not ``kokpit``, plus everything in ``mimo_rozsahu``.

    A cockpit-owned block is deliberately excluded. Reserved ranges are consulted AFTER
    the projects table but they do not know which project is asking, so reserving a
    cockpit block would answer "reserved" when a project re-checks its OWN port — the
    projects-table lookup excludes the asking project, and the reserved check would then
    refuse it. Cockpit projects are already protected by that table; they need no second
    guard, and a second guard here would be actively wrong.
    """
    if not PORT_REGISTRY_FILE.exists():
        return (), (), False
    try:
        doc = yaml.safe_load(PORT_REGISTRY_FILE.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        # Loud: a registry we cannot read is a guard the operator believes is protecting
        # ranges that are in fact wide open. Never downgrade this to "no reservations".
        logger.warning("Port registry %s could not be read: %s", PORT_REGISTRY_FILE, exc)
        return (), (), False

    entries: list[tuple[str, str]] = []
    for block in doc.get("bloky") or []:
        if isinstance(block, dict) and block.get("druh") != "kokpit":
            entries.append((str(block.get("rozsah", "")), str(block.get("vlastník", "?"))))
    for block in doc.get("mimo_rozsahu") or []:
        if isinstance(block, dict):
            entries.append((str(block.get("rozsah", "")), str(block.get("vlastník", "?"))))

    ranges: list[tuple[int, int]] = []
    malformed: list[str] = []
    for spec, owner in entries:
        start_str, sep, end_str = spec.partition("-")
        if not sep:
            malformed.append(f"{owner}: {spec}")
            continue
        try:
            start, end = int(start_str.strip()), int(end_str.strip())
        except ValueError:
            malformed.append(f"{owner}: {spec}")
            continue
        if start > end:
            malformed.append(f"{owner}: {spec}")
            continue
        ranges.append((start, end))
    return tuple(ranges), tuple(malformed), True


def reserved_ranges_status(db: Session) -> ReservedRangesStatus:
    """Reserved ranges, from the KB registry file — the setting is only the fallback.

    :data:`PORT_REGISTRY_FILE` is the source of record. The ``reserved_port_ranges``
    setting remains as a fallback for an instance that cannot see the KB (a detached
    container, a fresh machine), so losing the mount degrades to the old behaviour
    instead of silently declaring "no reservations".

    External reservations (NEX Automat per D-022, per-customer stacks and
    other neighbours managed outside NEX Studio) name the ranges the cockpit
    cannot see for itself. Every allocation path consults this — not just
    the block suggester as before — so a reserved port can no longer be
    handed out by :func:`suggest_next_port` and then rejected at create time.
    """
    file_ranges, file_malformed, found = _ranges_from_registry_file()
    if found and (file_ranges or file_malformed):
        if file_malformed:
            logger.warning(
                "Malformed entries in %s are NOT being enforced: %s",
                PORT_REGISTRY_FILE,
                ", ".join(repr(entry) for entry in file_malformed),
            )
        return ReservedRangesStatus(configured=True, ranges=file_ranges, malformed=file_malformed)

    if found and "empty-file" not in _reserved_warning_logged:
        _reserved_warning_logged.add("empty-file")
        logger.warning(
            "Port registry %s declares no reservations — falling back to the 'reserved_port_ranges' setting.",
            PORT_REGISTRY_FILE,
        )

    raw = (system_setting_service.get_str(db, "reserved_port_ranges") or "").strip()
    if not raw:
        if "unset" not in _reserved_warning_logged:
            _reserved_warning_logged.add("unset")
            logger.warning(
                "system setting 'reserved_port_ranges' is empty — no externally-managed port "
                "reservations are declared; host Docker state is the only non-cockpit guard."
            )
        return ReservedRangesStatus(configured=False)

    ranges: list[tuple[int, int]] = []
    malformed: list[str] = []
    for spec in (s.strip() for s in raw.split(",")):
        if not spec:
            continue
        start_str, sep, end_str = spec.partition("-")
        if not sep:
            malformed.append(spec)
            continue
        try:
            r_start = int(start_str.strip())
            r_end = int(end_str.strip())
        except ValueError:
            malformed.append(spec)
            continue
        if r_start > r_end:
            malformed.append(spec)
            continue
        ranges.append((r_start, r_end))

    if malformed:
        # Loud, every time: a mistyped reservation is a guard the operator
        # believes is protecting a range that is in fact wide open.
        logger.warning(
            "Malformed entries in 'reserved_port_ranges' are NOT being enforced: %s",
            ", ".join(repr(entry) for entry in malformed),
        )

    return ReservedRangesStatus(
        configured=True,
        ranges=tuple(ranges),
        malformed=tuple(malformed),
    )


def _get_reserved_ports(db: Session) -> set[int]:
    """Expand ``reserved_port_ranges`` into a set of ports (see :func:`reserved_ranges_status`)."""
    return reserved_ranges_status(db).ports


def suggest_next_port_block(db: Session, block_size: int | None = None) -> int:
    """Return the base port of the first free ``block_size``-port block.

    A block is considered free when **none** of its ``block_size``
    consecutive ports (``base``, ``base+1``, …, ``base+block_size-1``)
    is currently allocated to any project. Blocks start at the
    configured ``port_range_min`` and advance by ``block_size``.

    Callers are expected to use the first ``block_size`` / number-of-
    ports-in-use slots for actual services (backend, frontend, db —
    see DECISIONS.md D-020) and leave the rest as per-project reserve.

    Parameters
    ----------
    db:
        Active SQLAlchemy session.
    block_size:
        Number of consecutive ports per block. When ``None`` the
        configured ``port_block_size`` system setting is used.

    Returns
    -------
    int
        Base port of the first free block.

    Raises
    ------
    ValueError
        If ``block_size`` is not positive, or if no free block remains
        in the registry range.
    HostProbeError
        If the host's published-port map cannot be read — no block is
        suggested rather than one we cannot vouch for.
    """
    if block_size is None:
        block_size = _port_block_size(db)
    if block_size <= 0:
        raise ValueError(f"block_size must be positive, got {block_size!r}.")

    # DB-allocated ports + externally-reserved ranges + ports the HOST is
    # actually publishing, so the suggested base neither lands inside a range
    # that _validate_ports will reject nor collides with a neighbouring stack
    # this cockpit has no row for.
    blocked, _reserved = _blocked_ports(db)
    range_min, range_max = _port_range(db)

    for base in range(range_min, range_max + 1, block_size):
        block_end = base + block_size - 1
        if block_end > range_max:
            break
        candidate = range(base, base + block_size)
        # Same reasoning as suggest_next_port: the bind probe closes the non-docker gap the port map
        # cannot see, so the suggester and `describe_port_availability` agree on what "free" means.
        if blocked.isdisjoint(candidate) and not any(_bind_probe_says_taken(p) for p in candidate):
            return base

    raise ValueError(f"No free {block_size}-port block in range {range_min}–{range_max}.")


def _drop_block_for(text: str, slug: str) -> str:
    """Remove the ``bloky`` entry owned by *slug*, if there is one.

    Used when a project's ports change: without it the registry would carry two claims for
    the same project and the abandoned block would stay reserved against everyone else.
    Only entries the cockpit owns (``druh: kokpit``) are removed — an externally managed
    block that happens to share a name is not ours to delete.

    Operates on text, like the rest of the writer: the file is a document a human maintains
    and a YAML round-trip would drop every comment in it.
    """
    lines = text.splitlines(keepends=True)
    start: int | None = None
    for i, line in enumerate(lines):
        if re.match(r"^\s*-\s*rozsah:", line):
            start = i
            owned_by_us = False
            is_ours = False
            continue
        if start is not None:
            if re.match(rf"^\s+vlastník:\s*{re.escape(slug)}\s*$", line):
                owned_by_us = True
            elif re.match(r"^\s+druh:\s*kokpit\s*$", line):
                is_ours = True
            # entry ends at the next item, the next top-level key, or a blank line run
            ends = (
                i + 1 >= len(lines)
                or re.match(r"^\s*-\s*rozsah:", lines[i + 1])
                or not lines[i + 1].startswith((" ", "\t"))
            )
            if ends and owned_by_us and is_ours:
                end = i + 1
                # swallow one trailing blank line so removal does not leave a gap
                if end < len(lines) and not lines[end].strip():
                    end += 1
                return "".join(lines[:start] + lines[end:])
            if ends:
                start = None
    return text


def record_allocation(
    *,
    slug: str,
    base: int | None,
    block_size: int,
    backend_port: int | None,
    frontend_port: int | None,
    db_port: int | None,
) -> str | None:
    """Write a freshly allocated block into the KB registry. Returns a warning, or ``None``.

    Closes the loop that ICCINT-2 opened. Reading one registry stops the cockpit handing out
    a neighbour's block; writing back is what stops the registry going stale again. Every
    hole we found on 21.08.2026 — ``nex-manager`` 10210-10219, ``nex-payables`` 10220-10229,
    ``nex-shopify`` inheriting the retired NEX Test block — came from an allocation nobody
    remembered to copy into the knowledge base by hand.

    The file is edited as TEXT, never round-tripped through the YAML dumper. It is a document
    a human maintains: ranges carry comments explaining why a block is 50 ports wide, which
    decision allocated it, and which collision is still open. ``yaml.safe_dump`` would drop
    every one of them and hand back a machine's idea of the same data.

    Best-effort by design — a project that exists must not be rolled back because a file could
    not be written — but NEVER silent: the caller puts the returned string in ``setup_warnings``,
    which the create response shows the Manažér. A best-effort step nobody is told about is the
    defect ICCINT-3 is open on.

    A project without ports records nothing and warns about nothing — ``backend_port`` is
    nullable and a project may legitimately have none. There is no block to write down, so
    silence here is the honest answer rather than a swallowed failure.
    """
    if base is None:
        return None
    end = base + block_size - 1
    entry = (
        f"\n  - rozsah: {base}-{end}\n"
        f"    vlastník: {slug}\n"
        f"    druh: kokpit\n"
        f"    porty: {{backend: {backend_port}, frontend: {frontend_port}, db: {db_port}}}\n"
        f"    pridelené: {date.today().isoformat()}\n"
    )

    try:
        text = PORT_REGISTRY_FILE.read_text(encoding="utf-8")
    except OSError as exc:
        return (
            f"Blok {base}-{end} sa nepodarilo zapísať do evidencie portov "
            f"({PORT_REGISTRY_FILE}): {exc}. Dopíš ho ručne, inak ho ďalší projekt môže dostať tiež."
        )

    if re.search(rf"^\s*-\s*rozsah:\s*{base}-{end}\s*$", text, re.MULTILINE):
        return None  # already recorded — a re-run must not duplicate the entry

    # A project whose ports were CHANGED must not end up owning two blocks. Drop the old
    # entry first, otherwise the registry grows a second claim for the same slug and the
    # abandoned block stays reserved against everyone else forever.
    text = _drop_block_for(text, slug)

    lines = text.splitlines(keepends=True)
    anchor = next((i for i, line in enumerate(lines) if line.startswith("mimo_rozsahu:")), None)
    if anchor is None:
        return (
            f"Blok {base}-{end} sa nepodarilo zapísať do evidencie portov: v súbore chýba sekcia "
            f"„mimo_rozsahu“, podľa ktorej sa hľadá koniec zoznamu blokov. Dopíš blok ručne."
        )
    # Walk back over the blank lines and the comment block that introduce `mimo_rozsahu`,
    # so the new entry lands at the END of `bloky` rather than inside someone's heading.
    while anchor > 0 and (not lines[anchor - 1].strip() or lines[anchor - 1].lstrip().startswith("#")):
        anchor -= 1

    updated = "".join(lines[:anchor]) + entry + "".join(lines[anchor:])
    updated = re.sub(r"^(\s*next_free:\s*)\d+", rf"\g<1>{base + block_size}", updated, count=1, flags=re.MULTILINE)
    updated = re.sub(
        r"^(aktualizované:\s*).*$", rf"\g<1>{date.today().isoformat()}", updated, count=1, flags=re.MULTILINE
    )

    try:
        # Write via a sibling temp file + rename: a half-written registry would read as
        # "no reservations" on the next allocation, which is the failure we are removing.
        tmp = PORT_REGISTRY_FILE.with_suffix(".yaml.tmp")
        tmp.write_text(updated, encoding="utf-8")
        tmp.replace(PORT_REGISTRY_FILE)
    except OSError as exc:
        return (
            f"Blok {base}-{end} sa nepodarilo zapísať do evidencie portov "
            f"({PORT_REGISTRY_FILE}): {exc}. Dopíš ho ručne, inak ho ďalší projekt môže dostať tiež."
        )

    logger.info("Port block %s-%s recorded in %s for %s", base, end, PORT_REGISTRY_FILE, slug)
    return None
