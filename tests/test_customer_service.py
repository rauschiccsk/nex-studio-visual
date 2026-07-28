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
  also removes the stored secret;
* **deploy-safe identifiers** — the form refuses exactly what the deploy path
  refuses (``^[a-z0-9][a-z0-9-]*$`` on ``(subdomain or slug)``), and no two
  customers of a project may resolve to ONE instance directory.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from backend.config.settings import settings
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.schemas.customer import CustomerCreate, CustomerRead, CustomerUpdate
from backend.services import credentials as credentials_service
from backend.services import customer as service
from backend.services import deploy as deploy_service
from backend.services import uat_provisioner

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
    """Authenticate as the ``admin`` ACCOUNT so the customers router's writes pass.

    The customers routes authorize through the owning PROJECT, and the ri role no longer reaches a
    project it does not own — only the admin account does.
    """
    from backend.core import authz
    from backend.core.security import get_current_user, require_ri_role
    from backend.main import app

    ri_user = User(
        id=uuid.uuid4(),
        username=authz.ADMIN_USERNAME,
        email="admin@example.com",
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


# ---------------------------------------------------------------------------
# Deploy-safe identifiers — the form refuses what the deploy path refuses
#
# Audit finding: ``slug`` / ``subdomain`` carried only length limits, while the deploy
# path derives the instance directory from ``(subdomain or slug).lower()`` and runs it
# through ``uat_provisioner.validate_uat_slug`` (``^[a-z0-9][a-z0-9-]*$``). A space / dot /
# underscore was accepted at the form and only surfaced days later as a FAILED DEPLOY.
# ---------------------------------------------------------------------------

# Values the deploy path cannot use — each was accepted by the old form.
_DEPLOY_HOSTILE = [
    "andros s.r.o.",  # space
    "andros.sk",  # dot
    "andros_sk",  # underscore
    "-andros",  # leading hyphen
    "andros/uat",  # slash
    "androš",  # diacritics
    "   ",  # whitespace only → empty after trim
    "@ndros",  # punctuation
]


@pytest.mark.parametrize("bad", _DEPLOY_HOSTILE)
def test_create_schema_rejects_deploy_hostile_slug(bad):
    with pytest.raises(ValidationError) as exc:
        CustomerCreate(name="X", slug=bad)
    # The message must TEACH the rule in Slovak, not just say "invalid".
    message = str(exc.value)
    assert "Skratka" in message
    assert "malé písmená a-z" in message
    assert "spojovník" in message


@pytest.mark.parametrize("bad", [v for v in _DEPLOY_HOSTILE if v.strip()])
def test_create_schema_rejects_deploy_hostile_subdomain(bad):
    with pytest.raises(ValidationError) as exc:
        CustomerCreate(name="X", slug="ok", subdomain=bad)
    assert "Subdoména" in str(exc.value)


@pytest.mark.parametrize("bad", _DEPLOY_HOSTILE)
def test_update_schema_accepts_a_slug_the_effective_label_can_still_survive(bad):
    """The PATCH shape is deliberately LENIENT on ``slug`` — the service checks the real thing.

    Deploy validates the DERIVED label ``(subdomain or slug)``, not the two fields separately. A
    customer registered before the rule — slug ``icc.sk`` with subdomain ``icc`` — resolves to
    ``icc`` and deploys perfectly well, and the edit form resends the stored slug on every save. A
    per-field rejection here would therefore block every unrelated edit (fixing a typo in the name)
    on a row that is not broken. The guard that matters lives in ``customer_service.update``, which
    knows both values after the merge — see the test below.
    """
    assert CustomerUpdate(slug=bad).slug == bad.strip().lower()


def test_update_service_refuses_an_edit_that_moves_the_customer_to_an_unusable_directory(db_session):
    """...and that is where the real guard is: the EFFECTIVE label must stay deploy-safe."""
    project = _make_project(db_session)
    customer = service.create(
        db_session,
        project.id,
        CustomerCreate(name="ANDROS", slug="andros", subdomain=None),
    )
    with pytest.raises(ValueError, match="nedá použiť"):
        service.update(db_session, customer.id, CustomerUpdate(slug="andros.sk"))


def test_schema_normalizes_to_the_value_the_deploy_path_will_use():
    """Stored value == deploy-derived value: trimmed + lowercased, so the two cannot drift."""
    payload = CustomerCreate(name="ANDROS", slug="  ANDROS-SK  ", subdomain="  Andros  ")
    assert payload.slug == "andros-sk"
    assert payload.subdomain == "andros"


def test_schema_blank_subdomain_becomes_none_not_an_empty_instance_dir():
    """A whitespace-only subdomain is truthy in ``(subdomain or slug)`` → it would reach the deploy
    path as an EMPTY instance directory. It must normalise to None (= fall back to the slug)."""
    assert CustomerCreate(name="X", slug="x", subdomain="   ").subdomain is None
    assert CustomerCreate(name="X", slug="x", subdomain="").subdomain is None
    assert CustomerUpdate(subdomain="  ").subdomain is None


@pytest.mark.parametrize("good", ["icc", "andros", "andros-sk", "a1", "9lives", "x-y-z"])
def test_schema_accepts_what_the_deploy_path_accepts(good):
    """Whatever the form accepts, ``validate_uat_slug`` must accept — that is the whole point.

    Asserted against the REAL validator (not a copy of the pattern), so a future change to
    the deploy rule that this schema does not follow fails here.
    """
    payload = CustomerCreate(name="X", slug=good, subdomain=good)
    uat_provisioner.validate_uat_slug(payload.slug)
    uat_provisioner.validate_uat_slug(payload.subdomain)
    # …and the directory the deploy path derives from the stored row is likewise valid.
    uat_provisioner.validate_uat_slug(deploy_service._customer_dir_slug(payload))


def test_every_accepted_slug_survives_the_deploy_validator(db_session):
    """End-to-end on a PERSISTED row: what the registry stores, the deploy path can use."""
    project = _make_project(db_session)
    customer = service.create(
        db_session,
        project.id,
        CustomerCreate(name="ANDROS", slug=" ANDROS ", subdomain=" ANDROS-UAT "),
    )
    uat_provisioner.validate_uat_slug(deploy_service._customer_dir_slug(customer))
    assert customer.slug == "andros"
    assert customer.subdomain == "andros-uat"


# ---------------------------------------------------------------------------
# Instance-directory collisions — two customers, one directory
# ---------------------------------------------------------------------------


def test_create_duplicate_subdomain_in_project_rejected(db_session):
    """Two customers sharing a subdomain resolve to ONE instance directory — refuse at registration."""
    project = _make_project(db_session)
    service.create(db_session, project.id, CustomerCreate(name="A", slug="a", subdomain="shared"))
    with pytest.raises(ValueError, match="already exists"):  # 'already exists' ⇒ router maps to 409
        service.create(db_session, project.id, CustomerCreate(name="B", slug="b", subdomain="shared"))


def test_create_subdomain_colliding_with_another_slug_rejected(db_session):
    """The collision unit is the DERIVED directory: a subdomain equal to another customer's slug
    (which has no subdomain of its own) lands both on ``/opt/customers/alpha``."""
    project = _make_project(db_session)
    service.create(db_session, project.id, CustomerCreate(name="A", slug="alpha"))
    with pytest.raises(ValueError, match="already exists"):
        service.create(db_session, project.id, CustomerCreate(name="B", slug="beta", subdomain="alpha"))


def test_same_subdomain_allowed_across_projects(db_session):
    """The registry is project-scoped — two projects may each have an 'icc' customer."""
    p1 = _make_project(db_session)
    p2 = _make_project(db_session)
    c1 = service.create(db_session, p1.id, CustomerCreate(name="A", slug="a1", subdomain="icc"))
    c2 = service.create(db_session, p2.id, CustomerCreate(name="A", slug="a2", subdomain="icc"))
    assert c1.id != c2.id


def test_update_subdomain_collision_rejected_and_row_untouched(db_session):
    """A rejected edit must leave the row EXACTLY as it was — not half-applied."""
    project = _make_project(db_session)
    service.create(db_session, project.id, CustomerCreate(name="A", slug="a", subdomain="taken"))
    other = service.create(db_session, project.id, CustomerCreate(name="B", slug="b", subdomain="free"))

    with pytest.raises(ValueError, match="already exists"):
        service.update(db_session, other.id, CustomerUpdate(slug="b2", subdomain="taken", name="B2"))

    assert other.slug == "b"  # the slug assignment must NOT have run before the conflict check
    assert other.subdomain == "free"
    assert other.name == "B"


def test_pre_existing_collision_does_not_lock_the_customer_out_of_editing(db_session):
    """A row registered BEFORE this guard existed may already collide. It must stay editable —
    refusing every save would trap the person in a record they cannot fix, and the escape (changing
    the subdomain) is itself a save. Only an edit that MOVES the customer is checked."""
    from backend.db.models.customers import Customer

    project = _make_project(db_session)
    # Two colliding rows inserted straight through the model — the state the old form could produce.
    db_session.add(Customer(project_id=project.id, name="A", slug="a", subdomain="dup"))
    stuck = Customer(project_id=project.id, name="B", slug="b", subdomain="dup")
    db_session.add(stuck)
    db_session.flush()

    # An unrelated edit still goes through (the directory is not changing).
    renamed = service.update(db_session, stuck.id, CustomerUpdate(name="B s.r.o."))
    assert renamed.name == "B s.r.o."

    # …and the way OUT — moving to a free directory — works.
    fixed = service.update(db_session, stuck.id, CustomerUpdate(subdomain="b-uat"))
    assert fixed.subdomain == "b-uat"


def test_update_keeping_own_subdomain_is_not_a_collision(db_session):
    """Editing a customer without changing its subdomain must not collide with ITSELF."""
    project = _make_project(db_session)
    customer = service.create(db_session, project.id, CustomerCreate(name="A", slug="a", subdomain="andros"))
    updated = service.update(db_session, customer.id, CustomerUpdate(name="A2", subdomain="andros"))
    assert updated.name == "A2"
    assert updated.subdomain == "andros"


def test_http_deploy_hostile_slug_422_with_slovak_reason(client, db_session):
    """Over HTTP the refusal arrives as 422 carrying the Slovak rule — the FE can show it verbatim."""
    _auth_ri(client)
    project = _make_project(db_session)
    db_session.commit()
    resp = client.post(
        f"/api/v1/projects/{project.slug}/customers",
        json={"name": "ANDROS", "slug": "andros s.r.o."},
    )
    assert resp.status_code == 422, resp.text
    assert "malé písmená a-z" in resp.text


def test_http_duplicate_subdomain_409(client, db_session):
    _auth_ri(client)
    project = _make_project(db_session)
    db_session.commit()
    first = client.post(
        f"/api/v1/projects/{project.slug}/customers",
        json={"name": "A", "slug": "a", "subdomain": "shared"},
    )
    assert first.status_code == 201, first.text
    resp = client.post(
        f"/api/v1/projects/{project.slug}/customers",
        json={"name": "B", "slug": "b", "subdomain": "shared"},
    )
    assert resp.status_code == 409, resp.text
    # The detail names the real cause so the FE can offer the right fix (change the subdomain).
    assert "instance directory" in resp.json()["detail"]
