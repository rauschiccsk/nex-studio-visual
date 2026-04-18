"""Pydantic schemas package.

Request/response DTOs used by FastAPI routes. Domain-specific schemas live in
sibling modules (e.g. ``guardian.py``) and are re-exported from here for
convenient consumption by API routers.
"""

from backend.schemas.architect_message import (
    ArchitectMessageCost,
    ArchitectMessageCreate,
    ArchitectMessageRead,
    ArchitectMessageRole,
    ArchitectMessageUpdate,
)
from backend.schemas.architect_session import (
    ArchitectSessionCreate,
    ArchitectSessionRead,
    ArchitectSessionStatus,
    ArchitectSessionUpdate,
)
from backend.schemas.auto_fix_attempt import (
    AutoFixAttemptCreate,
    AutoFixAttemptRead,
    AutoFixAttemptUpdate,
)
from backend.schemas.bug import (
    BugCreate,
    BugRead,
    BugSeverity,
    BugSource,
    BugStatus,
    BugUpdate,
)
from backend.schemas.bug_fix_task import (
    BugFixTaskCreate,
    BugFixTaskRead,
    BugFixTaskStatus,
    BugFixTaskType,
    BugFixTaskUpdate,
)
from backend.schemas.delegation import (
    DelegationCCAgent,
    DelegationCreate,
    DelegationRead,
    DelegationStatus,
    DelegationUpdate,
)
from backend.schemas.design_document import (
    DesignDocumentCreate,
    DesignDocumentRead,
    DesignDocumentType,
    DesignDocumentUpdate,
)
from backend.schemas.epic import (
    EpicCreate,
    EpicRead,
    EpicStatus,
    EpicUpdate,
)
from backend.schemas.execution_log import (
    ExecutionLogCreate,
    ExecutionLogRead,
    ExecutionLogStatus,
    ExecutionLogTotalCost,
    ExecutionLogUpdate,
)
from backend.schemas.feat import (
    FeatCreate,
    FeatRead,
    FeatStatus,
    FeatUpdate,
)
from backend.schemas.guardian import (
    GuardianPrecedentCreate,
    GuardianPrecedentRead,
    GuardianPrecedentUpdate,
    GuardianReviewCreate,
    GuardianReviewLayer,
    GuardianReviewRead,
    GuardianReviewRiskLevel,
    GuardianReviewUpdate,
    GuardianVerdict,
)
from backend.schemas.kb_document import (
    KbDocumentCategory,
    KbDocumentCreate,
    KbDocumentRead,
    KbDocumentUpdate,
)
from backend.schemas.migration_batch import (
    MigrationBatchCreate,
    MigrationBatchDirection,
    MigrationBatchRead,
    MigrationBatchStatus,
    MigrationBatchUpdate,
)
from backend.schemas.migration_category_status import (
    MigrationCategoryStatusCreate,
    MigrationCategoryStatusRead,
    MigrationCategoryStatusStatus,
    MigrationCategoryStatusUpdate,
)
from backend.schemas.migration_id_map import (
    MigrationIdMapCreate,
    MigrationIdMapRead,
    MigrationIdMapUpdate,
)
from backend.schemas.module_dependency import (
    ModuleDependencyCreate,
    ModuleDependencyRead,
    ModuleDependencyUpdate,
)
from backend.schemas.professional_specification import (
    ProfessionalSpecificationCreate,
    ProfessionalSpecificationRead,
    ProfessionalSpecificationUpdate,
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
from backend.schemas.raw_specification import (
    RawSpecificationCreate,
    RawSpecificationInputFormat,
    RawSpecificationRead,
    RawSpecificationStatus,
    RawSpecificationUpdate,
)
from backend.schemas.report_config import (
    ReportConfigCreate,
    ReportConfigHourlyRate,
    ReportConfigRead,
    ReportConfigUpdate,
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
    "ArchitectMessageCost",
    "ArchitectMessageCreate",
    "ArchitectMessageRead",
    "ArchitectMessageRole",
    "ArchitectMessageUpdate",
    "ArchitectSessionCreate",
    "ArchitectSessionRead",
    "ArchitectSessionStatus",
    "ArchitectSessionUpdate",
    "AutoFixAttemptCreate",
    "AutoFixAttemptRead",
    "AutoFixAttemptUpdate",
    "BugCreate",
    "BugFixTaskCreate",
    "BugFixTaskRead",
    "BugFixTaskStatus",
    "BugFixTaskType",
    "BugFixTaskUpdate",
    "BugRead",
    "BugSeverity",
    "BugSource",
    "BugStatus",
    "BugUpdate",
    "DelegationCCAgent",
    "DelegationCreate",
    "DelegationRead",
    "DelegationStatus",
    "DelegationUpdate",
    "DesignDocumentCreate",
    "DesignDocumentRead",
    "DesignDocumentType",
    "DesignDocumentUpdate",
    "EpicCreate",
    "EpicRead",
    "EpicStatus",
    "EpicUpdate",
    "ExecutionLogCreate",
    "ExecutionLogRead",
    "ExecutionLogStatus",
    "ExecutionLogTotalCost",
    "ExecutionLogUpdate",
    "FeatCreate",
    "FeatRead",
    "FeatStatus",
    "FeatUpdate",
    "GuardianPrecedentCreate",
    "GuardianPrecedentRead",
    "GuardianPrecedentUpdate",
    "GuardianReviewCreate",
    "GuardianReviewLayer",
    "GuardianReviewRead",
    "GuardianReviewRiskLevel",
    "GuardianReviewUpdate",
    "GuardianVerdict",
    "KbDocumentCategory",
    "KbDocumentCreate",
    "KbDocumentRead",
    "KbDocumentUpdate",
    "MigrationBatchCreate",
    "MigrationBatchDirection",
    "MigrationBatchRead",
    "MigrationBatchStatus",
    "MigrationBatchUpdate",
    "MigrationCategoryStatusCreate",
    "MigrationCategoryStatusRead",
    "MigrationCategoryStatusStatus",
    "MigrationCategoryStatusUpdate",
    "MigrationIdMapCreate",
    "MigrationIdMapRead",
    "MigrationIdMapUpdate",
    "ModuleDependencyCreate",
    "ModuleDependencyRead",
    "ModuleDependencyUpdate",
    "ProfessionalSpecificationCreate",
    "ProfessionalSpecificationRead",
    "ProfessionalSpecificationUpdate",
    "ProjectCategory",
    "ProjectCreate",
    "ProjectModuleCreate",
    "ProjectModuleRead",
    "ProjectModuleStatus",
    "ProjectModuleUpdate",
    "ProjectRead",
    "ProjectStatus",
    "ProjectUpdate",
    "RawSpecificationCreate",
    "RawSpecificationInputFormat",
    "RawSpecificationRead",
    "RawSpecificationStatus",
    "RawSpecificationUpdate",
    "ReportConfigCreate",
    "ReportConfigHourlyRate",
    "ReportConfigRead",
    "ReportConfigUpdate",
    "TaskCreate",
    "TaskRead",
    "TaskStatus",
    "TaskType",
    "TaskUpdate",
    "ChangePasswordRequest",
    "UserCreate",
    "UserRead",
    "UserRole",
    "UserSessionCreate",
    "UserSessionRead",
    "UserSessionUpdate",
    "UserUpdate",
    "VersionCreate",
    "VersionRead",
    "VersionStatus",
    "VersionUpdate",
]
