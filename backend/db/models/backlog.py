"""Backlog domain model — deferred future customer requirements (E2, CR-NS-041).

A per-project list of FUTURE customer requirements (each a stable ``REQ-{number}`` id) with a lifecycle
(open → included-in-version → realized-in-version / rejected) and a realization History. Standalone +
project-scoped — deliberately OUTSIDE the VERSION→EPIC→FEAT→TASK pipeline (an Epic requires a version;
a backlog item must not). The backlog NEVER auto-creates Epics/Tasks; version-include is content the
Designer reads, not Epic creation. Mirrors :class:`~backend.db.models.tasks.Epic` (per-project numbering,
FK CASCADE, status guarded by a DB CHECK rather than a Python Enum).
"""

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
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID

from backend.db.models.base import Base, TimestampMixin, UUIDMixin


class BacklogItem(Base, UUIDMixin, TimestampMixin):
    """One deferred customer requirement within a project (display id ``REQ-{number}``)."""

    __tablename__ = "backlog_items"

    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Per-project, auto MAX(number)+1 — display id REQ-{number} (stable for the item's lifetime).
    number = Column(Integer, nullable=False)
    title = Column(String(500), nullable=False)
    description = Column(Text, nullable=True)
    priority = Column(String(20), nullable=False, server_default="medium")
    status = Column(String(20), nullable=False, server_default="open")
    # The version the item is included-in / realized-in. SET NULL nullable — deleting the version leaves
    # the (historical) item intact, just unlinked.
    version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("versions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    realized_at = Column(TIMESTAMP(timezone=True), nullable=True)
    # DEV-121: the requirement's ticket in Plane's DEV register — id for the API, identifier (DEV-n) and address
    # for people. Set once by the first „Založiť tiket"; a second click returns this ticket.
    plane_issue_id = Column(String(64), nullable=True)
    plane_identifier = Column(String(32), nullable=True)
    plane_url = Column(String(500), nullable=True)
    # DEV-121: why the requirement was rejected — written to its ticket when it is cancelled.
    rejection_reason = Column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("project_id", "number", name="uq_backlog_items_project_id_number"),
        Index("ix_backlog_items_project_status", "project_id", "status"),
        CheckConstraint(
            "priority IN ('low', 'medium', 'high', 'critical')",
            name="ck_backlog_items_priority",
        ),
        CheckConstraint(
            "status IN ('open', 'included', 'realized', 'rejected')",
            name="ck_backlog_items_status",
        ),
    )
