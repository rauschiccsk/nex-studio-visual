"""„Kde stojíme“ — where every project's development stands, from the cockpit's own records (DEV-150).

Director 20.10.2026: „V NSV v záložke prehľad by som privítal informciu o tom, ako stojíme s daným projektom. Aby v
prípade, že vývoj projektu z nejakej príčiny nedokončíme (príde niečo dôležitejšie) mali by sme presnú informáciu o
tom, kde a ako pokračovať v projekte.“ Until then the Prehľad showed a project's name and nothing of its state —
NEX Manager 1.3.0 had waited on a question of the agent since 15.10. and only its own version page said so.

Only facts the cockpit records — the build's phase and state, since when it waits on the Manažér, the task plan, the
deploys. Nothing the agent wrote (its question stays in Riadiace centrum): no tokens, nothing invented. The screen
words the facts with the labels Riadiace centrum uses.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.core import authz
from backend.db.models.deploy import DeployEvent
from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineState
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version
from backend.schemas.standing import OpenVersionStanding, ProjectStanding

#: Waiting on a person first, then the agent at work, then a build the Manažér paused.
_ORDER = {"awaiting_manazer": 0, "blocked": 0, "agent_working": 1, "paused": 2}
_NEVER = datetime.max.replace(tzinfo=timezone.utc)


def _open_versions(db: Session, project_id) -> list[OpenVersionStanding]:
    rows = db.execute(
        select(Version, PipelineState)
        .join(PipelineState, PipelineState.version_id == Version.id)
        .where(Version.project_id == project_id, PipelineState.status != "done")
        .order_by(Version.created_at.desc())
    ).all()
    out = []
    for version, state in rows:
        done, total = db.execute(
            select(func.count(Task.id).filter(Task.status == "done"), func.count(Task.id))
            .join(Feat, Feat.id == Task.feat_id)
            .join(Epic, Epic.id == Feat.epic_id)
            .where(Epic.version_id == version.id)
        ).one()
        out.append(
            OpenVersionStanding(
                version_id=version.id,
                version_number=version.version_number,
                stage=state.current_stage,
                status=state.status,
                # The model clears both the moment the build stops waiting (``_stamp_awaiting_director_since``).
                block_reason=state.block_reason,
                waiting_since=state.awaiting_director_since,
                tasks_done=done,
                tasks_total=total,
                fast_fix=state.flow_type == "fast_fix",
            )
        )
    return out


def _prod_version(db: Session, project_id) -> Optional[str]:
    return db.execute(
        select(DeployEvent.version_number)
        .where(
            DeployEvent.project_id == project_id,
            DeployEvent.environment == "prod",
            DeployEvent.event_type == "deploy",
            DeployEvent.status == "ok",
        )
        # By the order they were written — ``created_at`` is the start of their transaction, not the write.
        .order_by(DeployEvent.seq.desc())
        .limit(1)
    ).scalar_one_or_none()


def _rank(project: ProjectStanding) -> tuple:
    """Waiting on a person first, the longest waiting at the top; then at work, paused, and what stands still."""
    if not project.open_versions:
        return (3, _NEVER, project.name.lower())
    first = min(project.open_versions, key=lambda v: (_ORDER.get(v.status, 3), v.waiting_since or _NEVER))
    return (_ORDER.get(first.status, 3), first.waiting_since or _NEVER, project.name.lower())


def project_standing(db: Session, user: User) -> list[ProjectStanding]:
    """Every active project the person works on — as the project list: the admin all, anyone else their own."""
    query = select(Project).where(Project.status == "active")
    if not authz.is_admin(user):
        query = query.where(Project.created_by == user.id)
    standing = [
        ProjectStanding(
            project_id=p.id,
            name=p.name,
            slug=p.slug,
            open_versions=_open_versions(db, p.id),
            prod_version=_prod_version(db, p.id),
        )
        for p in db.execute(query).scalars()
    ]
    for project in standing:
        project.open_versions.sort(key=lambda v: (_ORDER.get(v.status, 3), v.waiting_since or _NEVER))
    return sorted(standing, key=_rank)
