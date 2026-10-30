"""DEV-271 — the door check after a deploy asks where the internet's request ends up, as somebody the Manager has.

30.10.2026 NEX Knowledge 0.2.0 on ICC's UAT: the deploy said „do aplikácie sa nedá vojsť: aplikácia vstupenku
odmietla novým tvarom (v tele) (stav 403) … neodovzdávaj používateľom“. The app never saw the ticket — its logs had no
request at all. The check went from the cockpit's container to the installation's public name; NEX Knowledge is
private (``*.int.isnex.eu``), and the zone's nginx lets in Tailscale only, so nginx answered 403. And the ticket was
for „rausch“, whom ICC's NEX Manager does not know — through any door it would have failed, and blamed the app again.

Now the door is asked at the shared Traefik with the installation's name in ``Host`` (a public installation the same
way — the edge's WAF refuses a container too), and the Manager is asked about the operator first.
"""

from __future__ import annotations

import uuid
from typing import Callable

import httpx
from jose import jwt

from backend.config.settings import settings
from backend.services import deploy as deploy_service
from backend.services import uat_launch, uat_provisioner
from backend.tests.test_uat_launch import _write_env

KEY = "test-launch-signing-key-0123456789"
PRIVATE = "https://uat-icc-knowledge.int.isnex.eu"
NAME = "uat-icc-knowledge.int.isnex.eu"


def _pair(tmp_path, monkeypatch) -> None:
    """ICC's NEX Manager and NEX Knowledge, paired as the deploy pairs them."""
    monkeypatch.setattr(uat_provisioner, "UAT_ROOT", tmp_path)
    _write_env(
        tmp_path / "icc" / "nex-knowledge",
        MANAGER_LAUNCH_SIGNING_KEY=KEY,
        MANAGER_MODULE_SLUG="nex-knowledge",
        MANAGER_DEPLOY_SLUG="uat-icc",
        NEX_MANAGER_BASE_URL="http://uat-icc-manager-backend:8000",
        NEX_MANAGER_API_KEY="module-key-0123456789",
    )
    _write_env(tmp_path / "icc" / "nex-manager", LAUNCH_SIGNING_KEY=KEY, DEPLOY_SLUG="uat-icc")


def _traefik(monkeypatch, app: Callable[[httpx.Request], httpx.Response]) -> list[httpx.Request]:
    """The network as it is: Traefik routes by ``Host`` and answers its own 404 for a name it has no route for; the
    edge in front of the public name refuses a container (403) — it is never reached here, or the test fails."""
    seen: list[httpx.Request] = []

    def network(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        port = request.url.port or {"http": 80, "https": 443}[request.url.scheme]  # httpx leaves a default port out
        if (request.url.host, port) != (settings.uat_traefik_host, settings.uat_traefik_port):
            return httpx.Response(403, text="403 Forbidden — nginx")
        if request.headers.get("host") != NAME:
            return httpx.Response(404, text="404 page not found")
        return app(request)

    real = httpx.Client
    monkeypatch.setattr(uat_launch.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(network), **kw))
    return seen


def _knowledge(request: httpx.Request) -> httpx.Response:
    """NEX Knowledge: checks the ticket like the template, answers with a ``Secure`` session cookie."""
    if request.url.path == "/api/v1/launch":
        token = dict(httpx.QueryParams(request.content.decode()))["lt"]
        claims = jwt.decode(token, KEY, algorithms=["HS256"], audience="nex-knowledge")
        assert claims["deploy"] == "uat-icc" and claims["sub"] == "zoltan"
        return httpx.Response(303, headers={"set-cookie": "nk_session=abc; Path=/; HttpOnly; Secure; SameSite=Lax"})
    if request.url.path == "/api/v1/session":
        return httpx.Response(200 if "nk_session=abc" in request.headers.get("cookie", "") else 401)
    return httpx.Response(404)


def test_a_private_installation_is_entered_through_traefik_by_its_name(tmp_path, monkeypatch):
    _pair(tmp_path, monkeypatch)
    seen = _traefik(monkeypatch, _knowledge)

    opens, detail = uat_launch.uat_door_opens("icc", "nex-knowledge", PRIVATE, subject="zoltan")

    assert (opens, detail) == (True, "vstup jedným kliknutím overený až po prihlásené sedenie")
    assert [(r.method, r.url.path) for r in seen] == [("POST", "/api/v1/launch"), ("GET", "/api/v1/session")]
    assert {r.headers["host"] for r in seen} == {NAME}


