"""Tests for the per-project Customers registry (v2.0.0, CR-V2-025).

Covers :mod:`backend.services.customer` and the customers REST router against the
SAVEPOINT-isolated session from ``tests/conftest.py``. The load-bearing checks
per the CR's safety invariants:

* a customer is added via the form → row persisted (service + HTTP);
* a per-customer **secret** entered is NEVER echoed back in the API response and
  NEVER stored on a ``customers`` column / in the row's dump — it lives only in
  the credentials store, surfaced as a ``has_secret`` boolean (CLAUDE.md §4/§5,
  OQ-5);
* **ICC s.r.o.** (the internal app's customer) registers through the *identical*
  form / code path as any external customer — one path, no internal branch
  (design §3.2);
* slug uniqueness within a project; project-scoping; secret rotation; delete
  also removes the stored secret.
"""

from __future__ import annotations

import uuid

import pytest

from backend.config.settings import settings
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.schemas.customer import CustomerCreate, CustomerRead, CustomerUpdate
from backend.services import credentials as credentials_service
from backend.services import customer as service

# ---------------------------------------------------------------------------
# Isolation — every test in this module writes secrets to a throwaway store,
# never the real ``/opt/data/nex-studio/credentials`` directory.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_credentials_store(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "credentials_storage_path", str(tmp_path / "creds"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(db_session, **overrides) -> User:
    defaults = {
        "username": f"user_{uuid.uuid4().hex[:8]}",
        "email": f"{uuid.uuid4().hex[:8]}@example.com",
        "password_hash": "hashed_password_placeholder",
        "role": "ri",
    }
    defaults.update(overrides)
    user = User(**defaults)
    db_session.add(user)
    db_session.flush()
    return user


def _make_project(db_session, *, user: User | None = None, **overrides) -> Project:
    if user is None:
        user = _make_user(db_session)
    suffix = uuid.uuid4().hex[:8]
    defaults = {
        "name": f"Project {suffix}",
        "slug": f"project-{suffix}",
        "type": "standard",
        "auth_mode": "password",
        "description": "Test project description",
        "created_by": user.id,
    }
    defaults.update(overrides)
    project = Project(**defaults)
    db_session.add(project)
    db_session.flush()
    return project


# ---------------------------------------------------------------------------
# create / list / get
# ---------------------------------------------------------------------------


def test_create_persists_row(db_session):
    project = _make_project(db_session)
    customer = service.create(
        db_session,
        project.id,
        CustomerCreate(name="ANDROS", slug="andros", subdomain="andros"),
    )
    assert customer.id is not None
    assert customer.project_id == project.id
    assert customer.name == "ANDROS"
    assert customer.slug == "andros"
    assert customer.subdomain == "andros"
    assert customer.credential_id is None

    fetched = service.get_by_id(db_session, customer.id)
    assert fetched.id == customer.id


def test_create_with_integrations_jsonb(db_session):
    project = _make_project(db_session)
    customer = service.create(
        db_session,
        project.id,
        CustomerCreate(name="C", slug="c", integrations={"erp": "nex-genesis", "smb": True}),
    )
    db_session.refresh(customer)
    assert customer.integrations == {"erp": "nex-genesis", "smb": True}


def test_create_unknown_project_raises(db_session):
    with pytest.raises(ValueError, match="not found"):
        service.create(db_session, uuid.uuid4(), CustomerCreate(name="X", slug="x"))


def test_create_duplicate_slug_in_project_rejected(db_session):
    project = _make_project(db_session)
    service.create(db_session, project.id, CustomerCreate(name="A", slug="dup"))
    with pytest.raises(ValueError, match="already exists"):
        service.create(db_session, project.id, CustomerCreate(name="B", slug="dup"))


def test_same_slug_allowed_across_projects(db_session):
    p1 = _make_project(db_session)
    p2 = _make_project(db_session)
    c1 = service.create(db_session, p1.id, CustomerCreate(name="A", slug="shared"))
    c2 = service.create(db_session, p2.id, CustomerCreate(name="A", slug="shared"))
    assert c1.id != c2.id
    assert c1.project_id != c2.project_id


def test_list_is_project_scoped(db_session):
    p1 = _make_project(db_session)
    p2 = _make_project(db_session)
    service.create(db_session, p1.id, CustomerCreate(name="A", slug="a"))
    service.create(db_session, p1.id, CustomerCreate(name="B", slug="b"))
    service.create(db_session, p2.id, CustomerCreate(name="C", slug="c"))

    p1_customers = service.list_customers(db_session, p1.id)
    assert {c.slug for c in p1_customers} == {"a", "b"}
    p2_customers = service.list_customers(db_session, p2.id)
    assert {c.slug for c in p2_customers} == {"c"}


