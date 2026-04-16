"""Conftest for ``backend/tests``.

The canonical pytest fixtures (SAVEPOINT-isolated ``db_session``,
FastAPI ``client``, session-scoped ``test_engine``) live in
``tests/conftest.py`` at the repository root. Pytest discovers
conftest.py files by walking up from each test file, so fixtures defined
in ``tests/conftest.py`` are NOT visible to tests under
``backend/tests/``.

Re-importing the fixture functions here exposes them to pytest's
fixture discovery without duplicating their implementation. The
``@pytest.fixture`` decorator survives the import, so pytest treats
each name as a locally-defined fixture of the same scope.
"""

from tests.conftest import (  # noqa: F401
    client,
    db_connection,
    db_session,
    test_engine,
)
