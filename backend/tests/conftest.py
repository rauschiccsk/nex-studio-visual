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
import uuid as _uuid

# ---------------------------------------------------------------------------
# Local ``client`` fixture — backend/tests are integration workflows that
# all hit RBAC-gated routes. We override role gates here to a seeded ri
# user so test bodies (which never sent JWTs) keep working after the
# M2.D RBAC roll-out (2026-05-07). tests/api/test_auth_*.py — which
# deliberately assert 401 / 403 — use the root tests/conftest.py
# ``client`` fixture which DOES NOT override role gates.
# ---------------------------------------------------------------------------
import bcrypt
import pytest
from fastapi.testclient import TestClient

from backend.core.security import (
    get_current_user,
    require_ha_or_above,
    require_ri_role,
    require_shu_or_above,
)
from backend.db.models.bugs import Bug  # noqa: F401
from backend.db.models.foundation import User
from backend.db.models.projects import Project  # noqa: F401
from backend.db.models.system_settings import SystemSetting  # noqa: F401
from backend.db.models.tasks import Epic  # noqa: F401
from backend.db.models.versions import Version  # noqa: F401
from backend.db.session import get_db
from backend.main import app
from tests.conftest import (  # noqa: F401
    _guard_prod_db_isolation,
    db_connection,
    db_session,
    test_engine,
)


@pytest.fixture()
def client(db_session):  # noqa: F811
    """TestClient with DB + RBAC dependencies overridden to an ri user."""

    suffix = _uuid.uuid4().hex[:8]
    ri_user = User(
        username=f"ri_workflow_{suffix}",
        email=f"ri_workflow_{suffix}@test.local",
        password_hash=bcrypt.hashpw(b"test", bcrypt.gensalt(rounds=4)).decode(),
        role="ri",
        is_active=True,
    )
    db_session.add(ri_user)
    db_session.flush()

    def _override_get_db():
        yield db_session

    def _override_user() -> User:
        return ri_user

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[require_ri_role] = _override_user
    app.dependency_overrides[require_ha_or_above] = _override_user
    app.dependency_overrides[require_shu_or_above] = _override_user

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()
