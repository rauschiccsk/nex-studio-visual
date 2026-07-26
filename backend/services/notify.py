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
import os
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


async def send_telegram_checked(message: str, chat_id: str) -> tuple[bool, str]:
    """Send ``message`` to ``chat_id`` and REPORT the outcome — for the self-service "Poslať test" button
    (v4.0.52). Runs the notify script in ``NOTIFY_CHECK=1`` mode, which prints ``TELEGRAM_OK`` or
    ``TELEGRAM_FAIL:<description>`` parsed from the Telegram API response (the bot token is NEVER printed —
    it lives only in the request URL, absent from the response body). Returns ``(ok, plain-Slovak detail)``.
    Never raises. A non-expert uses this to learn their chat_id actually works (silent-fail was the gap)."""
    chat_id = chat_id.strip() if chat_id else chat_id
    if not chat_id:
        return False, "Telegram chat ID nie je nastavené — najprv ho zadaj a ulož."
    if not NOTIFY_SCRIPT.exists():
        return False, "Odosielač Telegramu nie je na serveri dostupný."
    try:
        proc = await asyncio.create_subprocess_exec(
            "bash",
            str(NOTIFY_SCRIPT),
            message,
            chat_id,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={**os.environ, "NOTIFY_CHECK": "1"},
        )
        out, _ = await proc.communicate()
    except Exception:
        logger.exception("Telegram checked send failed (chat_id suppressed)")
        return False, "Odoslanie testu zlyhalo (chyba servera)."
    text = (out or b"").decode("utf-8", "replace").strip()
    if text.startswith("TELEGRAM_OK"):
        return True, "Testovacia správa bola odoslaná — pozri Telegram."
    if text.startswith("TELEGRAM_FAIL:"):
        detail = text[len("TELEGRAM_FAIL:") :].strip() or "Nedoručené."
        # "chat not found" is the classic "bot not started / wrong id" case — give the actionable hint.
        if "chat not found" in detail.lower():
            return False, "Nedoručené (chat not found) — spustil si nášho bota (/start) a je chat ID správne?"
        return False, f"Nedoručené: {detail}"
    # No marker on stdout → the script no-oped early (missing token / server env).
    return False, "Telegram nie je na serveri nakonfigurovaný."
