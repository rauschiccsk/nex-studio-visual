"""DEV-320 — migration 143 gives the accounts that exist the palette in the order they were created, as a new account
takes the next colour. Run on a throwaway PostgreSQL database: up to 142, nine accounts, then 143."""

from __future__ import annotations

import uuid

from sqlalchemy import create_engine, text

from backend.config.settings import ACCOUNT_COLORS
from tests.test_alembic_migrations import (
    REPO_ROOT,
    _create_clean_database,
    _drop_database_if_exists,
    _get_test_database_url,
)


def test_existing_accounts_take_the_palette_in_the_order_they_were_created(monkeypatch) -> None:
    from alembic import command
    from alembic.config import Config

    from backend.config import settings as settings_module

    base, name = _get_test_database_url().rsplit("/", 1)
    admin_url, db_name = base + "/postgres", name.split("?")[0] + "_m143"
    db_url = f"{base}/{db_name}"
    _create_clean_database(admin_url, db_name)
    monkeypatch.setattr(settings_module.settings, "database_url", db_url)
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    engine = None
    try:
        command.upgrade(config, "142")
        engine = create_engine(db_url)
        created = [f"2026-0{1 + i // 5}-1{i % 5} 10:00:00+00" for i in range(9)]
        with engine.begin() as conn:
            for i, when in reversed(list(enumerate(created))):  # inserted out of order on purpose
                conn.execute(
                    text(
                        "INSERT INTO users (id, username, email, password_hash, role, created_at, updated_at) "
                        "VALUES (:id, :u, :e, 'x', 'ri', :t, :t)"
                    ),
                    {"id": str(uuid.uuid4()), "u": f"u{i}", "e": f"u{i}@example.com", "t": when},
                )
        engine.dispose()
        engine = None

        command.upgrade(config, "143")

        engine = create_engine(db_url)
        with engine.connect() as conn:
            got = conn.execute(text("SELECT username, accent_color FROM users ORDER BY created_at, id")).all()
        # The chain seeds the admin account itself (created now, so last); nine accounts and it wrap the palette once.
        assert [username for username, _ in got] == [f"u{i}" for i in range(9)] + ["admin"]
        assert [colour for _, colour in got] == [ACCOUNT_COLORS[i % len(ACCOUNT_COLORS)] for i in range(len(got))]
    finally:
        if engine is not None:
            engine.dispose()
        _drop_database_if_exists(admin_url, db_name)
