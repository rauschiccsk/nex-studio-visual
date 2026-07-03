"""Integration tests for the UAT & PROD tabs — version × customer matrix (CR-V2-027).

Exercises the per-customer deploy surface (design §3.3/§3.4/§3.5) end-to-end
through the real FastAPI ``app``: the ``/projects/{slug}/deploy-matrix`` read
that feeds both tabs, the Nasadiť (deploy) action, the Akceptovať (UAT
acceptance) action, and the never-bypassed PROD acceptance gate.

The deploy runner (real ``uat_provisioner`` + docker compose up) is faked so no
``git``/``docker`` is spawned — the module-level ``_default_deploy_runner`` is
monkeypatched (the route never injects a runner, per the deploy service
contract).

Safety invariants asserted here (the CR's load-bearing rules):
  * **The acceptance gate is NEVER bypassed (§3.5, incident 2026-06-10).** A PROD
    deploy of an un-accepted (version, customer) is rejected with 409, and the
    matrix exposes ``accepted_versions`` so the FE keeps PROD Nasadiť disabled
    until acceptance.
  * **Akceptovať LOGS who/when/version/customer (§3.5).** The accept event
    records the actor, the version, the customer and a timestamp.
  * **Different customers may run different versions simultaneously (§3.3).**
  * **No secret material is ever returned (§4/OQ-5).** No response field carries
    a secret.
"""

from __future__ import annotations

import uuid as _uuid

import bcrypt
import pytest

from backend.db.models.customers import Customer
from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import deploy as deploy_service
from backend.services import orchestrator

# ---------------------------------------------------------------------------
# Fixtures — a project with two customers and two verified versions
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_deploy_runner(monkeypatch):
    """Replace the real provision+up runner with an in-memory success stub.

    Returns ``(ok=True, detail, url)`` without spawning git/docker. The recorded
    ``calls`` list lets a test assert the runner was invoked with the expected
    (preserve-by-default) parameters.
    """
    calls: list[dict] = []

    async def _runner(*, project_slug, uat_slug, version_number, force_fresh):
        calls.append(
            {
                "project_slug": project_slug,
                "uat_slug": uat_slug,
                "version_number": version_number,
                "force_fresh": force_fresh,
            }
        )
        return True, "OK (faked)", f"https://uat-{uat_slug}.isnex.eu"

    monkeypatch.setattr(deploy_service, "_default_deploy_runner", _runner)
    return calls


def _seed_project(db, *, creator: User) -> Project:
    suffix = _uuid.uuid4().hex[:8]
    project = Project(
        name=f"Deploy Matrix Proj {suffix}",
        slug=f"deploy-matrix-{suffix}",
        type="standard",
        auth_mode="password",
        description="CR-V2-027 deploy matrix test project.",
        created_by=creator.id,
    )
    db.add(project)
    db.flush()
    return project


def _seed_verified_version(db, project: Project, version_number: str) -> Version:
    """A version carried to Hotovo (``current_stage='done'``) = VERIFIED (§3.1)."""
    version = Version(project_id=project.id, version_number=version_number, name=version_number)
    db.add(version)
    db.flush()
    db.add(
        PipelineState(
            version_id=version.id,
            flow_type="new_version",
            current_stage="done",
            current_actor="auditor",
            status="done",
            next_action="",
        )
    )
    db.flush()
    # CR-V2-056: verified is COMPUTED from the Verifikácia PASS verdict (version_verified), not the stored
    # 'done' stage alone — record the PASS. No verified_sha in the test repo → 'unbound' → verified.
    orchestrator._record_message(
        db,
        version_id=version.id,
        stage="verifikacia",
        author="auditor",
        recipient="manazer",
        kind="verdict",
        content="PASS",
        payload={"verdict": "PASS", "phase": "verifikacia"},
    )
    db.flush()
    return version


def _seed_unverified_version(db, project: Project, version_number: str) -> Version:
    """A version still in-flight (Programovanie) — NOT deployable."""
    version = Version(project_id=project.id, version_number=version_number, name=version_number)
    db.add(version)
    db.flush()
    db.add(
        PipelineState(
            version_id=version.id,
            flow_type="new_version",
            current_stage="programovanie",
            current_actor="ai_agent",
            status="agent_working",
            next_action="",
        )
    )
    db.flush()
    return version


def _seed_customer(db, project: Project, slug: str) -> Customer:
    customer = Customer(project_id=project.id, name=slug.upper(), slug=slug, subdomain=slug)
    db.add(customer)
    db.flush()
    return customer


def _current_user(db) -> User:
    """The ri user the conftest ``client`` fixture authenticates as.

    The conftest seeds its own ri user and overrides the gates to it; for the
    actor assertion we read the most recently created ri user back from the DB.
    """
    rows = db.query(User).filter(User.role == "ri").order_by(User.created_at.desc()).all()
    return rows[0]


