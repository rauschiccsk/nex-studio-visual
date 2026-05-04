"""Extend kb_documents.doc_category CHECK to cover filesystem layout.

Original CHECK (migration X) allowed only 7 values mapped to NEX Studio
pipeline artefacts: standards, decisions, lessons, patterns, design,
behavior, session. The KB at ``/home/icc/knowledge/`` has more top-level
categories (icc/, infrastructure/, customers/, shuhari/, templates/,
service-manuals/, deployment/, quarantine/, credentials/) plus per-
project subdirs (projects/<slug>/{STATUS,HISTORY,ARCHITECT,...}.md).

Phase A initial seed (kb_sync service) needs to register every markdown
file under /home/icc/knowledge/ so the KB UI lists them. This migration
extends the CHECK constraint with the filesystem-derived categories
without touching the existing 7 (some may be repurposed at write time
via mapping logic — e.g. /icc/DECISIONS.md → 'decisions').

Revision ID: 037
Revises: 036
Create Date: 2026-05-04
"""

from typing import Sequence, Union

from alembic import op

revision: str = "037"
down_revision: Union[str, None] = "036"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_OLD_CATEGORIES = (
    "standards",
    "decisions",
    "lessons",
    "patterns",
    "design",
    "behavior",
    "session",
)

_NEW_CATEGORIES = _OLD_CATEGORIES + (
    "icc",
    "infrastructure",
    "customers",
    "shuhari",
    "templates",
    "service-manuals",
    "deployment",
    "quarantine",
    "credentials",
    "project-status",
    "project-history",
    "project-architect",
    "project-other",
)


def _check_clause(values: Sequence[str]) -> str:
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"doc_category IN ({quoted})"


def upgrade() -> None:
    op.drop_constraint("ck_kb_documents_doc_category", "kb_documents", type_="check")
    op.create_check_constraint(
        "ck_kb_documents_doc_category",
        "kb_documents",
        _check_clause(_NEW_CATEGORIES),
    )


def downgrade() -> None:
    op.drop_constraint("ck_kb_documents_doc_category", "kb_documents", type_="check")
    op.create_check_constraint(
        "ck_kb_documents_doc_category",
        "kb_documents",
        _check_clause(_OLD_CATEGORIES),
    )
