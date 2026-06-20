"""Response schema for the public release-notes changelog.

A single per-version entry of the user-facing changelog served by
``GET /api/v1/release-notes`` (the *Aktualizácie* feature). Mirrors the
service-layer dict produced by
:func:`backend.services.release_notes.list_release_notes`.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class ReleaseNote(BaseModel):
    """One version's user-facing release notes.

    Attributes:
        version: Version directory name, e.g. ``"v0.9.0"`` (drives both the
            card heading and the newest-first ordering).
        released_at: ISO date string (``YYYY-MM-DD``) of the release. Sourced
            from the ``versions`` table (``release_date``) when a matching row
            exists, otherwise the ``RELEASE_NOTES.md`` file mtime. ``None`` only
            in the degenerate case where neither is available.
        markdown: Raw Markdown body of the version's ``RELEASE_NOTES.md`` —
            rendered client-side.
    """

    version: str
    released_at: Optional[str] = None
    markdown: str

    model_config = {"from_attributes": True}
