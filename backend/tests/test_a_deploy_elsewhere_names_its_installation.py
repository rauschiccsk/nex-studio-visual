"""DEV-405 — a deploy to another machine makes its installation's names lead there, and believes the route only then.

16.11.2026 the Manažér deployed NEX Manager 1.6.0 to PROD for SKIT o.z. on MAGER and the cockpit said „Nasadené“, yet
the app did not open: ``skit-manager.icc.sk`` had no record of its own, the zone's catch-all ``*.icc.sk`` sent it to a
web hosting answering 403, and the check after the deploy took that answer for the app's. Director: „Kokpit musí
vedieť urobiť od zadania do nasadenia na PROD, bez Deda.“
"""

from __future__ import annotations

import asyncio
import threading
import types
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx
import pytest
import yaml

from backend.config.settings import settings
from backend.services import deploy as deploy_service
from backend.services import installation_names, orchestrator, public_route, remote_instance
from backend.services import uat_provisioner as P
from backend.tests.test_a_deploy_elsewhere_leaves_the_target_consistent import _docker, _instance

#: The real lookups, taken before the suite's default (every name leads to its machine) replaces them.
REAL_PUBLIC_ADDRESSES = installation_names.public_addresses
REAL_CLOUDFLARE = installation_names._cloudflare

MAGER = settings.prod_machines["mager"].address
CATCH_ALL = ["104.21.7.140", "172.67.187.149"]
NAME = "skit-manager.icc.sk"
ZONE_ID = "zone-icc"
TOKEN = "cf-test-key"


def _compose(*names: str, tailscale_name: bool = True) -> dict:
    """An installation the way the cockpit renders it on MAGER — a router per name, and the machine's Tailscale name on
    an entrypoint of its own."""
    labels = []
    for i, name in enumerate(names):
        labels += [
            f"traefik.http.routers.r{i}-sec.rule=Host(`{name}`)",
            f"traefik.http.routers.r{i}-sec.entrypoints=websecure",
            f"traefik.http.routers.r{i}-sec.tls.certresolver=cf",
            f"traefik.http.routers.r{i}-sec-api.rule=Host(`{name}`) && PathPrefix(`/api`)",
        ]
    if tailscale_name:
        labels += [
            "traefik.http.routers.r-ts.rule=Host(`mager.tail5c98e2.ts.net`)",
            "traefik.http.routers.r-ts.entrypoints=skit-manager",
        ]
    return {"services": {"frontend": {"labels": labels}}}


def _written(tmp_path: Path, compose: dict) -> Path:
    path = tmp_path / "docker-compose.yml"
    path.write_text(yaml.safe_dump(compose), encoding="utf-8")
    return path


class _Zone:
    """Public DNS and the zone in Cloudflare, as the cockpit's calls see them."""

    def __init__(self, dns: dict[str, list[str]], records: list[dict] | None = None, *, lags: bool = False):
        self.dns, self.records, self.lags = dns, list(records or []), lags
        self.calls: list[tuple] = []
        self.refuse: str | None = None

    def lookup(self, name: str) -> list[str]:
        return list(self.dns.get(name, CATCH_ALL))

    def request(self, method, url, *, headers, timeout, params=None, json=None):
        path = url.removeprefix(settings.cloudflare_api_url)
        self.calls.append((method, path, params, json, headers.get("Authorization")))
        if self.refuse:
            return httpx.Response(403, json={"success": False, "errors": [{"code": 10000, "message": self.refuse}]})
        if method == "GET" and path == "/zones":
            zones = [{"id": ZONE_ID, "name": "icc.sk"}] if params == {"name": "icc.sk"} else []
            return httpx.Response(200, json={"success": True, "result": zones})
        if method == "GET" and path == f"/zones/{ZONE_ID}/dns_records":
            mine = [r for r in self.records if r["name"] == params["name"]]
            return httpx.Response(200, json={"success": True, "result": mine})
        if method == "POST" and path == f"/zones/{ZONE_ID}/dns_records":
            self.records.append(json)
            if not self.lags:
                self.dns[json["name"]] = [json["content"]]
            return httpx.Response(200, json={"success": True, "result": {"id": "rec-1", **json}})
        raise AssertionError(f"unexpected call {method} {path}")

    def writes(self) -> list[tuple]:
        return [call for call in self.calls if call[0] != "GET"]


@pytest.fixture
def zone(monkeypatch):
    """The SKIT world: the name leads to the catch-all, no record of its own; the cockpit has its key."""
    world = _Zone({})
    monkeypatch.setattr(installation_names, "public_addresses", world.lookup)
    monkeypatch.setattr(installation_names, "_cloudflare", REAL_CLOUDFLARE)
    monkeypatch.setattr(
        installation_names, "httpx", types.SimpleNamespace(request=world.request, HTTPError=httpx.HTTPError)
    )
    monkeypatch.setattr(settings, "cloudflare_dns_token", TOKEN)
    monkeypatch.setattr(settings, "installation_name_wait_seconds", 0)
    monkeypatch.setattr(settings, "installation_name_poll_seconds", 0)
    return world


