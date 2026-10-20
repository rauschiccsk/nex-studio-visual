"""DEV-160 — the frontends of a project, and the preview of switching it to Web."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class SurfaceRead(BaseModel):
    """One frontend of a project (``backend.services.project_surfaces.Surface``)."""

    name: str
    label: str
    directory: str
    service: str
    port: Optional[int] = None
    #: ``username`` | ``email`` | ``token``.
    login: str
    #: Appended to the installation's name for this frontend's address; ``""`` = the main address.
    address_suffix: str


class WebSwitchRead(BaseModel):
    """What switching the project to Web gives it — or the one reason it cannot (``refusal``)."""

    allowed: bool
    refusal: Optional[str] = None
    web_port: Optional[int] = None
    surfaces: list[SurfaceRead]
