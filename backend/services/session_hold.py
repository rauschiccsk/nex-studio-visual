"""Does a build keep the session alive? (ICCINT-162)

Director, 03.10.2026: *„pokiaľ agent aktívne pracuje, treba predlžovať."* Programovanie runs for days and
the Manažér mostly watches it, so "touched the cockpit recently" is the wrong test of presence while a
build is alive. The open cockpit tab asks this module whether, since its current token was issued, anything
happened on a build the user can see:

  * an agent is working on it right now (``pipeline_state.status == 'agent_working'``), or
  * its conversation got a message after ``since`` — the agent reported, the system noticed a lost turn,
    somebody answered.

The second clause is what makes the rule symmetric with the Manažér's own activity: when the agent stops and
waits for him, the 8 hours count from the agent's last word, not from his last click.

Visibility is the projects list's rule (:mod:`backend.core.authz`): the creator sees his own projects and the
single ``admin`` account sees all. A build the user cannot see cannot keep him logged in.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core import authz
from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version


def _visible(stmt, user: User):
    """Narrow a statement already joined to :class:`Project` to the projects ``user`` can see."""
    if authz.is_admin(user):
        return stmt
    return stmt.where(Project.created_by == user.id)


def session_hold(db: Session, user: User, since: datetime) -> tuple[bool, str]:
    """Return ``(hold, reason)`` — whether a visible build was alive at any moment since ``since``."""
    working = db.execute(
        _visible(
            select(Project.slug, Version.version_number)
            .select_from(PipelineState)
            .join(Version, Version.id == PipelineState.version_id)
            .join(Project, Project.id == Version.project_id)
            .where(PipelineState.status == "agent_working"),
            user,
        ).limit(1)
    ).first()
    if working is not None:
        return True, f"AI Agent pracuje na {working.slug} v{working.version_number}."

    spoke = db.execute(
        _visible(
            select(Project.slug, Version.version_number)
            .select_from(PipelineMessage)
            .join(Version, Version.id == PipelineMessage.version_id)
            .join(Project, Project.id == Version.project_id)
            .where(PipelineMessage.created_at > since),
            user,
        ).limit(1)
    ).first()
    if spoke is not None:
        return True, f"Na {spoke.slug} v{spoke.version_number} sa od posledného predĺženia niečo stalo."

    return False, "Na žiadnej stavbe, ktorú vidíš, sa od posledného predĺženia nič nestalo."
