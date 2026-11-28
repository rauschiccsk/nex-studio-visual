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
    """The frontends the project has, and what adding the web frontend gives it — or the one reason it cannot."""

    allowed: bool
    refusal: Optional[str] = None
    web_port: Optional[int] = None
    #: DEV-522: the frontends the project HAS.
    surfaces: list[SurfaceRead]
    #: DEV-522: the frontends it would have after adding the web — empty when it cannot.
    after: list[SurfaceRead] = []
    #: DEV-524: what is unsaved when that is the refusal (the answer of ``/git-status``); ``None`` otherwise.
    unsaved_work: Optional[dict] = None
    #: DEV-161: after a switch — what finished only partly in the project's workspace (Slovak, for the Manažér).
    warnings: list[str] = []


class WebAddRequest(BaseModel):
    """DEV-522: adding the web frontend needs the reason — it is kept with the change."""

    reason: Optional[str] = None


class GuideSwitchRead(BaseModel):
    """DEV-536 — Sprievodca of the project's app: whether it is on and whether it can be switched now."""

    #: The project is on the app template — only there the app has Sprievodca.
    applies: bool
    on: bool
    #: Why it cannot be switched now; ``None`` = it can.
    refusal: Optional[str] = None
    #: The release of nex-app-kit the project builds with, and the one Sprievodca needs.
    kit_version: Optional[str] = None
    needed: str
    #: After a switch — what finished only partly (a push that did not go through).
    warnings: list[str] = []


class GuideSwitchRequest(BaseModel):
    """DEV-536 — turn Sprievodca on or off."""

    on: bool
