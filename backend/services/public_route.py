"""The public address of an installation on ANOTHER machine, asked the way its users open it (DEV-68).

An installation the cockpit runs here is checked from inside: a container on ``nex-proxy-net`` asks this machine's
Traefik with the public ``Host`` header (:func:`orchestrator._verify_public_route`). For an installation on another
machine that cannot work, and 11.10.2026 it failed the PROD deploy of NEX Inbox 1.7.1 on MAGER in three ways at once:
the ``docker compose exec`` went to THIS machine's Docker (where the installation does not run — „service "backend"
is not running“), it asked for a name the cockpit composed itself (``mager-inbox.isnex.eu``, which MAGER does not
serve) and it addressed this machine's Traefik by name (``nex-uat-traefik``; MAGER's is ``nex-traefik``).

So for another machine the addresses come from the installation's OWN routers — the ones its compose publishes —
and are asked over the network from the cockpit, exactly as a person on the network opens them.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from typing import Any

from backend.services.private_access import _HOST_RULE

#: How long one address may take to answer.
ROUTE_PROBE_TIMEOUT_SECONDS = 10

#: Traefik's standard entrypoints and the scheme a person uses on them. Routers on an installation's own
#: entrypoint (another port) are not asked: which port that is lives in the Traefik configuration, not in the
#: installation's compose.
ENTRYPOINT_SCHEMES = {"websecure": "https", "web": "http"}

STATE_OK, STATE_DOWN, STATE_SKIP = "ok", "down", "skip"


def _routers(compose: dict[str, Any]) -> dict[str, dict[str, str]]:
    """``{router: {attribute: value}}`` from the Traefik labels of every service of ``compose``."""
    routers: dict[str, dict[str, str]] = {}
    for svc in (compose.get("services") or {}).values():
        labels = (svc or {}).get("labels") or []
        items = labels.items() if isinstance(labels, dict) else (str(label).split("=", 1) for label in labels)
        for key, *value in items:
            parts = str(key).split(".", 4)
            if len(parts) == 5 and parts[:3] == ["traefik", "http", "routers"] and value:
                routers.setdefault(parts[3], {})[parts[4]] = str(value[0])
    return routers


def route_urls(compose: dict[str, Any]) -> list[str]:
    """``scheme://host/`` for every router of the installation that serves its web frontend on a standard entrypoint.

    API-only routers (``PathPrefix(`/api`)``) are not asked: the frontend's address is the one a person opens. A
    router without ``entrypoints`` listens on all of them; it is asked over https when it has TLS, else http."""
    urls: set[str] = set()
    for attrs in _routers(compose).values():
        rule = attrs.get("rule", "")
        if "PathPrefix" in rule:
            continue
        entrypoints = [e.strip() for e in attrs.get("entrypoints", "").split(",") if e.strip()]
        if entrypoints:
            schemes = [ENTRYPOINT_SCHEMES[e] for e in entrypoints if e in ENTRYPOINT_SCHEMES]
        else:
            schemes = ["https" if any(k.startswith("tls") for k in attrs) else "http"]
        for host in _HOST_RULE.findall(rule):
            urls.update(f"{scheme}://{host}/" for scheme in schemes)
    return sorted(urls)


def probe(url: str) -> tuple[str, str]:
    """``(state, what was seen)`` for one address — the same verdicts as the in-network probe: an answer of the app
    (below 500 and not 404) is a working route; Traefik's 404 (no route) or a 5xx is a broken one; no answer at all
    says nothing about the route."""
    request = urllib.request.Request(url, headers={"User-Agent": "nex-studio-route-check"})
    try:
        with urllib.request.urlopen(request, timeout=ROUTE_PROBE_TIMEOUT_SECONDS) as response:
            return STATE_OK, f"{url} {response.status}"
    except urllib.error.HTTPError as exc:
        return (STATE_OK if exc.code < 500 and exc.code != 404 else STATE_DOWN), f"{url} {exc.code}"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return STATE_SKIP, f"{url} {type(exc).__name__}"


def check(urls: list[str]) -> tuple[str, str]:
    """All of an installation's addresses at once: one broken route makes it ``down``; it is ``ok`` only when every
    address answered; anything else (no address, no answer) is ``skip``."""
    if not urls:
        return STATE_SKIP, "inštalácia nemá v smerovaní adresu na štandardnom vstupe (https alebo http)"
    seen = [probe(url) for url in urls]
    last = "; ".join(text for _state, text in seen)
    states = {state for state, _text in seen}
    if STATE_DOWN in states:
        return STATE_DOWN, last
    return (STATE_OK if states == {STATE_OK} else STATE_SKIP), last
