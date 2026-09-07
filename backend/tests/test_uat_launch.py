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
    url = uat_launch.build_uat_launch_url("acme", "demo-app", "https://uat-acme-app.isnex.eu", subject="zoltan")
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
    # ICCINT-61: the ticket. This used to assert "uat-test" — a name no Manager can resolve, so the launch
    # died inside the app every time. The subject is the operator who clicked; that is authentication, not
    # impersonation.
    assert claims["sub"] == "zoltan"
    assert claims["deploy"] == "uat-acme"
    assert claims["exp"] - claims["iat"] <= 60  # under the app's hard cap


def test_none_when_no_launch_key(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(uat_provisioner, "UAT_ROOT", tmp_path)
    _write_env(tmp_path / "acme" / "pw-app", SOME_OTHER="x")  # not token-launch (no launch key)
    assert uat_launch.build_uat_launch_url("acme", "pw-app", "https://x", subject="zoltan") is None


def test_none_when_no_uat_url(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(uat_provisioner, "UAT_ROOT", tmp_path)
    assert uat_launch.build_uat_launch_url("acme", "app", "", subject="zoltan") is None


def test_customer_dir_slug_is_lowercased() -> None:
    """v4.0.31 regression: the launch endpoint must locate the deploy .env under the CANONICAL customer
    dir slug (lowercased subdomain-or-slug) — a mixed-case DB slug like 'ANDROS' → dir 'andros'. Passing
    the raw slug pointed at a non-existent /opt/uat/ANDROS/... → no key → a spurious 400."""
    from types import SimpleNamespace

    from backend.services import deploy

    assert deploy._customer_dir_slug(SimpleNamespace(slug="ANDROS", subdomain=None)) == "andros"
    assert deploy._customer_dir_slug(SimpleNamespace(slug="X", subdomain=" Andros ")) == "andros"


# ── ICCINT-61: the ticket is that the button could never log anybody in ──────


def _manager_answering(monkeypatch, status_code: int):
    """The paired Manager, answering one question about one operator."""
    captured: dict[str, object] = {}

    class _Answer:
        status_code = 0

    def fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers or {}
        answer = _Answer()
        answer.status_code = status_code
        return answer

    monkeypatch.setattr(uat_launch.httpx, "get", fake_get)
    return captured


def _paired_env(tmp_path, monkeypatch):
    monkeypatch.setattr(uat_provisioner, "UAT_ROOT", tmp_path)
    _write_env(
        tmp_path / "acme" / "demo-app",
        NEX_MANAGER_BASE_URL="https://manager.example",
        MANAGER_MODULE_SLUG="demo-app",
        NEX_MANAGER_API_KEY="module-key-0123456789",
    )


def test_an_unknown_operator_is_reported_not_launched(tmp_path, monkeypatch) -> None:
    """The whole ticket in one assertion. Before ICCINT-61 the subject was ``uat-test``, so the Manager's
    answer was ALWAYS "no such person" — and nobody asked in advance, so the operator saw the app's own
    "Prihlásenie skončilo" and went looking for the fault in the app. For five days."""
    _paired_env(tmp_path, monkeypatch)
    _manager_answering(monkeypatch, 404)

    assert uat_launch.manager_knows_operator("acme", "demo-app", "zoltan") is False


def test_a_known_operator_gets_through(tmp_path, monkeypatch) -> None:
    _paired_env(tmp_path, monkeypatch)
    captured = _manager_answering(monkeypatch, 200)

    assert uat_launch.manager_knows_operator("acme", "demo-app", "zoltan") is True
    # BOTH headers, the shape the Manager actually requires — sending only one is what broke
    # nex-productcatalogs across two versions.
    assert captured["headers"]["X-Module-Slug"] == "demo-app"
    assert captured["headers"]["Authorization"].startswith("Bearer ")
    assert str(captured["url"]).endswith("/api/v1/identity/zoltan")


def test_a_manager_we_cannot_ask_does_not_block(tmp_path, monkeypatch) -> None:
    """Not being able to ask is not evidence the operator is unknown — same rule as the CI floor. A check
    that stops on ignorance is one people learn to route around."""
    _paired_env(tmp_path, monkeypatch)

    def boom(url, headers=None, timeout=None):
        raise uat_launch.httpx.ConnectError("nedostupný")

    monkeypatch.setattr(uat_launch.httpx, "get", boom)

    assert uat_launch.manager_knows_operator("acme", "demo-app", "zoltan") is None


def test_an_app_with_no_paired_manager_is_not_asked_about(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(uat_provisioner, "UAT_ROOT", tmp_path)
    _write_env(tmp_path / "acme" / "solo-app", SOME_OTHER="x")

    def never(url, headers=None, timeout=None):  # pragma: no cover — must not run
        raise AssertionError("appka bez spárovaného Managera sa nemá koho pýtať")

    monkeypatch.setattr(uat_launch.httpx, "get", never)

    assert uat_launch.manager_knows_operator("acme", "solo-app", "zoltan") is None


def test_the_made_up_name_cannot_come_back() -> None:
    """``uat-test`` was not a typo — it was a deliberate constant with a comment defending it ("no
    impersonation"). A defended mistake comes back, so it gets a guard rather than just a deletion.

    Guarded by SHAPE, not by hunting the string: the docstrings deliberately name ``uat-test`` to explain
    what went wrong, and a guard that forbids naming a mistake makes the code less honest, not safer.

      * ``subject`` is keyword-only with NO default — nobody can mint without deciding whose ticket it is;
      * the endpoint decides it as the operator who clicked. Swap that for any fixed string and the button
        silently returns to being unopenable, with the failure surfacing inside the app, three systems away
        from the line that caused it. That distance is what cost the five days.
    """
    import inspect
    from pathlib import Path

    subject = inspect.signature(uat_launch.build_uat_launch_url).parameters["subject"]
    assert subject.kind is inspect.Parameter.KEYWORD_ONLY
    assert subject.default is inspect.Parameter.empty, "razenie vstupenky nesmie mať predvolené meno"

    route = (Path(uat_launch.__file__).parents[1] / "api" / "routes" / "deploy.py").read_text(encoding="utf-8")
    assert "subject=_current_user.username" in route, (
        "vstupenka sa už nerazí na meno toho, kto klikol — takú Manager nevie priradiť k nikomu"
    )
