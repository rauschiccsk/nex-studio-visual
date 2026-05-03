"""Project domain models — projects and project modules."""

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
    ui_design_port = Column(Integer, nullable=True)
    repo_url = Column(String(255), nullable=True)
    source_path = Column(Text, nullable=True)
    kb_path = Column(Text, nullable=True)
    guardian_enabled = Column(Boolean, nullable=False, server_default="false")
    created_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
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
                AND (backend_port IS NULL OR ui_design_port IS NULL OR backend_port <> ui_design_port)
                AND (frontend_port IS NULL OR db_port IS NULL OR frontend_port <> db_port)
                AND (frontend_port IS NULL OR ui_design_port IS NULL OR frontend_port <> ui_design_port)
                AND (db_port IS NULL OR ui_design_port IS NULL OR db_port <> ui_design_port)
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


class ProjectModule(Base, UUIDMixin, TimestampMixin):
    """Module within a multimodule project."""

    __tablename__ = "project_modules"

    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    code = Column(String(50), nullable=False)
    name = Column(String(255), nullable=False)
    category = Column(String(50), nullable=False)
    status = Column(String(20), nullable=False, server_default="planned")
    design_doc_path = Column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("project_id", "code", name="uq_project_modules_project_id_code"),
        CheckConstraint(
            "status IN ('planned', 'in_design', 'in_development', 'done')",
            name="ck_project_modules_status",
        ),
        CheckConstraint(
            "category IN ('Systém', 'Katalógy', 'Sklad', 'Predaj', 'Nákup', 'Účtovníctvo', 'Pokladňa')",
            name="ck_project_modules_category",
        ),
        # Kebab-case, matches backend.schemas.project_module.MODULE_CODE_PATTERN
        # and the CHECK added by migration 032.
        CheckConstraint(
            r"code ~ '^[a-z][a-z0-9-]*[a-z0-9]$'",
            name="ck_project_modules_code_format",
        ),
    )


class ModuleDependency(Base, UUIDMixin, TimestampMixin):
    """Dependency edge between two modules within the same project."""

    __tablename__ = "module_dependencies"

    module_id = Column(
        UUID(as_uuid=True),
        ForeignKey("project_modules.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    depends_on_module_id = Column(
        UUID(as_uuid=True),
        ForeignKey("project_modules.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "module_id",
            "depends_on_module_id",
            name="uq_module_dependencies_module_id_depends_on_module_id",
        ),
    )
