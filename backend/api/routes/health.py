"""Health check endpoint with database and Claude CLI status."""

import logging
import shutil
from pathlib import Path

from sqlalchemy import text

from backend.config.settings import settings
from backend.db.session import engine
from backend.services import consult_sandbox, notify

logger = logging.getLogger(__name__)


def _check_claude_cli_available() -> bool:
    """Return True if the ``claude`` CLI binary is on PATH."""
    return shutil.which(settings.claude_cli_path) is not None


def _check_claude_config_mounted() -> bool:
    """Return True if the Claude config directory exists."""
    try:
        return Path(settings.claude_config_dir).is_dir()
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
        "version": settings.app_version,
        "db": "connected" if db_ok else "disconnected",
        "claude_cli_available": _check_claude_cli_available(),
        "claude_config_mounted": _check_claude_config_mounted(),
        # Wired-but-inert infrastructure has to be visible SOMEWHERE. The consult sidecar is enabled by
        # default and degrades to a weaker (non-kernel-enforced) read-only turn when it cannot launch, which
        # for the audited deployment was every single consult — the configured image did not exist on the
        # host. Publishing readiness here means an unusable sandbox stops looking healthy.
        "consult_sandbox": consult_sandbox.preflight().as_dict(),
        # Empty ``app_public_url`` strips the cockpit deep link out of every Telegram nudge, so an away
        # Manažér gets a ping with no way back in. Report the absence rather than letting it stay invisible.
        "app_public_url_configured": bool(settings.app_public_url.strip()),
        # Nobody notices an out-of-band nudge that never arrives. ``attempted > 0 and delivered == 0`` is a
        # deployment whose notifications have never once worked — previously unknowable, because the notify
        # script exited 0 on every path and nothing inspected it.
        "telegram": notify.ledger.as_dict(),
    }
