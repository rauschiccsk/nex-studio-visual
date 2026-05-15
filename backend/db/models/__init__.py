"""SQLAlchemy models package."""

from backend.db.models.agent_terminal import AgentTerminalSession
from backend.db.models.architect import ArchitectMessage, ArchitectSession
from backend.db.models.base import Base, TimestampMixin, UUIDMixin
from backend.db.models.bugs import Bug, BugFixTask
from backend.db.models.credentials import Credential
from backend.db.models.delegations import AutoFixAttempt, Delegation, ExecutionLog
from backend.db.models.dialogue import DialogueMessage, DialogueSession
from backend.db.models.foundation import User, UserSession
from backend.db.models.guardian import GuardianPrecedent, GuardianReview
from backend.db.models.migration import MigrationBatch, MigrationCategoryStatus, MigrationIdMap
from backend.db.models.project_member import ProjectMember
from backend.db.models.projects import ModuleDependency, Project, ProjectModule
from backend.db.models.reports import ReportConfig
from backend.db.models.specifications import (
    DesignDocument,
    ProfessionalSpecChatMessage,
    ProfessionalSpecification,
    RawSpecification,
    UIDesign,
    UIDesignChatMessage,
)
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version

__all__ = [
    "AgentTerminalSession",
    "ArchitectMessage",
    "ArchitectSession",
    "AutoFixAttempt",
    "Base",
    "Delegation",
    "DialogueMessage",
    "DialogueSession",
    "ExecutionLog",
    "UUIDMixin",
    "TimestampMixin",
    "Bug",
    "BugFixTask",
    "Credential",
    "DesignDocument",
    "GuardianPrecedent",
    "GuardianReview",
    "MigrationBatch",
    "MigrationCategoryStatus",
    "MigrationIdMap",
    "ModuleDependency",
    "Project",
    "ProjectMember",
    "ProjectModule",
    "ProfessionalSpecChatMessage",
    "ProfessionalSpecification",
    "RawSpecification",
    "ReportConfig",
    "Epic",
    "Feat",
    "Task",
    "UIDesign",
    "UIDesignChatMessage",
    "User",
    "UserSession",
    "Version",
]
