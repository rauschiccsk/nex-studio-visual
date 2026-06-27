"""Central import point for the SQLAlchemy ``Base`` and every ORM model.

This module exists so Alembic's ``env.py`` (and any drift-detection tooling)
can populate ``Base.metadata`` with a single import, instead of listing every
model module by hand. Importing this module guarantees that every table in
the domain is registered on ``Base.metadata`` and therefore visible to
``alembic revision --autogenerate``.

Whenever a new model is added, import it here so the Alembic autogenerate
pipeline picks it up automatically.
"""

from backend.db.models.backlog import BacklogItem
from backend.db.models.base import Base, TimestampMixin, UUIDMixin
from backend.db.models.bugs import Bug
from backend.db.models.credentials import Credential
from backend.db.models.customers import Customer
from backend.db.models.foundation import User, UserAgentSettings, UserSession
from backend.db.models.orchestrator import OrchestratorSession
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.project_member import ProjectMember
from backend.db.models.projects import Project
from backend.db.models.system_settings import SystemSetting
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version

# The complete list of concrete ORM models covered by the Alembic migration chain.
# Ordered loosely by creation order so diffs against migration history are easy
# to follow.
ALL_MODELS: tuple[type, ...] = (
    User,
    Project,
    Bug,
    BacklogItem,
    ProjectMember,
    Epic,
    Feat,
    Task,
    UserSession,
    Version,
    SystemSetting,
    PipelineState,
    PipelineMessage,
    OrchestratorSession,
    UserAgentSettings,
    Credential,
    Customer,
)

__all__ = [
    "ALL_MODELS",
    "BacklogItem",
    "Base",
    "Bug",
    "Epic",
    "Feat",
    "OrchestratorSession",
    "PipelineMessage",
    "PipelineState",
    "Project",
    "ProjectMember",
    "SystemSetting",
    "Task",
    "TimestampMixin",
    "UUIDMixin",
    "User",
    "UserAgentSettings",
    "UserSession",
    "Version",
]
