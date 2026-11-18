"""DEV-407 — every service of a new installation connects to its database with the installation's settings.

16.11.2026 the deploy rehearsal of NEX Inbox 1.10.4 made a new installation from the compose of 1.10.3: the database got
the installation's password, ``migrate`` kept the one written into the project's compose and could not log in
(„password authentication failed for user "nex_inbox"“). Director 16.11.2026: „Súhlasím, priprav na to tiket“;
18.11.2026 in the batch with DEV-425.
"""

from __future__ import annotations

import pytest
import yaml

from backend.services import uat_provisioner as P

#: NEX Inbox 1.10.3's compose as far as the database goes — the password written into the project, values as there.
PROJECT = {
    "services": {
        "db": {
            "image": "postgres:16-alpine",
            "environment": {"POSTGRES_DB": "nex_inbox", "POSTGRES_USER": "nex_inbox", "POSTGRES_PASSWORD": "dev_pw"},
        },
        "migrate": {
            "build": {"context": "./backend"},
            "command": "alembic upgrade head",
            "env_file": [".env"],
            "environment": {
                "DB_HOST": "db",
                "DB_PORT": "5432",
                "DB_NAME": "nex_inbox",
                "DB_USER": "nex_inbox",
                "DB_PASSWORD": "dev_pw",
            },
            "depends_on": ["db"],
        },
        "backend": {
            "build": {"context": "./backend"},
            "env_file": [".env"],
            "environment": ["DB_HOST=db", "DB_PASSWORD=dev_pw", "LOG_LEVEL=info"],
            "depends_on": ["migrate"],
        },
        "frontend": {"build": {"context": "./frontend"}, "depends_on": ["backend"]},
    }
}
EXAMPLE = "DB_HOST=localhost\nDB_PORT=5432\nDB_NAME=nex_inbox\nDB_USER=nex_inbox\nDB_PASSWORD=change-me\n"


@pytest.fixture
def installation(tmp_path, monkeypatch) -> tuple[dict, dict[str, str]]:
    project = tmp_path / "projects" / "nex-inbox"
    for d in ("backend", "frontend"):
        (project / d).mkdir(parents=True)
    (project / "docker-compose.yml").write_text(yaml.safe_dump(PROJECT), encoding="utf-8")
    (project / ".env.example").write_text(EXAMPLE, encoding="utf-8")
    monkeypatch.setattr(P, "_docker_image_exists", lambda image: True)
    result = P.provision_uat(
        "nex-inbox",
        "mager",
        projects_root=tmp_path / "projects",
        uat_root=tmp_path / "uat",
        customer_slug="mager",
        app="inbox",
        full_project_slug="nex-inbox",
        version="1.10.4",
    )
    return yaml.safe_load(result.compose_path.read_text(encoding="utf-8")), P._parse_env_file(result.env_path)


def _effective(svc: dict, env: dict[str, str]) -> dict[str, str]:
    """What the service runs with: its ``environment`` with ``${KEY}`` read from the installation's ``.env``."""
    items = svc.get("environment") or {}
    pairs = items.items() if isinstance(items, dict) else (str(i).split("=", 1) for i in items)
    out = {}
    for key, value in pairs:
        value = str(value)
        out[key] = env.get(value[2:-1], "") if value.startswith("${") and value.endswith("}") else value
    return out


def test_migrate_and_backend_log_in_where_the_database_was_made(installation) -> None:
    compose, env = installation
    services = compose["services"]
    db = _effective(next(s for n, s in services.items() if n.endswith("db")), env)
    migrate = _effective(next(s for n, s in services.items() if n.endswith("migrate")), env)
    backend = _effective(next(s for n, s in services.items() if n.endswith("backend")), env)

    assert db["POSTGRES_PASSWORD"] and db["POSTGRES_PASSWORD"] != "dev_pw"
    assert migrate["DB_PASSWORD"] == db["POSTGRES_PASSWORD"]
    assert migrate["DB_USER"] == db["POSTGRES_USER"] and migrate["DB_NAME"] == db["POSTGRES_DB"]
    assert backend["DB_PASSWORD"] == db["POSTGRES_PASSWORD"]
    assert backend["LOG_LEVEL"] == "info", "what is not a connection stays as written"


def test_a_password_the_settings_do_not_name_is_the_installations() -> None:
    compose = {"services": {"db": {"image": "postgres:16"}, "worker": {"environment": {"DB_PASSWORD": "x"}}}}

    assert P.database_connection_from_env(compose, "POSTGRES_PASSWORD=abc\n") == ["worker"]

    assert compose["services"]["worker"]["environment"] == {"DB_PASSWORD": "${POSTGRES_PASSWORD}"}


def test_a_connection_the_settings_name_is_read_from_them() -> None:
    """The settings hold the address of the installation's database; a plain value in the project points elsewhere."""
    compose = {"services": {"db": {"image": "postgres:16"}, "worker": {"environment": {"DB_HOST": "localhost"}}}}

    P.database_connection_from_env(compose, "POSTGRES_PASSWORD=abc\nDB_HOST=db\n")

    assert compose["services"]["worker"]["environment"] == {"DB_HOST": "${DB_HOST}"}


def test_the_database_service_what_reads_a_variable_and_other_settings_stay() -> None:
    db = {"image": "postgres:16", "environment": {"POSTGRES_USER": "app", "POSTGRES_PASSWORD": "${POSTGRES_PASSWORD}"}}
    reader = {"environment": {"DATABASE_URL": "${APP_DB_URL}", "LOG_LEVEL": "info", "DB_PORT": "5432"}}
    compose = {"services": {"db": db, "backend": reader}}
    env = "POSTGRES_PASSWORD=abc\nPOSTGRES_USER=other\nDATABASE_URL=postgresql://x\nLOG_LEVEL=debug\n"

    assert P.database_connection_from_env(compose, env) == []

    assert compose["services"]["db"]["environment"]["POSTGRES_USER"] == "app", "the database service makes the database"
    assert compose["services"]["backend"]["environment"] == {
        "DATABASE_URL": "${APP_DB_URL}",
        "LOG_LEVEL": "info",
        "DB_PORT": "5432",
    }
