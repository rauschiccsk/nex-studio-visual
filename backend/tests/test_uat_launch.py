"""v4.0.30: the UAT 'Spustiť' — mints a §4.4-valid launch token from the app's deploy .env, so the
Manager can open a deployed token-launch app logged-in directly from the UAT tab."""

from __future__ import annotations

from pathlib import Path

from jose import jwt  # python-jose — the backend's declared JWT lib (same one the app verifies with)

from backend.services import uat_launch, uat_provisioner


def _write_env(root: Path, **kv: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    root.joinpath(".env").write_text("\n".join(f"{k}={v}" for k, v in kv.items()), encoding="utf-8")


def test_build_launch_url_mints_a_verifiable_token(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(uat_provisioner, "UAT_ROOT", tmp_path)
    key = "test-launch-signing-key-0123456789"
    _write_env(
        tmp_path / "acme" / "demo-app",
        MANAGER_LAUNCH_SIGNING_KEY=key,
        MANAGER_MODULE_SLUG="demo-app",
        MANAGER_DEPLOY_SLUG="uat-acme",
    )
    url = uat_launch.build_uat_launch_url("acme", "demo-app", "https://uat-acme-app.isnex.eu")
    assert url and "/api/v1/launch?lt=" in url
    # The token must decode the SAME way the app verifies it → the app would accept it.
    # jose raises if aud/iss don't match (verified via kwargs), so a clean decode == the app accepts it.
    claims = jwt.decode(
        url.split("lt=")[1],
        key,
        algorithms=["HS256"],
        audience="demo-app",
        issuer="nex-manager",
    )
    assert claims["purpose"] == "module-launch"
    assert claims["sub"] == "uat-test"  # a test identity, never a real user
    assert claims["deploy"] == "uat-acme"
    assert claims["exp"] - claims["iat"] <= 60  # under the app's hard cap


def test_none_when_no_launch_key(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(uat_provisioner, "UAT_ROOT", tmp_path)
    _write_env(tmp_path / "acme" / "pw-app", SOME_OTHER="x")  # not token-launch (no launch key)
    assert uat_launch.build_uat_launch_url("acme", "pw-app", "https://x") is None


def test_none_when_no_uat_url(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(uat_provisioner, "UAT_ROOT", tmp_path)
    assert uat_launch.build_uat_launch_url("acme", "app", "") is None


def test_customer_dir_slug_is_lowercased() -> None:
    """v4.0.31 regression: the launch endpoint must locate the deploy .env under the CANONICAL customer
    dir slug (lowercased subdomain-or-slug) — a mixed-case DB slug like 'ANDROS' → dir 'andros'. Passing
    the raw slug pointed at a non-existent /opt/uat/ANDROS/... → no key → a spurious 400."""
    from types import SimpleNamespace

    from backend.services import deploy

    assert deploy._customer_dir_slug(SimpleNamespace(slug="ANDROS", subdomain=None)) == "andros"
    assert deploy._customer_dir_slug(SimpleNamespace(slug="X", subdomain=" Andros ")) == "andros"
