"""DEV-406 — the address of the database an installation's settings carry is the database service of ITS compose.

16.11.2026, NEX Inbox 1.10.4: MÁGERSTAV's installations are older than the project's layout — their database service is
called ``postgres``, the project's ``db``. The cockpit rendered every address of the database (``DB_HOST``,
``DATABASE_URL``) by the project's name, so the deploy rehearsal's migration found no database (exit 1, five rounds of
the Staviteľ guessing), and a real redeploy added the new ``DATABASE_URL`` with the host ``db`` — which version 1.10.4
prefers over ``DB_HOST``: MÁGERSTAV's UAT and PROD would have lost their database. Director on the fix: „Súhlasím“.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

import yaml

from backend.services import deploy_rehearsal
from backend.services import uat_provisioner as P

SOURCE = """
services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: nex_inbox
      POSTGRES_PASSWORD: ${DB_PASSWORD}
      POSTGRES_DB: nex_inbox
  backend:
    build:
      context: .
      dockerfile: backend/Dockerfile
    env_file: .env
    depends_on: [db]
  frontend:
    build:
      context: ./frontend
"""
EXAMPLE_1103 = "DB_HOST=db\nDB_PORT=5432\nDB_NAME=nex_inbox\nDB_USER=nex_inbox\nDB_PASSWORD=\n"
#: 1.10.4 brings the database as one address, which wins over DB_HOST.
EXAMPLE_1104 = EXAMPLE_1103 + "DATABASE_URL=\n"


def _project(tmp_path: Path, monkeypatch, example: str) -> Path:
    monkeypatch.setattr(P, "_docker_image_exists", lambda image: True)
    project = tmp_path / "projects" / "nex-inbox"
    project.mkdir(parents=True, exist_ok=True)
    (project / "docker-compose.yml").write_text(SOURCE, encoding="utf-8")
    (project / ".env.example").write_text(example, encoding="utf-8")
    return project


def _deploy(tmp_path: Path, version: str, *, rotate: bool = False):
    return P.provision_uat(
        "nex-inbox",
        "mager",
        projects_root=tmp_path / "projects",
        uat_root=tmp_path / "uat",
        customer_slug="mager",
        app="inbox",
        full_project_slug="nex-inbox",
        version=version,
        rotate_secrets=rotate,
    )


def _older_installation(result) -> None:
    """Make the installation the deploy wrote look like MÁGERSTAV's: its database service is called ``postgres``."""
    compose = result.compose_path.read_text(encoding="utf-8")
    data = yaml.safe_load(compose)
    services = data["services"]
    services["postgres"] = services.pop("db")
    for svc in services.values():
        depends = svc.get("depends_on")
        if isinstance(depends, dict) and "db" in depends:
            depends["postgres"] = depends.pop("db")
        elif isinstance(depends, list):
            svc["depends_on"] = ["postgres" if d == "db" else d for d in depends]
    header = "".join(line + "\n" for line in compose.splitlines() if line.startswith("#"))
    result.compose_path.write_text(header + yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    env = result.env_path.read_text(encoding="utf-8").replace("DB_HOST=db\n", "DB_HOST=postgres\n")
    result.env_path.write_text(env, encoding="utf-8")


def _database_hosts(result) -> tuple[str, str | None]:
    env = P._parse_env_file(result.env_path)
    url = env.get("DATABASE_URL")
    return env["DB_HOST"], (urlsplit(url).hostname if url else None)


def test_a_new_address_of_the_database_leads_to_the_installations_own_service(tmp_path, monkeypatch):
    project = _project(tmp_path, monkeypatch, EXAMPLE_1103)
    _older_installation(_deploy(tmp_path, "1.10.3"))

    (project / ".env.example").write_text(EXAMPLE_1104, encoding="utf-8")
    again = _deploy(tmp_path, "1.10.4")

    services = yaml.safe_load(again.compose_path.read_text(encoding="utf-8"))["services"]
    assert "postgres" in services and "db" not in services, "the installation keeps its own database service"
    assert _database_hosts(again) == ("postgres", "postgres")


def test_the_deploy_rehearsal_tries_the_installation_with_settings_that_lead_to_its_database(tmp_path, monkeypatch):
    """The rehearsal lays the installation's own compose over a first install made for the project's layout; its
    settings must lead where the installation's real ones do — else the migration finds no database (alembic-init exit
    1 on NEX Inbox 1.10.4, five rounds of Verifikácia)."""
    _project(tmp_path, monkeypatch, EXAMPLE_1103)
    _older_installation(_deploy(tmp_path, "1.10.3"))
    (tmp_path / "projects" / "nex-inbox" / ".env.example").write_text(EXAMPLE_1104, encoding="utf-8")

    targets = deploy_rehearsal.render_targets(
        "nex-inbox", "1.10.4", projects_root=tmp_path / "projects", uat_root=tmp_path / "uat", work=tmp_path / "work"
    )

    labels = [t.label for t in targets]
    assert labels == ["nová inštalácia", "inštalácia zákazníka mager"]
    mager = targets[1]
    services = yaml.safe_load(mager.compose.read_text(encoding="utf-8"))["services"]
    assert "postgres" in services and "db" not in services
    env = P._parse_env_file(mager.env)
    assert (env["DB_HOST"], urlsplit(env["DATABASE_URL"]).hostname) == ("postgres", "postgres")
    fresh = P._parse_env_file(targets[0].env)
    assert (fresh["DB_HOST"], urlsplit(fresh["DATABASE_URL"]).hostname) == ("db", "db"), "a new one keeps the project's"


def test_a_secrets_rotation_writes_the_projects_layout_and_its_addresses(tmp_path, monkeypatch):
    """Rendered anew, the installation takes the project's layout — its database service and the addresses agree."""
    _project(tmp_path, monkeypatch, EXAMPLE_1104)
    _older_installation(_deploy(tmp_path, "1.10.3"))

    again = _deploy(tmp_path, "1.10.4", rotate=True)

    assert "db" in yaml.safe_load(again.compose_path.read_text(encoding="utf-8"))["services"]
    assert _database_hosts(again) == ("db", "db")


def test_only_the_addresses_of_the_named_service_are_retargeted(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "# DB_HOST=db stays a comment\n"
        "# READ_DATABASE_URL=postgresql://u:p@db/old — so does this one\n"
        "DB_HOST=db\n"
        "POSTGRES_HOST=db\n"
        "DATABASE_URL=postgresql+psycopg2://u:p%40ss@db:5432/inbox\n"
        "READ_DATABASE_URL=postgresql://u:p@db/inbox\n"
        "ARCHIVE_DATABASE_URL=postgresql://u:p@dbx:5432/archive\n"
        "REDIS_HOST=db\n",
        encoding="utf-8",
    )

    changed = P.retarget_database_host(env, "db", "postgres")

    assert changed == ["DB_HOST", "POSTGRES_HOST", "DATABASE_URL", "READ_DATABASE_URL"]
    assert env.read_text(encoding="utf-8") == (
        "# DB_HOST=db stays a comment\n"
        "# READ_DATABASE_URL=postgresql://u:p@db/old — so does this one\n"
        "DB_HOST=postgres\n"
        "POSTGRES_HOST=postgres\n"
        "DATABASE_URL=postgresql+psycopg2://u:p%40ss@postgres:5432/inbox\n"
        "READ_DATABASE_URL=postgresql://u:p@postgres/inbox\n"
        "ARCHIVE_DATABASE_URL=postgresql://u:p@dbx:5432/archive\n"
        "REDIS_HOST=db\n"
    )


def test_a_new_installation_reaches_the_projects_service(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch, EXAMPLE_1104)

    assert _database_hosts(_deploy(tmp_path, "1.10.4")) == ("db", "db")
