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
    #: DEV-391 — how „Otvoriť“ opens it: ``link`` its address; ``uat_launch`` a token app in UAT, signed in through
    #: ``POST /customers/{customer_id}/uat-launch``; ``manager`` a token app in PROD, from the customer's NEX Manager.
    opens: Literal["link", "uat_launch", "manager"] = "link"
    #: For ``manager``: the PROD address of the customer's NEX Manager; ``None`` when the cockpit deployed none.
    manager_url: Optional[str] = None
    #: DEV-404 — the machine it was deployed to (its label); ``None``: the deploy recorded none (before 18.11.2026).
    machine: Optional[str] = None
    #: The customer's machine of today when the installation was deployed elsewhere — nothing is deployed there.
    planned_machine: Optional[str] = None


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
