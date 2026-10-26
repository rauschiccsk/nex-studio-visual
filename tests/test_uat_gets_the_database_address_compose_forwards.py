"""DEV-235 — UAT gets the database address even when the project's compose only forwards it from ``.env``.

26.10.2026 21:48 UTC, Author Asistent 0.1.0 on the UAT of customer tata: the ``migrate`` service died with
„RuntimeError: DATABASE_URL is not set“ and nothing else started. The installation's ``.env`` held ``DATABASE_URL``
EMPTY. The project's compose forwards it — ``DATABASE_URL: ${DATABASE_URL:-}`` on migrate, backend and worker — and
its ``.env.example`` carries the address, ``postgresql+pg8000://author_asistent:${POSTGRES_PASSWORD}@db:5432/…``.
``generate_uat_env`` overlaid the example with the backend's compose environment, so the key held the forwarding
``${DATABASE_URL:-}``; the branch for ``${…}`` values ran before the one for database connection variables and wrote
the compose default — nothing. The UAT database-address guard skips empty values, so nothing stopped the deploy.
The launch check and CI keep the example's address and passed. Director: „Áno, súhlasím, založ tiket a oprav to“.

Pinned: a forwarded database connection variable points at the UAT database, with the driver from the example; a
forwarded host does too; an empty ``DATABASE_URL`` that a service reads stops the deploy before anything is written,
and an address the compose builds itself is no such thing.
"""

from __future__ import annotations

import textwrap
from urllib.parse import urlsplit

import pytest

from backend.services import uat_provisioner as P
from tests.test_uat_provisioner import _PG8000_PYPROJECT, _provision

#: Author Asistent's compose, its database part as it stands in the project.
AA_COMPOSE = textwrap.dedent(
    """
    services:
      db:
        image: postgres:16-alpine
        environment:
          POSTGRES_DB: ${POSTGRES_DB:-author_asistent}
          POSTGRES_USER: ${POSTGRES_USER:-author_asistent}
          POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-ci}
      migrate:
        build:
          context: .
          dockerfile: backend/Dockerfile
        command: ["alembic", "upgrade", "head"]
        environment:
          DATABASE_URL: ${DATABASE_URL:-}
        depends_on:
          db:
            condition: service_healthy
      backend:
        build:
          context: .
          dockerfile: backend/Dockerfile
        ports: ["10270:8000"]
        environment:
          DATABASE_URL: ${DATABASE_URL:-}
          SESSION_SECRET: ${SESSION_SECRET:-}
        depends_on:
          migrate:
            condition: service_completed_successfully
      worker:
        build:
          context: .
          dockerfile: backend/Dockerfile
        environment:
          DATABASE_URL: ${DATABASE_URL:-}
      frontend:
        build:
          context: ./frontend
        ports: ["10271:80"]
    """
)
AA_ENV_EXAMPLE = textwrap.dedent(
    """
    SESSION_SECRET=
    POSTGRES_DB=author_asistent
    POSTGRES_USER=author_asistent
    POSTGRES_PASSWORD=
    DATABASE_URL=postgresql+pg8000://author_asistent:${POSTGRES_PASSWORD}@db:5432/author_asistent
    """
)


def _env(text: str) -> dict[str, str]:
    return {k: v for k, _eq, v in (line.partition("=") for line in text.splitlines()) if _eq and not k.startswith("#")}


def test_author_asistents_forwarded_address_points_at_the_uat_database(tmp_path):
    res = _provision(
        tmp_path, "author-asistent", "aa", AA_COMPOSE, env_example=AA_ENV_EXAMPLE, backend_pyproject=_PG8000_PYPROJECT
    )

    env = _env(res.env_path.read_text())
    url = urlsplit(env["DATABASE_URL"])
    assert url.scheme == "postgresql+pg8000", "the address is empty, or the driver from the example got lost"
    assert (url.username, url.password, url.path.lstrip("/")) == (
        env["POSTGRES_USER"],
        env["POSTGRES_PASSWORD"],
        env["POSTGRES_DB"],
    ), "the address does not open the database the UAT db service creates"
    assert url.hostname and url.port == int(P.UAT_DB_PORT)


def test_a_forwarded_database_host_points_at_the_uat_database(tmp_path):
    compose = AA_COMPOSE.replace(
        "      DATABASE_URL: ${DATABASE_URL:-}\n      SESSION_SECRET",
        "      DATABASE_URL: ${DATABASE_URL:-}\n      DB_HOST: ${DB_HOST:-localhost}\n      SESSION_SECRET",
    )
    res = _provision(
        tmp_path,
        "author-asistent",
        "aa",
        compose,
        env_example=AA_ENV_EXAMPLE + "DB_HOST=localhost\n",
        backend_pyproject=_PG8000_PYPROJECT,
    )

    env = _env(res.env_path.read_text())
    assert env["DB_HOST"] == urlsplit(env["DATABASE_URL"]).hostname != "localhost"


def test_an_empty_address_a_service_reads_stops_the_deploy_before_anything_is_written(tmp_path, monkeypatch):
    """Whatever empties it next time — the renderer of today does not — the deploy says so instead of starting."""
    rendered = P.generate_uat_env

    def _empty_address(**kwargs):
        return "\n".join(
            "DATABASE_URL=" if line.startswith("DATABASE_URL=") else line for line in rendered(**kwargs).splitlines()
        )

    monkeypatch.setattr(P, "generate_uat_env", _empty_address)

    with pytest.raises(ValueError, match=r"DATABASE_URL.*prázdn.*migrate, backend, worker"):
        _provision(
            tmp_path,
            "author-asistent",
            "aa",
            AA_COMPOSE,
            env_example=AA_ENV_EXAMPLE,
            backend_pyproject=_PG8000_PYPROJECT,
        )
    assert not (tmp_path / "uat" / "aa" / ".env").exists()
    assert not (tmp_path / "uat" / "aa" / "docker-compose.yml").exists()


def test_an_address_the_compose_builds_itself_is_not_an_empty_one():
    services = {
        "backend": {"environment": {"DATABASE_URL": "postgresql+pg8000://app:${DB_PASSWORD}@db:5432/app"}},
        "worker": {"env_file": [".env"], "environment": {"DATABASE_URL": "postgresql+pg8000://app:x@db:5432/app"}},
    }

    assert P.validate_rendered_database_address("DATABASE_URL=\nTZ=UTC\n", services) == []


def test_a_service_reading_the_env_file_reads_the_empty_address_too():
    services = {"backend": {"env_file": [".env"]}, "frontend": {}}

    failures = P.validate_rendered_database_address("DATABASE_URL=\n", services)

    assert len(failures) == 1 and "backend" in failures[0] and "frontend" not in failures[0]


@pytest.mark.parametrize(
    "environment",
    [["DATABASE_URL=${DATABASE_URL}"], ["DATABASE_URL"], {"DATABASE_URL": None}],
    ids=["list-forwarded", "list-bare-name", "dict-no-value"],
)
def test_every_way_of_forwarding_reads_the_address(environment):
    failures = P.validate_rendered_database_address("DATABASE_URL=\n", {"api": {"environment": environment}})

    assert len(failures) == 1 and " api " in failures[0]


def test_a_filled_address_passes():
    services = {"backend": {"environment": {"DATABASE_URL": "${DATABASE_URL:-}"}}}

    assert P.validate_rendered_database_address("DATABASE_URL=postgresql+pg8000://u:p@db:5432/d\n", services) == []
