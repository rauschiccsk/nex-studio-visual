"""DEV-301 — a variable a new version brings gets its value by the rules of the first install, not the example's.

03.11.2026, NEX Manager 1.5.0 on the UAT of ICC: the version brought ``ADMIN_INITIAL_PASSWORD`` (the initial password
of the ``admin`` account; the app refuses an empty one before it moves people onto the template). The customer had it
set in the cockpit and the deploy gate let the deploy through, but the installation already existed, so the redeploy
took the path that keeps the installation's values and adds new ones — with the value from the project's
``.env.example``: empty. Migration 005 refused („Začiatočné heslo správcu (Tajný kľúč) nie je nastavené“) and UAT
stood still. The same rules that render the first install — the customer's admin password, a random value for a
secret, nothing for a placeholder — now fill what a new version adds; what the installation already has stays, and the
admin password follows the customer.
"""

from __future__ import annotations

from pathlib import Path

from backend.services import uat_provisioner as P

SOURCE = """
services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: app
      POSTGRES_PASSWORD: x
      POSTGRES_DB: app
  backend:
    build:
      context: .
  frontend:
    build:
      context: ./frontend
"""
#: The example of 1.4.2 — no admin password yet; 1.5.0 brings it, empty, as NEX Manager's does.
EXAMPLE_142 = "SECRET_KEY=change-me\nSEED_ADMIN_EMAIL=admin@example.com\n"
EXAMPLE_150 = EXAMPLE_142 + "ADMIN_INITIAL_PASSWORD=\nNEW_SIGNING_SECRET=\n"


def _project(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setattr(P, "_docker_image_exists", lambda image: True)
    project = tmp_path / "projects" / "nex-manager"
    project.mkdir(parents=True)
    (project / "docker-compose.yml").write_text(SOURCE, encoding="utf-8")
    (project / ".env.example").write_text(EXAMPLE_142, encoding="utf-8")
    return project


def _deploy(tmp_path: Path, version: str, admin_password: str | None = None):
    return P.provision_uat(
        "nex-manager",
        "icc",
        projects_root=tmp_path / "projects",
        uat_root=tmp_path / "uat",
        customer_slug="icc",
        app="manager",
        full_project_slug="nex-manager",
        version=version,
        admin_password=admin_password,
    )


def test_the_admin_password_a_new_version_brings_is_the_customer_s(tmp_path, monkeypatch):
    project = _project(tmp_path, monkeypatch)
    first = _deploy(tmp_path, "1.4.2")
    assert "ADMIN_INITIAL_PASSWORD" not in P._parse_env_file(first.env_path)

    (project / ".env.example").write_text(EXAMPLE_150, encoding="utf-8")
    again = _deploy(tmp_path, "1.5.0", admin_password="heslo-od-zakaznika")

    assert again.env_path == first.env_path, "the same installation, redeployed"
    assert P._parse_env_file(again.env_path)["ADMIN_INITIAL_PASSWORD"] == "heslo-od-zakaznika"


def test_the_admin_password_follows_the_customer_on_the_next_deploy(tmp_path, monkeypatch):
    """Spec of NEX Manager 1.5.0 §8: „Koniec stavu: Manažér zmení Tajný kľúč a nasadí znova.“"""
    project = _project(tmp_path, monkeypatch)
    (project / ".env.example").write_text(EXAMPLE_150, encoding="utf-8")
    _deploy(tmp_path, "1.5.0", admin_password="prve")

    again = _deploy(tmp_path, "1.5.1", admin_password="druhe")

    assert P._parse_env_file(again.env_path)["ADMIN_INITIAL_PASSWORD"] == "druhe"


def test_a_new_secret_gets_a_random_value_and_the_installation_keeps_its_own(tmp_path, monkeypatch):
    project = _project(tmp_path, monkeypatch)
    first = _deploy(tmp_path, "1.4.2")
    own = P._parse_env_file(first.env_path)

    (project / ".env.example").write_text(EXAMPLE_150, encoding="utf-8")
    again = P._parse_env_file(_deploy(tmp_path, "1.5.0", admin_password="heslo-od-zakaznika").env_path)

    assert len(again["NEW_SIGNING_SECRET"]) >= 32, "a new secret is not the example's empty value"
    assert again["SECRET_KEY"] == own["SECRET_KEY"], "what the installation has stays"
    assert again["SEED_ADMIN_EMAIL"] == own["SEED_ADMIN_EMAIL"]


def test_without_a_customer_password_an_existing_one_stays(tmp_path, monkeypatch):
    project = _project(tmp_path, monkeypatch)
    (project / ".env.example").write_text(EXAMPLE_150, encoding="utf-8")
    _deploy(tmp_path, "1.5.0", admin_password="prve")

    again = _deploy(tmp_path, "1.5.1")

    assert P._parse_env_file(again.env_path)["ADMIN_INITIAL_PASSWORD"] == "prve"


def test_without_a_customer_password_the_cockpit_never_invents_one(tmp_path, monkeypatch):
    """A random admin password nobody knows locks the Manažér out (deploy.py, the self-sufficiency gate) — the
    installation's own value stays, even an empty one."""
    project = _project(tmp_path, monkeypatch)
    (project / ".env.example").write_text(EXAMPLE_150, encoding="utf-8")
    first = _deploy(tmp_path, "1.5.0", admin_password="prve")
    first.env_path.write_text(
        first.env_path.read_text(encoding="utf-8").replace("ADMIN_INITIAL_PASSWORD=prve", "ADMIN_INITIAL_PASSWORD="),
        encoding="utf-8",
    )

    again = _deploy(tmp_path, "1.5.1")

    assert P._parse_env_file(again.env_path)["ADMIN_INITIAL_PASSWORD"] == ""
