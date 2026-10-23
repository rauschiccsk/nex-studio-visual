import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes.agent_terminal import router as agent_terminal_router
from backend.api.routes.auth import router as auth_router
from backend.api.routes.backlog import router as backlog_router
from backend.api.routes.ci_runner import router as ci_runner_router
from backend.api.routes.ci_runs import router as ci_runs_router
from backend.api.routes.credentials import router as credentials_router
from backend.api.routes.customers import router as customers_router
from backend.api.routes.dead_code import router as dead_code_router
from backend.api.routes.dedo import router as dedo_router
from backend.api.routes.delivery_statements import router as delivery_statements_router
from backend.api.routes.deploy import router as deploy_router
from backend.api.routes.external_cost import router as external_cost_router
from backend.api.routes.health import health_check as _health_check_handler
from backend.api.routes.knowledge import router as knowledge_router
from backend.api.routes.metrics import router as metrics_router
from backend.api.routes.ns_code import router as ns_code_router
from backend.api.routes.pipeline import router as pipeline_router
from backend.api.routes.plane_tickets import router as plane_tickets_router
from backend.api.routes.poradca import router as poradca_router
from backend.api.routes.price_estimate import router as price_estimate_router
from backend.api.routes.project_files import router as project_files_router
from backend.api.routes.project_specs import router as project_specs_router
from backend.api.routes.project_surfaces import router as project_surfaces_router
from backend.api.routes.projects import router as projects_router
from backend.api.routes.rag import router as rag_router
from backend.api.routes.release_notes import router as release_notes_router
from backend.api.routes.standing import router as standing_router
from backend.api.routes.system_settings import router as system_settings_router
from backend.api.routes.user_agent_settings import router as user_agent_settings_router
from backend.api.routes.user_layout import router as user_layout_router
from backend.api.routes.user_notes import router as user_notes_router
from backend.api.routes.user_sessions import router as user_sessions_router
from backend.api.routes.users import router as users_router
from backend.api.routes.version_notes import router as version_notes_router
from backend.api.routes.versions import router as versions_router
from backend.config.settings import settings
from backend.db.session import SessionLocal
from backend.services import agent_terminal as agent_terminal_service
from backend.services import build_db as build_db_service
from backend.services import build_sandbox as build_sandbox_service
from backend.services import kb_index_sync as kb_index_sync_service
from backend.services import orchestrator as orchestrator_service
from backend.services import smoke_scratch
from backend.services.poradca import readiness as poradca_readiness
from backend.services.poradca import runner as poradca_runner
from backend.services.poradca import sandbox as poradca_sandbox

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


async def _reap_build_orphans() -> None:
    """Sweep the build-turn docker objects the previous backend process did not live to clean up.

    A module-level hook (like :func:`_run_alembic_upgrade`) so the test session can neutralise it: every
    ``TestClient(app)`` enters this lifespan, and a test process owns no build turn — for it, everything
    wearing the label belongs to a LIVE build on the same host (incident 03.10.2026, ICCINT-162).
    """
    await build_db_service.reap_orphans()


def _sweep_poradca_trash() -> int:
    """Finishes deleting records of deleted Poradca conversations that remained in the trash (ICCINT-167).

    A module-level hook (like :func:`_reap_build_orphans`) so the test suite can switch it off: the test process
    would otherwise touch the trash of the live cockpit on the same machine.
    """
    return poradca_sandbox.sweep_trash()


def _sweep_smoke_scratch() -> int:
    """DEV-58 — removes the scratch folders of project checks a restart interrupted (a check runs in this process,
    so at start none is in flight). A hook like :func:`_sweep_poradca_trash`."""
    return smoke_scratch.sweep()


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


async def _ci_watch_loop() -> None:
    """DEV-85 — watches every project's main branch on GitHub and tells its addressee when a run fails or turns green.

    Each pass has its own session and commits per project; a failed pass is logged and the loop goes on — the next
    pass asks again, and what was told is in the database, so nothing is told twice."""
    from backend.services import ci_watch

    while True:
        try:
            db = SessionLocal()
            try:
                await ci_watch.watch_once(db)
            finally:
                db.close()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("CI watch loop iteration failed")
        await asyncio.sleep(settings.ci_watch_interval_seconds)


