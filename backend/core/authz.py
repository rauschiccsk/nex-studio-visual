"""Project-ownership authorization (v4.0.35).

Role gates (:mod:`backend.core.security`) answer *what role* a user has; this module answers *may this
user touch THIS project*. Privileged users (``ri``/``ha``) may touch every project; a Junior (``shu``)
may touch ONLY projects they created (``Project.created_by == user.id``).

Every project-scoped route resolves its project — directly by ``project_id``/``slug`` or by walking a
sub-resource FK up to the project (version → project, epic → project, feat → epic → project, task → feat
→ epic → project, customer → project) — and calls the shared check, so a Junior can never see or operate
another user's project.

Two styles are provided:
* imperative ``authorize_created_by`` / ``assert_*_access`` — call inside a route body that already has
  (or resolves) the object;
* FastAPI dependencies ``require_project_by_id`` etc. — resolve + authorize + return the object in one
  shot for routes whose path carries the id/slug.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.security import require_shu_or_above
from backend.db.models.customers import Customer
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version
from backend.db.session import get_db

#: Roles that may READ/access EVERY project (the team leads); a ``shu`` is scoped to their own.
PRIVILEGED_ROLES = ("ri", "ha")


def is_owner_or_privileged(user: User, created_by: UUID, *, ri_only: bool = False) -> bool:
    """True if ``user`` may access a project whose creator is ``created_by``.

    The privilege tier preserves the pre-v4.0.35 role boundaries + adds owner access:
    * default (``ri_only=False``) — the project-management / read tier, open to ``ri``/``ha`` (they "see
      all") plus the owner. Use for reads and for operations that were ``require_ha_or_above``.
    * ``ri_only=True`` — the sensitive-write tier that was ``require_ri_role`` (create/patch/delete a
      version, drive the pipeline, delete a project, deploy). ``ha`` does NOT gain these on projects they
      don't own — only ``ri`` (or the owner) may. This keeps a Junior able to run THEIR OWN project without
      silently escalating the Medior role.
    """
    if created_by == user.id:
        return True
    return user.role == "ri" or (not ri_only and user.role == "ha")


def _forbidden() -> HTTPException:
    return HTTPException(status.HTTP_403_FORBIDDEN, detail="Nemáš prístup k tomuto projektu.")


def _not_found(what: str) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail=f"{what} sa nenašiel.")


def authorize_created_by(user: User, created_by: UUID, *, ri_only: bool = False) -> None:
    """Raise 403 unless ``user`` may access a project owned by ``created_by`` (see :func:`is_owner_or_privileged`)."""
    if not is_owner_or_privileged(user, created_by, ri_only=ri_only):
        raise _forbidden()


def authorize_project(user: User, project: Project, *, ri_only: bool = False) -> None:
    """Raise 403 unless ``user`` may access ``project``."""
    authorize_created_by(user, project.created_by, ri_only=ri_only)


# ── Resolvers (imperative) — walk a sub-resource up to its owning project, then check ────────────────


def _get_project(db: Session, project_id: UUID) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise _not_found("Projekt")
    return project


def _get_project_by_slug(db: Session, slug: str) -> Project:
    project = db.execute(select(Project).where(Project.slug == slug)).scalar_one_or_none()
    if project is None:
        raise _not_found("Projekt")
    return project


def assert_project_id_access(db: Session, user: User, project_id: UUID, *, ri_only: bool = False) -> Project:
    project = _get_project(db, project_id)
    authorize_project(user, project, ri_only=ri_only)
    return project


def assert_project_slug_access(db: Session, user: User, slug: str, *, ri_only: bool = False) -> Project:
    project = _get_project_by_slug(db, slug)
    authorize_project(user, project, ri_only=ri_only)
    return project


def assert_version_access(db: Session, user: User, version_id: UUID, *, ri_only: bool = False) -> Version:
    version = db.get(Version, version_id)
    if version is None:
        raise _not_found("Verzia")
    authorize_project(user, _get_project(db, version.project_id), ri_only=ri_only)
    return version


def assert_epic_access(db: Session, user: User, epic_id: UUID, *, ri_only: bool = False) -> Epic:
    epic = db.get(Epic, epic_id)
    if epic is None:
        raise _not_found("Epik")
    authorize_project(user, _get_project(db, epic.project_id), ri_only=ri_only)
    return epic


def assert_feat_access(db: Session, user: User, feat_id: UUID, *, ri_only: bool = False) -> Feat:
    feat = db.get(Feat, feat_id)
    if feat is None:
        raise _not_found("Funkcia")
    epic = db.get(Epic, feat.epic_id)
    if epic is None:
        raise _not_found("Epik")
    authorize_project(user, _get_project(db, epic.project_id), ri_only=ri_only)
    return feat


def assert_task_access(db: Session, user: User, task_id: UUID, *, ri_only: bool = False) -> Task:
    task = db.get(Task, task_id)
    if task is None:
        raise _not_found("Úloha")
    feat = db.get(Feat, task.feat_id)
    if feat is None:
        raise _not_found("Funkcia")
    epic = db.get(Epic, feat.epic_id)
    if epic is None:
        raise _not_found("Epik")
    authorize_project(user, _get_project(db, epic.project_id), ri_only=ri_only)
    return task


def assert_customer_access(db: Session, user: User, customer_id: UUID, *, ri_only: bool = False) -> Customer:
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise _not_found("Zákazník")
    authorize_project(user, _get_project(db, customer.project_id), ri_only=ri_only)
    return customer


# ── FastAPI dependencies — resolve from the path param + authorize + return the object ───────────────


def require_project_by_id(
    project_id: UUID,
    current_user: User = Depends(require_shu_or_above),
    db: Session = Depends(get_db),
) -> Project:
    return assert_project_id_access(db, current_user, project_id)


def require_project_by_slug(
    slug: str,
    current_user: User = Depends(require_shu_or_above),
    db: Session = Depends(get_db),
) -> Project:
    return assert_project_slug_access(db, current_user, slug)


def require_version_by_id(
    version_id: UUID,
    current_user: User = Depends(require_shu_or_above),
    db: Session = Depends(get_db),
) -> Version:
    return assert_version_access(db, current_user, version_id)


def require_customer_by_id(
    customer_id: UUID,
    current_user: User = Depends(require_shu_or_above),
    db: Session = Depends(get_db),
) -> Customer:
    return assert_customer_access(db, current_user, customer_id)
