"""Pydantic schemas for ReportConfig domain objects.

Mirrors :mod:`backend.db.models.reports.ReportConfig`.  Field names,
defaults and nullability match the SQLAlchemy model exactly so that
``ReportConfigRead.model_validate(orm_instance)`` round-trips cleanly.

A report configuration stores the per-project senior/junior hourly
rates (EUR) used by the reporting pipeline to convert AI/human time
expenditure into monetary human-cost estimates (DESIGN.md §1.9 and
§6.5, business rule R-01).  The combination ``(project_id)`` is
uniquely constrained (``uq_report_configs_project_id``) — exactly one
configuration row per project.

Rate columns are stored as ``DECIMAL(10, 4)`` at the database layer,
so the schema exposes them via :func:`pydantic.condecimal` with the
same precision to keep rounding semantics identical to the ORM.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, condecimal

# Mirrors `senior_hourly_rate_eur DECIMAL(10, 4)` and
# `junior_hourly_rate_eur DECIMAL(10, 4)` on the ``report_configs`` table.
ReportConfigHourlyRate = condecimal(max_digits=10, decimal_places=4)


class ReportConfigCreate(BaseModel):
    """Payload for creating a new report configuration.

    ``id``, ``created_at`` and ``updated_at`` are server-generated and
    therefore excluded.  Both hourly rates carry DB-level server defaults
    (``75.0000`` / ``35.0000``) so callers may omit them — the default
    values on this schema match the ``server_default`` values on the ORM
    columns so that validation-only round-trips produce identical rows.
    """

    project_id: UUID = Field(
        ...,
        description=(
            "Project the report configuration belongs to.  Unique across ``report_configs`` — one config per project."
        ),
    )
    senior_hourly_rate_eur: ReportConfigHourlyRate = Field(  # type: ignore[valid-type]
        default=Decimal("75.0000"),
        description="Senior developer hourly rate in EUR (default: 75.0000).",
    )
    junior_hourly_rate_eur: ReportConfigHourlyRate = Field(  # type: ignore[valid-type]
        default=Decimal("35.0000"),
        description="Junior developer hourly rate in EUR (default: 35.0000).",
    )


class ReportConfigUpdate(BaseModel):
    """Partial update for an existing report configuration.

    ``id``, ``project_id``, ``created_at`` and ``updated_at`` are
    immutable: the row identity (the project it configures) must not be
    rewritten after the fact, and ``updated_at`` is managed by the ORM
    via ``onupdate=func.now()``.  Only the hourly rate columns remain
    mutable — adjusting cost-model inputs is the sole legitimate
    mutation on this row.  All fields are optional to support PATCH-
    style semantics.
    """

    senior_hourly_rate_eur: Optional[ReportConfigHourlyRate] = Field(  # type: ignore[valid-type]
        default=None,
        description="Updated senior developer hourly rate in EUR.",
    )
    junior_hourly_rate_eur: Optional[ReportConfigHourlyRate] = Field(  # type: ignore[valid-type]
        default=None,
        description="Updated junior developer hourly rate in EUR.",
    )


class ReportConfigRead(BaseModel):
    """Serialised representation of a report configuration row.

    Mirrors every column on
    :class:`backend.db.models.reports.ReportConfig`.
    ``from_attributes=True`` enables construction directly from an ORM
    instance via ``ReportConfigRead.model_validate(obj)``.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    senior_hourly_rate_eur: Decimal
    junior_hourly_rate_eur: Decimal
    created_at: datetime
    updated_at: datetime
