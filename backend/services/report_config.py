"""Service layer for :class:`~backend.db.models.reports.ReportConfig`.

Provides the synchronous CRUD surface used by API routers. All methods
accept ``db: Session`` as the first argument and only ever call
``session.flush()`` — transaction commit is the router's responsibility.
Errors are signalled via :class:`ValueError` so the router can translate
them to the appropriate HTTP status code.

Design notes (per DESIGN.md §1.9 Reporting Configuration / §1.23
ReportConfig, §6.5 reporting pipeline, business rule R-01 and
:mod:`backend.db.models.reports.ReportConfig`):

    * ``id``, ``created_at`` and ``updated_at`` are server-managed and
      therefore immutable from the service layer (``updated_at`` is
      auto-stamped by the ORM via ``onupdate=func.now()`` on flush).
    * ``project_id`` is the row identity — ``UNIQUE(project_id)`` —
      and must not be rewritten after the fact (DESIGN.md §1.9: "one
      config per project"). :class:`ReportConfigUpdate` deliberately
      omits it; the service enforces that contract defensively with an
      ``allowed_fields`` allow-list.
    * Both hourly rate columns carry DB-level server defaults
      (``senior_hourly_rate_eur = 75.0000``, ``junior_hourly_rate_eur =
      35.0000``). The Pydantic schema mirrors those defaults so callers
      may omit them; :func:`create` only forwards the values it
      received and the DB / schema layer fills in the rest.
    * Unique constraint on ``project_id`` is enforced both at the DB
      layer (``uq_report_configs_project_id``) and pre-emptively by
      :func:`create`, so callers receive a clean :class:`ValueError`
      (HTTP 409 at the router layer) instead of a raw
      :class:`~sqlalchemy.exc.IntegrityError` coming out of ``flush``.
    * ``report_configs`` has **no** inbound foreign keys — no other
      table references it — so :func:`delete` performs no dependency
      RESTRICT check and is a straightforward hard-delete. The outbound
      FK ``project_id`` (``ON DELETE CASCADE``) keeps the row
      self-consistent when the parent project is deleted; deleting the
      configuration itself is the explicit inverse, used to reset the
      rate model to defaults (a fresh row with the schema/DB defaults
      can be inserted via :func:`create` afterwards).
    * List filters (``project_id``) match the unique-indexed column
      and support the reporting / settings UI — "load this project's
      report configuration". Since ``project_id`` is unique, filtering
      by it returns at most one row; the :func:`list_report_configs`
      surface still returns a list to match the rest of the service
      layer (and to support unfiltered admin listings).
    * List ordering is ``created_at DESC`` so the most recently
      created configuration appears first, matching the rest of the
      service layer.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.reports import ReportConfig
from backend.schemas.report_config import (
    ReportConfigCreate,
    ReportConfigUpdate,
)


def list_report_configs(
    db: Session,
    *,
    project_id: Optional[UUID] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[ReportConfig]:
    """Return report configurations filtered by the supplied criteria.

    Results are ordered by ``created_at DESC`` so the most recently
    created configuration appears first, matching the rest of the
    service layer.

    Args:
        db: Active SQLAlchemy session.
        project_id: Optional project filter — restrict to the
            configuration belonging to a specific project. Since
            ``project_id`` is unique, this returns at most one row.
        limit: Maximum number of rows to return.
        offset: Number of rows to skip.

    Returns:
        List of :class:`ReportConfig` instances.
    """
    stmt = select(ReportConfig)
    if project_id is not None:
        stmt = stmt.where(ReportConfig.project_id == project_id)
    stmt = stmt.order_by(ReportConfig.created_at.desc()).limit(limit).offset(offset)
    return list(db.execute(stmt).scalars().all())


def get_by_id(db: Session, config_id: UUID) -> ReportConfig:
    """Return a single report configuration by primary key.

    Raises:
        ValueError: If no configuration with the supplied ``config_id``
            exists. The router converts this to an HTTP 404 response.
    """
    cfg = db.get(ReportConfig, config_id)
    if cfg is None:
        raise ValueError(f"ReportConfig {config_id} not found")
    return cfg


def _get_by_project_id(db: Session, project_id: UUID) -> Optional[ReportConfig]:
    """Internal helper — look up a row by its ``project_id`` identity."""
    stmt = select(ReportConfig).where(ReportConfig.project_id == project_id)
    return db.execute(stmt).scalar_one_or_none()


def create(
    db: Session,
    data: ReportConfigCreate,
) -> ReportConfig:
    """Create a new report configuration row.

    ``senior_hourly_rate_eur`` and ``junior_hourly_rate_eur`` default to
    the values set by the Pydantic schema / DB ``server_default`` when
    omitted (``75.0000`` / ``35.0000``), matching the model declaration.

    Validates the ``UNIQUE(project_id)`` constraint before insertion so
    the caller receives a clean :class:`ValueError` (HTTP 409 at the
    router layer) instead of a raw
    :class:`~sqlalchemy.exc.IntegrityError` coming out of ``flush``. If
    the supplied ``project_id`` does not match an existing project the
    DB-level FK rejects the flush and the error propagates as-is
    (routed at the API layer as a 409/422).

    Args:
        db: Active SQLAlchemy session.
        data: Validated creation payload.

    Returns:
        The newly created and flushed :class:`ReportConfig` with its
        server-generated ``id``, ``created_at`` and ``updated_at``
        populated.

    Raises:
        ValueError: If a configuration for the supplied ``project_id``
            already exists.
    """
    if _get_by_project_id(db, data.project_id) is not None:
        raise ValueError(f"ReportConfig for project_id={data.project_id} already exists")

    cfg = ReportConfig(
        project_id=data.project_id,
        senior_hourly_rate_eur=data.senior_hourly_rate_eur,
        junior_hourly_rate_eur=data.junior_hourly_rate_eur,
    )
    db.add(cfg)
    db.flush()
    return cfg


def update(
    db: Session,
    config_id: UUID,
    data: ReportConfigUpdate,
) -> ReportConfig:
    """Partially update a report configuration.

    Only ``senior_hourly_rate_eur`` and ``junior_hourly_rate_eur`` may
    be changed. ``id``, ``project_id``, ``created_at`` and
    ``updated_at`` are immutable — the row identity (the project it
    configures) must not be rewritten after the fact, and ``updated_at``
    is auto-stamped by the ORM on flush via ``onupdate=func.now()``.

    Fields that are ``None`` in the payload are treated as "leave
    unchanged" to support PATCH semantics — adjusting the cost-model
    inputs is the sole legitimate mutation on this row.

    Raises:
        ValueError: If the configuration does not exist.
    """
    cfg = get_by_id(db, config_id)

    update_data = data.model_dump(exclude_unset=True)
    # Defensive guard — the schema already excludes immutable fields,
    # but silently dropping any that slip through keeps the service
    # honest.
    allowed_fields = {"senior_hourly_rate_eur", "junior_hourly_rate_eur"}

    for field, value in update_data.items():
        if field in allowed_fields and value is not None:
            setattr(cfg, field, value)

    db.flush()
    return cfg


def delete(db: Session, config_id: UUID) -> None:
    """Hard-delete a report configuration.

    ``report_configs`` has no inbound foreign keys — no other table
    references it — so no dependency RESTRICT check is required. The
    outbound FK ``project_id`` (``ON DELETE CASCADE``) keeps the row
    self-consistent when the parent project is deleted; deleting the
    configuration itself is the explicit inverse, used to reset the
    rate model to defaults (a fresh row with the schema/DB defaults can
    be inserted via :func:`create` afterwards).

    Raises:
        ValueError: If the configuration does not exist.
    """
    cfg = get_by_id(db, config_id)
    db.delete(cfg)
    db.flush()
