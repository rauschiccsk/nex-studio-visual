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

# The script lives on the mounted source tree (``/opt/projects`` is bind-mounted
# rw into the backend container), so no image COPY is needed.
NOTIFY_SCRIPT = Path("/opt/projects/nex-studio/scripts/notify_telegram.sh")


async def send_telegram(message: str, chat_id: str) -> None:
    """Send ``message`` to ``chat_id`` via the notify script. Never raises.

    No-op when ``chat_id`` is empty or the script is absent.
    """
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
