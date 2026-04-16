"""Version domain model — release version for a project.

A ``Version`` groups Epics and Bugs targeted at a specific release of a
project. Each project has many versions (e.g. ``v1.0``, ``v1.1``).
"""

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from backend.db.models.base import Base, TimestampMixin, UUIDMixin


class Version(Base, UUIDMixin, TimestampMixin):
    """Release version of a project — container for Epics and Bugs."""

    __tablename__ = "versions"

    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_number = Column(String(50), nullable=False)
    status = Column(String(20), nullable=False, server_default="planned")
    description = Column(Text, nullable=True)
    target_date = Column(Date, nullable=True)
    release_date = Column(Date, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "version_number",
            name="uq_versions_project_id_version_number",
        ),
        CheckConstraint(
            "status IN ('planned', 'active', 'released')",
            name="ck_versions_status",
        ),
    )

    # Relationship to the owning Project. The inverse side ``Project.versions``
    # is defined on the Project model.
    project = relationship("Project", back_populates="versions")

    # Inverse sides of Epic.version and Bug.version. The FK columns
    # (Epic.version_id, Bug.version_id) are nullable with ondelete='RESTRICT' —
    # a Version cannot be deleted while Epics or Bugs still reference it.
    # ``passive_deletes=True`` prevents SQLAlchemy from auto-nulling the FK
    # before the DELETE, letting the DB enforce the RESTRICT constraint.
    epics = relationship(
        "Epic",
        back_populates="version",
        passive_deletes=True,
    )
    bugs = relationship(
        "Bug",
        back_populates="version",
        passive_deletes=True,
    )