def _ensure(tmp_path, *names, owner="SKIT o.z. / manager"):
    return installation_names.ensure(_written(tmp_path, _compose(*names)), "mager", owner)


# --- before the installation starts ---------------------------------------------------------------------------


def test_a_name_without_a_record_gets_one_leading_to_the_machine_in_tailscale(zone, tmp_path):
    assert _ensure(tmp_path, NAME) == (
        True,
        f"Meno {NAME} dostalo v Cloudflare záznam na stroj MAGER v Tailscale ({MAGER}).",
    )
    assert zone.writes() == [
        (
            "POST",
            f"/zones/{ZONE_ID}/dns_records",
            None,
            {
                "type": "A",
                "name": NAME,
                "content": MAGER,
                "ttl": 1,
                "proxied": False,
                "comment": "NEX Studio Visual — SKIT o.z. / manager",
            },
            f"Bearer {TOKEN}",
        )
    ]


def test_a_name_that_already_leads_there_is_left_alone(zone, tmp_path):
    zone.dns[NAME] = [MAGER]

    assert _ensure(tmp_path, NAME) == (True, "")
    assert zone.calls == [], "Cloudflare is not asked about a name that already works"


def test_the_tailscale_name_of_the_machine_is_not_the_cockpits_to_create(zone, tmp_path):
    zone.dns[NAME] = [MAGER]
    # the Tailscale name is not in public DNS at all — it must not be asked, created or held against the deploy
    zone.dns["mager.tail5c98e2.ts.net"] = []

    assert _ensure(tmp_path, NAME) == (True, "")
    assert zone.calls == []


def test_a_record_somebody_else_made_is_never_rewritten(zone, tmp_path):
    zone.records = [{"type": "CNAME", "name": NAME, "content": "skit.example.org", "proxied": True}]

    assert _ensure(tmp_path, NAME) == (
        False,
        f"Meno {NAME} má v Cloudflare vlastný záznam (CNAME → skit.example.org, cez proxy Cloudflare), ktorý nevedie "
        f"na stroj MAGER v Tailscale ({MAGER}). Kokpit cudzí záznam neprepisuje — nasadenie nezačalo; over, komu meno "
        "patrí.",
    )
    assert zone.writes() == []


def test_an_own_record_public_dns_does_not_show_yet_is_waited_for_not_made_twice(zone, tmp_path, monkeypatch):
    zone.records = [{"type": "A", "name": NAME, "content": MAGER, "proxied": False}]
    answers = iter([CATCH_ALL, CATCH_ALL, [MAGER]])
    monkeypatch.setattr(installation_names, "public_addresses", lambda name: next(answers))
    monkeypatch.setattr(settings, "installation_name_wait_seconds", 60)

    assert _ensure(tmp_path, NAME) == (True, "")
    assert zone.writes() == []


def test_a_record_public_dns_never_shows_stops_the_deploy(zone, tmp_path):
    zone.lags = True

    assert _ensure(tmp_path, NAME) == (
        False,
        f"Meno {NAME} má v Cloudflare záznam na stroj MAGER ({MAGER}), ale verejné DNS ho do 0 s neukázalo (vidí "
        f"{', '.join(CATCH_ALL)}) — nasadenie nezačalo; nasaď znova o pár minút.",
    )


def test_without_the_cockpits_key_the_deploy_says_what_is_missing(zone, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "cloudflare_dns_token", "")

    assert _ensure(tmp_path, NAME) == (
        False,
        f"Meno {NAME} vedie na {', '.join(CATCH_ALL)}, nie na stroj MAGER v Tailscale ({MAGER}). Kokpit by meno "
        "v Cloudflare založil sám, ale v jeho nastavení chýba kľúč do Cloudflare (CLOUDFLARE_DNS_TOKEN) — nasadenie "
        "nezačalo.",
    )
    assert zone.calls == []


def test_cloudflare_refusing_is_said_in_its_own_words(zone, tmp_path):
    zone.refuse = "Authentication error"

    assert _ensure(tmp_path, NAME) == (
        False,
        f"Meno {NAME} vedie na {', '.join(CATCH_ALL)}, nie na stroj MAGER v Tailscale ({MAGER}) a záznam v Cloudflare "
        "sa nepodarilo založiť: Cloudflare odmietol: Authentication error — nasadenie nezačalo.",
    )


