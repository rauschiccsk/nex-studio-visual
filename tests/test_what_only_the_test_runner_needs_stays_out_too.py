"""DEV-273 — a service that only the parked test runner needs stays out of the customer's running stack too.

31.10.2026 the UAT of Author Asistent for „tata“ had its ``test`` parked behind the profile ``ci`` (ICCINT-60) — and
its ``test-db``, which exists for that runner alone, running with the app, ``unless-stopped``, back after every restart
of the server. Nothing that runs with the app waits for it. Now it is parked with the runner, in a new installation
and in an existing one at its next deploy, and the deploy says so.

DEV-295 (03.11.2026): parked was not enough — what only CI runs is taken out of the installation, the runner and its
database alike (MSG-209: a parked runner's test values stopped MAGER's backup as a secret).
"""

from __future__ import annotations

import textwrap

import yaml

from backend.services import uat_provisioner as P
from tests.test_provisioner_prod import _make_project

#: Author Asistent's shape: the runner asks for TEST_DATABASE_URL and waits for its own database.
COMPOSE = textwrap.dedent(
    """
    services:
      db:
        image: postgres:16-alpine
      redis:
        image: redis:7-alpine
      migrate:
        build:
          context: .
        command: ["alembic", "upgrade", "head"]
        depends_on: [db]
      backend:
        build:
          context: .
        depends_on: [db, redis]
      worker:
        build:
          context: .
        command: ["python", "-m", "app.worker"]
      frontend:
        build:
          context: ./frontend
      test-db:
        image: postgres:16-alpine
      test:
        build:
          context: .
          dockerfile: Dockerfile.test
        environment:
          TEST_DATABASE_URL: postgresql://test:test@test-db:5432/test
        depends_on:
          test-db:
            condition: service_healthy
          redis:
            condition: service_started
    """
)

SAID = (
    "Služby test, test-db spúšťa len CI (skúšky) — z predpisu inštalácie sú preč; skúšky patria do CI, nie do "
    "inštalácie zákazníka."
)


def _deploy(tmp_path, version=None):
    kwargs = {"version": version} if version else {}
    return P.provision_uat(
        "author-asistent", "tata", projects_root=tmp_path / "projects", uat_root=tmp_path / "uat", **kwargs
    )


def _services(result) -> dict:
    return yaml.safe_load(result.compose_path.read_text())["services"]


def test_a_new_installation_has_neither_the_runner_nor_its_database(tmp_path):
    _make_project(tmp_path, "author-asistent", COMPOSE)

    services = _services(_deploy(tmp_path))

    assert "test" not in services and "test-db" not in services
    assert not [n for n, s in services.items() if s.get("profiles")]
    # what the app runs with stays: its database, the cache the backend waits for too, a worker nobody waits for
    assert {services[n]["restart"] for n in ("db", "redis", "backend", "worker", "frontend")} == {"unless-stopped"}


def test_an_existing_installation_loses_the_runner_and_its_database_at_its_next_deploy_and_is_told(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(P, "_docker_image_exists", lambda image: True)
    _make_project(tmp_path, "author-asistent", COMPOSE)
    first = _deploy(tmp_path)
    text = first.compose_path.read_text()
    header = "".join(line for line in text.splitlines(keepends=True) if line.startswith("#"))
    data = yaml.safe_load(text)
    # as the UAT of „tata“ had it on 31.10.2026: the runner parked, its database running with the app
    source = yaml.safe_load(COMPOSE)["services"]
    data["services"]["test"] = dict(source["test"], profiles=[P.TEST_RUNNER_PROFILE], restart="no")
    data["services"]["test-db"] = dict(source["test-db"], restart="unless-stopped")
    first.compose_path.write_text(header + yaml.safe_dump(data, sort_keys=False))

    result = _deploy(tmp_path, version="1.2.0")

    services = _services(result)
    assert "test" not in services and "test-db" not in services
    assert "profiles" not in services["redis"] and "profiles" not in services["db"]
    assert SAID in result.warnings
    assert not [w for w in result.warnings if w.startswith("Jednorazové kroky") and "test-db" in w]
    assert not [w for w in result.warnings if w.startswith("Projekt má služby")]

    again = _deploy(tmp_path, version="1.2.1")

    assert not [w for w in again.warnings if "spúšťa len CI" in w], "said again with nothing changed"


def test_a_chain_only_the_runner_needs_is_parked_link_by_link():
    # the seed comes first: when it is looked at, its database is not parked yet — a single pass would miss it
    services = {
        "db": {"image": "postgres:16"},
        "backend": {"image": "app:1", "depends_on": ["db"]},
        "test-db-seed": {"image": "seed:1"},
        "test": {"profiles": ["ci"], "depends_on": ["test-db"]},
        "test-db": {"image": "postgres:16", "depends_on": ["test-db-seed"]},
    }

    assert P.park_what_only_parked_services_need(services) == ["test-db", "test-db-seed"]
    assert services["test-db-seed"]["profiles"] == ["ci"]


def test_the_applications_own_database_is_never_parked():
    services = {
        "backend": {"image": "app:1", "environment": {"DATABASE_URL": "postgresql://db/app"}},
        "db": {"image": "postgres:16"},
        "test": {"profiles": ["ci"], "depends_on": ["db"]},
    }
    before = {n: dict(s) for n, s in services.items()}

    assert P.park_what_only_parked_services_need(services) == []
    assert services == before


def test_a_service_under_another_profile_takes_that_profile():
    services = {"smoke": {"profiles": ["smoke"], "depends_on": {"mailhog": {"condition": "service_started"}}}}
    services["mailhog"] = {"image": "mailhog:1"}

    assert P.park_what_only_parked_services_need(services) == ["mailhog"]
    assert services["mailhog"]["profiles"] == ["smoke"]
