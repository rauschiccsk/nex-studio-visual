"""Test utilities for NEX Studio backend.

Provides helpers for creating FastAPI TestClient with DB session override,
and common test factory functions.
"""

from collections.abc import Generator
from contextlib import contextmanager

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.db.session import get_db
from backend.main import app


@contextmanager
def create_test_client(db_session: Session) -> Generator[TestClient]:
    """Create a FastAPI TestClient with the db_session dependency overridden.

    This ensures all API endpoint tests use the SAVEPOINT-isolated session
    instead of hitting the real database.

    Usage in tests::

        with create_test_client(db_session) as client:
            response = client.get("/health")
            assert response.status_code == 200
    """

    def _override_get_db() -> Generator[Session]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
