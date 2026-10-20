"""DEV-160 — the frontends of a project, in the one place every phase of the cockpit reads them from.

Everywhere in the cockpit a project was one backend and one frontend, while the project type „web“ (CR-V2-005, design
§4.2: backend + administration + a public site) was only a plan written into ``.nex-archetype.json`` that nothing read.
Director 20.10.2026: „Do web FE bude sa prihlasovať pomocou emailu a hesla, a do admin FE len meno a heslo. Ideme
kokpit naučiť stavať projekt s dvoma frontendmi.“

A project's type is a preset of its frontends, so they are derived, never stored twice — the type, the project's login
and its ports decide them:

* **Štandardný** — one frontend: ``frontend/``, service ``frontend``, the project's address and the project's login.
* **Web** — the administration in ``frontend/`` (service ``frontend``, address ``…-admin``, name + password) and the
  public web in ``web/`` (service ``web``, the project's main address, e-mail + password), both on the one backend.

Until every phase knows the second frontend (DEV-160 … DEV-164) the Manažér can neither found a Web project nor switch
one to Web — :data:`WEB_PROJECTS_AVAILABLE`, which DEV-164 opens. Director 20.10.2026 (kroky po jednom): „Súhlasím“.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from backend.db.models.project_type_change import ProjectTypeChange
from backend.db.models.projects import Project
from backend.services import port_registry

STANDARD = "standard"
WEB = "web"

#: How a person logs into a frontend.
USERNAME = "username"
EMAIL = "email"
TOKEN = "token"

#: DEV-164 opens Web projects to the Manažér; until then every phase does not know the second frontend yet.
WEB_PROJECTS_AVAILABLE = False
WEB_NOT_YET = (
    "Projekt s webom a administráciou kokpit ešte nevie postaviť ani nasadiť — učí sa to po krokoch a voľba Web "
    "sa sprístupní, keď bude hotový posledný z nich."
)

#: The web's port is this slot of the project's port block (backend = +0, frontend = +1, db = +2). The project
#: template reserves +3 … +5 and uses none of them; +5 is the slot it names for a second web surface.
WEB_PORT_OFFSET = 5


@dataclass(frozen=True)
class Surface:
    """One frontend of a project."""

    #: ``app`` (Štandardný), ``admin`` or ``web`` (Web).
    name: str
    #: What the Manažér reads.
    label: str
    #: Relative to the project's root.
    directory: str
    #: The service in the project's ``docker-compose.yml``.
    service: str
    port: Optional[int]
    #: :data:`USERNAME`, :data:`EMAIL` or :data:`TOKEN`.
    login: str
    #: Appended to the installation's name for this frontend's address; ``""`` = the installation's main address.
    address_suffix: str


def surfaces_of(project: Project) -> tuple[Surface, ...]:
    """The frontends of *project*, derived from its type, login and ports."""
    return _surfaces(project.type, project.auth_mode, project.frontend_port, project.web_port)


def _surfaces(
    project_type: str, auth_mode: str, frontend_port: Optional[int], web_port: Optional[int]
) -> tuple[Surface, ...]:
    if project_type == WEB:
        return (
            Surface(
                name="admin",
                label="Administrácia",
                directory="frontend",
                service="frontend",
                port=frontend_port,
                login=USERNAME,
                address_suffix="-admin",
            ),
            Surface(
                name="web",
                label="Web",
                directory="web",
                service="web",
                port=web_port,
                login=EMAIL,
                address_suffix="",
            ),
        )
    return (
        Surface(
            name="app",
            label="Aplikácia",
            directory="frontend",
            service="frontend",
            port=frontend_port,
            login=TOKEN if auth_mode == TOKEN else USERNAME,
            address_suffix="",
        ),
    )


@dataclass(frozen=True)
class WebSwitch:
    """What switching a project to Web would do — or why it cannot."""

    refusal: Optional[str]
    web_port: Optional[int]
    surfaces: tuple[Surface, ...]

    @property
    def allowed(self) -> bool:
        return self.refusal is None


def web_switch(db: Session, project: Project) -> WebSwitch:
    """The preview of switching *project* to Web: the frontends it would have, or the one reason it cannot.

    Asks the port registry about the web's port (the cockpit's table, the reservations and the host) — a port that
    cannot be vouched for is a refusal, not a guess."""

    def refused(reason: str) -> WebSwitch:
        return WebSwitch(refusal=reason, web_port=None, surfaces=surfaces_of(project))

    if not WEB_PROJECTS_AVAILABLE:
        return refused(WEB_NOT_YET)
    if project.type == WEB:
        return refused("Projekt už je Web — má administráciu aj web.")
    if project.auth_mode == TOKEN:
        return refused(
            "Projekt sa prihlasuje tokenom. Web projekt sa prihlasuje heslom — administrácia menom, web e-mailom."
        )
    if project.backend_port is None:
        return refused("Projekt nemá pridelený blok portov, z ktorého by web dostal port.")

    port = project.backend_port + WEB_PORT_OFFSET
    try:
        verdict = port_registry.describe_port_availability(
            db, port, str(project.id), for_project=(project.slug, project.name)
        )
    except ValueError:
        return refused(f"Port {port} pre web je mimo rozsahu portov projektov.")
    if verdict.state == port_registry.UNKNOWN:
        return refused(f"Či je port {port} pre web voľný, sa teraz nedá overiť — skús to o chvíľu.")
    if not verdict.available:
        return refused(f"Port {port} pre web je obsadený ({verdict.owner or verdict.holder}).")

    return WebSwitch(
        refusal=None, web_port=port, surfaces=_surfaces(WEB, project.auth_mode, project.frontend_port, port)
    )


def switch_to_web(db: Session, project: Project, *, changed_by: uuid.UUID) -> WebSwitch:
    """Switch *project* to Web and keep the change. Raises ``ValueError`` with the reason when it cannot."""
    preview = web_switch(db, project)
    if not preview.allowed:
        raise ValueError(preview.refusal)
    db.add(
        ProjectTypeChange(
            project_id=project.id,
            previous_type=project.type,
            type=WEB,
            web_port=preview.web_port,
            changed_by=changed_by,
        )
    )
    project.type = WEB
    project.web_port = preview.web_port
    db.flush()
    return preview
