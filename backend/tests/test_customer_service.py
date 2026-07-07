"""Service-level tests for the per-project Customers registry (v2.0.0, CR-V2-025).

Covers the load-bearing secret-governance invariant of :func:`customer_service.update`
(ux-batch2-followup Correction 4): a blank / ``None`` secret on update must NOT wipe the
stored per-customer credential — only an explicit non-``None`` secret rotates it (the
``if data.secret is not None:`` branch is skipped otherwise).

Secret governance (CLAUDE.md §4/§5, OQ-5): the fixture secret is a throwaway generated
token, never a real credential, and it is asserted via a SHA-256 digest so the raw value
can never surface in test output — not even in an assertion-failure diff.
"""

from __future__ import annotations

import hashlib
import uuid as _uuid

import pytest

from backend.config.settings import settings
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.schemas.customer import CustomerCreate, CustomerUpdate
from backend.services import credentials as credentials_service
from backend.services import customer as customer_service


@pytest.fixture()
def credentials_root(tmp_path, monkeypatch):
    """Point the credentials store at a tmp dir so a stored secret writes to disk hermetically."""
    root = tmp_path / "credentials"
    monkeypatch.setattr(settings, "credentials_storage_path", str(root))
    return root


def _seed_project(db) -> Project:
    suffix = _uuid.uuid4().hex[:8]
    creator = User(
        username=f"cust_{suffix}",
        email=f"cust_{suffix}@test.local",
        password_hash="x",
        role="ri",
        is_active=True,
    )
    db.add(creator)
    db.flush()
    project = Project(
        name=f"Customer Svc Proj {suffix}",
        slug=f"customer-svc-{suffix}",
        type="standard",
        auth_mode="password",
        description="Customer service secret-preservation test project.",
        created_by=creator.id,
    )
    db.add(project)
    db.flush()
    return project


def _stored_secret_digest(db, credential_id) -> str:
    """SHA-256 of the on-disk secret content — compared instead of the raw value (§4: never surface it)."""
    content = credentials_service.read_content(db, credential_id).content
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def test_update_with_secret_none_leaves_stored_secret_intact(db_session, credentials_root):
    """⚠ SECRET GOVERNANCE: ``update(..., secret=None)`` must NOT wipe the stored credential (§3.2).

    A customer edit that touches other fields but omits the secret (``secret=None``) must skip the
    credentials-store write entirely — the previously stored secret survives byte-for-byte and the
    ``credential_id`` pointer is unchanged. Only an explicit non-``None`` secret rotates it.
    """
    project = _seed_project(db_session)

    # Register a customer WITH a secret → it lands in the credentials store; the row keeps only the pointer.
    original_secret = f"tok-{_uuid.uuid4().hex}"  # throwaway generated token, never a real credential
    customer = customer_service.create(
        db_session,
        project.id,
        CustomerCreate(name="ANDROS", slug="andros", secret=original_secret),
    )
    assert customer.credential_id is not None
    credential_id = customer.credential_id
    before = _stored_secret_digest(db_session, credential_id)

    # Update OTHER fields with secret omitted (None) — the secret write branch must be skipped.
    updated = customer_service.update(
        db_session,
        customer.id,
        CustomerUpdate(name="ANDROS s.r.o.", notes="renamed", secret=None),
    )

    # The non-secret edit applied…
    assert updated.name == "ANDROS s.r.o."
    assert updated.notes == "renamed"
    # …and the stored secret is intact: same pointer, byte-for-byte identical content.
    assert updated.credential_id == credential_id  # pointer untouched, not re-created
    assert _stored_secret_digest(db_session, credential_id) == before  # content unchanged


def test_update_with_explicit_secret_rotates_the_stored_content(db_session, credentials_root):
    """An explicit non-``None`` secret DOES rotate the stored content (the other half of the invariant)."""
    project = _seed_project(db_session)

    customer = customer_service.create(
        db_session,
        project.id,
        CustomerCreate(name="ICC", slug="icc", secret=f"tok-{_uuid.uuid4().hex}"),
    )
    credential_id = customer.credential_id
    before = _stored_secret_digest(db_session, credential_id)

    customer_service.update(
        db_session,
        customer.id,
        CustomerUpdate(secret=f"tok-{_uuid.uuid4().hex}"),  # a genuinely new value
    )

    # Same pointer (rotation overwrites in place), but the stored content changed.
    assert customer.credential_id == credential_id
    assert _stored_secret_digest(db_session, credential_id) != before
