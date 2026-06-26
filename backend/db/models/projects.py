"""Project domain models — projects."""

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from backend.db.models.base import Base, TimestampMixin, UUIDMixin


class Project(Base, UUIDMixin, TimestampMixin):
    """Project managed in NEX Studio."""

    __tablename__ = "projects"

    name = Column(String(255), nullable=False)
    slug = Column(String(100), nullable=False)
    category = Column(String(20), nullable=False)
    description = Column(Text, nullable=False)
    status = Column(String(20), nullable=False, server_default="active")
    backend_port = Column(Integer, nullable=True)
    frontend_port = Column(Integer, nullable=True)
    db_port = Column(Integer, nullable=True)
    repo_url = Column(String(255), nullable=True)
    source_path = Column(Text, nullable=True)
    kb_path = Column(Text, nullable=True)
    # UAT deploy mapping (F-009, CR-NS-098). Maps this project to its
    # ``/opt/uat/<uat_slug>`` deploy (e.g. ``nex-ledger`` → ``"ledger"``,
    # ``nex-inbox`` → ``"mager"``) so the Fast-Fix Lane can auto-redeploy UAT
    # via ``scripts/uat-deploy.py <uat_slug> --project <slug>``. NULL = no UAT
    # configured → the fast-fix auto-deploy is skipped gracefully.
    uat_slug = Column(String(100), nullable=True)
    guardian_enabled = Column(Boolean, nullable=False, server_default="false")
    created_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # Notification owner (CR-NS-012). Receives agent Telegram notifications
    # for this project via their User.telegram_chat_id. Optional — defaults
    # to the creator at create time; ON DELETE SET NULL so removing the user
    # leaves the project intact (just unowned for notifications).
    owner_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    __table_args__ = (
        UniqueConstraint("name", name="uq_projects_name"),
        UniqueConstraint("slug", name="uq_projects_slug"),
        CheckConstraint(
            "category IN ('singlemodule', 'multimodule')",
            name="ck_projects_category",
        ),
        CheckConstraint(
            "status IN ('active', 'archived', 'paused')",
            name="ck_projects_status",
        ),
        # Per-project port uniqueness — no two port columns on the same
        # row may share a non-NULL value. Matches migration 030.
        CheckConstraint(
            """
                    (backend_port IS NULL OR frontend_port IS NULL OR backend_port <> frontend_port)
                AND (backend_port IS NULL OR db_port IS NULL OR backend_port <> db_port)
                AND (frontend_port IS NULL OR db_port IS NULL OR frontend_port <> db_port)
            """,
            name="ck_projects_ports_distinct",
        ),
    )

    # Inverse side of Version.project (defined in backend/db/models/versions.py).
    # Deleting a Project cascades to its Versions via the FK ondelete='CASCADE'.
    versions = relationship(
        "Version",
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
