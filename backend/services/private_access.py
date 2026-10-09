"""After a private project's deploy, the cockpit checks the installation cannot be reached from outside (DEV-42).

A project with ``private_network`` gets names only in the private zone ``*.int.isnex.eu``: the zone points at
ANDROS in Tailscale with no Cloudflare proxy, and nginx lets only Tailscale in (ICCINT-213). Those are three
independent facts, and each can break on its own — a hand edit adds a public router to the compose, a DNS record
moves, the nginx block loses its ``deny``. So each is checked after every deploy, on what was actually written:

* **no public name** — every Traefik router rule in the installation's compose names only private hosts;
* **the name points into Tailscale** — what the name resolves to lies in Tailscale's address ranges;
* **outside Tailscale the server refuses** — a request from the cockpit's own container (not a Tailscale
  address) to the host's web server, for that name, is answered 403.

Reachability from Tailscale is the deploy's own serve check (in-network); this module only answers „can anyone
else get in?". Each problem is a sentence for the Manažér; an empty list means private as promised.
"""

from __future__ import annotations

import http.client
import ipaddress
import re
import socket
import ssl
from pathlib import Path
from typing import Any, Optional

import yaml

from backend.services.uat_provisioner import PRIVATE_DOMAIN_SUFFIX

#: Tailscale's address ranges (CGNAT IPv4 and the ULA IPv6 prefix) — the same ones nginx allows.
TAILSCALE_NETWORKS = (ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48"))

#: How long the probe of the host's web server may take.
PROBE_TIMEOUT_SECONDS = 10

_HOST_RULE = re.compile(r"Host\(`([^`]+)`\)")


def _router_hosts(compose: dict[str, Any]) -> list[str]:
    hosts: list[str] = []
    for svc in (compose.get("services") or {}).values():
        labels = (svc or {}).get("labels") or []
        items = labels.items() if isinstance(labels, dict) else (str(label).split("=", 1) for label in labels)
        for key, *value in items:
            if re.fullmatch(r"traefik\.http\.routers\.[^.]+\.rule", str(key)) and value:
                hosts.extend(_HOST_RULE.findall(str(value[0])))
    return hosts


def public_hosts(compose: dict[str, Any]) -> list[str]:
    """Router hosts outside the private zone — for a private project there must be none."""
    return sorted({h for h in _router_hosts(compose) if not h.endswith("." + PRIVATE_DOMAIN_SUFFIX)})


def addresses_outside_tailscale(host: str) -> list[str]:
    """What ``host`` resolves to outside Tailscale's ranges. Raises ``OSError`` when it does not resolve."""
    found = {info[4][0] for info in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)}
    return sorted(a for a in found if not any(ipaddress.ip_address(a) in net for net in TAILSCALE_NETWORKS))


def default_gateway() -> Optional[str]:
    """The container's default gateway — the host, reached from an address that is not Tailscale's."""
    try:
        lines = Path("/proc/net/route").read_text(encoding="utf-8").splitlines()[1:]
    except OSError:
        return None
    for line in lines:
        fields = line.split()
        if len(fields) > 2 and fields[1] == "00000000":
            return socket.inet_ntoa(int(fields[2], 16).to_bytes(4, "little"))
    return None


def status_from_outside(host: str, via: str) -> int:
    """The HTTP status the host's web server gives ``host`` when asked from ``via`` — not from Tailscale.

    The name goes in the TLS handshake (SNI) and in the ``Host`` header, the connection to ``via``: exactly what a
    stranger who knows the name and the server's address would send."""
    context = ssl.create_default_context()
    context.check_hostname = False  # the probe is about who is let in, not about the certificate
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection((via, 443), timeout=PROBE_TIMEOUT_SECONDS) as raw:
        with context.wrap_socket(raw, server_hostname=host) as tls:
            tls.sendall(f"GET / HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode("ascii"))
            first_line = tls.recv(256).split(b"\r\n", 1)[0].decode("ascii", "replace")
    m = re.match(r"HTTP/\d(?:\.\d)? (\d{3})", first_line)
    if not m:
        raise http.client.HTTPException(f"unreadable answer: {first_line[:60]!r}")
    return int(m.group(1))


def check(compose_path: Path, host: str, *, via: Optional[str] = None) -> list[str]:
    """What makes the installation at ``host`` reachable from outside Tailscale — ``[]`` when nothing does."""
    problems: list[str] = []
    try:
        compose = yaml.safe_load(Path(compose_path).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        return [f"Súkromný prístup sa nedal overiť — predpis inštalácie sa nedal prečítať ({exc.__class__.__name__})."]
    for public in public_hosts(compose):
        problems.append(f"Inštalácia má aj verejné meno {public} — mala byť len v súkromnej sieti.")
    try:
        outside = addresses_outside_tailscale(host)
        if outside:
            problems.append(f"Meno {host} ukazuje mimo Tailscale ({', '.join(outside)}).")
    except OSError as exc:
        problems.append(f"Meno {host} sa nepodarilo preložiť ({exc.__class__.__name__}) — súkromný prístup neoverený.")
    gateway = via or default_gateway()
    if gateway is None:
        problems.append(f"Nepodarilo sa overiť, či server púšťa {host} len z Tailscale — nenašla sa cesta k nemu.")
        return problems
    try:
        code = status_from_outside(host, gateway)
        if code != 403:
            problems.append(f"Server pustil {host} aj mimo Tailscale (odpoveď {code}, mala byť 403).")
    except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
        problems.append(f"Nepodarilo sa overiť, či server púšťa {host} len z Tailscale ({exc.__class__.__name__}).")
    return problems
