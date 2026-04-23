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
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from backend.db.models.projects import Project

# ICC Port Registry v2 — commercial projects band (DECISIONS.md D-020).
PORT_RANGE_MIN = 10100
PORT_RANGE_MAX = 14999
PORT_TYPES = ("backend", "frontend", "db")

# Size of a per-project port block (DECISIONS.md D-020 layout: +0 backend,
# +1 frontend, +2 postgres, +3 UI design preview, +4–9 reserve).
PORT_BLOCK_SIZE = 10


def check_port_available(
    db: Session,
    port: int,
    project_id: str | None = None,
) -> bool:
    """Check whether *port* is free (not allocated to any other project).

    Parameters
    ----------
    db:
        Active SQLAlchemy session.
    port:
        The port number to check.
    project_id:
        If supplied, the port is considered "available" when it is only
        allocated to this project (i.e. editing an existing project).

    Returns
    -------
    bool
        ``True`` when the port is not used by another project.

    Raises
    ------
    ValueError
        If *port* is outside the allowed 9100–9299 range.
    """
    if port < PORT_RANGE_MIN or port > PORT_RANGE_MAX:
        raise ValueError(f"Port {port} is outside the allowed range ({PORT_RANGE_MIN}–{PORT_RANGE_MAX}).")

    stmt = select(Project.id).where(
        or_(
            Project.backend_port == port,
            Project.frontend_port == port,
            Project.db_port == port,
        )
    )

    if project_id is not None:
        stmt = stmt.where(Project.id != UUID(project_id) if isinstance(project_id, str) else Project.id != project_id)

    result = db.execute(stmt).first()
    return result is None


def suggest_next_port(db: Session, port_type: str) -> int:
    """Find the first free port in the 9100–9299 range for *port_type*.

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
    """
    if port_type not in PORT_TYPES:
        raise ValueError(f"Invalid port type '{port_type}'. Must be one of: {', '.join(PORT_TYPES)}.")

    allocated = _get_all_used_ports(db)

    for port in range(PORT_RANGE_MIN, PORT_RANGE_MAX + 1):
        if port not in allocated:
            return port

    raise ValueError(f"No free ports available in range {PORT_RANGE_MIN}–{PORT_RANGE_MAX}.")


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
    """
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


def suggest_next_port_block(
    db: Session, block_size: int = PORT_BLOCK_SIZE
) -> int:
    """Return the base port of the first free ``block_size``-port block.

    A block is considered free when **none** of its ``block_size``
    consecutive ports (``base``, ``base+1``, …, ``base+block_size-1``)
    is currently allocated to any project. Blocks start at
    :data:`PORT_RANGE_MIN` and advance by ``block_size`` (so the first
    four blocks are 10100, 10110, 10120, 10130 when ``block_size=10``).

    Callers are expected to use the first :data:`PORT_BLOCK_SIZE` /
    number-of-ports-in-use slots for actual services (backend,
    frontend, db, ui_design — see DECISIONS.md D-020) and leave the
    rest as per-project reserve.

    Parameters
    ----------
    db:
        Active SQLAlchemy session.
    block_size:
        Number of consecutive ports per block. Defaults to
        :data:`PORT_BLOCK_SIZE`.

    Returns
    -------
    int
        Base port of the first free block.

    Raises
    ------
    ValueError
        If ``block_size`` is not positive, or if no free block remains
        in the registry range.
    """
    if block_size <= 0:
        raise ValueError(f"block_size must be positive, got {block_size!r}.")

    used = _get_all_used_ports(db)

    for base in range(PORT_RANGE_MIN, PORT_RANGE_MAX + 1, block_size):
        block_end = base + block_size - 1
        if block_end > PORT_RANGE_MAX:
            break
        if used.isdisjoint(range(base, base + block_size)):
            return base

    raise ValueError(
        f"No free {block_size}-port block in range "
        f"{PORT_RANGE_MIN}–{PORT_RANGE_MAX}."
    )
