"""Widen the pipeline-message status CHECK for ``proposed`` (ICCINT-24).

Dedo's findings about a project reached the AI Agent only by the Director reading Dedo's text and RETYPING
it into the cockpit. The return leg built in ICCINT-12 does not help: everything it writes is ``pending``,
and ``pending`` means "the next agent turn carries this" — which is exactly what a finding about someone
else's healthy build must NOT do (ICCINT-14 §4.5: a Dedo message is not a note in a thread, it is the
directive that opens the agent's next prompt).

``proposed`` is the missing state in between: recorded, visible to the Manažér, and delivered to nobody.
The cockpit renders it as a one-click proposal; the Manažér edits it if he wants and presses send, and the
text then travels as HIS message through the ordinary ``uprav`` / ``answer`` / ``ask`` verbs, with every
guard those verbs already have. A rejected or sent proposal ends ``archived`` — it is never re-offered and
never becomes ``pending``.

No column change (``status`` is ``String(16)``; ``'proposed'`` fits), no data migration. Idempotent:
DROP CONSTRAINT IF EXISTS. ``downgrade`` narrows the CHECK back and must delete the ``proposed`` rows
first — Postgres validates existing rows when a CHECK is added, so leaving them would abort the downgrade
and leave the table with NO status CHECK at all. Deleting them loses nothing that was ever delivered: a
``proposed`` row is by definition one the agent never saw and the Manažér never sent. Mirrors migration 088
(the ``dedo`` participant widening).

Revision ID: 090
Revises: 089
Create Date: 2026-08-23

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "090"
down_revision: Union[str, None] = "089"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_STATUSES_NEW = "pending,delivered,answered,archived,proposed"
_STATUSES_OLD = "pending,delivered,answered,archived"


def _in_list(csv: str) -> str:
    return ", ".join(f"'{v}'" for v in csv.split(","))


def _set_statuses(values_csv: str) -> None:
    values = _in_list(values_csv)
    op.execute("ALTER TABLE pipeline_message DROP CONSTRAINT IF EXISTS ck_pipeline_message_status")
    op.execute(f"ALTER TABLE pipeline_message ADD CONSTRAINT ck_pipeline_message_status CHECK (status IN ({values}))")


def upgrade() -> None:
    _set_statuses(_STATUSES_NEW)


def downgrade() -> None:
    # A narrowed CHECK is validated against existing rows, so the un-sent proposals must go first.
    op.execute("DELETE FROM pipeline_message WHERE status = 'proposed'")
    _set_statuses(_STATUSES_OLD)
