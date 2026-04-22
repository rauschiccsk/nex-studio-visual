"""Specification domain models — raw, professional specifications, design documents and UI designs."""

from sqlalchemy import (
    TIMESTAMP,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID

from backend.db.models.base import Base, TimestampMixin, UUIDMixin


class RawSpecification(Base, UUIDMixin, TimestampMixin):
    """Customer specification — raw text input for AI transformation."""

    __tablename__ = "raw_specifications"

    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    input_text = Column(Text, nullable=False)
    input_format = Column(String(20), nullable=False, server_default="text")
    language = Column(String(10), nullable=False, server_default="sk")
    status = Column(String(20), nullable=False, server_default="pending")
    created_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "input_format IN ('text', 'pdf', 'docx')",
            name="ck_raw_specifications_input_format",
        ),
        CheckConstraint(
            "status IN ('pending', 'processing', 'done', 'failed')",
            name="ck_raw_specifications_status",
        ),
        Index("ix_raw_specifications_status", "status"),
    )


class ProfessionalSpecification(Base, UUIDMixin, TimestampMixin):
    """AI-generated professional specification derived from a raw specification."""

    __tablename__ = "professional_specifications"

    raw_spec_id = Column(
        UUID(as_uuid=True),
        ForeignKey("raw_specifications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    content = Column(Text, nullable=False)
    version = Column(Integer, nullable=False, server_default="1")
    approved_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    approved_at = Column(TIMESTAMP(timezone=True), nullable=True)


class DesignDocument(Base, UUIDMixin, TimestampMixin):
    """Design or behavior document for a project or module."""

    __tablename__ = "design_documents"

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
    doc_type = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    version = Column(Integer, nullable=False, server_default="1")
    approved_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    approved_at = Column(TIMESTAMP(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "doc_type IN ('design', 'behavior')",
            name="ck_design_documents_doc_type",
        ),
        Index(
            "ix_design_documents_project_module_type",
            "project_id",
            "module_id",
            "doc_type",
        ),
    )


class UIDesign(Base, UUIDMixin, TimestampMixin):
    """AI-assisted UI mockup for a project — Step 2B of the pipeline."""

    __tablename__ = "ui_designs"

    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    content = Column(Text, nullable=False, server_default="")
    html_preview = Column(Text, nullable=True)
    approved_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    approved_at = Column(TIMESTAMP(timezone=True), nullable=True)