async def _build_reminder_loop() -> None:
    """DEV-152 — a build waiting on the Manažér longer than the interval in Nastavenia reminds him on Telegram.

    Each pass has its own session; when a reminder went is in the database, so a restart never sends one twice."""
    from backend.services import build_reminders

    while True:
        try:
            db = SessionLocal()
            try:
                await build_reminders.remind_once(db)
            finally:
                db.close()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("build reminder loop iteration failed")
        await asyncio.sleep(settings.build_reminder_check_seconds)


async def kb_publish_loop() -> None:
    """DEV-94 — sends the Knowledge Base's commits to GitHub, where the copy on MAGER reads it from.

    Every cockpit write publishes at once; this pass sends again what could not leave then (network, GitHub). GitHub
    ahead of this tree is said, never merged (:mod:`backend.services.kb_git`). The reason is logged when it changes,
    not every pass."""
    from backend.services import kb_git

    said: Optional[str] = None
    while True:
        try:
            reason = await asyncio.to_thread(kb_git.publish, Path(settings.knowledge_base_path))
            if reason != said:
                if reason:
                    logger.warning("Knowledge Base not on GitHub: %s", reason)
                else:
                    logger.info("Knowledge Base on GitHub again")
                said = reason
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("KB publish loop iteration failed")
        await asyncio.sleep(settings.kb_publish_interval_seconds)


