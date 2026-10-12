"""Which commit wrote each delivered line of a version — kept once per (base, delivered) pair (DEV-75).

``git blame`` of the delivered state takes seconds per version (NEX Inbox 1.7.0: 136 files, 1.9 s), and the billing
tab counts every version at once. Commits never change, so the answer for a pair of commits never does: it is computed
once and kept. The payload is ``{"rule": n, "files": {path: [[commit sha, lines in a row], ...]}}`` — only who wrote
each added line, in the diff's order (:func:`backend.services.delivered_tokens.line_commits`); which task a commit
belongs to, what a file counts as and its tokens are read anew, by the rules of the running cockpit. A payload made
by another ``rule`` is computed again.
"""

from __future__ import annotations

from sqlalchemy import TIMESTAMP, Column, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID

from backend.db.models.base import Base, UUIDMixin


class DeliveredAuthorship(Base, UUIDMixin):
    __tablename__ = "delivered_authorship"

    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    base_sha = Column(String(40), nullable=False)
    head_sha = Column(String(40), nullable=False)
    payload = Column(JSONB, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (UniqueConstraint("project_id", "base_sha", "head_sha", name="uq_delivered_authorship_range"),)
