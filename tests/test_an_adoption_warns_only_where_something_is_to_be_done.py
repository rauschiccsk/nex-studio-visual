"""DEV-268 — a deploy warns only where somebody has something to do.

30.10.2026 the adoption of ICC's hand-written NEX Manager on UAT ended with two warnings that asked for nothing real:
„Projekt má služby, ktoré inštalácia nemá: test, test-db … doplň ich do inštalácie“ — services NEX Manager keeps behind
the compose profile ``test``, which a plain ``up`` never starts — and „Tieto tajomstvá nikto nedodal …
PROVISIONING_TOKEN … náhodná hodnota nikde neplatí“ — the key the cockpit itself reads from that installation to
register modules, for which a random value is exactly right. A warning that always comes teaches the reader to skip
the true one.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import yaml

from backend.services import uat_provisioner as P
from tests.test_provisioner_prod import _make_project

#: NEX Manager's own compose in its shape — the tests' services behind the profile ``test``.
SOURCE = {
    "services": {
        "db": {"image": "postgres:16-alpine"},
        "migrate": {"build": {"context": "."}, "command": ["alembic", "upgrade", "head"]},
        "backend": {"build": {"context": "."}},
        "frontend": {"build": {"context": "./frontend"}},
        "test-db": {"image": "postgres:16-alpine", "profiles": ["test"]},
        "test": {"build": {"context": ".", "dockerfile": "Dockerfile.test"}, "profiles": ["test"]},
    }
}

#: ICC's hand-written installation: the app, no test services.
ICC = {
    "services": {
        "db": {"image": "postgres:16-alpine"},
        "migrate": {"image": "nexmanager-backend:1.0.0", "command": ["alembic", "upgrade", "head"]},
        "backend": {"image": "nexmanager-backend:1.0.0"},
        "frontend": {"image": "nexmanager-frontend:1.0.0"},
    }
}


def _installation(tmp_path: Path) -> Path:
    inst = tmp_path / "uat" / "icc" / "nex-manager"
    inst.mkdir(parents=True)
    (inst / "docker-compose.yml").write_text(yaml.safe_dump(ICC), encoding="utf-8")
    return inst


def test_services_behind_a_profile_are_not_missing_from_an_installation(tmp_path):
    assert P.services_missing_against_source(_installation(tmp_path), SOURCE) == []


def test_a_service_the_app_runs_is_still_reported_missing(tmp_path):
    with_worker = {"services": {**SOURCE["services"], "worker": {"build": {"context": "."}}}}

    assert P.services_missing_against_source(_installation(tmp_path), with_worker) == ["worker"]


# ── the secrets the cockpit reads back itself ───────────────────────────────────────────────────────────────────

COMPOSE = textwrap.dedent(
    """
    services:
      db:
        image: postgres:16-alpine
      backend:
        build: .
        environment:
          PROVISIONING_TOKEN: ${PROVISIONING_TOKEN:-}
          LAUNCH_SIGNING_KEY: ${LAUNCH_SIGNING_KEY:-}
          SECRET_KEY: ${SECRET_KEY:-}
      frontend:
        build: ./frontend
    """
)

INVENTED = "Tieto tajomstvá nikto nedodal, tak dostali náhodné hodnoty: "


def _invented(tmp_path: Path, project: str) -> list[str]:
    _make_project(tmp_path, project, COMPOSE)
    result = P.provision_uat(project, "icc-uat", projects_root=tmp_path / "projects", uat_root=tmp_path / "uat")
    [said] = [w for w in result.warnings if w.startswith(INVENTED)]
    return said[len(INVENTED) :].split(". ", 1)[0].split(", ")


def test_the_managers_keys_the_cockpit_reads_are_not_named(tmp_path):
    assert _invented(tmp_path, "nex-manager") == ["SECRET_KEY"]


def test_the_same_keys_of_another_app_are_still_named(tmp_path):
    """The cockpit reads them back only from NEX Manager — in another app a key of that name is the app's own."""
    assert _invented(tmp_path, "nex-inbox") == ["LAUNCH_SIGNING_KEY", "PROVISIONING_TOKEN", "SECRET_KEY"]


def test_the_list_names_only_keys_the_cockpit_really_reads():
    from backend.services import launch_pairing

    assert P.SECRETS_THE_COCKPIT_READS == {
        launch_pairing.MANAGER_PROJECT: {P.PROVISIONING_TOKEN_ENV, launch_pairing.MANAGER_KEY}
    }
