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

from pathlib import Path

from backend.services.uat_provisioner import generate_uat_env, read_paired_manager_launch

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
