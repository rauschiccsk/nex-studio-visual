"""Pytest configuration with SAVEPOINT transaction isolation.

Uses TEST_DATABASE_URL — NEVER the production DATABASE_URL.
Each test runs in a savepoint that is rolled back after the test,
keeping the test database clean without costly create/drop cycles.
"""

import os

# Set required env vars BEFORE any backend imports trigger Settings() instantiation
os.environ.setdefault("GITHUB_TOKEN", "ghp_test_dummy_token")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

# ``backend.db.base`` imports every ORM model — importing it here populates
# ``Base.metadata`` with every table before ``create_all`` runs.
from backend.db.base import Base

# Explicit model imports for ``Base.metadata`` awareness — required by the
# Model Generation Checklist. ``backend.db.base`` re-exports these, but each
# new model is listed explicitly here so missing registrations surface as
# import errors during test collection.
from backend.db.models.bugs import Bug  # noqa: F401
from backend.db.models.projects import Project  # noqa: F401
from backend.db.models.tasks import Epic  # noqa: F401
from backend.db.models.versions import Version  # noqa: F401
from backend.db.session import _ensure_pg8000_driver, get_db
from backend.main import app


def _get_test_database_url() -> str:
    """Return the test database URL, ensuring pg8000 driver."""
    from backend.config.settings import settings

    url = os.environ.get("TEST_DATABASE_URL", settings.test_database_url)
    return _ensure_pg8000_driver(url)


def _ensure_test_database_exists(test_url: str) -> None:
    """Create the test database if it does not exist.

    Connects to the default 'postgres' database to issue CREATE DATABASE.
    """
    # Derive admin URL by replacing the DB name with 'postgres'
    parts = test_url.rsplit("/", 1)
    admin_url = parts[0] + "/postgres"

    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    db_name = parts[1].split("?")[0]  # strip query params if any

    with admin_engine.connect() as conn:
        result = conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": db_name})
        if not result.scalar():
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))  # noqa: S608
    admin_engine.dispose()


@pytest.fixture(scope="session")
def test_engine():
    """Create a SQLAlchemy engine for the test database (session-scoped)."""
    url = _get_test_database_url()
    _ensure_test_database_exists(url)

    engine = create_engine(url, pool_pre_ping=True)

    # Create all tables
    Base.metadata.create_all(bind=engine)

    yield engine

    # Drop all tables after all tests
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture()
def db_connection(test_engine):
    """Provide a connection with an open transaction that is rolled back after each test."""
    connection = test_engine.connect()
    transaction = connection.begin()

    yield connection

    transaction.rollback()
    connection.close()


@pytest.fixture()
def db_session(db_connection):
    """Provide a Session using SAVEPOINT isolation.

    session.commit() flushes but does NOT commit the outer transaction.
    After the test, the outer transaction is rolled back — all changes disappear.
    """
    session = Session(
        bind=db_connection,
        join_transaction_mode="create_savepoint",
    )

    yield session

    session.close()


@pytest.fixture()
def client(db_session):
    """Provide a FastAPI TestClient with the DB dependency overridden.

    All endpoint tests should use this fixture to ensure requests
    hit the SAVEPOINT-isolated test database, not the production DB.
    """

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
