"""DEV-270 — a test runner built from the test image stays out of the customer's running stack, new or existing.

30.10.2026 the cockpit rendered NEX Knowledge 0.2.0 into ICC's UAT with its ``test`` service in the stack, restarted
``unless-stopped``: the runner asks for ``DATABASE_URL`` (the template's asks for ``TEST_DATABASE_URL``), and only
the latter told the cockpit what it is (ICCINT-60). NEX Websites' runner has the same shape and was already in ICC's
UAT and PROD stacks — at the deploy of 15.10.2026 its suite (``TRUNCATE … RESTART IDENTITY``) ran in the UAT stack
against the application's own database. The runner is now recognised by its recipe too, ``Dockerfile.test``, and an
existing installation gets it parked at its next deploy — said aloud, like every other change a deploy makes.
"""

from __future__ import annotations

import textwrap

import pytest
import yaml

from backend.services import uat_provisioner as P
from tests.test_provisioner_prod import _make_project

#: NEX Knowledge's / NEX Websites' shape: the runner is built from Dockerfile.test and asks for DATABASE_URL.
COMPOSE = textwrap.dedent(
    """
    services:
      db:
        image: postgres:16-alpine
        environment:
          POSTGRES_USER: app
          POSTGRES_DB: app
          POSTGRES_PASSWORD: ${DB_PASSWORD}
        healthcheck:
          test: ["CMD-SHELL", "pg_isready -U app"]
      migrate:
        build:
          context: ./backend
        command: ["alembic", "upgrade", "head"]
        depends_on:
          db:
            condition: service_healthy
      backend:
        build:
          context: ./backend
        depends_on:
          migrate:
            condition: service_completed_successfully
      frontend:
        build:
          context: ./frontend
      test:
        build:
          context: .
          dockerfile: Dockerfile.test
        environment:
          DATABASE_URL: postgresql+asyncpg://app:${DB_PASSWORD}@db:5432/app
        depends_on:
          db:
            condition: service_healthy
    """
)

ROLES = {"backend": "backend", "frontend": "frontend", "db": "db"}
SAID = "Skúšobná služba test sa odteraz s aplikáciou nespúšťa — jej skúšky patria do CI, nie do inštalácie zákazníka."


def test_a_service_built_from_the_test_image_is_the_test_runner():
    def runner(name, build):
        return P.is_test_runner_service(name, {"build": build, "environment": {"DATABASE_URL": "x"}}, ROLES)

    assert runner("test", {"context": ".", "dockerfile": "Dockerfile.test"}) is True
    assert runner("checks", {"context": ".", "dockerfile": "docker/Dockerfile.test"}) is True
    assert runner("test", {"context": ".", "dockerfile": "Dockerfile.testing"}) is False
    assert runner("test", {"context": "."}) is False
    assert runner("test", "./tests") is False
    # the application itself is never taken out, whatever it is built from
    assert runner("backend", {"context": ".", "dockerfile": "Dockerfile.test"}) is False


def _deploy(tmp_path, version=None):
    kwargs = {"version": version} if version else {}
    return P.provision_uat(
        "nex-knowledge", "icc", projects_root=tmp_path / "projects", uat_root=tmp_path / "uat", **kwargs
    )


def test_a_new_installation_parks_the_runner_and_keeps_the_app(tmp_path):
    _make_project(tmp_path, "nex-knowledge", COMPOSE)

    services = yaml.safe_load(_deploy(tmp_path).compose_path.read_text())["services"]

    assert services["test"]["profiles"] == [P.TEST_RUNNER_PROFILE] and services["test"]["restart"] == "no"
    assert {n for n, s in services.items() if s.get("profiles")} == {"test"}


def _as_written_on_30_10(compose_path) -> None:
    """The installation as the cockpit wrote it before: the runner in the stack, restarted ``unless-stopped``."""
    text = compose_path.read_text()
    header = "".join(line for line in text.splitlines(keepends=True) if line.startswith("#"))
    data = yaml.safe_load(text)
    data["services"]["test"].pop("profiles")
    data["services"]["test"]["restart"] = "unless-stopped"
    compose_path.write_text(header + yaml.safe_dump(data, sort_keys=False))


def test_an_existing_installation_gets_the_runner_parked_at_its_next_deploy_and_is_told(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "_docker_image_exists", lambda image: True)
    _make_project(tmp_path, "nex-knowledge", COMPOSE)
    _as_written_on_30_10(_deploy(tmp_path).compose_path)

    result = _deploy(tmp_path, version="0.2.1")

    services = yaml.safe_load(result.compose_path.read_text())["services"]
    assert services["test"]["profiles"] == [P.TEST_RUNNER_PROFILE] and services["test"]["restart"] == "no"
    assert {n for n, s in services.items() if s.get("profiles")} == {"test"}, "a piece of the app was taken out"
    assert SAID in result.warnings

    again = _deploy(tmp_path, version="0.2.2")

    assert not [w for w in again.warnings if w.startswith("Skúšobná služba")], "said again with nothing changed"


def test_the_adoption_preview_foresees_the_same_parking():
    """ICC's NEX Websites, hand-written shape: the runner with ``restart: no`` and no profile — the preview and the
    write go through one function (ICCINT-151)."""
    before = textwrap.dedent(
        """
        services:
          db:
            image: postgres:16-alpine
          backend:
            image: nex-websites-backend:1.0.0
          test:
            image: nex-websites-test:1.0.0
            build:
              context: /opt/projects/nex-websites
              dockerfile: Dockerfile.test
            restart: "no"
            environment:
              DATABASE_URL: postgresql+asyncpg://app:x@db:5432/nex_websites
        """
    )

    after = P.version_bump_from_text(before, version="1.0.1", project_slug="nex-websites")

    assert after["services"]["test"]["profiles"] == [P.TEST_RUNNER_PROFILE]
    assert P.test_runners_parked(yaml.safe_load(before), after) == ["test"]
    assert "profiles" not in after["services"]["backend"] and "profiles" not in after["services"]["db"]


@pytest.mark.parametrize("waits", ["service_started", "service_completed_successfully"])
def test_a_runner_another_service_waits_for_stays_where_it_is(waits):
    before = {
        "services": {
            "test": {"build": {"context": ".", "dockerfile": "Dockerfile.test"}},
            "report": {"image": "reporter:1", "depends_on": {"test": {"condition": waits}}},
        }
    }

    after = P.version_bump_from_text(yaml.safe_dump(before), version="1.0.1", project_slug="x")

    assert "profiles" not in after["services"]["test"]
    assert P.test_runners_parked(before, after) == []


def test_a_runner_already_behind_its_own_profile_keeps_it():
    """Career Asistent's installations park their runner under ``test`` themselves — nothing to change, nothing said."""
    before = {"services": {"test": {"build": {"dockerfile": "Dockerfile.test"}, "profiles": ["test"], "restart": "no"}}}

    after = P.version_bump_from_text(yaml.safe_dump(before), version="1.0.1", project_slug="career-asistent")

    assert after["services"]["test"]["profiles"] == ["test"]
    assert P.test_runners_parked(before, after) == []