async def _kb_index_sync_loop() -> None:
    """Úloha na pozadí: dorovnáva RAG index so Znalostnou bázou (ICCINT-111).

    Dovtedy indexer nespúšťalo NIČ — žiadny časovač, cron ani sledovač — a povinnosť „po zápise do KB
    reindexuj" bola nesplniteľná, lebo príkaz nebol uvedený nikde (hlavný ``CLAUDE.md`` §13 posielal
    na rolu, rola späť na §13). Zmerané 10.09.2026: z 205 súborov nesedelo 113.

    Prvý prechod ide hneď po štarte, nie až o štvrťhodinu: po reštarte je toto jediné miesto, kde sa
    rozdiel dá zistiť, a čakať s ním by znamenalo pätnásť minút odpovedí zo starého sveta.

    Zlyhanie jedného prechodu sa zapíše a pokračuje sa — rozdiel zostane v čísle, ktoré kokpit
    ukazuje, takže sa nemá ako stratiť potichu.
    """
    while True:
        try:
            await kb_index_sync_service.sync_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("KB index sync loop iteration failed")
        try:
            await asyncio.sleep(kb_index_sync_service.KB_SYNC_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            raise


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

    Build sandbox readiness (ICCINT-16 STEP 2): the Príprava/Návrh/Programovanie build turns run OS-isolated and
    do NOT fall back to a subprocess when the sandbox cannot start — the fallback is the exposure the sandbox
    removes. An unmet precondition therefore FAILS those turns, so it is probed at
    boot and logged at ERROR: the operator learns the image is missing now, not when the first build stops.
    """
    _run_alembic_upgrade()

    build_sandbox_service.log_startup_readiness()
    _poradca_problems = poradca_readiness.problems()
    if _poradca_problems:
        logger.error("Poradca is NOT ready: %s", "; ".join(_poradca_problems))
    else:
        logger.info("Poradca ready")
    # …and sweep what the LAST process did not live to clean up. A Programovanie turn holds a PostgreSQL
    # container and a docker network that its ``finally`` releases — a ``docker compose up -d`` deploy in the
    # middle of that turn re-creates this container and no ``finally`` ever runs. Both objects carry
    # ``build_db.OWNER_LABEL`` precisely so they can be found; until now nothing looked. Safe HERE and only
    # here: this process owns no turn yet, so everything wearing the label is garbage by definition.
    await _reap_build_orphans()

    db = SessionLocal()
    try:
        agent_terminal_service.mark_orphaned_on_startup(db)
        # F-007 §7.3 / CR-NS-021: a backend restart kills the build loop's background dispatch,
        # stranding the pipeline at build/agent_working. Flip such builds to awaiting_director so
        # the Director can resume via "Pokračovať v builde" (the loop then reclaims the in_progress task).
        recovered_builds = orchestrator_service.recover_orphaned_builds_on_startup(db)
        if recovered_builds:
            logger.info("Recovered %d orphaned build pipeline(s) after restart", recovered_builds)
        # ICCINT-167: a Poradca answer interrupted by a restart will never be finished — it is closed with a reason,
        # so that the conversation does not hang in „Poradca odpovedá…" forever.
        failed_poradca = poradca_runner.fail_orphans(db)
        if failed_poradca:
            logger.info("Poradca: closed %d answer(s) interrupted by the restart", failed_poradca)
        # …and finishes deleting records of deleted conversations that could not be fully deleted (a container
        # from the previous run was still writing to them; ``_reap_build_orphans`` above already cleaned that one up).
        swept_poradca = _sweep_poradca_trash()
        if swept_poradca:
            logger.info("Poradca: removed %d transcript(s) of deleted conversations left in the trash", swept_poradca)
        swept_smoke = _sweep_smoke_scratch()
        if swept_smoke:
            logger.info("Release check: removed %d scratch folder(s) left by interrupted runs", swept_smoke)
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
    # Slučka sa NESPÚŠŤA v testoch: ``TestClient(app)`` prechádza skutočným životným cyklom, takže by
    # siahala na ostrý Qdrant s cestou ku Znalostnej báze prepnutou na dočasný priečinok — presne tak
    # vznikol incident 11.09.2026. Druhá, dôležitejšia poistka je v samotnej službe
    # (``deletion_is_safe``): tá chráni aj ostrú prevádzku, keby sa KB raz nepripojila.
    ci_watch_task: Optional[asyncio.Task] = None
    if settings.ci_watch_enabled:
        ci_watch_task = asyncio.create_task(_ci_watch_loop(), name="ci-watch")
    build_reminder_task: Optional[asyncio.Task] = None
    if settings.build_reminders_enabled:
        build_reminder_task = asyncio.create_task(_build_reminder_loop(), name="build-reminders")
    kb_publish_task: Optional[asyncio.Task] = None
    if settings.kb_publish_enabled:
        kb_publish_task = asyncio.create_task(kb_publish_loop(), name="kb-publish")
    kb_index_task: Optional[asyncio.Task] = None
    if settings.kb_index_sync_enabled:
        kb_index_task = asyncio.create_task(
            _kb_index_sync_loop(),
            name="kb-index-sync",
        )

    try:
        yield
    finally:
        idle_task.cancel()
        retention_task.cancel()
        orch_session_retention_task.cancel()
        if kb_index_task is not None:
            kb_index_task.cancel()
        if ci_watch_task is not None:
            ci_watch_task.cancel()
        if kb_publish_task is not None:
            kb_publish_task.cancel()
        if build_reminder_task is not None:
            build_reminder_task.cancel()
        for t in (
            idle_task,
            retention_task,
            orch_session_retention_task,
            kb_index_task,
            ci_watch_task,
            kb_publish_task,
            build_reminder_task,
        ):
            if t is None:
                continue
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
app.include_router(project_surfaces_router, prefix="/api/v1")
app.include_router(knowledge_router, prefix="/api/v1/knowledge")
app.include_router(project_specs_router, prefix="/api/v1/project-specs")
app.include_router(rag_router, prefix="/api/v1/rag")
app.include_router(credentials_router, prefix="/api/v1/credentials")
app.include_router(backlog_router, prefix="/api/v1/backlog")
# The versions router intentionally mounts under the bare ``/api/v1`` prefix
# because it spans two URL families (``/projects/{id}/versions`` and
# ``/versions/{id}``) — see DESIGN.md §2.6 Version Management.
app.include_router(versions_router, prefix="/api/v1")
# The customers router (CR-V2-025) also mounts under the bare ``/api/v1`` prefix
# because it spans two URL families (``/projects/{slug}/customers`` and
# ``/customers/{id}``) — see design §3.2 Zákazníci.
app.include_router(customers_router, prefix="/api/v1")
app.include_router(plane_tickets_router, prefix="/api/v1")  # DEV-120 „Plane tikety“
app.include_router(standing_router, prefix="/api/v1")  # DEV-150 „Kde stojíme“ na Prehľade
app.include_router(version_notes_router, prefix="/api/v1")  # DEV-151 „Kde sme skončili“
app.include_router(price_estimate_router, prefix="/api/v1")  # DEV-154 orientačná cena verzie
app.include_router(dead_code_router, prefix="/api/v1")  # DEV-156 nepoužívaný kód z minulých verzií
# The deploy router (CR-V2-026) also mounts under the bare ``/api/v1`` prefix
# because its routes sit under ``/customers/{id}/deploy`` | ``/accept`` — see design §3 Deploy & Customers.
app.include_router(deploy_router, prefix="/api/v1")
app.include_router(metrics_router, prefix="/api/v1")
# The external-cost router (CR-V2-063) feeds the same Náklady screen as the metrics router and shares
# its ``/projects/{slug}/…`` path family, so it mounts under the same bare ``/api/v1`` prefix.
app.include_router(external_cost_router, prefix="/api/v1")
# Public (no-auth) per-version changelog — the *Aktualizácie* feature. Mounted
# under the bare ``/api/v1`` prefix; the route path is ``/release-notes``.
app.include_router(release_notes_router, prefix="/api/v1")
app.include_router(system_settings_router, prefix="/api/v1/system-settings")
app.include_router(user_agent_settings_router, prefix="/api/v1/user-agent-settings")
app.include_router(user_layout_router, prefix="/api/v1/user-layout")
app.include_router(user_notes_router, prefix="/api/v1/notes")
app.include_router(ci_runs_router, prefix="/api/v1")
app.include_router(ci_runner_router, prefix="/api/v1")
app.include_router(ns_code_router, prefix="/api/v1")
app.include_router(poradca_router, prefix="/api/v1/poradca")
app.include_router(agent_terminal_router, prefix="/api/v1/agent-terminal")
app.include_router(pipeline_router, prefix="/api/v1/pipeline")
# DEV-44: files the Manažér hands the AI Agent — ``private/`` of the project, under the build they belong to.
app.include_router(project_files_router, prefix="/api/v1/pipeline")
app.include_router(delivery_statements_router, prefix="/api/v1")
# Dedo's own door (ICCINT-14, charter §4.5) — the NEX Studio technical team's MACHINE identity, mounted
# apart from every user-facing router because that separation IS the permission model: the router carries
# its own auth dependency (``X-Dedo-Token``, never a user JWT) and exposes exactly five endpoints, so
# Dedo's abilities are bounded by what is mounted here rather than by a check somebody has to remember.
app.include_router(dedo_router, prefix="/api/v1/dedo")


@app.get("/health")
def health_check() -> dict:
    """Health check endpoint — delegates to the health module.

    Called by: the container's HEALTHCHECK (``backend.scripts.healthcheck``) and Dedo's check after a deploy.
    """
    return _health_check_handler()


@app.get("/api/v1/version")
def running_version() -> dict:
    """Verzia BEŽIACEHO backendu — jediná cesta, ktorou sa k nej dostane prehliadač (ICCINT-128).

    **Prečo to nestačilo cez ``/health``.** Panel sa na verziu pýtal práve tam a nikdy ju nedostal:
    ``/health`` si odchytáva nginx obrazoviek a odpovedá zaň sám (``{"service": "frontend"}``).
    Panel teda spadol späť na číslo vpečené do obrazoviek a vydával ho za verziu celej aplikácie —
    presne tá chyba, ktorú mal ICCINT-128 odstrániť. Zbadal to Director 24.09.2026: panel hlásil
    v4.40.2, kým backend bežal na v4.40.7.

    **Prečo pod ``/api/v1/``.** Táto predpona je presmerovaná na backend v OBOCH prostrediach —
    nginx v prevádzke aj vite vo vývoji. Vlastná cesta mimo nej by vo vývoji ticho nefungovala, čo
    je tá istá pasca ešte raz.

    Bez prihlásenia zámerne: to isté číslo potrebuje aj prihlasovacia obrazovka. Nenesie nič, čo by
    sa nedalo prečítať z bežiaceho obrazu.
    """
    return {"version": settings.app_version}