# ---------------------------------------------------------------------------
# Matrix read — verified versions + per-customer cells
# ---------------------------------------------------------------------------


class TestDeployMatrixRead:
    def test_matrix_lists_only_verified_versions(self, client, db_session, fake_deploy_runner):
        user = _current_user(db_session)
        project = _seed_project(db_session, creator=user)
        _seed_verified_version(db_session, project, "v0.1.0")
        _seed_verified_version(db_session, project, "v0.2.0")
        _seed_unverified_version(db_session, project, "v0.3.0")  # in-flight → excluded
        _seed_customer(db_session, project, "andros")

        resp = client.get(f"/api/v1/projects/{project.slug}/deploy-matrix")
        assert resp.status_code == 200, resp.text
        body = resp.json()

        # Only the two Hotovo versions are deployable; the in-flight one is excluded.
        assert body["verified_versions"] == ["v0.2.0", "v0.1.0"]
        assert "v0.3.0" not in body["verified_versions"]
        assert len(body["rows"]) == 1
        row = body["rows"][0]
        assert row["customer_slug"] == "andros"
        # Never deployed yet → empty cells, gate closed.
        assert row["uat_version"] is None
        assert row["prod_version"] is None
        assert row["accepted_versions"] == []
        assert row["uat_url"] is None
        # No secret material in any field.
        assert "secret" not in body and all("secret" not in r for r in body["rows"])

    def test_matrix_404_for_unknown_project(self, client):
        resp = client.get("/api/v1/projects/does-not-exist/deploy-matrix")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# The never-bypassed PROD acceptance gate (§3.5) — the CR safety invariant
# ---------------------------------------------------------------------------


class TestProdAcceptanceGate:
    def test_prod_deploy_blocked_until_uat_accepted(self, client, db_session, fake_deploy_runner):
        """⚠ SAFETY INVARIANT: no PROD deploy without a recorded UAT acceptance."""
        user = _current_user(db_session)
        project = _seed_project(db_session, creator=user)
        _seed_verified_version(db_session, project, "v0.1.0")
        customer = _seed_customer(db_session, project, "andros")

        # 1) Deploy to UAT — allowed (no gate on UAT).
        uat = client.post(
            f"/api/v1/customers/{customer.id}/deploy",
            json={"version_number": "v0.1.0", "environment": "uat"},
        )
        assert uat.status_code == 200, uat.text
        assert uat.json()["ok"] is True

        # 2) PROD deploy of the SAME version BEFORE acceptance → BLOCKED (409).
        blocked = client.post(
            f"/api/v1/customers/{customer.id}/deploy",
            json={"version_number": "v0.1.0", "environment": "prod"},
        )
        assert blocked.status_code == 409, blocked.text
        assert "accept" in blocked.json()["detail"].lower()

        # The matrix still shows the version as NOT accepted → FE keeps PROD disabled.
        matrix = client.get(f"/api/v1/projects/{project.slug}/deploy-matrix").json()
        row = matrix["rows"][0]
        assert "v0.1.0" not in row["accepted_versions"]
        assert row["uat_version"] == "v0.1.0"
        assert row["uat_url"] is not None  # UAT deployed → link present

        # 3) Akceptovať the UAT — opens PROD.
        accept = client.post(
            f"/api/v1/customers/{customer.id}/accept",
            json={"version_number": "v0.1.0"},
        )
        assert accept.status_code == 200, accept.text

        # 4) Now the matrix shows it accepted → FE enables PROD Nasadiť.
        matrix2 = client.get(f"/api/v1/projects/{project.slug}/deploy-matrix").json()
        assert "v0.1.0" in matrix2["rows"][0]["accepted_versions"]

        # 5) PROD deploy now SUCCEEDS (gate satisfied) — and graduates to v1.0.0 (§3.6).
        prod = client.post(
            f"/api/v1/customers/{customer.id}/deploy",
            json={"version_number": "v0.1.0", "environment": "prod"},
        )
        assert prod.status_code == 200, prod.text
        result = prod.json()
        assert result["ok"] is True
        assert result["bumped_to"] == "v1.0.0"  # first PROD deploy bump (§3.6)

    def test_accept_logs_who_when_version_customer(self, client, db_session, fake_deploy_runner):
        """Akceptovať records who/when/version/customer (§3.5 audit log)."""
        user = _current_user(db_session)
        project = _seed_project(db_session, creator=user)
        _seed_verified_version(db_session, project, "v0.1.0")
        customer = _seed_customer(db_session, project, "icc")

        client.post(
            f"/api/v1/customers/{customer.id}/deploy",
            json={"version_number": "v0.1.0", "environment": "uat"},
        )
        accept = client.post(
            f"/api/v1/customers/{customer.id}/accept",
            json={"version_number": "v0.1.0"},
        )
        assert accept.status_code == 200, accept.text
        event = accept.json()
        assert event["event_type"] == "accept"
        assert event["version_number"] == "v0.1.0"  # which version
        assert event["customer_id"] == str(customer.id)  # which customer
        assert event["actor_id"] == str(user.id)  # WHO accepted
        assert event["created_at"] is not None  # WHEN
        assert event["status"] == "ok"

    def test_cannot_accept_version_never_deployed_to_uat(self, client, db_session, fake_deploy_runner):
        user = _current_user(db_session)
        project = _seed_project(db_session, creator=user)
        _seed_verified_version(db_session, project, "v0.1.0")
        customer = _seed_customer(db_session, project, "andros")

        # No UAT deploy yet → accept must be rejected (cannot accept the un-deployed).
        accept = client.post(
            f"/api/v1/customers/{customer.id}/accept",
            json={"version_number": "v0.1.0"},
        )
        assert accept.status_code == 409, accept.text


