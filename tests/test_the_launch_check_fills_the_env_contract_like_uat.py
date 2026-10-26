"""DEV-229 — the launch check fills a project's ``.env`` by the same contract as a UAT installation.

26.10.2026, Author Asistent 0.1.0, Verifikácia: once the cockpit's own ``DATABASE_URL`` no longer reached the stack
(DEV-227), the backend still did not boot — ``ConfigError``: ``PUBLIC_URL``, ``SESSION_SECRET``,
``SETTINGS_ENCRYPTION_KEY`` empty. The project marks them by the cockpit's contract (``# @public-url``; secrets left
empty, „the cockpit generates them“), and a UAT deploy fills them (uat_provisioner); the launch check only dropped
empty values (ICCINT-40) and generated nothing. Director: „Áno, súhlasím, založ tiket a oprav to“.

Pinned: an empty secret gets a value by UAT's own rule; an ``@public-url`` address the smoke stack's; what the customer
enters (``@customer``/``@customer-secret``) and what has no contract stay unset; the database password stays the one
the smoke database has.

Extended the same day: with its configuration filled the backend stopped at its first account — ``AUTHOR_USERNAME``
(``@customer required``) and ``AUTHOR_INITIAL_PASSWORD`` (``@customer-secret required``), which the Manažér enters for a
UAT install and the project's ``release_smoke_test.sh`` logs in with. A REQUIRED customer value gets a test value in
the throwaway stack — a secret by UAT's rule, any other a fixed one; an optional one stays unset. Director: „Áno,
súhlasím, rozšír DEV-229 a oprav to“.
"""

from __future__ import annotations

import base64

from backend.services import orchestrator, uat_provisioner

#: The shape of Author Asistent's ``.env.example`` (no values copied), plus a customer secret and a plain empty one.
EXAMPLE = """# --- Customer settings (the cockpit asks for them at installation) ---
# @customer required: „Prihlasovacie meno“
AUTHOR_USERNAME=
# @customer: „Zobrazované meno“
AUTHOR_DISPLAY_NAME=
# @customer-secret required: „Počiatočné heslo“
AUTHOR_INITIAL_PASSWORD=
# --- Address the browser opens (the cockpit fills it in per installation) ---
# @public-url
PUBLIC_URL=
# @public-url-admin
ADMIN_URL=
# --- The app's own secrets (the cockpit generates them; no marker) ---
SETTINGS_ENCRYPTION_KEY=
SESSION_SECRET=
WEBHOOK_TOKEN=
# @customer-secret
SMTP_PASSWORD=
CORS_ALLOW_ORIGINS=
POSTGRES_PASSWORD=
DATABASE_URL=postgresql+pg8000://author_asistent:${POSTGRES_PASSWORD}@db:5432/author_asistent
LOG_LEVEL=info
"""


def _render(tmp_path) -> dict[str, str]:
    src = tmp_path / ".env.example"
    src.write_text(EXAMPLE, encoding="utf-8")
    dst = tmp_path / "smoke.env"
    assert orchestrator._render_smoke_env(src, dst) is True
    lines = dst.read_text(encoding="utf-8").splitlines()
    return dict(line.split("=", 1) for line in lines if "=" in line and not line.startswith("#"))


def test_the_secrets_the_cockpit_generates_are_filled_like_on_uat(tmp_path):
    env = _render(tmp_path)

    assert len(base64.b64decode(env["SETTINGS_ENCRYPTION_KEY"], validate=True)) == 32  # *_KEY: 32 bytes in base64
    assert len(env["SESSION_SECRET"]) == 64 and len(env["WEBHOOK_TOKEN"]) == 64  # the other suffixes: token_hex(32)
    assert env["SESSION_SECRET"] != _render(tmp_path)["SESSION_SECRET"]  # random, not a constant anyone can know


def test_the_secret_rule_is_uats_own(tmp_path, monkeypatch):
    monkeypatch.setattr(uat_provisioner, "synthetic_secret", lambda key: f"by-uats-rule-{key}")

    env = _render(tmp_path)

    assert (env["SESSION_SECRET"], env["SETTINGS_ENCRYPTION_KEY"]) == (
        "by-uats-rule-SESSION_SECRET",
        "by-uats-rule-SETTINGS_ENCRYPTION_KEY",
    )


def test_a_public_address_is_the_smoke_stacks(tmp_path):
    env = _render(tmp_path)

    assert (env["PUBLIC_URL"], env["ADMIN_URL"]) == (orchestrator.SMOKE_PUBLIC_URL, orchestrator.SMOKE_PUBLIC_URL)


def test_a_required_customer_value_gets_a_test_value(tmp_path):
    """The throwaway stack has nobody to enter it; the app needs it to boot and the smoke script to log in."""
    env = _render(tmp_path)

    assert env["AUTHOR_USERNAME"] == orchestrator.SMOKE_CUSTOMER_VALUE
    assert len(env["AUTHOR_INITIAL_PASSWORD"]) == 64  # a secret: by UAT's rule (token_hex(32)), never a constant


def test_what_the_customer_enters_optionally_and_what_has_no_contract_stay_unset(tmp_path):
    env = _render(tmp_path)

    assert "AUTHOR_DISPLAY_NAME" not in env  # @customer, optional: the app keeps its own default
    assert "SMTP_PASSWORD" not in env  # @customer-secret, optional: never a synthetic, as on UAT with nothing entered
    assert "CORS_ALLOW_ORIGINS" not in env  # ICCINT-40: empty means unset, not the empty string
    assert env["LOG_LEVEL"] == "info"


def test_the_database_password_stays_the_smoke_databases(tmp_path):
    env = _render(tmp_path)

    lines = (tmp_path / "smoke.env").read_text(encoding="utf-8").splitlines()
    assert [line for line in lines if line.startswith("POSTGRES_PASSWORD=")] == [
        f"POSTGRES_PASSWORD={orchestrator._SMOKE_ENV_PASSWORD}"
    ]  # once — a synthetic one first would be a second, different password
    assert env["POSTGRES_PASSWORD"] == orchestrator._SMOKE_ENV_PASSWORD
