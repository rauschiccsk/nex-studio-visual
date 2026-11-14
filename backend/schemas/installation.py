"""DEV-378 — the „Inštalácie“ page: every installation the cockpit deployed, by customer."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel


class InstallationLink(BaseModel):
    """One frontend of a Web project's installation (DEV-164) — its name and address."""

    label: str
    url: str


class InstallationEnvironment(BaseModel):
    """A customer's installation of one app in one environment."""

    version: str
    deployed_at: Optional[datetime] = None
    url: str
    #: Empty for an app with one frontend — its one address is ``url``.
    links: list[InstallationLink] = []
    #: The newest attempt failed — the version is the last good one, not what was asked for.
    last_attempt_failed: bool = False


class InstallationApp(BaseModel):
    customer_id: UUID
    project_slug: str
    project_name: str
    uat: Optional[InstallationEnvironment] = None
    prod: Optional[InstallationEnvironment] = None


class InstallationCustomer(BaseModel):
    key: str
    name: str
    apps: list[InstallationApp]


class InstallationStatus(BaseModel):
    """Whether an installation lives: ``up`` (says it is ok), ``answers`` (the app answers, reports no state),
    ``down`` (nothing of the app answers — ``detail`` says why)."""

    state: Literal["up", "answers", "down"]
    version: Optional[str] = None
    detail: str = ""
