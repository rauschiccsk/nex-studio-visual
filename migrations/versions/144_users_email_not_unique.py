"""One e-mail on several accounts (DEV-321).

**What happened.** The Director has several accounts in the cockpit (zoltan, rausch, admin), all his own, and wants
his e-mail on each (07.11.2026: „pre viac používateľov môžem zadať ten istý email, toto budem chcieť aj pre kokpit“).
The cockpit refused it with ``uq_users_email``. Sign-in is by username and nobody is looked up by e-mail, so the
e-mail need not be unique.

**What it changes.** Drops ``uq_users_email``. ``uq_users_username`` stays.

**Downgrade** puts the unique e-mail back; it fails while two accounts share one e-mail.

Revision ID: 144
Revises: 143
Create Date: 2026-11-07
"""

from typing import Sequence, Union

from alembic import op

revision: str = "144"
down_revision: Union[str, None] = "143"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("uq_users_email", "users", type_="unique")


def downgrade() -> None:
    op.create_unique_constraint("uq_users_email", "users", ["email"])
