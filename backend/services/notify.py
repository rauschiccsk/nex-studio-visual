"""Presence-aware Telegram notification (CR-NS-018 Phase 5a).

A thin async wrapper over ``scripts/notify_telegram.sh``. The **script** owns the
bot token (sourced from ``/opt/infra/telegram/icc-agents.env``, mounted ro); the
backend passes only the message + the recipient ``chat_id`` and **never reads,
prints, or logs the token**. Fire-and-forget — a send failure is logged and
never propagates (notifications must not block the pipeline).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _resolve_notify_script() -> Path:
    """Locate ``notify_telegram.sh``. Prefer the copy BAKED into the image (``/app/scripts/``) so it
    works in ANY instance — including v3, where ``/opt/projects`` is an isolated per-instance workspace
    that does NOT contain nex-studio (the old hardcoded source path resolved to nothing there, so every
    Telegram nudge silently skipped). Falls back to the legacy bind-mounted source path for old images."""
    baked = Path(__file__).resolve().parents[2] / "scripts" / "notify_telegram.sh"
    if baked.exists():
        return baked
    return Path("/opt/projects/nex-studio/scripts/notify_telegram.sh")


NOTIFY_SCRIPT = _resolve_notify_script()


async def send_telegram(message: str, chat_id: str) -> None:
    """Send ``message`` to ``chat_id`` via the notify script. Never raises.

    No-op when ``chat_id`` is empty or the script is absent.
    """
    # Defensive: a chat_id pasted with surrounding whitespace (e.g. " 7204918893") is silently
    # rejected by the Telegram API → the nudge vanishes. Strip it so no send path can be broken by
    # stray whitespace, regardless of how the value was stored.
    chat_id = chat_id.strip() if chat_id else chat_id
    if not chat_id or not message:
        return
    if not NOTIFY_SCRIPT.exists():
        logger.warning("notify_telegram.sh not found at %s — skip", NOTIFY_SCRIPT)
        return
    try:
        proc = await asyncio.create_subprocess_exec(
            "bash",
            str(NOTIFY_SCRIPT),
            message,
            chat_id,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
    except Exception:
        logger.exception("Telegram notify failed (chat_id suppressed)")
