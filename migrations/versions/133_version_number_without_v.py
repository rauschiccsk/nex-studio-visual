"""A version number is ``major.minor.patch`` — the first PROD version is 1.0.0, not v1.0.0 (DEV-170).

Every version number was bare (``0.1.0``, ``1.1.0``) except the one a first PROD deploy renamed to: ``v1.0.0``. The
cockpit adds the ``v`` itself where one belongs, so for that version the Špecifikácia looked in
``docs/specs/versions/vv1.0.0`` and the deploy comments in Plane said ``vv1.0.0``. On 21.10.2026 Career Asistent and
NEX Websites each had a ``v1.0.0`` and the deploy log ten rows of it. Director: „Súhlasím“ — the twelve rows are
renamed here, and from now on the database takes no other shape of a number.

The rename stops before changing anything when a project already has the bare number its ``v`` number would become —
two versions would then claim one number (measured 21.10.2026: neither project has a 1.0.0).

Revision ID: 133
Revises: 132
Create Date: 2026-10-22

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "133"
down_revision: Union[str, None] = "132"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: The shape every version number has from now on — ``backend.db.models.versions.VERSION_NUMBER_PATTERN``.
NUMBER = r"^[0-9]+\.[0-9]+\.[0-9]+$"
#: The shape the first PROD deploy gave until DEV-170.
WITH_V = r"^v[0-9]+\.[0-9]+\.[0-9]+$"
#: Every table that keeps a version number — the versions and the deploy log, its refusals (DEV-181) included.
TABLES = ("versions", "deploy_events", "deploy_refusals")


def drop_the_v(bind) -> None:
    """Rename every ``vX.Y.Z`` to ``X.Y.Z`` in the versions and the deploy log — or nothing, when a project already has
    the number one of them would become."""
    taken = (
        bind.execute(
            sa.text(
                "SELECT prefixed.version_number FROM versions AS prefixed JOIN versions AS bare "
                "ON bare.project_id = prefixed.project_id AND bare.version_number = substr(prefixed.version_number, 2) "
                "WHERE prefixed.version_number ~ :with_v"
            ),
            {"with_v": WITH_V},
        )
        .scalars()
        .all()
    )
    if taken:
        raise RuntimeError(
            f"DEV-170: these versions cannot drop their 'v' — the project already has the bare number: {sorted(taken)}"
        )
    for table in TABLES:
        bind.execute(
            sa.text(f"UPDATE {table} SET version_number = substr(version_number, 2) WHERE version_number ~ :with_v"),
            {"with_v": WITH_V},
        )


def upgrade() -> None:
    drop_the_v(op.get_bind())
    for table in TABLES:
        op.create_check_constraint(f"ck_{table}_version_number", table, f"version_number ~ '{NUMBER}'")


def downgrade() -> None:
    # The renamed numbers stay bare — every reader of a version number takes it without the ``v``.
    for table in reversed(TABLES):
        op.drop_constraint(f"ck_{table}_version_number", table, type_="check")
