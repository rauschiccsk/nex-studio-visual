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

from sqlalchemy.orm import Session

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


def get_allowed_kb_categories(user: User, db: Session) -> list[str]:
    """Return list of allowed KB category prefixes for a user.

    * ``ri`` users get ``["*"]`` (full access).
    * ``ha`` users get the configured ``ha`` list.
    * ``shu`` users get the configured ``shu`` baseline.

    The Knowledge Base is the ONE place the Shuhari roles still decide anything (Director, 2026-07-28);
    projects are governed by ownership and know nothing about roles. This function is therefore
    unchanged in intent — but a ``shu`` user no longer gains extra project folders, because the
    ``project_members`` table that granted them is gone with the ownership simplification: a junior
    works under his manager's login, so there is no second account to widen.

    The removal deliberately took the CALL SITE and the model together. The lookup used to sit inside a
    bare ``except Exception: logger.warning(...)``, so dropping the table while leaving the call would
    not have raised anything — a junior would simply have stopped seeing project documents, with one
    warning line as the only trace. There is nothing left to swallow.

    ``db`` is retained in the signature: every caller passes the request-scoped session, and the
    parameter is part of a widely-used contract that a future per-project KB rule would need again.

    Args:
        user: Authenticated user.
        db: Active SQLAlchemy session (unused today — see above).
    """
    role = (user.role or "shu").lower()
    return _kb_access_for_role(role)


def filter_kb_documents(documents: Iterable[dict], user: User, db: Session) -> list[dict]:
    """Filter KB documents by allowed categories for the user."""
    allowed = get_allowed_kb_categories(user, db)

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


def is_path_allowed(path: str, user: User, db: Session) -> bool:
    """Check whether a specific KB path is accessible to the user."""
    allowed = get_allowed_kb_categories(user, db)

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
