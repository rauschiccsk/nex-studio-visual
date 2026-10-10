"""Database session configuration."""

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from backend.config.settings import settings


@event.listens_for(Engine, "connect")
def _compute_in_utc(dbapi_connection, _connection_record) -> None:
    """DEV-59 — the cockpit computes and exchanges time in UTC; people get it on their own clock.

    The database's DEFAULT zone is the Director's (migration 114), so whoever reads it directly sees local time. The
    cockpit's own connections are pinned to UTC here, on every PostgreSQL connection of every engine (the tests'
    too): the API then never mixes offsets — a time read from the database and one computed in Python come out in
    the same zone, and the screen compares and sorts them correctly. The backend writes times for people through
    :func:`backend.core.local_time.local_time`, the screen through the browser's clock."""
    if not type(dbapi_connection).__module__.startswith(("pg8000", "psycopg")):
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("SET TIME ZONE 'UTC'")
    finally:
        cursor.close()
    # The driver ran it inside an implicit transaction; a rollback would undo the SET with it (PostgreSQL reverts
    # session settings changed in a rolled-back transaction) — so it is committed here, on its own.
    dbapi_connection.commit()


def _ensure_pg8000_driver(url: str) -> str:
    """Ensure the database URL uses the pg8000 driver."""
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+pg8000://", 1)
    return url


engine = create_engine(
    _ensure_pg8000_driver(settings.database_url),
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)

SessionLocal = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


def get_db():
    """FastAPI dependency that provides a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
