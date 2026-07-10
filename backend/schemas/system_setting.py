"""Pydantic schemas for system_settings (ICC-wide runtime config).

Mirrors :mod:`backend.db.models.system_settings.SystemSetting`. Keys
are short snake_case identifiers (``github_org``, …), values are
free-form text — keep richer types in a dedicated column when they
appear rather than squeezing them through serialisation here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Mirrors ``ck_system_settings_value_type`` (migration 034).
SystemSettingValueType = Literal["string", "int", "float", "bool"]


class SystemSettingRead(BaseModel):
    """Serialised representation of a single system_settings row.

    ``value`` carries the effective value — either a stored override
    or the service-layer default when the DB has no row yet.
    """

    model_config = ConfigDict(from_attributes=True)

    key: str = Field(..., max_length=100)
    value: str
    label: str = Field(
        default="",
        description=("Human Slovak name shown as the setting's title — registry metadata, never stored per-row."),
    )
    unit: str = Field(
        default="",
        description=(
            "Optional unit suffix hint rendered after the editor (e.g. sekúnd, € / hod) — "
            "registry metadata, may be empty."
        ),
    )
    value_type: SystemSettingValueType = Field(
        default="string",
        description=(
            "Runtime type of ``value``. Service helpers cast against "
            "this hint; the UI uses it for type-aware input widgets."
        ),
    )
    description: Optional[str] = None
    updated_at: Optional[datetime] = None
    updated_by: Optional[UUID] = None
    updated_by_username: Optional[str] = Field(
        default=None,
        description=(
            "Username of the user who last edited — resolved via join. NULL when the value is a service-layer default."
        ),
    )
    is_default: bool = Field(
        default=False,
        description=(
            "True when the value is the service-layer default — no row exists in the system_settings table yet."
        ),
    )


class SystemSettingUpdate(BaseModel):
    """Payload for PATCH /system-settings/{key}.

    Only the value is mutable from the UI — ``key`` is the identity
    column, and ``description`` / ``updated_at`` / ``updated_by`` are
    server-managed.
    """

    value: str = Field(..., min_length=1)
