"""DEV-409 — an app that declares a narrow public entry gets it through a Cloudflare tunnel the cockpit sets up: only
paths under the declared prefix reach the app from the internet, everything else is refused before it.

NEX HelpDesk is called by a voice platform and a telephone operator (tools during a call, webhooks after it) that have
no Tailscale; its approved specification wants only ``/public/v1/`` on the internet, already in UAT, its screens in
Tailscale. Director 17.11.2026, to the cockpit setting the tunnel up itself with a key of its own: „Súhlasím“.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from backend.config.settings import settings
from backend.services import env_contract, installation_names, public_ingress
from backend.services import uat_provisioner as P

EXAMPLE = "SECRET_KEY=change-me\n# @public-ingress: /public/v1/\nPUBLIC_INGRESS_URL=\n"
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


class _Cloudflare:
    """Cloudflare's API as the cockpit's two keys see it — records every call, holds tunnels and records."""

    def __init__(self, records: list[dict[str, Any]] | None = None, tunnels: list[dict[str, Any]] | None = None):
        self.calls: list[tuple[str, str, str, Any]] = []
        self.records = records or []
        self.tunnels = tunnels or []

    def __call__(self, method: str, path: str, *, token: str | None = None, **kwargs: Any) -> Any:
        self.calls.append((method, path, "tunnels" if token == "tunnel-key" else "dns", kwargs.get("json")))
        if path == "/zones":
            return [{"id": "zone-1"}]
        if path.endswith("/cfd_tunnel") and method == "GET":
            return [t for t in self.tunnels if t["name"] == kwargs["params"]["name"]]
        if path.endswith("/cfd_tunnel") and method == "POST":
            self.tunnels.append({"id": "tunnel-1", "name": kwargs["json"]["name"]})
            return {"id": "tunnel-1"}
        if path.endswith("/configurations"):
            return {}
        if path.endswith("/token"):
            return "run-key-of-the-tunnel"
        if path.endswith("/dns_records") and method == "GET":
            return self.records
        return {"id": "record-1"}


@pytest.fixture
def cloudflare(monkeypatch) -> _Cloudflare:
    fake = _Cloudflare()
    monkeypatch.setattr(installation_names, "_cloudflare", fake)
    monkeypatch.setattr(settings, "cloudflare_tunnel_token", "tunnel-key")
    monkeypatch.setattr(settings, "cloudflare_account_id", "acc-1")
    return fake


def _project(tmp_path: Path, monkeypatch, example: str = EXAMPLE) -> None:
    monkeypatch.setattr(P, "_docker_image_exists", lambda image: True)
    project = tmp_path / "projects" / "nex-helpdesk"
    project.mkdir(parents=True)
    (project / "docker-compose.yml").write_text(SOURCE, encoding="utf-8")
    (project / ".env.example").write_text(example, encoding="utf-8")


def _deploy(tmp_path: Path):
    return P.provision_uat(
        "nex-helpdesk",
        "icc",
        projects_root=tmp_path / "projects",
        uat_root=tmp_path / "uat",
        customer_slug="icc",
        app="helpdesk",
        full_project_slug="nex-helpdesk",
        version="0.1.0",
    )


# --- the declaration ----------------------------------------------------------------------------------------------


def test_the_app_declares_the_prefix_that_goes_out() -> None:
    assert env_contract.parse(EXAMPLE).public_ingress == {"PUBLIC_INGRESS_URL": "/public/v1/"}
    wrong = env_contract.parse("# @public-ingress: public\nPUBLIC_INGRESS_URL=\n")
    assert wrong.public_ingress == {}
    assert wrong.problems == ("značka @public-ingress na riadku 1 potrebuje predponu ciest, napr. „/public/v1/“",)


# --- the tunnel in Cloudflare ---------------------------------------------------------------------------------


def test_the_tunnel_sends_only_the_prefix_to_the_backend_and_refuses_the_rest() -> None:
    assert public_ingress.rules(
        "uat-icc-helpdesk-ingress.icc.sk", "/public/v1/", "http://uat-icc-helpdesk-backend:8000"
    ) == {
        "ingress": [
            {
                "hostname": "uat-icc-helpdesk-ingress.icc.sk",
                "path": "^/public/v1/",
                "service": "http://uat-icc-helpdesk-backend:8000",
            },
            {"service": "http_status:403"},
        ]
    }


def test_a_new_entry_gets_a_tunnel_its_rules_and_its_name(cloudflare) -> None:
    entry, said = public_ingress.ensure(
        name="uat-icc-helpdesk-ingress.icc.sk", prefix="/public/v1/", backend_url="http://uat-icc-helpdesk-backend:8000"
    )

    assert said is None
    assert entry == public_ingress.Entry(
        "https://uat-icc-helpdesk-ingress.icc.sk", "/public/v1/", "run-key-of-the-tunnel"
    )
    made = [(m, p, key) for m, p, key, _ in cloudflare.calls]
    assert made == [
        ("GET", "/accounts/acc-1/cfd_tunnel", "tunnels"),
        ("POST", "/accounts/acc-1/cfd_tunnel", "tunnels"),
        ("PUT", "/accounts/acc-1/cfd_tunnel/tunnel-1/configurations", "tunnels"),
        ("GET", "/zones", "dns"),
        ("GET", "/zones/zone-1/dns_records", "dns"),
        ("POST", "/zones/zone-1/dns_records", "dns"),
        ("GET", "/accounts/acc-1/cfd_tunnel/tunnel-1/token", "tunnels"),
    ]
    assert cloudflare.calls[1][3] == {"name": "uat-icc-helpdesk-ingress", "config_src": "cloudflare"}
    record = cloudflare.calls[5][3]
    assert (record["type"], record["content"], record["proxied"]) == ("CNAME", "tunnel-1.cfargotunnel.com", True)


def test_a_redeploy_reuses_the_tunnel_and_keeps_a_name_that_leads_to_it(cloudflare) -> None:
    cloudflare.tunnels.append({"id": "tunnel-1", "name": "uat-icc-helpdesk-ingress"})
    cloudflare.records.append({"id": "r", "type": "CNAME", "content": "tunnel-1.cfargotunnel.com"})

    entry, _ = public_ingress.ensure(
        name="uat-icc-helpdesk-ingress.icc.sk", prefix="/public/v1/", backend_url="http://b:8000"
    )

    assert entry is not None
    assert [m for m, *_ in cloudflare.calls if m in ("POST", "PATCH")] == []


def test_a_name_somebody_else_made_is_never_rewritten(cloudflare) -> None:
    cloudflare.records.append({"id": "r", "type": "A", "content": "100.109.5.21", "comment": "hand-made"})

    entry, said = public_ingress.ensure(
        name="uat-icc-helpdesk-ingress.icc.sk", prefix="/public/v1/", backend_url="http://b:8000"
    )

    assert entry is None
    assert said == public_ingress.NOT_SET_UP.format(
        why="meno uat-icc-helpdesk-ingress.icc.sk už v zóne má záznam, ktorý nezaložil kokpit — nemením ho"
    )
    assert [m for m, *_ in cloudflare.calls if m in ("PATCH",)] == []


def test_without_the_tunnels_key_there_is_no_entry_and_it_is_said(monkeypatch) -> None:
    monkeypatch.setattr(settings, "cloudflare_tunnel_token", "")

    assert public_ingress.ensure(name="x.icc.sk", prefix="/public/v1/", backend_url="http://b:8000") == (
        None,
        public_ingress.NO_KEY,
    )


# --- the installation -------------------------------------------------------------------------------------------


def _services(result) -> dict[str, Any]:
    return yaml.safe_load(result.compose_path.read_text(encoding="utf-8"))["services"]


def test_a_new_installation_runs_the_tunnel_beside_the_app_and_knows_its_address(
    tmp_path, monkeypatch, cloudflare
) -> None:
    _project(tmp_path, monkeypatch)

    result = _deploy(tmp_path)

    connector = _services(result)[public_ingress.SERVICE]
    assert connector["image"] == settings.cloudflared_image
    assert connector["container_name"] == "uat-icc-helpdesk-ingress"
    assert connector["environment"] == {"TUNNEL_TOKEN": "${PUBLIC_INGRESS_TUNNEL_TOKEN}"}
    env = P._parse_env_file(result.env_path)
    assert env["PUBLIC_INGRESS_URL"] == "https://uat-icc-helpdesk-ingress.icc.sk"
    assert env["PUBLIC_INGRESS_TUNNEL_TOKEN"] == "run-key-of-the-tunnel"
    rules = next(call[3] for call in cloudflare.calls if call[1].endswith("/configurations"))
    assert rules["config"]["ingress"][0]["service"] == "http://uat-icc-helpdesk-backend:8000"


def test_a_redeploy_writes_the_entry_into_an_installation_that_had_none(tmp_path, monkeypatch, cloudflare) -> None:
    """The entry's settings are the cockpit's, written on every deploy — an installation made before the key was
    entered gets them on its next deploy."""
    _project(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "cloudflare_tunnel_token", "")
    first = _deploy(tmp_path)
    assert public_ingress.SERVICE not in _services(first)
    assert P._parse_env_file(first.env_path).get("PUBLIC_INGRESS_TUNNEL_TOKEN") is None

    monkeypatch.setattr(settings, "cloudflare_tunnel_token", "tunnel-key")
    again = _deploy(tmp_path)

    assert again.env_path == first.env_path
    assert public_ingress.SERVICE in _services(again)
    env = P._parse_env_file(again.env_path)
    assert env["PUBLIC_INGRESS_URL"] == "https://uat-icc-helpdesk-ingress.icc.sk"
    assert env["PUBLIC_INGRESS_TUNNEL_TOKEN"] == "run-key-of-the-tunnel"


def test_an_app_without_the_entry_gets_no_tunnel(tmp_path, monkeypatch, cloudflare) -> None:
    _project(tmp_path, monkeypatch, example="SECRET_KEY=change-me\n")

    result = _deploy(tmp_path)

    assert public_ingress.SERVICE not in _services(result)
    assert cloudflare.calls == []


def test_without_the_key_the_installation_is_deployed_without_the_entry_and_says_so(tmp_path, monkeypatch) -> None:
    _project(tmp_path, monkeypatch)

    result = _deploy(tmp_path)

    assert public_ingress.SERVICE not in _services(result)
    assert public_ingress.NO_KEY in result.warnings


# --- the check from outside ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("inside", "outside", "said"),
    [
        (["405", "405"], ["403", "403"], (public_ingress.OPEN.format(url="https://x", prefix="/public/v1/"), [])),
        (["404"], ["403"], (public_ingress.OPEN.format(url="https://x", prefix="/public/v1/"), [])),
        (
            ["403"],
            ["403"],
            (None, [public_ingress.DOES_NOT_REACH.format(url="https://x", prefix="/public/v1/", got="403")]),
        ),
        (
            ["530"],
            ["403"],
            (None, [public_ingress.DOES_NOT_REACH.format(url="https://x", prefix="/public/v1/", got="530")]),
        ),
        (["405"], ["200", "403"], (None, [public_ingress.NOT_NARROW.format(url="https://x", got="200, 403")])),
        (
            None,
            ["403"],
            (None, [public_ingress.NOT_CHECKED.format(url="https://x", why="služba check-host.net neodpovedala")]),
        ),
    ],
)
def test_the_entry_is_checked_from_outside(monkeypatch, inside, outside, said) -> None:
    asked: list[str] = []
    answers = {"https://x/public/v1/nex-ingress-probe": inside, "https://x/": outside}
    monkeypatch.setattr(public_ingress, "_codes", lambda url: asked.append(url) or answers[url])

    assert public_ingress.check_outside("https://x", "/public/v1/") == said
    assert asked == ["https://x/public/v1/nex-ingress-probe", "https://x/"]


def test_the_check_is_not_asked_when_the_entry_was_not_set_up() -> None:
    assert public_ingress.set_up(["iná vec"]) is True
    assert public_ingress.set_up([public_ingress.NO_KEY]) is False
    assert public_ingress.set_up([public_ingress.NOT_SET_UP.format(why="x")]) is False


# --- the deploy says it -----------------------------------------------------------------------------------------


async def _deploy_the_app(db_session, warnings_of_the_render: list[str]):
    import uuid

    from backend.services import claude_agent
    from backend.services import deploy as deploy_service
    from tests.test_deploy_service import _make_customer, _make_project, _make_user, _make_version

    user = _make_user(db_session)
    project = _make_project(db_session, user=user, slug=f"nex-helpdesk-{uuid.uuid4().hex[:6]}")
    folder = claude_agent.PROJECTS_ROOT / project.slug
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ".env.example").write_text(EXAMPLE, encoding="utf-8")
    _make_version(db_session, project, "0.1.0")  # the checked code carries the declaration
    customer = _make_customer(db_session, project)

    async def _runner(**_kwargs):
        return deploy_service.RunnerResult(True, "OK", "https://uat-x.isnex.eu", warnings_of_the_render)

    event, _url, _bumped = await deploy_service.deploy(
        db_session, customer.id, version_number="0.1.0", environment="uat", actor_id=user.id, deploy_runner=_runner
    )
    return event, project, customer


@pytest.mark.asyncio
async def test_the_deploy_checks_the_entry_from_outside_and_says_it(db_session, monkeypatch) -> None:
    asked: list = []

    def _check(url, prefix):
        asked.append((url, prefix))
        return public_ingress.OPEN.format(url=url, prefix=prefix), []

    monkeypatch.setattr(public_ingress, "check_outside", _check)

    event, project, customer = await _deploy_the_app(db_session, [])

    app = P.derive_uat_slug(project.slug)
    assert asked == [(f"https://uat-andros-{app}-ingress.{settings.public_ingress_zone}", "/public/v1/")]
    assert event.detail.endswith(public_ingress.OPEN.format(url=asked[0][0], prefix="/public/v1/"))


@pytest.mark.asyncio
async def test_an_entry_that_was_not_set_up_is_not_checked(db_session, monkeypatch) -> None:
    asked: list = []
    monkeypatch.setattr(public_ingress, "check_outside", lambda url, prefix: asked.append(url) or (None, []))

    event, _project, _customer = await _deploy_the_app(db_session, [public_ingress.NO_KEY])

    assert asked == []
    assert public_ingress.NO_KEY in event.warnings
