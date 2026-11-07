"""DEV-321 — migration 144 drops the unique e-mail of accounts and keeps the unique username.
Run on a throwaway PostgreSQL database: up to 143 the second account with the same e-mail is refused, after 144 it is
taken, and a second account with the same username is still refused."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from tests.test_alembic_migrations import (
    REPO_ROOT,
    _create_clean_database,
    _drop_database_if_exists,
    _get_test_database_url,
)

_INSERT = text(
    "INSERT INTO users (id, username, email, password_hash, role, created_at, updated_at) "
    "VALUES (:id, :u, :e, 'x', 'ri', now(), now())"
)


def _add(engine, username: str, email: str) -> None:
    with engine.begin() as conn:
        conn.execute(_INSERT, {"id": str(uuid.uuid4()), "u": username, "e": email})


def test_after_144_two_accounts_share_an_email_and_the_username_stays_unique(monkeypatch) -> None:
    from alembic import command
    from alembic.config import Config

    from backend.config import settings as settings_module

    base, name = _get_test_database_url().rsplit("/", 1)
    admin_url, db_name = base + "/postgres", name.split("?")[0] + "_m144"
    db_url = f"{base}/{db_name}"
    _create_clean_database(admin_url, db_name)
    monkeypatch.setattr(settings_module.settings, "database_url", db_url)
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    engine = None
    try:
        command.upgrade(config, "143")
        engine = create_engine(db_url)
        _add(engine, "first", "shared@example.com")
        with pytest.raises(IntegrityError, match="uq_users_email"):
            _add(engine, "second", "shared@example.com")
        engine.dispose()
        engine = None

        command.upgrade(config, "144")

        engine = create_engine(db_url)
        _add(engine, "second", "shared@example.com")
        with pytest.raises(IntegrityError, match="uq_users_username"):
            _add(engine, "second", "other@example.com")
        with engine.connect() as conn:
            got = conn.execute(text("SELECT username FROM users WHERE email = 'shared@example.com' ORDER BY username"))
            assert [row[0] for row in got] == ["first", "second"]
    finally:
        if engine is not None:
            engine.dispose()
        _drop_database_if_exists(admin_url, db_name)
