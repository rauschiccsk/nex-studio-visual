"""DEV-198 — the Vizuál mockup of a version that does not exist is „not found“ (404), not a server error (500).

23.10.2026 on the live cockpit ``GET /api/v1/pipeline/<unknown version>/vizual-mockup`` answered 500: the lookup of the
version's project raised before the route could say what it promises — 404 when there is no mockup. The route is
public (the iframe carries no login), so a stale or mistyped address is an ordinary visitor, not a fault of the cockpit.
"""

from __future__ import annotations

import uuid

from backend.services import orchestrator


def test_a_missing_version_has_no_mockup(db_session):
    assert orchestrator.read_vizual_mockup(db_session, uuid.uuid4()) is None


def test_the_route_answers_not_found(client):
    response = client.get(f"/api/v1/pipeline/{uuid.uuid4()}/vizual-mockup")

    assert response.status_code == 404
    assert response.json() == {"detail": "No Vizuál mockup for this version."}
