"""Token-launch key wiring (v4.0.20): a NEX-Manager-launched module shares the
paired Manager Deploy's ``LAUNCH_SIGNING_KEY`` instead of getting a synthetic one.

A module launched from NEX Manager must verify launch tokens with the SAME HS256 key
the Manager signs them with. The provisioner used to randomise every ``*_key`` var to a
synthetic value — which for ``MANAGER_LAUNCH_SIGNING_KEY`` never matches the Manager, so
every launch 401'd (nex-shopify crash-test, 2026-07-21). Now a token-launch app (one that
DECLARES ``MANAGER_LAUNCH_SIGNING_KEY``) gets that key + ``MANAGER_DEPLOY_SLUG`` wired from
the paired Manager's ``.env`` (a sibling under the same customer root). Test values are fake.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from backend.services.uat_provisioner import (
    generate_uat_env,
    launch_pairing_warning,
    provision_uat,
    read_paired_manager_launch,
    validate_rendered_launch_wiring,
)

# A token-launch app's declared env (as it appears in the source compose / .env.example).
_TOKEN_LAUNCH_ENV = {
    "MANAGER_LAUNCH_SIGNING_KEY": "${MANAGER_LAUNCH_SIGNING_KEY:-}",
    "MANAGER_SESSION_SIGNING_KEY": "${MANAGER_SESSION_SIGNING_KEY:-}",
    "MANAGER_MODULE_SLUG": "${MANAGER_MODULE_SLUG:-nex-shopify}",
    "MANAGER_DEPLOY_SLUG": "${MANAGER_DEPLOY_SLUG:-}",
}


def _write_manager_env(dir_: Path) -> Path:
    p = dir_ / ".env"
    p.write_text(
        "# manager deploy env\nLAUNCH_SIGNING_KEY=manager-shared-launch-key-FAKE\nDEPLOY_SLUG=andros-uat\n",
        encoding="utf-8",
    )
    return p


def _render(source_env, *, manager_env_path=None, preserved=None):
    return generate_uat_env(
        slug="andros-shopify",
        project="nex-shopify",
        version="v0.2.0",
        services={},
        be_service=None,
        db_service=None,
        source_env_example=source_env,
        db_user="app",
        db_name="app",
        shared_db_password="pw",
        preserved_secrets=preserved,
        manager_env_path=manager_env_path,
    )


# ── read_paired_manager_launch ───────────────────────────────────────────────


def test_read_paired_manager_launch_reads_key_and_slug(tmp_path: Path) -> None:
    key, slug = read_paired_manager_launch(_write_manager_env(tmp_path))
    assert key == "manager-shared-launch-key-FAKE"
    assert slug == "andros-uat"


def test_read_paired_manager_launch_missing_file_is_none(tmp_path: Path) -> None:
    assert read_paired_manager_launch(tmp_path / "nope" / ".env") == (None, None)


# ── generate_uat_env wiring ──────────────────────────────────────────────────


def test_token_launch_key_is_wired_from_paired_manager(tmp_path: Path) -> None:
    env = _render(_TOKEN_LAUNCH_ENV, manager_env_path=_write_manager_env(tmp_path))
    # The launch key is the Manager's shared key — NOT a synthetic placeholder.
    assert "MANAGER_LAUNCH_SIGNING_KEY=manager-shared-launch-key-FAKE" in env
    assert "MANAGER_LAUNCH_SIGNING_KEY=__UAT_SYNTHETIC__" not in env
    # The deploy slug is the Manager's (deploy pin lines up).
    assert "MANAGER_DEPLOY_SLUG=andros-uat" in env


def test_session_key_stays_synthetic_for_token_launch(tmp_path: Path) -> None:
    env = _render(_TOKEN_LAUNCH_ENV, manager_env_path=_write_manager_env(tmp_path))
    # The module-private session key is NOT the Manager's key — it stays synthetic.
    line = next(li for li in env.splitlines() if li.startswith("MANAGER_SESSION_SIGNING_KEY="))
    value = line.split("=", 1)[1]
    assert value and value != "manager-shared-launch-key-FAKE"


def test_no_paired_manager_leaves_launch_key_empty(tmp_path: Path) -> None:
    # Token-launch app but the Manager is not deployed at the paired path → the launch
    # key is EMPTY (token-launch cleanly off), never a synthetic that would 401 launches.
    env = _render(_TOKEN_LAUNCH_ENV, manager_env_path=tmp_path / "absent" / ".env")
    assert "MANAGER_LAUNCH_SIGNING_KEY=\n" in env or env.rstrip().endswith("MANAGER_LAUNCH_SIGNING_KEY=")
    assert "MANAGER_LAUNCH_SIGNING_KEY=__UAT_SYNTHETIC__" not in env


def test_non_token_app_never_reads_manager(tmp_path: Path) -> None:
    # An app that does NOT declare MANAGER_LAUNCH_SIGNING_KEY is untouched by the wiring.
    env = _render({"SOME_SECRET_KEY": "${SOME_SECRET_KEY:-}"}, manager_env_path=_write_manager_env(tmp_path))
    assert "MANAGER_LAUNCH_SIGNING_KEY" not in env
    # Its own secret still gets the normal synthetic treatment.
    assert "SOME_SECRET_KEY=" in env


def test_redeploy_manager_key_wins_over_preserved(tmp_path: Path) -> None:
    # On redeploy the FRESH Manager key wins (a Manager key rotation propagates), not the
    # stale preserved value.
    env = _render(
        _TOKEN_LAUNCH_ENV,
        manager_env_path=_write_manager_env(tmp_path),
        preserved={"MANAGER_LAUNCH_SIGNING_KEY": "stale-old-key"},
    )
    assert "MANAGER_LAUNCH_SIGNING_KEY=manager-shared-launch-key-FAKE" in env
    assert "stale-old-key" not in env


# ── the missing pairing is a WARNING, never a failure (2026-07-28) ────────────
#
# Nothing in NEX Studio creates ``<root>/<customer>/nex-manager/.env`` — it appears only once NEX Manager
# is itself deployed to that customer — so a customer's FIRST token-launch deploy renders the key empty.
# The instance is nonetheless complete and serving, so the provisioner reports the gap and carries on.


def test_unpaired_render_warns_and_names_the_remedy(tmp_path: Path) -> None:
    env = _render(_TOKEN_LAUNCH_ENV, manager_env_path=tmp_path / "absent" / ".env")
    warnings = validate_rendered_launch_wiring(env, customer_slug="andros")
    assert warnings == [launch_pairing_warning("andros")]
    message = warnings[0]
    assert "andros" in message  # WHICH customer is unpaired
    assert "NEX Manager" in message  # WHAT is missing
    assert "nasaď" in message.lower()  # WHAT to do about it — an action, not a diagnosis


def test_wired_render_produces_no_warning(tmp_path: Path) -> None:
    env = _render(_TOKEN_LAUNCH_ENV, manager_env_path=_write_manager_env(tmp_path))
    assert validate_rendered_launch_wiring(env, customer_slug="andros") == []


def test_non_token_app_produces_no_launch_warning(tmp_path: Path) -> None:
    """An app that never declares the launch key is not token-launch — warning about a pairing it does not
    need would be noise on every ordinary password app."""
    env = _render({"SOME_SECRET_KEY": "${SOME_SECRET_KEY:-}"}, manager_env_path=_write_manager_env(tmp_path))
    assert validate_rendered_launch_wiring(env, customer_slug="andros") == []


# ── provision_uat end-to-end: a first deploy to a new customer ───────────────

_TOKEN_APP_COMPOSE = textwrap.dedent(
    """
    services:
      db:
        image: postgres:16-alpine
      backend:
        build: .
        environment:
          MANAGER_LAUNCH_SIGNING_KEY: ${MANAGER_LAUNCH_SIGNING_KEY:-}
          MANAGER_MODULE_SLUG: ${MANAGER_MODULE_SLUG:-shopify}
          MANAGER_DEPLOY_SLUG: ${MANAGER_DEPLOY_SLUG:-}
      frontend:
        build: ./frontend
    """
)


def _provision_token_app(tmp_path: Path, *, paired: bool):
    """Provision nex-shopify UAT for customer ``andros`` — with or without a paired NEX Manager deploy."""
    project = tmp_path / "projects" / "nex-shopify"
    project.mkdir(parents=True)
    (project / "docker-compose.yml").write_text(_TOKEN_APP_COMPOSE, encoding="utf-8")
    if paired:
        manager_dir = tmp_path / "uat" / "andros" / "nex-manager"
        manager_dir.mkdir(parents=True)
        _write_manager_env(manager_dir)
    return provision_uat(
        "nex-shopify",
        "andros-uat",
        projects_root=tmp_path / "projects",
        uat_root=tmp_path / "uat",
        prod_root=tmp_path / "customers",
        environment="uat",
        customer_slug="andros",
        app="shopify",
        full_project_slug="nex-shopify",
    )


def test_first_deploy_to_an_unpaired_customer_provisions_AND_warns(tmp_path: Path) -> None:
    """The provisioning itself SUCCEEDS — compose + .env are on disk — and the missing pairing comes back
    as a warning. It must never become an exception: the caller records a failed deploy for a raise, and a
    failed UAT deploy also blocks Akceptovať, i.e. PROD, for that customer."""
    result = _provision_token_app(tmp_path, paired=False)

    assert result.compose_path.is_file()
    assert result.env_path.is_file()
    assert launch_pairing_warning("andros") in result.warnings
    # Rendered EMPTY (never a synthetic that would 401 every launch) — the evidence the warning is about.
    assert "MANAGER_LAUNCH_SIGNING_KEY=\n" in result.env_path.read_text(encoding="utf-8")


def test_first_deploy_to_a_paired_customer_warns_about_nothing(tmp_path: Path) -> None:
    result = _provision_token_app(tmp_path, paired=True)

    assert result.warnings == []
    assert "MANAGER_LAUNCH_SIGNING_KEY=manager-shared-launch-key-FAKE" in result.env_path.read_text(encoding="utf-8")


# ── ICCINT-58: a placeholder from .env.example must NOT be written as an empty string ──


def test_a_template_placeholder_is_left_unset_not_written_empty():
    """The first UAT deploy of nex-productcatalogs 0.1.2 (05.09.2026) died on exactly this.

    ``CORS_ALLOW_ORIGINS=`` in ``.env.example`` means "fill this in". Written into the UAT ``.env`` as an empty
    string it is PARSED, and pydantic-settings JSON-decodes a complex-typed field straight from the environment,
    ahead of the app's own validators: ``SettingsError: error parsing value for field "cors_allow_origins"``.
    The migrate container exited 1, the backend never started, and the deploy reported only "service backend is
    not running". Unset, the app's own default applies and it boots.
    """
    rendered = _render({"CORS_ALLOW_ORIGINS": "", "APP_NAME": "katalog"})

    assert "CORS_ALLOW_ORIGINS" not in rendered, "prázdna hodnota zo šablóny sa zapísala do .env"
    assert "APP_NAME=katalog" in rendered  # a real value is untouched


def test_the_token_launch_key_is_still_wired_from_an_empty_template_entry(tmp_path):
    """The placeholder rule must NOT reach the token-launch key (AND-42).

    ``MANAGER_LAUNCH_SIGNING_KEY`` is empty in the template on purpose: its PRESENCE is the flag that this app
    is launched from the paired NEX Manager, and the provisioner fills it from that Manager. A blanket
    drop-every-empty would have silently un-wired one-click launch — the very thing 0.1.2 exists to deliver.
    """
    manager_env = tmp_path / ".env"
    manager_env.write_text("LAUNCH_SIGNING_KEY=k-from-manager\nDEPLOY_SLUG=andros\n", encoding="utf-8")

    rendered = _render({"MANAGER_LAUNCH_SIGNING_KEY": ""}, manager_env_path=manager_env)

    assert "MANAGER_LAUNCH_SIGNING_KEY=k-from-manager" in rendered


def test_both_paths_share_one_definition_of_placeholder():
    """ICCINT-40 fixed the smoke renderer and left UAT provisioning broken; four weeks later UAT died on the
    identical error. The test is on the shared predicate, so a third consumer inherits the rule."""
    from backend.services import orchestrator, uat_provisioner

    assert uat_provisioner.is_template_placeholder("")
    assert uat_provisioner.is_template_placeholder("   ")
    assert not uat_provisioner.is_template_placeholder("https://x.sk")
    # the smoke renderer must be USING it, not carrying its own copy
    import inspect

    assert "is_template_placeholder" in inspect.getsource(orchestrator._render_smoke_env)
