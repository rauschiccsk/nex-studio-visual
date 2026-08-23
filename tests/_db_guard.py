"""Production-DB isolation guard helpers (CR-NS-076).

Pure, unit-testable helpers backing the root ``conftest.py`` guard that
guarantees the test suite can NEVER write to the cockpit/PROD database.

Background: ``backend.db.session.SessionLocal`` is bound to the production
``settings.database_url`` at import time. A test path that calls the shared
``SessionLocal()`` and commits *without* the per-test monkeypatch writes
straight into the live cockpit DB, outside the SAVEPOINT rollback. That is
how a full pipeline tree leaked into ``nexstudio`` on 2026-06-08.
"""


def run_scoped_url(url: str, token: str) -> str:
    """Return ``url`` with its database NAME suffixed by ``token`` — one database PER RUN.

    Why this exists (audit 2026-08-23, finding 6). The suite's schema setup begins with
    ``DROP SCHEMA public CASCADE`` against a SINGLE shared database. That is safe for one run and
    corrupting for two: when two agents work in the same checkout — the normal case in this workshop —
    whichever starts second pulls the schema out from under the first mid-run. The observed damage was
    not a crash but a LIE: the same commit produced ``3139 passed, 2 errors`` (``relation
    "agent_terminal_sessions" does not exist``), then ``11 failed``, then, on a private database,
    ``3141 passed``. A gate that reports failures that do not exist can also hide ones that do, so every
    verdict taken from it — including a release verdict — is worthless until the runs stop sharing.

    The token makes the database name a property of the RUNNING PROCESS (pytest-xdist worker id when
    sharded, else the PID), so concurrent runs cannot collide by construction rather than by scheduling
    luck. Non-alphanumerics are folded to ``_`` because the name goes into ``CREATE DATABASE``.

    >>> run_scoped_url("postgresql+pg8000://u:p@h:5432/nexstudiovisual_test", "gw0")
    'postgresql+pg8000://u:p@h:5432/nexstudiovisual_test_gw0'
    >>> run_scoped_url("postgresql://u:p@h/t?sslmode=require", "p1234")
    'postgresql://u:p@h/t_p1234?sslmode=require'
    """
    safe = "".join(c if c.isalnum() else "_" for c in token)
    head, _, tail = url.rpartition("/")
    name, sep, query = tail.partition("?")
    return f"{head}/{name}_{safe}{sep}{query}"


def database_name(url: str) -> str:
    """Return the database NAME segment of a SQLAlchemy/DB URL.

    Strips the driver, credentials, host:port and any query string, leaving
    just the final path segment (the database name).

    >>> database_name("postgresql+pg8000://u:p@host:5432/nexstudio?sslmode=require")
    'nexstudio'
    """
    return url.rsplit("/", 1)[-1].split("?")[0]


def assert_test_db_distinct(production_url: str, test_url: str) -> None:
    """Abort the run if the test DB is not a DISTINCT database from PROD.

    Compares the database NAME segment of each URL and raises ``RuntimeError``
    when they match — catching a mis-set ``TEST_DATABASE_URL`` that would make
    tests (and the ``test_engine`` ``create_all``/``drop_all``) operate on the
    cockpit/PROD database.
    """
    prod_name = database_name(production_url)
    test_name = database_name(test_url)
    if prod_name == test_name:
        raise RuntimeError(
            "Test isolation guard (CR-NS-076): the test database "
            f"({test_name!r}) must be a DISTINCT database from the production "
            f"database ({prod_name!r}). Refusing to run — tests would read/write "
            "and drop_all against the cockpit/PROD DB. Point TEST_DATABASE_URL "
            "at a separate database (default: nexstudio_test)."
        )
