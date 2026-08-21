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

from backend.core import authz
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
    _deterministic_host_port_probe,
    _guard_prod_db_isolation,
    _isolate_system_setting_cache,
    db_connection,
    db_session,
    host_ports,
    test_engine,
)


@pytest.fixture()
def client(db_session):  # noqa: F811
    """TestClient authenticated as the ``admin`` ACCOUNT — the one user who may touch every project.

    It used to seed ``role="ri"``, which under the old tier model meant "may touch every project". The
    role means nothing about projects any more (it governs only the Knowledge Base), so a workflow test
    that operates on a project it did not create needs the admin ACCOUNT, not the ri role — which is
    exactly the distinction the new model rests on, and exactly what these tests should be exercising.
    The username is the load-bearing part; the role below is incidental and kept as ``ri`` only so the
    KB-flavoured assertions that share this fixture keep their existing meaning.
    """

    suffix = _uuid.uuid4().hex[:8]
    ri_user = User(
        username=authz.ADMIN_USERNAME,
        email=f"admin_workflow_{suffix}@test.local",
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


@pytest.fixture(autouse=True)
def _isolate_port_registry(tmp_path_factory, monkeypatch):
    """Keep tests off the LIVE KB port registry.

    ``reserved_ranges_status`` reads ``/home/icc/knowledge/infrastructure/port-registry.yaml``
    (ICCINT-2). Left alone, every create-project test would depend on which blocks ICC has
    allocated in real life — four tests picked 10180, which became reserved for NEX Asistent
    the moment the registry landed, and turned red for a reason that had nothing to do with
    what they were testing. Point the service at an absent file so tests see "no external
    reservations" unless they say otherwise.

    A test that WANTS the real registry (``test_port_registry_reservations``) undoes this
    by setting the attribute back.
    """
    from backend.services import port_registry

    absent = tmp_path_factory.mktemp("no-registry") / "port-registry.yaml"
    monkeypatch.setattr(port_registry, "PORT_REGISTRY_FILE", absent)
