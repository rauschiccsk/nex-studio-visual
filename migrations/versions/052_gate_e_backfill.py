"""Backfill historical Gate E dialogue into pipeline_message (F-007, CR-NS-018 Phase 1).

Data-only migration. Copies every ``dialogue_messages`` row (whose session
is linked to a version) into ``pipeline_message`` as ``stage='gate_e'``, so
the unified audit trail carries the existing Gate E exchange. The live Gate E
path (``dialogue_*`` tables + ``/dialogue``) is left untouched — cutover and
the ``dialogue_*`` drop happen in Phase 5.

Mapping (dialogue → pipeline_message):
* version_id : ``dialogue_sessions.version_id`` (rows with NULL are SKIPPED +
  counted — they predate version linkage).
* author     : copied (customer / designer / director — all valid).
* recipient  : the other party (customer↔designer; director→designer).
* kind       : customer→question, designer→answer, director→directive.
* status     : delivered/approved→delivered, rejected→archived, else pending.
* created_at : preserved.

Idempotent — re-running is a no-op once any ``stage='gate_e'`` row exists.

Revision ID: 052
Revises: 051
Create Date: 2026-06-03

"""

import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "052"
down_revision: Union[str, None] = "051"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

_BACKFILL_SQL = sa.text(
    """
    INSERT INTO pipeline_message
        (id, version_id, stage, author, recipient, kind, content, status, payload, created_at)
    SELECT
        gen_random_uuid(),
        s.version_id,
        'gate_e',
        m.author,
        CASE m.author
            WHEN 'customer' THEN 'designer'
            WHEN 'designer' THEN 'customer'
            ELSE 'designer'
        END,
        CASE m.author
            WHEN 'customer' THEN 'question'
            WHEN 'designer' THEN 'answer'
            ELSE 'directive'
        END,
        m.content,
        CASE m.status
            WHEN 'delivered' THEN 'delivered'
            WHEN 'approved' THEN 'delivered'
            WHEN 'rejected' THEN 'archived'
            ELSE 'pending'
        END,
        NULL,
        m.created_at
    FROM dialogue_messages m
    JOIN dialogue_sessions s ON s.id = m.session_id
    WHERE s.version_id IS NOT NULL
    """
)

_SKIP_COUNT_SQL = sa.text(
    """
    SELECT count(*) FROM dialogue_messages m
    JOIN dialogue_sessions s ON s.id = m.session_id
    WHERE s.version_id IS NULL
    """
)

_EXISTING_SQL = sa.text("SELECT count(*) FROM pipeline_message WHERE stage = 'gate_e'")


def _run_backfill(bind) -> dict[str, int]:
    """Copy Gate E dialogue into pipeline_message. Returns inserted/skipped counts.

    Idempotent: if any ``stage='gate_e'`` pipeline_message already exists, this
    is a no-op (returns zero counts). Importable so the migration test can
    exercise the exact transform against seeded data.
    """
    existing = bind.execute(_EXISTING_SQL).scalar() or 0
    if existing > 0:
        return {"inserted": 0, "skipped": 0}

    skipped = bind.execute(_SKIP_COUNT_SQL).scalar() or 0
    result = bind.execute(_BACKFILL_SQL)
    inserted = result.rowcount if result.rowcount is not None and result.rowcount >= 0 else 0
    return {"inserted": inserted, "skipped": skipped}


def upgrade() -> None:
    counts = _run_backfill(op.get_bind())
    logger.info(
        "Gate E backfill: %d dialogue messages copied to pipeline_message, %d skipped (no version_id)",
        counts["inserted"],
        counts["skipped"],
    )


def downgrade() -> None:
    # Remove only the backfilled Gate E rows (leaves any cockpit-authored
    # gate_e messages from later phases intact would be wrong here — in Phase 1
    # the only gate_e rows are the backfill, so this cleanly reverses it).
    op.execute("DELETE FROM pipeline_message WHERE stage = 'gate_e'")
