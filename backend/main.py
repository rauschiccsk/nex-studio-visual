import logging
from contextlib import asynccontextmanager

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from backend.config.settings import settings
from backend.db.session import engine

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
    """Application lifespan: run migrations on startup."""
    _run_alembic_upgrade()
    yield


app = FastAPI(
    title="NEX Studio",
    version="0.1.0",
    description="Project management and AI delegation platform",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        f"http://localhost:{settings.frontend_port}",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health_check() -> dict:
    """Health check endpoint with database connectivity status."""
    db_ok = False
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            db_ok = True
    except Exception:
        logger.warning("Database health check failed", exc_info=True)

    return {"status": "ok", "version": "0.1.0", "db": "connected" if db_ok else "disconnected"}
