import logging
import sys
from contextlib import asynccontextmanager

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes.architect import router as architect_router
from backend.api.routes.architect_messages import router as architect_messages_router
from backend.api.routes.architect_sessions import router as architect_sessions_router
from backend.api.routes.auth import router as auth_router
from backend.api.routes.auto_fix_attempts import router as auto_fix_attempts_router
from backend.api.routes.bug_fix_tasks import router as bug_fix_tasks_router
from backend.api.routes.bugs import router as bugs_router
from backend.api.routes.credentials import router as credentials_router
from backend.api.routes.delegations import router as delegations_router
from backend.api.routes.design_documents import router as design_documents_router
from backend.api.routes.epics import router as epics_router
from backend.api.routes.execution_logs import router as execution_logs_router
from backend.api.routes.feats import router as feats_router
from backend.api.routes.guardian_precedents import router as guardian_precedents_router
from backend.api.routes.guardian_reviews import router as guardian_reviews_router
from backend.api.routes.health import health_check as _health_check_handler
from backend.api.routes.knowledge import router as knowledge_router
from backend.api.routes.migration_batches import router as migration_batches_router
from backend.api.routes.migration_category_statuses import (
    router as migration_category_statuses_router,
)
from backend.api.routes.migration_id_maps import router as migration_id_maps_router
from backend.api.routes.module_dependencies import router as module_dependencies_router
from backend.api.routes.professional_specifications import (
    router as professional_specifications_router,
)
from backend.api.routes.project_members import router as project_members_router
from backend.api.routes.project_modules import router as project_modules_router
from backend.api.routes.projects import router as projects_router
from backend.api.routes.rag import router as rag_router
from backend.api.routes.raw_specifications import router as raw_specifications_router
from backend.api.routes.report_configs import router as report_configs_router
from backend.api.routes.system_settings import router as system_settings_router
from backend.api.routes.tasks import router as tasks_router
from backend.api.routes.ui_designs import router as ui_designs_router
from backend.api.routes.uploads import router as uploads_router
from backend.api.routes.user_sessions import router as user_sessions_router
from backend.api.routes.users import router as users_router
from backend.api.routes.versions import router as versions_router
from backend.config.settings import settings

# Route application loggers at INFO to stderr so ``docker logs`` surfaces
# request-level diagnostics (SSE state, Claude subprocess events, spec
# chat statistics). Without this Python's root logger runs at WARNING
# and every ``logger.info`` in the codebase gets silently dropped, which
# hid the spec-chat state machine from us during the "žiadna reakcia"
# incident. We lift our ``backend.*`` logger directly instead of
# replacing uvicorn's root handlers (``force=True`` would break
# uvicorn's own colourised access log).
logging.getLogger("backend").setLevel(logging.INFO)
if not logging.getLogger("backend").hasHandlers():
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger("backend").addHandler(_handler)
    logging.getLogger("backend").propagate = False

logger = logging.getLogger(__name__)


def _run_alembic_upgrade() -> None:
    """Run Alembic migrations to head on startup."""
    try:
        alembic_cfg = Config("alembic.ini")
        command.upgrade(alembic_cfg, "head")
        logger.info("Alembic migrations applied successfully")
    except Exception:
        logger.error("Failed to apply Alembic migrations", exc_info=True)
        raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: run migrations on startup.

    KB seed (kb_sync.seed_from_filesystem) was removed in M1 of the
    feature parity audit (2026-05-07): KB is now filesystem-based via
    :mod:`backend.services.knowledge_manager` (1:1 port from NEX
    Command); no DB seed step is needed because the
    ``/api/v1/knowledge`` router reads the filesystem live.
    """
    _run_alembic_upgrade()
    yield


app = FastAPI(
    title="NEX Studio",
    version=settings.app_version,
    description="Project management and AI delegation platform",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Entity CRUD routers (Feat 4). Prefixes are kebab-case and applied here —
# each router module is prefix-less so it can be mounted on an isolated
# TestClient app in its router tests without prefix duplication.
app.include_router(auth_router, prefix="/api/v1/auth")
app.include_router(users_router, prefix="/api/v1/users")
app.include_router(user_sessions_router, prefix="/api/v1/user-sessions")
app.include_router(projects_router, prefix="/api/v1/projects")
app.include_router(project_modules_router, prefix="/api/v1/project-modules")
app.include_router(module_dependencies_router, prefix="/api/v1/module-dependencies")
app.include_router(raw_specifications_router, prefix="/api/v1/raw-specifications")
app.include_router(ui_designs_router, prefix="/api/v1/ui-designs")
app.include_router(
    professional_specifications_router,
    prefix="/api/v1/professional-specifications",
)
app.include_router(design_documents_router, prefix="/api/v1/design-documents")
app.include_router(knowledge_router, prefix="/api/v1/knowledge")
app.include_router(rag_router, prefix="/api/v1/rag")
app.include_router(project_members_router, prefix="/api/v1/project-members")
app.include_router(credentials_router, prefix="/api/v1/credentials")
app.include_router(architect_sessions_router, prefix="/api/v1/architect-sessions")
# The architect router spans two URL families (/projects/{id}/architect and
# /architect/sessions/{id}) — mount at bare /api/v1 like the versions router.
app.include_router(architect_router, prefix="/api/v1")
app.include_router(architect_messages_router, prefix="/api/v1/architect-messages")
app.include_router(epics_router, prefix="/api/v1/epics")
app.include_router(feats_router, prefix="/api/v1/feats")
app.include_router(tasks_router, prefix="/api/v1/tasks")
app.include_router(bugs_router, prefix="/api/v1/bugs")
app.include_router(bug_fix_tasks_router, prefix="/api/v1/bug-fix-tasks")
app.include_router(delegations_router, prefix="/api/v1/delegations")
app.include_router(execution_logs_router, prefix="/api/v1/execution-logs")
app.include_router(auto_fix_attempts_router, prefix="/api/v1/auto-fix-attempts")
app.include_router(guardian_reviews_router, prefix="/api/v1/guardian-reviews")
app.include_router(guardian_precedents_router, prefix="/api/v1/guardian-precedents")
app.include_router(report_configs_router, prefix="/api/v1/report-configs")
app.include_router(migration_batches_router, prefix="/api/v1/migration-batches")
app.include_router(
    migration_category_statuses_router,
    prefix="/api/v1/migration-category-statuses",
)
app.include_router(migration_id_maps_router, prefix="/api/v1/migration-id-maps")
# The versions router intentionally mounts under the bare ``/api/v1`` prefix
# because it spans two URL families (``/projects/{id}/versions`` and
# ``/versions/{id}``) — see DESIGN.md §2.6 Version Management.
app.include_router(versions_router, prefix="/api/v1")
app.include_router(uploads_router, prefix="/api/v1")
app.include_router(system_settings_router, prefix="/api/v1/system-settings")


@app.get("/health")
def health_check() -> dict:
    """Health check endpoint — delegates to the health module."""
    return _health_check_handler()