def test_a_web_project_gets_each_missing_name_and_only_those_are_said(zone, tmp_path):
    zone.dns["skit-web.icc.sk"] = [MAGER]

    ok, said = _ensure(tmp_path, NAME, "skit-web.icc.sk")

    assert ok is True
    assert said == f"Meno {NAME} dostalo v Cloudflare záznam na stroj MAGER v Tailscale ({MAGER})."
    assert [call[3]["name"] for call in zone.writes()] == [NAME]


def test_public_dns_not_answering_stops_the_deploy(zone, tmp_path, monkeypatch):
    def _silent(name):
        raise installation_names.NameLookupFailed("ConnectTimeout")

    monkeypatch.setattr(installation_names, "public_addresses", _silent)

    assert _ensure(tmp_path, NAME) == (
        False,
        f"Verejné DNS na meno {NAME} neodpovedá (ConnectTimeout) — nasadenie nezačalo; skús ho o chvíľu znova.",
    )


def test_a_machine_whose_names_are_not_the_cockpits_is_not_touched(zone, tmp_path):
    assert installation_names.ensure(tmp_path / "missing.yml", None, "x") == (True, "")
    assert installation_names.ensure(tmp_path / "missing.yml", "", "x") == (True, "")
    assert zone.calls == []


# --- public DNS ------------------------------------------------------------------------------------------------


class _Resolver:
    def __init__(self, reply):
        self.reply, self.asked = reply, []

    def get(self, url, *, params, headers, timeout):
        self.asked.append((url, params, headers))
        if isinstance(self.reply, Exception):
            raise self.reply
        return httpx.Response(200, json=self.reply, request=httpx.Request("GET", url))


@pytest.mark.parametrize(
    "reply, addresses",
    [
        (
            {
                "Status": 0,
                "Answer": [
                    {"type": 5, "data": "edge.example."},
                    {"type": 1, "data": "104.21.7.140"},
                    {"type": 1, "data": "172.67.187.149"},
                ],
            },
            ["104.21.7.140", "172.67.187.149"],
        ),
        ({"Status": 0, "Answer": [{"type": 1, "data": MAGER}]}, [MAGER]),
        ({"Status": 3}, []),
    ],
)
def test_public_dns_is_asked_over_https_and_only_addresses_count(monkeypatch, reply, addresses):
    resolver = _Resolver(reply)
    monkeypatch.setattr(installation_names, "httpx", types.SimpleNamespace(get=resolver.get, HTTPError=httpx.HTTPError))

    assert REAL_PUBLIC_ADDRESSES(NAME) == addresses
    assert resolver.asked == [
        (settings.public_dns_url, {"name": NAME, "type": "A"}, {"accept": "application/dns-json"})
    ]


@pytest.mark.parametrize("reply", [{"Status": 2}, httpx.ConnectTimeout("slow")])
def test_no_answer_from_public_dns_is_no_claim(monkeypatch, reply):
    monkeypatch.setattr(
        installation_names, "httpx", types.SimpleNamespace(get=_Resolver(reply).get, HTTPError=httpx.HTTPError)
    )

    with pytest.raises(installation_names.NameLookupFailed):
        REAL_PUBLIC_ADDRESSES(NAME)


# --- after the deploy ------------------------------------------------------------------------------------------


def test_the_check_says_where_a_wrong_name_leads(zone):
    assert installation_names.check(_compose(NAME), "mager") == (
        public_route.STATE_DOWN,
        f"meno {NAME} vedie na {', '.join(CATCH_ALL)}, nie na stroj MAGER v Tailscale ({MAGER}) — odpovedá tam iný "
        "server",
    )
    zone.dns[NAME] = [MAGER]
    assert installation_names.check(_compose(NAME), "mager") == (public_route.STATE_OK, "")
    assert installation_names.check(_compose(NAME), None) == (public_route.STATE_OK, "")


def test_skit_reported_deployed_while_its_name_led_to_a_web_hosting(tmp_path, monkeypatch, zone):
    """The incident: the containers run, the web hosting behind the catch-all answers 403 — not the app's answer."""
    compose = _instance(tmp_path, monkeypatch)
    _docker(monkeypatch, compose)
    monkeypatch.setattr(public_route, "probe", lambda url, address=None: (public_route.STATE_OK, f"{url} 403"))

    result = asyncio.run(
        orchestrator._run_prod_deploy(
            "nex-inbox", "mager", "inbox", "nex-inbox", version_number="1.7.1", deploy_host="mager"
        )
    )

    assert result[0] is False
    route, _, _backup = result[1].partition(" Záloha databázy pred nasadením: ")
    assert route == (
        "aplikácia beží, ale verejná adresa https://magerstav-inbox.icc.sk/ nie je dostupná — smerovanie zlyhalo: "
        f"meno magerstav-inbox.icc.sk vedie na {', '.join(CATCH_ALL)}, nie na stroj MAGER v Tailscale ({MAGER}) — "
        "odpovedá tam iný server"
    )


