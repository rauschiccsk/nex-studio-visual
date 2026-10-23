"""DEV-192 — what a page of the cockpit crashed on is written to the backend's log, so it can be found.

23.10.2026 the cockpit had no error boundary: a page that crashed left the window white and nothing reached the
server. The page now reports the error; the backend writes it with who and where, cut to a size a log can carry.
"""

from __future__ import annotations

import logging
import uuid

import pytest
from fastapi.testclient import TestClient

from backend.core.security import get_current_user
from backend.db.models.foundation import User
from backend.main import app


@pytest.fixture(autouse=True)
def _backend_logs_reach_caplog(monkeypatch):
    """main.py stops the ``backend`` logger at itself (stderr for docker logs); caplog listens above it."""
    monkeypatch.setattr(logging.getLogger("backend"), "propagate", True)


def _user(db) -> User:
    user = User(
        username=f"u-{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:8]}@e.sk", password_hash="x", role="ri"
    )
    db.add(user)
    db.flush()
    return user


def test_the_error_is_written_with_who_and_where(client: TestClient, db_session, caplog):
    user = _user(db_session)
    db_session.commit()
    app.dependency_overrides[get_current_user] = lambda: user

    with caplog.at_level(logging.WARNING, logger="backend.api.routes.client_errors"):
        response = client.post(
            "/api/v1/client-errors",
            json={
                "message": "Cannot read properties of undefined (reading 'versions')",
                "stack": "TypeError: …\n    at PoradcaPage",
                "path": "/poradca?verzia=v1",
                "version": "4.80.0",
            },
        )

    assert response.status_code == 204
    written = [r.getMessage() for r in caplog.records if r.name == "backend.api.routes.client_errors"]
    assert len(written) == 1
    assert user.username in written[0]
    assert "/poradca?verzia=v1" in written[0]
    assert "4.80.0" in written[0]
    assert "reading 'versions'" in written[0]


def test_a_long_report_is_cut_to_what_a_log_carries(client: TestClient, db_session, caplog):
    user = _user(db_session)
    db_session.commit()
    app.dependency_overrides[get_current_user] = lambda: user

    with caplog.at_level(logging.WARNING, logger="backend.api.routes.client_errors"):
        response = client.post(
            "/api/v1/client-errors", json={"message": "x" * 50_000, "stack": "y" * 50_000, "path": "/", "version": ""}
        )

    assert response.status_code == 204
    written = [r.getMessage() for r in caplog.records if r.name == "backend.api.routes.client_errors"]
    assert len(written[0]) < 12_000


def test_nobody_signed_in_cannot_write_to_the_log(client: TestClient):
    app.dependency_overrides.pop(get_current_user, None)

    response = client.post("/api/v1/client-errors", json={"message": "m", "stack": "", "path": "/", "version": ""})

    assert response.status_code == 401