# ---------------------------------------------------------------------------
# ICC s.r.o. — internal customer through the SAME form (design §3.2)
# ---------------------------------------------------------------------------


def test_icc_sro_registers_through_same_path(db_session):
    """The internal app's customer is just ICC s.r.o. via the identical create()."""
    project = _make_project(db_session)
    customer = service.create(
        db_session,
        project.id,
        CustomerCreate(name="ICC s.r.o.", slug="icc", subdomain="icc"),
    )
    # Same code path, same row shape — there is no internal/external flag on the
    # model at all (no branch to assert; its absence is the contract).
    assert not hasattr(customer, "is_internal")
    assert customer.name == "ICC s.r.o."
    assert customer in service.list_customers(db_session, project.id)


# ---------------------------------------------------------------------------
# SAFETY INVARIANT — secret never echoed / never on the row (CLAUDE.md §4/§5)
# ---------------------------------------------------------------------------

_SECRET = "super-secret-customer-token-DO-NOT-LEAK"


def test_secret_not_stored_on_customer_row(db_session):
    project = _make_project(db_session)
    customer = service.create(
        db_session,
        project.id,
        CustomerCreate(name="S", slug="s", secret=_SECRET),
    )
    # The customer row holds only a POINTER, never the secret value.
    assert customer.credential_id is not None
    db_session.refresh(customer)
    for col_value in (
        customer.name,
        customer.slug,
        customer.subdomain,
        customer.notes,
        str(customer.integrations),
    ):
        assert _SECRET not in (col_value or "")

    # The secret VALUE lives only in the credentials store.
    stored = credentials_service.read_content(db_session, customer.credential_id)
    assert stored.content == _SECRET


def test_customer_read_schema_has_no_secret_field_and_does_not_echo(db_session):
    project = _make_project(db_session)
    customer = service.create(
        db_session,
        project.id,
        CustomerCreate(name="S", slug="s", secret=_SECRET),
    )
    read = CustomerRead(
        id=customer.id,
        project_id=customer.project_id,
        name=customer.name,
        slug=customer.slug,
        subdomain=customer.subdomain,
        integrations=customer.integrations,
        notes=customer.notes,
        has_secret=customer.credential_id is not None,
        created_at=customer.created_at,
        updated_at=customer.updated_at,
    )
    # No secret field exists on the read schema at all.
    assert "secret" not in CustomerRead.model_fields
    assert read.has_secret is True
    # The serialised response carries no secret material whatsoever.
    dumped = read.model_dump_json()
    assert _SECRET not in dumped
    assert "credential_id" not in read.model_dump()


def test_create_without_secret_has_no_credential(db_session):
    project = _make_project(db_session)
    customer = service.create(db_session, project.id, CustomerCreate(name="N", slug="n"))
    assert customer.credential_id is None


def test_secret_rotation_overwrites_store_not_row(db_session):
    project = _make_project(db_session)
    customer = service.create(
        db_session,
        project.id,
        CustomerCreate(name="R", slug="r", secret="old-secret"),
    )
    cred_id = customer.credential_id
    assert cred_id is not None

    updated = service.update(db_session, customer.id, CustomerUpdate(secret="new-secret"))
    # Same credential row reused (rotation, not re-create).
    assert updated.credential_id == cred_id
    stored = credentials_service.read_content(db_session, cred_id)
    assert stored.content == "new-secret"


def test_update_adds_secret_when_none_existed(db_session):
    project = _make_project(db_session)
    customer = service.create(db_session, project.id, CustomerCreate(name="A", slug="a"))
    assert customer.credential_id is None
    updated = service.update(db_session, customer.id, CustomerUpdate(secret="fresh"))
    assert updated.credential_id is not None
    assert credentials_service.read_content(db_session, updated.credential_id).content == "fresh"


# ---------------------------------------------------------------------------
# update / delete
# ---------------------------------------------------------------------------


def test_update_mutable_fields(db_session):
    project = _make_project(db_session)
    customer = service.create(db_session, project.id, CustomerCreate(name="A", slug="a"))
    updated = service.update(
        db_session,
        customer.id,
        CustomerUpdate(name="A2", subdomain="a2", integrations={"x": 1}, notes="hello"),
    )
    assert updated.name == "A2"
    assert updated.subdomain == "a2"
    assert updated.integrations == {"x": 1}
    assert updated.notes == "hello"
    # Immutable identity preserved.
    assert updated.id == customer.id
    assert updated.project_id == project.id


