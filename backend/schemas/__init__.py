"""Pydantic schemas package.

Request/response DTOs used by FastAPI routes. Domain-specific schemas live in
sibling modules (e.g. ``project_module.py``) and are re-exported from here for
convenient consumption by API routers.
"""

from backend.schemas.bug import (
    BugCreate,
    BugRead,
    BugSeverity,
    BugSource,
    BugStatus,
    BugUpdate,
)
from backend.schemas.epic import (
    EpicCreate,
    EpicRead,
    EpicStatus,
    EpicUpdate,
)
from backend.schemas.feat import (
    FeatCreate,
    FeatRead,
    FeatStatus,
    FeatUpdate,
)
from backend.schemas.module_dependency import (
    ModuleDependencyCreate,
    ModuleDependencyRead,
    ModuleDependencyUpdate,
)
from backend.schemas.project import (
    ProjectCategory,
    ProjectCreate,
    ProjectRead,
    ProjectStatus,
    ProjectUpdate,
)
from backend.schemas.project_module import (
    ProjectModuleCreate,
    ProjectModuleRead,
    ProjectModuleStatus,
    ProjectModuleUpdate,
)
from backend.schemas.task import (
    TaskCreate,
    TaskRead,
    TaskStatus,
    TaskType,
    TaskUpdate,
)
from backend.schemas.user import (
    ChangePasswordRequest,
    UserCreate,
    UserRead,
    UserRole,
    UserUpdate,
)
from backend.schemas.user_session import (
    UserSessionCreate,
    UserSessionRead,
    UserSessionUpdate,
)
from backend.schemas.version import (
    VersionCreate,
    VersionRead,
    VersionStatus,
    VersionUpdate,
)

__all__ = [
    "BugCreate",
    "BugRead",
    "BugSeverity",
    "BugSource",
    "BugStatus",
    "BugUpdate",
    "EpicCreate",
    "EpicRead",
    "EpicStatus",
    "EpicUpdate",
    "FeatCreate",
    "FeatRead",
    "FeatStatus",
    "FeatUpdate",
    "ModuleDependencyCreate",
    "ModuleDependencyRead",
    "ModuleDependencyUpdate",
    "ProjectCategory",
    "ProjectCreate",
    "ProjectRead",
    "ProjectStatus",
    "ProjectUpdate",
    "ProjectModuleCreate",
    "ProjectModuleRead",
    "ProjectModuleStatus",
    "ProjectModuleUpdate",
    "TaskCreate",
    "TaskRead",
    "TaskStatus",
    "TaskType",
    "TaskUpdate",
    "ChangePasswordRequest",
    "UserCreate",
    "UserRead",
    "UserRole",
    "UserUpdate",
    "UserSessionCreate",
    "UserSessionRead",
    "UserSessionUpdate",
    "VersionCreate",
    "VersionRead",
    "VersionStatus",
    "VersionUpdate",
]
