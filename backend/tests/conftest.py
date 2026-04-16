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

# Explicit model imports for ``Base.metadata`` awareness — required by the
# Model Generation Checklist. Although ``tests.conftest`` already populates
# the metadata via ``backend.db.base``, each new model is listed here so a
# missing registration surfaces as an ImportError during test collection.
from backend.db.models.bugs import Bug  # noqa: F401
from backend.db.models.projects import Project  # noqa: F401
from backend.db.models.tasks import Epic  # noqa: F401
from backend.db.models.versions import Version  # noqa: F401
from tests.conftest import (  # noqa: F401
    client,
    db_connection,
    db_session,
    test_engine,
)
