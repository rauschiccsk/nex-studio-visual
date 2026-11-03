"""DEV-270 — a test runner built from the test image stays out of the customer's running stack, new or existing.

30.10.2026 the cockpit rendered NEX Knowledge 0.2.0 into ICC's UAT with its ``test`` service in the stack, restarted
``unless-stopped``: the runner asks for ``DATABASE_URL`` (the template's asks for ``TEST_DATABASE_URL``), and only
the latter told the cockpit what it is (ICCINT-60). NEX Websites' runner has the same shape and was already in ICC's
UAT and PROD stacks — at the deploy of 15.10.2026 its suite (``TRUNCATE … RESTART IDENTITY``) ran in the UAT stack
against the application's own database. The runner is now recognised by its recipe too, ``Dockerfile.test``, and an
existing installation gets it parked at its next deploy — said aloud, like every other change a deploy makes.

DEV-295 (03.11.2026): parked was not enough. The parked copy brought NEX Websites' test values into ICC's installation
on MAGER and the night backup stopped them as a secret (MSG-209). Director: „Súhlasím“ — the runner is now taken out
of the installation, new or existing.
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
SAID = (
    "Služby test spúšťa len CI (skúšky) — z predpisu inštalácie sú preč; skúšky patria do CI, nie do inštalácie "
    "zákazníka."
)


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


def test_a_new_installation_has_no_runner_and_keeps_the_app(tmp_path):
    _make_project(tmp_path, "nex-knowledge", COMPOSE)

    services = yaml.safe_load(_deploy(tmp_path).compose_path.read_text())["services"]

    assert set(services) == {"db", "migrate", "backend", "frontend"}
    assert not [n for n, s in services.items() if s.get("profiles")]


def _as_written_on_30_10(compose_path) -> None:
    """The installation as the cockpit wrote it before: the runner in the stack, restarted ``unless-stopped``."""
    text = compose_path.read_text()
    header = "".join(line for line in text.splitlines(keepends=True) if line.startswith("#"))
    data = yaml.safe_load(text)
    # DEV-295: today's render has no runner at all — the installation of 30.10.2026 had it in the stack, so put it back.
    data["services"]["test"] = dict(yaml.safe_load(COMPOSE)["services"]["test"], restart="unless-stopped")
    compose_path.write_text(header + yaml.safe_dump(data, sort_keys=False))


def test_an_existing_installation_loses_the_runner_at_its_next_deploy_and_is_told(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "_docker_image_exists", lambda image: True)
    _make_project(tmp_path, "nex-knowledge", COMPOSE)
    _as_written_on_30_10(_deploy(tmp_path).compose_path)

    result = _deploy(tmp_path, version="0.2.1")

    services = yaml.safe_load(result.compose_path.read_text())["services"]
    assert set(services) == {"db", "migrate", "backend", "frontend"}, "a piece of the app was taken out"
    assert SAID in result.warnings
    # the runner is gone — telling about its health check would be noise (30.10.2026 it was told)
    assert not [w for w in result.warnings if w.startswith("Jednorazové kroky") and " test " in w]
    # nor is the project's runner „a service the installation does not have“ — it never belongs there
    assert not [w for w in result.warnings if w.startswith("Projekt má služby")]

    again = _deploy(tmp_path, version="0.2.2")

    assert not [w for w in again.warnings if "spúšťa len CI" in w], "said again with nothing changed"
    assert not [w for w in again.warnings if w.startswith("Projekt má služby")]


def test_the_adoption_preview_foresees_the_same_removal():
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

    assert set(after["services"]) == {"db", "backend"}
    assert P.ci_only_services_removed(yaml.safe_load(before), after) == ["test"]
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
    assert P.ci_only_services_removed(before, after) == []


def test_a_runner_behind_its_own_profile_goes_too():
    """Career Asistent's installations park their runner under ``test`` themselves — DEV-295: out it goes as well."""
    before = {
        "services": {
            "backend": {"image": "career-asistent-backend:1.0.0"},
            "test": {"build": {"dockerfile": "Dockerfile.test"}, "profiles": ["test"], "restart": "no"},
        }
    }

    after = P.version_bump_from_text(yaml.safe_dump(before), version="1.0.1", project_slug="career-asistent")

    assert set(after["services"]) == {"backend"}
    assert P.ci_only_services_removed(before, after) == ["test"]
