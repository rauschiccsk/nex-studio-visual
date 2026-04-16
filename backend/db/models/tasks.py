"""Task domain models — epics, feats, and tasks (hierarchical numbering)."""

from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from backend.db.models.base import Base, TimestampMixin, UUIDMixin


class Epic(Base, UUIDMixin, TimestampMixin):
    """Epic — top-level grouping of feats within a project."""

    __tablename__ = "epics"

    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    module_id = Column(
        UUID(as_uuid=True),
        ForeignKey("project_modules.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("versions.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    number = Column(Integer, nullable=False)
    title = Column(String(500), nullable=False)
    status = Column(String(20), nullable=False, server_default="planned")

    __table_args__ = (
        UniqueConstraint("project_id", "number", name="uq_epics_project_id_number"),
        CheckConstraint(
            "status IN ('planned', 'in_progress', 'done')",
            name="ck_epics_status",
        ),
    )

    # Inverse side of Version.epics. The FK uses ondelete='RESTRICT' —
    # deleting a Version that still has Epics raises a FK violation.
    version = relationship("Version", back_populates="epics")


class Feat(Base, UUIDMixin, TimestampMixin):
    """Feat — a deliverable unit of work within an epic."""

    __tablename__ = "feats"

    epic_id = Column(
        UUID(as_uuid=True),
        ForeignKey("epics.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    number = Column(Integer, nullable=False)
    title = Column(String(500), nullable=False)
    description = Column(Text, nullable=False, server_default="")
    status = Column(String(20), nullable=False, server_default="todo")
    estimated_minutes = Column(Integer, nullable=True)
    actual_minutes = Column(Integer, nullable=True)
    task_count = Column(Integer, nullable=False, server_default="0")
    auto_fix_count = Column(Integer, nullable=False, server_default="0")

    __table_args__ = (
        UniqueConstraint("epic_id", "number", name="uq_feats_epic_id_number"),
        CheckConstraint(
            "status IN ('todo', 'in_progress', 'done', 'failed')",
            name="ck_feats_status",
        ),
        Index("ix_feats_status", "status"),
    )


class Task(Base, UUIDMixin, TimestampMixin):
    """Task — a single unit of delegated work within a feat."""

    __tablename__ = "tasks"

    feat_id = Column(
        UUID(as_uuid=True),
        ForeignKey("feats.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    number = Column(Integer, nullable=False)
    title = Column(String(500), nullable=False)
    description = Column(Text, nullable=False, server_default="")
    task_type = Column(String(20), nullable=False)
    status = Column(String(20), nullable=False, server_default="todo", index=True)
    estimated_minutes = Column(Integer, nullable=True)
    actual_minutes = Column(Integer, nullable=True)
    checklist_type = Column(String(30), nullable=True)

    __table_args__ = (
        UniqueConstraint("feat_id", "number", name="uq_tasks_feat_id_number"),
        CheckConstraint(
            "task_type IN ('backend', 'frontend', 'migration', 'test', 'docs')",
            name="ck_tasks_task_type",
        ),
        CheckConstraint(
            "status IN ('todo', 'in_progress', 'done', 'failed')",
            name="ck_tasks_status",
        ),
    )
