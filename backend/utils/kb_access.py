"""Knowledge Base access filtering based on Shuhari role.

Ported 1:1 from NEX Command (`backend/utils/kb_access.py`) per Director
mandate 2026-05-07 (M2.C milestone of feature parity audit).

Adaptations for NEX Studio:

* AuthUser shape — NEX Studio's :class:`User` has flat ``role`` field
  ('ri' | 'ha' | 'shu'), not separate ``shuhari_phase``.
* Project membership stored in ``project_members`` table with FKs on
  ``users.id`` (UUID) and ``projects.id`` (UUID). NEX Command used a
  free-text ``username`` column; we resolve via ``user_id``.
* Settings — :data:`backend.config.settings.kb_access_{ri,ha,shu}`
  carry the per-role access matrix (NEX Command had a single
  ``KB_ACCESS`` dict).
"""

from __future__ import annotations

import logging
from typing import Iterable, Union

from backend.config.settings import settings
from backend.db.models.foundation import User

logger = logging.getLogger(__name__)


def _kb_access_for_role(role: str) -> list[str]:
    """Return the configured baseline access list for a Shuhari role."""
    role = (role or "shu").lower()
    if role == "ri":
        return list(settings.kb_access_ri)
    if role == "ha":
        return list(settings.kb_access_ha)
    return list(settings.kb_access_shu)


def get_allowed_kb_categories(user: User) -> list[str]:
    """Return list of allowed KB category prefixes for a user.

    * ``ri`` users get ``["*"]`` (full access).
    * ``ha`` users get the configured ``ha`` list.
    * ``shu`` users get the configured ``shu`` baseline plus their
      assigned project paths (resolved via ``project_members``).
    """
    role = (user.role or "shu").lower()
    allowed = _kb_access_for_role(role)

    if role == "shu" and "*" not in allowed:
        _add_assigned_projects(user, allowed)

    return allowed


def _add_assigned_projects(user: User, allowed: list[str]) -> None:
    """Append ``projects/<slug>/`` paths the user is a member of.

    Resolution: ``project_members`` JOIN ``projects`` on
    ``project_members.project_id = projects.id``, filter by
    ``user_id = user.id``.
    """
    try:
        from backend.db.models.project_member import ProjectMember
        from backend.db.models.projects import Project
        from backend.db.session import SessionLocal

        with SessionLocal() as db:
            rows = (
                db.query(Project.slug)
                .join(ProjectMember, ProjectMember.project_id == Project.id)
                .filter(ProjectMember.user_id == user.id)
                .filter(Project.slug.isnot(None))
                .all()
            )
            for (slug,) in rows:
                if not slug:
                    continue
                project_path = f"projects/{slug}/"
                if project_path not in allowed:
                    allowed.append(project_path)
    except Exception as exc:
        logger.warning("Failed to load assigned projects for %s: %s", user.username, exc)


def filter_kb_documents(documents: Iterable[dict], user: User) -> list[dict]:
    """Filter KB documents by allowed categories for the user.

    Documents are expected to be dicts with keys like ``relative_path``
    or ``category`` indicating where the doc lives in the KB tree.
    """
    allowed = get_allowed_kb_categories(user)

    if "*" in allowed:
        return list(documents)

    filtered = []
    for doc in documents:
        doc_path = _extract_doc_path(doc)
        for allowed_cat in allowed:
            if doc_path.startswith(allowed_cat):
                filtered.append(doc)
                break
    return filtered


def is_path_allowed(path: str, user: User) -> bool:
    """Check whether a specific KB path is accessible to the user."""
    allowed = get_allowed_kb_categories(user)

    if "*" in allowed:
        return True

    return any(path.startswith(cat) for cat in allowed)


def _extract_doc_path(doc: Union[dict, object]) -> str:
    """Extract a path-like string from various document representations."""
    if isinstance(doc, dict):
        return (
            doc.get("relative_path", "")
            or doc.get("file_path", "")
            or doc.get("source_file", "")
            or doc.get("category", "")
        )
    for attr in ("relative_path", "file_path", "source_file", "category"):
        val = getattr(doc, attr, None)
        if val:
            return str(val)
    return ""
