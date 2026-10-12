"""A customer's values for the settings an app marks in its ``.env.example`` (DEV-72).

12.10.2026 career-asistent could not be deployed for a new customer with a working login: the cockpit had nowhere to
keep the e-mail and password of its first admin, or the customer's mailbox, and so nothing to hand the app. The plain
values live on the customer row; the secret ones in the credentials store, the row keeping only a pointer.

Revision ID: 116
Revises: 115
Create Date: 2026-10-12

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "116"
down_revision: Union[str, None] = "115"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("customers", sa.Column("settings", JSONB, nullable=True))
    op.add_column("customers", sa.Column("settings_credential_id", UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_customers_settings_credential_id",
        "customers",
        "credentials",
        ["settings_credential_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_customers_settings_credential_id", "customers", type_="foreignkey")
    op.drop_column("customers", "settings_credential_id")
    op.drop_column("customers", "settings")
