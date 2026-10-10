"""The cockpit's database answers in the Director's local time (DEV-59).

Director 10.10.2026: „V kokpite má byť také isté časové pásmo ako tu u mňa, veď to robíme na jednom serveri.“ Every
time column stores the instant with its zone (timestamp with time zone — 60 of them, none without), so the stored
times do not change; what changes is the zone a new session reads them in — the one in
``settings.display_timezone``. Existing sessions keep theirs until they reconnect (the backend starts after this).

Revision ID: 114
Revises: 113
Create Date: 2026-10-10

"""

from typing import Sequence, Union
from zoneinfo import ZoneInfo

from alembic import op

from backend.config.settings import settings

# revision identifiers, used by Alembic.
revision: str = "114"
down_revision: Union[str, None] = "113"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    zone = settings.display_timezone
    ZoneInfo(zone)  # a zone Python does not know stops the migration here, not in PostgreSQL's hands
    quoted = zone.replace("'", "''")
    op.execute(
        f"DO $$ BEGIN EXECUTE format('ALTER DATABASE %I SET timezone TO %L', current_database(), '{quoted}'); END $$;"
    )


def downgrade() -> None:
    op.execute("DO $$ BEGIN EXECUTE format('ALTER DATABASE %I RESET timezone', current_database()); END $$;")
