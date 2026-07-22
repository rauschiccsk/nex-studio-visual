"""v4.0.30: the UAT 'Spustiť' — mints a §4.4-valid launch token from the app's deploy .env, so the
Manager can open a deployed token-launch app logged-in directly from the UAT tab."""

from __future__ import annotations

from pathlib import Path

import jwt

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
    claims = jwt.decode(
        url.split("lt=")[1],
        key,
        algorithms=["HS256"],
        audience="demo-app",
        issuer="nex-manager",
        options={"require": ["exp", "iat", "iss", "aud", "sub"]},
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
