"""Presence-aware Telegram notification (CR-NS-018 Phase 5a).

A thin async wrapper over ``scripts/notify_telegram.sh``. The **script** owns the
bot token (sourced from ``/opt/infra/telegram/icc-agents.env``, mounted ro); the
backend passes only the message + the recipient ``chat_id`` and **never reads,
prints, or logs the token**.

Delivery is VERIFIED, not assumed (audit finding). Every send runs the script in
``NOTIFY_CHECK=1`` mode, which reports ``TELEGRAM_OK`` / ``TELEGRAM_FAIL:<reason>``
on stdout and exits non-zero when nothing was sent. Before that, the script exited
0 on every path — including its silent no-ops — and the backend inspected nothing,
so "notification sent" was structurally unknowable: a deployment with no bot token
configured looked exactly like a deployment delivering every nudge. Sending still
never raises and never blocks the pipeline; the difference is that the outcome is
now *known*, logged, and tallied on :data:`ledger` (published by ``GET /health``).
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

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


@dataclass
class DeliveryLedger:
    """Process-lifetime tally of Telegram sends, so "are nudges actually landing?" has an answer.

    A nudge is out-of-band by nature: nobody notices the ones that never arrive. The counters are the
    cheap standing evidence — ``attempted > 0 and delivered == 0`` is a deployment whose notifications
    have never worked, which is precisely the state the audit found and nothing reported."""

    attempted: int = 0
    delivered: int = 0
    failed: int = 0
    last_failure: Optional[str] = None

    def record(self, ok: bool, detail: str) -> None:
        self.attempted += 1
        if ok:
            self.delivered += 1
        else:
            self.failed += 1
            self.last_failure = detail

    def as_dict(self) -> dict:
        """JSON-safe payload for ``GET /health``. Carries no chat_id and no token — only counts + reason."""
        return {
            "attempted": self.attempted,
            "delivered": self.delivered,
            "failed": self.failed,
            "last_failure": self.last_failure,
            "script_available": NOTIFY_SCRIPT.exists(),
        }


#: The single process-wide ledger. Reset only by a restart (it is diagnostics, not state).
ledger = DeliveryLedger()


@dataclass(frozen=True)
class SendOutcome:
    """What the notify script reported for ONE send.

    ``kind`` separates the three genuinely different failures the old exit-0-everywhere script collapsed
    into one indistinguishable silence: Telegram itself refused (``telegram``), the script no-oped before
    ever calling Telegram because the server is unconfigured (``no_verdict``), or the script could not be
    started at all (``spawn_error``)."""

    ok: bool
    detail: str
    kind: str  # "ok" | "telegram" | "no_verdict" | "spawn_error"


async def _run_checked(message: str, chat_id: str) -> SendOutcome:
    """Run the notify script in ``NOTIFY_CHECK=1`` mode and classify its verdict. Never raises.

    ``detail`` is the script's own reason string on failure — already free of the bot token, which lives
    only in the request URL and never in the Telegram response body the script parses."""
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
        logger.exception("Telegram send failed to run (chat_id suppressed)")
        return SendOutcome(False, "notify script could not be started", "spawn_error")
    text = (out or b"").decode("utf-8", "replace").strip()
    if text.startswith("TELEGRAM_OK"):
        return SendOutcome(True, "", "ok")
    if text.startswith("TELEGRAM_FAIL:"):
        return SendOutcome(False, text[len("TELEGRAM_FAIL:") :].strip() or "nedoručené", "telegram")
    return SendOutcome(False, "notify script produced no verdict (server not configured?)", "no_verdict")


async def send_telegram(message: str, chat_id: str) -> bool:
    """Send ``message`` to ``chat_id``; return whether Telegram actually accepted it. Never raises.

    Returns ``False`` (without spawning anything) when ``chat_id``/``message`` is empty or the script is
    absent. A failure is logged at WARNING with the script's reason and counted on :data:`ledger` — the
    caller may ignore the result, but the deployment can no longer believe a nudge landed when it did not.
    """
    # Defensive: a chat_id pasted with surrounding whitespace (e.g. " 7204918893") is silently
    # rejected by the Telegram API → the nudge vanishes. Strip it so no send path can be broken by
    # stray whitespace, regardless of how the value was stored.
    chat_id = chat_id.strip() if chat_id else chat_id
    if not chat_id or not message:
        return False
    if not NOTIFY_SCRIPT.exists():
        logger.warning("notify_telegram.sh not found at %s — nudge NOT sent", NOTIFY_SCRIPT)
        ledger.record(False, f"notify script missing at {NOTIFY_SCRIPT}")
        return False
    outcome = await _run_checked(message, chat_id)
    ledger.record(outcome.ok, outcome.detail)
    if not outcome.ok:
        logger.warning(
            "Telegram nudge NOT delivered (chat_id suppressed): [%s] %s — %d/%d sends have failed this process",
            outcome.kind,
            outcome.detail,
            ledger.failed,
            ledger.attempted,
        )
    return outcome.ok


async def send_telegram_checked(message: str, chat_id: str) -> tuple[bool, str]:
    """Send ``message`` to ``chat_id`` and REPORT the outcome in plain Slovak — for the self-service
    "Poslať test" button (v4.0.52). Shares the verified send path with :func:`send_telegram` and only
    differs in translating the script's verdict into a sentence a non-expert can act on. Never raises.
    A non-expert uses this to learn their chat_id actually works (silent-fail was the gap)."""
    chat_id = chat_id.strip() if chat_id else chat_id
    if not chat_id:
        return False, "Telegram chat ID nie je nastavené — najprv ho zadaj a ulož."
    if not NOTIFY_SCRIPT.exists():
        return False, "Odosielač Telegramu nie je na serveri dostupný."
    outcome = await _run_checked(message, chat_id)
    ledger.record(outcome.ok, outcome.detail)
    if outcome.ok:
        return True, "Testovacia správa bola odoslaná — pozri Telegram."
    if outcome.kind == "no_verdict":
        # The script no-oped before reaching Telegram (missing token / server env).
        return False, "Telegram nie je na serveri nakonfigurovaný."
    if outcome.kind == "spawn_error":
        return False, "Odoslanie testu zlyhalo (chyba servera)."
    # "chat not found" is the classic "bot not started / wrong id" case — give the actionable hint.
    if "chat not found" in outcome.detail.lower():
        return False, "Nedoručené (chat not found) — spustil si nášho bota (/start) a je chat ID správne?"
    return False, f"Nedoručené: {outcome.detail}"
