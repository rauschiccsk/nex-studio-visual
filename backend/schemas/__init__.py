"""Pydantic schemas package.

Request/response DTOs used by FastAPI routes. Domain-specific schemas live in
sibling modules (e.g. ``project.py``) and are re-exported from here for
convenient consumption by API routers.
"""

from backend.schemas.epic import (
    EpicCreate,
    EpicStatus,
)
from backend.schemas.feat import (
    FeatCreate,
    FeatStatus,
)
from backend.schemas.project import (
    ProjectAuthMode,
    ProjectCreate,
    ProjectRead,
    ProjectStatus,
    ProjectType,
    ProjectUpdate,
)
from backend.schemas.task import (
    TaskCreate,
    TaskStatus,
    TaskType,
)
from backend.schemas.user import (
    ChangePasswordRequest,
    UserCreate,
    UserRead,
    UserRole,
    UserUpdate,
)
from backend.schemas.user_session import (
    UserSessionRead,
)
from backend.schemas.version import (
    VersionCreate,
    VersionRead,
    VersionStatus,
    VersionUpdate,
)

__all__ = [
    "EpicCreate",
    "EpicStatus",
    "FeatCreate",
    "FeatStatus",
    "ProjectAuthMode",
    "ProjectCreate",
    "ProjectRead",
    "ProjectStatus",
    "ProjectType",
    "ProjectUpdate",
    "TaskCreate",
    "TaskStatus",
    "TaskType",
    "ChangePasswordRequest",
    "UserCreate",
    "UserRead",
    "UserRole",
    "UserUpdate",
    "UserSessionRead",
    "VersionCreate",
    "VersionRead",
    "VersionStatus",
    "VersionUpdate",
]