# --- the deploy runner -----------------------------------------------------------------------------------------


class _Provisioned:
    warnings: list[str] = []
    fe_service = "frontend"
    uat_dir = Path("/opt/customers/skit/nex-manager")
    previous_files = {"docker-compose.yml": ("image: nex-manager-backend:v1.5.0\n", 0o644)}


def _run(monkeypatch, tmp_path):
    started: list = []
    written: list = []
    provisioned = _Provisioned()
    provisioned.compose_path = _written(tmp_path, _compose(NAME))

    async def _prod(*_a, **_k):
        started.append(1)
        return orchestrator.InstanceDeployResult(True, "OK Stavba z overeného commitu", new_version_started=True)

    async def _nothing(*_a, **_k):
        return None

    monkeypatch.setattr(P, "provision_uat", lambda *a, **k: provisioned)
    monkeypatch.setattr(orchestrator, "_run_prod_deploy", _prod)
    monkeypatch.setattr(orchestrator, "instance_health", _nothing)
    monkeypatch.setattr(orchestrator, "instance_version", _nothing)
    monkeypatch.setattr(remote_instance, "write_files", lambda d, files, deploy_host: written.append(files) or None)
    result = asyncio.run(
        deploy_service._default_deploy_runner(
            project_slug="nex-manager",
            uat_slug="skit-prod",
            version_number="1.6.0",
            force_fresh=False,
            deploy_host="mager",
            customer_name="SKIT o.z.",
        )
    )
    return result, started, written


def test_the_deploy_creates_the_name_before_the_installation_starts_and_says_so(zone, tmp_path, monkeypatch):
    result, started, _written_back = _run(monkeypatch, tmp_path)

    assert result[0] is True
    assert started == [1]
    assert result[1] == (
        f"OK Stavba z overeného commitu Meno {NAME} dostalo v Cloudflare záznam na stroj MAGER v Tailscale ({MAGER})."
    )
    assert zone.writes()[0][3]["comment"] == "NEX Studio Visual — SKIT o.z. / manager"


def test_a_name_that_cannot_be_made_stops_the_deploy_before_anything_is_replaced(zone, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "cloudflare_dns_token", "")

    result, started, written = _run(monkeypatch, tmp_path)

    assert result[0] is False
    assert started == [], "the installation must not start while its name leads elsewhere"
    assert result[1].startswith(f"Meno {NAME} vedie na {', '.join(CATCH_ALL)}, nie na stroj MAGER v Tailscale")
    assert result[1].endswith("Predpis na cieli vrátený do stavu pred nasadením.")
    assert written == [_Provisioned.previous_files]


def test_the_cockpits_key_to_cloudflare_never_reaches_an_agent(monkeypatch):
    from backend.core.agent_env import agent_env, project_env

    monkeypatch.setenv("CLOUDFLARE_DNS_TOKEN", "cf-secret")

    assert "CLOUDFLARE_DNS_TOKEN" not in agent_env({"CLOUDFLARE_DNS_TOKEN": "cf-secret"})
    assert "CLOUDFLARE_DNS_TOKEN" not in project_env()


# --- the probe at the machine ----------------------------------------------------------------------------------


class _Recorder(BaseHTTPRequestHandler):
    hosts: list[str] = []

    def do_GET(self):  # noqa: N802 — the name http.server calls
        _Recorder.hosts.append(self.headers.get("Host", ""))
        self.send_response({"/": 200, "/login": 401, "/missing": 404, "/broken": 502}[self.path])
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def machine_port():
    _Recorder.hosts = []
    httpd = HTTPServer(("127.0.0.1", 0), _Recorder)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1]
    httpd.shutdown()


def test_the_probe_at_the_machine_never_looks_the_name_up(machine_port):
    """The name does not exist in any DNS — the probe still reaches the machine, carrying the name."""
    url = f"http://skit-manager.invalid:{machine_port}"

    assert public_route.probe(f"{url}/", "127.0.0.1") == (public_route.STATE_OK, f"{url}/ 200")
    assert public_route.probe(f"{url}/login", "127.0.0.1")[0] == public_route.STATE_OK
    assert public_route.probe(f"{url}/missing", "127.0.0.1") == (public_route.STATE_DOWN, f"{url}/missing 404")
    assert public_route.probe(f"{url}/broken", "127.0.0.1")[0] == public_route.STATE_DOWN
    assert _Recorder.hosts == [f"skit-manager.invalid:{machine_port}"] * 4
    assert public_route.probe(f"{url}/", "127.0.0.9")[0] == public_route.STATE_SKIP, "no answer says nothing"
