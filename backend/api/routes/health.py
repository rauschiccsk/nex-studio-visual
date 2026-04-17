"""Health check endpoint with database and Claude CLI status."""

import logging
import shutil
from pathlib import Path

from sqlalchemy import text

from backend.db.session import engine

logger = logging.getLogger(__name__)


def _check_claude_cli_available() -> bool:
    """Return True if the ``claude`` CLI binary is on PATH."""
    return shutil.which("claude") is not None


def _check_claude_config_mounted() -> bool:
    """Return True if the Claude config directory exists at /root/.claude."""
    try:
        return Path("/root/.claude").is_dir()
    except (PermissionError, OSError):
        return False


def health_check() -> dict:
    """Health check endpoint with database connectivity and Claude CLI status."""
    db_ok = False
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            db_ok = True
    except Exception:
        logger.warning("Database health check failed", exc_info=True)

    return {
        "status": "ok",
        "version": "0.1.0",
        "db": "connected" if db_ok else "disconnected",
        "claude_cli_available": _check_claude_cli_available(),
        "claude_config_mounted": _check_claude_config_mounted(),
    }
