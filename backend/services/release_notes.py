"""Service layer for the public release-notes changelog (``/api/v1/release-notes``).

Backs the *Aktualizácie* feature. A deployed app exposes its **own**
changelog by reading the per-version ``RELEASE_NOTES.md`` files that were
baked into its backend image (``backend/Dockerfile`` copies only those
files — never the full ``docs/specs`` tree). This is the serving decision
from the design (§0/§4): a backend endpoint reading image-baked committed
notes, joined with the ``versions`` table for the release date + ordering.

Two precedents inform the shape:

* :mod:`backend.services.project_specs` — reads ``.md`` from disk with a
  path-traversal guard. Unlike that service this one takes **no** caller
  -supplied path (the leaf glob ``v*/RELEASE_NOTES.md`` is hard-coded), so
  there is no traversal surface; the ``.resolve()`` + ``relative_to`` guard
  is applied defensively anyway (a symlink planted inside a version dir
  cannot surface content from outside the docs tree).
* :func:`backend.api.routes.health.health_check` — public, no auth. The
  changelog is user-facing and carries no credentials.

Authority of the two data sources (design §4b):

* **File presence drives WHICH versions appear** — only version dirs that
  physically ship a ``RELEASE_NOTES.md`` are returned. Old versions without
  the file are simply skipped (no error).
* **The DB drives the release date** — joined by ``version_number``. When no
  matching row (or no ``release_date``) is found, the file mtime is used as a
  graceful fallback. The date is **never** parsed from the Markdown heading.
  A deployed generated app has its own ``versions`` table with only its own
  rows; NEX Studio's control-plane DB holds many projects' versions and no
  self-row, so the mtime fallback is the realistic path when it dogfoods.

Ordering is newest-first by a parsed numeric semver key so ``v0.10.0`` sorts
above ``v0.2.0`` — honouring the load-bearing "newest-first" requirement and
avoiding the lexicographic caveat that ``list_versions`` documents.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.versions import Version

#: Image-baked location of the per-version release notes. ``__file__`` is
#: ``<root>/backend/services/release_notes.py`` so ``parents[2]`` is the app
#: root (``/app`` in the image, the repo root in dev/tests). The Dockerfile
#: copies ``RELEASE_NOTES.md`` files into ``<root>/docs/specs/versions/v<X>/``.
DOCS_VERSIONS_ROOT = Path(__file__).resolve().parents[2] / "docs" / "specs" / "versions"

#: Version directory names: ``v`` followed by dot-separated integers
#: (``v0``, ``v0.9``, ``v0.9.0``). Anything else is ignored.
_VERSION_DIR_RE = re.compile(r"^v\d+(?:\.\d+)*$")


def list_release_notes(db: Session) -> list[dict]:
    """Return every version that ships a ``RELEASE_NOTES.md``, newest first.

    Args:
        db: Active SQLAlchemy session — used only to look up release dates by
            ``version_number``. A missing/unavailable row never fails the call.

    Returns:
        List of ``{"version", "released_at", "markdown"}`` dicts ordered
        newest-version-first. Empty when no version ships a notes file.
    """
    if not DOCS_VERSIONS_ROOT.is_dir():
        return []

    root = DOCS_VERSIONS_ROOT.resolve()
    discovered: list[tuple[str, Path, str]] = []
    for path in DOCS_VERSIONS_ROOT.glob("v*/RELEASE_NOTES.md"):
        resolved = path.resolve()
        # Defense in depth — the glob leaf is hard-coded with no caller input,
        # but reject anything that resolves outside the docs tree (e.g. a
        # symlinked version dir).
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        if not resolved.is_file():
            continue
        version = path.parent.name
        if not _VERSION_DIR_RE.match(version):
            continue
        try:
            markdown = resolved.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        discovered.append((version, resolved, markdown))

    if not discovered:
        return []

    release_dates = _release_dates_by_version(db, [v for v, _, _ in discovered])

    out: list[dict] = []
    for version, resolved, markdown in discovered:
        released = release_dates.get(version)
        if released is None:
            # mtime fallback — no released row in the DB for this version
            # number (e.g. NEX Studio is not a project in its own DB).
            released = date.fromtimestamp(resolved.stat().st_mtime)
        out.append(
            {
                "version": version,
                "released_at": released.isoformat() if released else None,
                "markdown": markdown,
            }
        )

    out.sort(key=lambda item: _version_sort_key(item["version"]), reverse=True)
    return out


def _release_dates_by_version(db: Session, version_numbers: list[str]) -> dict[str, date]:
    """Map ``version_number → release_date`` for the discovered versions.

    Only rows with a non-NULL ``release_date`` participate. Cross-project
    caveat: NEX Studio's control-plane DB holds many projects' versions, so the
    same ``version_number`` may appear more than once — keep the latest
    ``release_date`` deterministically. A generated app's own DB has a single
    project, so this collapses to an exact match.
    """
    if not version_numbers:
        return {}

    stmt = select(Version.version_number, Version.release_date).where(
        Version.version_number.in_(version_numbers),
        Version.release_date.is_not(None),
    )
    out: dict[str, date] = {}
    for version_number, release_date in db.execute(stmt).all():
        if release_date is None:
            continue
        existing = out.get(version_number)
        if existing is None or release_date > existing:
            out[version_number] = release_date
    return out


def _version_sort_key(version: str) -> tuple[int, ...]:
    """Parse ``"v0.9.0"`` → ``(0, 9, 0)`` for numeric newest-first ordering.

    Non-numeric components degrade to ``0`` (the ``_VERSION_DIR_RE`` filter
    already excludes non-version dirs, so this is purely defensive).
    """
    parts = version.lstrip("v").split(".")
    key: list[int] = []
    for part in parts:
        try:
            key.append(int(part))
        except ValueError:
            key.append(0)
    return tuple(key)