def test_update_slug_collision_rejected(db_session):
    project = _make_project(db_session)
    service.create(db_session, project.id, CustomerCreate(name="A", slug="taken"))
    other = service.create(db_session, project.id, CustomerCreate(name="B", slug="free"))
    with pytest.raises(ValueError, match="already exists"):
        service.update(db_session, other.id, CustomerUpdate(slug="taken"))


def test_update_unknown_raises(db_session):
    with pytest.raises(ValueError, match="not found"):
        service.update(db_session, uuid.uuid4(), CustomerUpdate(name="X"))


def test_delete_removes_row_and_secret(db_session):
    project = _make_project(db_session)
    customer = service.create(
        db_session,
        project.id,
        CustomerCreate(name="D", slug="d", secret=_SECRET),
    )
    cred_id = customer.credential_id
    assert cred_id is not None

    service.delete(db_session, customer.id)

    with pytest.raises(ValueError, match="not found"):
        service.get_by_id(db_session, customer.id)
    # The stored secret is gone too — no orphan secret survives.
    with pytest.raises(ValueError, match="not found"):
        credentials_service.read_content(db_session, cred_id)


def test_delete_unknown_raises(db_session):
    with pytest.raises(ValueError, match="not found"):
        service.delete(db_session, uuid.uuid4())


def test_secret_file_lands_in_credentials_store_dir(db_session, tmp_path, monkeypatch):
    """The secret file is written under the ri-gated credentials store root, mode 0600."""
    monkeypatch.setattr(settings, "credentials_storage_path", str(tmp_path))
    project = _make_project(db_session)
    customer = service.create(
        db_session,
        project.id,
        CustomerCreate(name="F", slug="f", secret=_SECRET),
    )
    cred = credentials_service.get_by_id(db_session, customer.credential_id)
    from pathlib import Path

    p = Path(cred.file_path)
    assert p.parent == tmp_path
    assert p.read_text(encoding="utf-8") == _SECRET
    # Owner-only file mode.
    assert (p.stat().st_mode & 0o777) == 0o600


# ---------------------------------------------------------------------------
# HTTP layer — form add + secret never echoed
# ---------------------------------------------------------------------------


def _auth_ri(client):
    """Override RBAC so the customers router's ri-gated POST/PATCH/DELETE pass."""
    from backend.core.security import get_current_user, require_ri_role
    from backend.main import app

    ri_user = User(
        id=uuid.uuid4(),
        username="ri_tester",
        email="ri@example.com",
        password_hash="x",
        role="ri",
    )
    app.dependency_overrides[require_ri_role] = lambda: ri_user
    app.dependency_overrides[get_current_user] = lambda: ri_user


def test_http_create_persists_and_never_echoes_secret(client, db_session):
    _auth_ri(client)
    user = _make_user(db_session)
    project = _make_project(db_session, user=user)
    db_session.commit()

    resp = client.post(
        f"/api/v1/projects/{project.slug}/customers",
        json={"name": "ICC s.r.o.", "slug": "icc", "subdomain": "icc", "secret": _SECRET},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    # Row persisted + has_secret signalled, but the secret value is NOWHERE in
    # the response payload (raw text assertion = belt-and-suspenders).
    assert body["name"] == "ICC s.r.o."
    assert body["has_secret"] is True
    assert "secret" not in body
    assert "credential_id" not in body
    assert _SECRET not in resp.text

    # The GET list / detail likewise never echo the secret.
    lst = client.get(f"/api/v1/projects/{project.slug}/customers")
    assert lst.status_code == 200
    assert _SECRET not in lst.text
    assert lst.json()[0]["has_secret"] is True

    detail = client.get(f"/api/v1/customers/{body['id']}")
    assert detail.status_code == 200
    assert _SECRET not in detail.text


def test_http_create_unknown_project_404(client, db_session):
    _auth_ri(client)
    resp = client.post(
        "/api/v1/projects/does-not-exist/customers",
        json={"name": "X", "slug": "x"},
    )
    assert resp.status_code == 404


def test_http_duplicate_slug_409(client, db_session):
    _auth_ri(client)
    project = _make_project(db_session)
    db_session.commit()
    client.post(f"/api/v1/projects/{project.slug}/customers", json={"name": "A", "slug": "dup"})
    resp = client.post(f"/api/v1/projects/{project.slug}/customers", json={"name": "B", "slug": "dup"})
    assert resp.status_code == 409
