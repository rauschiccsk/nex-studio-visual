"""Knowledge Base domain models."""

from sqlalchemy import (
    TIMESTAMP,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID

from backend.db.models.base import Base, TimestampMixin, UUIDMixin


class KbDocument(Base, UUIDMixin, TimestampMixin):
    """Knowledge-base document tracked for a project or ICC-wide."""

    __tablename__ = "kb_documents"

    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    module_id = Column(
        UUID(as_uuid=True),
        ForeignKey("project_modules.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    title = Column(String(500), nullable=False)
    file_path = Column(Text, nullable=False)
    doc_category = Column(String(30), nullable=False)
    qdrant_collection = Column(String(100), nullable=True)
    qdrant_point_id = Column(String(100), nullable=True, index=True)
    indexed_at = Column(TIMESTAMP(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "doc_category IN ("
            "'standards','decisions','lessons','patterns','design','behavior','session',"
            "'icc','infrastructure','customers','shuhari','templates','service-manuals',"
            "'deployment','quarantine','credentials','project-status','project-history',"
            "'project-architect','project-other'"
            ")",
            name="ck_kb_documents_doc_category",
        ),
        Index("ix_kb_documents_doc_category", "doc_category"),
    )
