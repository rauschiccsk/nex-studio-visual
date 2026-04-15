"""Tests verifying the SAVEPOINT-isolated db_session fixture works correctly."""

from sqlalchemy import text


def test_db_session_provides_working_connection(db_session):
    """db_session fixture can execute queries."""
    result = db_session.execute(text("SELECT 1"))
    assert result.scalar() == 1


def test_db_session_rollback_isolation(db_session):
    """Changes in one test are NOT visible in another — each test is isolated."""
    # Create a temporary table and insert a row
    db_session.execute(text("CREATE TABLE IF NOT EXISTS _test_isolation (id serial PRIMARY KEY, val text)"))
    db_session.execute(text("INSERT INTO _test_isolation (val) VALUES ('should_not_persist')"))
    db_session.commit()  # commit flushes via savepoint, not outer txn

    result = db_session.execute(text("SELECT val FROM _test_isolation"))
    assert result.scalar() == "should_not_persist"


def test_db_session_isolation_verified(db_session):
    """Verify that the previous test's table does not exist (rollback worked)."""
    result = db_session.execute(
        text("SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = '_test_isolation')")
    )
    assert result.scalar() is False
