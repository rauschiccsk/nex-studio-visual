"""A project's tickets in Plane — which module they live in and who founded them through the cockpit (DEV-117).

The gateway (:mod:`plane_gateway`) knows Plane; this module knows the cockpit: a project's tickets live in the DEV
register under the module of its application, remembered by id on the project (a renamed project keeps its tickets),
and every ticket says who founded it through the cockpit and from where — the cockpit's account is the author in
Plane, so without that sentence nobody would know which person asked.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.services.plane_gateway import PlaneGateway, TicketRef

#: Where a ticket came from — said in its text, so the register shows which door of the cockpit founded it.
SOURCES = {
    "poradca": "z rozhovoru s Poradcom",
    "zasobnik": "zo Zásobníka",
    "plane_tikety": "zo záložky Plane tikety",
    "kokpit": "z kokpitu",
}


def person(user: Optional[User]) -> str:
    """The name a person goes by in the register — first and last name, else the user name."""
    if user is None:
        return "neznámy používateľ"
    full = " ".join(part for part in (user.first_name, user.last_name) if part)
    return full or user.username


def module_for(db: Session, project: Project, gateway: PlaneGateway) -> dict:
    """The project's module — by the id it remembers, else by the application's name (created when missing)."""
    if project.plane_module_id:
        kept = [m for m in gateway.modules() if m.get("id") == project.plane_module_id]
        if kept:
            return kept[0]
    module = gateway.ensure_module(project.name)
    project.plane_module_id = module["id"]
    db.flush()
    return module


def file_ticket(
    db: Session,
    gateway: PlaneGateway,
    *,
    project: Project,
    user: Optional[User],
    source: str,
    title: str,
    description: str,
    labels: tuple[str, ...],
    doer: str,
    module_name: Optional[str] = None,
) -> TicketRef:
    """Found a ticket for ``project`` (or in ``module_name`` — NEX Studio Visual for a problem of the cockpit itself).

    ``doer`` goes to the ``Robí:`` line the Dedos' gate reads (who works on it now). The text ends with who founded
    the ticket through the cockpit and from where.
    """
    module = gateway.ensure_module(module_name) if module_name else module_for(db, project, gateway)
    text = (
        f"Robí: {doer}\n\n{description.strip()}\n\n"
        f"Založil: {person(user)} cez NEX Studio Visual ({SOURCES.get(source, source)}), projekt {project.name}."
    )
    return gateway.create_ticket(title=title, description=text, module=module, labels=labels)
