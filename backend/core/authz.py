"""Project-ownership authorization.

Role gates (:mod:`backend.core.security`) answer *what role* a user has; this module answers *may this
user touch THIS project*. Since the ownership simplification (Director, 2026-07-28) the two questions
are fully separated: **the Shuhari roles no longer decide anything about projects.** They govern the
Knowledge Base (:mod:`backend.utils.kb_access`), user administration and the credentials store — and
nothing here.

THE WHOLE MODEL, in one sentence: a project belongs to the one user who created it, that user may do
everything on it, nobody else sees it at all, and the single account named ``admin`` sees and may do
everything everywhere.

What that replaced: a matrix of three roles × an owner flag × a "sensitive vs ordinary operation" split
(``ri_only``). Three interacting variables produced rules nobody could hold in their head, and holes kept
turning up in them. One question — *is it his project?* — cannot rot the same way.

OWNERSHIP IS ``Project.created_by``, NOT ``Project.owner_id``. The column literally named "owner" is the
TELEGRAM NOTIFICATION target (CR-NS-012): nullable, ``ON DELETE SET NULL``, and it may legitimately point
at somebody other than the creator. ``created_by`` is ``NOT NULL`` with ``ON DELETE RESTRICT`` — the
creator cannot be deleted out from under the project. Binding rights to ``owner_id`` would make the
permission follow whoever receives the notifications, and would make a project *unowned* the moment that
user was removed. Read :func:`project_owner_id` before touching any of this.

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

#: The one account that sees and may do everything, in every project. An ACCOUNT, not a role: giving
#: somebody the ``ri`` role makes them a Manager of the Knowledge Base, not of everyone else's projects.
#: Deliberately a username rather than a flag column — there is exactly one such account, it is created by
#: the bootstrap, and a column would be one more thing that can drift.
ADMIN_USERNAME = "admin"


def is_admin(user: User) -> bool:
    """True for the single ``admin`` account — the only user who may touch a project they do not own."""
    return user.username == ADMIN_USERNAME


def is_owner_or_admin(user: User, created_by: UUID) -> bool:
    """True if ``user`` may touch a project created by ``created_by``.

    The entire rule. There is no tier, no role clause and no ``ri_only`` variant: the owner may do
    EVERYTHING on his own project, and ``admin`` may do everything anywhere. Anyone else gets nothing —
    and gets it uniformly, so a read cannot succeed where a write fails.

    ``ri_only`` was removed rather than defaulted, on purpose: every one of its ~30 call sites had to be
    revisited, and deleting the parameter turns each into a loud ``TypeError`` at import/call time. Had it
    stayed accepted-and-ignored, thirty sensitive-write sites would have kept passing ``ri_only=True``
    while it silently meant nothing — a half-migration that looks finished and is not.
    """
    return created_by == user.id or is_admin(user)


def project_owner_id(project: Project) -> UUID:
    """The user a project BELONGS to — ``created_by``, always.

    A named accessor exists so nobody reaches for ``project.owner_id``, which is a different thing: the
    Telegram NOTIFICATION target (nullable, ``SET NULL`` on user delete, may point elsewhere by design).
    Two columns, one of them named "owner", and only the other one confers rights — this is the single
    likeliest way to wire the permission to the wrong field.
    """
    return project.created_by


def _forbidden() -> HTTPException:
    return HTTPException(status.HTTP_403_FORBIDDEN, detail="Nemáš prístup k tomuto projektu.")


def _not_found(what: str) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail=f"{what} sa nenašiel.")


def authorize_created_by(user: User, created_by: UUID) -> None:
    """Raise 403 unless ``user`` may access a project owned by ``created_by`` (see :func:`is_owner_or_admin`)."""
    if not is_owner_or_admin(user, created_by):
        raise _forbidden()


def authorize_project(user: User, project: Project) -> None:
    """Raise 403 unless ``user`` may access ``project``."""
    authorize_created_by(user, project_owner_id(project))


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


def assert_project_id_access(db: Session, user: User, project_id: UUID) -> Project:
    project = _get_project(db, project_id)
    authorize_project(user, project)
    return project


def assert_project_slug_access(db: Session, user: User, slug: str) -> Project:
    project = _get_project_by_slug(db, slug)
    authorize_project(user, project)
    return project


def assert_version_access(db: Session, user: User, version_id: UUID) -> Version:
    version = db.get(Version, version_id)
    if version is None:
        raise _not_found("Verzia")
    authorize_project(user, _get_project(db, version.project_id))
    return version


def assert_epic_access(db: Session, user: User, epic_id: UUID) -> Epic:
    epic = db.get(Epic, epic_id)
    if epic is None:
        raise _not_found("Epik")
    authorize_project(user, _get_project(db, epic.project_id))
    return epic


def assert_feat_access(db: Session, user: User, feat_id: UUID) -> Feat:
    feat = db.get(Feat, feat_id)
    if feat is None:
        raise _not_found("Funkcia")
    epic = db.get(Epic, feat.epic_id)
    if epic is None:
        raise _not_found("Epik")
    authorize_project(user, _get_project(db, epic.project_id))
    return feat


def assert_task_access(db: Session, user: User, task_id: UUID) -> Task:
    task = db.get(Task, task_id)
    if task is None:
        raise _not_found("Úloha")
    feat = db.get(Feat, task.feat_id)
    if feat is None:
        raise _not_found("Funkcia")
    epic = db.get(Epic, feat.epic_id)
    if epic is None:
        raise _not_found("Epik")
    authorize_project(user, _get_project(db, epic.project_id))
    return task


def assert_customer_access(db: Session, user: User, customer_id: UUID) -> Customer:
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise _not_found("Zákazník")
    authorize_project(user, _get_project(db, customer.project_id))
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
