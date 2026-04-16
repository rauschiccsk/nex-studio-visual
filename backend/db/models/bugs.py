"""Bug domain models — Bug and BugFixTask."""

from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import relationship

from backend.db.models.base import Base, TimestampMixin, UUIDMixin


class Bug(Base, UUIDMixin, TimestampMixin):
    """Bug reported against a project."""

    __tablename__ = "bugs"

    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("versions.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    bug_number = Column(Integer, nullable=False)
    title = Column(String(500), nullable=False)
    description = Column(Text, nullable=False)
    severity = Column(String(10), nullable=False, index=True)
    status = Column(String(20), nullable=False, server_default="new", index=True)
    source = Column(String(20), nullable=False, server_default="internal")
    reported_by = Column(String(255), nullable=True)
    environment = Column(String(50), nullable=True)
    resolved_at = Column(TIMESTAMP(timezone=True), nullable=True)
    commit_hash = Column(String(40), nullable=True)
    created_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("project_id", "bug_number", name="uq_bugs_project_id_bug_number"),
        CheckConstraint(
            "severity IN ('critical', 'major', 'minor')",
            name="ck_bugs_severity",
        ),
        CheckConstraint(
            "status IN ('new', 'accepted', 'in_progress', 'resolved', 'wont_fix')",
            name="ck_bugs_status",
        ),
        CheckConstraint(
            "source IN ('internal', 'customer')",
            name="ck_bugs_source",
        ),
    )

    # Inverse side of Version.bugs. The FK uses ondelete='RESTRICT' —
    # deleting a Version that still has Bugs raises a FK violation.
    version = relationship("Version", back_populates="bugs")


class BugFixTask(Base, UUIDMixin, TimestampMixin):
    """Task created to fix a specific bug."""

    __tablename__ = "bug_fix_tasks"

    bug_id = Column(
        UUID(as_uuid=True),
        ForeignKey("bugs.id", ondelete="CASCADE"),
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
        UniqueConstraint("bug_id", "number", name="uq_bug_fix_tasks_bug_id_number"),
        CheckConstraint(
            "task_type IN ('backend', 'frontend', 'migration', 'test', 'docs')",
            name="ck_bug_fix_tasks_task_type",
        ),
        CheckConstraint(
            "status IN ('todo', 'in_progress', 'done', 'failed')",
            name="ck_bug_fix_tasks_status",
        ),
    )
