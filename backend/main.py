import asyncio
import logging
import sys
from contextlib import asynccontextmanager

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes.agent_terminal import router as agent_terminal_router
from backend.api.routes.auth import router as auth_router
from backend.api.routes.backlog import router as backlog_router
from backend.api.routes.bugs import router as bugs_router
from backend.api.routes.credentials import router as credentials_router
from backend.api.routes.customers import router as customers_router
from backend.api.routes.epics import router as epics_router
from backend.api.routes.feats import router as feats_router
from backend.api.routes.health import health_check as _health_check_handler
from backend.api.routes.knowledge import router as knowledge_router
from backend.api.routes.metrics import router as metrics_router
from backend.api.routes.pipeline import router as pipeline_router
from backend.api.routes.project_members import router as project_members_router
from backend.api.routes.project_specs import router as project_specs_router
from backend.api.routes.projects import router as projects_router
from backend.api.routes.rag import router as rag_router
from backend.api.routes.release_notes import router as release_notes_router
from backend.api.routes.system_settings import router as system_settings_router
from backend.api.routes.tasks import router as tasks_router
from backend.api.routes.uploads import router as uploads_router
from backend.api.routes.user_agent_settings import router as user_agent_settings_router
from backend.api.routes.user_sessions import router as user_sessions_router
from backend.api.routes.users import router as users_router
from backend.api.routes.versions import router as versions_router
from backend.config.settings import settings
from backend.db.session import SessionLocal
from backend.services import agent_terminal as agent_terminal_service
from backend.services import orchestrator as orchestrator_service

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


async def _agent_terminal_idle_loop() -> None:
    """Background task: every 5 min, kill agent terminal sessions idle > TTL.

    Lifecycle is tied to the FastAPI lifespan — task is created on startup
    and cancelled on shutdown. Each pass uses its own short-lived DB
    session (``SessionLocal()``) since the asyncio loop survives many
    requests but a single transaction would not.
    """
    while True:
        try:
            await asyncio.sleep(300)
            db = SessionLocal()
            try:
                await agent_terminal_service.idle_cleanup(db)
            finally:
                db.close()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("agent_terminal idle_cleanup loop iteration failed")


async def _agent_terminal_log_retention_loop() -> None:
    """Background task: daily, delete agent terminal log files older than
    :data:`agent_terminal.LOG_RETENTION_DAYS` days.

    Director directive 2026-05-19: durable PTY logs need retention to
    avoid unbounded disk growth. Files for sessions still active or
    ended within the retention window are preserved.
    """
    while True:
        try:
            await asyncio.sleep(agent_terminal_service.LOG_CLEANUP_INTERVAL_SECONDS)
            db = SessionLocal()
            try:
                agent_terminal_service.cleanup_old_logs(db)
            finally:
                db.close()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("agent_terminal log retention loop iteration failed")


async def _orchestrator_session_retention_loop() -> None:
    """Background task: daily, prune orchestrator_session rows idle > 7 days (R1-d / D3).

    Mirrors :func:`_agent_terminal_idle_loop` — own short-lived DB session per pass, lifecycle tied to
    the FastAPI lifespan. Session hygiene only: a new-version kickoff already deletes a project's
    sessions, so this just bounds unbounded row growth from long-idle ``(project, role)`` threads.
    """
    while True:
        try:
            await asyncio.sleep(orchestrator_service.ORCHESTRATOR_SESSION_CLEANUP_INTERVAL_SECONDS)
            db = SessionLocal()
            try:
                orchestrator_service.cleanup_old_orchestrator_sessions(db)
            finally:
                db.close()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("orchestrator session retention loop iteration failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: migrations + agent terminal startup hooks.

    KB seed (kb_sync.seed_from_filesystem) was removed in M1 of the
    feature parity audit (2026-05-07): KB is now filesystem-based via
    :mod:`backend.services.knowledge_manager` (1:1 port from NEX
    Command); no DB seed step is needed because the
    ``/api/v1/knowledge`` router reads the filesystem live.

    Agent terminal hooks (added 2026-05-13):

    * On startup, mark every ``ended_at IS NULL`` row in
      ``agent_terminal_sessions`` as ``server_restart`` — sessions
      cannot survive a BE container restart.
    * Spawn the periodic idle-cleanup task that kills sessions idle
      beyond :data:`agent_terminal.IDLE_TTL_SECONDS`.
    """
    _run_alembic_upgrade()

    db = SessionLocal()
    try:
        agent_terminal_service.mark_orphaned_on_startup(db)
        # F-007 §7.3 / CR-NS-021: a backend restart kills the build loop's background dispatch,
        # stranding the pipeline at build/agent_working. Flip such builds to awaiting_director so
        # the Director can resume via "Pokračovať v builde" (the loop then reclaims the in_progress task).
        recovered_builds = orchestrator_service.recover_orphaned_builds_on_startup(db)
        if recovered_builds:
            logger.info("Recovered %d orphaned build pipeline(s) after restart", recovered_builds)
    finally:
        db.close()

    idle_task = asyncio.create_task(
        _agent_terminal_idle_loop(),
        name="agent-terminal-idle-cleanup",
    )
    retention_task = asyncio.create_task(
        _agent_terminal_log_retention_loop(),
        name="agent-terminal-log-retention",
    )
    orch_session_retention_task = asyncio.create_task(
        _orchestrator_session_retention_loop(),
        name="orchestrator-session-retention",
    )

    try:
        yield
    finally:
        idle_task.cancel()
        retention_task.cancel()
        orch_session_retention_task.cancel()
        for t in (idle_task, retention_task, orch_session_retention_task):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass


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
app.include_router(knowledge_router, prefix="/api/v1/knowledge")
app.include_router(project_specs_router, prefix="/api/v1/project-specs")
app.include_router(rag_router, prefix="/api/v1/rag")
app.include_router(project_members_router, prefix="/api/v1/project-members")
app.include_router(credentials_router, prefix="/api/v1/credentials")
app.include_router(epics_router, prefix="/api/v1/epics")
app.include_router(feats_router, prefix="/api/v1/feats")
app.include_router(tasks_router, prefix="/api/v1/tasks")
app.include_router(bugs_router, prefix="/api/v1/bugs")
app.include_router(backlog_router, prefix="/api/v1/backlog")
# The versions router intentionally mounts under the bare ``/api/v1`` prefix
# because it spans two URL families (``/projects/{id}/versions`` and
# ``/versions/{id}``) — see DESIGN.md §2.6 Version Management.
app.include_router(versions_router, prefix="/api/v1")
# The customers router (CR-V2-025) also mounts under the bare ``/api/v1`` prefix
# because it spans two URL families (``/projects/{slug}/customers`` and
# ``/customers/{id}``) — see design §3.2 Zákazníci.
app.include_router(customers_router, prefix="/api/v1")
app.include_router(metrics_router, prefix="/api/v1")
app.include_router(uploads_router, prefix="/api/v1")
# Public (no-auth) per-version changelog — the *Aktualizácie* feature. Mounted
# under the bare ``/api/v1`` prefix; the route path is ``/release-notes``.
app.include_router(release_notes_router, prefix="/api/v1")
app.include_router(system_settings_router, prefix="/api/v1/system-settings")
app.include_router(user_agent_settings_router, prefix="/api/v1/user-agent-settings")
app.include_router(agent_terminal_router, prefix="/api/v1/agent-terminal")
app.include_router(pipeline_router, prefix="/api/v1/pipeline")


@app.get("/health")
def health_check() -> dict:
    """Health check endpoint — delegates to the health module."""
    return _health_check_handler()
