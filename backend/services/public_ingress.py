"""The narrow public entry of an installation — a Cloudflare tunnel the cockpit sets up (DEV-409).

**Why.** The cockpit's apps are reached over Tailscale only; NEX HelpDesk is called by a voice platform and a telephone
operator during and after a call (its tools, its webhooks), which have no Tailscale. Its approved specification wants
only the paths under ``/public/v1/`` on the internet, already in UAT; its screens stay in Tailscale. MAGER sits behind
DS-Lite, so nothing comes in unless a tunnel goes out. Director 17.11.2026, to the cockpit setting the tunnel up
itself with a key of its own: „Súhlasím“.

**How.** An app declares the entry in its ``.env.example`` (``# @public-ingress: /public/v1/`` above
``PUBLIC_INGRESS_URL``). On a deploy the cockpit, with its tunnels' key:

* finds or creates the installation's tunnel (one per installation, by name — a redeploy reuses it);
* sets the tunnel's rules: requests for the entry's name whose path starts with the prefix go to the installation's
  backend, everything else is refused with 403 before it reaches the app;
* points the entry's name in the zone at the tunnel (with the DNS key; a record somebody else made is never
  rewritten);
* hands the installation the tunnel's run key, for the connector that runs beside the app (``cloudflared``).

After the deploy the entry is checked from outside by a service on the internet (never from the server): a path
under the prefix must reach the app, the root must be refused (:func:`check_outside`).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from backend.config.settings import settings
from backend.services import installation_names
from backend.services.installation_names import COMMENT_MAX, CloudflareRefused

#: What the app reads its entry's address at, and what the connector reads the tunnel's run key at.
INGRESS_URL_ENV = "PUBLIC_INGRESS_URL"
TUNNEL_TOKEN_ENV = "PUBLIC_INGRESS_TUNNEL_TOKEN"
#: The connector's service in the installation's compose.
SERVICE = "public-ingress"
#: What the tunnel answers to anything outside the prefix — not the app's own 404, so the check can tell them apart.
REFUSED_STATUS = 403
#: The path the check asks under the prefix — no app has it; the app's own answer (404, 405) proves it got there.
PROBE = "nex-ingress-probe"
#: Where a record or tunnel says who made it.
OWNER = "NEX Studio Visual — úzky vstup"

NO_KEY = (
    "Úzky verejný vstup: kokpit nemá kľúč Cloudflare na tunely (CLOUDFLARE_TUNNEL_TOKEN, CLOUDFLARE_ACCOUNT_ID) — "
    "inštalácia je bez vstupu, kým ho správca kokpitu nezadá."
)
NOT_SET_UP = "Úzky verejný vstup sa nepodarilo zriadiť ({why}) — inštalácia je bez vstupu."
OPEN = "Úzky verejný vstup {url}: z internetu len cesty {prefix} — overené zvonku."
NOT_CHECKED = "Úzky verejný vstup {url} sa zvonku nedal overiť ({why})."
DOES_NOT_REACH = "Úzky verejný vstup {url}: cesta pod {prefix} sa zvonku do aplikácie nedostala ({got})."
NOT_NARROW = "Úzky verejný vstup {url}: zvonku je dostupné aj to, čo nemá byť ({got}) — vstup nie je úzky."


@dataclass(frozen=True)
class Entry:
    """An installation's entry: its address, the prefix that goes out, and the tunnel's run key (a secret)."""

    url: str
    prefix: str
    token: str


def host(environment: str, customer_slug: str, app: str) -> str:
    """The entry's name — ``<customer>-<app>-ingress.<zone>``, in UAT with ``uat-`` before it."""
    name = f"{customer_slug}-{app}-ingress"
    return f"{'uat-' if environment == 'uat' else ''}{name}.{settings.public_ingress_zone}"


def _tunnels(path: str = "", **kwargs: Any) -> Any:
    return installation_names._cloudflare(
        kwargs.pop("method", "GET"),
        f"/accounts/{settings.cloudflare_account_id}/cfd_tunnel{path}",
        token=settings.cloudflare_tunnel_token,
        **kwargs,
    )


def _tunnel_id(name: str) -> str:
    found = _tunnels(params={"name": name, "is_deleted": "false"}) or []
    if found:
        return str(found[0]["id"])
    made = _tunnels(method="POST", json={"name": name, "config_src": "cloudflare"})
    return str(made["id"])


def rules(name: str, prefix: str, backend_url: str) -> dict[str, Any]:
    """The tunnel's rules: the prefix of the entry's name to the backend, everything else refused."""
    return {
        "ingress": [
            {"hostname": name, "path": f"^{re.escape(prefix)}", "service": backend_url},
            {"service": f"http_status:{REFUSED_STATUS}"},
        ]
    }


