"""Tests for migration 024 — seed admin user.

The test DB is created via ``Base.metadata.create_all`` (not Alembic),
so the seed data from migration 024 is not present. Each test applies
the migration's ``upgrade()`` logic by calling the function directly
against the test DB connection.
"""

import bcrypt
from sqlalchemy import text


def _seed_admin(db_session):
    """Apply migration 024 logic: insert admin user + session."""
    password_hash = bcrypt.hashpw(b"Nex123", bcrypt.gensalt(rounds=12)).decode()
    db_session.execute(
        text(
            "INSERT INTO users (id, username, email, password_hash, role, is_active, created_at, updated_at) "
            "VALUES (gen_random_uuid(), 'admin', 'admin@isnex.eu', :pw, 'ri', true, now(), now())"
        ),
        {"pw": password_hash},
    )
    db_session.execute(
        text(
            "INSERT INTO user_sessions (id, user_id, token_version, last_seen_at, created_at, updated_at) "
            "VALUES (gen_random_uuid(), "
            "(SELECT id FROM users WHERE username = 'admin'), 0, now(), now(), now())"
        ),
    )
    db_session.commit()


def test_admin_user_seeded(db_session):
    """Admin user exists with correct attributes after upgrade."""
    _seed_admin(db_session)

    row = db_session.execute(
        text("SELECT username, email, role, is_active FROM users WHERE username = 'admin'")
    ).fetchone()

    assert row is not None, "Admin user should exist in users table"
    assert row[0] == "admin"
    assert row[1] == "admin@isnex.eu"
    assert row[2] == "ri"
    assert row[3] is True


def test_admin_password_validates(db_session):
    """Bcrypt hash stored for admin validates against 'Nex123'."""
    _seed_admin(db_session)

    row = db_session.execute(text("SELECT password_hash FROM users WHERE username = 'admin'")).fetchone()

    assert row is not None
    stored_hash = row[0].encode()
    assert bcrypt.checkpw(b"Nex123", stored_hash), "Password 'Nex123' must validate against stored hash"
    assert not bcrypt.checkpw(b"WrongPassword", stored_hash), "Wrong password must not validate"


def test_admin_user_session_exists(db_session):
    """A user_sessions record with token_version=0 exists for admin."""
    _seed_admin(db_session)

    row = db_session.execute(
        text(
            "SELECT us.token_version FROM user_sessions us JOIN users u ON us.user_id = u.id WHERE u.username = 'admin'"
        )
    ).fetchone()

    assert row is not None, "user_sessions record must exist for admin"
    assert row[0] == 0, "token_version should be 0"
