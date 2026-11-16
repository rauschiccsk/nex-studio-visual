"""DEV-406 — the database an installation's settings lead to is the database of ITS compose: service, user, name.

16.11.2026, NEX Inbox 1.10.4: MÁGERSTAV's installations are older than the project's layout — their database service is
called ``postgres``, the project's ``db``. The cockpit rendered every address of the database (``DB_HOST``,
``DATABASE_URL``) by the project's name, so the deploy rehearsal's migration found no database (exit 1, five rounds of
the Staviteľ guessing), and a real redeploy added the new ``DATABASE_URL`` with the host ``db`` — which version 1.10.4
prefers over ``DB_HOST``: MÁGERSTAV's UAT and PROD would have lost their database. Director on the fix: „Súhlasím“.

The first fix aligned the host only. The same installation creates its own user and database (``nex_inbox``,
``nex_inbox_mager``) while the project takes them from ``.env``; the rehearsal then logged in as the project's
``postgres`` to ``nex-inbox_uat`` — five more rounds of „password authentication failed“. Director: „Áno doplňa sprav.“
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
      POSTGRES_USER: ${DB_USER:-nex_inbox}
      POSTGRES_PASSWORD: ${DB_PASSWORD:-dev}
      POSTGRES_DB: ${DB_NAME:-nex_inbox_dev}
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


#: What MÁGERSTAV's installation creates — written in its compose, not taken from its settings.
OWN_USER, OWN_DATABASE = "nex_inbox", "nex_inbox_mager"


def _older_installation(result) -> None:
    """Make the installation the deploy wrote look like MÁGERSTAV's: its database service is called ``postgres`` and
    creates its own user and database; its settings (as the real ones) lead there."""
    compose = result.compose_path.read_text(encoding="utf-8")
    data = yaml.safe_load(compose)
    services = data["services"]
    services["postgres"] = services.pop("db")
    services["postgres"].setdefault("environment", {})
    services["postgres"]["environment"].update({"POSTGRES_USER": OWN_USER, "POSTGRES_DB": OWN_DATABASE})
    for svc in services.values():
        depends = svc.get("depends_on")
        if isinstance(depends, dict) and "db" in depends:
            depends["postgres"] = depends.pop("db")
        elif isinstance(depends, list):
            svc["depends_on"] = ["postgres" if d == "db" else d for d in depends]
    header = "".join(line + "\n" for line in compose.splitlines() if line.startswith("#"))
    result.compose_path.write_text(header + yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    lines = []
    for line in result.env_path.read_text(encoding="utf-8").splitlines():
        key = line.split("=", 1)[0]
        own = {"DB_HOST": "postgres", "DB_USER": OWN_USER, "POSTGRES_USER": OWN_USER}
        own |= {"DB_NAME": OWN_DATABASE, "POSTGRES_DB": OWN_DATABASE}
        lines.append(f"{key}={own[key]}" if key in own else line)
    result.env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _database_hosts(result) -> tuple[str, str | None]:
    env = P._parse_env_file(result.env_path)
    url = env.get("DATABASE_URL")
    return env["DB_HOST"], (urlsplit(url).hostname if url else None)


def _logs_in(env_path: Path) -> tuple:
    """Where and as whom the settings log in: ``DB_*`` and ``DATABASE_URL`` (host, user, database)."""
    env = P._parse_env_file(env_path)
    url = urlsplit(env["DATABASE_URL"])
    return (env["DB_HOST"], env["DB_USER"], env["DB_NAME"]), (url.hostname, url.username, url.path.lstrip("/"))


def test_a_new_address_of_the_database_leads_to_the_installations_own_service(tmp_path, monkeypatch):
    project = _project(tmp_path, monkeypatch, EXAMPLE_1103)
    _older_installation(_deploy(tmp_path, "1.10.3"))

    (project / ".env.example").write_text(EXAMPLE_1104, encoding="utf-8")
    again = _deploy(tmp_path, "1.10.4")

    services = yaml.safe_load(again.compose_path.read_text(encoding="utf-8"))["services"]
    assert "postgres" in services and "db" not in services, "the installation keeps its own database service"
    own = ("postgres", OWN_USER, OWN_DATABASE)
    assert _logs_in(again.env_path) == (own, own), "the new address carries the installation's user and database"


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
    own = ("postgres", OWN_USER, OWN_DATABASE)
    assert _logs_in(mager.env) == (own, own), "it logs in where and as whom the installation's database expects"
    env = P._parse_env_file(mager.env)
    assert env["DB_PASSWORD"] == env["POSTGRES_PASSWORD"], "with the password the database is created with"
    (fresh_db, fresh_url) = _logs_in(targets[0].env)
    assert fresh_db[0] == fresh_url[0] == "db", "a new one keeps the project's"


def test_a_secrets_rotation_writes_the_projects_layout_and_its_addresses(tmp_path, monkeypatch):
    """Rendered anew, the installation takes the project's layout — its database service and the addresses agree."""
    _project(tmp_path, monkeypatch, EXAMPLE_1104)
    _older_installation(_deploy(tmp_path, "1.10.3"))

    again = _deploy(tmp_path, "1.10.4", rotate=True)

    assert "db" in yaml.safe_load(again.compose_path.read_text(encoding="utf-8"))["services"]
    assert _database_hosts(again) == ("db", "db")


