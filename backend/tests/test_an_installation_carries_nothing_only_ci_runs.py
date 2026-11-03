"""DEV-295 — an installation carries nothing that only CI runs, so no test value of the project reaches the customer.

03.11.2026 the night backup of MAGER stopped at /opt/customers/icc/nex-websites/docker-compose.yml: line 110,
SESSION_SECRET, a value right in the file (MSG-209). It was not a secret of the installation — it was the test value of
NEX Websites' ``test`` service from its git, which the cockpit copied into every installation parked behind a profile
(ICCINT-60: „a faithful copy“). The backup cannot know a value is a test value, so MAGER had no backup that night.
Director: „Súhlasím“ — the installation does not get what only CI runs.
"""

from __future__ import annotations

import textwrap

import yaml

from backend.services import uat_provisioner as P
from tests.test_provisioner_prod import _make_project

#: A test value as NEX Websites' test service carries it in git (not a real secret — that is exactly the point).
TEST_VALUE = "test-session-secret-not-for-production-0123456789"
#: NEX Websites' shape: the runner built from Dockerfile.test, its test values written into the file.
COMPOSE = textwrap.dedent(
    f"""
    services:
      db:
        image: postgres:16-alpine
        environment:
          POSTGRES_USER: nexweb
          POSTGRES_DB: nex_websites
          POSTGRES_PASSWORD: ${{POSTGRES_PASSWORD}}
        volumes:
          - db-data:/var/lib/postgresql/data
      backend:
        build:
          context: ./backend
        env_file: [.env]
        depends_on: [db]
      frontend:
        build:
          context: ./frontend
      test:
        build:
          context: .
          dockerfile: Dockerfile.test
        environment:
          DATABASE_URL: postgresql+asyncpg://nexweb:${{POSTGRES_PASSWORD:-ci}}@db:5432/nex_websites
          MANAGER_LAUNCH_SIGNING_KEY: ""
          SESSION_SECRET: {TEST_VALUE}
        volumes:
          - test-cache:/cache
        depends_on: [db]
    volumes:
      db-data:
      test-cache:
    """
)


def test_a_new_installation_has_no_test_value_of_the_project(tmp_path):
    _make_project(tmp_path, "nex-websites", COMPOSE)

    result = P.provision_uat("nex-websites", "icc", projects_root=tmp_path / "projects", uat_root=tmp_path / "uat")

    text = result.compose_path.read_text(encoding="utf-8")
    assert TEST_VALUE not in text
    compose = yaml.safe_load(text)
    assert set(compose["services"]) == {"db", "backend", "frontend"}
    assert set(compose["volumes"]) == {"db-data"}, "a volume only the runner used is taken out with it"


def test_a_profiled_service_the_app_waits_for_stays():
    """A service an application service waits for is load-bearing, whatever profile it declares."""
    compose = {
        "services": {
            "backend": {"image": "app:1", "depends_on": ["mail"]},
            "mail": {"image": "mailhog:1", "profiles": ["dev"]},
            "test": {"image": "app-test:1", "profiles": ["ci"], "volumes": ["shared:/s", "own:/o"]},
            "worker": {"image": "app:1", "volumes": ["shared:/s"]},
        },
        "volumes": {"shared": None, "own": None},
    }

    assert P.drop_ci_only_services(compose) == ["test"]
    assert set(compose["services"]) == {"backend", "mail", "worker"}
    assert compose["volumes"] == {"shared": None}, "a volume another service mounts stays"


def test_nothing_to_take_out_changes_nothing():
    compose = {"services": {"backend": {"image": "app:1"}}, "volumes": {"data": None}}
    before = yaml.safe_dump(compose)

    assert P.drop_ci_only_services(compose) == []
    assert yaml.safe_dump(compose) == before


def test_the_project_s_runner_is_not_a_service_the_installation_misses(tmp_path):
    """It never belongs there — reporting it would warn at every deploy and teach the person to ignore the warning."""
    _make_project(tmp_path, "nex-websites", COMPOSE)
    result = P.provision_uat("nex-websites", "icc", projects_root=tmp_path / "projects", uat_root=tmp_path / "uat")

    missing = P.services_missing_against_source(result.compose_path.parent, yaml.safe_load(COMPOSE))

    assert missing == []
