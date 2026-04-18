"""SQLAlchemy models package."""

from backend.db.models.architect import ArchitectMessage, ArchitectSession
from backend.db.models.base import Base, TimestampMixin, UUIDMixin
from backend.db.models.bugs import Bug, BugFixTask
from backend.db.models.delegations import AutoFixAttempt, Delegation, ExecutionLog
from backend.db.models.foundation import User, UserSession
from backend.db.models.guardian import GuardianPrecedent, GuardianReview
from backend.db.models.kb import KbDocument
from backend.db.models.migration import MigrationBatch, MigrationCategoryStatus, MigrationIdMap
from backend.db.models.projects import ModuleDependency, Project, ProjectModule
from backend.db.models.reports import ReportConfig
from backend.db.models.specifications import (
    DesignDocument,
    ProfessionalSpecification,
    RawSpecification,
)
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version

__all__ = [
    "ArchitectMessage",
    "ArchitectSession",
    "AutoFixAttempt",
    "Base",
    "Delegation",
    "ExecutionLog",
    "UUIDMixin",
    "TimestampMixin",
    "Bug",
    "BugFixTask",
    "DesignDocument",
    "GuardianPrecedent",
    "GuardianReview",
    "KbDocument",
    "MigrationBatch",
    "MigrationCategoryStatus",
    "MigrationIdMap",
    "ModuleDependency",
    "Project",
    "ProjectModule",
    "ProfessionalSpecification",
    "RawSpecification",
    "ReportConfig",
    "Epic",
    "Feat",
    "Task",
    "User",
    "UserSession",
    "Version",
]
