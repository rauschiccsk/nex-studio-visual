"""DEV-59 — the cockpit keeps the Director's time, not UTC.

Director 10.10.2026, after a time read from the cockpit's database reached him in UTC (18:22:58 for his 20:22:58):
„V kokpite má byť také isté časové pásmo ako tu u mňa, veď to robíme na jednom serveri.“ People get local time —
whatever the backend writes for them goes through ``local_time``, the database answers in local time to whoever
reads it directly, the containers run in the same zone. The cockpit itself computes and exchanges in UTC (its
connections are pinned), so its API never mixes offsets.
"""

from __future__ import annotations

import ast
import asyncio
import os
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import create_engine, text

from backend.config.settings import settings
from backend.core.local_time import local_time
from backend.services.poradca import tools as poradca_tools

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("utc", "local"),
    [
        (datetime(2026, 10, 10, 18, 22, 58, tzinfo=timezone.utc), "10.10.2026 20:22:58"),  # summer time, +2
        (datetime(2026, 1, 15, 9, 0, 0, tzinfo=timezone.utc), "15.01.2026 10:00:00"),  # winter time, +1
        (datetime(2026, 10, 10, 18, 22, 58), "10.10.2026 20:22:58"),  # no zone = UTC, as the cockpit stores
    ],
)
def test_a_time_for_people_is_on_the_local_clock(utc, local):
    assert local_time(utc).strftime("%d.%m.%Y %H:%M:%S") == local


def test_the_database_answers_people_in_local_time_and_the_cockpit_computes_in_utc(db_session):
    default = (
        db_session.execute(
            text(
                "SELECT unnest(setconfig) FROM pg_db_role_setting "
                "WHERE setdatabase = (SELECT oid FROM pg_database WHERE datname = current_database()) AND setrole = 0"
            )
        )
        .scalars()
        .all()
    )
    assert f"TimeZone={settings.display_timezone}" in default, "databáza nemá miestne pásmo ako predvolené"
    assert db_session.execute(text("SHOW timezone")).scalar() == "UTC", "spojenie kokpitu nie je pripnuté na UTC"

    stored = db_session.execute(text("SELECT TIMESTAMPTZ '2026-10-10 18:22:58+00'")).scalar()
    assert stored.utcoffset().total_seconds() == 0 and stored.hour == 18


def test_migration_114_sets_the_default_zone_and_takes_it_back(monkeypatch):
    from tests.test_migration_versions import (
        _alembic_config,
        _create_clean_database,
        _drop_database_if_exists,
        _get_test_database_url,
    )

    base_url = _get_test_database_url()
    parts = base_url.rsplit("/", 1)
    admin_url = parts[0] + "/postgres"
    db_name = parts[1].split("?")[0] + "_mig114"
    db_url = parts[0] + "/" + db_name
    _create_clean_database(admin_url, db_name)
    engine = create_engine(db_url)

    def _default() -> list[str]:
        with engine.connect() as conn:
            return (
                conn.execute(
                    text(
                        "SELECT unnest(setconfig) FROM pg_db_role_setting WHERE setdatabase = "
                        "(SELECT oid FROM pg_database WHERE datname = current_database()) AND setrole = 0"
                    )
                )
                .scalars()
                .all()
            )

    try:
        config = _alembic_config(db_url, monkeypatch)
        command.upgrade(config, "113")
        assert not any(s.startswith("TimeZone=") for s in _default())
        command.upgrade(config, "114")
        assert f"TimeZone={settings.display_timezone}" in _default()
        command.downgrade(config, "113")
        assert not any(s.startswith("TimeZone=") for s in _default())
    finally:
        engine.dispose()
        _drop_database_if_exists(admin_url, db_name)


def test_poradcas_git_history_is_on_the_local_clock(tmp_path):
    env = dict(
        os.environ,
        GIT_AUTHOR_NAME="Agent",
        GIT_AUTHOR_EMAIL="a@t",
        GIT_COMMITTER_NAME="Agent",
        GIT_COMMITTER_EMAIL="a@t",
        GIT_AUTHOR_DATE="2026-07-07T14:05:00+00:00",
        GIT_COMMITTER_DATE="2026-07-07T14:05:00+00:00",
        TZ="UTC",  # whatever zone the process runs in, the tool puts the time on the local clock itself
    )
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, env=env)
    (tmp_path / "a.txt").write_text("x")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, env=env)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "Oprava faktúry"], check=True, env=env)
    tool = poradca_tools.PoradcaTools(project_id=uuid.uuid4(), version_id=None, user_id=uuid.uuid4())
    tool._project_dir = lambda _db: str(tmp_path)  # type: ignore[method-assign]

    out = asyncio.run(tool.git_historia({}))

    assert out.split("  ")[1:] == ["07.07.2026 16:05", "Agent", "Oprava faktúry"]


def test_every_time_the_backend_formats_is_local_or_says_it_is_utc():
    """The rule, not a list: every ``strftime`` in the backend formats either a time put on the local clock by
    ``local_time(...)`` (for people) or a machine stamp that names its zone (``…Z`` / ``%z`` / ``%Z``)."""
    found, wrong = 0, []
    for path in sorted((REPO / "backend").rglob("*.py")):
        if "tests" in path.relative_to(REPO).parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (
                isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "strftime"
            ):
                continue
            found += 1
            receiver = node.func.value
            local = isinstance(receiver, ast.Call) and getattr(receiver.func, "id", None) == "local_time"
            fmt = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) else ""
            names_zone = isinstance(fmt, str) and (fmt.endswith("Z") or "%z" in fmt or "%Z" in fmt)
            if not (local or names_zone):
                wrong.append(f"{path.relative_to(REPO)}:{node.lineno}")
    assert found >= 5, "stráž nenašla formátovanie času — hľadá zle"
    assert wrong == []
