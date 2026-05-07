"""ProjectMember domain model — per-user project assignments for Shuhari RBAC.

Ported from NEX Command (`backend/db/models/orm.py::ProjectMember`)
per Director mandate 2026-05-07 (M2.B milestone of feature parity audit).

A ``shu`` user can only see KB documents under
``projects/<slug>/`` for projects they are explicitly a member of.
``ri`` and ``ha`` users see all projects regardless of membership.

Differences from NEX Command source:

* ``user_id`` is an FK on ``users.id`` (UUID) instead of a free-text
  ``username`` string. NEX Studio has a proper users table; we use it.
* ``id``/``created_at``/``updated_at`` come from ``UUIDMixin`` /
  ``TimestampMixin`` (NEX Studio convention) instead of inline columns.
* ``role`` defaults to ``"member"`` (project-level role, distinct from
  Shuhari role). Future use: distinguish project-owner / project-member
  for project-scoped permissions.
"""

from sqlalchemy import Column, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from backend.db.models.base import Base, TimestampMixin, UUIDMixin


class ProjectMember(Base, UUIDMixin, TimestampMixin):
    """Membership of a user in a project — gates KB access for ``shu`` role."""

    __tablename__ = "project_members"

    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role = Column(String(50), nullable=False, server_default="member")

    __table_args__ = (UniqueConstraint("project_id", "user_id", name="uq_project_members_project_user"),)
