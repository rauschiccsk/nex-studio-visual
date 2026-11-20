"""The Pravidlá agenta a session last received (DEV-448).

**What happened.** 20.11.2026 the cockpit 4.160.0 added a rule to the Staviteľ's charter; the Staviteľ of Orthodox
Hermenia 0.2.0 kept working without it — a charter reaches the agent only when its session starts, and one session
serves a (project, role) across versions.

**What it changes.** Adds ``orchestrator_session.charter_sha256``: the fingerprint of the charter the session last
received. A turn whose role's charter differs is given the current text once. Empty for sessions from before — they
receive the current text at their next turn.

**Downgrade** drops the column.

Revision ID: 152
Revises: 151
Create Date: 2026-11-20
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "152"
down_revision: Union[str, None] = "151"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("orchestrator_session", sa.Column("charter_sha256", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("orchestrator_session", "charter_sha256")
