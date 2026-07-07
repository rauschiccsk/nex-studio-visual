"""Agent → Dedo escalation delivery (Director observation #6).

When the AI Agent hits an error it CANNOT fix because the fix requires a change to
NEX Studio ITSELF (the framework/tooling, §15 "fix NEX Studio, not the project"), the
orchestrator settles the build ``blocked``/``block_reason='framework_issue'`` and calls
:func:`escalate_to_dedo` to DELIVER the agent's message to Dedo two ways (the
Director-approved A+B):

  * **(A)** an audit-trail file written into ``<DEDO_CHANNEL_DIR>/inbox/`` — the channel Dedo
    monitors (``.dedo-channel/README.md`` format: ``system-to-dedo-YYYY-MM-DD-HHMM-framework-issue-
    <slug>.md`` with YAML frontmatter ``from: system`` / ``to: dedo`` / ``type: flag`` + the message
    + context). The channel dir is env-configurable (``DEDO_CHANNEL_DIR``) so the v3 backend can point
    it at the mounted ``.dedo-channel`` (the legacy path is unreachable inside a v3 instance — the SAME
    reachability class of bug as the #5-fixed notify script).
  * **(B)** a Telegram ping to the project owner (Director) via :func:`notify.send_telegram` — reusing the
    #5-fixed notify script (the script owns the bot token; the backend never reads/logs it).

Best-effort + never raises (mirrors :mod:`notify`): a delivery failure is logged and never propagates —
an unreachable channel mount or a Telegram hiccup must NOT crash the settle path (the block is already
recorded in the DB + the append-only message log, so the escalation is never lost even if delivery fails).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from backend.config.settings import settings
from backend.services import notify

logger = logging.getLogger(__name__)


def _channel_dir() -> Path:
    """The .dedo-channel directory (``DEDO_CHANNEL_DIR`` env → the legacy path default)."""
    return Path(settings.dedo_channel_dir)


def _slugify_topic(value: str) -> str:
    """Lowercase kebab-case, safe-for-filename fragment (``.dedo-channel/README.md`` topic convention)."""
    cleaned = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return cleaned or "unknown"


def build_channel_file(
    *,
    project_slug: str,
    version_number: str,
    dedo_message: str,
    context: str,
    now: datetime,
) -> tuple[str, str]:
    """Build the (filename, markdown body) for the ``.dedo-channel/inbox`` escalation file.

    Pure + deterministic (``now`` injected) so the settle path and the tests share ONE format. The
    filename follows the channel convention ``system-to-dedo-YYYY-MM-DD-HHMM-framework-issue-<slug>.md``;
    the body is Markdown with the README's YAML frontmatter (``from: system`` / ``to: dedo`` /
    ``type: flag``) + the agent's message + the build context.
    """
    slug = _slugify_topic(project_slug)
    stamp = now.strftime("%Y-%m-%d-%H%M")
    filename = f"system-to-dedo-{stamp}-framework-issue-{slug}.md"
    body = (
        "---\n"
        "from: system\n"
        "to: dedo\n"
        f"topic: framework-issue {project_slug} v{version_number}\n"
        f"date: {now.strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
        "type: flag\n"
        "---\n\n"
        "## Téma\n\n"
        f"AI Agent projektu **{project_slug}** (v{version_number}) narazil na problém, ktorý NEVIE opraviť, "
        "lebo si vyžaduje zmenu NEX Studia samotného (framework/tooling, §15). Build je zablokovaný "
        "(`block_reason=framework_issue`), Manažér s tým nevie nič urobiť — čaká sa na Deda.\n\n"
        "## Správa od agenta\n\n"
        f"{dedo_message.strip()}\n\n"
        "## Kontext\n\n"
        f"{context.strip()}\n\n"
        "## Akcia očakávaná od Deda\n\n"
        "Posúď potrebnú zmenu NEX Studia, oprav framework a odblokuj build "
        "(reset → `awaiting_manazer`), aby Manažér mohol pokračovať.\n"
    )
    return filename, body


def _write_channel_file(
    *,
    project_slug: str,
    version_number: str,
    dedo_message: str,
    context: str,
    now: datetime,
) -> Optional[Path]:
    """Delivery (A): write the escalation file into ``<channel>/inbox/``. Returns the path, or ``None`` when
    the channel dir is unreachable/unwritable (logged, never raised)."""
    filename, body = build_channel_file(
        project_slug=project_slug,
        version_number=version_number,
        dedo_message=dedo_message,
        context=context,
        now=now,
    )
    try:
        inbox = _channel_dir() / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        path = inbox / filename
        path.write_text(body, encoding="utf-8")
        return path
    except OSError:
        # A v3 instance without the .dedo-channel mount, a read-only dir, a perms mismatch — never crash the
        # settle path (the block is already durable in the DB + message log; Telegram (B) is the live nudge).
        logger.exception("dedo-channel escalation file write failed (dir=%s)", _channel_dir())
        return None


def _telegram_summary(*, project_slug: str, version_number: str, dedo_message: str) -> str:
    """Short (B) Telegram body — the escalation headline + a trimmed message preview (no secrets: this is
    the agent's own prose, never credentials)."""
    preview = dedo_message.strip().replace("\n", " ")
    if len(preview) > 400:
        preview = preview[:397] + "…"
    return (
        f"🛠️ NEX Studio potrebuje opravu (Dedo) — projekt {project_slug} v{version_number}.\n"
        f"AI Agent narazil na problém, ktorý si vyžaduje zmenu NEX Studia (framework). Build je "
        f"zablokovaný, Manažér to nevie opraviť.\n\n{preview}"
    )


async def escalate_to_dedo(
    *,
    project_slug: str,
    version_number: str,
    dedo_message: str,
    context: str,
    owner_chat_id: Optional[str],
    now: Optional[datetime] = None,
) -> Optional[Path]:
    """Deliver an agent → Dedo ``framework_issue`` escalation both ways (A + B). Never raises.

    (A) writes the ``.dedo-channel/inbox`` audit file; (B) pings the project owner over Telegram. Returns
    the written channel-file path (``None`` if the write was skipped/failed). ``now`` is injected for
    deterministic tests (defaults to the current UTC time).
    """
    stamp = now or datetime.now(timezone.utc)
    path = _write_channel_file(
        project_slug=project_slug,
        version_number=version_number,
        dedo_message=dedo_message,
        context=context,
        now=stamp,
    )
    if owner_chat_id:
        # notify.send_telegram is itself best-effort (never raises); the guard just skips a pointless call
        # when the owner has no chat_id configured.
        await notify.send_telegram(
            _telegram_summary(project_slug=project_slug, version_number=version_number, dedo_message=dedo_message),
            owner_chat_id,
        )
    return path
