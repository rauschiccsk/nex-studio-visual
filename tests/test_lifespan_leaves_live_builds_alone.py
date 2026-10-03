"""The test session must never touch docker objects of a LIVE build (ICCINT-162, incident 03.10.2026).

The backend's startup sweeps every container and network wearing ``build_db.OWNER_LABEL`` — correct in
production, where the process that starts owns no turn yet. But every ``TestClient(app)`` enters the same
lifespan, and the ``client`` fixture is function-scoped: one pytest run on ANDROS swept the database of the
dedo-home Programovanie turn that was running at that moment, and the agent lost its ``db`` mid-turn. The
nex-studio-visual CI ``Test`` job runs on a self-hosted runner on that same host, as a user in the
``docker`` group — so every push did the same to whatever build happened to be running.

A test process owns no build turn and never had a predecessor whose leftovers it should clean, so for it
everything wearing the label belongs to somebody else.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.main import app
from backend.services import build_db


def test_entering_the_app_lifespan_issues_no_docker_command(monkeypatch):
    issued: list[tuple[str, ...]] = []

    async def _recording_docker(*args: str, timeout: float = 0) -> tuple[int, str]:
        issued.append(args)
        return 0, ""

    monkeypatch.setattr(build_db, "_docker", _recording_docker)

    with TestClient(app):
        pass

    assert issued == [], f"the test lifespan ran docker against the host: {issued}"
