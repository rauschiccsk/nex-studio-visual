"""DEV-59 — the cockpit tells time as the Director's clock shows it, not UTC.

Director 10.10.2026, after a time read from the cockpit's database was given to him in UTC (18:22:58 for his
20:22:58): „V kokpite má byť také isté časové pásmo ako tu u mňa, veď to robíme na jednom serveri.“ Everything the
backend writes for a person goes through :func:`local_time` (a guard finds every ``strftime`` in the backend);
the containers and the database run in the same zone (PROD compose ``TZ``, migration 114). The zone is ONE setting:
``settings.display_timezone``. Machine stamps stay UTC and say so (``…Z``).
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from backend.config.settings import settings


def local_zone() -> ZoneInfo:
    return ZoneInfo(settings.display_timezone)


def local_time(at: datetime) -> datetime:
    """``at`` on the local clock. A time without a zone is taken as UTC — what the cockpit stores and computes in."""
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return at.astimezone(local_zone())
