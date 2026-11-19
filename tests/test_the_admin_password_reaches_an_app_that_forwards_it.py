"""DEV-436 — the customer's initial admin password reaches an app whose compose forwards it from ``.env``.

19.11.2026, Orthodox Hermenia's UAT for SKIT o.z.: the customer has its „Inicializačné heslo administrátora“, yet the
installation's ``ADMIN_INITIAL_PASSWORD`` was a random 64-character value and the deploy listed it among the secrets
„nikto nedodal“. The NSV standard template forwards the password from ``.env`` (``ADMIN_INITIAL_PASSWORD:
${ADMIN_INITIAL_PASSWORD:-}``), and the render handled the ``${…}`` form before it looked for the customer's password —
the same order that once emptied ``DATABASE_URL`` (DEV-235). The app founded ``admin`` with a password nobody knew.
Director: „Súhlasím, založ tiket a daj ho do balíka“. (Test values are fake.)
"""

from __future__ import annotations

from backend.services.uat_provisioner import generate_uat_env

#: The backend service as the NSV standard template writes it: the password comes from ``.env``.
SERVICES = {
    "backend": {
        "environment": {
            "ADMIN_INITIAL_PASSWORD": "${ADMIN_INITIAL_PASSWORD:-}",
            "SECRET_KEY": "${SECRET_KEY:-}",
        }
    }
}


def _render(admin_password, *, preserved=None, synthesised=None):
    return generate_uat_env(
        slug="skit-orthodox-hermenia",
        project="orthodox-hermenia",
        version="v0.1.0",
        services=SERVICES,
        be_service="backend",
        db_service=None,
        source_env_example={"ADMIN_INITIAL_PASSWORD": "", "SECRET_KEY": ""},
        db_user="app",
        db_name="app",
        shared_db_password="pw",
        admin_password=admin_password,
        preserved_secrets=preserved,
        synthesised_secrets=synthesised,
    )


def _value(env: str, key: str) -> str:
    return [line.split("=", 1)[1] for line in env.splitlines() if line.startswith(f"{key}=")][0]


def test_the_customers_password_wins_over_the_forwarded_form():
    synthesised: list[str] = []

    env = _render("skit-heslo", synthesised=synthesised)

    assert _value(env, "ADMIN_INITIAL_PASSWORD") == "skit-heslo"
    assert synthesised == ["SECRET_KEY"], "the customer supplied the admin password — it is no random secret"


def test_a_redeploy_realigns_it_over_the_random_value_kept_from_before():
    env = _render("skit-heslo", preserved={"ADMIN_INITIAL_PASSWORD": "f" * 64, "SECRET_KEY": "k" * 64})

    assert _value(env, "ADMIN_INITIAL_PASSWORD") == "skit-heslo"
    assert _value(env, "SECRET_KEY") == "k" * 64


def test_without_a_customers_password_it_stays_a_random_secret():
    synthesised: list[str] = []

    env = _render(None, synthesised=synthesised)

    assert len(_value(env, "ADMIN_INITIAL_PASSWORD")) >= 32
    assert "ADMIN_INITIAL_PASSWORD" in synthesised
