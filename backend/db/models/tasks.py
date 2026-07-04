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
    version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("versions.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    number = Column(Integer, nullable=False)
    title = Column(String(500), nullable=False)
    # STEP 3 (Plán úloh): the plain-language, jargon-free one-liner shown to the Manažér in the
    # three-layer rail (F-007 §5.x; step3-plan-design.md FIX4). The Epic has no ``description`` column —
    # ``plain_description`` is its ONLY prose. Nullable Text (migration 080); an omission parses (default
    # empty on the generating schema), and the FE shows a muted placeholder rather than technical text.
    plain_description = Column(Text, nullable=True)
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
    # STEP 3 (Plán úloh): plain-language one-liner for the three-layer rail (step3-plan-design.md FIX4) —
    # distinct from the technical ``description``. Nullable Text (migration 080); default empty on the
    # generating schema so an omission parses; the FE never falls back to ``description`` when empty.
    plain_description = Column(Text, nullable=True)
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
    # STEP 3 (Plán úloh): plain-language one-liner for the three-layer rail (step3-plan-design.md FIX4) —
    # distinct from the technical ``description``. Nullable Text (migration 080); default empty on the
    # generating schema so an omission parses; the FE never falls back to ``description`` when empty.
    plain_description = Column(Text, nullable=True)
    task_type = Column(String(20), nullable=False)
    status = Column(String(20), nullable=False, server_default="todo", index=True)
    priority = Column(String(10), nullable=False, server_default="normal")
    estimated_minutes = Column(Integer, nullable=True)
    actual_minutes = Column(Integer, nullable=True)
    checklist_type = Column(String(30), nullable=True)
    # F-007 §4: per-task diff anchor — repo HEAD captured when the task is
    # dispatched, so a retry still diffs against the original baseline. Written
    # by the per-task build loop (CR-3); dormant in CR-1.
    baseline_sha = Column(String(40), nullable=True)

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
        CheckConstraint(
            "priority IN ('normal', 'high', 'urgent')",
            name="ck_tasks_priority",
        ),
    )