def test_a_ticket_the_app_really_refuses_is_still_reported(tmp_path, monkeypatch):
    _pair(tmp_path, monkeypatch)
    _traefik(monkeypatch, lambda request: httpx.Response(401))

    opens, detail = uat_launch.uat_door_opens("icc", "nex-knowledge", PRIVATE, subject="zoltan")

    assert (opens, detail) == (False, "aplikácia vstupenku odmietla novým tvarom (v tele) (stav 401)")


# ── the operator, asked first ────────────────────────────────────────────────────────────────────────────────────


def _manager_says(monkeypatch, status: int, body: object = None) -> list[str]:
    asked: list[str] = []

    def get(url, headers=None, timeout=None):
        asked.append(url)
        return httpx.Response(status, json=body) if body is not None else httpx.Response(status)

    monkeypatch.setattr(uat_launch.httpx, "get", get)
    return asked


class _Db:
    """The deploy's question „who deployed?“ — answered „rausch“, as on 30.10.2026."""

    def execute(self, *a, **k):
        class _R:
            def scalar_one_or_none(self_inner):
                return "rausch"

        return _R()


class _Knowledge:
    slug = "nex-knowledge"


def _warning(monkeypatch, door: Callable) -> str | None:
    monkeypatch.setattr(deploy_service, "_customer_dir_slug", lambda customer: "icc")
    monkeypatch.setattr(deploy_service, "_instance_url", lambda *a, **k: PRIVATE)
    monkeypatch.setattr(uat_launch, "uat_door_opens", door)
    return deploy_service._uat_door_warning(_Db(), object(), _Knowledge(), uuid.uuid4())


def _door_must_not_be_tried(*a, **k):
    raise AssertionError("vstupenka na meno, ktoré Manager nepustí, sa nemá skúšať")


IDENTITY = {"login": "rausch", "email": "r@icc.sk", "role": "user", "is_active": True, "groups": []}


def test_an_operator_the_manager_does_not_know_is_named_not_the_app_blamed(tmp_path, monkeypatch):
    _pair(tmp_path, monkeypatch)
    asked = _manager_says(monkeypatch, 404, {"code": "NXM-USER-NOT-FOUND", "message": "Používateľ neexistuje."})

    warning = _warning(monkeypatch, _door_must_not_be_tried)

    assert asked == ["http://uat-icc-manager-backend:8000/api/v1/identity/rausch"]
    assert warning == (
        "Vstup do aplikácie kokpit neoveril: NEX Manager zákazníka nepozná používateľa „rausch“, takže vstupenka "
        "na jeho meno by neprešla. Vyskúšaj vstup z NEX Managera účtom, ktorý má aplikáciu pridelenú."
    )


def test_an_operator_without_the_app_assigned_is_named(tmp_path, monkeypatch):
    _pair(tmp_path, monkeypatch)
    _manager_says(monkeypatch, 200, {**IDENTITY, "has_module_access": False})

    warning = _warning(monkeypatch, _door_must_not_be_tried)

    assert warning is not None and "používateľ „rausch“ nemá v NEX Manageri zákazníka pridelenú aplikáciu" in warning


def test_a_switched_off_operator_is_named(tmp_path, monkeypatch):
    _pair(tmp_path, monkeypatch)
    _manager_says(monkeypatch, 403, {"code": "NXM-USER-INACTIVE", "message": "Používateľ je deaktivovaný."})

    warning = _warning(monkeypatch, _door_must_not_be_tried)

    assert warning is not None and "používateľ „rausch“ je v NEX Manageri zákazníka deaktivovaný" in warning


def test_an_operator_the_manager_lets_in_goes_through_the_door(tmp_path, monkeypatch):
    _pair(tmp_path, monkeypatch)
    _manager_says(monkeypatch, 200, {**IDENTITY, "has_module_access": True})
    tried: list[str] = []

    def door(customer, project, url, *, subject):
        tried.append(subject)
        return False, "aplikácia vstupenku odmietla novým tvarom (v tele) (stav 401)"

    warning = _warning(monkeypatch, door)

    assert tried == ["rausch"]
    assert warning is not None and warning.startswith("Nasadenie prebehlo, ale do aplikácie sa nedá vojsť")


def test_a_manager_that_cannot_be_asked_leaves_the_door_to_decide(tmp_path, monkeypatch):
    """The module's own key refused (401) says nothing about the operator — the door is tried as before."""
    _pair(tmp_path, monkeypatch)
    _manager_says(monkeypatch, 401, {"code": "NXM-AUTH-INVALID", "message": "x"})
    tried: list[str] = []

    warning = _warning(monkeypatch, lambda *a, subject, **k: tried.append(subject) or (True, "ok"))

    assert tried == ["rausch"] and warning is None
