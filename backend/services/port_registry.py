"""Port Registry Management Service.

Provides port availability checking, next-port suggestion, and
allocated-port querying across all projects in the 9100–9299 range.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from backend.db.models.projects import Project

# ICC Port Registry valid range
PORT_RANGE_MIN = 9100
PORT_RANGE_MAX = 9299
PORT_TYPES = ("backend", "frontend", "db")


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
