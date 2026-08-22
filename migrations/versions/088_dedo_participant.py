"""Widen the pipeline-message participant CHECKs for ``dedo`` (ICCINT-12).

The escalation path AI Agent → Dedo already works (``block_reason='framework_issue'`` + the
.dedo-channel inbox file + a Telegram ping). The way BACK did not exist: ``pipeline_message.author`` /
``recipient`` accepted only ``ai_agent`` / ``auditor`` / ``manazer`` / ``system``, so Dedo's answer had to
be retyped by a human into the Manažér's box — two windows, one lost audit trail.

This widens BOTH participant CHECKs to include ``'dedo'``:

  * ``ck_pipeline_message_author`` — Dedo can now AUTHOR a message.
  * ``ck_pipeline_message_recipient`` — and be ADDRESSED by one (the agent's reply back to him).

``dedo`` is deliberately NOT added to ``ck_pipeline_state_current_actor``: Dedo is never "on turn" — he
does not build, he only answers — so the state machine can never wait on him.

No column change (``author``/``recipient`` are already ``String(16)``; ``'dedo'`` fits), no data
migration. Idempotent: DROP CONSTRAINT IF EXISTS. ``downgrade`` narrows both CHECKs back to the pre-
ICCINT-12 list; any ``dedo``-authored/-addressed rows are deleted first, otherwise the narrowed CHECK
cannot be re-added at all (Postgres validates existing rows) and the downgrade would abort mid-way.
Mirrors migration 082 (``framework_issue`` block-reason widening).

Revision ID: 088
Revises: 087
Create Date: 2026-08-22

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "088"
down_revision: Union[str, None] = "087"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PARTICIPANTS_NEW = "ai_agent,auditor,manazer,system,dedo"
_PARTICIPANTS_OLD = "ai_agent,auditor,manazer,system"


def _in_list(csv: str) -> str:
    return ", ".join(f"'{v}'" for v in csv.split(","))


def _set_participants(values_csv: str) -> None:
    values = _in_list(values_csv)
    for column in ("author", "recipient"):
        op.execute(f"ALTER TABLE pipeline_message DROP CONSTRAINT IF EXISTS ck_pipeline_message_{column}")
        op.execute(
            f"ALTER TABLE pipeline_message ADD CONSTRAINT ck_pipeline_message_{column} CHECK ({column} IN ({values}))"
        )


def upgrade() -> None:
    _set_participants(_PARTICIPANTS_NEW)


def downgrade() -> None:
    # A narrowed CHECK is validated against existing rows, so Dedo's messages must go first — otherwise the
    # ADD CONSTRAINT fails and the database is left with NO participant CHECK at all.
    op.execute("DELETE FROM pipeline_message WHERE author = 'dedo' OR recipient = 'dedo'")
    _set_participants(_PARTICIPANTS_OLD)
