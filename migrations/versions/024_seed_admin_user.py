"""Seed admin user (admin/Nex123) with user_session record.

Inserts a default admin user into the ``users`` table and creates an
associated ``user_sessions`` record with ``token_version = 0``.
The password is hashed with bcrypt at migration time.

Revision ID: 024
Revises: 023
Create Date: 2026-04-17

"""

from typing import Sequence, Union
from uuid import uuid4

import bcrypt
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "024"
down_revision: Union[str, None] = "023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ADMIN_USERNAME = "admin"
ADMIN_EMAIL = "admin@isnex.eu"
ADMIN_ROLE = "ri"


def upgrade() -> None:
    password_hash = bcrypt.hashpw(b"Nex123", bcrypt.gensalt(rounds=12)).decode()
    user_id = uuid4()
    session_id = uuid4()

    users = sa.table(
        "users",
        sa.column("id", sa.Uuid),
        sa.column("username", sa.String),
        sa.column("email", sa.String),
        sa.column("password_hash", sa.String),
        sa.column("role", sa.String),
        sa.column("is_active", sa.Boolean),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )

    user_sessions = sa.table(
        "user_sessions",
        sa.column("id", sa.Uuid),
        sa.column("user_id", sa.Uuid),
        sa.column("token_version", sa.Integer),
        sa.column("last_seen_at", sa.DateTime),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )

    now = sa.func.now()

    op.execute(
        users.insert().values(
            id=user_id,
            username=ADMIN_USERNAME,
            email=ADMIN_EMAIL,
            password_hash=password_hash,
            role=ADMIN_ROLE,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
    )

    op.execute(
        user_sessions.insert().values(
            id=session_id,
            user_id=user_id,
            token_version=0,
            last_seen_at=now,
            created_at=now,
            updated_at=now,
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text("DELETE FROM user_sessions WHERE user_id = (SELECT id FROM users WHERE username = :u)"),
        {"u": ADMIN_USERNAME},
    )
    conn.execute(
        sa.text("DELETE FROM users WHERE username = :u"),
        {"u": ADMIN_USERNAME},
    )
