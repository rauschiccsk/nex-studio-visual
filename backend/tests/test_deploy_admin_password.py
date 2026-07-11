"""The deployed app's admin login = the customer secret, not a random synthetic (self-sufficiency, 2026-07-11).

A per-customer deploy seeds the app's ``admin`` user from ``ADMIN_INITIAL_PASSWORD``. The provisioner randomises
every ``*_password/_secret/_key/_token`` env var to a synthetic value — correct for machine secrets, but for the
ONE human-facing credential (the manager's initial admin login) it left ``admin`` with a password nobody could
discover → the manager was locked out of their own instance. Now the render sets ``ADMIN_INITIAL_PASSWORD`` to
the customer secret (which the manager set + knows); ALL other secrets stay synthetic. (Test values are fake.)
"""

from __future__ import annotations

from backend.services.uat_provisioner import generate_uat_env


def _render(admin_password):
    return generate_uat_env(
        slug="andros-payables",
        project="nex-payables",
        version="v1.1.0",
        services={},
        be_service=None,
        db_service=None,
        source_env_example={"ADMIN_INITIAL_PASSWORD": "example-default", "SECRET_KEY": "example-key"},
        db_user="app",
        db_name="app",
        shared_db_password="pw",
        admin_password=admin_password,
    )


def test_admin_password_is_the_customer_secret_when_provided() -> None:
    env = _render("customer-set-secret")
    # The manager's login is the customer secret — knowable, NOT the .env.example default nor a random synthetic.
    assert "ADMIN_INITIAL_PASSWORD=customer-set-secret" in env
    assert "ADMIN_INITIAL_PASSWORD=example-default" not in env
    # Every OTHER secret stays synthetic (not the example value).
    assert "SECRET_KEY=example-key" not in env


def test_admin_password_stays_synthetic_when_no_customer_secret() -> None:
    env = _render(None)
    # No customer secret → the key falls back to the synthetic path (never the plain .env.example default).
    assert "ADMIN_INITIAL_PASSWORD=example-default" not in env
    assert "ADMIN_INITIAL_PASSWORD=" in env  # still emitted (bootstrap needs it), just synthetic
