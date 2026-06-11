"""Add ``pipeline_state.returns_to`` (E7 route_to_designer, CR-NS-034).

A transient return marker for the build→Designer spec-fix round-trip: ``coordinator_route_to_designer``
dispatches the Designer mid-build to fix a spec problem and sets ``returns_to = 'build'`` so the
dispatch-completion handler returns to ``_run_build_round`` (re-attempting the task against the corrected
spec) instead of advancing a gate. Cleared on the Designer's DONE. Persisted (not in-memory like
``gate_e_dispatch``) because the route is an internal executor — the action route can't compute a
transient marker for it. Nullable, defaults NULL; no behavioural change until route_to_designer runs.

Revision ID: 059
Revises: 058
Create Date: 2026-06-11

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "059"
down_revision: Union[str, None] = "058"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("pipeline_state", sa.Column("returns_to", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("pipeline_state", "returns_to")
