"""Post-deploy PUBLIC-route verification (andros-payables outage follow-up, 2026-07-10).

The in-network serve-verify proved the app answers on localhost, but NOT that the PUBLIC Traefik route works —
so a poisoned ``Host()`` label (no route for the real domain → 404 at the public URL) let the cockpit report
"✓ Nasadené" while the site was DOWN. ``_verify_public_route`` now probes the route the internet uses (Traefik
+ the public Host header, from a container on nex-proxy-net) and classifies it ``ok`` / ``down`` / ``skip``.
These pin that classification + the probe shape, with ``_compose_smoke_step`` mocked (no docker in unit tests).
"""

from __future__ import annotations

import asyncio

from backend.services import orchestrator


async def _noop(*_a, **_k) -> None:  # replaces asyncio.sleep so the retry loop doesn't really wait
    return None


def test_probe_src_sends_public_host_header_and_hits_traefik() -> None:
    src = orchestrator._traefik_public_route_probe_src("andros-payables.isnex.eu")
    assert "'Host': 'andros-payables.isnex.eu'" in src  # the request carries the PUBLIC host as a header
    assert orchestrator._PUBLIC_ROUTE_TRAEFIK_HOST in src  # aimed at the shared Traefik entrypoint
    assert "e.code != 404" in src  # Traefik's no-route 404 is classified DOWN, never OK


def test_route_ok_when_app_answers(monkeypatch) -> None:
    async def fake_step(_cmd, _timeout):
        return 0, "status 200"

    monkeypatch.setattr(orchestrator, "_compose_smoke_step", fake_step)
    state, _last = asyncio.run(orchestrator._verify_public_route(["docker", "compose"], "backend", "h.isnex.eu"))
    assert state == "ok"


def test_route_down_when_traefik_returns_no_route(monkeypatch) -> None:
    """rc==1 (reached Traefik, 404 no-route / 5xx) → DOWN → the deploy must NOT report success."""

    async def fake_step(_cmd, _timeout):
        return 1, "status 404"

    monkeypatch.setattr(orchestrator, "_compose_smoke_step", fake_step)
    monkeypatch.setattr(orchestrator.asyncio, "sleep", _noop)
    state, _last = asyncio.run(orchestrator._verify_public_route(["docker", "compose"], "backend", "h.isnex.eu"))
    assert state == "down"


def test_route_skip_when_traefik_unreachable(monkeypatch) -> None:
    """rc==2 (could not reach Traefik at all) → SKIP → a defensive skip, never a false FAIL."""

    async def fake_step(_cmd, _timeout):
        return 2, "err connection refused"

    monkeypatch.setattr(orchestrator, "_compose_smoke_step", fake_step)
    monkeypatch.setattr(orchestrator.asyncio, "sleep", _noop)
    state, _last = asyncio.run(orchestrator._verify_public_route(["docker", "compose"], "backend", "h.isnex.eu"))
    assert state == "skip"