# ---------------------------------------------------------------------------
# Per-customer independence (§3.3) — two customers on different versions
# ---------------------------------------------------------------------------


class TestPerCustomerIndependence:
    def test_two_customers_run_different_versions(self, client, db_session, fake_deploy_runner):
        user = _current_user(db_session)
        project = _seed_project(db_session, creator=user)
        _seed_verified_version(db_session, project, "v1.0.0")
        _seed_verified_version(db_session, project, "v1.1.0")
        andros = _seed_customer(db_session, project, "andros")
        icc = _seed_customer(db_session, project, "icc")

        # ANDROS UAT → v1.0.0; ICC UAT → v1.1.0 (different versions, same time).
        client.post(
            f"/api/v1/customers/{andros.id}/deploy",
            json={"version_number": "v1.0.0", "environment": "uat"},
        )
        client.post(
            f"/api/v1/customers/{icc.id}/deploy",
            json={"version_number": "v1.1.0", "environment": "uat"},
        )

        matrix = client.get(f"/api/v1/projects/{project.slug}/deploy-matrix").json()
        by_slug = {r["customer_slug"]: r for r in matrix["rows"]}
        assert by_slug["andros"]["uat_version"] == "v1.0.0"
        assert by_slug["icc"]["uat_version"] == "v1.1.0"  # genuinely different

    def test_redeploy_preserves_secrets_by_default(self, client, db_session, fake_deploy_runner):
        """⚠ SAFETY INVARIANT: a redeploy does NOT rotate secrets by default (§3.7)."""
        user = _current_user(db_session)
        project = _seed_project(db_session, creator=user)
        _seed_verified_version(db_session, project, "v0.1.0")
        _seed_verified_version(db_session, project, "v0.2.0")
        customer = _seed_customer(db_session, project, "andros")

        client.post(
            f"/api/v1/customers/{customer.id}/deploy",
            json={"version_number": "v0.1.0", "environment": "uat"},
        )
        # A later version to an EXISTING instance — must preserve (force_fresh False).
        client.post(
            f"/api/v1/customers/{customer.id}/deploy",
            json={"version_number": "v0.2.0", "environment": "uat"},
        )
        # The runner was invoked with force_fresh=False on every call (preserve-by-default).
        assert fake_deploy_runner, "runner was never called"
        assert all(call["force_fresh"] is False for call in fake_deploy_runner)


# ---------------------------------------------------------------------------
# Service-level unit checks (the matrix helpers — no HTTP)
# ---------------------------------------------------------------------------


class TestMatrixServiceHelpers:
    def test_list_verified_versions_excludes_in_flight(self, db_session):
        creator = User(
            username=f"svc_{_uuid.uuid4().hex[:8]}",
            email=f"svc_{_uuid.uuid4().hex[:8]}@test.local",
            password_hash=bcrypt.hashpw(b"x", bcrypt.gensalt(rounds=4)).decode(),
            role="ri",
            is_active=True,
        )
        db_session.add(creator)
        db_session.flush()
        project = _seed_project(db_session, creator=creator)
        _seed_verified_version(db_session, project, "v0.1.0")
        _seed_unverified_version(db_session, project, "v0.2.0")

        verified = deploy_service.list_verified_versions(db_session, project.id)
        assert verified == ["v0.1.0"]

    def test_instance_url_is_single_source_of_truth(self, db_session):
        creator = User(
            username=f"url_{_uuid.uuid4().hex[:8]}",
            email=f"url_{_uuid.uuid4().hex[:8]}@test.local",
            password_hash=bcrypt.hashpw(b"x", bcrypt.gensalt(rounds=4)).decode(),
            role="ri",
            is_active=True,
        )
        db_session.add(creator)
        db_session.flush()
        project = _seed_project(db_session, creator=creator)
        customer = _seed_customer(db_session, project, "andros")
        # The matrix UAT URL must equal the runner-built URL for the same slug.
        instance_slug = deploy_service._instance_slug(customer, "uat")
        assert deploy_service._instance_url(customer, "uat") == deploy_service._url_for_instance_slug(instance_slug)
