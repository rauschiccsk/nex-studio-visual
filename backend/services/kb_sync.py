"""Filesystem ↔ kb_documents synchronization service.

Phase A — initial seed: walk ``/home/icc/knowledge/`` recursively and
register every markdown file in the ``kb_documents`` table so the KB UI
lists them. Idempotent — uses ``ON CONFLICT DO NOTHING`` on the
``(file_path)`` unique key (added implicitly by checking existence
before insert), so repeated runs are safe and a no-op when nothing
new appeared.

Phase B — real-time watchdog (planned): a separate module
:mod:`backend.services.kb_watcher` will monitor the same root for
``created`` / ``modified`` / ``deleted`` / ``moved`` events and keep
``kb_documents`` in sync.

Categorisation rules (path → doc_category):

============================================ =====================
Filesystem path                               doc_category
============================================ =====================
``/icc/ICC_STANDARDS.md``                    ``standards``
``/icc/DECISIONS.md``                        ``decisions``
``/icc/LESSONS_LEARNED.md``                  ``lessons``
``/icc/PROJECT_PATTERNS.md``                 ``patterns``
``/icc/<other>.md``                          ``icc``
``/infrastructure/...md``                    ``infrastructure``
``/customers/...md``                         ``customers``
``/shuhari/...md``                           ``shuhari``
``/templates/...md``                         ``templates``
``/service-manuals/...md``                   ``service-manuals``
``/deployment/...md``                        ``deployment``
``/quarantine/...md``                        ``quarantine``
``/credentials/...md``                       ``credentials`` (no read)
``/projects/<slug>/STATUS.md``               ``project-status``
``/projects/<slug>/HISTORY.md``              ``project-history``
``/projects/<slug>/ARCHITECT.md``            ``project-architect``
``/projects/<slug>/<other>.md``              ``project-other``
============================================ =====================

Per CLAUDE.md §13: files under ``credentials/`` are registered with
``title=<filename without extension>`` and **the file is never opened**
— content is not read for title extraction. All other categories use
the first ``# Heading`` from the markdown body as title, falling back
to the filename if no heading is present.

Per the kb_documents schema, ``project_id`` is set when the path is
``projects/<slug>/...`` and the slug resolves to an existing
``projects.slug`` row; otherwise NULL (ICC-wide doc).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.kb import KbDocument
from backend.db.models.projects import Project

logger = logging.getLogger(__name__)


KB_ROOT = Path("/home/icc/knowledge")

# Top-level KB filenames whose category is fixed by name (not by parent
# directory). When a file matches this map, the listed category is
# used unconditionally — overrides the parent-directory heuristic.
_ICC_FIXED_CATEGORIES: dict[str, str] = {
    "ICC_STANDARDS.md": "standards",
    "DECISIONS.md": "decisions",
    "LESSONS_LEARNED.md": "lessons",
    "PROJECT_PATTERNS.md": "patterns",
}

# Per-project filename → category mapping. ``projects/<slug>/<file>``.
_PROJECT_FIXED_CATEGORIES: dict[str, str] = {
    "STATUS.md": "project-status",
    "HISTORY.md": "project-history",
    "ARCHITECT.md": "project-architect",
}

# Top-level directory → default category when no fixed-name match.
_DIR_CATEGORY: dict[str, str] = {
    "icc": "icc",
    "infrastructure": "infrastructure",
    "customers": "customers",
    "shuhari": "shuhari",
    "templates": "templates",
    "service-manuals": "service-manuals",
    "deployment": "deployment",
    "quarantine": "quarantine",
    "credentials": "credentials",
}

# First-Markdown-heading regex (#H1 only, optionally with leading spaces).
_HEADING_RE = re.compile(r"^\s*#\s+(.+?)\s*$")


@dataclass(frozen=True)
class SeedResult:
    """Summary of one seed pass."""

    scanned: int
    inserted: int
    skipped_existing: int
    errors: int


def _categorise(rel_path: Path) -> tuple[str, Optional[str]]:
    """Return (doc_category, project_slug) for a path relative to KB_ROOT.

    ``project_slug`` is non-None only for files under ``projects/<slug>/``;
    the caller looks the slug up against the ``projects`` table.
    """
    parts = rel_path.parts
    if not parts:
        # KB_ROOT root-level files (e.g. README.md) — index under 'icc'.
        return "icc", None

    top = parts[0]
    name = rel_path.name

    if top == "projects" and len(parts) >= 2:
        slug = parts[1]
        category = _PROJECT_FIXED_CATEGORIES.get(name, "project-other")
        return category, slug

    if top == "icc" and name in _ICC_FIXED_CATEGORIES:
        return _ICC_FIXED_CATEGORIES[name], None

    if top in _DIR_CATEGORY:
        return _DIR_CATEGORY[top], None

    # Unknown top-level directory — fall back to 'icc' to keep the
    # CHECK constraint happy. Operator can re-categorise via UI.
    logger.warning("kb_sync: unknown top-level directory %r — falling back to 'icc'", top)
    return "icc", None


def _extract_title(file_path: Path, *, read_content: bool) -> str:
    """Return the document title.

    For files where ``read_content=False`` (credentials/) — title is
    derived from the filename without extension. The file is never
    opened. Per CLAUDE.md §13.

    For everything else — open the file and look for the first ``#``
    H1 heading. Falls back to filename-without-extension when no
    heading is present (binary-only / empty / non-conformant markdown).
    """
    fallback = file_path.stem
    if not read_content:
        return fallback

    try:
        with file_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                # Skip blank lines + frontmatter blocks (--- delimiters)
                # heuristically — only honour the first H1 we see.
                stripped = line.strip()
                if not stripped or stripped == "---":
                    continue
                match = _HEADING_RE.match(line)
                if match:
                    title = match.group(1).strip()
                    # Trim title to the column constraint (500 chars).
                    return title[:500] if title else fallback
                # First non-empty, non-frontmatter line that's not an
                # H1 — give up on heading extraction (the file may
                # start with prose or a different heading level).
                break
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning(
            "kb_sync: could not read %s for title extraction (%s) — using filename",
            file_path,
            exc,
        )
    return fallback


def _resolve_project_id(db: Session, slug: Optional[str]) -> Optional[str]:
    """Look up ``projects.id`` by slug. Returns None when slug is None or unknown."""
    if not slug:
        return None
    row = db.execute(select(Project.id).where(Project.slug == slug)).scalar_one_or_none()
    return row


def _existing_paths(db: Session) -> set[str]:
    """Snapshot every ``file_path`` already present in ``kb_documents``."""
    rows = db.execute(select(KbDocument.file_path)).all()
    return {row[0] for row in rows}


def seed_from_filesystem(db: Session, *, root: Path = KB_ROOT) -> SeedResult:
    """Walk ``root`` recursively and INSERT one ``kb_documents`` row per .md.

    Idempotent — files whose ``file_path`` already exists in the table
    are skipped. ``project_id`` is resolved by slug match; failed
    resolution leaves it NULL (operator may patch later via UI / API).

    Args:
        db: Active session. Caller commits.
        root: KB filesystem root (default :data:`KB_ROOT`).

    Returns:
        :class:`SeedResult` with per-pass counters for logging.
    """
    if not root.exists():
        logger.error("kb_sync: KB_ROOT %s does not exist — nothing to seed", root)
        return SeedResult(scanned=0, inserted=0, skipped_existing=0, errors=0)

    existing = _existing_paths(db)
    scanned = 0
    inserted = 0
    skipped_existing = 0
    errors = 0

    for path in root.rglob("*.md"):
        scanned += 1
        try:
            file_path_str = str(path)
            if file_path_str in existing:
                skipped_existing += 1
                continue

            rel = path.relative_to(root)
            category, slug = _categorise(rel)
            read_content = category != "credentials"
            title = _extract_title(path, read_content=read_content)
            project_id = _resolve_project_id(db, slug)

            row = KbDocument(
                project_id=project_id,
                module_id=None,
                title=title,
                file_path=file_path_str,
                doc_category=category,
            )
            db.add(row)
            db.flush()
            inserted += 1
        except Exception:
            errors += 1
            logger.exception("kb_sync: failed to register %s", path)
            db.rollback()
            # Re-fetch existing so subsequent rows in this pass don't
            # hit the same path twice after a rollback wiped session
            # state. (Edge case: rare in normal seed.)
            existing = _existing_paths(db)

    logger.info(
        "kb_sync: seed complete — scanned=%d inserted=%d skipped_existing=%d errors=%d",
        scanned,
        inserted,
        skipped_existing,
        errors,
    )
    return SeedResult(
        scanned=scanned,
        inserted=inserted,
        skipped_existing=skipped_existing,
        errors=errors,
    )
