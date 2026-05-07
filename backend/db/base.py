"""Central import point for the SQLAlchemy ``Base`` and every ORM model.

This module exists so Alembic's ``env.py`` (and any drift-detection tooling)
can populate ``Base.metadata`` with a single import, instead of listing every
model module by hand. Importing this module guarantees that every table in
the domain is registered on ``Base.metadata`` and therefore visible to
``alembic revision --autogenerate``.

Whenever a new model is added, import it here so the Alembic autogenerate
pipeline picks it up automatically.
"""

from backend.db.models.architect import ArchitectMessage, ArchitectSession
from backend.db.models.base import Base, TimestampMixin, UUIDMixin
from backend.db.models.bugs import Bug, BugFixTask
from backend.db.models.delegations import AutoFixAttempt, Delegation, ExecutionLog
from backend.db.models.foundation import User, UserSession
from backend.db.models.guardian import GuardianPrecedent, GuardianReview
from backend.db.models.migration import MigrationBatch, MigrationCategoryStatus, MigrationIdMap
from backend.db.models.project_member import ProjectMember
from backend.db.models.projects import ModuleDependency, Project, ProjectModule
from backend.db.models.reports import ReportConfig
from backend.db.models.specifications import DesignDocument, ProfessionalSpecification, RawSpecification, UIDesign
from backend.db.models.system_settings import SystemSetting
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version

# The complete list of concrete ORM models covered by Alembic migrations 001-022.
# Ordered loosely by creation order so diffs against migration history are easy
# to follow.
ALL_MODELS: tuple[type, ...] = (
    GuardianPrecedent,
    User,
    Project,
    Bug,
    MigrationBatch,
    MigrationCategoryStatus,
    ProjectModule,
    ProjectMember,
    ArchitectSession,
    ArchitectMessage,
    DesignDocument,
    Epic,
    Feat,
    ModuleDependency,
    RawSpecification,
    ProfessionalSpecification,
    ReportConfig,
    Task,
    UserSession,
    BugFixTask,
    MigrationIdMap,
    Delegation,
    AutoFixAttempt,
    ExecutionLog,
    GuardianReview,
    UIDesign,
    Version,
    SystemSetting,
)

__all__ = [
    "ALL_MODELS",
    "ArchitectMessage",
    "ArchitectSession",
    "AutoFixAttempt",
    "Base",
    "Bug",
    "BugFixTask",
    "Delegation",
    "DesignDocument",
    "Epic",
    "ExecutionLog",
    "Feat",
    "GuardianPrecedent",
    "GuardianReview",
    "MigrationBatch",
    "MigrationCategoryStatus",
    "MigrationIdMap",
    "ModuleDependency",
    "ProfessionalSpecification",
    "Project",
    "ProjectMember",
    "ProjectModule",
    "RawSpecification",
    "ReportConfig",
    "SystemSetting",
    "Task",
    "TimestampMixin",
    "UIDesign",
    "UUIDMixin",
    "User",
    "UserSession",
    "Version",
]
