"""Tests for the CR-NS-076 production-DB isolation guard.

These verify that during a test run the shared ``SessionLocal`` / ``engine``
are bound to the TEST database (never the cockpit/PROD DB), and that the
distinctness guard aborts when the two are the same database.
"""

import pytest

from backend.config.settings import settings
from backend.db import session as db_session_module
from tests._db_guard import assert_test_db_distinct, database_name, run_scoped_url


def test_sessionlocal_binds_to_test_db_not_prod():
    """The live, shared ``SessionLocal()`` must bind to the TEST database.

    This is the core invariant: even without a per-test monkeypatch, opening
    the module-global session must NOT reach the production (cockpit) database.
    """
    live = db_session_module.SessionLocal()
    try:
        bound_db = live.get_bind().url.database
    finally:
        live.close()

    prod_db = database_name(settings.database_url)
    assert bound_db != prod_db, f"SessionLocal is bound to the production DB {bound_db!r} during tests"


def test_engine_module_attr_points_at_test_db():
    """The module-level ``engine`` is rebound to the test DB during the session."""
    assert db_session_module.engine.url.database != database_name(settings.database_url)


def test_database_name_extraction():
    """``database_name`` strips driver/credentials/host/query, leaving the DB name."""
    assert database_name("postgresql+pg8000://u:p@h:5432/nexstudio") == "nexstudio"
    assert database_name("postgresql://u:p@h/nexstudio_test?sslmode=require") == "nexstudio_test"


def test_assert_test_db_distinct_aborts_when_identical():
    """The guard raises when the test DB equals the production DB."""
    same = "postgresql+pg8000://u:p@h:5432/nexstudio"
    with pytest.raises(RuntimeError, match="DISTINCT"):
        assert_test_db_distinct(same, same)


def test_assert_test_db_distinct_passes_when_different():
    """No exception when the test DB is a distinct database from production."""
    assert (
        assert_test_db_distinct(
            "postgresql+pg8000://u:p@h/nexstudio",
            "postgresql+pg8000://u:p@h/nexstudio_test",
        )
        is None
    )


# ── one database per RUN (audit 2026-08-23, finding 6) ────────────────────────
#
# The shared test database was not merely untidy, it made the gate dishonest: two agents running pytest in
# the same checkout each begin with ``DROP SCHEMA public CASCADE``, so the second one pulls the schema out
# from under the first and the loser reports failures that do not exist. A verdict from such a gate cannot
# be trusted in either direction, so the run's database name is now derived from the running process.


def test_a_run_scoped_url_keeps_everything_but_the_database_name():
    assert (
        run_scoped_url("postgresql+pg8000://u:p@h:5432/nexstudiovisual_test", "gw0")
        == "postgresql+pg8000://u:p@h:5432/nexstudiovisual_test_gw0"
    )


def test_a_run_scoped_url_keeps_the_query_string():
    """Query params sit AFTER the name — a naive append would produce ``…_test?sslmode=require_p1``."""
    assert (
        run_scoped_url("postgresql://u:p@h/t?sslmode=require", "p1234") == "postgresql://u:p@h/t_p1234?sslmode=require"
    )


def test_two_processes_never_get_the_same_database():
    base = "postgresql+pg8000://u:p@h:5432/nexstudiovisual_test"
    assert run_scoped_url(base, "p111") != run_scoped_url(base, "p222")


def test_a_run_scoped_name_is_safe_for_create_database():
    """The token lands in ``CREATE DATABASE "<name>"`` — anything but letters/digits is folded away."""
    scoped = run_scoped_url("postgresql://u:p@h/t", 'gw0"; DROP DATABASE nexstudio; --')
    assert database_name(scoped).isidentifier()


def test_the_run_database_is_still_distinct_from_production():
    """Suffixing must not defeat the CR-NS-076 guard — it makes the distinctness stronger, not weaker."""
    assert_test_db_distinct(
        "postgresql+pg8000://u:p@h/nexstudio",
        run_scoped_url("postgresql+pg8000://u:p@h/nexstudio_test", "p9"),
    )


def test_this_very_run_is_using_its_own_database():
    """Not a unit test of the helper — the live proof that THIS session got a private database."""
    from tests.conftest import _base_test_database_url, _get_test_database_url

    live = db_session_module.engine.url.database
    assert live == database_name(_get_test_database_url())
    assert live != database_name(_base_test_database_url()), (
        "this run is on the SHARED test database — a concurrent run would drop its schema mid-test"
    )