def _name_to_tunnel(name: str, tunnel_id: str) -> None:
    """The entry's name in the zone leads to the tunnel — created, or moved when it is ours and led elsewhere."""
    zone = installation_names._zone_id(settings.public_ingress_zone)
    target = f"{tunnel_id}.cfargotunnel.com"
    records = installation_names._cloudflare("GET", f"/zones/{zone}/dns_records", params={"name": name}) or []
    record = {"type": "CNAME", "name": name, "content": target, "proxied": True, "comment": OWNER[:COMMENT_MAX]}
    if not records:
        installation_names._cloudflare("POST", f"/zones/{zone}/dns_records", json=record)
        return
    ours = records[0]
    if ours.get("type") == "CNAME" and ours.get("content") == target:
        return
    if not str(ours.get("comment") or "").startswith(OWNER):
        raise CloudflareRefused(f"meno {name} už v zóne má záznam, ktorý nezaložil kokpit — nemením ho")
    installation_names._cloudflare("PATCH", f"/zones/{zone}/dns_records/{ours['id']}", json=record)


def ensure(*, name: str, prefix: str, backend_url: str) -> tuple[Optional[Entry], Optional[str]]:
    """The installation's entry set up in Cloudflare — ``(entry, None)``, or ``(None, sentence)`` when it cannot be.
    Blocking (HTTP) — call it off the event loop."""
    if not (settings.cloudflare_tunnel_token and settings.cloudflare_account_id):
        return None, NO_KEY
    tunnel_name = name.removesuffix(f".{settings.public_ingress_zone}")
    try:
        tunnel_id = _tunnel_id(tunnel_name)
        _tunnels(f"/{tunnel_id}/configurations", method="PUT", json={"config": rules(name, prefix, backend_url)})
        _name_to_tunnel(name, tunnel_id)
        token = _tunnels(f"/{tunnel_id}/token")
    except CloudflareRefused as refused:
        return None, NOT_SET_UP.format(why=str(refused))
    return Entry(url=f"https://{name}", prefix=prefix, token=str(token)), None


def connector(container_name: str) -> dict[str, Any]:
    """The connector's service: ``cloudflared`` running the installation's tunnel with the run key from its ``.env``."""
    return {
        "image": settings.cloudflared_image,
        "container_name": container_name,
        "command": ["tunnel", "--no-autoupdate", "run"],
        "environment": {"TUNNEL_TOKEN": f"${{{TUNNEL_TOKEN_ENV}}}"},
        "restart": "unless-stopped",
    }


def set_up(warnings: list[str]) -> bool:
    """Whether the deploy set the entry up — none of its warnings says it could not."""
    not_set_up = NOT_SET_UP.split("(", 1)[0]
    return not any(w == NO_KEY or w.startswith(not_set_up) for w in warnings)


# --- the check from outside -------------------------------------------------------------------------------------


def _codes(url: str) -> Optional[list[str]]:
    """The HTTP status codes the nodes of check-host.net got for ``url`` — ``None`` when the service did not say.
    The one place that goes to the internet (the suite stubs it)."""
    base = settings.public_ingress_check_url
    headers = {"Accept": "application/json"}
    try:
        asked = httpx.get(f"{base}/check-http", params={"host": url, "max_nodes": 3}, headers=headers, timeout=20)
        request_id = asked.json()["request_id"]
        deadline = time.monotonic() + settings.public_ingress_check_timeout_seconds
        while time.monotonic() < deadline:
            time.sleep(3)
            results = httpx.get(f"{base}/check-result/{request_id}", headers=headers, timeout=20).json()
            answered = [r for r in results.values() if r]
            if answered and len(answered) == len(results):
                return [str(row[0][3]) for row in answered if row and row[0] and len(row[0]) > 3 and row[0][3]]
    except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError):
        return None
    return None


def check_outside(url: str, prefix: str) -> tuple[Optional[str], list[str]]:
    """``(what the deploy says, warnings)``: a path under the prefix must reach the app (any answer of the app — not
    the tunnel's 403, not Cloudflare's 5xx) and the root must be refused with 403."""
    inside = _codes(f"{url}{prefix}{PROBE}")
    outside = _codes(f"{url}/")
    if not inside or not outside:
        return None, [NOT_CHECKED.format(url=url, why="služba check-host.net neodpovedala")]
    reached = [c for c in inside if c != str(REFUSED_STATUS) and not c.startswith("5")]
    refused = [c for c in outside if c == str(REFUSED_STATUS)]
    if len(refused) != len(outside):
        return None, [NOT_NARROW.format(url=url, got=", ".join(sorted(set(outside))))]
    if len(reached) != len(inside):
        return None, [DOES_NOT_REACH.format(url=url, prefix=prefix, got=", ".join(sorted(set(inside))))]
    return OPEN.format(url=url, prefix=prefix), []