def test_settings_are_aligned_with_the_installations_database_and_nothing_else(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "# DB_HOST=db stays a comment\n"
        "# READ_DATABASE_URL=postgresql://u:p@db/old — so does this one\n"
        "DB_HOST=db\n"
        "POSTGRES_HOST=db\n"
        "DB_USER=postgres\n"
        "POSTGRES_USER=postgres\n"
        "DB_NAME=nex-inbox_uat\n"
        "POSTGRES_DB=nex-inbox_uat\n"
        "DB_PASSWORD=s3cr%t\n"
        "DATABASE_URL=postgresql+psycopg2://postgres:p%40ss@db:5432/nex-inbox_uat\n"
        "READ_DATABASE_URL=postgresql://postgres:p@db/nex-inbox_uat\n"
        "ARCHIVE_DATABASE_URL=postgresql://u:p@dbx:5432/archive\n"
        "REDIS_HOST=db\n",
        encoding="utf-8",
    )

    changed = P.align_database_settings(env, "db", P.InstallationDatabase("postgres", OWN_USER, OWN_DATABASE))

    assert changed == [
        "DB_HOST", "POSTGRES_HOST", "DB_USER", "POSTGRES_USER", "DB_NAME", "POSTGRES_DB", "DATABASE_URL",
        "READ_DATABASE_URL",
    ]  # fmt: skip
    assert env.read_text(encoding="utf-8") == (
        "# DB_HOST=db stays a comment\n"
        "# READ_DATABASE_URL=postgresql://u:p@db/old — so does this one\n"
        "DB_HOST=postgres\n"
        "POSTGRES_HOST=postgres\n"
        "DB_USER=nex_inbox\n"
        "POSTGRES_USER=nex_inbox\n"
        "DB_NAME=nex_inbox_mager\n"
        "POSTGRES_DB=nex_inbox_mager\n"
        "DB_PASSWORD=s3cr%t\n"
        "DATABASE_URL=postgresql+psycopg2://nex_inbox:p%40ss@postgres:5432/nex_inbox_mager\n"
        "READ_DATABASE_URL=postgresql://nex_inbox:p@postgres/nex_inbox_mager\n"
        "ARCHIVE_DATABASE_URL=postgresql://u:p@dbx:5432/archive\n"
        "REDIS_HOST=db\n"
    )


def test_what_the_installation_takes_from_its_settings_is_not_overwritten(tmp_path):
    """A database whose user and name come from the settings themselves is consistent already — only the host moves."""
    env = tmp_path / ".env"
    env.write_text("DB_HOST=db\nDB_USER=postgres\nDB_NAME=app\n", encoding="utf-8")

    assert P.align_database_settings(env, "db", P.InstallationDatabase("postgres", None, None)) == ["DB_HOST"]
    assert env.read_text(encoding="utf-8") == "DB_HOST=postgres\nDB_USER=postgres\nDB_NAME=app\n"


def test_the_installations_database_is_read_from_its_compose():
    compose = yaml.safe_dump(
        {
            "services": {
                "postgres": {"image": "postgres:16", "environment": {"POSTGRES_USER": "a", "POSTGRES_DB": "b"}},
                "backend": {"build": {"context": "."}},
            }
        }
    )
    referenced = compose.replace("POSTGRES_USER: a", "POSTGRES_USER: ${DB_USER}")

    assert P.installation_database(compose) == P.InstallationDatabase("postgres", "a", "b")
    assert P.installation_database(referenced) == P.InstallationDatabase("postgres", None, "b")
    assert P.installation_database(None) == P.InstallationDatabase(None, None, None)


def test_a_new_installation_reaches_the_projects_service(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch, EXAMPLE_1104)

    assert _database_hosts(_deploy(tmp_path, "1.10.4")) == ("db", "db")


def test_a_database_on_another_host_is_left_where_it_is(tmp_path):
    env = tmp_path / ".env"
    env.write_text("DB_HOST=10.0.0.5\n", encoding="utf-8")

    assert P.align_database_settings(env, "db", P.InstallationDatabase("postgres", None, None)) == []
    assert env.read_text(encoding="utf-8") == "DB_HOST=10.0.0.5\n"


def test_a_redeploy_takes_the_user_the_installation_logs_in_as_today(tmp_path, monkeypatch):
    """An installation whose compose takes the user from its settings: the new address logs in as they say."""
    project = _project(tmp_path, monkeypatch, EXAMPLE_1103)
    first = _deploy(tmp_path, "1.10.3")
    _older_installation(first)
    data = yaml.safe_load(first.compose_path.read_text(encoding="utf-8"))
    data["services"]["postgres"]["environment"]["POSTGRES_USER"] = "${DB_USER}"
    header = "".join(
        line + "\n" for line in first.compose_path.read_text(encoding="utf-8").splitlines() if line[:1] == "#"
    )
    first.compose_path.write_text(header + yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    env = first.env_path.read_text(encoding="utf-8").replace(f"DB_USER={OWN_USER}\n", "DB_USER=inbox_operator\n")
    first.env_path.write_text(env, encoding="utf-8")

    (project / ".env.example").write_text(EXAMPLE_1104, encoding="utf-8")
    again = _deploy(tmp_path, "1.10.4")

    url = urlsplit(P._parse_env_file(again.env_path)["DATABASE_URL"])
    assert (url.hostname, url.username, url.path.lstrip("/")) == ("postgres", "inbox_operator", OWN_DATABASE)
